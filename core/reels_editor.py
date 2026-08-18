"""Editor de Reels — overlay de texto via FFmpeg (layout estilo Story Studio)."""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from app.config import settings

VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v", ".mkv", ".avi"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
EMOJI_DIR = Path(__file__).resolve().parents[1] / "app" / "static" / "reels-emojis"
_EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF"
    r"\U0001F100-\U0001F1FF\U0001F600-\U0001F64F\U0001F000-\U0001F0FF"
    r"\U0001F200-\U0001F2FF\U00002700-\U000027BF]+",
    re.UNICODE,
)


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
    cmd = [
        "ffprobe",
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
    return (raw or "").replace("\\n", "\n").replace("/n", "\n").strip()


def _ffmpeg_color(raw: str, *, fallback: str = "white") -> str:
    color = (raw or fallback).strip() or fallback
    if color.startswith("#") and len(color) >= 7:
        return f"0x{color[1:7].upper()}"
    return color


def _font_size(height: int, font_scale: float) -> int:
    base = height * 0.045 * max(0.35, min(2.5, float(font_scale or 1.0)))
    return int(max(22, min(120, round(base))))


def strip_emojis(text: str) -> str:
    cleaned = _EMOJI_RE.sub("", text or "")
    lines = []
    for line in cleaned.split("\n"):
        line = re.sub(r" +", " ", line.strip())
        lines.append(line)
    return "\n".join(lines).strip()


def _has_audio_stream(media_path: Path) -> bool:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        str(media_path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=20)
        return "audio" in (out.stdout or "").lower()
    except Exception:
        return False


def _resolve_emoji_pngs(emojis: list[str]) -> list[Path]:
    paths: list[Path] = []
    for raw in emojis:
        name = Path((raw or "").strip()).name
        if not name.lower().endswith(".png"):
            continue
        png = EMOJI_DIR / name
        if png.exists() and png.is_file():
            paths.append(png)
    return paths


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
    watermark_x_frac: float,
    watermark_y_frac: float,
    fit_cover: bool,
    is_image: bool,
    emoji_pngs: list[Path] | None = None,
) -> str:
    x_frac = max(0.05, min(0.95, float(x_frac)))
    y_frac = max(0.08, min(0.92, float(y_frac)))
    wm_x = max(0.05, min(0.95, float(watermark_x_frac)))
    wm_y = max(0.08, min(0.96, float(watermark_y_frac)))
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
    color = _ffmpeg_color(text_color, fallback="white")
    border = _ffmpeg_color(border_color, fallback="black")

    parts = [
        scale,
        f"[base]drawtext={font_part}"
        f"textfile='{text_path}':"
        f"fontsize={fs}:"
        f"fontcolor={color}:"
        f"borderw={border_w}:"
        f"bordercolor={border}@0.95:"
        f"shadowcolor=black@0.45:shadowx=2:shadowy=2:"
        f"x={x_expr}:"
        f"y={y_expr}:"
        f"line_spacing={max(4, fs // 6)}[txt];",
    ]

    current = "txt"
    label_counter = 1
    emoji_size = max(28, int(fs * 1.25))
    emoji_y_frac = min(0.92, y_frac + 0.07)
    emoji_paths = emoji_pngs or []

    if emoji_paths:
        total_w = len(emoji_paths) * emoji_size + max(0, len(emoji_paths) - 1) * int(
            emoji_size * 0.12
        )
        start_x = int(width * x_frac - total_w / 2)
        for idx, png in enumerate(emoji_paths):
            emoji_label = f"em{idx}"
            next_label = f"v{label_counter}"
            label_counter += 1
            emoji_x = start_x + idx * (emoji_size + int(emoji_size * 0.12))
            emoji_y = int(height * emoji_y_frac)
            png_path = str(png.resolve()).replace("\\", "/").replace(":", "\\:")
            parts.append(f"movie='{png_path}',scale={emoji_size}:-1[{emoji_label}];")
            parts.append(
                f"[{current}][{emoji_label}]overlay="
                f"x={emoji_x}:y={emoji_y}[{next_label}];"
            )
            current = next_label

    if watermark_enabled and (watermark_text or "").strip():
        wm = (
            (watermark_text or "")
            .replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace(":", "\\:")
        )
        wm_fs = max(14, fs // 3)
        wm_label = f"v{label_counter}"
        parts.append(
            f"[{current}]drawtext={font_part}"
            f"text='{wm}':"
            f"fontsize={wm_fs}:"
            f"fontcolor=white:"
            f"borderw=1:"
            f"bordercolor=black@0.85:"
            f"x=(w*{wm_x:.4f})-(text_w/2):"
            f"y=(h*{wm_y:.4f})-(text_h/2)[{wm_label}];"
        )
        current = wm_label

    parts.append(f"[{current}]copy[outv];")
    return "".join(parts)


def _layout_kwargs(
    *,
    text: str,
    emojis: list[str] | None,
    x_frac: float,
    y_frac: float,
    font_scale: float,
    text_color: str,
    border_color: str,
    border_width: int,
    watermark_text: str,
    watermark_enabled: bool,
    watermark_x_frac: float,
    watermark_y_frac: float,
    fit_cover: bool,
    is_image: bool,
    cache_dir: Path,
) -> tuple[str, str]:
    text_only = strip_emojis(_normalize_text(text))
    text_file = cache_dir / "overlay.txt"
    text_file.write_text(text_only, encoding="utf-8")
    emoji_pngs = _resolve_emoji_pngs(emojis or [])
    filt = build_overlay_filter(
        text_file=text_file,
        width=1080,
        height=1920,
        x_frac=x_frac,
        y_frac=y_frac,
        font_scale=font_scale,
        text_color=text_color,
        border_color=border_color,
        border_width=border_width,
        watermark_text=watermark_text,
        watermark_enabled=watermark_enabled,
        watermark_x_frac=watermark_x_frac,
        watermark_y_frac=watermark_y_frac,
        fit_cover=fit_cover,
        is_image=is_image,
        emoji_pngs=emoji_pngs,
    )
    return filt, text_only


def render_reel_mp4(
    media_path: Path,
    output_path: Path,
    *,
    text: str,
    emojis: list[str] | None = None,
    x_frac: float = 0.5,
    y_frac: float = 0.5,
    font_scale: float = 1.0,
    text_color: str = "white",
    border_color: str = "black",
    border_width: int = 2,
    watermark_text: str = "",
    watermark_enabled: bool = True,
    watermark_x_frac: float = 0.5,
    watermark_y_frac: float = 0.88,
    fit_cover: bool = True,
    photo_duration: float = 8.0,
    video_duration: float = 60.0,
    audio_path: Path | None = None,
    audio_mode: str = "replace",
    audio_volume: float = 1.0,
) -> Path:
    is_image = is_image_path(media_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    has_music = bool(audio_path and audio_path.exists())
    mode = (audio_mode or "replace").strip().lower()
    vol = max(0.0, min(2.0, float(audio_volume or 1.0)))
    has_video_audio = (not is_image) and _has_audio_stream(media_path)

    with tempfile.TemporaryDirectory(prefix="ib-reels-render-") as td:
        td_path = Path(td)
        filt, _ = _layout_kwargs(
            text=text,
            emojis=emojis,
            x_frac=x_frac,
            y_frac=y_frac,
            font_scale=font_scale,
            text_color=text_color,
            border_color=border_color,
            border_width=border_width,
            watermark_text=watermark_text,
            watermark_enabled=watermark_enabled,
            watermark_x_frac=watermark_x_frac,
            watermark_y_frac=watermark_y_frac,
            fit_cover=fit_cover,
            is_image=is_image,
            cache_dir=td_path,
        )
        duration = max(1.0, min(60.0, float(photo_duration if is_image else video_duration)))
        cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y"]
        if is_image:
            cmd.extend(["-loop", "1", "-i", str(media_path)])
        else:
            cmd.extend(["-i", str(media_path)])
        if has_music:
            cmd.extend(["-i", str(audio_path)])

        if has_music and mode == "mix" and has_video_audio:
            filt = (
                filt.replace("[outv];", "[outv];")
                + f"[0:a]volume=0.35[a0];[1:a]volume={vol:.2f}[a1];"
                + "[a0][a1]amix=inputs=2:duration=first:dropout_transition=2[aout];"
            )
            cmd.extend(["-filter_complex", filt, "-map", "[outv]", "-map", "[aout]"])
        else:
            cmd.extend(["-filter_complex", filt, "-map", "[outv]"])
            if has_music:
                cmd.extend(["-map", "1:a"])
            elif has_video_audio:
                cmd.extend(["-map", "0:a"])

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
        if has_music or has_video_audio:
            cmd.extend(["-c:a", "aac", "-b:a", "128k"])
        cmd.extend(["-t", str(int(duration)), "-shortest", "-movflags", "+faststart", str(output_path)])
        _run_ffmpeg(cmd, timeout=600)

    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise ReelsEditorError("Vídeo não gerado.")
    return output_path
