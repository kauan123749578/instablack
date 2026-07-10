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
    account_hint: str | None = None,
) -> tuple[Path, dict | None]:
    """Remove metadados. Falha se não conseguir limpar (nunca publica original).

    Retorna (path_limpo, meta_info|None). meta_info tem fingerprint único por post.
    """
    ext = raw_path.suffix.lower()
    is_video = ext in VIDEO_EXT

    if content_type == "photo" or (content_type == "story" and not is_video):
        if ext not in IMAGE_EXT and ext not in VIDEO_EXT:
            raise MetadataStripError(f"Formato não suportado: {ext}")
        strip_image_metadata(raw_path, clean_path)
        return clean_path, {"fingerprint": f"img-{account_hint or 'x'}-{clean_path.stat().st_size}"}

    if not is_video:
        raise MetadataStripError(f"Reels/Story em vídeo exige arquivo de vídeo, recebido: {ext}")

    path, meta = strip_metadata(raw_path, clean_path, account_hint=account_hint)
    return path, meta


def prepare_clean_thumb(raw_path: Path, clean_path: Path) -> Path:
    """Remove EXIF da capa do Reel (sempre único)."""
    strip_image_metadata(raw_path, clean_path)
    return clean_path
