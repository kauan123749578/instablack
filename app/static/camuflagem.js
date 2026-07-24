/** Camuflagem — orquestração das abas (owner-only page) */

import { initTextTab } from "./camuflagem/text.js";
import {
  processImage,
  processImagesBatch,
  loadImageFromFile,
  MODE_CFG,
} from "./camuflagem/image.js";
import { encodeHotMp4 } from "./camuflagem/hot.js";
import { processVideoFile, adversarialNoise } from "./camuflagem/video.js";
import { stripMetadataBatch } from "./camuflagem/metadata.js";
import JSZip from "https://cdn.jsdelivr.net/npm/jszip@3.10.1/+esm";

function $(sel, root = document) {
  return root.querySelector(sel);
}

function downloadBlob(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

async function downloadMany(files) {
  if (files.length === 1) {
    downloadBlob(files[0].blob, files[0].name);
    return;
  }
  const zip = new JSZip();
  for (const f of files) zip.file(f.name, f.blob);
  const blob = await zip.generateAsync({ type: "blob" });
  downloadBlob(blob, `camuflagem_${Date.now()}.zip`);
}

function renderResults(container, files) {
  if (!container) return;
  container.innerHTML = "";
  files.forEach((f) => {
    const url = URL.createObjectURL(f.blob);
    const row = document.createElement("div");
    row.className = "camu-result-item";
    const isVid = (f.name || "").endsWith(".mp4") || (f.blob?.type || "").includes("video");
    const thumb = isVid
      ? `<video src="${url}" muted playsinline style="width:64px;height:64px;object-fit:cover;border-radius:8px"></video>`
      : f.blob?.type?.startsWith("image/")
        ? `<img src="${url}" alt="" style="width:64px;height:64px;object-fit:cover;border-radius:8px">`
        : "";
    row.innerHTML = `${thumb}<span style="flex:1">${f.name}</span><a href="${url}" download="${f.name}">Baixar</a>`;
    container.appendChild(row);
  });
}

function bindDrop(zone, input, { multiple = false, onChange } = {}) {
  if (!zone || !input) return;
  const open = () => input.click();
  zone.addEventListener("click", (e) => {
    if (e.target.closest("a,button")) return;
    open();
  });
  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.classList.add("is-drag");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("is-drag"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("is-drag");
    const files = [...(e.dataTransfer?.files || [])];
    if (!files.length) return;
    const dt = new DataTransfer();
    (multiple ? files : files.slice(0, 1)).forEach((f) => dt.items.add(f));
    input.files = dt.files;
    input.dispatchEvent(new Event("change"));
  });
  input.addEventListener("change", () => onChange?.(input.files));
}

function listFiles(el, files, maxShow = 12) {
  if (!el) return;
  const arr = [...(files || [])];
  el.innerHTML = arr
    .slice(0, maxShow)
    .map((f) => `<li>${f.name} <span class="muted">(${Math.round(f.size / 1024)} KB)</span></li>`)
    .join("");
  if (arr.length > maxShow) {
    el.innerHTML += `<li class="muted">+ ${arr.length - maxShow} arquivo(s)</li>`;
  }
}

function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

function setPreviewMedia(frameEl, emptyEl, node) {
  if (!frameEl) return;
  frameEl.querySelectorAll("img,video,canvas").forEach((n) => n.remove());
  if (!node) {
    if (emptyEl) emptyEl.hidden = false;
    return;
  }
  if (emptyEl) emptyEl.hidden = true;
  frameEl.appendChild(node);
}

function initTabs() {
  const tabs = document.querySelectorAll("[data-camu-tabs] .camu-tab");
  const panels = document.querySelectorAll(".camu-panel");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const id = tab.dataset.tab;
      tabs.forEach((t) => {
        t.classList.toggle("is-active", t === tab);
        t.setAttribute("aria-selected", t === tab ? "true" : "false");
      });
      panels.forEach((p) => {
        const on = p.dataset.panel === id;
        p.classList.toggle("is-active", on);
        p.hidden = !on;
      });
      window.lucide?.createIcons?.();
    });
  });
}

