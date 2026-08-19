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
    import os

    env_font = os.environ.get("REELS_FONT_PATH", "").strip()
    candidates = []
    if env_font:
        candidates.append(Path(env_font))
    candidates += [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
        Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
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


def _escape_drawtext(text: str) -> str:
    """Escapa uma string para uso direto no parâmetro text= do drawtext."""
    return (
        text
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace("\n", " ")
    )


def _ffmpeg_color(raw: str, *, fallback: str = "white") -> str:
    color = (raw or fallback).strip() or fallback
    if color.startswith("#") and len(color) >= 7:
        return f"0x{color[1:7].upper()}"
    return color


def calculate_optimal_font_size(
    text: str,
    width: int,
    height: int,
    *,
    font_scale: float = 1.0,
    min_font_size: int = 30,
    max_font_size: int = 80,
) -> int:
    """Tamanho de fonte automático (mesma lógica do editor_reels de referência)."""
    lines = [line for line in (text or "").split("\n") if line.strip()]
    if not lines:
        return min_font_size
    max_line_length = max(len(line) for line in lines)
    num_lines = len(lines)
    available_width = int(width * 0.9)
    font_size_by_width = int(available_width / max(max_line_length, 1) * 1.2)
    available_height = int(height * 0.6)
    font_size_by_height = int(available_height / max(num_lines * 1.5, 1))
    font_size = min(font_size_by_width, font_size_by_height)
    scaled = int(font_size * max(0.35, min(2.5, float(font_scale or 1.0))))
    return max(min_font_size, min(scaled, max_font_size))


def _font_size(height: int, font_scale: float, text: str, width: int = 1080) -> int:
    return calculate_optimal_font_size(text, width, height, font_scale=font_scale)


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
    emoji_count: int = 0,
    video_duration: float = 60.0,
) -> str:
    width = 1080
    height = 1920
    y_frac = max(0.08, min(0.92, float(y_frac)))
    wm_x = max(0.05, min(0.95, float(watermark_x_frac)))
    raw_text = text_file.read_text(encoding="utf-8", errors="replace").strip()
    lines = [line for line in raw_text.split("\n") if line.strip()] or [""]
    fs = _font_size(height, font_scale, raw_text, width)
    font = _font_path_escaped()
    font_part = f"fontfile='{font}':" if font else ""
    border_w = max(0, min(6, int(border_width or 2)))
    color = _ffmpeg_color(text_color, fallback="yellow")
    border = _ffmpeg_color(border_color, fallback="black")
    line_sp = max(4, int(fs * 0.3))

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

    total_text_height = len(lines) * (fs + line_sp)
    center_y = int(height * y_frac)
    start_y = max(10, center_y - total_text_height // 2)

    current = "base"
    label_counter = 0
    parts = [scale]

    for i, line in enumerate(lines):
        label_counter += 1
        next_label = f"lt{label_counter}"
        if not line.strip():
            parts.append(f"[{current}]copy[{next_label}];")
            current = next_label
            continue
        esc = _escape_drawtext(line)
        line_y = start_y + i * (fs + line_sp)
        parts.append(
            f"[{current}]drawtext={font_part}"
            f"text='{esc}':"
            f"fontsize={fs}:"
            f"fontcolor={color}:"
            f"borderw={border_w}:"
            f"bordercolor={border}@0.95:"
            f"shadowcolor=black@0.45:shadowx=2:shadowy=2:"
            f"x=(w-text_w)/2:"
            f"y={line_y}[{next_label}];"
        )
        current = next_label

    last = parts.pop()
    last = last[: last.rfind("[")] + "[txt];"
    parts.append(last)
    current = "txt"
    label_counter += 1
    emoji_y: int | None = None
    emoji_size = max(28, int(fs * 1.3))

    if emoji_count > 0:
        emoji_spacing = int(emoji_size * 0.15)
        total_w = emoji_count * emoji_size + max(0, emoji_count - 1) * emoji_spacing
        start_x = max(0, int(width / 2 - total_w / 2))
        emoji_y = start_y + total_text_height + int(fs * 0.8)
        even = emoji_size if emoji_size % 2 == 0 else emoji_size + 1
        for idx in range(emoji_count):
            input_idx = idx + 1  # input 0 = vídeo
            emoji_label = f"em{idx}"
            next_label = f"v{label_counter}"
            label_counter += 1
            emoji_x = start_x + idx * (emoji_size + emoji_spacing)
            parts.append(f"[{input_idx}:v]scale={even}:{even}[{emoji_label}];")
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
        wm_fs = max(14, int(height * 0.012))
        wm_label = f"v{label_counter}"
        label_counter += 1
        if emoji_count > 0 and emoji_y is not None:
            watermark_y = emoji_y + emoji_size + int(fs * 0.5)
        else:
            watermark_y = start_y + total_text_height + int(fs * 0.6)
        use_auto_wm = abs(float(watermark_y_frac) - 0.88) < 0.02
        if use_auto_wm:
            wm_x_expr = "(w-text_w)/2"
            wm_y_expr = str(watermark_y)
        else:
            wm_x_expr = f"(w*{wm_x:.4f})-(text_w/2)"
            wm_y_expr = f"(h*{max(0.08, min(0.96, float(watermark_y_frac))):.4f})-(text_h/2)"
        parts.append(
            f"[{current}]drawtext={font_part}"
            f"text='{wm}':"
            f"fontsize={wm_fs}:"
            f"fontcolor=white:"
            f"borderw=1:"
            f"bordercolor=black@0.85:"
            f"x={wm_x_expr}:"
            f"y={wm_y_expr}[{wm_label}];"
        )
        current = wm_label

    fade_duration = 0.5
    clip_duration = max(1.0, float(video_duration or 60.0))
    fade_in_label = f"v{label_counter}"
    fade_out_label = "outv"
    parts.append(f"[{current}]fade=t=in:st=0:d={fade_duration}[{fade_in_label}];")
    parts.append(
        f"[{fade_in_label}]fade=t=out:st={max(0.0, clip_duration - fade_duration)}:"
        f"d={fade_duration}[{fade_out_label}];"
    )
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
    video_duration: float = 60.0,
) -> tuple[str, str, list[Path]]:
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
        emoji_count=len(emoji_pngs),
        video_duration=video_duration,
    )
    return filt, text_only, emoji_pngs


