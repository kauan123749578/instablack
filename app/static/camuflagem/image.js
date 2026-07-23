/** Camuflagem — imagem Canvas (filtro + capa + ruído → PNG) */

const MODE_CFG = {
  basica: { blend: 0.92, intensity: 3, hueAmp: 2 },
  normal: { blend: 0.9, intensity: 6, hueAmp: 4 },
  agressiva: { blend: 0.85, intensity: 12, hueAmp: 6 },
};

function clamp(v) {
  return Math.max(0, Math.min(255, v));
}

function randomSmall() {
  return (Math.random() * 0.06 - 0.03);
}

function loadImageFromFile(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error(`Falha ao ler ${file.name}`));
    };
    img.src = url;
  });
}

export function processImage(img, cover, index, blend, noiseLevel, mode) {
  const cfg = MODE_CFG[mode] || MODE_CFG.normal;
  const intensity = cfg.intensity;
  const w = img.naturalWidth;
  const h = img.naturalHeight;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });

  const hue = Math.floor(Math.random() * (cfg.hueAmp * 2 + 1)) - cfg.hueAmp;
  const brightness = 1 + randomSmall();
  const saturate = 1 + randomSmall();
  ctx.filter = `hue-rotate(${hue}deg) brightness(${brightness}) saturate(${saturate})`;
  ctx.drawImage(img, 0, 0, w, h);
  ctx.filter = "none";

  if (cover) {
    ctx.globalAlpha = 1 - (blend ?? cfg.blend);
    ctx.drawImage(cover, 0, 0, w, h);
    ctx.globalAlpha = 1;
  }

  const data = ctx.getImageData(0, 0, w, h);
  const step = intensity <= 3 ? 80 : intensity <= 6 ? 50 : 20;
  const amp = Math.max(1, Math.round((noiseLevel * intensity) / 6));

  for (let i = 0; i < data.data.length; i += 4 * step) {
    const d = () => ((Math.random() * amp * 2 - amp) | 0);
    data.data[i] = clamp(data.data[i] + d());
    data.data[i + 1] = clamp(data.data[i + 1] + d());
    data.data[i + 2] = clamp(data.data[i + 2] + d());
  }

  if (mode === "agressiva") {
    const delta = Math.max(2, Math.round(amp / 2));
    for (let by = 0; by < h; by += 8) {
      for (let bx = 0; bx < w; bx += 8) {
        const channel = (Math.random() * 3) | 0;
        const sign = Math.random() > 0.5 ? 1 : -1;
        for (let y = by; y < Math.min(by + 8, h); y++) {
          for (let x = bx; x < Math.min(bx + 8, w); x++) {
            const i = (y * w + x) * 4 + channel;
            data.data[i] = clamp(data.data[i] + sign * delta);
          }
        }
      }
    }
  }

  ctx.putImageData(data, 0, 0);
  return {
    dataUrl: canvas.toDataURL("image/png"),
    fileName: `camuflado_${index + 1}_${Date.now()}.png`,
    canvas,
  };
}

export { loadImageFromFile, MODE_CFG };

export async function processImagesBatch(files, coverFile, opts, onProgress) {
  const mode = opts.mode || "normal";
  const noise = opts.noiseLevel ?? 6;
  const blend = MODE_CFG[mode]?.blend ?? 0.9;
  const cover = coverFile ? await loadImageFromFile(coverFile) : null;
  const out = [];
  for (let i = 0; i < files.length; i++) {
    onProgress?.(i + 1, files.length, files[i].name);
    const img = await loadImageFromFile(files[i]);
    const result = processImage(img, cover, i, blend, noise, mode);
    const blob = await (await fetch(result.dataUrl)).blob();
    out.push({ blob, name: result.fileName, canvas: result.canvas, img });
  }
  return out;
}