function initImageTab() {
  const mainInput = $("#camu-img-main");
  const coverInput = $("#camu-img-cover");
  const noise = $("#camu-noise");
  const noiseVal = $("#camu-noise-val");
  const opacityInput = $("#camu-img-opacity");
  const opacityVal = $("#camu-img-opacity-val");
  const runBtn = $("#camu-img-run");
  const progress = $("#camu-img-progress");
  const results = $("#camu-img-results");
  const previewFrame = $("#camu-img-preview");
  const previewEmpty = $("#camu-img-preview-empty");
  const previewMeta = $("#camu-img-preview-meta");
  let mode = "normal";
  let previewToken = 0;

  function coverOpacity() {
    return Math.max(0.01, Math.min(0.4, (Number(opacityInput?.value) || 10) / 100));
  }

  const refreshPreview = debounce(async () => {
    const file = mainInput?.files?.[0];
    if (!file) {
      setPreviewMedia(previewFrame, previewEmpty, null);
      if (previewMeta) previewMeta.textContent = "";
      return;
    }
    const token = ++previewToken;
    try {
      const img = await loadImageFromFile(file);
      const coverFile = coverInput?.files?.[0] || null;
      const cover = coverFile ? await loadImageFromFile(coverFile) : null;
      if (token !== previewToken) return;
      const noiseLevel = Number(noise?.value || 6);
      const blend = MODE_CFG[mode]?.blend ?? 0.9;
      const result = processImage(img, cover, 0, blend, noiseLevel, mode, coverOpacity());
      if (token !== previewToken) return;
      const imgEl = document.createElement("img");
      imgEl.alt = "Preview camuflado";
      imgEl.src = result.dataUrl;
      setPreviewMedia(previewFrame, previewEmpty, imgEl);
      if (previewMeta) {
        const niche = document.querySelector('input[name="niche"]:checked')?.value || "default";
        const pct = Math.round(coverOpacity() * 100);
        previewMeta.textContent =
          niche === "default"
            ? `Modo ${mode} · capa ${pct}% · ruído ${noiseLevel} · PNG`
            : `Modo ${mode} · capa ${pct}% · perfil ${niche.toUpperCase()} (MP4)`;
      }
    } catch (err) {
      console.error(err);
      if (previewMeta) previewMeta.textContent = err.message || String(err);
    }
  }, 180);

  document.querySelectorAll("[data-img-modes] .camu-mode").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-img-modes] .camu-mode").forEach((b) => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      mode = btn.dataset.mode || "normal";
      refreshPreview();
    });
  });

  document.querySelectorAll("[data-niche] .camu-niche-opt").forEach((lab) => {
    lab.addEventListener("click", () => {
      document.querySelectorAll("[data-niche] .camu-niche-opt").forEach((l) => l.classList.remove("is-active"));
      lab.classList.add("is-active");
      refreshPreview();
    });
  });

  noise?.addEventListener("input", () => {
    if (noiseVal) noiseVal.textContent = noise.value;
    refreshPreview();
  });
  opacityInput?.addEventListener("input", () => {
    if (opacityVal) opacityVal.textContent = `${opacityInput.value}%`;
    refreshPreview();
  });

  const syncBtn = () => {
    if (runBtn) runBtn.disabled = !(mainInput?.files?.length);
  };

  bindDrop($('[data-drop="img-main"]'), mainInput, {
    multiple: true,
    onChange: (files) => {
      const capped = [...files].slice(0, 50);
      if (files.length > 50 && mainInput) {
        const dt = new DataTransfer();
        capped.forEach((f) => dt.items.add(f));
        mainInput.files = dt.files;
      }
      listFiles($("#camu-img-main-list"), mainInput.files);
      syncBtn();
      refreshPreview();
    },
  });
  bindDrop($('[data-drop="img-cover"]'), coverInput, {
    onChange: () => {
      listFiles($("#camu-img-cover-list"), coverInput.files);
      refreshPreview();
    },
  });

  runBtn?.addEventListener("click", async () => {
    const files = [...(mainInput?.files || [])];
    if (!files.length) return;
    const coverFile = coverInput?.files?.[0] || null;
    const niche = document.querySelector('input[name="niche"]:checked')?.value || "default";
    const noiseLevel = Number(noise?.value || 6);
    runBtn.disabled = true;
    results.innerHTML = "";
    try {
      if (niche === "default") {
        const out = await processImagesBatch(
          files,
          coverFile,
          { mode, noiseLevel, coverOpacity: coverOpacity() },
          (i, n, name) => {
            if (progress) progress.textContent = `Imagem ${i}/${n}: ${name}`;
          }
        );
        const mapped = out.map((o) => ({ blob: o.blob, name: o.name }));
        renderResults(results, mapped);
        await downloadMany(mapped);
        if (progress) progress.textContent = `${mapped.length} PNG gerado(s).`;
      } else {
        const cover = coverFile
          ? await loadImageFromFile(coverFile)
          : await loadImageFromFile(files[0]);
        const out = [];
        for (let i = 0; i < files.length; i++) {
          if (progress) progress.textContent = `HOT/Outro ${i + 1}/${files.length}…`;
          const processed = await processImagesBatch(
            [files[i]],
            coverFile,
            { mode, noiseLevel, coverOpacity: coverOpacity() },
            () => {}
          );
          const mainImg = processed[0].img;
          const blob = await encodeHotMp4(mainImg, cover, niche, (p, msg) => {
            if (progress) progress.textContent = `${msg} (${Math.round(p * 100)}%)`;
          });
          out.push({ blob, name: `camuflado_${niche}_${i + 1}_${Date.now()}.mp4` });
        }
        renderResults(results, out);
        await downloadMany(out);
        if (progress) progress.textContent = `${out.length} MP4 gerado(s). Cauda longa pode demorar.`;
      }
    } catch (err) {
      console.error(err);
      if (progress) progress.textContent = err.message || String(err);
    } finally {
      syncBtn();
      window.lucide?.createIcons?.();
    }
  });
}

