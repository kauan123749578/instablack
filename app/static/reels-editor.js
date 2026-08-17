(() => {
  const $ = (id) => document.getElementById(id);
  const canvas = $("canvas");
  const textLayer = $("textLayer");
  const textPreview = $("textPreview");
  const handle = $("resizeHandle");
  const reelVideo = $("reelVideo");
  const reelImage = $("reelImage");
  const emptyState = $("emptyState");
  const statusBox = $("status");
  const previewDialog = $("previewDialog");
  const reelsShell = $("reelsShell");
  const mediaInput = $("mediaInput");
  const mediaList = $("mediaList");
  const mediaCountHint = $("mediaCountHint");
  const fileDropLabel = $("fileDropLabel");
  const publishThumbImg = $("publishThumbImg");

  const state = {
    x: 0.5,
    y: 0.5,
    fontScale: 1,
  };
  let mediaFiles = [];
  let previewIndex = 0;
  let activePhraseIndex = 0;
  let phrases = [{ texto: "Sua frase aqui\nquebra de linha ok" }];
  let interaction = null;
  let objectUrls = [];

  function clamp(min, value, max) {
    return Math.max(min, Math.min(max, value));
  }

  function csrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || "";
  }

  function setStatus(message, kind = "") {
    statusBox.textContent = message || "";
    statusBox.className = `status${kind ? ` ${kind}` : ""}`;
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function revokeUrls() {
    objectUrls.forEach((u) => URL.revokeObjectURL(u));
    objectUrls = [];
  }

  function isVideoFile(file) {
    return (file?.type || "").startsWith("video/");
  }

  function activePhraseText() {
    return (phrases[activePhraseIndex]?.texto || "").trim();
  }

  function syncFitClass() {
    const cover = $("fitInput").value === "cover";
    reelsShell.classList.toggle("fit-contain", !cover);
  }

  function updateTextLayer() {
    syncFitClass();
    const canvasH = canvas.clientHeight || 640;
    const fs = Math.round(canvasH * 0.045 * state.fontScale);
    const color = $("textColor").value || "yellow";
    const borderW = Number($("borderWidth").value || 2);
    const borderColor = $("borderColor").value || "black";

    textPreview.textContent = activePhraseText() || "Texto…";
    textPreview.style.fontSize = `${fs}px`;
    textPreview.style.color = color;
    textPreview.style.webkitTextStroke = borderW
      ? `${Math.max(1, borderW / 2)}px ${borderColor}`
      : "";
    textLayer.style.left = `${state.x * 100}%`;
    textLayer.style.top = `${state.y * 100}%`;
    textLayer.style.transform = `translate(-50%, -50%)`;
    textLayer.hidden = false;

    $("fontScaleValue").textContent = `${Math.round(state.fontScale * 100)}%`;
    $("borderWidthValue").textContent = String(borderW);
  }

  function point(event) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) / rect.width,
      y: (event.clientY - rect.top) / rect.height,
    };
  }

  function showPreviewFile(file) {
    revokeUrls();
    reelVideo.hidden = true;
    reelImage.hidden = true;
    if (!file) {
      emptyState.style.display = "grid";
      publishThumbImg.hidden = true;
      const empty = $("publishThumb")?.querySelector(".publish-thumb-empty");
      if (empty) empty.hidden = false;
      return;
    }
    emptyState.style.display = "none";
    const url = URL.createObjectURL(file);
    objectUrls.push(url);
    if (isVideoFile(file)) {
      reelVideo.src = url;
      reelVideo.hidden = false;
      reelVideo.play().catch(() => {});
    } else {
      reelImage.src = url;
      reelImage.hidden = false;
    }
    publishThumbImg.src = url;
    publishThumbImg.hidden = false;
    const empty = $("publishThumb")?.querySelector(".publish-thumb-empty");
    if (empty) empty.hidden = true;
    updateTextLayer();
  }

  function renderMediaList() {
    if (!mediaFiles.length) {
      mediaList.hidden = true;
      mediaList.innerHTML = "";
      if (mediaCountHint) mediaCountHint.textContent = "";
      fileDropLabel.textContent = "Clique ou arraste mídia de fundo";
      return;
    }
    mediaList.hidden = false;
    if (mediaCountHint) mediaCountHint.textContent = `(${mediaFiles.length})`;
    fileDropLabel.textContent = `${mediaFiles.length} arquivo(s)`;
    mediaList.innerHTML = mediaFiles
      .map(
        (file, idx) =>
          `<li class="${idx === previewIndex ? "active" : ""}" data-idx="${idx}">` +
          `<button type="button" class="studio-media-pick">${idx + 1}. ${escapeHtml(file.name)}</button>` +
          `<button type="button" class="studio-media-remove" title="Remover" data-remove="${idx}">×</button></li>`
      )
      .join("");
  }

  function renderPhrasesList() {
    const box = $("phrasesList");
    if (!box) return;
    box.innerHTML = phrases
      .map(
        (p, idx) =>
          `<div class="reels-phrase-card${idx === activePhraseIndex ? " active" : ""}" data-phrase="${idx}">` +
          `<pre>${escapeHtml(p.texto || "")}</pre>` +
          `<div class="reels-phrase-actions">` +
          `<button type="button" class="btn btn-sm" data-edit="${idx}">Editar</button>` +
          `<button type="button" class="btn btn-sm btn-danger" data-del="${idx}">Remover</button>` +
          `</div></div>`
      )
      .join("");
  }

  function appendLayoutFields(form) {
    updateTextLayer();
    form.append("text", activePhraseText());
    form.append("x", String(state.x));
    form.append("y", String(state.y));
    form.append("font_scale", String(state.fontScale));
    form.append("text_color", $("textColor").value);
    form.append("border_color", $("borderColor").value);
    form.append("border_width", $("borderWidth").value);
    form.append("watermark_text", $("watermarkInput").value);
    form.append(
      "watermark_enabled",
      $("watermarkEnabled").checked ? "true" : "false"
    );
    form.append(
      "fit_cover",
      $("fitInput").value === "cover" ? "true" : "false"
    );
    form.append("photo_duration", $("photoDuration").value || "8");
    form.append("video_duration", $("videoDuration").value || "60");
  }

  function currentMediaFile() {
    return mediaFiles[previewIndex] || mediaFiles[0] || null;
  }

  function formForPreview() {
    const file = currentMediaFile();
    if (!file) throw new Error("Escolha vídeo ou foto de fundo.");
    const form = new FormData();
    form.append("media", file);
    appendLayoutFields(form);
    return form;
  }

  function formForRenderOne() {
    const file = currentMediaFile();
    if (!file) throw new Error("Escolha vídeo ou foto de fundo.");
    if (!activePhraseText()) throw new Error("Digite o texto da frase.");
    const form = new FormData();
    form.append("media", file);
    form.append("filename", `reel_${activePhraseIndex + 1}.mp4`);
    appendLayoutFields(form);
    return form;
  }

  function formForBatch() {
    if (!mediaFiles.length) throw new Error("Escolha mídia de fundo.");
    const valid = phrases.filter((p) => (p.texto || "").trim());
    if (!valid.length) throw new Error("Adicione frases com texto.");
    const form = new FormData();
    mediaFiles.forEach((f) => form.append("media_files", f));
    form.append("phrases_json", JSON.stringify(valid.map((p) => ({ texto: p.texto }))));
    appendLayoutFields(form);
    return form;
  }

  async function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
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

  textLayer.addEventListener("pointerdown", (event) => {
    if (event.target === handle) return;
    const p = point(event);
    interaction = { type: "drag", dx: state.x - p.x, dy: state.y - p.y };
    textLayer.setPointerCapture(event.pointerId);
  });

  handle.addEventListener("pointerdown", (event) => {
    event.stopPropagation();
    const p = point(event);
    interaction = { type: "resize", startX: p.x, startScale: state.fontScale };
    handle.setPointerCapture(event.pointerId);
  });

  window.addEventListener("pointermove", (event) => {
    if (!interaction) return;
    const p = point(event);
    if (interaction.type === "drag") {
      state.x = clamp(0.05, p.x + interaction.dx, 0.95);
      state.y = clamp(0.08, p.y + interaction.dy, 0.92);
    } else {
      const delta = (p.x - interaction.startX) * 2;
      state.fontScale = clamp(0.5, interaction.startScale + delta, 2);
      $("fontScale").value = Math.round(state.fontScale * 100);
    }
    updateTextLayer();
  });
  window.addEventListener("pointerup", () => {
    interaction = null;
  });

  mediaInput.addEventListener("change", () => {
    const picked = [...(mediaInput.files || [])];
    if (!picked.length) return;
    mediaFiles = picked.slice(0, 30);
    previewIndex = 0;
    renderMediaList();
    showPreviewFile(mediaFiles[0]);
  });

  mediaList?.addEventListener("click", (event) => {
    const removeBtn = event.target.closest("[data-remove]");
    if (removeBtn) {
      const idx = Number(removeBtn.dataset.remove);
      mediaFiles.splice(idx, 1);
      if (previewIndex >= mediaFiles.length) previewIndex = Math.max(0, mediaFiles.length - 1);
      renderMediaList();
      showPreviewFile(mediaFiles[previewIndex] || null);
      return;
    }
    const pick = event.target.closest(".studio-media-pick");
    if (!pick) return;
    const li = pick.closest("[data-idx]");
    previewIndex = Number(li?.dataset.idx || 0);
    renderMediaList();
    showPreviewFile(mediaFiles[previewIndex]);
  });

  $("phraseInput").addEventListener("input", () => {
    phrases[activePhraseIndex].texto = $("phraseInput").value;
    renderPhrasesList();
    updateTextLayer();
  });

  $("textColor").addEventListener("change", updateTextLayer);
  $("borderColor").addEventListener("change", updateTextLayer);
  $("borderWidth").addEventListener("input", updateTextLayer);
  $("fontScale").addEventListener("input", (e) => {
    state.fontScale = Number(e.target.value) / 100;
    updateTextLayer();
  });
  $("fitInput").addEventListener("change", updateTextLayer);

  $("addPhraseBtn").addEventListener("click", () => {
    phrases.push({ texto: "Nova frase" });
    activePhraseIndex = phrases.length - 1;
    $("phraseInput").value = phrases[activePhraseIndex].texto;
    renderPhrasesList();
    updateTextLayer();
  });

  $("phrasesList").addEventListener("click", (event) => {
    const del = event.target.closest("[data-del]");
    if (del) {
      const idx = Number(del.dataset.del);
      if (phrases.length <= 1) return;
      phrases.splice(idx, 1);
      if (activePhraseIndex >= phrases.length) activePhraseIndex = phrases.length - 1;
      $("phraseInput").value = phrases[activePhraseIndex].texto;
      renderPhrasesList();
      updateTextLayer();
      return;
    }
    const edit = event.target.closest("[data-edit]");
    const card = event.target.closest("[data-phrase]");
    const idx = Number((edit || card)?.dataset.edit ?? card?.dataset.phrase);
    if (Number.isNaN(idx)) return;
    activePhraseIndex = idx;
    $("phraseInput").value = phrases[idx].texto;
    renderPhrasesList();
    updateTextLayer();
  });

  $("closePreview").addEventListener("click", () => previewDialog.close());

  $("previewButton").addEventListener("click", async () => {
    const btn = $("previewButton");
    btn.disabled = true;
    setStatus("Gerando prévia FFmpeg…");
    try {
      const response = await fetch("/reels-editor/preview", {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken() },
        body: formForPreview(),
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
      btn.disabled = false;
    }
  });

  $("renderOneButton").addEventListener("click", async () => {
    const btn = $("renderOneButton");
    btn.disabled = true;
    setStatus("Gerando reel…");
    try {
      const response = await fetch("/reels-editor/render", {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken() },
        body: formForRenderOne(),
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
      await downloadBlob(blob, `reel_${activePhraseIndex + 1}.mp4`);
      setStatus("Reel baixado.", "ok");
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      btn.disabled = false;
    }
  });

  $("renderBatchButton").addEventListener("click", async () => {
    if (!confirm(`Gerar ${phrases.length} reel(s) em ZIP? Pode demorar.`)) return;
    const btn = $("renderBatchButton");
    btn.disabled = true;
    setStatus("Gerando lote…");
    try {
      const response = await fetch("/reels-editor/render-batch", {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken() },
        body: formForBatch(),
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
      await downloadBlob(blob, "reels_gerados.zip");
      setStatus("ZIP baixado.", "ok");
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      btn.disabled = false;
    }
  });

  window.addEventListener("resize", () => updateTextLayer());

  $("phraseInput").value = phrases[0].texto;
  renderPhrasesList();
  updateTextLayer();

  if (window.lucide?.createIcons) window.lucide.createIcons();
})();
