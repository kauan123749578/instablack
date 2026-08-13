# Lab Frida — capturar Instagram app (HTTP Toolkit)

Conta **de teste** only. Patches quebram Play Integrity; Meta pode flagar a conta.

## Duas formas

| Modo | Precisa | Melhor para |
|------|---------|-------------|
| **frida-server** | Root / emulador root | Desenvolvimento rápido |
| **Frida Gadget** | APK repatchado (sem root) | POCO sem root (mais chato) |

---

## A) Com root (ou emulador) — mais fácil

```powershell
pip install frida-tools objection
adb devices

# Mesma versão frida no PC e frida-server no celular
# https://github.com/frida/frida/releases

adb push frida-server /data/local/tmp/
adb shell "chmod 755 /data/local/tmp/frida-server"
adb shell "/data/local/tmp/frida-server &"

# Script genérico do repo
frida -U -f com.instagram.android -l scripts/frida/ssl_unpin.js --no-pause

# OU script por versão do IG (recomendado se genérico falhar)
frida -U -f com.instagram.android --codeshare takaotr/instagram-ssl-pinning-bypass-v422 --no-pause
```

HTTP Toolkit Android ligado → filtro `i.instagram.com` → deve aparecer **200**, não `Aborted`.

---

## B) Frida Gadget (sem root) — o que você pediu

### 1. Ferramentas no PC

- [JDK 17+](https://adoptium.net/)
- [Android SDK platform-tools](https://developer.android.com/tools/releases/platform-tools) (`adb`)
- [apktool 2.9.3+](https://apktool.org/)
- [uber-apk-signer](https://github.com/patrickfav/uber-apk-signer)
- Node.js → `npm install -g apk-mitm` (injeta Gadget automaticamente)

### 2. APK do Instagram

- Baixe **APK + splits** (arm64-v8a) da **mesma versão** que você vai usar.
- Instagram usa **split APK** — instalar só o base falha.

### 3. Injetar Gadget (apk-mitm)

```powershell
# APK base (não o bundle .apkm inteiro sem extrair)
apk-mitm --certificate ~/.mitmproxy/mitmproxy-ca-cert.cer instagram-base.apk

# Ou certificado exportado do HTTP Toolkit (Settings → HTTPS → Export CA)
```

Isso embute `libfrida-gadget.so` + network security config para confiar no proxy.

### 4. Copiar script deste repo

Coloque na pasta que o Gadget carrega (depende do patcher; apk-mitm documenta o path):

- `ssl_unpin.js` (deste diretório)
- `frida-gadget.config` (aponta para o script)

Exemplo `frida-gadget.config`:

```json
{
  "interaction": {
    "type": "script",
    "path": "ssl_unpin.js",
    "on_change": "reload"
  }
}
```

### 5. Assinar e instalar

```powershell
java -jar uber-apk-signer.jar --apks instagram-base-patched.apk
adb install-multiple base.apk split_config.arm64_v8a.apk ...
```

(Xiaomi: ativar **Instalar apps desconhecidos** + desinstalar IG da Play Store antes.)

### 6. Proxy

1. HTTP Toolkit → Android (ou proxy manual).
2. Certificado CA instalado no celular.
3. Abrir Instagram **patched**.
4. Filtrar `i.instagram.com`.

### 7. Gadget escutando na rede (alternativa)

Se o patch usar modo `listen`:

```powershell
adb forward tcp:27042 tcp:27042
frida -H 127.0.0.1:27042 -n Gadget -l scripts/frida/ssl_unpin.js
```

---

## POCO X6 Pro (HyperOS) — armadilhas

- **Bootloader / root**: desbloquear Xiaomi é possível, mas demorado; Gadget evita root mas exige APK patchado.
- **Play Integrity**: IG patchado pode nem abrir login — normal.
- **Splits**: sempre `adb install-multiple`.
- **Versão IG**: script de unpin amarrado à versão — veja [takaotr/Android-Instagram-SSL-Pinning-Bypass](https://github.com/takaotr/Android-Instagram-SSL-Pinning-Bypass).

---

## O que capturar (igual SteeL / Phantom)

Procure requests para:

- `i.instagram.com/api/v1/...`
- Headers: `x-ig-nav-chain`, `x-fb-friendly-name`, `Authorization`
- Login: `b.i.instagram.com`, `bloks/...`

Compare com `phantom/headers.py` e `phantom/endpoints.py`.

---

## Alternativa mais simples (sem Frida)

HTTP Toolkit → **Fresh Terminal** no PC → proxy Python com Phantom:

```powershell
$env:HTTP_PROXY="http://127.0.0.1:8000"
$env:HTTPS_PROXY="http://127.0.0.1:8000"
python -c "from core.instagram import _new_instagrapi_client; _new_instagrapi_client()"
```

Isso mostra tráfego `i.instagram.com` **sem** mexer no celular.