def render_reel_mp4(
    media_path: Path,
    output_path: Path,
    *,
    text: str,
    emojis: list[str] | None = None,
    x_frac: float = 0.5,
    y_frac: float = 0.5,
    font_scale: float = 1.0,
    text_color: str = "yellow",
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
        duration = max(1.0, min(60.0, float(photo_duration if is_image else video_duration)))
        filt, _, emoji_pngs = _layout_kwargs(
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
            video_duration=duration,
        )
        filter_file = td_path / "filter.txt"
        filter_file.write_text(filt, encoding="utf-8")

        cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y"]
        if is_image:
            cmd.extend(["-loop", "1", "-i", str(media_path)])
        else:
            cmd.extend(["-i", str(media_path)])
        for ep in emoji_pngs:
            cmd.extend(["-i", str(ep)])

        audio_input_idx: int | None = None
        if has_music:
            audio_input_idx = 1 + len(emoji_pngs)
            cmd.extend(["-i", str(audio_path)])

        if has_music and mode == "mix" and has_video_audio:
            filt_audio = (
                f"[0:a]volume=0.35[a0];[{audio_input_idx}:a]volume={vol:.2f}[a1];"
                "[a0][a1]amix=inputs=2:duration=first:dropout_transition=2[aout];"
            )
            filter_file.write_text(filt + filt_audio, encoding="utf-8")
            cmd.extend(["-filter_complex_script", str(filter_file), "-map", "[outv]", "-map", "[aout]"])
        else:
            cmd.extend(["-filter_complex_script", str(filter_file), "-map", "[outv]"])
            if has_music and audio_input_idx is not None:
                cmd.extend(["-map", f"{audio_input_idx}:a"])
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
