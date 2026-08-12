(function () {
  "use strict";

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) return meta.content;
    const hidden = document.querySelector('input[name="csrf_token"]');
    return hidden && hidden.value ? hidden.value : "";
  }

  function ensureCsrfOnForm(form) {
    if (!form || form.method.toLowerCase() === "get") return;
    const token = csrfToken();
    if (!token) return;
    let input = form.querySelector('input[name="csrf_token"]');
    if (!input) {
      input = document.createElement("input");
      input.type = "hidden";
      input.name = "csrf_token";
      form.appendChild(input);
    }
    input.value = token;
  }

  const _fetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    init = init ? { ...init } : {};
    const method = (init.method || "GET").toUpperCase();
    if (method !== "GET" && method !== "HEAD" && method !== "OPTIONS") {
      const headers = new Headers(init.headers || {});
      const token = csrfToken();
      if (token && !headers.has("X-CSRF-Token")) {
        headers.set("X-CSRF-Token", token);
      }
      init.headers = headers;
      if (init.body instanceof FormData && token && !init.body.has("csrf_token")) {
        init.body.append("csrf_token", token);
      }
    }
    return _fetch(input, init);
  };

  document.addEventListener(
    "submit",
    (ev) => {
      const form = ev.target;
      if (!form || form.tagName !== "FORM") return;
      ensureCsrfOnForm(form);

      // Multipart com arquivo: submit nativo NÃO manda X-CSRF-Token.
      // Se o middleware tentar request.form() pra ler o token, o body
      // some no endpoint. Converte pra fetch (header injetado no wrapper).
      // #automation-form: Reels/Story/Foto tratam no initAutomationForm.
      // #profile-edit-form: tratado em initProfileEditForm (loading + HTML).
      if (form.id === "automation-form") return;
      if (form.id === "profile-edit-form") return;
      if (form.dataset.nativeSubmit === "1") return;
      if (ev.defaultPrevented) return;
      const method = (form.getAttribute("method") || "get").toLowerCase();
      if (method === "get" || method === "") return;
      if (!form.querySelector('input[type="file"]')) return;

      ev.preventDefault();
      const action = form.getAttribute("action") || window.location.href;
      const btn = form.querySelector('[type="submit"], button:not([type="button"])');
      if (btn) btn.disabled = true;
      fetch(action, {
        method: method.toUpperCase(),
        body: new FormData(form),
        credentials: "same-origin",
        redirect: "follow",
        headers: { "X-Requested-With": "fetch" },
      })
        .then(async (res) => {
          const ctype = (res.headers.get("content-type") || "").toLowerCase();
          if (ctype.includes("text/html")) {
            const html = await res.text();
            document.open();
            document.write(html);
            document.close();
            return;
          }
          if (res.redirected || res.ok) {
            window.location.href = res.url;
            return;
          }
          throw new Error("Falha no envio.");
        })
        .catch(() => {
          alert("Falha no envio. Recarregue a página e tente de novo.");
          if (btn) btn.disabled = false;
        });
    },
    true
  );

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("form").forEach(ensureCsrfOnForm);
  });

  const appContent = document.getElementById("app-content");
  const drawer = document.getElementById("mobile-drawer");
  const drawerOpen = document.getElementById("drawer-open");
  const drawerBackdrop = document.getElementById("drawer-backdrop");
  const sidebar = document.getElementById("sidebar");
  const sidebarPinBtn = document.getElementById("sidebar-pin-btn");
  const SIDEBAR_PIN_KEY = "instablack_sidebar_pinned";
  let notifPollTimer = null;
  let dashActivityPollTimer = null;

  function applySidebarPin(pinned) {
    if (!sidebar) return;
    sidebar.classList.toggle("is-pinned", pinned);
    if (!sidebarPinBtn) return;
    sidebarPinBtn.classList.toggle("is-active", pinned);
    sidebarPinBtn.setAttribute("aria-pressed", pinned ? "true" : "false");
    sidebarPinBtn.title = pinned ? "Desafixar sidebar" : "Fixar sidebar";
    const label = sidebarPinBtn.querySelector(".sidebar-pin-label");
    if (label) label.textContent = pinned ? "Desafixar sidebar" : "Fixar sidebar";
    const icon = sidebarPinBtn.querySelector("[data-lucide]");
    if (icon) {
      icon.setAttribute("data-lucide", pinned ? "panel-left-close" : "panel-left");
      try {
        if (window.lucide) lucide.createIcons({ nodes: [sidebarPinBtn] });
      } catch (_) {}
    }
  }

  try {
    applySidebarPin(localStorage.getItem(SIDEBAR_PIN_KEY) === "1");
  } catch (_) {
    applySidebarPin(false);
  }

  sidebarPinBtn?.addEventListener("click", () => {
    const next = !sidebar?.classList.contains("is-pinned");
    try { localStorage.setItem(SIDEBAR_PIN_KEY, next ? "1" : "0"); } catch (_) {}
    applySidebarPin(next);
  });

  function closeDrawer() { drawer?.classList.remove("open"); }

  drawerOpen?.addEventListener("click", () => drawer?.classList.add("open"));
  drawerBackdrop?.addEventListener("click", closeDrawer);
  document.getElementById("drawer-close")?.addEventListener("click", closeDrawer);

  document.getElementById("mobile-menu-btn")?.addEventListener("click", () => {
    sidebar?.classList.toggle("mobile-open");
  }, true);

  function setActiveNav(path) {
    const els = Array.from(document.querySelectorAll("[data-nav]"));
    const hrefOf = (el) => el.getAttribute("data-nav") || el.getAttribute("href");
    // Se houver match exato, só ele fica ativo (evita /accounts acender junto de /accounts/connected)
    const hasExact = els.some((el) => hrefOf(el) === path);
    els.forEach((el) => {
      const href = hrefOf(el);
      const isActive = hasExact
        ? path === href
        : path === href || (href !== "/" && path.startsWith(href));
      el.classList.toggle("active", isActive);
    });
  }
  setActiveNav(window.location.pathname);

  let navAbort = null;
  let navInFlight = null;

  async function navigateTo(url, push = true) {
    // Em "Ver como", força reload completo para todas as abas usarem o usuário alvo.
    if (document.body.classList.contains("is-view-as")) {
      window.location.href = url;
      return;
    }
    if (
      url.startsWith("/automations/new") ||
      url.startsWith("/automations/story-studio") ||
      url.startsWith("/accounts/notes") ||
      url.startsWith("/camuflagem")
    ) {
      window.location.href = url;
      return;
    }
    if (!appContent) { window.location.href = url; return; }

    // Cancela navegação SPA anterior (evita sidebar "travada" com fetch pendurado).
    if (navAbort) {
      try { navAbort.abort(); } catch (_) {}
    }
    navAbort = typeof AbortController !== "undefined" ? new AbortController() : null;
    const abort = navAbort;
    const timeoutId = window.setTimeout(() => {
      try { abort?.abort(); } catch (_) {}
    }, 12000);

    appContent.classList.add("content-loading");
    if (dashActivityPollTimer) {
      clearInterval(dashActivityPollTimer);
      dashActivityPollTimer = null;
    }
    const run = (async () => {
      try {
        const resp = await fetch(url, {
          headers: { "X-Partial": "1" },
          signal: abort?.signal,
          credentials: "same-origin",
        });
        if (!resp.ok) throw new Error(String(resp.status));
        const html = await resp.text();
        const doc = new DOMParser().parseFromString(html, "text/html");
        const newContent = doc.getElementById("app-content");
        if (!newContent) throw new Error("missing-app-content");
        // Só aplica se esta ainda for a navegação vigente.
        if (abort && navAbort !== abort) return;
        appContent.innerHTML = newContent.innerHTML;
        delete document.body.dataset.pageAccountsConnected;
        delete document.body.dataset.pageVault;
        if (doc.body?.dataset?.pageAccountsConnected) {
          document.body.dataset.pageAccountsConnected = doc.body.dataset.pageAccountsConnected;
        }
        // Scripts do bloco {% block scripts %} não vêm no #app-content — procura marcadores no HTML.
        if (html.includes('data-page-accounts-connected="1"') || html.includes("pageAccountsConnected")) {
          document.body.dataset.pageAccountsConnected = "1";
        }
        if (html.includes('data-page-vault="1"') || html.includes('data-page-vault')) {
          document.body.dataset.pageVault = "1";
        }
        if (html.includes('data-page-notes="1"')) {
          document.body.dataset.pageNotes = "1";
        }
        if (push) history.pushState({ url }, "", url);
        setActiveNav(new URL(url, window.location.origin).pathname);
        initPage();
        closeDrawer();
        sidebar?.classList.remove("mobile-open");
      } catch (err) {
        if (abort && err && err.name === "AbortError" && navAbort !== abort) {
          return; // abortada por navegação mais nova
        }
        window.location.href = url;
      } finally {
        window.clearTimeout(timeoutId);
        if (!abort || navAbort === abort) {
          appContent.classList.remove("content-loading");
        }
      }
    })();
    navInFlight = run;
    await run;
  }

  document.addEventListener("click", (e) => {
    const link = e.target.closest("[data-nav]");
    if (!link || link.tagName === "BUTTON") return;
    const href = link.getAttribute("data-nav") || link.getAttribute("href");
    if (!href || href.startsWith("http") || link.target === "_blank") return;
    e.preventDefault();
    navigateTo(href);
  }, true);

  async function copyToClipboard(text) {
    const value = String(text || "");
    if (!value) return false;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(value);
        return true;
      }
    } catch (_) {
      // Fallback abaixo
    }

    try {
      const ta = document.createElement("textarea");
      ta.value = value;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      ta.style.top = "-9999px";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return Boolean(ok);
    } catch (_) {}
    return false;
  }

  document.addEventListener("click", async (e) => {
    const tokenToggle = e.target.closest(".meta-token-toggle");
    if (tokenToggle) {
      e.preventDefault();
      e.stopPropagation();
      const wrap = tokenToggle.closest(".meta-token-reveal");
      if (!wrap) return;
      const revealed = wrap.getAttribute("data-revealed") === "1";
      wrap.setAttribute("data-revealed", revealed ? "0" : "1");
      const icon = tokenToggle.querySelector("[data-lucide]");
      if (icon) {
        icon.setAttribute("data-lucide", revealed ? "eye" : "eye-off");
        if (window.lucide?.createIcons) window.lucide.createIcons({ nodes: [tokenToggle] });
      }
      tokenToggle.setAttribute(
        "aria-label",
        revealed ? "Mostrar token" : "Ocultar token"
      );
      return;
    }

    const copyBtn = e.target.closest(".copy-url-btn");
    if (copyBtn) {
      e.preventDefault();
      e.stopPropagation();
      const t = copyBtn.getAttribute("data-copy") || copyBtn.dataset?.copy || "";
      let text = t;
      if (!text) {
        const input =
          copyBtn.parentElement?.querySelector("input[readonly], textarea[readonly]") || null;
        if (input && input.value) text = input.value;
      }
      const ok = await copyToClipboard(text);
      const prevHtml = copyBtn.innerHTML;
      const okLabel = copyBtn.dataset?.copyOkLabel || "OK";
      const failLabel = copyBtn.dataset?.copyFailLabel || "Falha";
      copyBtn.innerHTML = ok ? okLabel : failLabel;
      window.setTimeout(() => {
        copyBtn.innerHTML = prevHtml;
      }, 1200);
      return;
    }

    const newBtn = e.target.closest("#meta-app-new-btn");
    if (newBtn) {
      e.preventDefault();
      e.stopPropagation();
      initMetaAppsPage();
      const dlg = document.getElementById("meta-app-dialog");
      if (dlg && typeof dlg.showModal === "function") {
        if (!dlg.open) dlg.showModal();
      }
      else if (dlg) dlg.setAttribute("open", "open");
      return;
    }

    const closeBtn = e.target.closest("#meta-app-dialog-close");
    if (closeBtn) {
      e.preventDefault();
      e.stopPropagation();
      const dlg = document.getElementById("meta-app-dialog");
      if (dlg && typeof dlg.close === "function") dlg.close();
      else if (dlg) dlg.removeAttribute("open");
      return;
    }
  }, true);

  window.addEventListener("popstate", (e) => {
    if (e.state?.url) navigateTo(e.state.url, false);
  });

  function initMetaAppsPage() {
    const dialogs = Array.from(document.querySelectorAll("#meta-app-dialog"));
    if (!dialogs.length) return;

    // Prefer dialog instance currently rendered in #app-content (fresh navigation),
    // and remove any stale dialog moved from a previous SPA visit.
    const inContent = appContent ? dialogs.filter((d) => appContent.contains(d)) : dialogs;
    const dialog = (inContent.length ? inContent[inContent.length - 1] : dialogs[dialogs.length - 1]);
    dialogs.forEach((d) => { if (d !== dialog) d.remove(); });

    if (dialog && !document.body.contains(dialog)) {
      document.body.appendChild(dialog);
    }

    const params = new URLSearchParams(window.location.search);
    const edit = params.get("edit");
    if (!edit) return;

    if (dialog && typeof dialog.showModal === "function") {
      if (!dialog.open) dialog.showModal();
    } else if (dialog) {
      dialog.setAttribute("open", "open");
    }
  }

  function initCharts() {
    const tooltip = document.getElementById("chart-tooltip");
    document.querySelectorAll(".chart-bar").forEach((bar) => {
      bar.addEventListener("mouseenter", (e) => {
        if (!tooltip) return;
        tooltip.textContent = bar.dataset.tip || "";
        tooltip.style.opacity = "1";
        const rect = bar.getBoundingClientRect();
        const wrap = document.getElementById("chart-wrap");
        if (wrap) {
          const wr = wrap.getBoundingClientRect();
          tooltip.style.left = (rect.left - wr.left + rect.width / 2) + "px";
          tooltip.style.top = (rect.top - wr.top - 28) + "px";
        }
      });
      bar.addEventListener("mouseleave", () => { if (tooltip) tooltip.style.opacity = "0"; });
    });

    document.querySelectorAll(".gauge-fill").forEach((gf) => {
      const target = parseFloat(gf.dataset.target || "0");
      const circumference = 126;
      requestAnimationFrame(() => {
        gf.style.strokeDashoffset = String(circumference - (circumference * target / 100));
      });
    });
  }

  function initPeriodPills() {
    /* pills são links reais (?days=) — só reforça estado visual */
    document.querySelectorAll(".period-pill").forEach((pill) => {
      pill.addEventListener("click", () => {
        document.querySelectorAll(".period-pill").forEach((p) => p.classList.remove("active"));
        pill.classList.add("active");
      });
    });
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const raw = atob(base64);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  let deferredPwaPrompt = null;
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredPwaPrompt = e;
    document.querySelectorAll("#btn-pwa-install-profile").forEach((btn) => {
      btn.textContent = "Instalar app agora";
    });
  });

  async function ensurePushSubscription() {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      throw new Error("unsupported");
    }
    const keyRes = await fetch("/api/vapid-public-key");
    const keyData = await keyRes.json();
    if (!keyData.configured || !keyData.publicKey) {
      throw new Error("vapid_not_configured");
    }
    const perm = await Notification.requestPermission();
    if (perm !== "granted") {
      throw new Error("permission_denied");
    }
    const reg = await navigator.serviceWorker.register("/sw.js?v=2", { scope: "/" });
    await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(keyData.publicKey),
      });
    }
    const res = await fetch("/api/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(sub.toJSON()),
    });
    if (!res.ok) throw new Error("subscribe_failed");
    return sub;
  }

  async function showLocalNotification(title, body, url) {
    try {
      const reg = await navigator.serviceWorker.ready;
      if (reg && "showNotification" in reg) {
        await reg.showNotification(title, {
          body,
          icon: "/static/favicon.png?v=4",
          badge: "/static/favicon.png?v=4",
          tag: "instablack-local",
          data: { url: url || "/perfil" },
        });
        return;
      }
    } catch (_) {}
    if ("Notification" in window && Notification.permission === "granted") {
      new Notification(title, { body, icon: "/static/favicon.png?v=4" });
    }
  }

  function updatePushStatusProfile(text, on) {
    const status = document.getElementById("push-status-profile");
    if (!status) return;
    if (text) status.textContent = text;
    status.classList.toggle("push-status--on", !!on);
  }

  function markPushButtonsEnabled() {
    document.querySelectorAll("[data-push-btn]").forEach((b) => {
      b.textContent = "Notificações ativadas ✓";
      b.disabled = true;
    });
    updatePushStatusProfile("Dispositivo registrado — alertas ativos neste navegador.", true);
  }

  async function activateWebPush(triggerBtn) {
    if (triggerBtn) triggerBtn.disabled = true;
    try {
      await ensurePushSubscription();
      markPushButtonsEnabled();
      alert("Notificações no celular ativadas!");
    } catch (err) {
      console.error(err);
      if (err.message === "unsupported") {
        alert("Seu navegador não suporta push. Use Chrome no Android ou Safari no iOS.");
      } else if (err.message === "permission_denied") {
        alert("Permissão negada. Ative nas configurações do navegador.");
      } else if (err.message === "vapid_not_configured") {
        alert("Web Push não configurado no servidor (VAPID).");
      } else {
        alert("Não foi possível ativar. Use HTTPS e tente de novo.");
      }
    } finally {
      if (triggerBtn && triggerBtn.textContent !== "Notificações ativadas ✓") {
        triggerBtn.disabled = false;
      }
    }
  }

  function initWebPush() {
    const buttons = document.querySelectorAll("[data-push-btn]");
    buttons.forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        await activateWebPush(btn);
      });
    });

    if ("Notification" in window && Notification.permission === "granted") {
      navigator.serviceWorker.register("/sw.js?v=2", { scope: "/" }).then(() => {
        markPushButtonsEnabled();
      }).catch(() => {});
    }
  }

  function initProfileNotifications() {
    const testBtn = document.getElementById("btn-test-notify");
    const installBtn = document.getElementById("btn-pwa-install-profile");
    const prefsForm = document.getElementById("notify-prefs-form");
    if (!testBtn && !installBtn && !prefsForm) return;

    const isIos = /iphone|ipad|ipod/i.test(navigator.userAgent);
    if (installBtn && isIos) {
      installBtn.hidden = false;
      installBtn.textContent = "Instalar app na tela do celular";
      installBtn.addEventListener("click", () => {
        alert("No iPhone: toque em Compartilhar → Adicionar à Tela de Início, depois abra o app e teste as notificações.");
      });
    } else if (installBtn) {
      installBtn.addEventListener("click", async () => {
        if (!deferredPwaPrompt) {
          alert("Use o menu do navegador → Instalar app / Adicionar à tela inicial.");
          return;
        }
        deferredPwaPrompt.prompt();
        await deferredPwaPrompt.userChoice;
        deferredPwaPrompt = null;
        installBtn.textContent = "App instalado ✓";
      });
    }

    testBtn?.addEventListener("click", async () => {
      testBtn.disabled = true;
      try {
        const desktopOn = prefsForm?.querySelector('input[name="desktop"]')?.checked;
        if (!desktopOn) {
          alert("Marque \"Notificações do navegador\" e salve antes de testar.");
          return;
        }
        await ensurePushSubscription();
        markPushButtonsEnabled();
        const res = await fetch("/api/push/test", { method: "POST" });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.message || data.error || "Falha no teste");
        }
        await showLocalNotification(
          "instablack — teste OK",
          "Notificações no celular funcionando!",
          "/perfil"
        );
        alert(`Teste enviado! ${data.sent || 0} dispositivo(s) notificado(s).`);
      } catch (err) {
        console.error(err);
        alert(err.message || "Não foi possível testar. Aceite a permissão e tente de novo.");
      } finally {
        testBtn.disabled = false;
      }
    });

    prefsForm?.addEventListener("submit", async () => {
      const desktopOn = prefsForm.querySelector('input[name="desktop"]')?.checked;
      if (desktopOn && "Notification" in window && Notification.permission === "default") {
        try {
          await ensurePushSubscription();
          markPushButtonsEnabled();
        } catch (_) {}
      }
    });
  }

  function formatNotifTime(iso) {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      return d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
    } catch { return ""; }
  }

  async function loadNotifications() {
    const list = document.getElementById("notif-list");
    const dot = document.getElementById("notif-dot");
    if (!list) return;
    try {
      const res = await fetch("/api/notifications");
      if (!res.ok) throw new Error("fail");
      const data = await res.json();
      if (dot) {
        if (data.unread > 0) { dot.hidden = false; } else { dot.hidden = true; }
        const hasOffline = (data.items || []).some(
          (n) => !n.is_read && n.kind === "offline"
        );
        dot.classList.toggle("notif-dot--alert", Boolean(hasOffline));
      }
      // Remove popup flutuante antigo se ainda existir no DOM
      document.getElementById("og-offline-toast-float")?.remove();
      if (!data.items || !data.items.length) {
        list.innerHTML = '<li class="notif-empty">Nenhuma notificação ainda.</li>';
        return;
      }
      list.innerHTML = data.items.map((n) => {
        const cls = `notif-kind-${n.kind || "info"}${n.is_read ? "" : " unread"}`;
        const body = n.body ? `<span>${escapeHtml(n.body)}</span>` : "";
        const link = n.link ? ` data-href="${escapeHtml(n.link)}"` : "";
        return `<li class="${cls}"${link}><strong>${escapeHtml(n.title)}</strong>${body}<time>${formatNotifTime(n.created_at)}</time></li>`;
      }).join("");
      list.querySelectorAll("li[data-href]").forEach((li) => {
        li.style.cursor = "pointer";
        li.addEventListener("click", () => { window.location.href = li.dataset.href; });
      });
    } catch {
      list.innerHTML = '<li class="notif-empty">Não foi possível carregar.</li>';
    }
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function initDashActivityPoll() {
    const panel = document.getElementById("dash-activity-panel");
    const list = document.getElementById("dash-activity-list");
    if (!panel || !list || panel.dataset.pollBound === "1") return;
    if (dashActivityPollTimer) {
      clearInterval(dashActivityPollTimer);
      dashActivityPollTimer = null;
    }
    panel.dataset.pollBound = "1";
    let latest = Number(panel.dataset.latestId || 0);

    const iconFor = (s) => {
      if (s === "success") return "check";
      if (s === "failed") return "x";
      return "minus";
    };
    const labelFor = (s) => ({ success: "Sucesso", failed: "Erro", skipped: "Ignorada" }[s] || s);
    const badgeFor = (s) => {
      if (s === "success") return "badge-green";
      if (s === "failed") return "badge-red";
      return "badge-yellow";
    };

    async function poll() {
      if (document.hidden) return;
      try {
        // Lista vazia: busca as últimas (since_id=0). Depois só deltas.
        const bootstrapping = list.children.length === 0;
        const res = await fetch(
          "/api/logs/latest?since_id=" + (bootstrapping ? 0 : latest)
        );
        if (!res.ok) return;
        const data = await res.json();
        if (!data.items || !data.items.length) {
          if (typeof data.latest_id === "number" && data.latest_id > latest) {
            latest = data.latest_id;
            panel.dataset.latestId = String(latest);
          }
          return;
        }
        const empty = document.getElementById("dash-activity-empty");
        if (empty) empty.hidden = true;
        list.hidden = false;
        for (const item of data.items.reverse()) {
          if (list.querySelector('[data-log-id="' + item.id + '"]')) continue;
          const when = item.created_at
            ? new Date(item.created_at).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
            : "";
          const li = document.createElement("li");
          li.className = "og-timeline-item";
          li.dataset.logId = String(item.id);
          li.innerHTML =
            '<span class="og-timeline-icon og-timeline-icon--' + item.status + '"><i data-lucide="' + iconFor(item.status) + '"></i></span>' +
            '<div class="og-timeline-body"><strong><span class="ig-handle">@' + escapeHtml(item.username || "?") +
            "</span>" +
            (item.automation ? " · " + escapeHtml(item.automation) : "") +
            "</strong><span>" + when + "</span></div>" +
            '<span class="og-badge og-timeline-badge ' + badgeFor(item.status) + '">' + labelFor(item.status) + "</span>";
          list.prepend(li);
          latest = Math.max(latest, item.id);
          panel.dataset.latestId = String(latest);
          while (list.children.length > 12) list.removeChild(list.lastElementChild);
        }
        try { if (window.lucide) lucide.createIcons(); } catch (_) {}
        if (!bootstrapping) {
          try { loadNotifications(); } catch (_) {}
        }
      } catch (_) {}
    }

    window.setTimeout(poll, 2000);
    dashActivityPollTimer = setInterval(poll, 15000);
  }

  function initLogsClearForm() {
    const form = document.getElementById("logs-clear-form");
    if (!form || form.dataset.bound === "1") return;
    form.dataset.bound = "1";
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!confirm("Limpar a aba de logs? O ranking e as visualizações NÃO serão apagados.")) {
        return;
      }
      const btn = document.getElementById("logs-clear-btn");
      if (btn) {
        btn.disabled = true;
        btn.textContent = "Limpando…";
      }
      try {
        const res = await fetch("/logs/clear", {
          method: "POST",
          headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(data.detail || "Falha ao limpar logs");
        }
        window.location.href = data.redirect || "/logs?ok=cleared";
      } catch (_) {
        form.submit();
      }
    });
  }

  function initLogsWatchPoll() {
    const params = new URLSearchParams(window.location.search);
    if (params.get("watch") !== "1") return;
    const tbody = document.querySelector(".og-table tbody");
    const panel = document.querySelector(".og-table-panel");
    if (!panel) return;
    if (panel.dataset.watchBound === "1") return;
    panel.dataset.watchBound = "1";

    let latest = 0;
    tbody?.querySelectorAll("tr[data-log-id]").forEach((tr) => {
      latest = Math.max(latest, Number(tr.dataset.logId || 0));
    });
    if (!latest) {
      latest = Number(panel.dataset.latestId || 0);
    }

    const badgeFor = (s) => {
      if (s === "success") return "badge-green";
      if (s === "failed") return "badge-red";
      return "badge-yellow";
    };
    const labelFor = (s) => ({ success: "Sucesso", failed: "Erro", skipped: "Ignorada" }[s] || s);

    let ticks = 0;
    const maxTicks = 60; // ~2 min a 2s

    async function poll() {
      ticks += 1;
      if (ticks > maxTicks) return;
      try {
        const res = await fetch("/api/logs/latest?since_id=" + latest);
        if (!res.ok) return;
        const data = await res.json();
        if (!data.items || !data.items.length) {
          if (ticks < maxTicks) setTimeout(poll, 2000);
          return;
        }
        let table = document.querySelector(".og-table");
        if (!table) {
          const wrap = document.querySelector(".og-table-wrap");
          if (wrap) {
            wrap.innerHTML =
              '<table class="og-table"><thead><tr>' +
              "<th>Quando</th><th>Conta</th><th>Automação</th><th>Status</th><th>Detalhe</th>" +
              "</tr></thead><tbody></tbody></table>";
            table = wrap.querySelector(".og-table");
            const empty = wrap.querySelector(".og-empty");
            if (empty) empty.remove();
          }
        }
        const body = table && table.querySelector("tbody");
        if (!body) {
          if (ticks < maxTicks) setTimeout(poll, 2000);
          return;
        }
        for (const item of data.items.reverse()) {
          if (body.querySelector('tr[data-log-id="' + item.id + '"]')) continue;
          const when = item.created_at
            ? new Date(item.created_at).toLocaleString("pt-BR", {
                day: "2-digit", month: "2-digit", year: "numeric",
                hour: "2-digit", minute: "2-digit", second: "2-digit",
              })
            : "";
          let detail = "—";
          if (item.media_url) {
            detail = '<a href="' + escapeHtml(item.media_url) + '" target="_blank" rel="noopener">Abrir post</a>';
          } else if (item.error) {
            detail = '<span class="og-muted log-error-cell">' + escapeHtml(item.error) + "</span>";
          }
          const tr = document.createElement("tr");
          tr.dataset.logId = String(item.id);
          tr.className = "log-row-new";
          tr.innerHTML =
            '<td class="og-muted">' + when + "</td>" +
            "<td><strong class=\"ig-handle\">@" + escapeHtml(item.username || "?") + "</strong></td>" +
            '<td class="og-muted">' + escapeHtml(item.automation || "Post imediato") + "</td>" +
            '<td><span class="og-badge ' + badgeFor(item.status) + '">' + labelFor(item.status) + "</span></td>" +
            "<td>" + detail + "</td>";
          body.prepend(tr);
          latest = Math.max(latest, item.id);
          panel.dataset.latestId = String(latest);
        }
        loadNotifications();
      } catch (_) {}
      if (ticks < maxTicks) setTimeout(poll, 2000);
    }

    setTimeout(poll, 1500);
  }

  function initNotifCard() {
    const wrap = document.getElementById("notif-wrap");
    const btn = document.getElementById("notif-bell-btn");
    const card = document.getElementById("notif-card");
    const markBtn = document.getElementById("notif-mark-read");
    const clearBtn = document.getElementById("notif-clear-all");
    if (!btn || !card) return;
    if (btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";

    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const open = card.hasAttribute("hidden");
      if (open) {
        card.removeAttribute("hidden");
        btn.setAttribute("aria-expanded", "true");
        loadNotifications();
      } else {
        card.setAttribute("hidden", "");
        btn.setAttribute("aria-expanded", "false");
      }
    });

    document.addEventListener("click", (e) => {
      if (wrap && !wrap.contains(e.target) && !card.hasAttribute("hidden")) {
        card.setAttribute("hidden", "");
        btn.setAttribute("aria-expanded", "false");
      }
    });

    markBtn?.addEventListener("click", async (e) => {
      e.stopPropagation();
      await fetch("/api/notifications/read", { method: "POST" });
      loadNotifications();
    });

    clearBtn?.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm("Limpar todas as notificações do sino?")) return;
      const list = document.getElementById("notif-list");
      const dot = document.getElementById("notif-dot");
      try {
        const res = await fetch("/api/notifications/clear", { method: "POST" });
        if (!res.ok) throw new Error("fail");
        if (list) list.innerHTML = '<li class="notif-empty">Nenhuma notificação ainda.</li>';
        if (dot) dot.hidden = true;
      } catch {
        if (list) list.innerHTML = '<li class="notif-empty">Não foi possível limpar.</li>';
      }
    });

    // Notificações só quando abrir o sino — evita saturar o worker web no load.
  }

  function initContentTypeForm() {
    const sel = document.getElementById("content-type");
    const mediaLabel = document.getElementById("media-label");
    const captionWrap = document.getElementById("caption-wrap");
    const thumbWrap = document.getElementById("thumb-wrap");
    const camouflageWrap = document.getElementById("camouflage-wrap");
    const storyLinkWrap = document.getElementById("story-link-wrap");
    const videoInput = document.getElementById("video-input");
    const videoList = document.getElementById("video-file-list");
    const reelUploadHelp = document.getElementById("reel-upload-help");
    if (!sel) return;

    const params = new URLSearchParams(window.location.search);
    const pathType = window.location.pathname.endsWith("/story") ? "story" : null;
    if (params.get("type") === "story" || pathType === "story") sel.value = "story";

    function update() {
      const t = sel.value;
      if (t === "story") {
        if (mediaLabel) mediaLabel.firstChild.textContent = "Mídias dos Stories (fotos ou vídeos) ";
        if (videoInput) {
          videoInput.name = "video";
          videoInput.setAttribute("multiple", "multiple");
          videoInput.accept = "image/jpeg,image/png,image/webp,video/mp4,video/quicktime";
        }
        if (captionWrap) captionWrap.style.display = "none";
        const capField = document.getElementById("caption-field");
        if (capField) capField.removeAttribute("required");
        if (thumbWrap) thumbWrap.style.display = "none";
        if (camouflageWrap) camouflageWrap.style.display = "none";
        if (storyLinkWrap) storyLinkWrap.style.display = "";
        if (reelUploadHelp) reelUploadHelp.style.display = "none";
      } else if (t === "photo") {
        if (mediaLabel) mediaLabel.firstChild.textContent = "Foto para o feed (.jpg/.png) ";
        if (videoInput) {
          videoInput.name = "video";
          videoInput.removeAttribute("multiple");
          videoInput.accept = "image/jpeg,image/png,image/webp";
        }
        if (videoList) videoList.style.display = "none";
        if (captionWrap) captionWrap.style.display = "";
        const capFieldPhoto = document.getElementById("caption-field");
        if (capFieldPhoto) capFieldPhoto.setAttribute("required", "required");
        if (thumbWrap) thumbWrap.style.display = "none";
        if (camouflageWrap) camouflageWrap.style.display = "none";
        if (storyLinkWrap) storyLinkWrap.style.display = "none";
        if (reelUploadHelp) reelUploadHelp.style.display = "none";
      } else {
        if (mediaLabel) mediaLabel.firstChild.textContent = "Vídeos Reels (.mp4) ";
        if (videoInput) {
          videoInput.name = "videos";
          videoInput.setAttribute("multiple", "multiple");
          videoInput.accept = "video/mp4,video/quicktime,video/webm";
        }
        if (captionWrap) captionWrap.style.display = "";
        const capFieldReel = document.getElementById("caption-field");
        if (capFieldReel) capFieldReel.setAttribute("required", "required");
        if (thumbWrap) thumbWrap.style.display = "";
        if (camouflageWrap) camouflageWrap.style.display = "";
        if (storyLinkWrap) storyLinkWrap.style.display = "none";
        if (reelUploadHelp) reelUploadHelp.style.display = "block";
      }
      document.dispatchEvent(new CustomEvent("automation-media-changed"));
    }
    sel.addEventListener("change", () => {
      if (sel.value === "story" && !window.location.pathname.endsWith("/story")) {
        window.location.href = "/automations/new/story";
        return;
      }
      // Na página de Story, trocar para Reels/Foto vai para o formulário certo
      // (evita postar Story em /new e “voltar” pra tela de Reels no erro).
      if (
        window.location.pathname.endsWith("/story") &&
        sel.value !== "story"
      ) {
        window.location.href =
          sel.value === "photo"
            ? "/automations/new?type=photo"
            : "/automations/new";
        return;
      }
      update();
    });
    update();
  }

  /** Editar automação na lista: Story não pede legenda. */
  function initEditAutomationCaption() {
    document.querySelectorAll("select.edit-content-type").forEach((sel) => {
      const form = sel.closest("form");
      if (!form) return;
      const wrap = form.querySelector(".edit-caption-wrap");
      const field = form.querySelector(".edit-caption-field");
      if (!wrap || !field) return;

      function sync() {
        if (sel.value === "story") {
          wrap.style.display = "none";
          field.removeAttribute("required");
        } else {
          wrap.style.display = "";
          field.setAttribute("required", "required");
        }
      }
      sel.addEventListener("change", sync);
      sync();
    });
  }

  function initThumbPreview() {
    const input = document.getElementById("thumb-input");
    const preview = document.getElementById("thumb-preview");
    if (!input || !preview) return;
    input.addEventListener("change", () => {
      const f = input.files[0];
      if (f) { preview.src = URL.createObjectURL(f); preview.style.display = "block"; }
    });
  }

  function initScheduleMode() {
    const modeNow = document.getElementById("mode-now");
    const modeRecurring = document.getElementById("mode-recurring");
    const modeCalendar = document.getElementById("mode-calendar");
    const intervalWrap = document.getElementById("interval-wrap");
    const calendarWrap = document.getElementById("calendar-wrap");
    const submitBtn = document.getElementById("submit-btn");
    const contentType = document.getElementById("content-type");
    if (!modeNow && !modeCalendar) return;

    function update() {
      const isNow = modeNow?.checked;
      const isCalendar = modeCalendar?.checked;
      const isStory = contentType?.value === "story";
      const pathStory = window.location.pathname.endsWith("/story");
      const storyMode = isStory || pathStory;
      const showInterval = Boolean(modeRecurring?.checked) || (!storyMode && !isNow && !isCalendar);
      if (intervalWrap) intervalWrap.style.display = showInterval ? "" : "none";
      if (calendarWrap) calendarWrap.style.display = isCalendar ? "" : "none";
      if (submitBtn) {
        if (isNow) {
          submitBtn.textContent = isStory ? "Postar Story agora" : "Publicar agora";
        } else if (isCalendar) {
          submitBtn.textContent = isStory ? "Agendar Story" : "Criar agendamento";
        } else {
          submitBtn.textContent = isStory ? "Agendar Story" : "Criar automação";
        }
      }
    }
    modeNow?.addEventListener("change", update);
    modeRecurring?.addEventListener("change", update);
    modeCalendar?.addEventListener("change", update);
    contentType?.addEventListener("change", update);
    update();
  }

  function initMetaIntervalFilter() {
    function applyFilter(root) {
      const scope = root || document;
      const selects = scope.querySelectorAll("#interval-minutes-select, .interval-minutes-select");
      selects.forEach((select) => {
        const form = select.closest("form") || document;
        const metaMin = parseInt(select.dataset.metaMin || "60", 10) || 60;
        const warmupDays = parseInt(select.dataset.metaWarmupDays || "7", 10) || 7;
        const warmupMin = parseInt(select.dataset.metaWarmupMin || "180", 10) || 180;
        const warmupEnabled = (select.dataset.metaWarmupEnabled || "1") !== "0";
        const checked = form.querySelectorAll('input[name="account_ids"]:checked');
        let hasMeta = false;
        let hasWarmup = false;
        let effectiveMin = metaMin;
        checked.forEach((cb) => {
          if ((cb.dataset.provider || "") !== "meta") return;
          hasMeta = true;
          if (!warmupEnabled) return;
          if ((cb.dataset.warmupActive || "0") === "1") {
            hasWarmup = true;
            effectiveMin = Math.max(effectiveMin, warmupMin);
          }
        });
        const current = parseInt(select.value, 10);
        let firstVisible = null;
        Array.from(select.options).forEach((opt) => {
          const minutes = parseInt(opt.dataset.minutes || opt.value, 10);
          const hide = hasMeta && minutes < effectiveMin;
          opt.hidden = hide;
          opt.disabled = hide;
          if (!hide && firstVisible === null) firstVisible = minutes;
        });
        if (hasMeta && current < effectiveMin && firstVisible !== null) {
          select.value = String(firstVisible);
        }
        const hint = form.querySelector("#meta-interval-hint, .meta-interval-hint");
        if (hint) hint.style.display = hasMeta && !hasWarmup ? "block" : "none";
        const warmHint = form.querySelector("#meta-warmup-hint, .meta-warmup-hint");
        if (warmHint) warmHint.style.display = hasWarmup ? "block" : "none";
      });
    }

    document.querySelectorAll('input[name="account_ids"]').forEach((cb) => {
      cb.addEventListener("change", () => applyFilter(cb.closest("form") || document));
    });
    applyFilter(document);
  }

  function normalizeProxyValue(raw) {
    const value = raw.trim();
    if (!value || value.includes("://")) return value;
    const parts = value.split(":");
    if (parts.length === 4) {
      const [host, port, user, pass] = parts;
      return `http://${user}:${pass}@${host}:${port}`;
    }
    if (parts.length === 2) return `http://${parts[0]}:${parts[1]}`;
    return value;
  }

  function initProxyInput() {
    document.querySelectorAll(".proxy-update-input, #account-proxy-input").forEach((input) => {
      input.addEventListener("blur", () => {
        input.value = normalizeProxyValue(input.value);
      });
    });
  }

  function initAccountProxyUpdate() {
    document.querySelectorAll(".proxy-update-form").forEach((form) => {
      const input = form.querySelector(".proxy-update-input");
      const testBtn = form.querySelector(".proxy-test-btn");
      const result = form.querySelector(".proxy-test-result");

      async function runTest() {
        if (!input?.value.trim()) {
          if (result) {
            result.textContent = "Informe o proxy antes de testar.";
            result.className = "proxy-test-result fail";
          }
          return;
        }
        if (testBtn) { testBtn.disabled = true; testBtn.textContent = "Testando…"; }
        if (result) { result.textContent = "Testando proxy…"; result.className = "proxy-test-result muted"; }
        const fd = new FormData();
        fd.set("proxy", normalizeProxyValue(input.value.trim()));
        try {
          const resp = await fetch("/accounts/test-proxy", { method: "POST", body: fd });
          const data = await resp.json();
          if (result) {
            if (data.ok) {
              const geo = data.geo ? " · " + data.geo : "";
              result.textContent = "OK — IP: " + data.ip + geo;
              result.className = "proxy-test-result ok";
            } else {
              result.textContent = data.error || "Proxy inválido";
              result.className = "proxy-test-result fail";
            }
          }
        } catch {
          if (result) {
            result.textContent = "Falha ao testar proxy.";
            result.className = "proxy-test-result fail";
          }
        } finally {
          if (testBtn) { testBtn.disabled = false; testBtn.textContent = "Testar"; }
        }
      }

      testBtn?.addEventListener("click", runTest);
      form.addEventListener("submit", (e) => {
        if (input) input.value = normalizeProxyValue(input.value.trim());
      });
    });
  }

  function initProfileEditForm() {
    const form = document.getElementById("profile-edit-form");
    if (!form || form.dataset.bound === "1") return;
    form.dataset.bound = "1";

    const all = document.getElementById("profile-edit-select-all");
    if (all) {
      all.addEventListener("change", () => {
        document.querySelectorAll(".profile-edit-acc-cb").forEach((cb) => {
          cb.checked = all.checked;
        });
      });
    }

    const busy = document.getElementById("profile-edit-busy");
    const busyStatus = document.getElementById("profile-edit-busy-status");
    const btn = document.getElementById("profile-edit-submit");
    const hint = document.getElementById("profile-edit-hint");
    const liveBox = document.getElementById("profile-edit-live");
    const liveList = document.getElementById("profile-edit-live-list");
    const liveTitle = document.getElementById("profile-edit-live-title");

    function addResult(username, ok, detail) {
      if (!liveList) return;
      const li = document.createElement("li");
      li.className = ok ? "ok" : "fail";
      li.textContent = `@${username} — ${ok ? `OK (${detail})` : detail}`;
      liveList.appendChild(li);
      if (liveBox) liveBox.hidden = false;
    }

    // Uma requisição por conta: cada chamada ao Instagram é lenta, e um POST
    // único com muitas contas estoura o timeout do servidor.
    async function applyToAccount(accountId, username) {
      const data = new FormData();
      data.append("account_id", accountId);
      data.append("biography", form.querySelector('textarea[name="biography"]')?.value || "");
      const fileInput = form.querySelector("#profile-pic-input");
      if (fileInput && fileInput.files && fileInput.files[0]) {
        data.append("profile_pic", fileInput.files[0]);
      }

      try {
        const res = await fetch("/accounts/profile-edit/one", {
          method: "POST",
          body: data,
          credentials: "same-origin",
          headers: { "X-Requested-With": "fetch" },
        });
        let payload = null;
        try {
          payload = await res.json();
        } catch (_) {
          payload = null;
        }
        if (!payload) {
          return { ok: false, detail: `Servidor respondeu ${res.status}.` };
        }
        return { ok: !!payload.ok, detail: payload.detail || String(res.status) };
      } catch (err) {
        return { ok: false, detail: "Conexão interrompida (tente de novo)." };
      }
    }

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();

      const checked = Array.from(form.querySelectorAll(".profile-edit-acc-cb:checked"));
      if (!checked.length) {
        alert("Selecione ao menos uma conta.");
        return;
      }
      const bio = (form.querySelector('textarea[name="biography"]')?.value || "").trim();
      const fileInput = form.querySelector("#profile-pic-input");
      const hasFile = !!(fileInput && fileInput.files && fileInput.files.length > 0);
      if (!bio && !hasFile) {
        alert("Informe a bio e/ou escolha uma foto de perfil.");
        return;
      }

      if (liveList) liveList.innerHTML = "";
      if (liveBox) liveBox.hidden = true;
      if (busy) busy.hidden = false;
      if (btn) {
        btn.disabled = true;
        btn.textContent = "Enviando…";
      }

      const total = checked.length;
      let done = 0;
      let okCount = 0;
      for (const cb of checked) {
        const username =
          cb.closest(".profile-edit-account")?.querySelector(".ig-handle")?.textContent?.replace("@", "") ||
          cb.value;
        if (busyStatus) {
          busyStatus.textContent = `Conta ${done + 1} de ${total} · @${username}`;
        }
        // eslint-disable-next-line no-await-in-loop
        const result = await applyToAccount(cb.value, username);
        done += 1;
        if (result.ok) okCount += 1;
        addResult(username, result.ok, result.detail);
        if (liveTitle) liveTitle.textContent = `Resultado (${okCount}/${done})`;
      }

      if (busy) busy.hidden = true;
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Aplicar nas selecionadas";
      }
      if (hint) {
        hint.textContent = `${okCount}/${total} conta(s) atualizada(s).`;
      }
    });
  }

  function initAuthMethodForm() {
    const form = document.getElementById("account-add-form");
    if (!form) return;
    const passwordInput = document.getElementById("account-password-input");
    const proxyInput = document.getElementById("account-proxy-input");
    const connectBtn = document.getElementById("account-connect-btn");
    const radios = form.querySelectorAll('input[name="auth_method"]');

    function update() {
      const method = form.querySelector('input[name="auth_method"]:checked')?.value || "meta";
      const isMeta = method === "meta";
      if (passwordInput) {
        passwordInput.required = method === "password" || method === "aiograpi";
      }
      if (proxyInput) {
        proxyInput.required = !isMeta;
        if (isMeta) proxyInput.removeAttribute("required");
      }
      if (connectBtn) {
        connectBtn.hidden = isMeta;
      }
    }
    radios.forEach((r) => {
      r.addEventListener("change", update);
      r.addEventListener("click", update);
    });
    update();
  }

  let twofaHasTotp = false;
  let twofaAccountId = null;

  function openTwofaModal(message, opts) {
    const modal = document.getElementById("twofa-modal");
    const codeInput = document.getElementById("twofa-code-input");
    const msgEl = document.getElementById("twofa-message");
    const useSaved = document.getElementById("twofa-use-saved");
    if (!modal) return;
    twofaHasTotp = !!(opts && opts.hasTotp);
    twofaAccountId = opts && opts.accountId ? opts.accountId : null;
    if (msgEl && message) msgEl.textContent = message;
    if (useSaved) {
      useSaved.hidden = !twofaHasTotp;
    }
    modal.classList.add("modal-overlay--open");
    modal.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    if (codeInput) {
      codeInput.value = "";
      setTimeout(() => codeInput.focus(), 50);
    }
  }

  function closeTwofaModal() {
    const modal = document.getElementById("twofa-modal");
    const hiddenCode = document.getElementById("verification-code-hidden");
    const useSaved = document.getElementById("twofa-use-saved");
    if (!modal) return;
    modal.classList.remove("modal-overlay--open");
    modal.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
    if (hiddenCode) hiddenCode.value = "";
    if (useSaved) useSaved.hidden = true;
    twofaHasTotp = false;
    twofaAccountId = null;
  }

  async function fillTwofaFromVault() {
    const accountId =
      twofaAccountId ||
      pendingReconnect?.accountId ||
      null;
    const codeInput = document.getElementById("twofa-code-input");
    if (!accountId || !codeInput) {
      alert("Salve a chave TOTP em Credenciais / 2FA nesta conta.");
      return;
    }
    try {
      const res = await fetch(`/accounts/${accountId}/totp-code`, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.code) {
        alert(data.error || "Não foi possível gerar o código TOTP.");
        return;
      }
      codeInput.value = data.code;
      codeInput.focus();
    } catch (err) {
      alert(err.message || "Erro ao buscar código TOTP");
    }
  }

  function initTwofaModal() {
    const modal = document.getElementById("twofa-modal");
    if (!modal || modal.dataset.bound === "1") return;
    modal.dataset.bound = "1";
    const cancelBtn = document.getElementById("twofa-cancel");
    const submitBtn = document.getElementById("twofa-submit");
    const codeInput = document.getElementById("twofa-code-input");
    const useSaved = document.getElementById("twofa-use-saved");

    cancelBtn?.addEventListener("click", closeTwofaModal);
    useSaved?.addEventListener("click", fillTwofaFromVault);
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeTwofaModal();
    });
    submitBtn?.addEventListener("click", () => {
      if (pendingReconnect) {
        submitReconnect2fa();
        return;
      }
      const form = document.getElementById("account-add-form");
      if (form && typeof form._submitWith2fa === "function") {
        form._submitWith2fa(true);
      }
    });
    codeInput?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        if (pendingReconnect) {
          submitReconnect2fa();
          return;
        }
        const form = document.getElementById("account-add-form");
        if (form && typeof form._submitWith2fa === "function") {
          form._submitWith2fa(true);
        }
      }
    });
  }

  async function submitReconnect2fa() {
    if (!pendingReconnect) return;
    const codeInput = document.getElementById("twofa-code-input");
    const submitBtn = document.getElementById("twofa-submit");
    const code = (codeInput?.value || "").trim();
    if (!code) {
      alert("Digite o código 2FA.");
      return;
    }
    const { accountId, payload } = pendingReconnect;
    if (submitBtn) submitBtn.disabled = true;
    try {
      const data = await reconnectAccountApi(accountId, {
        ...payload,
        verification_code: code,
      });
      if (data.status === "connected") {
        pendingReconnect = null;
        closeTwofaModal();
        window.location.href = "/accounts/connected?ok=session_reconnected";
      } else if (data.status === "needs_2fa") {
        alert("Código incorreto. Tente novamente.");
      } else {
        pendingReconnect = null;
        closeTwofaModal();
        alert(data.message || "Falha ao reconectar");
      }
    } catch (err) {
      alert(err.message || "Erro ao reconectar");
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  function initAccountsConnect() {
    const form = document.getElementById("account-add-form");
    const codeInput = document.getElementById("twofa-code-input");
    const connectBtn = document.getElementById("account-connect-btn");
    if (!form || form.dataset.connectInit === "1") return;
    form.dataset.connectInit = "1";

    initTwofaModal();

    if (document.getElementById("needs-2fa-flag")) {
      openTwofaModal();
    }

    async function submitForm(with2fa) {
      const fd = new FormData(form);
      const proxyInput = form.querySelector('[name="proxy"]');
      if (proxyInput) fd.set("proxy", normalizeProxyValue(proxyInput.value));
      if (with2fa) {
        const code = codeInput?.value.trim() || "";
        if (!code) {
          alert("Digite o código 2FA do autenticador.");
          codeInput?.focus();
          return;
        }
        fd.set("verification_code", code);
      }
      if (connectBtn) { connectBtn.disabled = true; connectBtn.textContent = "Conectando…"; }
      try {
        const resp = await fetch(form.action, {
          method: "POST",
          body: fd,
          headers: { "X-Requested-With": "fetch", Accept: "application/json, text/html" },
          redirect: "manual",
        });
        if (resp.status === 303 || resp.status === 302) {
          closeTwofaModal();
          try {
            sessionStorage.setItem("ib_flash_ok", "Conta conectada com sucesso!");
          } catch (_) {
            /* ignore */
          }
          window.location.href = resp.headers.get("Location") || "/accounts/connected?ok=account_added";
          return;
        }
        const ct = resp.headers.get("content-type") || "";
        if (ct.includes("application/json")) {
          const data = await resp.json();
          if (data.ok && data.redirect) {
            closeTwofaModal();
            try {
              sessionStorage.setItem("ib_flash_ok", data.message || "Conta conectada com sucesso!");
            } catch (_) {
              /* ignore */
            }
            window.location.href = data.redirect;
            return;
          }
          if (resp.status === 403 && data.needs_2fa) {
            const formTotp = !!(form.querySelector('[name="totp_secret"]')?.value || "").trim();
            openTwofaModal(data.message, {
              hasTotp: !!(data.has_totp || formTotp),
            });
            return;
          }
        }
        if (resp.ok || resp.status === 400 || resp.status === 403) {
          const html = await resp.text();
          const doc = new DOMParser().parseFromString(html, "text/html");
          const newContent = doc.getElementById("app-content");
          if (newContent && appContent) {
            appContent.innerHTML = newContent.innerHTML;
            history.pushState({ url: "/accounts" }, "", "/accounts");
            initPage();
            if (doc.getElementById("needs-2fa-flag")) {
              openTwofaModal();
            }
            return;
          }
        }
        window.location.href = "/accounts";
      } catch {
        form.submit();
      } finally {
        if (connectBtn) { connectBtn.disabled = false; connectBtn.textContent = "Conectar conta"; }
      }
    }

    form._submitWith2fa = submitForm;

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      submitForm(false);
    });
  }

  let pendingReconnect = null;

  async function reconnectAccountApi(accountId, payload) {
    const res = await fetch(`/accounts/${accountId}/reconnect/api`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload || { mode: "auto" }),
      credentials: "same-origin",
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok && !data.status) {
      throw new Error(data.detail || data.message || "Falha ao reconectar");
    }
    return data;
  }

  function handleReconnectResult(data, accountId, username) {
    if (data.status === "connected") {
      window.location.href = "/accounts/connected?ok=session_reconnected";
      return;
    }
    if (data.status === "needs_2fa") {
      pendingReconnect = {
        accountId,
        payload: {
          ...(pendingReconnect?.payload || { mode: "password" }),
          mode: pendingReconnect?.payload?.mode || "password",
        },
      };
      openTwofaModal(`Digite o código 2FA da conta @${username || data.username || ""}.`, {
        hasTotp: !!data.has_totp,
        accountId,
      });
      return;
    }
    alert(data.message || "Não foi possível reconectar a sessão.");
  }

  async function runReconnect(accountId, username, payload, btn) {
    if (btn) {
      btn.disabled = true;
      btn.dataset.origLabel = btn.textContent;
      btn.textContent = "Conectando…";
    }
    pendingReconnect = { accountId, payload, username };
    try {
      const data = await reconnectAccountApi(accountId, payload);
      handleReconnectResult(data, accountId, username);
    } catch (err) {
      alert(err.message || "Erro ao reconectar");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = btn.dataset.origLabel || "Reconectar";
      }
    }
  }

  function initAccountsReconnect() {
    const marker =
      document.body.dataset.pageAccountsConnected ||
      document.querySelector("[data-page-accounts-connected]");
    if (!marker) return;
    document.body.dataset.pageAccountsConnected = "1";
    initTwofaModal();
    // Cofre virou página /accounts/vault (initVaultPage)

    const modal = document.getElementById("reconnect-session-modal");
    let reconnectTarget = null;

    function closeReconnectModal() {
      if (!modal) return;
      modal.classList.remove("modal-overlay--open");
      modal.setAttribute("aria-hidden", "true");
      document.body.style.overflow = "";
      reconnectTarget = null;
      const sid = document.getElementById("reconnect-modal-sessionid");
      const cookies = document.getElementById("reconnect-modal-cookies");
      if (sid) sid.value = "";
      if (cookies) cookies.value = "";
    }

    function openReconnectModal(accountId, username, hasCookies, hasPassword, opts) {
      opts = opts || {};
      if (!modal) {
        // Fallback: modal sumiu (SPA antiga) — tenta senha do cofre direto.
        if (hasPassword && !opts.nativeChallenge) {
          runReconnect(accountId, username, { mode: "password" }, null);
          return;
        }
        alert(
          opts.nativeChallenge
            ? "Esta conta pediu verificação manual no Instagram (challenge). Abra o app/site do Instagram, resolva o checkpoint, depois cole um sessionid/cookies novos aqui. Se o modal não abrir, recarregue com F5."
            : "Não foi possível abrir o modal de reconectar. Recarregue a página (F5) e tente de novo, ou cole sessionid/cookies."
        );
        return;
      }
      // Evita clip do overflow do #app-content (position:fixed dentro de scroll).
      if (modal.parentElement !== document.body) {
        document.body.appendChild(modal);
      }
      reconnectTarget = {
        accountId,
        username: username || "",
        hasPassword: !!hasPassword,
        nativeChallenge: !!opts.nativeChallenge,
      };
      const title = document.getElementById("reconnect-session-title");
      const msg = document.getElementById("reconnect-session-message");
      const hint = document.getElementById("reconnect-cookies-hint");
      const pwBtn = document.getElementById("reconnect-modal-password-btn");
      const pwDiv = document.getElementById("reconnect-password-divider");
      if (title) {
        title.textContent = username
          ? `Reconectar @${username}`
          : "Reconectar sessão";
      }
      if (msg) {
        msg.textContent = opts.nativeChallenge
          ? "O Instagram pediu verificação manual (challenge/checkpoint). Senha automática costuma falhar. Abra o app/site do Instagram nessa conta, complete a verificação, depois cole sessionid ou cookies novos abaixo."
          : "Cole o sessionid do navegador ou o JSON do Cookie-Editor. Ou use senha+TOTP salvos no cofre.";
      }
      if (hint) {
        if (opts.nativeChallenge) {
          hint.hidden = false;
          hint.textContent =
            "Dica: após liberar no Instagram, exporte cookies com Cookie-Editor (ou cole só o sessionid) e reconecte aqui.";
        } else if (hasCookies) {
          hint.hidden = false;
          hint.textContent =
            "Esta conta já tem cookies web salvos. Se o sessionid antigo ainda valer, o health check tenta reaproveitar sozinho — se falhou, cole um sessionid/cookies novos.";
        } else {
          hint.hidden = false;
          hint.textContent =
            "Preferível colar o JSON completo do Cookie-Editor (sessionid + csrftoken) para Stories com link.";
        }
      }
      if (pwBtn) pwBtn.hidden = !hasPassword;
      if (pwDiv) pwDiv.hidden = !hasPassword;
      modal.classList.add("modal-overlay--open");
      modal.setAttribute("aria-hidden", "false");
      document.body.style.overflow = "hidden";
      setTimeout(() => document.getElementById("reconnect-modal-sessionid")?.focus(), 40);
    }

    document.querySelectorAll(".account-reconnect-open-btn").forEach((btn) => {
      if (btn.dataset.reconnectBound === "1") return;
      btn.dataset.reconnectBound = "1";
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const id = parseInt(btn.dataset.accountId, 10);
        if (!id) return;
        const err = (btn.dataset.lastError || "").toLowerCase();
        const nativeChallenge = [
          "challenge",
          "checkpoint",
          "manual verification",
          "challenge_code_handler",
        ].some((x) => err.includes(x));
        openReconnectModal(
          id,
          btn.dataset.username || "",
          btn.dataset.hasCookies === "1",
          btn.dataset.hasPassword === "1",
          { nativeChallenge }
        );
      });
    });

    document.getElementById("reconnect-modal-cancel")?.addEventListener("click", closeReconnectModal);
    modal?.addEventListener("click", (e) => {
      if (e.target === modal) closeReconnectModal();
    });

    document.getElementById("reconnect-modal-password-btn")?.addEventListener("click", () => {
      if (!reconnectTarget) return;
      const btn = document.getElementById("reconnect-modal-password-btn");
      runReconnect(
        reconnectTarget.accountId,
        reconnectTarget.username,
        { mode: "password" },
        btn
      );
    });

    document.getElementById("reconnect-modal-sessionid-btn")?.addEventListener("click", () => {
      if (!reconnectTarget) return;
      const sid = (document.getElementById("reconnect-modal-sessionid")?.value || "").trim();
      if (!sid) {
        alert("Cole o sessionid do navegador.");
        return;
      }
      const btn = document.getElementById("reconnect-modal-sessionid-btn");
      runReconnect(
        reconnectTarget.accountId,
        reconnectTarget.username,
        { mode: "sessionid", sessionid: sid },
        btn
      );
    });

    document.getElementById("reconnect-modal-cookies-btn")?.addEventListener("click", () => {
      if (!reconnectTarget) return;
      const cookies = (document.getElementById("reconnect-modal-cookies")?.value || "").trim();
      if (!cookies) {
        alert("Cole o JSON do Cookie-Editor.");
        return;
      }
      const btn = document.getElementById("reconnect-modal-cookies-btn");
      runReconnect(
        reconnectTarget.accountId,
        reconnectTarget.username,
        { mode: "cookies", web_cookies: cookies },
        btn
      );
    });

    // Um dropdown de proxy aberto por vez (evita empilhar absolute).
    document.querySelectorAll(".account-actions .proxy-update-panel").forEach((panel) => {
      panel.addEventListener("toggle", () => {
        if (!panel.open) return;
        document.querySelectorAll(".account-actions .proxy-update-panel").forEach((other) => {
          if (other !== panel) other.open = false;
        });
      });
    });
  }

  function initVaultPage() {
    const onVault =
      document.querySelector("[data-page-vault]") ||
      document.querySelector(".vault-card") ||
      document.querySelector(".auth-panel");
    if (!onVault) {
      if (window.__vaultPoll) {
        clearInterval(window.__vaultPoll);
        window.__vaultPoll = null;
      }
      if (window.__vaultTick) {
        clearInterval(window.__vaultTick);
        window.__vaultTick = null;
      }
      return;
    }
    document.body.dataset.pageVault = "1";

    const RING = 2 * Math.PI * 15.5; // ~97.39
    const PERIOD = 30;

    function setMsg(card, text, kind) {
      const msg = card.querySelector(".vault-msg");
      if (!msg) return;
      msg.textContent = text || "";
      msg.classList.toggle("is-error", kind === "error");
      msg.classList.toggle("is-ok", kind === "ok");
    }

    function formatCode(code) {
      const c = String(code || "").replace(/\s/g, "");
      if (c.length === 6) return c.slice(0, 3) + " " + c.slice(3);
      return c || "------";
    }

    function ensureAuthEntry(accountId, username, email) {
      const list = document.getElementById("vault-auth-list");
      if (!list) return null;
      let item = list.querySelector(`.vault-auth-item[data-account-id="${accountId}"]`);
      if (item) return item;
      const empty = document.getElementById("vault-auth-empty");
      if (empty) empty.remove();
      item = document.createElement("button");
      item.type = "button";
      item.className = "auth-entry vault-auth-item";
      item.dataset.accountId = String(accountId);
      item.dataset.hasTotp = "1";
      item.title = "Clique para copiar";
      item.innerHTML =
        '<div class="auth-entry-main">' +
        `<div class="auth-entry-label">@${username || ""}</div>` +
        (email ? `<div class="auth-entry-sub">${email}</div>` : "") +
        '<div class="auth-entry-code vault-code">------</div>' +
        "</div>" +
        '<div class="auth-timer" aria-hidden="true">' +
        '<svg class="auth-ring" viewBox="0 0 36 36">' +
        '<circle class="auth-ring-bg" cx="18" cy="18" r="15.5" />' +
        `<circle class="auth-ring-fg vault-ring" cx="18" cy="18" r="15.5" stroke-dasharray="${RING}" stroke-dashoffset="0" />` +
        "</svg></div>";
      item.addEventListener("click", () => copyAuthCode(item));
      list.appendChild(item);
      return item;
    }

    function removeAuthEntry(accountId) {
      const list = document.getElementById("vault-auth-list");
      const item = list?.querySelector(`.vault-auth-item[data-account-id="${accountId}"]`);
      item?.remove();
      if (list && !list.querySelector(".vault-auth-item") && !document.getElementById("vault-auth-empty")) {
        const p = document.createElement("p");
        p.className = "auth-empty muted";
        p.id = "vault-auth-empty";
        p.innerHTML =
          'Nenhum Authenticator salvo ainda. Embaixo, cole a <strong>chave secreta</strong> ' +
          "(não o código de 6 dígitos) e clique em Salvar.";
        list.appendChild(p);
      }
    }

    function applyCodeToItem(item, code, secondsRemaining) {
      if (!item) return;
      const codeEl = item.querySelector(".vault-code");
      if (codeEl) codeEl.textContent = formatCode(code);
      item.dataset.code = String(code || "").replace(/\s/g, "");
      item.dataset.remaining = String(secondsRemaining ?? PERIOD);
      item.dataset.syncedAt = String(Date.now());
      updateRing(item);
    }

    function updateRing(item) {
      const ring = item.querySelector(".vault-ring");
      if (!ring) return;
      const syncedAt = Number(item.dataset.syncedAt || Date.now());
      const remAtSync = Number(item.dataset.remaining || PERIOD);
      const elapsed = (Date.now() - syncedAt) / 1000;
      let rem = Math.max(0, remAtSync - elapsed);
      const ratio = rem / PERIOD;
      ring.setAttribute("stroke-dasharray", String(RING));
      ring.setAttribute("stroke-dashoffset", String(RING * (1 - ratio)));
      ring.classList.toggle("is-urgent", rem <= 5);
    }

    async function copyAuthCode(item) {
      const code = (item.dataset.code || item.querySelector(".vault-code")?.textContent || "")
        .replace(/\s/g, "");
      if (!code || code === "------") return;
      try {
        await navigator.clipboard.writeText(code);
        const label = item.querySelector(".auth-entry-label");
        const prev = label?.textContent;
        if (label) {
          label.textContent = "Copiado!";
          setTimeout(() => {
            if (label && prev) label.textContent = prev;
          }, 900);
        }
      } catch {
        alert("Não foi possível copiar.");
      }
    }

    async function refreshAllCodes() {
      const items = document.querySelectorAll(".vault-auth-item[data-has-totp='1']");
      if (!items.length) return;
      try {
        const res = await fetch("/accounts/vault/codes", {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) return;
        const byId = new Map((data.codes || []).map((c) => [String(c.account_id), c]));
        items.forEach((item) => {
          const row = byId.get(String(item.dataset.accountId));
          if (!row) return;
          applyCodeToItem(item, row.code, row.seconds_remaining);
        });
      } catch {
        /* silencioso — tenta de novo no próximo tick */
      }
    }

    async function saveCard(card, payload) {
      const id = card.dataset.accountId;
      if (!id) return;
      setMsg(card, "Salvando…", null);
      try {
        const res = await fetch(`/accounts/${id}/credentials`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          credentials: "same-origin",
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          setMsg(card, data.error || `Falha ao salvar (${res.status}).`, "error");
          return;
        }
        card.dataset.hasTotp = data.has_totp ? "1" : "0";
        const emailInput = card.querySelector(".vault-email");
        const pw = card.querySelector(".vault-password");
        const totp = card.querySelector(".vault-totp");
        if (emailInput) emailInput.value = data.login_email || "";
        if (pw) pw.value = "";
        if (totp) totp.value = "";
        const badge = card.querySelector(".vault-badge");
        if (badge) {
          badge.textContent = data.has_totp ? "Authenticator" : "Sem 2FA";
          badge.classList.toggle("badge-green", !!data.has_totp);
        }
        if (data.has_totp) {
          ensureAuthEntry(id, card.dataset.username || data.username, data.login_email);
          setMsg(card, "Salvo. Código ao vivo no Autenticador acima.", "ok");
          await refreshAllCodes();
        } else {
          removeAuthEntry(id);
          setMsg(card, "Salvo.", "ok");
        }
      } catch (err) {
        setMsg(card, err.message || "Erro ao salvar.", "error");
      }
    }

    document.querySelectorAll(".vault-card").forEach((card) => {
      if (card.dataset.vaultReady === "1") return;
      card.dataset.vaultReady = "1";

      card.querySelector(".vault-save")?.addEventListener("click", () => {
        const login_email = (card.querySelector(".vault-email")?.value || "").trim();
        const password = (card.querySelector(".vault-password")?.value || "").trim();
        const totp_secret = (card.querySelector(".vault-totp")?.value || "").trim();
        if (!password && !totp_secret && login_email === (card.querySelector(".vault-email")?.defaultValue || login_email)) {
          // still allow email-only save
        }
        if (totp_secret && /^\d{6}$/.test(totp_secret.replace(/\s/g, ""))) {
          setMsg(
            card,
            "Cole a chave secreta do Authenticator, não o código de 6 dígitos.",
            "error"
          );
          return;
        }
        const payload = { login_email };
        if (password) payload.password = password;
        if (totp_secret) payload.totp_secret = totp_secret;
        if (!password && !totp_secret && !login_email && card.dataset.hasTotp !== "1") {
          setMsg(card, "Preencha email, senha ou a chave do Authenticator.", "error");
          return;
        }
        saveCard(card, payload);
      });

      card.querySelector(".vault-clear-totp")?.addEventListener("click", () => {
        if (!confirm("Remover Authenticator desta conta?")) return;
        saveCard(card, { clear_totp: true });
      });

      card.querySelector(".vault-clear-password")?.addEventListener("click", () => {
        if (!confirm("Remover senha salva desta conta?")) return;
        saveCard(card, { clear_password: true });
      });
    });

    document.querySelectorAll(".vault-auth-item").forEach((item) => {
      if (item.dataset.copyBound === "1") return;
      item.dataset.copyBound = "1";
      item.addEventListener("click", () => copyAuthCode(item));
    });

    // Poll de códigos (batch) — sem recarregar a página
    if (window.__vaultPoll) clearInterval(window.__vaultPoll);
    refreshAllCodes();
    window.__vaultPoll = setInterval(refreshAllCodes, 1000);

    // Anima o anel entre os fetches
    if (window.__vaultTick) clearInterval(window.__vaultTick);
    window.__vaultTick = setInterval(() => {
      document.querySelectorAll(".vault-auth-item").forEach(updateRing);
    }, 200);
  }

  function initCredsVault() {
    // legado — página dedicada /accounts/vault
  }

  function initCalendarPicker() {
    const grid = document.getElementById("calendar-grid");
    const input = document.getElementById("calendar-days-input");
    const countEl = document.getElementById("cal-count");
    if (!grid || !input) return;

    const selected = new Set();
    const now = new Date();
    const year = now.getFullYear();
    const month = now.getMonth();
    const firstDow = new Date(year, month, 1).getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const today = now.getDate();

    function sync() {
      const arr = Array.from(selected).sort((a, b) => a - b);
      input.value = JSON.stringify(arr);
      if (countEl) countEl.textContent = arr.length + " dia(s) selecionado(s)";
    }

    function toggle(day) {
      if (selected.has(day)) selected.delete(day);
      else selected.add(day);
      sync();
      grid.querySelectorAll(".cal-day").forEach((el) => {
        const d = parseInt(el.dataset.day, 10);
        el.classList.toggle("cal-day--selected", selected.has(d));
      });
    }

    for (let i = 0; i < firstDow; i++) {
      const empty = document.createElement("div");
      empty.className = "cal-day cal-day--empty";
      grid.appendChild(empty);
    }
    for (let d = 1; d <= daysInMonth; d++) {
      const cell = document.createElement("button");
      cell.type = "button";
      cell.className = "cal-day" + (d === today ? " cal-day--today" : "");
      cell.dataset.day = String(d);
      cell.textContent = String(d);
      cell.addEventListener("click", () => toggle(d));
      grid.appendChild(cell);
    }

    document.getElementById("cal-select-all")?.addEventListener("click", () => {
      for (let d = 1; d <= daysInMonth; d++) selected.add(d);
      sync();
      grid.querySelectorAll(".cal-day:not(.cal-day--empty)").forEach((el) => {
        el.classList.add("cal-day--selected");
      });
    });
    document.getElementById("cal-clear")?.addEventListener("click", () => {
      selected.clear();
      sync();
      grid.querySelectorAll(".cal-day").forEach((el) => el.classList.remove("cal-day--selected"));
    });

    const sel = document.getElementById("content-type-cal");
    if (sel) {
      sel.remove();
    }
    const storyLinkWrap = document.getElementById("story-link-wrap-cal");
    if (storyLinkWrap) storyLinkWrap.remove();
  }

  function initCalendarTimes() {
    const list = document.getElementById("calendar-times-list");
    const addBtn = document.getElementById("cal-add-time");
    const contentType = document.getElementById("content-type");
    const videoInput = document.getElementById("video-input");
    const help = document.getElementById("calendar-times-help");
    const modeCalendar = document.getElementById("mode-calendar");
    if (!list || !addBtn) return;

    function styleRow(row) {
      row.style.display = "flex";
      row.style.gap = "8px";
      row.style.alignItems = "center";
      row.style.marginTop = "6px";
    }

    function isStory() {
      return contentType?.value === "story";
    }

    function syncRemoveButtons() {
      const rows = list.querySelectorAll(".calendar-time-row");
      rows.forEach((row) => {
        let btn = row.querySelector("[data-remove-time]");
        if (isStory()) {
          if (btn) btn.remove();
          return;
        }
        if (rows.length <= 1) {
          if (btn) btn.remove();
          return;
        }
        if (!btn) {
          btn = document.createElement("button");
          btn.type = "button";
          btn.className = "btn btn-sm";
          btn.dataset.removeTime = "1";
          btn.textContent = "Remover";
          btn.addEventListener("click", () => {
            row.remove();
            syncRemoveButtons();
          });
          row.appendChild(btn);
        }
      });
    }

    addBtn.addEventListener("click", () => {
      if (isStory()) return;
      const row = document.createElement("div");
      row.className = "calendar-time-row";
      styleRow(row);
      row.innerHTML = '<input type="time" name="calendar_times" value="14:00">';
      list.appendChild(row);
      syncRemoveButtons();
    });

    function syncStoryTimes() {
      if (!isStory()) {
        addBtn.hidden = false;
        if (help) help.textContent = "Vários horários no mesmo dia (ex.: 10:00, 15:00, 21:00).";
        list.querySelectorAll(".calendar-time-row").forEach(styleRow);
        syncRemoveButtons();
        return;
      }

      addBtn.hidden = true;
      const files = videoInput?.files ? Array.from(videoInput.files) : [];
      const previousTimes = Array.from(
        list.querySelectorAll('input[name="calendar_times"]')
      ).map((input) => input.value);
      list.innerHTML = "";

      if (!files.length) {
        const empty = document.createElement("p");
        empty.className = "muted";
        empty.style.margin = "6px 0 0";
        empty.textContent = "Selecione as mídias para definir um horário para cada Story.";
        list.appendChild(empty);
      } else {
        files.forEach((file, index) => {
          const row = document.createElement("div");
          row.className = "calendar-time-row calendar-time-row--story";
          styleRow(row);

          const media = document.createElement("span");
          media.className = "calendar-story-media";
          media.textContent = `Story ${index + 1}: ${file.name}`;

          const input = document.createElement("input");
          input.type = "time";
          input.name = "calendar_times";
          input.required = Boolean(modeCalendar?.checked);
          input.value = previousTimes[index] || (index === 0 ? "10:00" : "");

          row.append(media, input);
          list.appendChild(row);
        });
      }

      if (help) {
        help.textContent = files.length
          ? `${files.length} Story(s): horário diferente para cada uma · repete nos dias do mês selecionados.`
          : "Ex.: selecione o mês todo + Story 1 às 12:00 e Story 2 às 18:00.";
      }
    }

    videoInput?.addEventListener("change", syncStoryTimes);
    contentType?.addEventListener("change", syncStoryTimes);
    document.querySelectorAll('input[name="schedule_mode"]').forEach((radio) => {
      radio.addEventListener("change", () => {
        list.querySelectorAll('input[name="calendar_times"]').forEach((input) => {
          input.required = Boolean(isStory() && modeCalendar?.checked);
        });
      });
    });
    syncStoryTimes();
  }

  function initLucide() {
    if (typeof lucide !== "undefined") {
      lucide.createIcons();
    }
  }

  function formatCountShort(n) {
    const v = Number(n || 0);
    if (v >= 1000000) return (v / 1000000).toFixed(1).replace(/\.0$/, "") + "M";
    if (v >= 1000) return (v / 1000).toFixed(1).replace(/\.0$/, "") + "k";
    return String(v);
  }

  function rankRowClass(index) {
    if (index === 1) return " og-rank-row--gold";
    if (index === 2) return " og-rank-row--silver";
    if (index === 3) return " og-rank-row--bronze";
    return "";
  }

  function renderRankListHtml(players) {
    if (!players || !players.length) {
      return '<div class="og-rank-empty">Nenhum player no ranking ainda hoje.</div>';
    }
    let html = '<ol class="og-rank-list og-rank-list--card">';
    players.forEach((item, i) => {
      const index = i + 1;
      const initial = (item.display_name || "?")[0].toUpperCase();
      const avatar = item.avatar_url
        ? `<img src="${escapeHtml(item.avatar_url)}" alt="" class="og-rank-avatar-img">`
        : escapeHtml(initial);
      const gold = index === 1 ? " og-rank-avatar--gold" : "";
      html +=
        `<li class="og-rank-row${rankRowClass(index)}">` +
        `<div class="og-rank-avatar${item.avatar_url ? " og-rank-avatar--photo" : ""}${gold}">${avatar}</div>` +
        `<div class="og-rank-info"><strong>${escapeHtml(item.display_name)} <span class="og-rank-badge">#${index}</span></strong>` +
        `<span class="og-rank-tier">${escapeHtml(item.tier || "")}</span></div>` +
        `<div class="og-rank-score-stack"><span class="og-rank-score-pill">${item.posts_today}</span></div>` +
        "</li>";
    });
    html += "</ol>";
    return html;
  }

  function renderRankModalHtml(players, myRank) {
    if (!players || !players.length) {
      return '<div class="og-rank-empty">Sem publicações hoje.</div>' + renderRankYouHtml(myRank);
    }
    const p1 = players[0];
    const p2 = players[1] || null;
    const p3 = players[2] || null;
    const slot = (player, pos, extra) => {
      if (!player && pos !== 1) {
        return `<div class="rank-podium-slot rank-podium-slot--${pos} is-empty"><div class="rank-podium-block"><span>${pos}</span></div></div>`;
      }
      const av = player.avatar_url
        ? `<img src="${escapeHtml(player.avatar_url)}" alt="">`
        : escapeHtml((player.display_name || "?")[0].toUpperCase());
      const crown = pos === 1 ? '<span class="rank-podium-crown" aria-hidden="true"><i data-lucide="crown"></i></span>' : "";
      const gold = pos === 1 ? " rank-podium-avatar--gold" : "";
      return (
        `<div class="rank-podium-slot rank-podium-slot--${pos}${extra || ""}">` +
        crown +
        `<div class="rank-podium-avatar${gold}${player.avatar_url ? " has-photo" : ""}">${av}</div>` +
        `<strong>${escapeHtml(player.display_name)}</strong>` +
        `<span class="rank-podium-tier">${escapeHtml(player.tier || "")}</span>` +
        `<span class="rank-podium-score">${player.posts_today} posts</span>` +
        `<div class="rank-podium-block"><span>${pos}</span></div></div>`
      );
    };
    let html = '<div class="rank-podium">' + slot(p2, 2, "") + slot(p1, 1, "") + slot(p3, 3, "") + "</div>";
    if (players.length > 3) {
      html += '<ol class="rank-modal-list">';
      players.slice(3).forEach((item, i) => {
        const pos = i + 4;
        const initial = (item.display_name || "?")[0].toUpperCase();
        const av = item.avatar_url
          ? `<img src="${escapeHtml(item.avatar_url)}" alt="" class="og-rank-avatar-img">`
          : escapeHtml(initial);
        html +=
          "<li><span class=\"rank-modal-pos\">" + pos + "</span>" +
          `<div class="og-rank-avatar${item.avatar_url ? " og-rank-avatar--photo" : ""}">${av}</div>` +
          `<div class="og-rank-info"><strong>${escapeHtml(item.display_name)}</strong><span class="og-rank-tier">${escapeHtml(item.tier || "")}</span></div>` +
          `<div class="og-rank-score-stack"><span class="og-rank-score-pill">${item.posts_today}</span></div></li>`;
      });
      html += "</ol>";
    }
    html += renderRankYouHtml(myRank);
    return html;
  }

  function renderRankYouHtml(myRank) {
    let body = "<strong>Sem publicações hoje</strong>";
    if (myRank && myRank.rank) {
      body = `<strong>#${myRank.rank} · ${myRank.post_count} posts</strong>`;
    }
    return `<div class="rank-modal-you"><span>SUA POSIÇÃO HOJE</span>${body}</div>`;
  }

  async function loadDashboardRank() {
    const listMount = document.getElementById("rank-list-mount");
    const modalMount = document.getElementById("rank-modal-mount");
    if (!listMount) return;
    try {
      const res = await fetch("/api/dashboard/rank");
      if (!res.ok) throw new Error("fail");
      const data = await res.json();
      listMount.innerHTML = renderRankListHtml(data.top_players || []);
      if (modalMount) {
        modalMount.innerHTML = renderRankModalHtml(data.top_players || [], data.my_rank);
      }
      try { if (window.lucide) lucide.createIcons(); } catch (_) {}
    } catch (_) {
      listMount.innerHTML = '<div class="og-rank-empty">Ranking indisponível agora.</div>';
      if (modalMount) {
        modalMount.innerHTML = '<div class="og-rank-empty">Ranking indisponível agora.</div>';
      }
    }
  }

  function initOgDashboard() {
    const tooltip = document.getElementById("og-chart-tooltip");
    const chartWrap = document.getElementById("og-line-chart");
    const chartDots = Array.from(document.querySelectorAll(".og-chart-dot"));

    function showChartTip(dot) {
      if (!tooltip || !chartWrap || !dot) return;
      tooltip.textContent = dot.dataset.tip || "";
      tooltip.style.opacity = "1";
      const wr = chartWrap.getBoundingClientRect();
      const dr = dot.getBoundingClientRect();
      tooltip.style.left = (dr.left - wr.left + dr.width / 2) + "px";
      tooltip.style.top = (dr.top - wr.top - 36) + "px";
    }

    function hideChartTip() {
      if (tooltip) tooltip.style.opacity = "0";
    }

    if (chartWrap && chartDots.length) {
      chartWrap.addEventListener("mousemove", (e) => {
        const wr = chartWrap.getBoundingClientRect();
        const x = e.clientX;
        let best = null;
        let bestDist = Infinity;
        chartDots.forEach((dot) => {
          const dr = dot.getBoundingClientRect();
          const cx = dr.left + dr.width / 2;
          const dist = Math.abs(cx - x);
          if (dist < bestDist) {
            bestDist = dist;
            best = dot;
          }
        });
        // Só mostra se o mouse está dentro da área horizontal do gráfico
        if (best && x >= wr.left && x <= wr.right) showChartTip(best);
      });
      chartWrap.addEventListener("mouseleave", hideChartTip);
    }

    document.querySelectorAll(".og-bar-fill").forEach((bar, i) => {
      bar.style.animationDelay = (i * 0.06) + "s";
    });

    document.querySelectorAll(".og-rank-tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        const target = tab.dataset.rankTab;
        if (!target) return;
        document.querySelectorAll(".og-rank-tab").forEach((t) => {
          const active = t.dataset.rankTab === target;
          t.classList.toggle("active", active);
          t.setAttribute("aria-selected", active ? "true" : "false");
        });
        document.querySelectorAll(".og-rank-panel").forEach((panel) => {
          const show = panel.id === `rank-panel-${target}`;
          panel.hidden = !show;
          panel.classList.toggle("active", show);
        });
      });
    });

    const rankModal = document.getElementById("rankModal");
    const rankEye = document.getElementById("rankEyeBtn");
    const rankClose = document.getElementById("rankModalClose");
    const openRankModal = () => {
      if (!rankModal) return;
      if (typeof rankModal.showModal === "function") rankModal.showModal();
      else rankModal.setAttribute("open", "");
      try { if (window.lucide) lucide.createIcons(); } catch (_) {}
    };
    const closeRankModal = () => {
      if (!rankModal) return;
      if (typeof rankModal.close === "function") rankModal.close();
      else rankModal.removeAttribute("open");
    };
    rankEye?.addEventListener("click", openRankModal);
    rankClose?.addEventListener("click", closeRankModal);
    rankModal?.addEventListener("click", (e) => {
      if (e.target === rankModal) closeRankModal();
    });
    loadDashboardRank();
  }

  const directUploadConcurrency = 6;

  async function uploadDirectToR2(automationId, files, onProgress, serverFallback) {
    const presignResponse = await fetch(`/automations/${automationId}/direct-upload-urls`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "fetch",
      },
      body: JSON.stringify({
        files: files.map((file) => ({
          name: file.name,
          size: file.size,
          type: file.type,
        })),
      }),
    });
    const presign = await presignResponse.json().catch(() => ({}));
    if (presignResponse.status === 409 && presign.fallback && serverFallback) {
      return serverFallback();
    }
    if (!presignResponse.ok || presign.error || !Array.isArray(presign.uploads)) {
      throw new Error(presign.error || "Não foi possível preparar o upload direto ao R2.");
    }

    let nextIndex = 0;
    let done = 0;
    async function putWithRetry(upload, file) {
      let lastError;
      for (let attempt = 1; attempt <= 3; attempt += 1) {
        try {
          const response = await fetch(upload.url, {
            method: "PUT",
            headers: { "Content-Type": upload.content_type },
            body: file,
          });
          if (response.ok) return;
          lastError = new Error(`R2 respondeu HTTP ${response.status}`);
        } catch (err) {
          lastError = err;
        }
        if (attempt < 3) {
          await new Promise((resolve) => setTimeout(resolve, attempt * 700));
        }
      }
      throw lastError || new Error(`Falha ao enviar ${file.name} ao R2.`);
    }

    const workers = Array.from(
      { length: Math.min(directUploadConcurrency, files.length) },
      async () => {
        while (nextIndex < files.length) {
          const index = nextIndex;
          nextIndex += 1;
          await putWithRetry(presign.uploads[index], files[index]);
          done += 1;
          if (onProgress) onProgress(done, files.length);
        }
      }
    );
    await Promise.all(workers);

    const registerResponse = await fetch(`/automations/${automationId}/register-direct-uploads`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "fetch",
      },
      body: JSON.stringify({
        uploads: presign.uploads.map((upload) => ({
          key: upload.key,
          name: upload.name,
        })),
      }),
    });
    const registered = await registerResponse.json().catch(() => ({}));
    if (!registerResponse.ok || registered.error) {
      throw new Error(registered.error || "Vídeos chegaram ao R2, mas não foi possível registrar a playlist.");
    }
    return registered.total || files.length;
  }

  function initAutomationForm() {
    const form = document.getElementById("automation-form");
    if (!form) return;

    const contentType = document.getElementById("content-type");
    const videoInput = document.getElementById("video-input");
    const videoName = document.getElementById("video-file-name");
    const videoList = document.getElementById("video-file-list");
    const submitBtn = document.getElementById("submit-btn");

    function refreshAccountPickCount() {
      const boxes = form.querySelectorAll('input[name="account_ids"]');
      const checked = form.querySelectorAll('input[name="account_ids"]:checked');
      const el = document.getElementById("accounts-pick-count");
      if (!el || !boxes.length) return;
      el.textContent = `${checked.length} de ${boxes.length} selecionada(s)`;
    }

    function setAccountChecks(predicate) {
      form.querySelectorAll('input[name="account_ids"]').forEach((box) => {
        box.checked = !!predicate(box);
      });
      refreshAccountPickCount();
      form.dispatchEvent(new Event("change", { bubbles: true }));
    }

    document.getElementById("accounts-select-all")?.addEventListener("click", () => {
      setAccountChecks(() => true);
    });
    document.getElementById("accounts-select-active")?.addEventListener("click", () => {
      setAccountChecks((box) => (box.getAttribute("data-status") || "active") === "active");
    });
    document.getElementById("accounts-clear")?.addEventListener("click", () => {
      setAccountChecks(() => false);
    });
    form.addEventListener("change", (e) => {
      if (e.target && e.target.name === "account_ids") refreshAccountPickCount();
    });
    refreshAccountPickCount();

    const videoExt = /\.(mp4|mov|webm|m4v|mkv)$/i;
    const imageExt = /\.(jpe?g|png|webp)$/i;
    const reelVideosRemaining = Math.max(
      0,
      parseInt(form.dataset.reelVideosRemaining || "150", 10) || 150
    );
    const reelVideosLimit = Math.max(
      1,
      parseInt(form.dataset.reelVideosLimit || "150", 10) || 150
    );
    const maxReelFiles = Math.min(150, reelVideosRemaining || 0);
    // Fallback local: no R2, o navegador envia direto sem passar pela Railway.
    const reelUploadConcurrency = 4;

    function filesTotalMb(files) {
      return Math.round(files.reduce((s, f) => s + f.size, 0) / 1024 / 1024 * 10) / 10;
    }

    function setSubmitState(disabled, text) {
      if (!submitBtn) return;
      submitBtn.disabled = disabled;
      submitBtn.textContent = text;
    }

    async function postForm(url, data) {
      const res = await fetch(url, {
        method: "POST",
        body: data,
        headers: { "X-Requested-With": "fetch" },
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || payload.error) {
        throw new Error(payload.error || "Falha no envio. Tente novamente.");
      }
      return payload;
    }

    async function uploadFilesInParallel(automationId, files, onProgress) {
      let nextIndex = 0;
      let done = 0;
      let total = 0;
      const workers = Array.from(
        { length: Math.min(reelUploadConcurrency, files.length) },
        async () => {
          while (nextIndex < files.length) {
            const i = nextIndex;
            nextIndex += 1;
            const data = new FormData();
            data.append("videos", files[i]);
            const result = await postForm(`/automations/${automationId}/upload-batch`, data);
            done += 1;
            total = result.total || total + 1;
            if (onProgress) onProgress(done, files.length, total);
          }
        }
      );
      await Promise.all(workers);
      return total;
    }

    function draftFormData() {
      const data = new FormData();
      const nameField = form.querySelector('[name="name"]');
      data.append("name", (nameField?.value || "").trim() || "Reels");
      ["content_type", "caption", "story_link", "story_sticker_text", "interval_minutes", "jitter_minutes", "posts_per_batch", "rest_minutes", "stagger_min_minutes", "stagger_max_minutes"].forEach((name) => {
        const field = form.querySelector(`[name="${name}"]`);
        if (field) data.append(name, field.value || "");
      });
      const mode = form.querySelector('[name="schedule_mode"]:checked');
      data.append("schedule_mode", mode ? mode.value : "recurring");
      if (mode && mode.value === "calendar") {
        const calDays = form.querySelector('[name="calendar_days"]');
        data.append("calendar_days", calDays ? calDays.value || "[]" : "[]");
        form.querySelectorAll('[name="calendar_times"]').forEach((field) => {
          if (field.value) data.append("calendar_times", field.value);
        });
      }
      const jitter = form.querySelector('[name="jitter_enabled"]');
      if (jitter && jitter.checked) data.append("jitter_enabled", "1");
      const stagger = form.querySelector('[name="stagger_enabled"]');
      if (stagger && stagger.checked) data.append("stagger_enabled", "1");
      form.querySelectorAll('[name="account_ids"]:checked').forEach((field) => {
        data.append("account_ids", field.value);
      });
      const thumb = form.querySelector('[name="thumb"]');
      if (thumb?.files?.[0]) data.append("thumb", thumb.files[0]);
      const camuEnabled = form.querySelector('#camouflage-enabled');
      if (camuEnabled && camuEnabled.checked) {
        data.append("camouflage_enabled", "1");
        const camuCover = form.querySelector('[name="camouflage_cover"]');
        if (camuCover?.files?.[0]) data.append("camouflage_cover", camuCover.files[0]);
        const camuOpacity = form.querySelector('[name="camouflage_opacity_pct"]');
      if (camuOpacity) data.append("camouflage_opacity_pct", camuOpacity.value || "25");
      }
      return data;
    }

    async function submitReelsInBatches(files) {
      setSubmitState(true, "Criando rascunho…");
      const draft = await postForm("/automations/new/reel-draft", draftFormData());
      const automationId = draft.automation_id;
      await uploadDirectToR2(automationId, files, (done, totalFiles) => {
        setSubmitState(true, `Enviando vídeos: ${done}/${totalFiles}…`);
        if (videoName) {
          videoName.textContent = `Enviando vídeos: ${done}/${totalFiles}`;
          videoName.style.color = "var(--green, #22c55e)";
        }
      }, () => uploadFilesInParallel(automationId, files, (done, totalFiles) => {
        setSubmitState(true, `Enviando ${done}/${totalFiles} pelo servidor…`);
      }));
      setSubmitState(true, "Finalizando rascunho…");
      const finished = await postForm(`/automations/${automationId}/upload-finish`, new FormData());
      window.location.href = finished.redirect || "/automations?ok=draft";
    }

    function updateVideoLabel() {
      const files = videoInput?.files ? Array.from(videoInput.files) : [];
      const isReel = contentType?.value === "reel";
      const countInput = document.getElementById("video-count-input");
      if (countInput) countInput.value = String(files.length);
      if (!videoName) return;
      if (!files.length) {
        videoName.textContent = isReel
          ? "Nenhum vídeo selecionado — escolha um ou mais .mp4"
          : "Nenhum arquivo selecionado";
        videoName.style.color = "var(--red, #ef4444)";
        if (videoList) videoList.style.display = "none";
        return;
      }
      if (isReel) {
        const bad = files.filter((f) => !videoExt.test(f.name));
        if (bad.length) {
          videoName.textContent = "Arquivo inválido: " + bad.map((f) => f.name).join(", ") + " — use .mp4";
          videoName.style.color = "var(--red, #ef4444)";
        } else if (maxReelFiles <= 0) {
          videoName.textContent = "Limite de " + reelVideosLimit + " vídeos Reels atingido nesta conta. Apague vídeos antigos para liberar espaço.";
          videoName.style.color = "var(--red, #ef4444)";
        } else if (files.length > maxReelFiles) {
          videoName.textContent = "Limite: no máximo " + maxReelFiles + " vídeo(s) agora (" + reelVideosLimit + " no total por conta).";
          videoName.style.color = "var(--red, #ef4444)";
        } else {
          const mb = filesTotalMb(files);
          videoName.textContent = files.length + " vídeo(s) — " + mb + " MB total";
          videoName.style.color = "var(--green, #22c55e)";
        }
        if (videoList) {
          videoList.innerHTML = files.map((f) => "<li>" + escapeHtml(f.name) + "</li>").join("");
          videoList.style.display = files.length > 1 ? "block" : "none";
        }
      } else {
        videoName.textContent = files[0].name;
        videoName.style.color = "var(--green, #22c55e)";
        if (videoList && contentType?.value === "story") {
          videoName.textContent = files.length === 1
            ? files[0].name
            : files.length + " Stories selecionados — um por horário";
          videoList.innerHTML = files.map((f) => "<li>" + escapeHtml(f.name) + "</li>").join("");
          videoList.style.display = files.length > 1 ? "block" : "none";
        } else if (videoList) {
          videoList.style.display = "none";
        }
      }
    }

    videoInput?.addEventListener("change", updateVideoLabel);
    document.addEventListener("automation-media-changed", updateVideoLabel);
    updateVideoLabel();

    form.addEventListener("submit", async (e) => {
      const files = videoInput?.files ? Array.from(videoInput.files) : [];
      const isReel = contentType?.value === "reel";
      const isPhoto = contentType?.value === "photo";
      const captionField = form.querySelector('[name="caption"]');
      const captionText = (captionField?.value || "").trim();
      if ((isReel || isPhoto) && !captionText) {
        e.preventDefault();
        alert("Legenda obrigatória. Cole o texto do Reel/Foto antes de criar.");
        captionField?.focus();
        return;
      }
      if (!files.length) {
        e.preventDefault();
        alert(isReel
          ? "Selecione pelo menos um vídeo .mp4. A capa (.png) sozinha não publica."
          : "Selecione o arquivo de mídia.");
        videoInput?.focus();
        return;
      }
      if (isReel) {
        if (maxReelFiles <= 0) {
          e.preventDefault();
          alert("Limite de " + reelVideosLimit + " vídeos Reels por conta atingido. Apague vídeos ou automações antigas para liberar espaço.");
          return;
        }
        if (files.length > maxReelFiles) {
          e.preventDefault();
          alert("Você só pode enviar mais " + maxReelFiles + " vídeo(s) (limite " + reelVideosLimit + " por conta). Selecione menos arquivos.");
          return;
        }
        const bad = files.filter((f) => !videoExt.test(f.name));
        if (bad.length) {
          e.preventDefault();
          alert("Estes arquivos não são vídeo: " + bad.map((f) => f.name).join(", "));
          return;
        }
        const camuOn = form.querySelector("#camouflage-enabled");
        const camuFile = form.querySelector("#camouflage-cover-input");
        if (camuOn?.checked && !camuFile?.files?.[0]) {
          e.preventDefault();
          alert("Marcou aplicar camuflagem — envie a imagem que vai por cima de todos os Reels.");
          camuFile?.focus();
          return;
        }
        if (files.length > 0) {
          e.preventDefault();
          try {
            await submitReelsInBatches(files);
          } catch (err) {
            alert(err?.message || "Falha ao enviar os vídeos em blocos.");
            setSubmitState(false, "Criar automação");
          }
          return;
        }
      } else if (contentType?.value === "photo") {
        if (!imageExt.test(files[0].name)) {
          e.preventDefault();
          alert("Para foto no feed, use .jpg ou .png.");
          return;
        }
      }

      // Story/Foto: NÃO usar submit nativo. O CSRF middleware não pode
      // ler multipart via request.form() (esvazia o body). Fetch manda
      // X-CSRF-Token e a mídia chega intacta no endpoint.
      if (!isReel) {
        e.preventDefault();
        setSubmitState(true, "Criando automação…");
        try {
          const res = await fetch(form.getAttribute("action") || form.action || "/automations/new", {
            method: "POST",
            body: new FormData(form),
            credentials: "same-origin",
            redirect: "follow",
            headers: { "X-Requested-With": "fetch" },
          });
          if (res.redirected || res.ok) {
            window.location.href = res.url;
            return;
          }
          const html = await res.text();
          if (html && html.indexOf("<html") !== -1) {
            document.open();
            document.write(html);
            document.close();
            return;
          }
          throw new Error("Não deu para criar a automação. Tente de novo.");
        } catch (err) {
          alert(err?.message || "Falha ao enviar a mídia.");
          setSubmitState(
            false,
            contentType?.value === "story" ? "Agendar Story" : "Criar automação"
          );
        }
        return;
      }

      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = "Criando automação…";
      }
    });
  }

  function initAutomationPlaylistUploads() {
    const forms = document.querySelectorAll("[data-playlist-upload-form]");
    if (!forms.length) return;

    const videoExt = /\.(mp4|mov|webm|m4v|mkv)$/i;

    async function postForm(url, data) {
      const res = await fetch(url, {
        method: "POST",
        body: data,
        headers: { "X-Requested-With": "fetch" },
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || payload.error) {
        throw new Error(payload.error || "Falha no envio. Tente novamente.");
      }
      return payload;
    }

    forms.forEach((form) => {
      const input = form.querySelector("[data-playlist-upload-input]");
      const button = form.querySelector("[data-playlist-upload-button]");
      const statusEl = form.querySelector("[data-playlist-upload-status]");
      const automationId = form.dataset.automationId;

      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const files = input?.files ? Array.from(input.files) : [];
        if (!files.length) {
          alert("Selecione um ou mais vídeos para adicionar.");
          return;
        }
        const remaining = Math.max(
          0,
          parseInt(form.dataset.reelVideosRemaining || "150", 10) || 150
        );
        const limit = Math.max(
          1,
          parseInt(form.dataset.reelVideosLimit || "150", 10) || 150
        );
        if (remaining <= 0) {
          alert("Limite de " + limit + " vídeos Reels por conta atingido. Apague vídeos antigos para liberar espaço.");
          return;
        }
        if (files.length > remaining) {
          alert("Só cabem mais " + remaining + " vídeo(s) no limite de " + limit + " por conta. Selecione menos arquivos.");
          return;
        }
        const bad = files.filter((f) => !videoExt.test(f.name));
        if (bad.length) {
          alert("Estes arquivos não são vídeo: " + bad.map((f) => f.name).join(", "));
          return;
        }
        if (!automationId) {
          alert("Automação inválida para upload.");
          return;
        }

        const originalText = button?.textContent || "Adicionar vídeos";
        if (button) {
          button.disabled = true;
          button.textContent = "Enviando…";
        }
        try {
          const serverFallback = async () => {
            let nextIndex = 0;
            let done = 0;
            let fallbackTotal = 0;
            const workers = Array.from(
              { length: Math.min(4, files.length) },
              async () => {
                while (nextIndex < files.length) {
                  const i = nextIndex;
                  nextIndex += 1;
                  const data = new FormData();
                  data.append("videos", files[i]);
                  const result = await postForm(`/automations/${automationId}/upload-batch`, data);
                  done += 1;
                  fallbackTotal = result.total || fallbackTotal + 1;
                  if (statusEl) statusEl.textContent = `Enviando ${done}/${files.length} pelo servidor…`;
                  if (button) button.textContent = `Enviando ${done}/${files.length}…`;
                }
              }
            );
            await Promise.all(workers);
            return fallbackTotal;
          };
          const total = await uploadDirectToR2(
            automationId,
            files,
            (done, count) => {
              if (statusEl) statusEl.textContent = `Direto ao R2: ${done}/${count}…`;
              if (button) button.textContent = `Enviando ${done}/${count}…`;
            },
            serverFallback
          );
          if (statusEl) statusEl.textContent = `${files.length} vídeo(s) adicionados. Total na playlist: ${total}.`;
          window.location.href = `/automations?ok=videos_added&n=${total}`;
        } catch (err) {
          alert(err?.message || "Falha ao adicionar vídeos.");
          if (statusEl) statusEl.textContent = "Falha no envio. Tente novamente.";
          if (button) {
            button.disabled = false;
            button.textContent = originalText;
          }
        }
      });
    });
  }

  function initPrivacyBlur() {
    const KEY = "instablack_privacy_blur_handles";
    const btn = document.getElementById("privacy-blur-btn");

    function apply(on) {
      document.body.classList.toggle("privacy-blur-handles", on);
      if (btn) {
        btn.classList.toggle("is-active", on);
        btn.setAttribute("aria-pressed", on ? "true" : "false");
        btn.title = on
          ? "Mostrar @ das contas"
          : "Desfocar @ das contas (para prints)";
        const icon = btn.querySelector("[data-lucide]");
        if (icon) {
          icon.setAttribute("data-lucide", on ? "eye" : "eye-off");
          try {
            if (window.lucide) lucide.createIcons({ nodes: [btn] });
          } catch (_) {}
        }
      }
    }

    const saved = localStorage.getItem(KEY) === "1";
    apply(saved);

    if (!btn || btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", () => {
      const next = !document.body.classList.contains("privacy-blur-handles");
      localStorage.setItem(KEY, next ? "1" : "0");
      apply(next);
    });
  }

  function initAutomationCamouflagePreview() {
    const wrap = document.getElementById("camouflage-wrap");
    if (!wrap) return;
    const enabled = document.getElementById("camouflage-enabled");
    const options = document.getElementById("camouflage-options");
    const videoInput = document.getElementById("video-input");
    const coverInput = document.getElementById("camouflage-cover-input");
    const coverLabel = document.getElementById("camouflage-cover-label");
    const opacityInput = document.getElementById("camouflage-opacity");
    const opacityVal = document.getElementById("camouflage-opacity-val");
    const screen = document.getElementById("camouflage-preview");
    const empty = document.getElementById("camouflage-preview-empty");
    const meta = document.getElementById("camouflage-preview-meta");
    if (!screen || !opacityInput) return;

    let videoEl = null;
    let coverImg = null;
    let canvas = null;
    let raf = 0;
    let videoUrl = "";
    let coverUrl = "";

    function isEnabled() {
      return !enabled || enabled.checked;
    }

    function syncOptions() {
      if (options) options.style.display = isEnabled() ? "" : "none";
      if (!isEnabled()) clearPreview();
      else rebuild();
    }

    function opacityAlpha() {
      return Math.max(0.05, Math.min(0.4, (Number(opacityInput.value) || 25) / 100));
    }

    function syncOpacityLabel() {
      if (opacityVal) opacityVal.textContent = `${Number(opacityInput.value) || 25}%`;
    }

    function clearPreview() {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      if (videoEl) {
        videoEl.pause();
        videoEl.removeAttribute("src");
        videoEl.load();
        videoEl = null;
      }
      if (videoUrl) URL.revokeObjectURL(videoUrl);
      if (coverUrl) URL.revokeObjectURL(coverUrl);
      videoUrl = "";
      coverUrl = "";
      coverImg = null;
      canvas = null;
      screen.innerHTML = "";
      if (empty) {
        empty.style.display = "";
        screen.appendChild(empty);
      }
      if (meta) meta.textContent = "";
    }

    function drawFrame() {
      if (!canvas || !videoEl || videoEl.readyState < 2) return;
      const ctx = canvas.getContext("2d");
      const cw = canvas.width;
      const ch = canvas.height;
      const vw = videoEl.videoWidth || 1080;
      const vh = videoEl.videoHeight || 1920;
      const scale = Math.max(cw / vw, ch / vh);
      const dw = vw * scale;
      const dh = vh * scale;
      const dx = (cw - dw) / 2;
      const dy = (ch - dh) / 2;
      ctx.clearRect(0, 0, cw, ch);
      ctx.drawImage(videoEl, dx, dy, dw, dh);
      if (coverImg && coverImg.complete) {
        ctx.globalAlpha = opacityAlpha();
        const cs = Math.max(cw / coverImg.naturalWidth, ch / coverImg.naturalHeight);
        const cdw = coverImg.naturalWidth * cs;
        const cdh = coverImg.naturalHeight * cs;
        ctx.drawImage(coverImg, (cw - cdw) / 2, (ch - cdh) / 2, cdw, cdh);
        ctx.globalAlpha = 1;
      }
    }

    function loop() {
      drawFrame();
      raf = requestAnimationFrame(loop);
    }

    function rebuild() {
      clearPreview();
      if (!isEnabled()) return;
      const videoFile = videoInput?.files?.[0];
      const coverFile = coverInput?.files?.[0];
      syncOpacityLabel();
      if (coverFile && coverLabel) {
        coverLabel.textContent = coverFile.name;
      }
      if (!videoFile || !coverFile) {
        if (meta) {
          meta.textContent = videoFile && !coverFile
            ? "Selecione a imagem de camuflagem para ver o overlay."
            : (!videoFile && coverFile ? "Selecione um vídeo para o preview." : "");
        }
        return;
      }
      if (empty) empty.style.display = "none";
      canvas = document.createElement("canvas");
      canvas.width = 540;
      canvas.height = 960;
      canvas.style.width = "100%";
      canvas.style.height = "100%";
      canvas.style.objectFit = "cover";
      screen.appendChild(canvas);

      videoUrl = URL.createObjectURL(videoFile);
      coverUrl = URL.createObjectURL(coverFile);
      coverImg = new Image();
      coverImg.onload = () => drawFrame();
      coverImg.src = coverUrl;

      videoEl = document.createElement("video");
      videoEl.muted = true;
      videoEl.playsInline = true;
      videoEl.loop = true;
      videoEl.preload = "auto";
      videoEl.src = videoUrl;
      videoEl.addEventListener("loadeddata", () => {
        videoEl.play().catch(() => {});
        loop();
        if (meta) {
          meta.textContent = `Preview do 1º vídeo · opacidade ${Number(opacityInput.value) || 15}% · aplica em todos`;
        }
      });
    }

    enabled?.addEventListener("change", syncOptions);
    opacityInput.addEventListener("input", () => {
      syncOpacityLabel();
      drawFrame();
      if (meta && isEnabled() && coverInput?.files?.[0] && videoInput?.files?.[0]) {
        meta.textContent = `Preview do 1º vídeo · opacidade ${Number(opacityInput.value) || 15}% · aplica em todos`;
      }
    });
    coverInput?.addEventListener("change", () => {
      if (coverInput.files?.[0] && enabled && !enabled.checked) {
        enabled.checked = true;
        syncOptions();
      } else {
        rebuild();
      }
    });
    videoInput?.addEventListener("change", rebuild);
    document.addEventListener("automation-media-changed", rebuild);
    syncOpacityLabel();
    syncOptions();
  }

  function initStoryMetaLinkHint() {
    const wrap = document.getElementById("story-link-wrap");
    const fields = document.getElementById("story-link-fields");
    const metaOnly = document.getElementById("story-link-meta-only");
    const linkInput = document.getElementById("story-link-input");
    const stickerInput = document.getElementById("story-sticker-input");
    const form = document.getElementById("automation-form");
    if (!wrap || !form) return;

    function sync() {
      if (wrap.style.display === "none") {
        if (fields) fields.style.display = "none";
        if (metaOnly) metaOnly.style.display = "none";
        return;
      }
      const checked = Array.from(form.querySelectorAll('[name="account_ids"]:checked'));
      const onlyMeta =
        checked.length > 0 &&
        checked.every((el) => (el.getAttribute("data-provider") || "instagrapi") === "meta");

      if (onlyMeta) {
        if (fields) fields.style.display = "none";
        if (metaOnly) metaOnly.style.display = "block";
        if (linkInput) {
          linkInput.value = "";
          linkInput.disabled = true;
        }
        if (stickerInput) {
          stickerInput.value = "";
          stickerInput.disabled = true;
        }
      } else {
        if (fields) fields.style.display = "";
        if (metaOnly) metaOnly.style.display = "none";
        if (linkInput) linkInput.disabled = false;
        if (stickerInput) stickerInput.disabled = false;
      }
    }

    form.addEventListener("change", (e) => {
      if (e.target && e.target.name === "account_ids") sync();
    });
    document.addEventListener("automation-media-changed", sync);
    sync();
  }

  function showFlashOkFromStorage() {
    try {
      const msg = sessionStorage.getItem("ib_flash_ok");
      if (!msg) return;
      sessionStorage.removeItem("ib_flash_ok");
      const host =
        document.getElementById("app-content") ||
        document.querySelector(".container") ||
        document.body;
      const el = document.createElement("div");
      el.className = "alert alert-ok";
      el.setAttribute("role", "status");
      el.textContent = msg;
      host.insertBefore(el, host.firstChild);
      el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (_) {
      /* ignore */
    }
  }

  function initPage() {
    showFlashOkFromStorage();
    initLucide();
    initPrivacyBlur();
    initMetaAppsPage();
    initCharts();
    initPeriodPills();
    initContentTypeForm();
    initEditAutomationCaption();
    initThumbPreview();
    initScheduleMode();
    initMetaIntervalFilter();
    initAutomationForm();
    initAutomationCamouflagePreview();
    initStoryMetaLinkHint();
    initAutomationPlaylistUploads();
    initOgDashboard();
    initCalendarPicker();
    initCalendarTimes();
    initAccountsConnect();
    initAccountsReconnect();
    initVaultPage();
    initAuthMethodForm();
    initProfileEditForm();
    initProxyInput();
    initAccountProxyUpdate();
    initWebPush();
    initProfileNotifications();
    initNotifCard();
    initDashActivityPoll();
    initLogsClearForm();
    initLogsWatchPoll();
  }

  initPage();
  initLucide();

  const canvas = document.getElementById("login-rays");
  if (canvas) {
    const ctx = canvas.getContext("2d");
    let w, h, t = 0;
    function resize() { w = canvas.width = window.innerWidth; h = canvas.height = window.innerHeight; }
    resize();
    window.addEventListener("resize", resize);
    (function draw() {
      ctx.clearRect(0, 0, w, h);
      const cx = w * 0.5, cy = h * 0.3;
      for (let i = 0; i < 8; i++) {
        const angle = (i / 8) * Math.PI * 2 + t * 0.0003;
        const len = Math.max(w, h) * 1.2;
        const grad = ctx.createLinearGradient(cx, cy, cx + Math.cos(angle) * len, cy + Math.sin(angle) * len);
        grad.addColorStop(0, "rgba(212,175,55,0.14)");
        grad.addColorStop(0.5, "rgba(212,175,55,0.04)");
        grad.addColorStop(1, "transparent");
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.cos(angle - 0.08) * len, cy + Math.sin(angle - 0.08) * len);
        ctx.lineTo(cx + Math.cos(angle + 0.08) * len, cy + Math.sin(angle + 0.08) * len);
        ctx.closePath();
        ctx.fillStyle = grad;
        ctx.fill();
      }
      t++;
      requestAnimationFrame(draw);
    })();
  }
})();
