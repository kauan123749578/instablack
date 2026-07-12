(function () {
  "use strict";

  const appContent = document.getElementById("app-content");
  const drawer = document.getElementById("mobile-drawer");
  const drawerOpen = document.getElementById("drawer-open");
  const drawerBackdrop = document.getElementById("drawer-backdrop");
  const sidebar = document.getElementById("sidebar");

  function closeDrawer() { drawer?.classList.remove("open"); }

  drawerOpen?.addEventListener("click", () => drawer?.classList.add("open"));
  drawerBackdrop?.addEventListener("click", closeDrawer);

  document.getElementById("mobile-menu-btn")?.addEventListener("click", () => {
    sidebar?.classList.toggle("mobile-open");
  });

  function setActiveNav(path) {
    document.querySelectorAll("[data-nav]").forEach((el) => {
      const href = el.getAttribute("data-nav") || el.getAttribute("href");
      const isActive = path === href || (href !== "/" && path.startsWith(href));
      el.classList.toggle("active", isActive);
    });
  }
  setActiveNav(window.location.pathname);

  async function navigateTo(url, push = true) {
    if (!appContent) { window.location.href = url; return; }
    appContent.classList.add("content-loading");
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
  });

  window.addEventListener("popstate", (e) => {
    if (e.state?.url) navigateTo(e.state.url, false);
  });

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
            '<div class="og-timeline-body"><strong>@' + escapeHtml(item.username || "?") +
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
    setInterval(poll, 2500);
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
            "<td><strong>@" + escapeHtml(item.username || "?") + "</strong></td>" +
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
    setInterval(loadNotifications, 30000);
  }

  function initContentTypeForm() {
    const sel = document.getElementById("content-type");
    const mediaLabel = document.getElementById("media-label");
    const captionWrap = document.getElementById("caption-wrap");
    const thumbWrap = document.getElementById("thumb-wrap");
    const storyLinkWrap = document.getElementById("story-link-wrap");
    const videoInput = document.getElementById("video-input");
    const videoList = document.getElementById("video-file-list");
    if (!sel) return;

    const params = new URLSearchParams(window.location.search);
    const pathType = window.location.pathname.endsWith("/story") ? "story" : null;
    if (params.get("type") === "story" || pathType === "story") sel.value = "story";

    function update() {
      const t = sel.value;
      if (t === "story") {
        if (mediaLabel) mediaLabel.firstChild.textContent = "Mídia do Story (foto ou vídeo) ";
        if (videoInput) {
          videoInput.name = "video";
          videoInput.removeAttribute("multiple");
          videoInput.accept = "image/jpeg,image/png,image/webp,video/mp4,video/quicktime";
        }
        if (videoList) videoList.style.display = "none";
        if (captionWrap) captionWrap.style.display = "none";
        if (thumbWrap) thumbWrap.style.display = "none";
        if (storyLinkWrap) storyLinkWrap.style.display = "";
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
      if (calendarWrap) calendarWrap.style.display = (isCalendar && storyMode) ? "" : "none";
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
    const radios = form.querySelectorAll('input[name="auth_method"]');

    function update() {
      const method = form.querySelector('input[name="auth_method"]:checked')?.value || "sessionid";
      if (passwordInput) {
        passwordInput.required = method === "password";
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

  function initLucide() {
    if (typeof lucide !== "undefined") {
      lucide.createIcons();
    }
  }

  function initOgDashboard() {
    const tooltip = document.getElementById("og-chart-tooltip");
    document.querySelectorAll(".og-chart-dot").forEach((dot) => {
      dot.addEventListener("mouseenter", () => {
        if (!tooltip) return;
        tooltip.textContent = dot.dataset.tip || "";
        tooltip.style.opacity = "1";
        const wrap = document.getElementById("og-line-chart");
        if (wrap) {
          const wr = wrap.getBoundingClientRect();
          const dr = dot.getBoundingClientRect();
          tooltip.style.left = (dr.left - wr.left + dr.width / 2) + "px";
          tooltip.style.top = (dr.top - wr.top - 36) + "px";
        }
      });
      dot.addEventListener("mouseleave", () => {
        if (tooltip) tooltip.style.opacity = "0";
      });
    });

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
  }

  function initAutomationForm() {
    const form = document.getElementById("automation-form");
    if (!form) return;

    const contentType = document.getElementById("content-type");
    const videoInput = document.getElementById("video-input");
    const videoName = document.getElementById("video-file-name");
    const videoList = document.getElementById("video-file-list");
    const accountBoxes = form.querySelectorAll('input[name="account_ids"]');

    const videoExt = /\.(mp4|mov|webm|m4v|mkv)$/i;
    const imageExt = /\.(jpe?g|png|webp)$/i;

    function updateVideoLabel() {
      const files = videoInput?.files ? Array.from(videoInput.files) : [];
      const isReel = contentType?.value === "reel";
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
        } else {
          const mb = Math.round(files.reduce((s, f) => s + f.size, 0) / 1024 / 1024 * 10) / 10;
          videoName.textContent = files.length + " vídeo(s) — " + mb + " MB total";
          videoName.style.color = "var(--green, #22c55e)";
        }
        if (videoList) {
          videoList.innerHTML = files.map((f) => "<li>" + f.name + "</li>").join("");
          videoList.style.display = files.length > 1 ? "block" : "none";
        }
      } else {
        videoName.textContent = files[0].name;
        videoName.style.color = "var(--green, #22c55e)";
        if (videoList) videoList.style.display = "none";
      }
    }

    videoInput?.addEventListener("change", updateVideoLabel);
    document.addEventListener("automation-media-changed", updateVideoLabel);
    updateVideoLabel();

    form.addEventListener("submit", (e) => {
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
        const bad = files.filter((f) => !videoExt.test(f.name));
        if (bad.length) {
          e.preventDefault();
          alert("Estes arquivos não são vídeo: " + bad.map((f) => f.name).join(", "));
          return;
        }
      } else if (contentType?.value === "photo") {
        if (!imageExt.test(files[0].name)) {
          e.preventDefault();
          alert("Para foto no feed, use .jpg ou .png.");
          return;
        }
      }
      const checked = Array.from(accountBoxes).some((cb) => cb.checked);
      if (accountBoxes.length && !checked) {
        e.preventDefault();
        alert("Marque pelo menos uma conta para publicar.");
        return;
      }
    });
  }

  function initPage() {
    initLucide();
    initCharts();
    initPeriodPills();
    initContentTypeForm();
    initThumbPreview();
    initScheduleMode();
    initAutomationForm();
    initOgDashboard();
    initCalendarPicker();
    initAccountsConnect();
    initAuthMethodForm();
    initProxyInput();
    initAccountProxyUpdate();
    initWebPush();
    initProfileNotifications();
    initNotifCard();
    initDashActivityPoll();
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
