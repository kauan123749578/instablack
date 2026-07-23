/** Camuflagem — vídeo Normal / Agressiva (overlay capa + WebCodecs) */

import { Muxer, ArrayBufferTarget } from "https://cdn.jsdelivr.net/npm/mp4-muxer@5.1.3/+esm";

const FLASH_COLORS = ["#000000", "#ff0000", "#00ff00", "#0000ff", "#808080", "#5c3317"];

function clamp(v) {
  return Math.max(0, Math.min(255, v | 0));
}

export function adversarialNoise(ctx, epsilon = 4) {
  const { width, height } = ctx.canvas;
  const img = ctx.getImageData(0, 0, width, height);
  let seed = (performance.now() * 1000) | 0 | 1;
  const next = () => {
    seed ^= seed << 13;
    seed ^= seed >> 17;
    seed ^= seed << 5;
    seed = seed >>> 0;
    return seed;
  };
  for (let i = 0; i < img.data.length; i += 4) {
    const dr = (next() % 3) - 1;
    const dg = (next() % 3) - 1;
    const db = (next() % 3) - 1;
    img.data[i] = clamp(img.data[i] + dr * epsilon);
    img.data[i + 1] = clamp(img.data[i + 1] + dg * epsilon);
    img.data[i + 2] = clamp(img.data[i + 2] + db * epsilon);
  }
  ctx.putImageData(img, 0, 0);
}

function loadVideo(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";
    video.src = url;
    video.onloadedmetadata = () => resolve({ video, url });
    video.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error(`Falha ao ler vídeo ${file.name}`));
    };
  });
}

function seek(video, t) {
  return new Promise((resolve) => {
    const onSeeked = () => {
      video.removeEventListener("seeked", onSeeked);
      resolve();
    };
    video.addEventListener("seeked", onSeeked);
    video.currentTime = Math.min(t, Math.max(0, video.duration - 0.05));
  });
}

export async function processVideoFile(file, coverImg, mode, onProgress) {
  if (typeof VideoEncoder === "undefined") {
    throw new Error("WebCodecs indisponível — use Chrome ou Edge.");
  }
  const { video, url } = await loadVideo(file);
  const width = video.videoWidth || 1080;
  const height = video.videoHeight || 1920;
  const duration = video.duration || 1;
  const fps = 15;
  const frameCount = Math.min(Math.ceil(duration * fps), fps * 60 * 5); // cap 5 min

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });

  const target = new ArrayBufferTarget();
  const muxer = new Muxer({
    target,
    video: { codec: "avc", width, height },
    fastStart: "in-memory",
  });

  let encoderError = null;
  const encoder = new VideoEncoder({
    output: (chunk, meta) => muxer.addVideoChunk(chunk, meta),
    error: (e) => {
      encoderError = e;
    },
  });

  encoder.configure({
    codec: "avc1.42001f",
    width,
    height,
    bitrate: 2_000_000,
    framerate: fps,
  });

  const frameDurUs = Math.round(1_000_000 / fps);
  let nextFlashAt = mode === "agressiva" ? Math.random() * 2 + 0.5 : Infinity;
  let flashLeft = 0;

  for (let i = 0; i < frameCount; i++) {
    if (encoderError) throw encoderError;
    const t = i / fps;
    await seek(video, t);
    ctx.drawImage(video, 0, 0, width, height);

    ctx.globalAlpha = 0.1;
    ctx.drawImage(coverImg, 0, 0, width, height);
    ctx.globalAlpha = 1;

    if (mode === "agressiva") {
      adversarialNoise(ctx, 4);
      if (flashLeft > 0) {
        ctx.globalAlpha = 0.35;
        ctx.fillStyle = FLASH_COLORS[(Math.random() * FLASH_COLORS.length) | 0];
        ctx.fillRect(0, 0, width, height);
        ctx.globalAlpha = 1;
        flashLeft -= 1;
      } else if (t >= nextFlashAt) {
        flashLeft = 1 + ((Math.random() * 2) | 0);
        nextFlashAt = t + 1.5 + Math.random() * 3;
      }
    }

    const frame = new VideoFrame(canvas, {
      timestamp: i * frameDurUs,
      duration: frameDurUs,
    });
    encoder.encode(frame, { keyFrame: i % 30 === 0 });
    frame.close();

    if (encoder.encodeQueueSize > 6) {
      await encoder.flush();
    }
    if (i % 5 === 0) onProgress?.(i / frameCount, `Frame ${i + 1}/${frameCount}`);
  }

  await encoder.flush();
  encoder.close();
  muxer.finalize();
  URL.revokeObjectURL(url);
  onProgress?.(1, "Pronto");
  return new Blob([target.buffer], { type: "video/mp4" });
}
