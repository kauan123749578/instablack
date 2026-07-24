"""Prepara mídia sem metadados — obrigatório antes de publicar."""
from __future__ import annotations

import subprocess
from pathlib import Path

from app.config import settings
from core.metadata import MetadataStripError, sha256_file, strip_image_metadata, strip_metadata

VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v", ".mkv", ".avi"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}


def prepare_clean_media(
    raw_path: Path,
    clean_path: Path,
    *,
    content_type: str,
    account_hint: str | None = None,
) -> tuple[Path, dict | None]:
    """Remove metadados. Falha se não conseguir limpar (nunca publica original).

    Retorna (path_limpo, meta_info|None). meta_info tem fingerprint único por post.
    """
    ext = raw_path.suffix.lower()
    is_video = ext in VIDEO_EXT

    if content_type == "photo":
        if ext not in IMAGE_EXT:
            raise MetadataStripError(f"Foto no feed exige imagem, recebido: {ext}")
        raw_sha = sha256_file(raw_path)
        strip_image_metadata(raw_path, clean_path)
        clean_sha = sha256_file(clean_path)
        if clean_sha == raw_sha:
            raise MetadataStripError("Imagem limpa ficou idêntica ao original (hash igual).")
        return clean_path, {
            "fingerprint": clean_sha[:24],
            "raw_sha256": raw_sha,
            "clean_sha256": clean_sha,
            "clean_size": str(clean_path.stat().st_size),
        }

    if content_type == "story" and not is_video:
        if ext not in IMAGE_EXT:
            raise MetadataStripError(f"Story sem vídeo exige imagem, recebido: {ext}")
        raw_sha = sha256_file(raw_path)
        strip_image_metadata(raw_path, clean_path)
        clean_sha = sha256_file(clean_path)
        if clean_sha == raw_sha:
            raise MetadataStripError("Imagem limpa ficou idêntica ao original (hash igual).")
        return clean_path, {
            "fingerprint": clean_sha[:24],
            "raw_sha256": raw_sha,
            "clean_sha256": clean_sha,
            "clean_size": str(clean_path.stat().st_size),
        }

    if not is_video:
        raise MetadataStripError(f"Reels/Story em vídeo exige arquivo de vídeo, recebido: {ext}")

    path, meta = strip_metadata(raw_path, clean_path, account_hint=account_hint)
    return path, meta


def prepare_clean_thumb(raw_path: Path, clean_path: Path) -> Path:
    """Remove EXIF da capa do Reel (sempre único)."""
    strip_image_metadata(raw_path, clean_path)
    return clean_path


def apply_camouflage_overlay(
    video_path: Path,
    cover_path: Path,
    output_path: Path,
    *,
    opacity: float = 0.10,
) -> Path:
    """Mistura imagem de camuflagem por cima do vídeo (alpha 0.01–0.40).

    A capa é redimensionada para o frame do vídeo e aplicada em todos os frames.
    """
    alpha = max(0.01, min(0.40, float(opacity or 0.10)))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filter_complex = (
        f"[1:v][0:v]scale2ref=flags=bicubic[cov][vid];"
        f"[cov]format=rgba,colorchannelmixer=aa={alpha:.4f}[ov];"
        f"[vid][ov]overlay=0:0:shortest=1[outv]"
    )
    cmd = [
        settings.ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-loop",
        "1",
        "-framerate",
        "30",
        "-i",
        str(cover_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except FileNotFoundError as exc:
        raise MetadataStripError(
            f"FFmpeg não encontrado (FFMPEG_BIN={settings.ffmpeg_bin})."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MetadataStripError("FFmpeg excedeu o tempo na camuflagem.") from exc
    if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
        detail = (proc.stderr or proc.stdout or "erro desconhecido")[-700:]
        raise MetadataStripError(f"Falha ao aplicar camuflagem: {detail}")
    return output_path


def generate_video_thumbnail(video_path: Path, output_path: Path) -> Path:
    """Extrai um frame JPEG para evitar geração interna frágil do Instagrapi."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [
                settings.ffmpeg_bin,
                "-y",
                "-ss",
                "0.5",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError as exc:
        raise MetadataStripError(
            f"FFmpeg não encontrado (FFMPEG_BIN={settings.ffmpeg_bin})."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MetadataStripError("FFmpeg excedeu 60 segundos ao gerar thumbnail.") from exc
    if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
        detail = (proc.stderr or proc.stdout or "erro desconhecido")[-500:]
        raise MetadataStripError(f"Não foi possível gerar thumbnail do vídeo: {detail}")
    return output_path
