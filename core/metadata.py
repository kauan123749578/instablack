"""Remoção de metadados únicos antes de cada publicação.

Cada post gera um fingerprint de metadados diferente (UUID + campos aleatórios).
Vídeo: ffmpeg remux sem metadados originais + metadados novos únicos.
Imagem (capa): Pillow re-salva sem EXIF.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random
import secrets
import shutil
import string
import subprocess
import uuid
from pathlib import Path

from PIL import Image

from app.config import settings

# Evita reutilizar o mesmo fingerprint na mesma execução do worker
_RECENT_FINGERPRINTS: set[str] = set()
_MAX_RECENT = 500


class MetadataStripError(RuntimeError):
    pass


def _ffmpeg_available() -> bool:
    return shutil.which(settings.ffmpeg_bin) is not None


def _rand_token(n: int = 12) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _unique_meta_bundle(account_hint: str | None = None) -> dict[str, str]:
    """Gera metadados únicos que nunca se repetem entre posts."""
    for _ in range(20):
        uid = str(uuid.uuid4())
        rand_seconds = random.randint(0, 90 * 24 * 60 * 60)
        fake_dt = dt.datetime.utcnow() - dt.timedelta(seconds=rand_seconds)
        # microsegundos aleatórios para não colidir
        fake_dt = fake_dt.replace(microsecond=random.randint(0, 999999))
        creation_time = fake_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        title = f"clip_{_rand_token(8)}"
        comment = f"id:{uid[:8]}:{_rand_token(6)}"
        encoder = random.choice([
            f"Lavf{random.randint(58, 61)}.{random.randint(10, 99)}.{random.randint(100, 999)}",
            f"HandBrake {random.randint(1, 1)}.{random.randint(5, 9)}.{random.randint(0, 2)}",
            f"export_{_rand_token(5)}",
        ])
        artist = account_hint or _rand_token(7)
        description = f"u{uid.replace('-', '')[:16]}"
        copyright_ = f"© {fake_dt.year} {_rand_token(5)}"
        raw = "|".join([uid, creation_time, title, comment, encoder, artist, description])
        fp = hashlib.sha256(raw.encode()).hexdigest()[:24]
        if fp not in _RECENT_FINGERPRINTS:
            _RECENT_FINGERPRINTS.add(fp)
            if len(_RECENT_FINGERPRINTS) > _MAX_RECENT:
                # remove um item arbitrário
                _RECENT_FINGERPRINTS.pop()
            return {
                "uuid": uid,
                "fingerprint": fp,
                "creation_time": creation_time,
                "title": title,
                "comment": comment,
                "encoder": encoder,
                "artist": artist,
                "description": description,
                "copyright": copyright_,
            }
    # fallback extremo
    uid = str(uuid.uuid4())
    return {
        "uuid": uid,
        "fingerprint": uid[:24],
        "creation_time": dt.datetime.utcnow().isoformat(timespec="milliseconds"),
        "title": f"m_{_rand_token(10)}",
        "comment": uid,
        "encoder": f"enc_{_rand_token(8)}",
        "artist": _rand_token(8),
        "description": uid,
        "copyright": _rand_token(8),
    }


def strip_metadata(
    src: Path,
    dest: Path,
    *,
    account_hint: str | None = None,
) -> tuple[Path, dict[str, str]]:
    """Gera cópia de ``src`` em ``dest`` com metadados únicos (nunca reutilizados)."""
    if not src.exists():
        raise MetadataStripError(f"Vídeo de origem não encontrado: {src}")
    if not _ffmpeg_available():
        raise MetadataStripError(
            f"ffmpeg não encontrado no PATH (FFMPEG_BIN={settings.ffmpeg_bin})."
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    meta = _unique_meta_bundle(account_hint)

    cmd = [
        settings.ffmpeg_bin,
        "-y",
        "-i", str(src),
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-metadata", f"creation_time={meta['creation_time']}",
        "-metadata", f"title={meta['title']}",
        "-metadata", f"comment={meta['comment']}",
        "-metadata", f"description={meta['description']}",
        "-metadata", f"artist={meta['artist']}",
        "-metadata", f"encoder={meta['encoder']}",
        "-metadata", f"copyright={meta['copyright']}",
        "-metadata", f"unique_id={meta['uuid']}",
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

    return dest, meta


def strip_image_metadata(src: Path, dest: Path) -> Path:
    """Re-salva a imagem (capa) sem metadados EXIF — bytes únicos a cada save."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as img:
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        else:
            img = img.copy()
        # Qualidade levemente aleatória para fingerprint de arquivo diferente
        quality = random.randint(90, 96)
        fmt = "JPEG"
        # Pixel invisível no canto (1px) com cor aleatória mínima — quebra hash idêntico
        try:
            px = img.load()
            x, y = img.size[0] - 1, img.size[1] - 1
            r, g, b = px[x, y][:3] if isinstance(px[x, y], tuple) else (px[x, y],) * 3
            px[x, y] = (
                max(0, min(255, r + random.choice([-1, 0, 1]))),
                max(0, min(255, g + random.choice([-1, 0, 1]))),
                max(0, min(255, b + random.choice([-1, 0, 1]))),
            )
        except Exception:
            pass
        img.save(dest, format=fmt, quality=quality, optimize=True)
    return dest
