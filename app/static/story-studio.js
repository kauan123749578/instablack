(() => {
  const $ = (id) => document.getElementById(id);
  const canvas = $("canvas");
  const sticker = $("sticker");
  const handle = $("resizeHandle");
  const imageInput = $("imageInput");
  const storyImage = $("storyImage");
  const emptyState = $("emptyState");
  const statusBox = $("status");
  const previewDialog = $("previewDialog");
  const studioShell = $("studioShell");
  const publishThumbImg = $("publishThumbImg");
  const fileDropLabel = $("fileDropLabel");
  const mediaList = $("mediaList");
  const mediaCountHint = $("mediaCountHint");

  const BASE_WIDTH = 0.6;
  const BASE_HEIGHT = 0.068625;
  const state = {
    x: 0.5,
    y: 0.8,
    scale: 1,
    rotation: 0,
    variant: "default",
    mode: "custom",
    measuredWidth: BASE_WIDTH,
  };
  let imageFiles = [];
  let previewIndex = 0;
  let interaction = null;

  function clamp(min, value, max) {
    return Math.max(min, Math.min(max, value));
  }

  function heightFrac() {
    return BASE_HEIGHT * state.scale;
  }

  function widthFrac() {
    if (!isCustom()) return BASE_WIDTH * state.scale;
    return state.measuredWidth;
  }

  function isCustom() {
    return state.mode === "custom";
  }

  function labelText() {
    return ($("textInput").value || domainText()).replace(/\n/g, " ").slice(0, 53);
  }

  function setStatus(message, kind = "") {
    statusBox.textContent = message || "";
    statusBox.className = `status${kind ? ` ${kind}` : ""}`;
  }

  function selectedAccountIds() {
    return [...document.querySelectorAll('#accountsList input[type="checkbox"]:checked')]
      .map((el) => el.value)
      .filter(Boolean);
  }

  function syncModeUi() {
    const custom = isCustom();
    studioShell.classList.toggle("mode-custom", custom);
    studioShell.classList.toggle("mode-native", !custom);
    sticker.hidden = !custom;
  }

  function measureStickerWidth() {
    if (!isCustom() || canvas.clientWidth < 1) {
      state.measuredWidth = BASE_WIDTH * state.scale;
      return;
    }
    const ratio = sticker.offsetWidth / canvas.clientWidth;
    state.measuredWidth = clamp(0.14, ratio || BASE_WIDTH * state.scale, 0.9);
  }

  function updateSticker() {
    syncModeUi();
    if (!isCustom()) return;

    const h = heightFrac();
    const canvasH = canvas.clientHeight || 1;
    sticker.style.height = `${h * 100}%`;
    sticker.style.width = "auto";
    sticker.style.fontSize = `${canvasH * h}px`;
    sticker.style.left = `${state.x * 100}%`;
    sticker.style.top = `${state.y * 100}%`;
    sticker.style.transform = `translate(-50%, -50%) rotate(${state.rotation}turn)`;
    sticker.className = `sticker ${state.variant} selected`;
    $("stickerText").textContent = labelText();

    measureStickerWidth();
    const w = widthFrac();
    state.x = clamp(w / 2 + 0.02, state.x, 1 - w / 2 - 0.02);
    state.y = clamp(h / 2 + 0.02, state.y, 1 - h / 2 - 0.02);
    sticker.style.left = `${state.x * 100}%`;
    sticker.style.top = `${state.y * 100}%`;

    $("sizeValue").textContent = `${Math.round(state.scale * 100)}%`;
    $("rotationValue").textContent = `${Math.round(state.rotation * 360)}°`;
  }

  function domainText() {
    try {
      let value = $("urlInput").value.trim();
      if (!/^https?:\/\//i.test(value)) value = `https://${value}`;
      return new URL(value).hostname.replace(/^www\./i, "").toUpperCase().slice(0, 60);
    } catch {
      return "LINK";
    }
  }

  function point(event) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) / rect.width,
      y: (event.clientY - rect.top) / rect.height,
    };
  }

  function showPreviewFile(file) {
    if (!file) {
      storyImage.removeAttribute("src");
      storyImage.style.display = "none";
      emptyState.style.display = "grid";
      publishThumbImg.hidden = true;
      const empty = $("publishThumb")?.querySelector(".publish-thumb-empty");
      if (empty) empty.hidden = false;
      return;
    }
    const url = URL.createObjectURL(file);
    storyImage.src = url;
    storyImage.style.display = "block";
    emptyState.style.display = "none";
    publishThumbImg.src = url;
    publishThumbImg.hidden = false;
    const empty = $("publishThumb")?.querySelector(".publish-thumb-empty");
    if (empty) empty.hidden = true;
    updateSticker();
  }

  function renderMediaList() {
    if (!mediaList) return;
    if (!imageFiles.length) {
      mediaList.hidden = true;
      mediaList.innerHTML = "";
      if (mediaCountHint) mediaCountHint.textContent = "";
      fileDropLabel.textContent = "Clique ou arraste uma ou mais fotos";
      return;
    }
    mediaList.hidden = false;
    if (mediaCountHint) mediaCountHint.textContent = `(${imageFiles.length})`;
    fileDropLabel.textContent = `${imageFiles.length} foto(s) selecionada(s)`;
    mediaList.innerHTML = imageFiles
      .map(
        (file, idx) =>
          `<li class="${idx === previewIndex ? "active" : ""}" data-idx="${idx}">` +
          `<button type="button" class="studio-media-pick">${idx + 1}. ${escapeHtml(file.name)}</button>` +
          `<button type="button" class="studio-media-remove" title="Remover" data-remove="${idx}">×</button></li>`
      )
      .join("");
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function appendLayoutFields(form) {
    updateSticker();
    form.append("url", $("urlInput").value);
    form.append("text", $("textInput").value);
    form.append("x", String(state.x));
    form.append("y", String(state.y));
    form.append("width", String(widthFrac()));
    form.append("height", String(heightFrac()));
    form.append("rotation", String(state.rotation));
    form.append("variant", state.variant);
    form.append("cover", $("fitInput").value === "cover" ? "true" : "false");
    form.append("draw_sticker", isCustom() ? "true" : "false");
  }

  function formDataForPreview() {
    const file = imageFiles[previewIndex] || imageFiles[0];
    if (!file) throw new Error("Escolha pelo menos uma foto");
    const form = new FormData();
    form.append("image", file);
    const accounts = selectedAccountIds();
    form.append("account_id", accounts[0] || "0");
    appendLayoutFields(form);
    return form;
  }

  function formDataForPublish(accountId) {
    const file = imageFiles[0];
    if (!file) throw new Error("Escolha pelo menos uma foto");
    if (!accountId) throw new Error("Selecione pelo menos uma conta");
    if (imageFiles.length > 1) {
      throw new Error("Para várias fotos use AGENDAR (aba Agendar + dias).");
    }
    const form = new FormData();
    form.append("image", file);
    form.append("account_id", accountId);
    appendLayoutFields(form);
    return form;
  }

  function formDataForSchedule() {
    if (!imageFiles.length) throw new Error("Escolha pelo menos uma foto");
    const accounts = selectedAccountIds();
    if (!accounts.length) throw new Error("Selecione pelo menos uma conta");
    const days = ($("calendar-days-input")?.value || "[]").trim();
    let parsed = [];
    try {
      parsed = JSON.parse(days);
    } catch {
      parsed = [];
    }
    if (!Array.isArray(parsed) || !parsed.length) {
      throw new Error("Selecione os dias na aba Agendar");
    }
    const times = [...document.querySelectorAll('#calendar-times-list input[type="time"]')]
      .map((el) => el.value)
      .filter(Boolean);
    if (!times.length) throw new Error("Informe pelo menos um horário");

    const form = new FormData();
    imageFiles.forEach((file) => form.append("images", file));
    accounts.forEach((id) => form.append("account_ids", id));
    form.append("calendar_days", JSON.stringify(parsed));
    times.forEach((t) => form.append("calendar_times", t));
    form.append("name", $("scheduleName")?.value || "");
    appendLayoutFields(form);
    return form;
  }

  function initCalendar() {
    const grid = $("calendar-grid");
    const input = $("calendar-days-input");
    const countEl = $("cal-count");
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
      if (countEl) countEl.textContent = `${arr.length} dia(s)`;
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

    $("cal-select-all")?.addEventListener("click", () => {
      for (let d = 1; d <= daysInMonth; d++) selected.add(d);
      sync();
      grid.querySelectorAll(".cal-day:not(.cal-day--empty)").forEach((el) => {
        el.classList.add("cal-day--selected");
      });
    });
    $("cal-clear")?.addEventListener("click", () => {
      selected.clear();
      sync();
      grid.querySelectorAll(".cal-day").forEach((el) => el.classList.remove("cal-day--selected"));
    });

    $("cal-add-time")?.addEventListener("click", () => {
      const list = $("calendar-times-list");
      if (!list) return;
      const row = document.createElement("div");
      row.className = "calendar-time-row";
      row.innerHTML = '<input type="time" name="calendar_times" value="18:00">';
      list.appendChild(row);
    });
  }

  document.querySelectorAll(".studio-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const name = tab.dataset.tab;
      document.querySelectorAll(".studio-tab").forEach((t) => t.classList.toggle("active", t === tab));
      document.querySelectorAll(".studio-tab-panel").forEach((panel) => {
        panel.classList.toggle("active", panel.id === `tab-${name}`);
      });
    });
  });

  $("clearUrl").addEventListener("click", () => {
    $("urlInput").value = "";
    $("urlInput").focus();
    if (!$("textInput").value.trim()) updateSticker();
  });

  sticker.addEventListener("pointerdown", (event) => {
    if (event.target === handle || !isCustom()) return;
    const p = point(event);
    interaction = { type: "drag", dx: state.x - p.x, dy: state.y - p.y };
    sticker.setPointerCapture(event.pointerId);
  });

  handle.addEventListener("pointerdown", (event) => {
    event.stopPropagation();
    if (!isCustom()) return;
    const p = point(event);
    interaction = { type: "resize", startX: p.x, startScale: state.scale };
    handle.setPointerCapture(event.pointerId);
  });

  window.addEventListener("pointermove", (event) => {
    if (!interaction) return;
    const p = point(event);
    if (interaction.type === "drag") {
      state.x = p.x + interaction.dx;
      state.y = p.y + interaction.dy;
    } else {
      const delta = (p.x - interaction.startX) * 2;
      state.scale = clamp(0.7, interaction.startScale + delta, 1.4);
      $("sizeInput").value = Math.round(state.scale * 100);
    }
    updateSticker();
  });
  window.addEventListener("pointerup", () => {
    interaction = null;
  });

  imageInput.addEventListener("change", () => {
    const picked = [...(imageInput.files || [])];
    if (!picked.length) return;
    imageFiles = picked.slice(0, 30);
    previewIndex = 0;
    renderMediaList();
    showPreviewFile(imageFiles[0]);
  });

  mediaList?.addEventListener("click", (event) => {
    const removeBtn = event.target.closest("[data-remove]");
    if (removeBtn) {
      const idx = Number(removeBtn.dataset.remove);
      imageFiles.splice(idx, 1);
      if (previewIndex >= imageFiles.length) previewIndex = Math.max(0, imageFiles.length - 1);
      renderMediaList();
      showPreviewFile(imageFiles[previewIndex] || null);
      return;
    }
    const pick = event.target.closest(".studio-media-pick");
    if (!pick) return;
    const li = pick.closest("[data-idx]");
    previewIndex = Number(li?.dataset.idx || 0);
    renderMediaList();
    showPreviewFile(imageFiles[previewIndex]);
  });

  $("fitInput").addEventListener("change", (event) => {
    storyImage.style.objectFit = event.target.value;
  });
  $("modeInput").addEventListener("change", (event) => {
    state.mode = event.target.value;
    updateSticker();
  });
  $("textInput").addEventListener("input", updateSticker);
  $("urlInput").addEventListener("input", () => {
    if (!$("textInput").value.trim()) updateSticker();
  });
  $("variantInput").addEventListener("change", (event) => {
    state.variant = event.target.value;
    updateSticker();
  });
  $("sizeInput").addEventListener("input", (event) => {
    state.scale = Number(event.target.value) / 100;
    updateSticker();
  });
  $("rotationInput").addEventListener("input", (event) => {
    state.rotation = Number(event.target.value) / 360;
    updateSticker();
  });
  $("closePreview").addEventListener("click", () => previewDialog.close());

  window.addEventListener("resize", () => updateSticker());

  $("renderButton").addEventListener("click", async () => {
    const button = $("renderButton");
    button.disabled = true;
    setStatus("Gerando prévia…");
    try {
      const response = await fetch("/automations/story-studio/preview", {
        method: "POST",
        body: formDataForPreview(),
      });
      if (!response.ok) {
        let message = `HTTP ${response.status}`;
        try {
          const payload = await response.json();
          message = payload.detail || payload.error || message;
        } catch {}
        throw new Error(message);
      }
      const blob = await response.blob();
      $("finalPreview").src = URL.createObjectURL(blob);
      setStatus("Prévia pronta.", "ok");
      previewDialog.showModal();
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  $("publishButton").addEventListener("click", async () => {
    const accounts = selectedAccountIds();
    if (!accounts.length) {
      setStatus("Selecione pelo menos uma conta.", "error");
      return;
    }
    if (!confirm(`Publicar este Story agora em ${accounts.length} conta(s)?`)) return;
    const button = $("publishButton");
    button.disabled = true;
    setStatus("Enfileirando…");
    try {
      let lastRedirect = "/logs?watch=1";
      for (const accountId of accounts) {
        const response = await fetch("/automations/story-studio/publish", {
          method: "POST",
          body: formDataForPublish(accountId),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(payload.detail || payload.error || `HTTP ${response.status}`);
        }
        lastRedirect = payload.redirect || lastRedirect;
      }
      setStatus(`Story enfileirado para ${accounts.length} conta(s).`, "ok");
      window.location.href = lastRedirect;
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  $("scheduleButton").addEventListener("click", async () => {
    if (!confirm("Criar automação de Story agendado?")) return;
    const button = $("scheduleButton");
    button.disabled = true;
    setStatus("Agendando…");
    try {
      const response = await fetch("/automations/story-studio/schedule", {
        method: "POST",
        body: formDataForSchedule(),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = payload.detail;
        const msg = Array.isArray(detail)
          ? detail.map((d) => d.msg || d).join("; ")
          : detail || payload.error || `HTTP ${response.status}`;
        throw new Error(msg);
      }
      setStatus(payload.message || "Agendado.", "ok");
      if (payload.redirect) window.location.href = payload.redirect;
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  // Prefere cookies web; marca a primeira conta web
  const webBox = [...document.querySelectorAll('#accountsList input[data-web="1"]')][0];
  if (webBox && !webBox.disabled) webBox.checked = true;

  initCalendar();

  if (window.lucide && typeof window.lucide.createIcons === "function") {
    window.lucide.createIcons();
  }

  updateSticker();
})();
