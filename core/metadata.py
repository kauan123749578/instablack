"""Remoção de metadados antes de cada publicação.

Cada post gera um arquivo limpo e único, mas sem gravar tags aleatórias no container.
Vídeo: ffmpeg remove metadados originais e reencoda com variação mínima imperceptível.
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


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _unique_meta_bundle(account_hint: str | None = None) -> dict[str, str]:
    """Gera fingerprint interno único sem gravar dados aleatórios no arquivo."""
    for _ in range(20):
        uid = str(uuid.uuid4())
        rand_seconds = random.randint(0, 90 * 24 * 60 * 60)
        fake_dt = dt.datetime.utcnow() - dt.timedelta(seconds=rand_seconds)
        # microsegundos aleatórios para não colidir
        fake_dt = fake_dt.replace(microsecond=random.randint(0, 999999))
        creation_time = fake_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        artist_seed = hashlib.sha256((account_hint or _rand_token(7)).encode()).hexdigest()[:10]
        stream_seed = _rand_token(10)
        raw = "|".join([uid, creation_time, artist_seed, stream_seed])
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
                "account_seed": artist_seed,
                "stream_seed": stream_seed,
            }
    # fallback extremo
    uid = str(uuid.uuid4())
    return {
        "uuid": uid,
        "fingerprint": uid[:24],
        "creation_time": dt.datetime.utcnow().isoformat(timespec="milliseconds"),
        "account_seed": _rand_token(10),
        "stream_seed": _rand_token(10),
    }


def strip_metadata(
    src: Path,
    dest: Path,
    *,
    account_hint: str | None = None,
) -> tuple[Path, dict[str, str]]:
    """Gera cópia de ``src`` em ``dest`` sem metadados e com bytes únicos."""
    if not src.exists():
        raise MetadataStripError(f"Vídeo de origem não encontrado: {src}")
    if not _ffmpeg_available():
        raise MetadataStripError(
            f"ffmpeg não encontrado no PATH (FFMPEG_BIN={settings.ffmpeg_bin})."
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    meta = _unique_meta_bundle(account_hint)
    raw_sha = sha256_file(src)
    # Variação imperceptível para mudar também o stream, não só o container.
    brightness = random.choice([-1, 1]) * random.uniform(0.00015, 0.00055)

    cmd = [
        settings.ffmpeg_bin,
        "-y",
        "-i", str(src),
        "-map", "0:v:0",
        "-map", "0:a?",
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-vf", f"eq=brightness={brightness:.6f}:contrast=1.0",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "160k",
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

    clean_sha = sha256_file(dest)
    if clean_sha == raw_sha:
        raise MetadataStripError("Arquivo limpo ficou idêntico ao original (hash igual).")

    meta["raw_sha256"] = raw_sha
    meta["clean_sha256"] = clean_sha
    meta["clean_size"] = str(dest.stat().st_size)
    meta["video_filter"] = f"eq=brightness={brightness:.6f}:contrast=1.0"
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
