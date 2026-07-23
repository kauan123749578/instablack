(function () {
  "use strict";

  const appContent = document.getElementById("app-content");
  const drawer = document.getElementById("mobile-drawer");
  const drawerOpen = document.getElementById("drawer-open");
  const drawerBackdrop = document.getElementById("drawer-backdrop");
  const sidebar = document.getElementById("sidebar");
  let notifPollTimer = null;
  let dashActivityPollTimer = null;

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

  async function navigateTo(url, push = true) {
    if (url.startsWith("/automations/new") || url.startsWith("/automations/story-studio")) {
      window.location.href = url;
      return;
    }
    if (!appContent) { window.location.href = url; return; }
    appContent.classList.add("content-loading");
    if (dashActivityPollTimer) {
      clearInterval(dashActivityPollTimer);
      dashActivityPollTimer = null;
    }
    try {
      const resp = await fetch(url, { headers: { "X-Partial": "1" } });
      if (!resp.ok) throw new Error(resp.status);
      const html = await resp.text();
      const doc = new DOMParser().parseFromString(html, "text/html");
      const newContent = doc.getElementById("app-content");
      if (newContent) {
        appContent.innerHTML = newContent.innerHTML;
        if (push) history.pushState({ url }, "", url);
        setActiveNav(url);
        initPage();
        closeDrawer();
        sidebar?.classList.remove("mobile-open");
      } else {
        window.location.href = url;
      }
    } catch {
      window.location.href = url;
    } finally {
      appContent.classList.remove("content-loading");
    }
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
          icon: "/static/favicon.svg",
          badge: "/static/favicon.svg",
          tag: "instablack-local",
          data: { url: url || "/perfil" },
        });
        return;
      }
    } catch (_) {}
    if ("Notification" in window && Notification.permission === "granted") {
      new Notification(title, { body, icon: "/static/favicon.svg" });
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
      }
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
        const res = await fetch("/api/logs/latest?since_id=" + latest);
        if (!res.ok) return;
        const data = await res.json();
        if (!data.items || !data.items.length) return;
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
        loadNotifications();
      } catch (_) {}
    }

    poll();
    dashActivityPollTimer = setInterval(poll, 7000);
  }

  function initLogsClearForm() {
    const form = document.getElementById("logs-clear-form");
    if (!form || form.dataset.bound === "1") return;
    form.dataset.bound = "1";
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!confirm("Apagar TODO o histórico de logs? Esta ação não pode ser desfeita.")) {
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
    if (btn.dataset.bound === "1") {
      loadNotifications();
      return;
    }
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

    loadNotifications();
    if (!notifPollTimer) {
      notifPollTimer = setInterval(() => {
        if (!document.hidden) loadNotifications();
      }, 15000);
    }
  }

  function initContentTypeForm() {
    const sel = document.getElementById("content-type");
    const mediaLabel = document.getElementById("media-label");
    const captionWrap = document.getElementById("caption-wrap");
    const thumbWrap = document.getElementById("thumb-wrap");
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
        if (thumbWrap) thumbWrap.style.display = "none";
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
        if (thumbWrap) thumbWrap.style.display = "none";
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
        if (thumbWrap) thumbWrap.style.display = "";
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
      update();
    });
    update();
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
        const checked = form.querySelectorAll('input[name="account_ids"]:checked');
        let hasMeta = false;
        checked.forEach((cb) => {
          if ((cb.dataset.provider || "") === "meta") hasMeta = true;
        });
        const current = parseInt(select.value, 10);
        let firstVisible = null;
        Array.from(select.options).forEach((opt) => {
          const minutes = parseInt(opt.dataset.minutes || opt.value, 10);
          const hide = hasMeta && minutes < metaMin;
          opt.hidden = hide;
          opt.disabled = hide;
          if (!hide && firstVisible === null) firstVisible = minutes;
        });
        if (hasMeta && current < metaMin && firstVisible !== null) {
          select.value = String(firstVisible);
        }
        const hint = form.querySelector("#meta-interval-hint, .meta-interval-hint");
        if (hint) hint.style.display = hasMeta ? "block" : "none";
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

  function initAuthMethodForm() {
    const form = document.getElementById("account-add-form");
    if (!form) return;
    const passwordInput = document.getElementById("account-password-input");
    const proxyInput = document.getElementById("account-proxy-input");
    const connectBtn = document.getElementById("account-connect-btn");
    const radios = form.querySelectorAll('input[name="auth_method"]');

    function update() {
      const method = form.querySelector('input[name="auth_method"]:checked')?.value || "password";
      const isMeta = method === "meta";
      if (passwordInput) {
        passwordInput.required = method === "password";
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

  function openTwofaModal(message) {
    const modal = document.getElementById("twofa-modal");
    const codeInput = document.getElementById("twofa-code-input");
    const msgEl = document.getElementById("twofa-message");
    if (!modal) return;
    if (msgEl && message) msgEl.textContent = message;
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
    if (!modal) return;
    modal.classList.remove("modal-overlay--open");
    modal.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
    if (hiddenCode) hiddenCode.value = "";
  }

  function initTwofaModal() {
    const modal = document.getElementById("twofa-modal");
    if (!modal || modal.dataset.bound === "1") return;
    modal.dataset.bound = "1";
    const cancelBtn = document.getElementById("twofa-cancel");
    const submitBtn = document.getElementById("twofa-submit");
    const codeInput = document.getElementById("twofa-code-input");

    cancelBtn?.addEventListener("click", closeTwofaModal);
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeTwofaModal();
    });
    submitBtn?.addEventListener("click", () => {
      const form = document.getElementById("account-add-form");
      if (form && typeof form._submitWith2fa === "function") {
        form._submitWith2fa(true);
      }
    });
    codeInput?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        const form = document.getElementById("account-add-form");
        if (form && typeof form._submitWith2fa === "function") {
          form._submitWith2fa(true);
        }
      }
    });
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
          window.location.href = resp.headers.get("Location") || "/accounts";
          return;
        }
        const ct = resp.headers.get("content-type") || "";
        if (resp.status === 403 && ct.includes("application/json")) {
          const data = await resp.json();
          if (data.needs_2fa) {
            openTwofaModal(data.message);
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
          ? `${files.length} mídia(s): escolha um horário diferente para cada Story.`
          : "Ex.: Story 1 às 12:00 e Story 2 às 18:00.";
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
    document.querySelectorAll(".rank-modal-tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        const target = tab.dataset.rankModalTab;
        if (!target) return;
        document.querySelectorAll(".rank-modal-tab").forEach((t) => {
          t.classList.toggle("active", t.dataset.rankModalTab === target);
        });
        document.querySelectorAll(".rank-modal-panel").forEach((panel) => {
          const show = panel.id === `rank-modal-${target}`;
          panel.hidden = !show;
          panel.classList.toggle("active", show);
        });
      });
    });
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

    const videoExt = /\.(mp4|mov|webm|m4v|mkv)$/i;
    const imageExt = /\.(jpe?g|png|webp)$/i;
    const maxReelFiles = 300;
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
      ["name", "content_type", "caption", "story_link", "story_sticker_text", "interval_minutes", "jitter_minutes", "posts_per_batch", "rest_minutes"].forEach((name) => {
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
      form.querySelectorAll('[name="account_ids"]:checked').forEach((field) => {
        data.append("account_ids", field.value);
      });
      const thumb = form.querySelector('[name="thumb"]');
      if (thumb?.files?.[0]) data.append("thumb", thumb.files[0]);
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
        } else if (files.length > maxReelFiles) {
          videoName.textContent = "Muitos vídeos: envie no máximo " + maxReelFiles + " por criação para evitar timeout.";
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
      if (!files.length) {
        e.preventDefault();
        alert(isReel
          ? "Selecione pelo menos um vídeo .mp4. A capa (.png) sozinha não publica."
          : "Selecione o arquivo de mídia.");
        videoInput?.focus();
        return;
      }
      if (isReel) {
        if (files.length > maxReelFiles) {
          e.preventDefault();
          alert("Selecione no máximo " + maxReelFiles + " vídeos por automação. Depois você pode criar outra ou duplicar.");
          return;
        }
        const bad = files.filter((f) => !videoExt.test(f.name));
        if (bad.length) {
          e.preventDefault();
          alert("Estes arquivos não são vídeo: " + bad.map((f) => f.name).join(", "));
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

  function initPage() {
    initLucide();
    initPrivacyBlur();
    initMetaAppsPage();
    initCharts();
    initPeriodPills();
    initContentTypeForm();
    initThumbPreview();
    initScheduleMode();
    initMetaIntervalFilter();
    initAutomationForm();
    initAutomationPlaylistUploads();
    initOgDashboard();
    initCalendarPicker();
    initCalendarTimes();
    initAccountsConnect();
    initAuthMethodForm();
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
        grad.addColorStop(0, "rgba(17,97,254,0.12)");
        grad.addColorStop(0.5, "rgba(17,97,254,0.03)");
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
