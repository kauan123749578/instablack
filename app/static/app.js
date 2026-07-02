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
    if (!sel) return;

    const params = new URLSearchParams(window.location.search);
    const pathType = window.location.pathname.endsWith("/story") ? "story" : null;
    if (params.get("type") === "story" || pathType === "story") sel.value = "story";

    function update() {
      const t = sel.value;
      if (t === "story") {
        if (mediaLabel) mediaLabel.textContent = "Mídia do Story (foto ou vídeo)";
        if (captionWrap) captionWrap.style.display = "none";
        if (thumbWrap) thumbWrap.style.display = "none";
      } else if (t === "photo") {
        if (mediaLabel) mediaLabel.textContent = "Foto para o feed (.jpg/.png)";
        if (captionWrap) captionWrap.style.display = "";
        if (thumbWrap) thumbWrap.style.display = "none";
      } else {
        if (mediaLabel) mediaLabel.textContent = "Vídeo Reels (.mp4)";
        if (captionWrap) captionWrap.style.display = "";
        if (thumbWrap) thumbWrap.style.display = "";
      }
    }
    sel.addEventListener("change", update);
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
    const intervalWrap = document.getElementById("interval-wrap");
    const submitBtn = document.getElementById("submit-btn");
    const contentType = document.getElementById("content-type");
    if (!modeNow || !modeRecurring) return;

    function update() {
      const isNow = modeNow.checked;
      const isStory = contentType?.value === "story";
      if (intervalWrap) intervalWrap.style.display = isNow ? "none" : "";
      if (submitBtn) {
        if (isNow) {
          submitBtn.textContent = isStory ? "Postar Story agora" : "Publicar agora";
        } else {
          submitBtn.textContent = isStory ? "Agendar Story" : "Criar automação";
        }
      }
    }
    modeNow.addEventListener("change", update);
    modeRecurring.addEventListener("change", update);
    contentType?.addEventListener("change", update);
    update();
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
    const mediaLabel = document.getElementById("media-label-cal");
    const captionWrap = document.getElementById("caption-wrap-cal");
    const thumbWrap = document.getElementById("thumb-wrap-cal");
    if (sel) {
      function updateType() {
        const t = sel.value;
        if (t === "story") {
          if (mediaLabel) mediaLabel.textContent = "Mídia do Story";
          if (captionWrap) captionWrap.style.display = "none";
          if (thumbWrap) thumbWrap.style.display = "none";
        } else if (t === "photo") {
          if (mediaLabel) mediaLabel.textContent = "Foto do feed";
          if (captionWrap) captionWrap.style.display = "";
          if (thumbWrap) thumbWrap.style.display = "none";
        } else {
          if (mediaLabel) mediaLabel.textContent = "Vídeo Reels";
          if (captionWrap) captionWrap.style.display = "";
          if (thumbWrap) thumbWrap.style.display = "";
        }
      }
      sel.addEventListener("change", updateType);
      updateType();
    }
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

  function initPage() {
    initLucide();
    initCharts();
    initPeriodPills();
    initContentTypeForm();
    initThumbPreview();
    initScheduleMode();
    initOgDashboard();
    initCalendarPicker();
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