function initVideoTab() {
  const mainInput = $("#camu-vid-main");
  const coverInput = $("#camu-vid-cover");
  const runBtn = $("#camu-vid-run");
  const progress = $("#camu-vid-progress");
  const results = $("#camu-vid-results");
  const hint = $("#camu-vid-hint");
  const opacityInput = $("#camu-vid-opacity");
  const opacityVal = $("#camu-vid-opacity-val");
  const previewFrame = $("#camu-vid-preview");
  const previewEmpty = $("#camu-vid-preview-empty");
  const previewMeta = $("#camu-vid-preview-meta");
  let mode = "normal";
  let previewToken = 0;
  let previewUrl = null;

  function coverOpacity() {
    return Math.max(0.01, Math.min(0.4, (Number(opacityInput?.value) || 10) / 100));
  }

  const refreshPreview = debounce(async () => {
    const videoFile = mainInput?.files?.[0];
    const coverFile = coverInput?.files?.[0];
    if (!videoFile || !coverFile) {
      setPreviewMedia(previewFrame, previewEmpty, null);
      if (previewMeta) previewMeta.textContent = "";
      return;
    }
    const token = ++previewToken;
    try {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(videoFile);
      const video = document.createElement("video");
      video.muted = true;
      video.playsInline = true;
      video.preload = "auto";
      video.src = previewUrl;
      await new Promise((resolve, reject) => {
        video.onloadeddata = () => resolve();
        video.onerror = () => reject(new Error("Falha ao ler vídeo"));
      });
      video.currentTime = Math.min(0.2, (video.duration || 1) * 0.05);
      await new Promise((resolve) => {
        video.onseeked = () => resolve();
      });
      if (token !== previewToken) return;

      const coverImg = await loadImageFromFile(coverFile);
      if (token !== previewToken) return;

      const w = 540;
      const h = 960;
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });

      // cover-fit do vídeo no 9:16
      const vw = video.videoWidth || w;
      const vh = video.videoHeight || h;
      const vScale = Math.max(w / vw, h / vh);
      const dw = vw * vScale;
      const dh = vh * vScale;
      ctx.drawImage(video, (w - dw) / 2, (h - dh) / 2, dw, dh);

      const alpha = coverOpacity();
      ctx.globalAlpha = alpha;
      const cw = coverImg.naturalWidth || coverImg.width;
      const ch = coverImg.naturalHeight || coverImg.height;
      const cScale = Math.max(w / cw, h / ch);
      const cdw = cw * cScale;
      const cdh = ch * cScale;
      ctx.drawImage(coverImg, (w - cdw) / 2, (h - cdh) / 2, cdw, cdh);
      ctx.globalAlpha = 1;
      if (mode === "agressiva") {
        adversarialNoise(ctx, 4);
      }

      const img = document.createElement("img");
      img.alt = "Preview frame camuflado";
      img.src = canvas.toDataURL("image/jpeg", 0.92);
      setPreviewMedia(previewFrame, previewEmpty, img);
      if (previewMeta) {
        const pct = Math.round(alpha * 100);
        previewMeta.textContent =
          mode === "agressiva"
            ? `Agressiva · capa ${pct}% + ruído (1º frame)`
            : `Normal · capa ${pct}% (1º frame)`;
      }
    } catch (err) {
      console.error(err);
      if (previewMeta) previewMeta.textContent = err.message || String(err);
    }
  }, 220);

  document.querySelectorAll("[data-vid-modes] .camu-mode").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-vid-modes] .camu-mode").forEach((b) => {
        b.classList.remove("is-active", "is-hot");
      });
      btn.classList.add("is-active");
      mode = btn.dataset.vmode || "normal";
      if (mode === "agressiva") btn.classList.add("is-hot");
      if (hint) {
        hint.classList.toggle("is-aggr", mode === "agressiva");
        hint.textContent =
          mode === "agressiva"
            ? "Aplica distorção por frame, ruído anti-IA e flashes. Mais lento."
            : "Mistura a imagem de camuflagem em cada frame. Ajuste a opacidade abaixo.";
      }
      refreshPreview();
    });
  });

  opacityInput?.addEventListener("input", () => {
    if (opacityVal) opacityVal.textContent = `${opacityInput.value}%`;
    refreshPreview();
  });

  const syncBtn = () => {
    if (runBtn) runBtn.disabled = !(mainInput?.files?.length && coverInput?.files?.length);
  };

  bindDrop($('[data-drop="vid-main"]'), mainInput, {
    multiple: true,
    onChange: () => {
      const capped = [...(mainInput.files || [])].slice(0, 20);
      const dt = new DataTransfer();
      capped.forEach((f) => {
        if (f.size <= 50 * 1024 * 1024) dt.items.add(f);
      });
      mainInput.files = dt.files;
      listFiles($("#camu-vid-main-list"), mainInput.files);
      syncBtn();
      refreshPreview();
    },
  });
  bindDrop($('[data-drop="vid-cover"]'), coverInput, {
    onChange: () => {
      listFiles($("#camu-vid-cover-list"), coverInput.files);
      syncBtn();
      refreshPreview();
    },
  });

  runBtn?.addEventListener("click", async () => {
    const videos = [...(mainInput?.files || [])];
    const coverFile = coverInput?.files?.[0];
    if (!videos.length || !coverFile) return;
    runBtn.disabled = true;
    results.innerHTML = "";
    try {
      const coverImg = await loadImageFromFile(coverFile);
      const out = [];
      for (let i = 0; i < videos.length; i++) {
        const blob = await processVideoFile(
          videos[i],
          coverImg,
          mode,
          (p, msg) => {
            if (progress) {
              progress.textContent = `Vídeo ${i + 1}/${videos.length}: ${msg} (${Math.round(p * 100)}%)`;
            }
          },
          coverOpacity()
        );
        out.push({
          blob,
          name: `camuflado_vid_${mode}_${i + 1}_${Date.now()}.mp4`,
        });
      }
      renderResults(results, out);
      await downloadMany(out);
      if (progress) progress.textContent = `${out.length} vídeo(s) camuflado(s).`;
    } catch (err) {
      console.error(err);
      if (progress) progress.textContent = err.message || String(err);
    } finally {
      syncBtn();
    }
  });
}

function initMetaTab() {
  const input = $("#camu-meta-files");
  const runBtn = $("#camu-meta-run");
  const progress = $("#camu-meta-progress");
  const results = $("#camu-meta-results");

  const syncBtn = () => {
    if (runBtn) runBtn.disabled = !(input?.files?.length);
  };

  bindDrop($('[data-drop="meta-files"]'), input, {
    multiple: true,
    onChange: () => {
      listFiles($("#camu-meta-list"), input.files);
      syncBtn();
    },
  });

  runBtn?.addEventListener("click", async () => {
    const files = [...(input?.files || [])];
    if (!files.length) return;
    runBtn.disabled = true;
    results.innerHTML = "";
    try {
      const out = await stripMetadataBatch(files, (p, msg) => {
        if (progress) progress.textContent = `${msg} (${Math.round(p * 100)}%)`;
      });
      renderResults(results, out);
      await downloadMany(out);
      if (progress) progress.textContent = `${out.length} arquivo(s) limpo(s).`;
    } catch (err) {
      console.error(err);
      if (progress) progress.textContent = err.message || String(err);
    } finally {
      syncBtn();
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initTextTab();
  initImageTab();
  initVideoTab();
  initMetaTab();
  window.lucide?.createIcons?.();
});
