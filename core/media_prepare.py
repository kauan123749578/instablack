"""Prepara mídia sem metadados — obrigatório antes de publicar."""
from __future__ import annotations

from pathlib import Path

from core.metadata import MetadataStripError, sha256_file, strip_image_metadata, strip_metadata

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
