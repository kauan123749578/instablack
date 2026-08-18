(() => {
  const EMOJI_BASE = "/static/reels-emojis";
  const $ = (id) => document.getElementById(id);
  const canvas = $("canvas");
  const textLayer = $("textLayer");
  const textPreview = $("textPreview");
  const emojiPreviewRow = $("emojiPreviewRow");
  const watermarkLayer = $("watermarkLayer");
  const watermarkPreview = $("watermarkPreview");
  const handle = $("resizeHandle");
  const reelVideo = $("reelVideo");
  const reelImage = $("reelImage");
  const emptyState = $("emptyState");
  const statusBox = $("status");
  const reelsShell = $("reelsShell");
  const mediaInput = $("mediaInput");
  const fileDropLabel = $("fileDropLabel");
  const mediaNameHint = $("mediaNameHint");
  const audioInput = $("audioInput");

  const state = {
    x: 0.5,
    y: 0.45,
    wmX: 0.5,
    wmY: 0.88,
    fontScale: 1,
    selected: "text",
  };
  let mediaFile = null;
  let audioFile = null;
  let activePhraseIndex = 0;
  let phrases = [{ texto: "Sua frase aqui\nquebra de linha ok", emojis: [] }];
  let emojiCatalog = [];
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

  function emojiUrl(file) {
    return `${EMOJI_BASE}/${encodeURIComponent(file)}`;
  }

  function revokeUrls() {
    objectUrls.forEach((u) => URL.revokeObjectURL(u));
    objectUrls = [];
  }

  function isVideoFile(file) {
    return (file?.type || "").startsWith("video/");
  }

  function activePhrase() {
    return phrases[activePhraseIndex] || phrases[0];
  }

  function activePhraseText() {
    return (activePhrase()?.texto || "").trim();
  }

  function activeEmojis() {
    return Array.isArray(activePhrase()?.emojis) ? activePhrase().emojis : [];
  }

  function syncFitClass() {
    const cover = $("fitInput").value === "cover";
    reelsShell.classList.toggle("fit-contain", !cover);
  }

  function cssColor(value) {
    const v = (value || "white").trim();
    if (v.startsWith("0x")) return `#${v.slice(2)}`;
    return v;
  }

  function selectLayer(name) {
    state.selected = name;
    textLayer.classList.toggle("selected", name === "text");
    watermarkLayer.classList.toggle("selected", name === "watermark");
  }

  function updateTextLayer() {
    syncFitClass();
    const canvasH = canvas.clientHeight || 640;
    const fs = Math.round(canvasH * 0.045 * state.fontScale);
    const color = cssColor($("textColor").value || "white");
    const borderW = Number($("borderWidth").value || 2);
    const borderColor = cssColor($("borderColor").value || "black");

    textPreview.textContent = activePhraseText() || "Texto…";
    textPreview.style.fontSize = `${fs}px`;
    textPreview.style.color = color;
    textPreview.style.webkitTextStroke = borderW
      ? `${Math.max(1, borderW / 2)}px ${borderColor}`
      : "";
    textLayer.style.left = `${state.x * 100}%`;
    textLayer.style.top = `${state.y * 100}%`;
    textLayer.style.transform = "translate(-50%, -50%)";
    textLayer.hidden = false;

    emojiPreviewRow.innerHTML = activeEmojis()
      .map((file) => `<img src="${emojiUrl(file)}" alt="" draggable="false">`)
      .join("");

    const wmOn = $("watermarkEnabled").checked;
    watermarkLayer.hidden = !wmOn;
    if (wmOn) {
      watermarkPreview.textContent = ($("watermarkInput").value || "").trim() || "@marca";
      watermarkLayer.style.left = `${state.wmX * 100}%`;
      watermarkLayer.style.top = `${state.wmY * 100}%`;
      watermarkLayer.style.transform = "translate(-50%, -50%)";
    }

    $("fontScaleValue").textContent = `${Math.round(state.fontScale * 100)}%`;
    $("borderWidthValue").textContent = String(borderW);
    $("audioVolumeValue").textContent = `${$("audioVolume").value}%`;
  }

  function renderActiveEmojis() {
    const box = $("activeEmojis");
    if (!box) return;
    const emojis = activeEmojis();
    if (!emojis.length) {
      box.innerHTML = '<span class="muted" style="font-size:12px">Nenhum emoji selecionado</span>';
      return;
    }
    box.innerHTML = emojis
      .map(
        (file, idx) =>
          `<span class="emoji-chip"><img src="${emojiUrl(file)}" alt="" width="24" height="24">` +
          `<button type="button" data-rm-emoji="${idx}" title="Remover">×</button></span>`
      )
      .join("");
  }

  function renderEmojiPicker() {
    const box = $("emojiPicker");
    if (!box) return;
    box.innerHTML = emojiCatalog
      .map(
        (item) =>
          `<button type="button" data-add-emoji="${escapeHtml(item.file)}" title="${escapeHtml(item.label || item.char || item.file)}">` +
          `<img src="${emojiUrl(item.file)}" alt="${escapeHtml(item.char || "")}"></button>`
      )
      .join("");
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
      mediaNameHint.textContent = "";
      fileDropLabel.textContent = "Clique ou arraste 1 vídeo ou foto";
      return;
    }
    emptyState.style.display = "none";
    fileDropLabel.textContent = file.name;
    mediaNameHint.textContent = isVideoFile(file) ? "Vídeo carregado" : "Foto carregada";
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
    updateTextLayer();
  }

  function parsePhrasesFromTxt(raw) {
    const text = String(raw || "")
      .replace(/^\uFEFF/, "")
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n")
      .trim();
    if (!text) return [];
    const expand = (block) =>
      block.replace(/\\n/g, "\n").replace(/\/n/g, "\n").trim();
    const chunks = /\n\s*\n/.test(text)
      ? text.split(/\n\s*\n/)
      : text.split("\n");
    return chunks.map(expand).filter(Boolean).slice(0, 80);
  }

  function applyImportedPhrases(lines) {
    phrases = lines.map((texto) => ({ texto, emojis: [] }));
    activePhraseIndex = 0;
    $("phraseInput").value = phrases[0].texto;
    renderPhrasesList();
    renderActiveEmojis();
    updateTextLayer();
  }

  function renderPhrasesList() {
    const box = $("phrasesList");
    if (!box) return;
    if ($("phrasesCountHint")) {
      $("phrasesCountHint").textContent = `(${phrases.length})`;
    }
    if ($("exportHint")) {
      $("exportHint").textContent =
        phrases.length > 1
          ? `${phrases.length} frases · mesmo vídeo`
          : "Mesmo vídeo · uma frase por reel";
    }
    box.innerHTML = phrases
      .map((p, idx) => {
        const emCount = Array.isArray(p.emojis) && p.emojis.length ? ` · ${p.emojis.length} emoji(s)` : "";
        return (
          `<div class="reels-phrase-card${idx === activePhraseIndex ? " active" : ""}" data-phrase="${idx}">` +
          `<pre>${escapeHtml(p.texto || "")}${escapeHtml(emCount)}</pre>` +
          `<div class="reels-phrase-actions">` +
          `<button type="button" class="btn btn-sm" data-edit="${idx}">Editar</button>` +
          `<button type="button" class="btn btn-sm btn-danger" data-del="${idx}">Remover</button>` +
          `</div></div>`
        );
      })
      .join("");
  }

  function appendLayoutFields(form) {
    updateTextLayer();
    form.append("text", activePhraseText());
    form.append("emojis_json", JSON.stringify(activeEmojis()));
    form.append("x", String(state.x));
    form.append("y", String(state.y));
    form.append("watermark_x", String(state.wmX));
    form.append("watermark_y", String(state.wmY));
    form.append("font_scale", String(state.fontScale));
    form.append("text_color", $("textColor").value);
    form.append("border_color", $("borderColor").value);
    form.append("border_width", $("borderWidth").value);
    form.append("watermark_text", $("watermarkInput").value);
    form.append("watermark_enabled", $("watermarkEnabled").checked ? "true" : "false");
    form.append("fit_cover", $("fitInput").value === "cover" ? "true" : "false");
    form.append("photo_duration", $("photoDuration").value || "8");
    form.append("video_duration", $("videoDuration").value || "60");
    form.append("audio_mode", $("audioMode").value || "replace");
    form.append("audio_volume", String(Number($("audioVolume").value || 100) / 100));
    if (audioFile) form.append("audio", audioFile, audioFile.name);
  }

  function requireMedia() {
    if (!mediaFile) throw new Error("Escolha um vídeo ou foto de fundo.");
    return mediaFile;
  }

  function formForRenderOne() {
    if (!activePhraseText()) throw new Error("Digite o texto da frase.");
    const form = new FormData();
    form.append("media", requireMedia());
    form.append("filename", `reel_${activePhraseIndex + 1}.mp4`);
    appendLayoutFields(form);
    return form;
  }

  function formForBatch() {
    const valid = phrases.filter((p) => (p.texto || "").trim());
    if (!valid.length) throw new Error("Adicione frases com texto.");
    const form = new FormData();
    form.append("media", requireMedia());
    form.append(
      "phrases_json",
      JSON.stringify(valid.map((p) => ({ texto: p.texto, emojis: p.emojis || [] })))
    );
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

  async function loadEmojiCatalog() {
    try {
      const res = await fetch("/reels-editor/emojis/catalog");
      if (res.ok) emojiCatalog = await res.json();
    } catch {}
    renderEmojiPicker();
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
    selectLayer("text");
    const p = point(event);
    interaction = { type: "drag-text", dx: state.x - p.x, dy: state.y - p.y };
    textLayer.setPointerCapture(event.pointerId);
  });

  watermarkLayer.addEventListener("pointerdown", (event) => {
    selectLayer("watermark");
    const p = point(event);
    interaction = { type: "drag-wm", dx: state.wmX - p.x, dy: state.wmY - p.y };
    watermarkLayer.setPointerCapture(event.pointerId);
  });

  handle.addEventListener("pointerdown", (event) => {
    event.stopPropagation();
    selectLayer("text");
    const p = point(event);
    interaction = { type: "resize", startX: p.x, startScale: state.fontScale };
    handle.setPointerCapture(event.pointerId);
  });

  window.addEventListener("pointermove", (event) => {
    if (!interaction) return;
    const p = point(event);
    if (interaction.type === "drag-text") {
      state.x = clamp(0.05, p.x + interaction.dx, 0.95);
      state.y = clamp(0.08, p.y + interaction.dy, 0.82);
    } else if (interaction.type === "drag-wm") {
      state.wmX = clamp(0.08, p.x + interaction.dx, 0.92);
      state.wmY = clamp(0.1, p.y + interaction.dy, 0.96);
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
    const picked = mediaInput.files?.[0];
    if (!picked) return;
    mediaFile = picked;
    showPreviewFile(mediaFile);
  });

  audioInput.addEventListener("change", () => {
    audioFile = audioInput.files?.[0] || null;
    $("audioDropLabel").textContent = audioFile ? audioFile.name : "Opcional — clique para escolher";
    $("clearAudioBtn").hidden = !audioFile;
  });

  $("clearAudioBtn").addEventListener("click", () => {
    audioFile = null;
    audioInput.value = "";
    $("audioDropLabel").textContent = "Opcional — clique para escolher";
    $("clearAudioBtn").hidden = true;
  });

  $("phrasesTxtInput").addEventListener("change", async () => {
    const file = $("phrasesTxtInput").files?.[0];
    if (!file) return;
    try {
      const raw = await file.text();
      const lines = parsePhrasesFromTxt(raw);
      if (!lines.length) throw new Error("Nenhuma frase encontrada no .txt.");
      applyImportedPhrases(lines);
      $("phrasesTxtLabel").textContent = `${file.name} · ${lines.length} frase(s)`;
      setStatus(`${lines.length} frase(s) importadas.`, "ok");
    } catch (error) {
      setStatus(error.message || "Falha ao ler o .txt.", "error");
    } finally {
      $("phrasesTxtInput").value = "";
    }
  });

  $("emojiPicker").addEventListener("click", (event) => {
    const btn = event.target.closest("[data-add-emoji]");
    if (!btn) return;
    const file = btn.dataset.addEmoji;
    const list = activeEmojis();
    if (list.length >= 8) return;
    list.push(file);
    activePhrase().emojis = list;
    renderActiveEmojis();
    renderPhrasesList();
    updateTextLayer();
  });

  $("activeEmojis").addEventListener("click", (event) => {
    const btn = event.target.closest("[data-rm-emoji]");
    if (!btn) return;
    const idx = Number(btn.dataset.rmEmoji);
    const list = activeEmojis();
    list.splice(idx, 1);
    activePhrase().emojis = list;
    renderActiveEmojis();
    renderPhrasesList();
    updateTextLayer();
  });

  $("phraseInput").addEventListener("input", () => {
    activePhrase().texto = $("phraseInput").value;
    renderPhrasesList();
    updateTextLayer();
  });

  ["textColor", "borderColor"].forEach((id) => {
    $(id).addEventListener("change", updateTextLayer);
  });

  document.querySelectorAll(".reels-swatches").forEach((group) => {
    group.addEventListener("click", (event) => {
      const btn = event.target.closest("button[data-val]");
      if (!btn) return;
      const target = $(group.dataset.for);
      if (!target) return;
      target.value = btn.dataset.val;
      group.querySelectorAll("button").forEach((b) => b.classList.toggle("is-on", b === btn));
      target.dispatchEvent(new Event("change"));
    });
  });
  $("borderWidth").addEventListener("input", updateTextLayer);
  $("fontScale").addEventListener("input", (e) => {
    state.fontScale = Number(e.target.value) / 100;
    updateTextLayer();
  });
  $("fitInput").addEventListener("change", updateTextLayer);
  $("watermarkInput").addEventListener("input", updateTextLayer);
  $("watermarkEnabled").addEventListener("change", updateTextLayer);
  $("audioVolume").addEventListener("input", updateTextLayer);

  $("addPhraseBtn").addEventListener("click", () => {
    phrases.push({ texto: "Nova frase", emojis: [] });
    activePhraseIndex = phrases.length - 1;
    $("phraseInput").value = phrases[activePhraseIndex].texto;
    renderPhrasesList();
    renderActiveEmojis();
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
      renderActiveEmojis();
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
    renderActiveEmojis();
    updateTextLayer();
  });

  async function postAction(url, form, successMsg, download) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken() },
      body: form,
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
    if (download) await downloadBlob(blob, download);
    setStatus(successMsg, "ok");
    return blob;
  }

  $("renderOneButton").addEventListener("click", async () => {
    const btn = $("renderOneButton");
    btn.disabled = true;
    setStatus("Gerando reel…");
    try {
      await postAction(
        "/reels-editor/render",
        formForRenderOne(),
        "Reel baixado.",
        `reel_${activePhraseIndex + 1}.mp4`
      );
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      btn.disabled = false;
    }
  });

  $("renderBatchButton").addEventListener("click", async () => {
    const count = phrases.filter((p) => (p.texto || "").trim()).length;
    if (!confirm(`Gerar ${count} reel(s) com o mesmo vídeo? Pode demorar.`)) return;
    const btn = $("renderBatchButton");
    btn.disabled = true;
    setStatus("Gerando lote…");
    try {
      await postAction("/reels-editor/render-batch", formForBatch(), "ZIP baixado.", "reels_gerados.zip");
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      btn.disabled = false;
    }
  });

  window.addEventListener("resize", () => updateTextLayer());

  $("phraseInput").value = phrases[0].texto;
  selectLayer("text");
  renderPhrasesList();
  renderActiveEmojis();
  loadEmojiCatalog().then(updateTextLayer);
  updateTextLayer();

  if (window.lucide?.createIcons) window.lucide.createIcons();
})();
