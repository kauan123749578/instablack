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
  const accountInput = $("accountInput");
  const studioShell = $("studioShell");
  const publishThumbImg = $("publishThumbImg");
  const fileDropLabel = $("fileDropLabel");

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
  let imageFile = null;
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

  function formData() {
    if (!imageFile) throw new Error("Escolha uma foto");
    if (!accountInput.value) throw new Error("Selecione uma conta Instagram");
    updateSticker();
    const form = new FormData();
    form.append("image", imageFile);
    form.append("account_id", accountInput.value);
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
    return form;
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
    imageFile = imageInput.files[0] || null;
    if (!imageFile) return;
    const url = URL.createObjectURL(imageFile);
    storyImage.src = url;
    storyImage.style.display = "block";
    emptyState.style.display = "none";
    fileDropLabel.textContent = imageFile.name;
    publishThumbImg.src = url;
    publishThumbImg.hidden = false;
    const empty = $("publishThumb")?.querySelector(".publish-thumb-empty");
    if (empty) empty.hidden = true;
    updateSticker();
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
        body: formData(),
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
    if (!confirm("Publicar este Story no Instagram?")) return;
    const button = $("publishButton");
    button.disabled = true;
    setStatus("Enfileirando…");
    try {
      const response = await fetch("/automations/story-studio/publish", {
        method: "POST",
        body: formData(),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || payload.error || `HTTP ${response.status}`);
      }
      setStatus(payload.message || "Enfileirado.", "ok");
      if (payload.redirect) window.location.href = payload.redirect;
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  if (!accountInput.value) {
    const webOpt = [...accountInput.options].find((o) => o.text.includes("Cookies web"));
    if (webOpt) accountInput.value = webOpt.value;
  }

  if (window.lucide && typeof window.lucide.createIcons === "function") {
    window.lucide.createIcons();
  }

  updateSticker();
})();
