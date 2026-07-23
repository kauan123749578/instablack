/** Camuflagem — strip metadados (canvas imagem + ffmpeg.wasm vídeo/áudio) */

import { FFmpeg } from "https://cdn.jsdelivr.net/npm/@ffmpeg/ffmpeg@0.12.10/+esm";
import { fetchFile, toBlobURL } from "https://cdn.jsdelivr.net/npm/@ffmpeg/util@0.12.1/+esm";

let ffmpegSingleton = null;
let ffmpegLoading = null;

async function getFFmpeg(onLog) {
  if (ffmpegSingleton?.loaded) return ffmpegSingleton;
  if (ffmpegLoading) return ffmpegLoading;
  ffmpegLoading = (async () => {
    const ffmpeg = new FFmpeg();
    ffmpeg.on("log", ({ message }) => onLog?.(message));
    const base = "https://cdn.jsdelivr.net/npm/@ffmpeg/core@0.12.6/dist/esm";
    await ffmpeg.load({
      coreURL: await toBlobURL(`${base}/ffmpeg-core.js`, "text/javascript"),
      wasmURL: await toBlobURL(`${base}/ffmpeg-core.wasm`, "application/wasm"),
    });
    ffmpegSingleton = ffmpeg;
    return ffmpeg;
  })();
  return ffmpegLoading;
}

export async function stripImageMetadata(file) {
  const bmp = await createImageBitmap(file);
  const canvas = document.createElement("canvas");
  canvas.width = bmp.width;
  canvas.height = bmp.height;
  canvas.getContext("2d").drawImage(bmp, 0, 0);
  bmp.close();
  const type = file.type === "image/png" ? "image/png" : "image/jpeg";
  const ext = type === "image/png" ? ".png" : ".jpg";
  const blob = await new Promise((resolve, reject) => {
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("blob fail"))), type, 0.95);
  });
  const base = file.name.replace(/\.[^.]+$/, "") || "limpo";
  return { blob, name: `${base}_limpo${ext}` };
}

function isImage(file) {
  return (file.type || "").startsWith("image/") || /\.(jpe?g|png|webp|gif|bmp)$/i.test(file.name);
}

function isAv(file) {
  return (
    (file.type || "").startsWith("video/") ||
    (file.type || "").startsWith("audio/") ||
    /\.(mp4|mov|avi|mkv|webm|mp3|wav|aac|m4a)$/i.test(file.name)
  );
}

export async function stripAvMetadata(file, onProgress) {
  const ffmpeg = await getFFmpeg();
  onProgress?.(0.1, "Carregando FFmpeg…");
  const inName = `in_${Date.now()}${file.name.includes(".") ? file.name.slice(file.name.lastIndexOf(".")) : ".mp4"}`;
  const outName = `out_${Date.now()}.mp4`;
  await ffmpeg.writeFile(inName, await fetchFile(file));
  onProgress?.(0.4, "Removendo tags…");
  const code = await ffmpeg.exec([
    "-i",
    inName,
    "-map_metadata",
    "-1",
    "-map_chapters",
    "-1",
    "-metadata:s:v",
    "rotate=0",
    "-c",
    "copy",
    "-movflags",
    "+faststart",
    outName,
  ]);
  if (code !== 0) {
    // fallback reencode leve se copy falhar
    await ffmpeg.exec([
      "-i",
      inName,
      "-map_metadata",
      "-1",
      "-map_chapters",
      "-1",
      "-c:v",
      "libx264",
      "-preset",
      "ultrafast",
      "-crf",
      "23",
      "-c:a",
      "aac",
      "-movflags",
      "+faststart",
      outName,
    ]);
  }
  const data = await ffmpeg.readFile(outName);
  await ffmpeg.deleteFile(inName).catch(() => {});
  await ffmpeg.deleteFile(outName).catch(() => {});
  onProgress?.(1, "Pronto");
  const base = file.name.replace(/\.[^.]+$/, "") || "limpo";
  return {
    blob: new Blob([data.buffer], { type: "video/mp4" }),
    name: `${base}_limpo.mp4`,
  };
}

export async function stripMetadataBatch(files, onProgress) {
  const out = [];
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    onProgress?.(i / files.length, `Processando ${file.name} (${i + 1}/${files.length})`);
    if (isImage(file)) {
      out.push(await stripImageMetadata(file));
    } else if (isAv(file)) {
      if (file.size > 200 * 1024 * 1024) {
        throw new Error(`${file.name}: acima de 200MB`);
      }
      out.push(
        await stripAvMetadata(file, (p, msg) =>
          onProgress?.((i + p) / files.length, msg || file.name)
        )
      );
    } else {
      throw new Error(`Tipo não suportado: ${file.name}`);
    }
  }
  onProgress?.(1, "Concluído");
  return out;
}
