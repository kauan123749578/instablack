"""Editor de Reels — overlay de texto via FFmpeg (layout estilo Story Studio)."""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from app.config import settings

VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v", ".mkv", ".avi"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}


class ReelsEditorError(RuntimeError):
    pass


def is_video_path(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXT


def is_image_path(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXT


def _ffmpeg() -> str:
    return settings.ffmpeg_bin or "ffmpeg"


def _font_path_escaped() -> str | None:
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
    ]
    for p in candidates:
        if p.exists():
            return str(p.resolve()).replace("\\", "/").replace(":", "\\:")
    return None


def _probe_size(media_path: Path) -> tuple[int, int]:
    ffprobe = "ffprobe"
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0:s=x",
        str(media_path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
        w, h = out.stdout.strip().split("x")
        return int(w), int(h)
    except Exception:
        return 1080, 1920


def _run_ffmpeg(cmd: list[str], *, timeout: int = 600) -> None:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ReelsEditorError(
            f"FFmpeg não encontrado (FFMPEG_BIN={settings.ffmpeg_bin})."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ReelsEditorError("FFmpeg excedeu o tempo limite.") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "erro desconhecido")[-900:]
        raise ReelsEditorError(f"FFmpeg falhou: {detail}")


def _normalize_text(raw: str) -> str:
    t = (raw or "").replace("\\n", "\n").replace("/n", "\n").strip()
    return t


def _font_size(height: int, font_scale: float) -> int:
    base = height * 0.045 * max(0.35, min(2.5, float(font_scale or 1.0)))
    return int(max(22, min(120, round(base))))


def build_overlay_filter(
    *,
    text_file: Path,
    width: int,
    height: int,
    x_frac: float,
    y_frac: float,
    font_scale: float,
    text_color: str,
    border_color: str,
    border_width: int,
    watermark_text: str,
    watermark_enabled: bool,
    fit_cover: bool,
    is_image: bool,
) -> str:
    """filter_complex: escala mídia 1080x1920 + drawtext + watermark opcional."""
    x_frac = max(0.05, min(0.95, float(x_frac)))
    y_frac = max(0.08, min(0.92, float(y_frac)))
    fs = _font_size(height, font_scale)
    font = _font_path_escaped()
    font_part = f"fontfile='{font}':" if font else ""

    if fit_cover or is_image:
        scale = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,setsar=1[base];"
        )
    else:
        scale = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[base];"
        )

    text_path = str(text_file.resolve()).replace("\\", "/").replace(":", "\\:")
    x_expr = f"(w*{x_frac:.4f})-(text_w/2)"
    y_expr = f"(h*{y_frac:.4f})-(text_h/2)"

    border_w = max(0, min(6, int(border_width or 2)))
    color = (text_color or "yellow").strip() or "yellow"
    border = (border_color or "black").strip() or "black"

    parts = [
        scale,
        f"[base]drawtext={font_part}"
        f"textfile='{text_path}':"
        f"fontsize={fs}:"
        f"fontcolor={color}:"
        f"borderw={border_w}:"
        f"bordercolor={border}@0.9:"
        f"shadowcolor=black@0.5:shadowx=2:shadowy=2:"
        f"x={x_expr}:"
        f"y={y_expr}:"
        f"line_spacing={max(4, fs // 6)}[txt];",
    ]

    current = "txt"
    if watermark_enabled and (watermark_text or "").strip():
        wm = (
            (watermark_text or "")
            .replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace(":", "\\:")
        )
        wm_fs = max(14, fs // 3)
        wm_y = f"(h*{min(0.96, y_frac + 0.12):.4f})"
        parts.append(
            f"[{current}]drawtext={font_part}"
            f"text='{wm}':"
            f"fontsize={wm_fs}:"
            f"fontcolor=white:"
            f"borderw=1:"
            f"bordercolor=black@0.8:"
            f"x=(w-text_w)/2:"
            f"y={wm_y}[outv];"
        )
        current = "outv"
    else:
        parts.append(f"[{current}]copy[outv];")

    return "".join(parts)


def render_preview_jpeg(
    media_path: Path,
    *,
    text: str,
    x_frac: float = 0.5,
    y_frac: float = 0.5,
    font_scale: float = 1.0,
    text_color: str = "yellow",
    border_color: str = "black",
    border_width: int = 2,
    watermark_text: str = "",
    watermark_enabled: bool = True,
    fit_cover: bool = True,
) -> bytes:
    is_image = is_image_path(media_path)
    with tempfile.TemporaryDirectory(prefix="ib-reels-preview-") as td:
        td_path = Path(td)
        text_file = td_path / "overlay.txt"
        text_file.write_text(_normalize_text(text), encoding="utf-8")
        out = td_path / "preview.jpg"
        width, height = _probe_size(media_path)
        filt = build_overlay_filter(
            text_file=text_file,
            width=width,
            height=height,
            x_frac=x_frac,
            y_frac=y_frac,
            font_scale=font_scale,
            text_color=text_color,
            border_color=border_color,
            border_width=border_width,
            watermark_text=watermark_text,
            watermark_enabled=watermark_enabled,
            fit_cover=fit_cover,
            is_image=is_image,
        )
        cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y"]
        if is_image:
            cmd.extend(["-loop", "1", "-i", str(media_path)])
        else:
            cmd.extend(["-ss", "0.5", "-i", str(media_path)])
        cmd.extend(
            [
                "-filter_complex",
                filt,
                "-map",
                "[outv]",
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(out),
            ]
        )
        _run_ffmpeg(cmd, timeout=120)
        if not out.exists():
            raise ReelsEditorError("Prévia não gerada.")
        return out.read_bytes()


def render_reel_mp4(
    media_path: Path,
    output_path: Path,
    *,
    text: str,
    x_frac: float = 0.5,
    y_frac: float = 0.5,
    font_scale: float = 1.0,
    text_color: str = "yellow",
    border_color: str = "black",
    border_width: int = 2,
    watermark_text: str = "",
    watermark_enabled: bool = True,
    fit_cover: bool = True,
    photo_duration: float = 8.0,
    video_duration: float = 60.0,
) -> Path:
    is_image = is_image_path(media_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ib-reels-render-") as td:
        td_path = Path(td)
        text_file = td_path / "overlay.txt"
        text_file.write_text(_normalize_text(text), encoding="utf-8")
        width, height = _probe_size(media_path)
        filt = build_overlay_filter(
            text_file=text_file,
            width=width,
            height=height,
            x_frac=x_frac,
            y_frac=y_frac,
            font_scale=font_scale,
            text_color=text_color,
            border_color=border_color,
            border_width=border_width,
            watermark_text=watermark_text,
            watermark_enabled=watermark_enabled,
            fit_cover=fit_cover,
            is_image=is_image,
        )
        duration = max(1.0, min(60.0, float(photo_duration if is_image else video_duration)))
        cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y"]
        if is_image:
            cmd.extend(["-loop", "1", "-i", str(media_path)])
        else:
            cmd.extend(["-i", str(media_path)])
        cmd.extend(
            [
                "-filter_complex",
                filt,
                "-map",
                "[outv]",
            ]
        )
        if not is_image:
            cmd.extend(["-map", "0:a?"])
        cmd.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
            ]
        )
        if not is_image:
            cmd.extend(["-c:a", "aac", "-b:a", "128k"])
        cmd.extend(["-t", str(int(duration)), "-movflags", "+faststart", str(output_path)])
        _run_ffmpeg(cmd, timeout=600)
    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise ReelsEditorError("Vídeo não gerado.")
    return output_path
