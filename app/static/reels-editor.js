(() => {
  const EMOJI_BASE = "/static/reels-emojis";
  const SLOT_COUNT = 2;
  const $ = (id) => document.getElementById(id);
  const statusBox = $("status");
  const reelsShell = $("reelsShell");

  const slots = Array.from({ length: SLOT_COUNT }, (_, i) => ({
    index: i,
    wrap: $(`phoneWrap${i}`),
    canvas: $(`canvas${i}`),
    video: $(`reelVideo${i}`),
    image: $(`reelImage${i}`),
    empty: $(`emptyState${i}`),
    textLayer: $(`textLayer${i}`),
    textPreview: $(`textPreview${i}`),
    emojiRow: $(`emojiPreviewRow${i}`),
    watermarkLayer: $(`watermarkLayer${i}`),
    watermarkPreview: $(`watermarkPreview${i}`),
    handle: $(`resizeHandle${i}`),
    mediaInput: $(`mediaInput${i}`),
    fileDropLabel: $(`fileDropLabel${i}`),
  }));

  const state = {
    wmX: 0.5,
    wmY: 0.88,
    selected: "text",
    activeSlot: 0,
  };
  const mediaFiles = [null, null];
  const objectUrls = [[], []];
  let audioFile = null;
  let activePhraseIndex = 0;
  let phrases = [{ texto: "Sua frase aqui\nquebra de linha ok", emojis: [] }];
  let emojiCatalog = [];
  let interaction = null;

  function defaultSlotLayout(texto = "") {
    return { texto, emojis: [], x: 0.5, y: 0.5, fontScale: 1 };
  }

  const slotLayouts = [
    defaultSlotLayout("Sua frase aqui\nquebra de linha ok"),
    defaultSlotLayout("Texto do reel B"),
  ];

  function activeLayout() {
    return slotLayouts[state.activeSlot] || slotLayouts[0];
  }

  function slotLayout(slot) {
    return slotLayouts[slot] || slotLayouts[0];
  }

  function syncActiveSlotFromUI() {
    const layout = activeLayout();
    layout.texto = $("phraseInput")?.value || layout.texto || "";
  }

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

  function revokeSlotUrls(slot) {
    objectUrls[slot].forEach((u) => URL.revokeObjectURL(u));
    objectUrls[slot] = [];
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
    return Array.isArray(activeLayout().emojis) ? activeLayout().emojis : [];
  }

  function slotText(slot) {
    return (slotLayout(slot).texto || "").trim();
  }

  function loadedMediaCount() {
    return mediaFiles.filter(Boolean).length;
  }

  function slotLabel(i) {
    return i === 0 ? "A" : "B";
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

  function setActiveSlot(index) {
    syncActiveSlotFromUI();
    state.activeSlot = index;
    const layout = activeLayout();
    if ($("phraseInput")) $("phraseInput").value = layout.texto || "";
    if ($("fontScale")) $("fontScale").value = Math.round((layout.fontScale || 1) * 100);
    slots.forEach((s, i) => {
      s.wrap.classList.toggle("is-active", i === index);
      s.textLayer.classList.toggle("selected", i === index && state.selected === "text");
      s.watermarkLayer.classList.toggle("selected", i === index && state.selected === "watermark");
    });
    $("renderOneButton").textContent = `Gerar ${slotLabel(index)}`;
    renderActiveEmojis();
    updateVariationsButton();
    updateTextLayer();
  }

  function selectLayer(name) {
    state.selected = name;
    slots.forEach((s, i) => {
      s.textLayer.classList.toggle("selected", i === state.activeSlot && name === "text");
      s.watermarkLayer.classList.toggle("selected", i === state.activeSlot && name === "watermark");
    });
  }

  function updateVariationsButton() {
    const btn = $("renderVariationsButton");
    const count = loadedMediaCount();
    btn.disabled = count === 0;
    btn.textContent = count >= 2 ? "Gerar A+B" : "Gerar A+B (precisa 2)";
  }

  function autoFontSize(text, canvasH, fontScale = 1) {
    const lines = String(text || "")
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);
    if (!lines.length) return Math.round(canvasH * 0.045);
    const maxLen = Math.max(...lines.map((l) => l.length));
    const numLines = lines.length;
    const byWidth = Math.floor((canvasH * 0.9 * (9 / 16)) / Math.max(maxLen, 1) * 1.2);
    const byHeight = Math.floor((canvasH * 0.6) / (numLines * 1.5));
    const base = Math.max(30, Math.min(byWidth, byHeight, 80));
    return Math.round(base * Math.max(0.35, Math.min(2.5, fontScale || 1)));
  }

  function updateTextLayer() {
    syncFitClass();
    const refCanvas = slots[state.activeSlot].canvas;
    const canvasH = refCanvas?.clientHeight || 640;
    const color = cssColor($("textColor").value || "yellow");
    const borderW = Number($("borderWidth").value || 2);
    const borderColor = cssColor($("borderColor").value || "black");
    const wmOn = $("watermarkEnabled").checked;
    const wmText = ($("watermarkInput").value || "").trim() || "@marca";

    slots.forEach((s, i) => {
      const layout = slotLayout(i);
      const text = (layout.texto || "").trim() || (i === state.activeSlot ? "Texto…" : "");
      const fs = autoFontSize(text, canvasH, layout.fontScale || 1);
      const emojiHtml = (layout.emojis || [])
        .map((file) => `<img src="${emojiUrl(file)}" alt="" draggable="false">`)
        .join("");
      s.textPreview.textContent = text;
      s.textPreview.style.fontSize = `${fs}px`;
      s.textPreview.style.color = color;
      s.textPreview.style.webkitTextStroke = borderW
        ? `${Math.max(1, borderW / 2)}px ${borderColor}`
        : "";
      s.textLayer.style.left = `${layout.x * 100}%`;
      s.textLayer.style.top = `${layout.y * 100}%`;
      s.textLayer.style.transform = "translate(-50%, -50%)";
      s.textLayer.hidden = !text && !emojiHtml;
      s.emojiRow.innerHTML = emojiHtml;
      s.emojiRow.hidden = !emojiHtml;
      s.emojiRow.style.marginTop = `${Math.round(fs * 0.35)}px`;

      s.watermarkLayer.hidden = !wmOn;
      if (wmOn) {
        s.watermarkPreview.textContent = wmText;
        const useAutoWm = Math.abs(state.wmY - 0.88) < 0.02;
        if (useAutoWm && emojiHtml) {
          s.watermarkLayer.style.left = "50%";
          s.watermarkLayer.style.top = `${Math.min(92, layout.y * 100 + 18)}%`;
        } else {
          s.watermarkLayer.style.left = `${state.wmX * 100}%`;
          s.watermarkLayer.style.top = `${state.wmY * 100}%`;
        }
        s.watermarkLayer.style.transform = "translate(-50%, -50%)";
      }
    });

    const activeFs = activeLayout().fontScale || 1;
    $("fontScaleValue").textContent = `${Math.round(activeFs * 100)}%`;
    $("borderWidthValue").textContent = String(borderW);
    $("audioVolumeValue").textContent = `${$("audioVolume").value}%`;
    updateVariationsButton();
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

  function point(event, canvas) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) / rect.width,
      y: (event.clientY - rect.top) / rect.height,
    };
  }

  function showPreviewFile(slot, file) {
    const s = slots[slot];
    revokeSlotUrls(slot);
    s.video.hidden = true;
    s.image.hidden = true;
    mediaFiles[slot] = file || null;
    if (!file) {
      s.empty.style.display = "grid";
      s.fileDropLabel.textContent = "Clique ou solte";
      updateMediaHint();
      updateVariationsButton();
      return;
    }
    s.empty.style.display = "none";
    s.fileDropLabel.textContent = file.name.length > 18 ? `${file.name.slice(0, 15)}…` : file.name;
    const url = URL.createObjectURL(file);
    objectUrls[slot].push(url);
    if (isVideoFile(file)) {
      s.video.src = url;
      s.video.hidden = false;
      s.video.play().catch(() => {});
    } else {
      s.image.src = url;
      s.image.hidden = false;
    }
    updateMediaHint();
    updateTextLayer();
  }

  function updateMediaHint() {
    const parts = mediaFiles
      .map((f, i) => (f ? `${slotLabel(i)}: ${f.name}` : null))
      .filter(Boolean);
    $("mediaNameHint").textContent = parts.length
      ? parts.join(" · ")
      : "Ou arraste direto no preview A ou B";
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
    box.innerHTML = phrases
      .map((p, idx) => {
        const emCount =
          Array.isArray(p.emojis) && p.emojis.length ? ` · ${p.emojis.length} emoji(s)` : "";
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

  function appendLayoutFields(form, slotIndex, prefix = "") {
    const layout = slotLayout(slotIndex);
    const p = prefix ? `${prefix}_` : "";
    form.append(`${p}text`, (layout.texto || "").trim());
    form.append(`${p}emojis_json`, JSON.stringify(layout.emojis || []));
    form.append(`${p}x`, String(layout.x));
    form.append(`${p}y`, String(layout.y));
    form.append(`${p}font_scale`, String(layout.fontScale || 1));
    if (!prefix) {
      form.append("watermark_x", String(state.wmX));
      form.append("watermark_y", String(state.wmY));
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
  }

  function formForRenderOne() {
    syncActiveSlotFromUI();
    if (!slotText(state.activeSlot)) throw new Error("Digite o texto da frase.");
    const slot = state.activeSlot;
    const form = new FormData();
    form.append("media", requireMedia(slot));
    form.append("filename", `reel_${slotLabel(slot).toLowerCase()}.mp4`);
    appendLayoutFields(form, slot);
    return form;
  }

  function formForVariations() {
    syncActiveSlotFromUI();
    if (!slotText(0)) throw new Error("Digite o texto do reel A.");
    if (!mediaFiles[0]) throw new Error("Carregue o vídeo A.");
    const form = new FormData();
    form.append("media_a", requireMedia(0));
    if (mediaFiles[1]) form.append("media_b", mediaFiles[1], mediaFiles[1].name);
    appendLayoutFields(form, 0);
    if (mediaFiles[1]) appendLayoutFields(form, 1, "b");
    return form;
  }

  function requireMedia(slot) {
    const file = mediaFiles[slot];
    if (!file) throw new Error(`Escolha o vídeo ${slotLabel(slot)}.`);
    return file;
  }

  function formForBatch() {
    const valid = phrases.filter((p) => (p.texto || "").trim());
    if (!valid.length) throw new Error("Adicione frases com texto.");
    const firstSlot = mediaFiles.findIndex(Boolean);
    if (firstSlot < 0) throw new Error("Carregue pelo menos um vídeo.");
    const form = new FormData();
    form.append("media", requireMedia(firstSlot));
    if (mediaFiles[0] && mediaFiles[1]) {
      form.append("media_b", mediaFiles[1], mediaFiles[1].name);
    }
    form.append(
      "phrases_json",
      JSON.stringify(valid.map((p) => ({ texto: p.texto, emojis: p.emojis || [] })))
    );
    appendLayoutFields(form, 0);
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

  slots.forEach((s) => {
    s.wrap.addEventListener("click", (event) => {
      if (event.target.closest(".handle")) return;
      setActiveSlot(s.index);
    });

    s.textLayer.addEventListener("pointerdown", (event) => {
      if (event.target === s.handle) return;
      setActiveSlot(s.index);
      selectLayer("text");
      const p = point(event, s.canvas);
      const layout = activeLayout();
      interaction = { type: "drag-text", dx: layout.x - p.x, dy: layout.y - p.y, canvas: s.canvas };
      s.textLayer.setPointerCapture(event.pointerId);
    });

    s.watermarkLayer.addEventListener("pointerdown", (event) => {
      setActiveSlot(s.index);
      selectLayer("watermark");
      const p = point(event, s.canvas);
      interaction = { type: "drag-wm", dx: state.wmX - p.x, dy: state.wmY - p.y, canvas: s.canvas };
      s.watermarkLayer.setPointerCapture(event.pointerId);
    });

    s.handle.addEventListener("pointerdown", (event) => {
      event.stopPropagation();
      setActiveSlot(s.index);
      selectLayer("text");
      const p = point(event, s.canvas);
      const layout = activeLayout();
      interaction = { type: "resize", startX: p.x, startScale: layout.fontScale, canvas: s.canvas };
      s.handle.setPointerCapture(event.pointerId);
    });

    s.mediaInput.addEventListener("change", () => {
      const picked = s.mediaInput.files?.[0];
      if (!picked) return;
      showPreviewFile(s.index, picked);
      setActiveSlot(s.index);
    });

    s.canvas.addEventListener("dragover", (event) => {
      event.preventDefault();
      s.wrap.classList.add("is-dragover");
    });
    s.canvas.addEventListener("dragleave", () => {
      s.wrap.classList.remove("is-dragover");
    });
    s.canvas.addEventListener("drop", (event) => {
      event.preventDefault();
      s.wrap.classList.remove("is-dragover");
      const file = event.dataTransfer?.files?.[0];
      if (!file) return;
      showPreviewFile(s.index, file);
      setActiveSlot(s.index);
    });
  });

  window.addEventListener("pointermove", (event) => {
    if (!interaction) return;
    const p = point(event, interaction.canvas);
    const layout = activeLayout();
    if (interaction.type === "drag-text") {
      layout.x = clamp(0.05, p.x + interaction.dx, 0.95);
      layout.y = clamp(0.08, p.y + interaction.dy, 0.82);
    } else if (interaction.type === "drag-wm") {
      state.wmX = clamp(0.08, p.x + interaction.dx, 0.92);
      state.wmY = clamp(0.1, p.y + interaction.dy, 0.96);
    } else {
      const delta = (p.x - interaction.startX) * 2;
      layout.fontScale = clamp(0.5, interaction.startScale + delta, 2);
      $("fontScale").value = Math.round(layout.fontScale * 100);
    }
    updateTextLayer();
  });
  window.addEventListener("pointerup", () => {
    interaction = null;
  });

  $("audioInput").addEventListener("change", () => {
    audioFile = $("audioInput").files?.[0] || null;
    $("audioDropLabel").textContent = audioFile ? audioFile.name : "MP3 / M4A";
    $("clearAudioBtn").hidden = !audioFile;
  });

  $("clearAudioBtn").addEventListener("click", () => {
    audioFile = null;
    $("audioInput").value = "";
    $("audioDropLabel").textContent = "MP3 / M4A";
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
    const list = activeLayout().emojis || [];
    if (list.length >= 8) return;
    list.push(file);
    activeLayout().emojis = list;
    renderActiveEmojis();
    renderPhrasesList();
    updateTextLayer();
  });

  $("activeEmojis").addEventListener("click", (event) => {
    const btn = event.target.closest("[data-rm-emoji]");
    if (!btn) return;
    const idx = Number(btn.dataset.rmEmoji);
    const list = activeLayout().emojis || [];
    list.splice(idx, 1);
    activeLayout().emojis = list;
    renderActiveEmojis();
    renderPhrasesList();
    updateTextLayer();
  });

  $("phraseInput").addEventListener("input", () => {
    activeLayout().texto = $("phraseInput").value;
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
    activeLayout().fontScale = Number(e.target.value) / 100;
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
    activeLayout().texto = phrases[idx].texto;
    activeLayout().emojis = [...(phrases[idx].emojis || [])];
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
    setStatus(`Gerando reel ${slotLabel(state.activeSlot)}…`);
    try {
      await postAction(
        "/reels-editor/render",
        formForRenderOne(),
        "Reel baixado.",
        `reel_${slotLabel(state.activeSlot).toLowerCase()}.mp4`
      );
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      btn.disabled = false;
    }
  });

  $("renderVariationsButton").addEventListener("click", async () => {
    const btn = $("renderVariationsButton");
    btn.disabled = true;
    const dual = loadedMediaCount() >= 2;
    setStatus(dual ? "Gerando A + B…" : "Gerando reel A…");
    try {
      const dl = dual ? "reels_variacoes.zip" : "reel_a.mp4";
      await postAction(
        "/reels-editor/render-variations",
        formForVariations(),
        dual ? "Variações baixadas (ZIP)." : "Reel A baixado.",
        dl
      );
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      btn.disabled = false;
      updateVariationsButton();
    }
  });

  $("renderBatchButton").addEventListener("click", async () => {
    const valid = phrases.filter((p) => (p.texto || "").trim()).length;
    const medias = loadedMediaCount();
    const total = valid * medias;
    if (!confirm(`Gerar ${total} reel(s) (${valid} frase(s) × ${medias} vídeo(s))? Pode demorar.`)) return;
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

  $("phraseInput").value = slotLayouts[0].texto;
  setActiveSlot(0);
  selectLayer("text");
  renderPhrasesList();
  renderActiveEmojis();
  loadEmojiCatalog().then(updateTextLayer);
  updateTextLayer();

  if (window.lucide?.createIcons) window.lucide.createIcons();
})();
