/** Camuflagem — perfil HOT / Outro → MP4 via WebCodecs + mp4-muxer */

import { Muxer, ArrayBufferTarget } from "https://cdn.jsdelivr.net/npm/mp4-muxer@5.1.3/+esm";

const WIDTH = 1080;
const HEIGHT = 1080;
const FPS = 15;
const BITRATE = 1_200_000;

function randomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

const MOTIONS = ["zoom_in", "zoom_out", "pan_h", "pan_v", "ken_burns"];

export function getProfile(profile) {
  switch (profile) {
    case "hot":
      return {
        profile: "hot",
        introFrames: 1,
        mainDurationSec: 0,
        tailDurationSec: randomInt(240, 360),
        middleWhiteDurationSec: 0,
        flashFrames: 0,
        flashCount: 0,
        enableWhiteFlashes: false,
        enableMiddleWhiteFrame: false,
      };
    case "other":
      return {
        profile: "other",
        introFrames: randomInt(2, 5),
        mainDurationSec: randomInt(8, 15),
        tailDurationSec: randomInt(240, 360),
        middleWhiteDurationSec: randomInt(1, 3),
        flashFrames: randomInt(2, 5),
        flashCount: randomInt(2, 4),
        enableWhiteFlashes: true,
        enableMiddleWhiteFrame: true,
      };
    default:
      return { profile: "default" };
  }
}

/** Timeline simplificada do guia (HOT) / variação Outro */
export function buildTimeline(profile) {
  const cfg = getProfile(profile);
  const segs = [];

  if (profile === "other") {
    for (let i = 0; i < cfg.introFrames; i++) {
      segs.push({ kind: "white", durationSec: 0.2 });
    }
    segs.push({ kind: "image", durationSec: cfg.mainDurationSec, t: 0 });
    if (cfg.enableMiddleWhiteFrame) {
      segs.push({ kind: "white", durationSec: cfg.middleWhiteDurationSec });
    }
    if (cfg.enableWhiteFlashes) {
      for (let i = 0; i < cfg.flashCount; i++) {
        segs.push({ kind: "flash", durationSec: cfg.flashFrames / FPS });
        segs.push({ kind: "image", durationSec: 0.4, t: 0.3 });
      }
    }
    segs.push({ kind: "image", durationSec: 8, t: 0.5 });
    segs.push({ kind: "edge", durationSec: cfg.tailDurationSec });
    return mergeWhite(segs);
  }

  // HOT
  segs.push({ kind: "edge_overlay", durationSec: 0.006, alpha: 1, t: 0 });
  for (let i = 0; i < 200; i++) {
    segs.push({ kind: "edge", durationSec: 0.001 });
    segs.push({ kind: "image", durationSec: 0.001, t: 0 });
  }
  segs.push({ kind: "image", durationSec: 2, t: 0.1 });
  for (let i = 0; i < 200; i++) {
    segs.push({ kind: "edge_overlay", durationSec: 0.001, alpha: 0.5, t: 0.2 });
    segs.push({ kind: "image", durationSec: 0.001, t: 0.2 });
  }
  segs.push({ kind: "image", durationSec: 60, t: 0.4 });
  for (let i = 0; i < 200; i++) {
    segs.push({ kind: "edge", durationSec: 0.001 });
    segs.push({ kind: "image", durationSec: 0.001, t: 0.6 });
  }
  segs.push({ kind: "edge", durationSec: cfg.tailDurationSec });
  return mergeWhite(segs);
}

function mergeWhite(segs) {
  const out = [];
  for (const s of segs) {
    const last = out[out.length - 1];
    if (last && (s.kind === "white" || s.kind === "flash") && last.kind === s.kind) {
      last.durationSec += s.durationSec;
    } else {
      out.push({ ...s });
    }
  }
  return out;
}

