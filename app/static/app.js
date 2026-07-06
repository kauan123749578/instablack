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
    document.querySelectorAll(".period-pill").forEach((pill) => {
      pill.addEventListener("click", () => {
        document.querySelectorAll(".period-pill").forEach((p) => p.classList.remove("active"));
        pill.classList.add("active");
      });
    });
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
    const passwordWrap = document.getElementById("password-wrap");
    const sessionWrap = document.getElementById("sessionid-wrap");
    const importWrap = document.getElementById("import-wrap");
    const radios = form.querySelectorAll('input[name="auth_method"]');

    function update() {
      const method = form.querySelector('input[name="auth_method"]:checked')?.value || "sessionid";
      if (sessionWrap) sessionWrap.style.display = method === "sessionid" ? "" : "none";
      if (importWrap) importWrap.style.display = method === "import" ? "" : "none";
      if (passwordWrap) passwordWrap.style.display = method === "password" || method === "import" ? "" : "none";
    }
    radios.forEach((r) => r.addEventListener("change", update));
    update();
  }

  function initAccountsConnect() {
    const form = document.getElementById("account-add-form");
    const modal = document.getElementById("twofa-modal");
    const codeInput = document.getElementById("twofa-code-input");
    const hiddenCode = document.getElementById("verification-code-hidden");
    const submitBtn = document.getElementById("twofa-submit");
    const cancelBtn = document.getElementById("twofa-cancel");
    const connectBtn = document.getElementById("account-connect-btn");
    if (!form) return;

    function openModal() {
      modal?.classList.add("modal-overlay--open");
      if (modal) modal.setAttribute("aria-hidden", "false");
      if (codeInput) { codeInput.value = ""; codeInput.focus(); }
    }
    function closeModal() {
      modal?.classList.remove("modal-overlay--open");
      if (modal) modal.setAttribute("aria-hidden", "true");
      if (hiddenCode) hiddenCode.value = "";
    }

    cancelBtn?.addEventListener("click", closeModal);
    modal?.addEventListener("click", (e) => {
      if (e.target === modal) closeModal();
    });

    async function submitForm(with2fa) {
      const fd = new FormData(form);
      const proxyInput = form.querySelector('[name="proxy"]');
      if (proxyInput) fd.set("proxy", normalizeProxyValue(proxyInput.value));
      if (with2fa && codeInput) fd.set("verification_code", codeInput.value.trim());
      if (connectBtn) { connectBtn.disabled = true; connectBtn.textContent = "Conectando…"; }
      try {
        const resp = await fetch(form.action, {
          method: "POST",
          body: fd,
          headers: { "X-Requested-With": "fetch" },
          redirect: "manual",
        });
        if (resp.status === 303 || resp.status === 302) {
          window.location.href = resp.headers.get("Location") || "/accounts";
          return;
        }
        const ct = resp.headers.get("content-type") || "";
        if (ct.includes("application/json")) {
          const data = await resp.json();
          if (data.needs_2fa) {
            openModal();
            return;
          }
        }
        if (resp.ok || resp.status === 400) {
          const html = await resp.text();
          const doc = new DOMParser().parseFromString(html, "text/html");
          const newContent = doc.getElementById("app-content");
          if (newContent && appContent) {
            appContent.innerHTML = newContent.innerHTML;
            history.pushState({ url: "/accounts" }, "", "/accounts");
            initPage();
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

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      submitForm(false);
    });
    submitBtn?.addEventListener("click", () => submitForm(true));
    codeInput?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); submitForm(true); }
    });

    if (modal?.classList.contains("modal-overlay--open")) openModal();
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
        grad.addColorStop(0, "rgba(225,48,108,0.1)");
        grad.addColorStop(0.5, "rgba(225,48,108,0.02)");
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
