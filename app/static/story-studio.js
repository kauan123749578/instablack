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

  const BASE_WIDTH = 0.6;
  const BASE_HEIGHT = 0.068625;
  const state = { x: 0.5, y: 0.8, scale: 1, rotation: 0, variant: "default" };
  let imageFile = null;
  let interaction = null;

  function clamp(min, value, max) {
    return Math.max(min, Math.min(max, value));
  }
  function width() {
    return BASE_WIDTH * state.scale;
  }
  function height() {
    return BASE_HEIGHT * state.scale;
  }

  function setStatus(message, kind = "") {
    statusBox.textContent = message || "";
    statusBox.className = `status${kind ? ` ${kind}` : ""}`;
  }

  function updateSticker() {
    state.x = clamp(width() / 2 + 0.02, state.x, 1 - width() / 2 - 0.02);
    state.y = clamp(height() / 2 + 0.02, state.y, 1 - height() / 2 - 0.02);
    sticker.style.left = `${state.x * 100}%`;
    sticker.style.top = `${state.y * 100}%`;
    sticker.style.width = `${width() * 100}%`;
    sticker.style.height = `${height() * 100}%`;
    sticker.style.transform = `translate(-50%, -50%) rotate(${state.rotation}turn)`;
    sticker.style.setProperty("--scale", state.scale);
    sticker.className = `sticker ${state.variant} selected`;
    $("stickerText").textContent = ($("textInput").value || domainText())
      .replace(/\n/g, " ")
      .slice(0, 53);
    $("sizeValue").textContent = `${Math.round(state.scale * 100)}%`;
    $("rotationValue").textContent = `${Math.round(state.rotation * 360)}°`;
    $("metricX").textContent = state.x.toFixed(3);
    $("metricY").textContent = state.y.toFixed(3);
    $("metricW").textContent = width().toFixed(3);
    $("metricH").textContent = height().toFixed(3);
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
    const form = new FormData();
    form.append("image", imageFile);
    form.append("account_id", accountInput.value);
    form.append("url", $("urlInput").value);
    form.append("text", $("textInput").value);
    form.append("x", String(state.x));
    form.append("y", String(state.y));
    form.append("width", String(width()));
    form.append("height", String(height()));
    form.append("rotation", String(state.rotation));
    form.append("variant", state.variant);
    form.append("cover", $("fitInput").value === "cover" ? "true" : "false");
    return form;
  }

  sticker.addEventListener("pointerdown", (event) => {
    if (event.target === handle) return;
    const p = point(event);
    interaction = { type: "drag", dx: state.x - p.x, dy: state.y - p.y };
    sticker.setPointerCapture(event.pointerId);
  });

  handle.addEventListener("pointerdown", (event) => {
    event.stopPropagation();
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
      state.scale = clamp(0.3, interaction.startScale + delta, 1.6);
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
    storyImage.src = URL.createObjectURL(imageFile);
    storyImage.style.display = "block";
    emptyState.style.display = "none";
  });

  $("fitInput").addEventListener("change", (event) => {
    storyImage.style.objectFit = event.target.value;
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

  $("renderButton").addEventListener("click", async () => {
    const button = $("renderButton");
    button.disabled = true;
    setStatus("Gerando prévia final...");
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
      setStatus("Prévia final gerada.", "ok");
      previewDialog.showModal();
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  $("publishButton").addEventListener("click", async () => {
    if (!confirm("Publicar este Story de verdade no Instagram?")) return;
    const button = $("publishButton");
    button.disabled = true;
    setStatus("Enfileirando publicação...");
    try {
      const response = await fetch("/automations/story-studio/publish", {
        method: "POST",
        body: formData(),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || payload.error || `HTTP ${response.status}`);
      }
      setStatus(payload.message || "Publicação enfileirada.", "ok");
      if (payload.redirect) {
        window.location.href = payload.redirect;
      }
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  updateSticker();
})();
