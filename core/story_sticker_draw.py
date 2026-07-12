"""Desenha sticker de link estilo Instagram (pill branco + ícone + texto) na mídia do Story."""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.config import settings

log = logging.getLogger(__name__)

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_link_icon(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color: tuple[int, int, int]) -> None:
    """Ícone de link (dois elos) — visual próximo ao sticker do Instagram."""
    stroke = max(2, size // 7)
    r = max(5, int(size * 0.38))
    # elo esquerdo (inclinado)
    box1 = [cx - size // 2, cy - r, cx - size // 2 + 2 * r, cy + r]
    draw.arc(box1, start=40, end=320, fill=color, width=stroke)
    # elo direito
    box2 = [cx + size // 2 - 2 * r, cy - r, cx + size // 2, cy + r]
    draw.arc(box2, start=220, end=140, fill=color, width=stroke)


def render_link_sticker_rgba(text: str, scale: int = 1) -> Image.Image:
    """Gera PNG RGBA do pill branco com ícone azul + texto."""
    label = (text or "Link").strip()[:40] or "Link"
    pad_x = 28 * scale
    pad_y = 16 * scale
    icon_size = 28 * scale
    gap = 14 * scale
    font = _load_font(26 * scale)

    # mede texto
    tmp = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(tmp)
    bbox = d.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    width = pad_x + icon_size + gap + tw + pad_x
    height = max(icon_size, th) + pad_y * 2
    radius = height // 2  # pill

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, width - 1, height - 1], radius=radius, fill=(255, 255, 255, 245))

    icon_cx = pad_x + icon_size // 2
    icon_cy = height // 2
    _draw_link_icon(draw, icon_cx, icon_cy, icon_size, (37, 116, 254))  # azul IG-ish

    text_x = pad_x + icon_size + gap
    text_y = (height - th) // 2 - (bbox[1] if bbox else 0)
    draw.text((text_x, text_y), label, font=font, fill=(20, 20, 20, 255))
    return img


def _paste_sticker_on_image(base: Image.Image, sticker: Image.Image, *, y_ratio: float = 0.78) -> Image.Image:
    canvas = base.convert("RGBA")
    w, h = canvas.size
    # escala sticker ~48% da largura do story
    target_w = int(w * 0.48)
    ratio = target_w / sticker.width
    target_h = max(1, int(sticker.height * ratio))
    sticker_r = sticker.resize((target_w, target_h), Image.Resampling.LANCZOS)
    x = (w - target_w) // 2
    y = int(h * y_ratio) - target_h // 2
    y = max(0, min(h - target_h, y))
    canvas.alpha_composite(sticker_r, (x, y))
    return canvas


def apply_link_sticker_to_image(src: Path, dst: Path, text: str) -> Path:
    with Image.open(src) as im:
        base = im.convert("RGB")
        sticker = render_link_sticker_rgba(text, scale=2)
        composed = _paste_sticker_on_image(base, sticker)
        out = composed.convert("RGB")
        out.save(dst, format="JPEG", quality=92, optimize=True)
    return dst


def apply_link_sticker_to_video(src: Path, dst: Path, text: str) -> Path:
    """Queima o sticker no vídeo via ffmpeg overlay (requer ffmpeg)."""
    ffmpeg = settings.ffmpeg_bin
    if not shutil.which(ffmpeg):
        raise RuntimeError(f"ffmpeg não encontrado ({ffmpeg}) — necessário para sticker em story de vídeo")

    # Descobre resolução do vídeo
    probe = subprocess.run(
        [
            shutil.which("ffprobe") or "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(src),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    w, h = 1080, 1920
    if probe.returncode == 0 and probe.stdout.strip():
        parts = probe.stdout.strip().split(",")
        if len(parts) >= 2:
            try:
                w, h = int(parts[0]), int(parts[1])
            except ValueError:
                pass

    sticker = render_link_sticker_rgba(text, scale=3)
    target_w = int(w * 0.48)
    ratio = target_w / sticker.width
    target_h = max(1, int(sticker.height * ratio))
    sticker = sticker.resize((target_w, target_h), Image.Resampling.LANCZOS)

    # canvas transparente do tamanho do vídeo com sticker posicionado
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    x = (w - target_w) // 2
    y = int(h * 0.78) - target_h // 2
    y = max(0, min(h - target_h, y))
    overlay.alpha_composite(sticker, (x, y))

    with tempfile.TemporaryDirectory(prefix="sticker_") as td:
        overlay_path = Path(td) / "sticker.png"
        overlay.save(overlay_path, format="PNG")
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(src),
            "-i",
            str(overlay_path),
            "-filter_complex",
            "[0:v][1:v]overlay=0:0:format=auto",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            "-pix_fmt",
            "yuv420p",
            str(dst),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            log.error("ffmpeg sticker falhou: %s", (result.stderr or "")[-800:])
            raise RuntimeError("Falha ao gravar sticker no vídeo do story")
    return dst


def apply_story_link_sticker(src: Path, dst: Path, text: str) -> Path:
    """Aplica sticker de link desenhado em foto ou vídeo de story. Retorna dst."""
    ext = src.suffix.lower()
    label = (text or "").strip() or "Link"
    log.info("Desenhando sticker de story texto=%r em %s", label, src.name)
    if ext in IMAGE_EXT:
        return apply_link_sticker_to_image(src, dst, label)
    if ext in VIDEO_EXT:
        return apply_link_sticker_to_video(src, dst, label)
    raise RuntimeError(f"Formato não suportado para sticker: {ext}")
