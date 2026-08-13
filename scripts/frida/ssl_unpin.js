/**
 * SSL unpinning genérico (OkHttp, TrustManager, Cronet básico).
 * Lab Instablack — usar só em conta de teste + HTTP Toolkit.
 *
 * Instagram muda rápido: se falhar, use script por versão:
 *   frida -U -f com.instagram.android --codeshare takaotr/instagram-ssl-pinning-bypass-v422 --no-pause
 *   https://github.com/takaotr/Android-Instagram-SSL-Pinning-Bypass
 *
 * Com Frida Gadget (APK repatchado): coloque este .js na pasta files/ do app
 * junto com frida-gadget.config (ver README).
 */

'use strict';

function log(msg) {
  console.log('[instablack-unpin] ' + msg);
}

function hookOkHttp() {
  Java.perform(function () {
    try {
      var CertificatePinner = Java.use('okhttp3.CertificatePinner');
      CertificatePinner.check.overload('java.lang.String', 'java.util.List').implementation = function (a, b) {
        log('OkHttp CertificatePinner.check bypass: ' + a);
      };
      log('OkHttp CertificatePinner hooked');
    } catch (e) {
      log('OkHttp skip: ' + e);
    }

    try {
      var TrustManagerImpl = Java.use('com.android.org.conscrypt.TrustManagerImpl');
      TrustManagerImpl.verifyChain.implementation = function (
        untrustedChain,
        trustAnchorChain,
        host,
        clientAuth,
        ocspData,
        tlsSctData
      ) {
        log('TrustManagerImpl.verifyChain bypass: ' + host);
        return untrustedChain;
      };
      log('TrustManagerImpl hooked');
    } catch (e) {
      log('TrustManagerImpl skip: ' + e);
    }

    try {
      var SSLContext = Java.use('javax.net.ssl.SSLContext');
      var TrustManager = Java.use('javax.net.ssl.X509TrustManager');
      var EmptyTrustManager = Java.registerClass({
        name: 'com.instablack.lab.EmptyTrustManager',
        implements: [TrustManager],
        methods: {
          checkClientTrusted: function (chain, authType) {},
          checkServerTrusted: function (chain, authType) {},
          getAcceptedIssuers: function () {
            return [];
          },
        },
      });
      var TrustManagers = [EmptyTrustManager.$new()];
      var SSLContextInit = SSLContext.init.overload(
        '[Ljavax.net.ssl.KeyManager;',
        '[Ljavax.net.ssl.TrustManager;',
        'java.security.SecureRandom'
      );
      SSLContextInit.implementation = function (km, tm, sr) {
        log('SSLContext.init → empty TrustManager');
        SSLContextInit.call(this, km, TrustManagers, sr);
      };
      log('SSLContext.init hooked');
    } catch (e) {
      log('SSLContext skip: ' + e);
    }
  });
}

function hookNative() {
  var libs = ['libssl.so', 'libboringssl.so'];
  libs.forEach(function (lib) {
    try {
      var ptr = Module.findExportByName(lib, 'SSL_CTX_set_verify');
      if (ptr) {
        Interceptor.attach(ptr, {
          onEnter: function (args) {
            args[2] = ptr(0);
          },
        });
        log('Native SSL_CTX_set_verify hooked (' + lib + ')');
      }
    } catch (e) {
      log('Native ' + lib + ' skip: ' + e);
    }
  });
}

log('starting…');
setImmediate(function () {
  hookOkHttp();
  hookNative();
  log('hooks installed — abra HTTP Toolkit e use o Instagram');
});
