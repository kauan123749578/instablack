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
from core.storage import get_storage
from models.models import User

router = APIRouter(prefix="/reels-editor", tags=["reels-editor"])

MAX_MEDIA_BYTES = 80 * 1024 * 1024
ALLOWED_MEDIA = {
    ".mp4",
    ".mov",
    ".webm",
    ".mkv",
    ".avi",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}


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
    fit_cover: str,
    photo_duration: float,
    video_duration: float,
) -> dict:
    return {
        "x_frac": max(0.0, min(1.0, float(x))),
        "y_frac": max(0.0, min(1.0, float(y))),
        "font_scale": max(0.35, min(2.5, float(font_scale or 1.0))),
        "text_color": (text_color or "yellow").strip() or "yellow",
        "border_color": (border_color or "black").strip() or "black",
        "border_width": max(0, min(6, int(border_width or 2))),
        "watermark_text": (watermark_text or "").strip(),
        "watermark_enabled": str(watermark_enabled).lower() in ("1", "true", "yes", "on"),
        "fit_cover": str(fit_cover).lower() in ("1", "true", "yes", "on", "cover"),
        "photo_duration": max(1.0, min(60.0, float(photo_duration or 8))),
        "video_duration": max(1.0, min(60.0, float(video_duration or 60))),
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
    x: float = Form(0.5),
    y: float = Form(0.5),
    font_scale: float = Form(1.0),
    text_color: str = Form("yellow"),
    border_color: str = Form("black"),
    border_width: int = Form(2),
    watermark_text: str = Form(""),
    watermark_enabled: str = Form("true"),
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
        fit_cover=fit_cover,
        photo_duration=photo_duration,
        video_duration=video_duration,
    )
    raw, ext = await _read_media(media)
    import tempfile

    with tempfile.TemporaryDirectory(prefix="ib-reels-in-") as td:
        src = Path(td) / f"source{ext}"
        src.write_bytes(raw)
        try:
            jpeg = render_preview_jpeg(
                src,
                text=text,
                x_frac=layout["x_frac"],
                y_frac=layout["y_frac"],
                font_scale=layout["font_scale"],
                text_color=layout["text_color"],
                border_color=layout["border_color"],
                border_width=layout["border_width"],
                watermark_text=layout["watermark_text"],
                watermark_enabled=layout["watermark_enabled"],
                fit_cover=layout["fit_cover"],
            )
        except ReelsEditorError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
    return Response(jpeg, media_type="image/jpeg")


@router.post("/render")
async def reels_editor_render_one(
    media: UploadFile = File(...),
    text: str = Form(""),
    x: float = Form(0.5),
    y: float = Form(0.5),
    font_scale: float = Form(1.0),
    text_color: str = Form("yellow"),
    border_color: str = Form("black"),
    border_width: int = Form(2),
    watermark_text: str = Form(""),
    watermark_enabled: str = Form("true"),
    fit_cover: str = Form("true"),
    photo_duration: float = Form(8),
    video_duration: float = Form(60),
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
        fit_cover=fit_cover,
        photo_duration=photo_duration,
        video_duration=video_duration,
    )
    raw, ext = await _read_media(media)
    import tempfile

    with tempfile.TemporaryDirectory(prefix="ib-reels-out-") as td:
        td_path = Path(td)
        src = td_path / f"source{ext}"
        src.write_bytes(raw)
        out = td_path / "reel.mp4"
        try:
            render_reel_mp4(
                src,
                out,
                text=text,
                x_frac=layout["x_frac"],
                y_frac=layout["y_frac"],
                font_scale=layout["font_scale"],
                text_color=layout["text_color"],
                border_color=layout["border_color"],
                border_width=layout["border_width"],
                watermark_text=layout["watermark_text"],
                watermark_enabled=layout["watermark_enabled"],
                fit_cover=layout["fit_cover"],
                photo_duration=layout["photo_duration"],
                video_duration=layout["video_duration"],
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
    media_files: list[UploadFile] = File(...),
    phrases_json: str = Form("[]"),
    x: float = Form(0.5),
    y: float = Form(0.5),
    font_scale: float = Form(1.0),
    text_color: str = Form("yellow"),
    border_color: str = Form("black"),
    border_width: int = Form(2),
    watermark_text: str = Form(""),
    watermark_enabled: str = Form("true"),
    fit_cover: str = Form("true"),
    photo_duration: float = Form(8),
    video_duration: float = Form(60),
    user: User = Depends(get_current_user),
):
    """Gera um ZIP com um reel por frase (rotação de mídias)."""
    _ = user
    try:
        phrases = json.loads(phrases_json or "[]")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, detail="Frases inválidas.") from exc
    if not isinstance(phrases, list) or not phrases:
        raise HTTPException(400, detail="Adicione pelo menos uma frase.")
    texts = [str(p.get("texto") if isinstance(p, dict) else p).strip() for p in phrases]
    texts = [t for t in texts if t]
    if not texts:
        raise HTTPException(400, detail="Nenhuma frase com texto.")

    if not media_files:
        raise HTTPException(400, detail="Envie pelo menos uma mídia de fundo.")

    layout = _parse_layout(
        x=x,
        y=y,
        font_scale=font_scale,
        text_color=text_color,
        border_color=border_color,
        border_width=border_width,
        watermark_text=watermark_text,
        watermark_enabled=watermark_enabled,
        fit_cover=fit_cover,
        photo_duration=photo_duration,
        video_duration=video_duration,
    )

    import tempfile

    media_blobs: list[tuple[bytes, str]] = []
    for mf in media_files[:30]:
        raw, ext = await _read_media(mf)
        media_blobs.append((raw, ext))
    if not media_blobs:
        raise HTTPException(400, detail="Mídia inválida.")

    zip_buf = io.BytesIO()
    with tempfile.TemporaryDirectory(prefix="ib-reels-batch-") as td:
        td_path = Path(td)
        with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for i, text in enumerate(texts):
                raw, ext = media_blobs[i % len(media_blobs)]
                src = td_path / f"in_{i}{ext}"
                out = td_path / f"reel_{i + 1}.mp4"
                src.write_bytes(raw)
                try:
                    render_reel_mp4(
                        src,
                        out,
                        text=text,
                        x_frac=layout["x_frac"],
                        y_frac=layout["y_frac"],
                        font_scale=layout["font_scale"],
                        text_color=layout["text_color"],
                        border_color=layout["border_color"],
                        border_width=layout["border_width"],
                        watermark_text=layout["watermark_text"],
                        watermark_enabled=layout["watermark_enabled"],
                        fit_cover=layout["fit_cover"],
                        photo_duration=layout["photo_duration"],
                        video_duration=layout["video_duration"],
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
