"""Prepara mídia sem metadados — obrigatório antes de publicar."""
from __future__ import annotations

from pathlib import Path

from core.metadata import MetadataStripError, strip_image_metadata, strip_metadata

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}


def prepare_clean_media(
    raw_path: Path,
    clean_path: Path,
    *,
    content_type: str,
) -> Path:
    """Remove metadados. Falha se não conseguir limpar (nunca publica original)."""
    ext = raw_path.suffix.lower()
    is_video = ext in VIDEO_EXT

    if content_type == "photo" or (content_type == "story" and not is_video):
        if ext not in IMAGE_EXT and ext not in VIDEO_EXT:
            raise MetadataStripError(f"Formato não suportado: {ext}")
        strip_image_metadata(raw_path, clean_path)
        return clean_path

    if not is_video:
        raise MetadataStripError(f"Reels/Story em vídeo exige arquivo de vídeo, recebido: {ext}")

    strip_metadata(raw_path, clean_path)
    return clean_path


def prepare_clean_thumb(raw_path: Path, clean_path: Path) -> Path:
    """Remove EXIF da capa do Reel."""
    strip_image_metadata(raw_path, clean_path)
    return clean_path
