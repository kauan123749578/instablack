"""Remoção de metadados antes de cada publicação.

Vídeo: ffmpeg via subprocess (copy, sem reencodar).
Imagem (capa): Pillow re-salva sem EXIF.
"""
from __future__ import annotations

import datetime as dt
import random
import shutil
import subprocess
from pathlib import Path

from PIL import Image

from app.config import settings


class MetadataStripError(RuntimeError):
    pass


def _ffmpeg_available() -> bool:
    return shutil.which(settings.ffmpeg_bin) is not None


def strip_metadata(src: Path, dest: Path) -> Path:
    """Gera uma cópia de ``src`` em ``dest`` sem metadados de vídeo."""
    if not src.exists():
        raise MetadataStripError(f"Vídeo de origem não encontrado: {src}")
    if not _ffmpeg_available():
        raise MetadataStripError(
            f"ffmpeg não encontrado no PATH (FFMPEG_BIN={settings.ffmpeg_bin})."
        )

    dest.parent.mkdir(parents=True, exist_ok=True)

    rand_seconds = random.randint(0, 30 * 24 * 60 * 60)
    fake_dt = dt.datetime.utcnow() - dt.timedelta(seconds=rand_seconds)
    creation_time = fake_dt.strftime("%Y-%m-%dT%H:%M:%S")

    cmd = [
        settings.ffmpeg_bin,
        "-y",
        "-i", str(src),
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-metadata", f"creation_time={creation_time}",
        "-c", "copy",
        "-movflags", "+faststart",
        str(dest),
    ]

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise MetadataStripError(
            "Falha ao remover metadados: " + proc.stderr.decode("utf-8", errors="ignore")[-500:]
        )

    return dest


def strip_image_metadata(src: Path, dest: Path) -> Path:
    """Re-salva a imagem (capa) sem metadados EXIF."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as img:
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        else:
            img = img.copy()
        fmt = "JPEG" if (img.format or "").upper() in ("JPEG", "JPG") else "PNG"
        img.save(dest, format=fmt, quality=95)
    return dest
