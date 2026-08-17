"""Editor de Reels — UI estilo Story Studio + render FFmpeg."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.deps import get_current_user
from app.templating import templates
from core.database import get_db
from core.reels_editor import ReelsEditorError, render_preview_jpeg, render_reel_mp4
from models.models import User

router = APIRouter(prefix="/reels-editor", tags=["reels-editor"])

MAX_MEDIA_BYTES = 80 * 1024 * 1024
MAX_AUDIO_BYTES = 25 * 1024 * 1024
ALLOWED_MEDIA = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_AUDIO = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}
EMOJI_CATALOG = Path(__file__).resolve().parents[1] / "static" / "reels-emojis" / "catalog.json"


def _parse_emojis(raw: str) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        s = str(item or "").strip()
        if s:
            out.append(s)
    return out[:12]


def _parse_layout(
    *,
    x: float,
    y: float,
    font_scale: float,
    text_color: str,
    border_color: str,
    border_width: int,
    watermark_text: str,
    watermark_enabled: str,
    watermark_x: float,
    watermark_y: float,
    fit_cover: str,
    photo_duration: float,
    video_duration: float,
    emojis_json: str = "[]",
    audio_mode: str = "replace",
    audio_volume: float = 1.0,
) -> dict:
    return {
        "x_frac": max(0.0, min(1.0, float(x))),
        "y_frac": max(0.0, min(1.0, float(y))),
        "font_scale": max(0.35, min(2.5, float(font_scale or 1.0))),
        "text_color": (text_color or "white").strip() or "white",
        "border_color": (border_color or "black").strip() or "black",
        "border_width": max(0, min(6, int(border_width or 2))),
        "watermark_text": (watermark_text or "").strip(),
        "watermark_enabled": str(watermark_enabled).lower() in ("1", "true", "yes", "on"),
        "watermark_x_frac": max(0.0, min(1.0, float(watermark_x))),
        "watermark_y_frac": max(0.0, min(1.0, float(watermark_y))),
        "fit_cover": str(fit_cover).lower() in ("1", "true", "yes", "on", "cover"),
        "photo_duration": max(1.0, min(60.0, float(photo_duration or 8))),
        "video_duration": max(1.0, min(60.0, float(video_duration or 60))),
        "emojis": _parse_emojis(emojis_json),
        "audio_mode": (audio_mode or "replace").strip().lower() or "replace",
        "audio_volume": max(0.0, min(2.0, float(audio_volume or 1.0))),
    }


async def _read_media(media: UploadFile) -> tuple[bytes, str]:
    raw = await media.read()
    if not raw:
        raise HTTPException(400, detail="Envie um vídeo ou imagem de fundo.")
    if len(raw) > MAX_MEDIA_BYTES:
        raise HTTPException(413, detail="Arquivo maior que 80 MB.")
    ext = Path(media.filename or "media.mp4").suffix.lower()
    if ext not in ALLOWED_MEDIA:
        raise HTTPException(400, detail="Formato não suportado.")
    return raw, ext


async def _read_audio(audio: UploadFile | None) -> tuple[bytes, str] | None:
    if audio is None or not audio.filename:
        return None
    raw = await audio.read()
    if not raw:
        return None
    if len(raw) > MAX_AUDIO_BYTES:
        raise HTTPException(413, detail="Áudio maior que 25 MB.")
    ext = Path(audio.filename or "music.mp3").suffix.lower()
    if ext not in ALLOWED_AUDIO:
        raise HTTPException(400, detail="Áudio: use MP3, M4A, AAC, WAV ou OGG.")
    return raw, ext


def _render_kwargs(layout: dict, *, text: str, emojis: list[str] | None = None) -> dict:
    return {
        "text": text,
        "emojis": emojis if emojis is not None else layout["emojis"],
        "x_frac": layout["x_frac"],
        "y_frac": layout["y_frac"],
        "font_scale": layout["font_scale"],
        "text_color": layout["text_color"],
        "border_color": layout["border_color"],
        "border_width": layout["border_width"],
        "watermark_text": layout["watermark_text"],
        "watermark_enabled": layout["watermark_enabled"],
        "watermark_x_frac": layout["watermark_x_frac"],
        "watermark_y_frac": layout["watermark_y_frac"],
        "fit_cover": layout["fit_cover"],
        "photo_duration": layout["photo_duration"],
        "video_duration": layout["video_duration"],
        "audio_mode": layout["audio_mode"],
        "audio_volume": layout["audio_volume"],
    }


@router.get("/emojis/catalog")
def reels_emoji_catalog(user: User = Depends(get_current_user)):
    _ = user
    if not EMOJI_CATALOG.exists():
        return []
    try:
        return json.loads(EMOJI_CATALOG.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


@router.get("")
def reels_editor_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ = db
    return templates.TemplateResponse(
        "reels_editor.html",
        {
            "request": request,
            "user": user,
        },
    )


@router.post("/preview")
async def reels_editor_preview(
    media: UploadFile = File(...),
    text: str = Form(""),
    emojis_json: str = Form("[]"),
    x: float = Form(0.5),
    y: float = Form(0.5),
    font_scale: float = Form(1.0),
    text_color: str = Form("white"),
    border_color: str = Form("black"),
    border_width: int = Form(2),
    watermark_text: str = Form(""),
    watermark_enabled: str = Form("true"),
    watermark_x: float = Form(0.5),
    watermark_y: float = Form(0.88),
    fit_cover: str = Form("true"),
    photo_duration: float = Form(8),
    video_duration: float = Form(60),
    user: User = Depends(get_current_user),
):
    _ = user
    layout = _parse_layout(
        x=x,
        y=y,
        font_scale=font_scale,
        text_color=text_color,
        border_color=border_color,
        border_width=border_width,
        watermark_text=watermark_text,
        watermark_enabled=watermark_enabled,
        watermark_x=watermark_x,
        watermark_y=watermark_y,
        fit_cover=fit_cover,
        photo_duration=photo_duration,
        video_duration=video_duration,
        emojis_json=emojis_json,
    )
    raw, ext = await _read_media(media)
    import tempfile

    with tempfile.TemporaryDirectory(prefix="ib-reels-in-") as td:
        src = Path(td) / f"source{ext}"
        src.write_bytes(raw)
        try:
            jpeg = render_preview_jpeg(src, **_render_kwargs(layout, text=text))
        except ReelsEditorError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
    return Response(jpeg, media_type="image/jpeg")


@router.post("/render")
async def reels_editor_render_one(
    media: UploadFile = File(...),
    text: str = Form(""),
    emojis_json: str = Form("[]"),
    x: float = Form(0.5),
    y: float = Form(0.5),
    font_scale: float = Form(1.0),
    text_color: str = Form("white"),
    border_color: str = Form("black"),
    border_width: int = Form(2),
    watermark_text: str = Form(""),
    watermark_enabled: str = Form("true"),
    watermark_x: float = Form(0.5),
    watermark_y: float = Form(0.88),
    fit_cover: str = Form("true"),
    photo_duration: float = Form(8),
    video_duration: float = Form(60),
    audio_mode: str = Form("replace"),
    audio_volume: float = Form(1.0),
    audio: UploadFile | None = File(None),
    filename: str = Form("reel.mp4"),
    user: User = Depends(get_current_user),
):
    _ = user
    layout = _parse_layout(
        x=x,
        y=y,
        font_scale=font_scale,
        text_color=text_color,
        border_color=border_color,
        border_width=border_width,
        watermark_text=watermark_text,
        watermark_enabled=watermark_enabled,
        watermark_x=watermark_x,
        watermark_y=watermark_y,
        fit_cover=fit_cover,
        photo_duration=photo_duration,
        video_duration=video_duration,
        emojis_json=emojis_json,
        audio_mode=audio_mode,
        audio_volume=audio_volume,
    )
    raw, ext = await _read_media(media)
    audio_blob = await _read_audio(audio)
    import tempfile

    with tempfile.TemporaryDirectory(prefix="ib-reels-out-") as td:
        td_path = Path(td)
        src = td_path / f"source{ext}"
        src.write_bytes(raw)
        audio_path = None
        if audio_blob:
            ab, aext = audio_blob
            audio_path = td_path / f"music{aext}"
            audio_path.write_bytes(ab)
        out = td_path / "reel.mp4"
        try:
            render_reel_mp4(
                src,
                out,
                audio_path=audio_path,
                **_render_kwargs(layout, text=text),
            )
        except ReelsEditorError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        safe_name = Path(filename or "reel.mp4").name
        if not safe_name.lower().endswith(".mp4"):
            safe_name = f"{safe_name}.mp4"
        return Response(
            out.read_bytes(),
            media_type="video/mp4",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
        )


@router.post("/render-batch")
async def reels_editor_render_batch(
    media: UploadFile = File(...),
    phrases_json: str = Form("[]"),
    emojis_json: str = Form("[]"),
    x: float = Form(0.5),
    y: float = Form(0.5),
    font_scale: float = Form(1.0),
    text_color: str = Form("white"),
    border_color: str = Form("black"),
    border_width: int = Form(2),
    watermark_text: str = Form(""),
    watermark_enabled: str = Form("true"),
    watermark_x: float = Form(0.5),
    watermark_y: float = Form(0.88),
    fit_cover: str = Form("true"),
    photo_duration: float = Form(8),
    video_duration: float = Form(60),
    audio_mode: str = Form("replace"),
    audio_volume: float = Form(1.0),
    audio: UploadFile | None = File(None),
    user: User = Depends(get_current_user),
):
    """Gera um ZIP com um reel por frase usando a mesma mídia de fundo."""
    _ = user
    try:
        phrases = json.loads(phrases_json or "[]")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, detail="Frases inválidas.") from exc
    if not isinstance(phrases, list) or not phrases:
        raise HTTPException(400, detail="Adicione pelo menos uma frase.")

    parsed: list[tuple[str, list[str]]] = []
    default_emojis = _parse_emojis(emojis_json)
    for p in phrases:
        if isinstance(p, dict):
            txt = str(p.get("texto") or "").strip()
            em = p.get("emojis")
            emojis = [str(e).strip() for e in em if str(e).strip()] if isinstance(em, list) else default_emojis
        else:
            txt = str(p).strip()
            emojis = default_emojis
        if txt:
            parsed.append((txt, emojis[:12]))
    if not parsed:
        raise HTTPException(400, detail="Nenhuma frase com texto.")

    layout = _parse_layout(
        x=x,
        y=y,
        font_scale=font_scale,
        text_color=text_color,
        border_color=border_color,
        border_width=border_width,
        watermark_text=watermark_text,
        watermark_enabled=watermark_enabled,
        watermark_x=watermark_x,
        watermark_y=watermark_y,
        fit_cover=fit_cover,
        photo_duration=photo_duration,
        video_duration=video_duration,
        emojis_json=emojis_json,
        audio_mode=audio_mode,
        audio_volume=audio_volume,
    )

    raw, ext = await _read_media(media)
    audio_blob = await _read_audio(audio)
    import tempfile

    zip_buf = io.BytesIO()
    with tempfile.TemporaryDirectory(prefix="ib-reels-batch-") as td:
        td_path = Path(td)
        src = td_path / f"source{ext}"
        src.write_bytes(raw)
        audio_path = None
        if audio_blob:
            ab, aext = audio_blob
            audio_path = td_path / f"music{aext}"
            audio_path.write_bytes(ab)
        with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for i, (text, emojis) in enumerate(parsed):
                out = td_path / f"reel_{i + 1}.mp4"
                try:
                    render_reel_mp4(
                        src,
                        out,
                        audio_path=audio_path,
                        **_render_kwargs(layout, text=text, emojis=emojis),
                    )
                except ReelsEditorError as exc:
                    raise HTTPException(400, detail=f"Frase {i + 1}: {exc}") from exc
                zf.write(out, arcname=f"reel_{i + 1}.mp4")

    zip_buf.seek(0)
    return StreamingResponse(
        zip_buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="reels_gerados.zip"'},
    )