export function drawCoverFit(ctx, img, t, motion) {
  const iw = img.naturalWidth || img.width;
  const ih = img.naturalHeight || img.height;
  const scale0 = Math.max(WIDTH / iw, HEIGHT / ih);
  let scale = scale0;
  let ox = 0;
  let oy = 0;
  const p = Math.min(1, Math.max(0, t || 0));

  switch (motion) {
    case "zoom_in":
      scale = scale0 * (1 + 0.12 * p);
      break;
    case "zoom_out":
      scale = scale0 * (1.12 - 0.12 * p);
      break;
    case "pan_h":
      ox = (WIDTH - iw * scale) / 2 + (p - 0.5) * 40;
      oy = (HEIGHT - ih * scale) / 2;
      break;
    case "pan_v":
      ox = (WIDTH - iw * scale) / 2;
      oy = (HEIGHT - ih * scale) / 2 + (p - 0.5) * 40;
      break;
    case "ken_burns":
      scale = scale0 * (1 + 0.1 * p);
      ox = (WIDTH - iw * scale) / 2 + (p - 0.5) * 30;
      oy = (HEIGHT - ih * scale) / 2 + (0.5 - p) * 20;
      break;
    default:
      ox = (WIDTH - iw * scale) / 2;
      oy = (HEIGHT - ih * scale) / 2;
  }
  if (motion !== "pan_h" && motion !== "pan_v" && motion !== "ken_burns") {
    ox = (WIDTH - iw * scale) / 2;
    oy = (HEIGHT - ih * scale) / 2;
  }
  ctx.drawImage(img, ox, oy, iw * scale, ih * scale);
}

export function drawFrame(ctx, segment, mainImg, coverImg, motion) {
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, WIDTH, HEIGHT);
  switch (segment.kind) {
    case "white":
    case "flash":
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, WIDTH, HEIGHT);
      break;
    case "edge":
      drawCoverFit(ctx, coverImg || mainImg, 0, motion);
      break;
    case "edge_overlay":
      drawCoverFit(ctx, mainImg, segment.t ?? 0, motion);
      ctx.globalAlpha = segment.alpha ?? 0.2;
      drawCoverFit(ctx, coverImg || mainImg, 0, motion);
      ctx.globalAlpha = 1;
      break;
    default:
      drawCoverFit(ctx, mainImg, segment.t ?? 0, motion);
      break;
  }
}

function supportsWebCodecs() {
  return typeof VideoEncoder !== "undefined" && typeof VideoFrame !== "undefined";
}

/**
 * Encode timeline. Long segments use 1 fps to keep browser usable.
 */
export async function encodeHotMp4(mainImg, coverImg, profile, onProgress) {
  if (!supportsWebCodecs()) {
    throw new Error("WebCodecs indisponível — use Chrome ou Edge.");
  }
  const timeline = buildTimeline(profile);
  const motion = MOTIONS[randomInt(0, MOTIONS.length - 1)];
  const canvas = document.createElement("canvas");
  canvas.width = WIDTH;
  canvas.height = HEIGHT;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });

  const target = new ArrayBufferTarget();
  const muxer = new Muxer({
    target,
    video: { codec: "avc", width: WIDTH, height: HEIGHT },
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
    width: WIDTH,
    height: HEIGHT,
    bitrate: BITRATE,
    framerate: FPS,
    latencyMode: "quality",
  });

  let timestampUs = 0;
  const totalSec = timeline.reduce((s, x) => s + x.durationSec, 0);
  let doneSec = 0;

  for (const seg of timeline) {
    // Long static: 1 frame/sec; short: full FPS
    const fps = seg.durationSec >= 5 ? 1 : FPS;
    const frameDurUs = Math.round(1_000_000 / fps);
    const nFrames = Math.max(1, Math.round(seg.durationSec * fps));

    for (let f = 0; f < nFrames; f++) {
      if (encoderError) throw encoderError;
      drawFrame(ctx, seg, mainImg, coverImg, motion);
      const frame = new VideoFrame(canvas, {
        timestamp: timestampUs,
        duration: frameDurUs,
      });
      const keyFrame = f === 0 || f % (fps * 2) === 0;
      encoder.encode(frame, { keyFrame });
      frame.close();
      timestampUs += frameDurUs;
      if (encoder.encodeQueueSize > 8) {
        await new Promise((r) => {
          encoder.ondequeue = () => r();
        });
      }
    }
    doneSec += seg.durationSec;
    onProgress?.(Math.min(0.99, doneSec / totalSec), `Codificando… ${Math.round(doneSec)}s / ${Math.round(totalSec)}s`);
  }

  await encoder.flush();
  encoder.close();
  muxer.finalize();

  const buf = target.buffer;
  onProgress?.(1, "Pronto");
  return new Blob([buf], { type: "video/mp4" });
}
