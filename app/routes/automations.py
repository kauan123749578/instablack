"""CRUD de automações recorrentes."""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from PIL import Image, ImageOps
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.deps import get_current_user, get_effective_user
from app.templating import templates
from app.utils.calendar_schedule import (
    days_to_json,
    format_calendar_times_label,
    next_calendar_run,
    parse_calendar_days,
    parse_calendar_times,
    times_to_storage,
)
from app.utils.anti_farm import (
    DEFAULT_STAGGER_MAX,
    DEFAULT_STAGGER_MIN,
    account_publish_countdown,
    captions_from_form,
    captions_textarea_value,
    captions_to_json,
    clamp_stagger_minutes,
    resolve_caption,
    resolve_stagger_config,
)
from app.utils.automation_schedule import (
    parse_jitter_enabled,
    parse_jitter_minutes,
    parse_posts_per_batch,
    parse_rest_minutes,
)
from app.utils.automation_videos import (
    is_video_filename,
    media_key_referenced_elsewhere,
    media_keys_for_automation,
    parse_videos_json,
    videos_to_json,
)
from app.utils.intervals import (
    ALLOWED_INTERVALS,
    META_MIN_INTERVAL,
    META_WARMUP_DAYS,
    META_WARMUP_MIN_INTERVAL,
    interval_label,
    validate_interval_for_accounts,
)
from celery_app.tasks.publish import publish_once, publish_to_account
from core.anti_farm_prefs import get_anti_farm_prefs
from core.database import get_db
from core.storage import get_storage
from core.web_cookies import web_cookies_status
from models.models import Automation, InstagramAccount, PublishLog, User

router = APIRouter(prefix="/automations", tags=["automations"])
log = logging.getLogger(__name__)

CONTENT_TYPES = ["reel", "story", "photo"]
VISIBLE_ACCOUNT_STATUSES = ("active", "paused", "needs_login", "proxy_down", "banned")
MAX_REEL_UPLOAD_FILES = 300
DIRECT_UPLOAD_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".m4v": "video/x-m4v",
    ".mkv": "video/x-matroska",
}


def _story_link_value(content_type: str, story_link: str) -> str | None:
    if content_type != "story":
        return None
    link = story_link.strip()
    return link or None


def _story_sticker_text_value(content_type: str, story_sticker_text: str) -> str | None:
    if content_type != "story":
        return None
    text = (story_sticker_text or "").strip()
    if not text:
        return None
    return text[:60]


def _collect_upload_files(form, *, field_names: tuple[str, ...]) -> list[UploadFile]:
    """Lê todos os arquivos do multipart (FastAPI/Starlette às vezes entrega só 1 via File())."""
    out: list[UploadFile] = []
    seen: set[int] = set()
    for name in field_names:
        for item in form.getlist(name):
            if not hasattr(item, "filename") or not item.filename:
                continue
            oid = id(item)
            if oid in seen:
                continue
            seen.add(oid)
            out.append(item)  # type: ignore[arg-type]
    return out


def _save_uploaded_videos(
    storage,
    files: list[UploadFile],
    *,
    allow_duplicates: bool = False,
) -> tuple[list[dict[str, str]], list[str]]:
    """Materializa cada upload sem carregar o arquivo inteiro em RAM."""
    entries: list[dict[str, str]] = []
    warnings: list[str] = []
    seen_hash: set[str] = set()
    for f in files:
        if not f.filename:
            continue
        try:
            f.file.seek(0)
        except Exception:
            pass
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = f.file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
        if total <= 0:
            warnings.append(f"“{f.filename}” estava vazio e foi ignorado")
            log.warning("Upload vazio ignorado: %s", f.filename)
            continue
        file_hash = digest.hexdigest()
        if not allow_duplicates and file_hash in seen_hash:
            warnings.append(f"“{f.filename}” é igual a outro arquivo da lista (duplicado) e foi ignorado")
            log.warning("Vídeo duplicado ignorado: %s", f.filename)
            continue
        seen_hash.add(file_hash)
        ext = Path(f.filename).suffix.lower() or ".mp4"
        try:
            f.file.seek(0)
        except Exception:
            pass
        key = storage.save(f.file, suggested_ext=ext)
        entries.append({
            "video_key": key,
            "video_original_name": f.filename,
        })
        log.info("Vídeo salvo %s: %s → %s (%s bytes)", len(entries), f.filename, key, total)
    return entries, warnings


def _save_thumb(storage, thumb: UploadFile | None) -> tuple[str | None, str | None]:
    if not thumb or not thumb.filename:
        return None, None
    try:
        thumb.file.seek(0)
    except Exception:
        pass
    try:
        with Image.open(thumb.file) as source:
            normalized = ImageOps.exif_transpose(source).convert("RGB")
            normalized = ImageOps.fit(
                normalized,
                (1080, 1920),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            output = io.BytesIO()
            normalized.save(
                output,
                format="JPEG",
                quality=92,
                optimize=True,
                progressive=False,
            )
            output.seek(0)
            return storage.save(output, suggested_ext=".jpg"), thumb.filename
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="A capa enviada não é uma imagem válida.") from exc


def _want_camouflage(enabled: str | None, cover: UploadFile | None) -> bool:
    """Ativa se o checkbox veio marcado OU se veio arquivo de capa."""
    if str(enabled or "").strip():
        return True
    return bool(cover and getattr(cover, "filename", None))


def _clamp_camouflage_opacity(raw: str | float | None) -> float:
    try:
        value = float(raw if raw is not None else 0.10)
    except (TypeError, ValueError):
        value = 0.10
    return max(0.01, min(0.40, value))


def _save_camouflage_cover(storage, cover: UploadFile | None) -> str | None:
    if not cover or not cover.filename:
        return None
    try:
        cover.file.seek(0)
    except Exception:
        pass
    try:
        with Image.open(cover.file) as source:
            normalized = ImageOps.exif_transpose(source).convert("RGB")
            output = io.BytesIO()
            normalized.save(output, format="JPEG", quality=90, optimize=True)
            output.seek(0)
            return storage.save(output, suggested_ext=".jpg")
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="A capa de camuflagem não é uma imagem válida.") from exc


def _activation_next_run(automation: Automation) -> dt.datetime:
    if automation.schedule_type == "calendar":
        days = parse_calendar_days(automation.calendar_days or "[]")
        nxt = next_calendar_run(days, automation.calendar_time or "")
        return nxt or dt.datetime.utcnow()
    return dt.datetime.utcnow()


def _schedule_humanize_fields(
    *,
    jitter_enabled: object = False,
    jitter_minutes: object = 10,
    posts_per_batch: object = 0,
    rest_minutes: object = 0,
    stagger_enabled: object = True,
    stagger_min_minutes: object = DEFAULT_STAGGER_MIN,
    stagger_max_minutes: object = DEFAULT_STAGGER_MAX,
    caption_rotate_by_account: object = True,
    caption_rotate_by_reel: object = False,
) -> dict[str, object]:
    lo, hi = clamp_stagger_minutes(stagger_min_minutes, stagger_max_minutes)
    if isinstance(stagger_enabled, bool):
        stagger_on = stagger_enabled
    else:
        stagger_on = str(stagger_enabled or "").strip().lower() in ("1", "on", "true", "yes")

    def _flag(raw: object) -> bool:
        if isinstance(raw, bool):
            return raw
        return str(raw or "").strip().lower() in ("1", "on", "true", "yes")

    return {
        "jitter_enabled": parse_jitter_enabled(jitter_enabled),
        "jitter_minutes": parse_jitter_minutes(jitter_minutes),
        "posts_per_batch": parse_posts_per_batch(posts_per_batch),
        "rest_minutes": parse_rest_minutes(rest_minutes),
        "posts_in_batch": 0,
        "stagger_enabled": stagger_on,
        "stagger_min_minutes": lo,
        "stagger_max_minutes": hi,
        "caption_rotate_by_account": _flag(caption_rotate_by_account),
        "caption_rotate_by_reel": _flag(caption_rotate_by_reel),
    }


@router.get("")
def list_automations(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    automations = db.scalars(
        select(Automation)
        .where(Automation.user_id == user.id)
        .options(selectinload(Automation.accounts))
        .order_by(desc(Automation.created_at))
    ).all()
    all_accounts = db.scalars(
        select(InstagramAccount).where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
        )
    ).all()
    return templates.TemplateResponse(
        "automations.html",
        {
            "request": request,
            "user": user,
            "automations": automations,
            "all_accounts": all_accounts,
            "intervals": ALLOWED_INTERVALS,
            "meta_min_interval": META_MIN_INTERVAL,
            "meta_warmup_days": META_WARMUP_DAYS,
            "meta_warmup_min_interval": META_WARMUP_MIN_INTERVAL,
            "captions_textarea_value": captions_textarea_value,
            "anti_farm_prefs": get_anti_farm_prefs(user),
        },
    )


@router.get("/new")
def new_automation_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    accounts = db.scalars(
        select(InstagramAccount)
        .where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
        )
        .order_by(InstagramAccount.username.asc())
    ).all()
    default_type = request.query_params.get("type", "reel")
    if default_type not in CONTENT_TYPES:
        default_type = "reel"
    err_key = request.query_params.get("error")
    err_msg = {
        "video": "Selecione pelo menos um vídeo .mp4. A capa (.png/.jpg) é só a thumbnail — não substitui o vídeo.",
    }.get(err_key or "")
    return templates.TemplateResponse(
        "new_automation.html",
        {
            "request": request,
            "user": user,
            "accounts": accounts,
            "intervals": ALLOWED_INTERVALS,
            "meta_min_interval": META_MIN_INTERVAL,
            "meta_warmup_days": META_WARMUP_DAYS,
            "meta_warmup_min_interval": META_WARMUP_MIN_INTERVAL,
            "anti_farm_prefs": get_anti_farm_prefs(user),
            "content_types": CONTENT_TYPES,
            "default_content_type": default_type,
            "error": err_msg,
        },
    )


@router.get("/new/story")
def new_story_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    """Atalho direto para criar automação de Story."""
    accounts = db.scalars(
        select(InstagramAccount)
        .where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
        )
        .order_by(InstagramAccount.username.asc())
    ).all()
    return templates.TemplateResponse(
        "new_automation.html",
        {
            "request": request,
            "user": user,
            "accounts": accounts,
            "intervals": ALLOWED_INTERVALS,
            "meta_min_interval": META_MIN_INTERVAL,
            "meta_warmup_days": META_WARMUP_DAYS,
            "meta_warmup_min_interval": META_WARMUP_MIN_INTERVAL,
            "anti_farm_prefs": get_anti_farm_prefs(user),
            "content_types": CONTENT_TYPES,
            "default_content_type": "story",
            "error": None,
        },
    )


def _parse_story_layout_form(
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    rotation: float,
    variant: str,
    cover: str,
    draw_sticker: str = "false",
) -> dict:
    for name, value in {"x": x, "y": y, "width": width, "height": height}.items():
        if not 0 < value <= 1:
            raise HTTPException(400, detail=f"{name} precisa estar entre 0 e 1")
    if not -2 <= rotation <= 2:
        raise HTTPException(400, detail="rotation inválida")
    variant = (variant or "default").strip().lower()
    allowed = {
        "default",
        "white",
        "rainbow",
        "solid",
        "brand",
        "black-text",
        "white-text",
    }
    if variant not in allowed:
        raise HTTPException(400, detail="Estilo de sticker inválido")
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "rotation": rotation,
        "variant": variant,
        "cover": str(cover).lower() in {"1", "true", "yes", "on"},
        "draw_sticker": str(draw_sticker).lower() in {"1", "true", "yes", "on"},
    }


@router.get("/story-studio")
def story_studio_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    """Editor visual de Story com link (somente contas com cookies web)."""
    all_accounts = db.scalars(
        select(InstagramAccount)
        .where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.status.in_(("active", "paused")),
        )
        .order_by(InstagramAccount.username.asc())
    ).all()
    cookie_flags = {
        acc.id: web_cookies_status(acc.encrypted_web_cookies) for acc in all_accounts
    }
    accounts = [
        acc
        for acc in all_accounts
        if (acc.provider or "instagrapi") != "meta"
        and cookie_flags.get(acc.id, {}).get("has_csrftoken")
    ]
    return templates.TemplateResponse(
        "story_studio.html",
        {
            "request": request,
            "user": user,
            "accounts": accounts,
            "cookie_flags": cookie_flags,
        },
    )


@router.post("/story-studio/preview")
async def story_studio_preview(
    image: UploadFile = File(...),
    url: str = Form(...),
    text: str = Form(""),
    x: float = Form(0.5),
    y: float = Form(0.8),
    width: float = Form(0.6),
    height: float = Form(0.068625),
    rotation: float = Form(0.0),
    variant: str = Form("default"),
    cover: str = Form("false"),
    draw_sticker: str = Form("false"),
    account_id: int = Form(0),
    user: User = Depends(get_current_user),
):
    from core.story_web import normalize_story_url, prepare_story_image_with_link

    _ = account_id, user
    layout = _parse_story_layout_form(
        x=x,
        y=y,
        width=width,
        height=height,
        rotation=rotation,
        variant=variant,
        cover=cover,
        draw_sticker=draw_sticker,
    )
    try:
        normalized = normalize_story_url(url)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    raw = await image.read()
    if not raw:
        raise HTTPException(400, detail="Selecione uma imagem")
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(413, detail="Imagem maior que 25 MB")

    import tempfile

    with tempfile.TemporaryDirectory(prefix="ib-story-preview-") as temp_dir:
        source = Path(temp_dir) / "source.jpg"
        output = Path(temp_dir) / "preview.jpg"
        source.write_bytes(raw)
        prepare_story_image_with_link(
            source,
            output,
            url=normalized,
            sticker_text=text,
            x=layout["x"],
            y=layout["y"],
            width=layout["width"],
            height=layout["height"],
            cover=layout["cover"],
            variant=layout["variant"],
            draw_sticker=layout["draw_sticker"],
        )
        return Response(output.read_bytes(), media_type="image/jpeg")


@router.post("/story-studio/publish")
async def story_studio_publish(
    image: UploadFile = File(...),
    account_id: int = Form(...),
    url: str = Form(...),
    text: str = Form(""),
    x: float = Form(0.5),
    y: float = Form(0.8),
    width: float = Form(0.6),
    height: float = Form(0.068625),
    rotation: float = Form(0.0),
    variant: str = Form("default"),
    cover: str = Form("false"),
    draw_sticker: str = Form("false"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from core.story_web import normalize_story_url

    layout = _parse_story_layout_form(
        x=x,
        y=y,
        width=width,
        height=height,
        rotation=rotation,
        variant=variant,
        cover=cover,
        draw_sticker=draw_sticker,
    )
    try:
        normalized = normalize_story_url(url)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    account = db.scalar(
        select(InstagramAccount).where(
            InstagramAccount.id == account_id,
            InstagramAccount.user_id == user.id,
            InstagramAccount.status.in_(("active", "paused")),
        )
    )
    if account is None:
        raise HTTPException(400, detail="Conta inválida ou indisponível")
    if (account.provider or "instagrapi") == "meta":
        raise HTTPException(
            400,
            detail="Story com link visual exige conta com cookies web (não API oficial Meta).",
        )
    if not web_cookies_status(account.encrypted_web_cookies).get("has_csrftoken"):
        raise HTTPException(
            400,
            detail="Importe cookies JSON (Cookie-Editor) com sessionid + csrftoken nesta conta.",
        )

    raw = await image.read()
    if not raw:
        raise HTTPException(400, detail="Selecione uma imagem")
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(413, detail="Imagem maior que 25 MB")

    storage = get_storage()
    ext = Path(image.filename or "story.jpg").suffix.lower() or ".jpg"
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        ext = ".jpg"
    video_key = storage.save(io.BytesIO(raw), suggested_ext=ext)
    sticker_text = (text or "").strip()[:60] or None

    publish_once.apply_async(
        args=[
            account.id,
            video_key,
            None,
            "",
            "story",
            normalized,
            sticker_text,
            layout,
        ],
        countdown=0,
    )
    return {
        "ok": True,
        "message": f"Story enfileirado para @{account.username}. Acompanhe em Logs.",
        "redirect": "/logs?watch=1&n=1",
    }


@router.post("/story-studio/schedule")
async def story_studio_schedule(
    request: Request,
    url: str = Form(...),
    text: str = Form(""),
    x: float = Form(0.5),
    y: float = Form(0.8),
    width: float = Form(0.6),
    height: float = Form(0.068625),
    rotation: float = Form(0.0),
    variant: str = Form("default"),
    cover: str = Form("false"),
    draw_sticker: str = Form("false"),
    calendar_days: str = Form("[]"),
    calendar_time: str = Form("10:00"),
    name: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Agenda Stories com link (API web): multi-contas, multi-dias e multi-mídias."""
    import json

    from core.story_web import normalize_story_url

    layout = _parse_story_layout_form(
        x=x,
        y=y,
        width=width,
        height=height,
        rotation=rotation,
        variant=variant,
        cover=cover,
        draw_sticker=draw_sticker,
    )
    try:
        normalized = normalize_story_url(url)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    form = await request.form()
    account_ids: list[int] = []
    for raw in form.getlist("account_ids"):
        try:
            account_ids.append(int(str(raw)))
        except (TypeError, ValueError):
            continue
    account_ids = sorted(set(account_ids))
    if not account_ids:
        raise HTTPException(400, detail="Selecione pelo menos uma conta")

    accounts = db.scalars(
        select(InstagramAccount).where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.id.in_(account_ids),
            InstagramAccount.status.in_(("active", "paused")),
        )
    ).all()
    if len(accounts) != len(account_ids):
        raise HTTPException(400, detail="Uma ou mais contas são inválidas")
    for account in accounts:
        if (account.provider or "instagrapi") == "meta":
            raise HTTPException(
                400,
                detail=f"@{account.username}: Story com link exige cookies web (não Meta).",
            )
        if not web_cookies_status(account.encrypted_web_cookies).get("has_csrftoken"):
            raise HTTPException(
                400,
                detail=f"@{account.username}: importe cookies JSON com csrftoken (API web).",
            )

    days = parse_calendar_days(calendar_days)
    if not days:
        raise HTTPException(400, detail="Selecione pelo menos um dia do mês")

    cal_times: list[str] = []
    for raw_time in form.getlist("calendar_times") or [calendar_time]:
        cal_times.extend(parse_calendar_times(str(raw_time)))
    cal_times = sorted(set(cal_times))
    if not cal_times:
        raise HTTPException(400, detail="Informe pelo menos um horário")

    images = _collect_upload_files(form, field_names=("images", "image"))
    if not images:
        raise HTTPException(400, detail="Envie pelo menos uma foto")
    if len(images) > 30:
        raise HTTPException(400, detail="No máximo 30 mídias por agendamento")

    storage = get_storage()
    video_entries: list[dict[str, str]] = []
    for image in images:
        raw = await image.read()
        if not raw:
            continue
        if len(raw) > 25 * 1024 * 1024:
            raise HTTPException(413, detail=f"Imagem maior que 25 MB: {image.filename}")
        ext = Path(image.filename or "story.jpg").suffix.lower() or ".jpg"
        if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise HTTPException(400, detail=f"Formato inválido: {image.filename}")
        key = storage.save(io.BytesIO(raw), suggested_ext=ext)
        video_entries.append(
            {
                "video_key": key,
                "video_original_name": (image.filename or "story.jpg")[:512],
            }
        )
    if not video_entries:
        raise HTTPException(400, detail="Nenhuma imagem válida enviada")

    # Um horário por mídia quando há várias fotos (igual fluxo Story em /automations/new)
    if len(video_entries) > 1 and len(cal_times) == 1:
        base = cal_times[0]
        hour, minute = map(int, base.split(":"))
        expanded: list[str] = []
        for i in range(len(video_entries)):
            total = hour * 60 + minute + i * 30
            expanded.append(f"{(total // 60) % 24:02d}:{total % 60:02d}")
        cal_times = expanded
    elif len(video_entries) > 1 and len(cal_times) < len(video_entries):
        raise HTTPException(
            400,
            detail=(
                f"Com {len(video_entries)} mídias, informe {len(video_entries)} horários "
                "ou deixe só um (gera +30 min por mídia)."
            ),
        )

    sticker_text = (text or "").strip()[:60] or None
    auto_name = (name or "").strip() or f"Story Studio · {dt.datetime.now().strftime('%d/%m %H:%M')}"
    time_stored = times_to_storage(cal_times)
    nxt = next_calendar_run(days, time_stored) or dt.datetime.utcnow()

    automation = Automation(
        user_id=user.id,
        name=auto_name[:255],
        content_type="story",
        caption="",
        story_link=normalized,
        story_sticker_text=sticker_text,
        story_layout_json=json.dumps(layout, ensure_ascii=False),
        video_key=video_entries[0]["video_key"],
        video_original_name=video_entries[0]["video_original_name"],
        videos_json=videos_to_json(video_entries),
        schedule_type="calendar",
        start_mode="calendar",
        calendar_days=days_to_json(days),
        calendar_time=time_stored,
        interval_minutes=1440,
        status="active",
        next_run_at=nxt,
        current_index=0,
    )
    automation.accounts = list(accounts)
    db.add(automation)
    db.commit()

    return {
        "ok": True,
        "message": (
            f"Agendado: {len(video_entries)} story(s) · {len(accounts)} conta(s) · "
            f"dias {', '.join(str(d) for d in days)}. Acompanhe em Automações."
        ),
        "redirect": "/automations?ok=calendar",
        "automation_id": automation.id,
    }


@router.get("/media-library")
def media_library(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    automations = db.scalars(
        select(Automation)
        .where(Automation.user_id == user.id)
        .order_by(desc(Automation.created_at))
    ).all()
    return templates.TemplateResponse(
        "media_library.html",
        {"request": request, "user": user, "automations": automations},
    )


@router.get("/new/calendar")
def new_calendar_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    accounts = db.scalars(
        select(InstagramAccount)
        .where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
        )
        .order_by(InstagramAccount.username.asc())
    ).all()
    return templates.TemplateResponse(
        "new_calendar_automation.html",
        {
            "request": request,
            "user": user,
            "accounts": accounts,
            "error": None,
        },
    )


@router.post("/new/calendar")
async def create_calendar_automation(
    request: Request,
    name: str = Form(...),
    content_type: str = Form("reel"),
    caption: str = Form(""),
    story_link: str = Form(""),
    calendar_days: str = Form(""),
    calendar_time: str = Form(""),
    calendar_times: list[str] = Form(default=[]),
    account_ids: list[int] = Form(default=[]),
    video: UploadFile = File(...),
    thumb: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    days = parse_calendar_days(calendar_days)
    times = parse_calendar_times(",".join(calendar_times) if calendar_times else calendar_time)
    time_stored = times_to_storage(times)
    error: str | None = None
    if content_type not in CONTENT_TYPES:
        error = "Tipo de conteúdo inválido."
    elif content_type != "reel":
        error = "Agendamento por calendário é apenas para Reels."
    elif not days:
        error = "Selecione pelo menos um dia do mês."
    elif not times:
        error = "Informe pelo menos um horário de publicação."
    elif not video or not video.filename:
        error = "Envie o arquivo de mídia."

    accounts: list[InstagramAccount] = []
    if not error:
        accounts = list(db.scalars(
            select(InstagramAccount).where(
                InstagramAccount.user_id == user.id,
                InstagramAccount.id.in_(account_ids),
                InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
            )
        ).all())
        if len(accounts) != len(set(account_ids)):
            error = "Alguma conta selecionada não existe."
        elif any((acc.provider or "instagrapi") == "meta" for acc in accounts):
            if Path(video.filename or "").suffix.lower() != ".mp4":
                error = "A API oficial aceita Reels em MP4."

    if error:
        all_accounts = db.scalars(
            select(InstagramAccount).where(
                InstagramAccount.user_id == user.id,
                InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
            )
        ).all()
        return templates.TemplateResponse(
            "new_calendar_automation.html",
            {
                "request": request,
                "user": user,
                "accounts": all_accounts,
                "error": error,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    has_accounts = bool(accounts)
    storage = get_storage()
    ext = Path(video.filename).suffix or ".mp4"
    video_key = storage.save(video.file, suggested_ext=ext)

    thumb_key = None
    thumb_original_name = None
    if thumb and thumb.filename:
        thumb_ext = Path(thumb.filename).suffix or ".jpg"
        thumb_key = storage.save(thumb.file, suggested_ext=thumb_ext)
        thumb_original_name = thumb.filename

    nxt = next_calendar_run(days, time_stored) or dt.datetime.utcnow()

    automation = Automation(
        user_id=user.id,
        name=name.strip(),
        content_type=content_type,
        caption=caption,
        video_key=video_key,
        video_original_name=video.filename,
        thumb_key=thumb_key,
        thumb_original_name=thumb_original_name,
        story_link=_story_link_value(content_type, story_link),
        schedule_type="calendar",
        start_mode="calendar",
        calendar_days=days_to_json(days),
        calendar_time=time_stored,
        interval_minutes=1440,
        status="active" if has_accounts else "paused",
        next_run_at=nxt if has_accounts else None,
    )
    automation.accounts = accounts
    db.add(automation)
    db.commit()
    return RedirectResponse(
        f"/automations?ok={'calendar' if has_accounts else 'draft'}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/new")
async def create_automation(
    request: Request,
    name: str = Form(...),
    content_type: str = Form("reel"),
    caption: str = Form(""),
    captions_alt: list[str] = Form(default=[]),
    story_link: str = Form(""),
    story_sticker_text: str = Form(""),
    schedule_mode: str = Form("recurring"),
    interval_minutes: int = Form(60),
    calendar_days: str = Form(""),
    calendar_time: str = Form("10:00"),
    calendar_times: list[str] = Form(default=[]),
    account_ids: list[int] = Form(default=[]),
    video_count: int = Form(0),
    jitter_enabled: str = Form(""),
    jitter_minutes: int = Form(10),
    posts_per_batch: int = Form(0),
    rest_minutes: int = Form(0),
    stagger_enabled: str = Form(""),
    stagger_min_minutes: int = Form(DEFAULT_STAGGER_MIN),
    stagger_max_minutes: int = Form(DEFAULT_STAGGER_MAX),
    caption_rotate_by_account: str = Form(""),
    caption_rotate_by_reel: str = Form(""),
    thumb: UploadFile | None = File(None),
    camouflage_enabled: str = Form(""),
    camouflage_cover: UploadFile | None = File(None),
    camouflage_opacity_pct: str = Form("15"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    humanize = _schedule_humanize_fields(
        jitter_enabled=jitter_enabled,
        jitter_minutes=jitter_minutes,
        posts_per_batch=posts_per_batch,
        rest_minutes=rest_minutes,
        stagger_enabled=stagger_enabled,
        stagger_min_minutes=stagger_min_minutes,
        stagger_max_minutes=stagger_max_minutes,
        caption_rotate_by_account=caption_rotate_by_account,
        caption_rotate_by_reel=caption_rotate_by_reel,
    )
    captions_json = captions_to_json(captions_from_form(captions_alt))
    submitted_cal_times: list[str] = []
    for raw_time in (calendar_times or [calendar_time]):
        submitted_cal_times.extend(parse_calendar_times(raw_time))
    cal_times = sorted(set(submitted_cal_times))
    cal_time_stored = times_to_storage(cal_times)
    error: str | None = None
    if content_type not in CONTENT_TYPES:
        error = "Tipo de conteúdo inválido."
    elif schedule_mode not in ("now", "recurring", "calendar"):
        error = "Modo de publicação inválido."
    elif schedule_mode == "recurring" and interval_minutes not in ALLOWED_INTERVALS:
        error = "Intervalo inválido."
    elif schedule_mode == "calendar":
        days = parse_calendar_days(calendar_days)
        if not days:
            error = "Selecione pelo menos um dia do mês."
        elif not cal_times:
            error = "Informe pelo menos um horário de publicação."

    # Só via request.form() — evita perder arquivos do input multiple
    form = await request.form()
    upload_files: list[UploadFile] = []
    if not error:
        if content_type == "reel":
            upload_files = _collect_upload_files(form, field_names=("videos", "video"))
            if not upload_files:
                error = "Envie pelo menos um vídeo Reels (.mp4)."
            elif len(upload_files) > MAX_REEL_UPLOAD_FILES:
                error = (
                    f"Selecione no máximo {MAX_REEL_UPLOAD_FILES} vídeos por criação. "
                    "Crie em lotes menores para evitar timeout do servidor."
                )
            else:
                bad = [f.filename for f in upload_files if not is_video_filename(f.filename)]
                if bad:
                    error = f"Arquivo inválido (precisa ser vídeo .mp4): {', '.join(bad)}"
                elif video_count > 1 and len(upload_files) < video_count:
                    error = (
                        f"Só chegaram {len(upload_files)} de {video_count} vídeos no servidor. "
                        "Tente de novo (arquivos menores) ou envie em lotes."
                    )
        else:
            upload_files = _collect_upload_files(form, field_names=("video", "videos"))
            if not upload_files:
                error = "Envie o arquivo de mídia."
            elif content_type == "photo":
                upload_files = upload_files[:1]
            elif content_type == "story" and len(upload_files) > 30:
                error = "Selecione no máximo 30 mídias por automação de Stories."
            elif content_type == "story":
                allowed_story_ext = {
                    ".jpg", ".jpeg", ".png", ".webp", ".heic",
                    ".mp4", ".mov", ".webm",
                }
                bad = [
                    f.filename
                    for f in upload_files
                    if Path(f.filename or "").suffix.lower() not in allowed_story_ext
                ]
                if bad:
                    error = f"Formato inválido para Story: {', '.join(bad)}"

    if not error and content_type == "story" and schedule_mode == "calendar":
        if len(submitted_cal_times) != len(upload_files):
            error = (
                "Escolha um horário para cada mídia do Story "
                f"({len(upload_files)} mídia(s), {len(submitted_cal_times)} horário(s))."
            )
        elif len(cal_times) != len(submitted_cal_times):
            error = "Cada Story precisa de um horário diferente."
        else:
            # O scheduler percorre horários em ordem crescente. Mantém a mídia
            # correspondente na mesma ordem para Story 1→12h, Story 2→18h etc.
            ordered = sorted(
                zip(submitted_cal_times, upload_files),
                key=lambda pair: pair[0],
            )
            cal_times = [pair[0] for pair in ordered]
            upload_files = [pair[1] for pair in ordered]
            cal_time_stored = times_to_storage(cal_times)

    accounts: list[InstagramAccount] = []
    if not error:
        accounts = list(db.scalars(
            select(InstagramAccount).where(
                InstagramAccount.user_id == user.id,
                InstagramAccount.id.in_(account_ids),
                InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
            )
        ).all())
        if len(accounts) != len(set(account_ids)):
            error = "Alguma conta selecionada não existe."
        elif schedule_mode == "recurring":
            iv_err = validate_interval_for_accounts(
                interval_minutes,
                accounts,
                meta_warmup_enabled=get_anti_farm_prefs(user).get("meta_warmup_enabled", True),
            )
            if iv_err:
                error = iv_err

    if error:
        all_accounts = db.scalars(
            select(InstagramAccount).where(
                InstagramAccount.user_id == user.id,
                InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
            )
        ).all()
        return templates.TemplateResponse(
            "new_automation.html",
            {
                "request": request,
                "user": user,
                "accounts": all_accounts,
                "intervals": ALLOWED_INTERVALS,
                "meta_min_interval": META_MIN_INTERVAL,
                "meta_warmup_days": META_WARMUP_DAYS,
                "meta_warmup_min_interval": META_WARMUP_MIN_INTERVAL,
                "anti_farm_prefs": get_anti_farm_prefs(user),
                "content_types": CONTENT_TYPES,
                "default_content_type": content_type if content_type in CONTENT_TYPES else "reel",
                "error": error,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    storage = get_storage()
    video_entries, upload_warnings = _save_uploaded_videos(
        storage,
        upload_files,
        allow_duplicates=content_type == "story",
    )
    if not video_entries:
        all_accounts = db.scalars(
            select(InstagramAccount).where(
                InstagramAccount.user_id == user.id,
                InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
            )
        ).all()
        msg = "Nenhum vídeo válido foi salvo. "
        if upload_warnings:
            msg += " ".join(upload_warnings)
        else:
            msg += "Selecione arquivos .mp4 de novo."
        return templates.TemplateResponse(
            "new_automation.html",
            {
                "request": request,
                "user": user,
                "accounts": all_accounts,
                "intervals": ALLOWED_INTERVALS,
                "meta_min_interval": META_MIN_INTERVAL,
                "meta_warmup_days": META_WARMUP_DAYS,
                "meta_warmup_min_interval": META_WARMUP_MIN_INTERVAL,
                "anti_farm_prefs": get_anti_farm_prefs(user),
                "content_types": CONTENT_TYPES,
                "default_content_type": content_type if content_type in CONTENT_TYPES else "reel",
                "error": msg,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if upload_warnings:
        log.warning(
            "Upload playlist avisos user=%s: %s (salvos=%s)",
            user.id,
            upload_warnings,
            len(video_entries),
        )
    video_key = video_entries[0]["video_key"]
    if len(video_entries) == 1:
        video_original_name = video_entries[0]["video_original_name"]
    else:
        video_original_name = f"{len(video_entries)} vídeos"
    # Sempre persiste a lista completa — sem isso o worker republica só o 1º
    videos_json = videos_to_json(video_entries)
    log.info(
        "Nova automação user=%s playlist=%s arquivos=%s",
        user.id,
        len(video_entries),
        [e["video_original_name"] for e in video_entries],
    )

    thumb_key, thumb_original_name = _save_thumb(storage, thumb)
    want_camu = _want_camouflage(camouflage_enabled, camouflage_cover) and content_type == "reel"
    camouflage_cover_key = None
    camouflage_opacity = 0.25
    if want_camu:
        if not camouflage_cover or not camouflage_cover.filename:
            error = "Marcou aplicar camuflagem — envie a imagem de camuflagem."
        else:
            camouflage_cover_key = _save_camouflage_cover(storage, camouflage_cover)
            camouflage_opacity = _clamp_camouflage_opacity(
                float(camouflage_opacity_pct or "25") / 100.0
            )
    warn_q = f"&warn={len(upload_warnings)}" if upload_warnings else ""
    has_accounts = bool(accounts)

    if error:
        all_accounts = db.scalars(
            select(InstagramAccount).where(
                InstagramAccount.user_id == user.id,
                InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
            )
        ).all()
        return templates.TemplateResponse(
            "new_automation.html",
            {
                "request": request,
                "user": user,
                "accounts": all_accounts,
                "intervals": ALLOWED_INTERVALS,
                "meta_min_interval": META_MIN_INTERVAL,
                "meta_warmup_days": META_WARMUP_DAYS,
                "meta_warmup_min_interval": META_WARMUP_MIN_INTERVAL,
                "anti_farm_prefs": get_anti_farm_prefs(user),
                "content_types": CONTENT_TYPES,
                "default_content_type": content_type if content_type in CONTENT_TYPES else "reel",
                "error": error,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if schedule_mode == "now" and has_accounts:
        countdown = 0
        n_accounts = len(accounts)
        prefs = get_anti_farm_prefs(user)
        use_stagger = bool(humanize["stagger_enabled"]) and bool(prefs.get("stagger_enabled", True))
        stagger_lo = int(humanize["stagger_min_minutes"])  # type: ignore[arg-type]
        stagger_hi = int(humanize["stagger_max_minutes"])  # type: ignore[arg-type]
        by_acc = bool(humanize["caption_rotate_by_account"]) and bool(
            prefs.get("caption_rotate_by_account", True)
        )
        by_reel = bool(humanize["caption_rotate_by_reel"]) and bool(
            prefs.get("caption_rotate_by_reel", False)
        )
        # stub mínimo para resolve_caption
        class _Cap:
            caption = caption
            captions_json = captions_json

        for v_idx, entry in enumerate(video_entries):
            for acc_idx, acc in enumerate(accounts):
                acc_caption = resolve_caption(
                    _Cap(),  # type: ignore[arg-type]
                    account_slot=acc_idx,
                    reel_index=v_idx,
                    by_account=by_acc,
                    by_reel=by_reel,
                )
                stagger = (
                    account_publish_countdown(
                        acc_idx,
                        n_accounts,
                        min_minutes=stagger_lo,
                        max_minutes=stagger_hi,
                    )
                    if use_stagger
                    else 0
                )
                publish_once.apply_async(
                    args=[
                        acc.id,
                        entry["video_key"],
                        thumb_key,
                        acc_caption,
                        content_type,
                        _story_link_value(content_type, story_link),
                        _story_sticker_text_value(content_type, story_sticker_text),
                    ],
                    kwargs={
                        "camouflage_cover_key": camouflage_cover_key,
                        "camouflage_opacity": camouflage_opacity,
                    },
                    countdown=countdown + stagger,
                )
            if len(video_entries) > 1:
                wave = (
                    account_publish_countdown(
                        max(n_accounts - 1, 0),
                        n_accounts,
                        min_minutes=stagger_lo,
                        max_minutes=stagger_hi,
                    )
                    if use_stagger
                    else 0
                )
                countdown += wave + max(90, 60)
        return RedirectResponse(
            f"/logs?watch=1&n={len(video_entries)}{warn_q}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    now = dt.datetime.utcnow()

    if schedule_mode == "calendar":
        days = parse_calendar_days(calendar_days)
        nxt = next_calendar_run(days, cal_time_stored) or now
        automation = Automation(
            user_id=user.id,
            name=name.strip(),
            content_type=content_type,
            caption=caption,
            captions_json=captions_json,
            video_key=video_key,
            video_original_name=video_original_name,
            videos_json=videos_json,
            current_index=0,
            thumb_key=thumb_key,
            thumb_original_name=thumb_original_name,
            camouflage_cover_key=camouflage_cover_key,
            camouflage_opacity=camouflage_opacity,
            story_link=_story_link_value(content_type, story_link),
            story_sticker_text=_story_sticker_text_value(content_type, story_sticker_text),
            schedule_type="calendar",
            start_mode="calendar",
            calendar_days=days_to_json(days),
            calendar_time=cal_time_stored,
            interval_minutes=1440,
            status="active" if has_accounts else "paused",
            next_run_at=nxt if has_accounts else None,
            **humanize,
        )
        automation.accounts = accounts
        db.add(automation)
        db.commit()
        return RedirectResponse(
            f"/automations?ok={'calendar' if has_accounts else 'draft'}&n={len(video_entries)}{warn_q}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    automation = Automation(
        user_id=user.id,
        name=name.strip(),
        content_type=content_type,
        caption=caption,
        captions_json=captions_json,
        video_key=video_key,
        video_original_name=video_original_name,
        videos_json=videos_json,
        current_index=0,
        thumb_key=thumb_key,
        thumb_original_name=thumb_original_name,
        camouflage_cover_key=camouflage_cover_key,
        camouflage_opacity=camouflage_opacity,
        story_link=_story_link_value(content_type, story_link),
        story_sticker_text=_story_sticker_text_value(content_type, story_sticker_text),
        schedule_type="interval",
        start_mode="recurring",
        interval_minutes=interval_minutes,
        status="active" if has_accounts else "paused",
        next_run_at=now if has_accounts else None,
        **humanize,
    )
    automation.accounts = accounts
    db.add(automation)
    db.commit()
    return RedirectResponse(
        f"/automations?ok={'1' if has_accounts else 'draft'}&n={len(video_entries)}{warn_q}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/new/reel-draft")
async def create_reel_upload_draft(
    name: str = Form(...),
    content_type: str = Form("reel"),
    caption: str = Form(""),
    captions_alt: list[str] = Form(default=[]),
    story_link: str = Form(""),
    schedule_mode: str = Form("recurring"),
    interval_minutes: int = Form(60),
    calendar_days: str = Form(""),
    calendar_times: list[str] = Form(default=[]),
    account_ids: list[int] = Form(default=[]),
    jitter_enabled: str = Form(""),
    jitter_minutes: int = Form(10),
    posts_per_batch: int = Form(0),
    rest_minutes: int = Form(0),
    stagger_enabled: str = Form(""),
    stagger_min_minutes: int = Form(DEFAULT_STAGGER_MIN),
    stagger_max_minutes: int = Form(DEFAULT_STAGGER_MAX),
    caption_rotate_by_account: str = Form(""),
    caption_rotate_by_reel: str = Form(""),
    thumb: UploadFile | None = File(None),
    camouflage_enabled: str = Form(""),
    camouflage_cover: UploadFile | None = File(None),
    camouflage_opacity_pct: str = Form("15"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Cria primeiro o rascunho leve; os vídeos chegam depois em lotes."""
    if content_type != "reel":
        return JSONResponse({"error": "Upload em lotes disponível apenas para Reels."}, status_code=400)
    if schedule_mode not in ("now", "recurring", "calendar"):
        return JSONResponse({"error": "Modo de publicação inválido."}, status_code=400)
    if schedule_mode == "recurring" and interval_minutes not in ALLOWED_INTERVALS:
        return JSONResponse({"error": "Intervalo inválido."}, status_code=400)

    cal_days: list[int] = []
    cal_time_stored = ""
    if schedule_mode == "calendar":
        cal_days = parse_calendar_days(calendar_days)
        if not cal_days:
            return JSONResponse({"error": "Selecione pelo menos um dia do mês."}, status_code=400)
        cal_times = parse_calendar_times(",".join(calendar_times) if calendar_times else "10:00")
        cal_time_stored = times_to_storage(cal_times)

    accounts = list(db.scalars(
        select(InstagramAccount).where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.id.in_(account_ids),
            InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
        )
    ).all())
    if len(accounts) != len(set(account_ids)):
        return JSONResponse({"error": "Alguma conta selecionada não existe."}, status_code=400)
    if schedule_mode == "recurring":
        iv_err = validate_interval_for_accounts(
            interval_minutes,
            accounts,
            meta_warmup_enabled=get_anti_farm_prefs(user).get("meta_warmup_enabled", True),
        )
        if iv_err:
            return JSONResponse({"error": iv_err}, status_code=400)

    humanize = _schedule_humanize_fields(
        jitter_enabled=jitter_enabled,
        jitter_minutes=jitter_minutes,
        posts_per_batch=posts_per_batch,
        rest_minutes=rest_minutes,
        stagger_enabled=stagger_enabled,
        stagger_min_minutes=stagger_min_minutes,
        stagger_max_minutes=stagger_max_minutes,
        caption_rotate_by_account=caption_rotate_by_account,
        caption_rotate_by_reel=caption_rotate_by_reel,
    )
    captions_json = captions_to_json(captions_from_form(captions_alt))
    storage = get_storage()
    thumb_key, thumb_original_name = _save_thumb(storage, thumb)
    want_camu = _want_camouflage(camouflage_enabled, camouflage_cover)
    camouflage_cover_key = None
    camouflage_opacity = 0.25
    if want_camu:
        if not camouflage_cover or not camouflage_cover.filename:
            return JSONResponse(
                {"error": "Marcou aplicar camuflagem — envie a imagem de camuflagem."},
                status_code=400,
            )
        camouflage_cover_key = _save_camouflage_cover(storage, camouflage_cover)
        camouflage_opacity = _clamp_camouflage_opacity(
            float(camouflage_opacity_pct or "25") / 100.0
        )
    log.info(
        "reel-draft camuflagem user=%s enabled=%s key=%s opacity=%.2f",
        user.id,
        want_camu,
        camouflage_cover_key,
        camouflage_opacity,
    )
    automation = Automation(
        user_id=user.id,
        name=name.strip(),
        content_type="reel",
        caption=caption,
        captions_json=captions_json,
        video_key="",
        video_original_name="0 vídeos",
        videos_json=videos_to_json([]),
        current_index=0,
        thumb_key=thumb_key,
        thumb_original_name=thumb_original_name,
        camouflage_cover_key=camouflage_cover_key,
        camouflage_opacity=camouflage_opacity,
        story_link=_story_link_value(content_type, story_link),
        schedule_type="calendar" if schedule_mode == "calendar" else "interval",
        start_mode=schedule_mode,
        calendar_days=days_to_json(cal_days) if schedule_mode == "calendar" else None,
        calendar_time=cal_time_stored or None,
        interval_minutes=1440 if schedule_mode == "calendar" else interval_minutes,
        status="paused",
        next_run_at=None,
        **humanize,
    )
    automation.accounts = accounts
    db.add(automation)
    db.commit()
    return {"ok": True, "automation_id": automation.id}


@router.post("/{automation_id}/upload-batch")
async def upload_reel_batch(
    automation_id: int,
    videos: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    a = _get_owned(db, automation_id, user)
    if a.content_type != "reel":
        return JSONResponse({"error": "Esta automação não é de Reels."}, status_code=400)
    if not videos:
        return JSONResponse({"error": "Nenhum vídeo recebido neste lote."}, status_code=400)

    bad = [f.filename for f in videos if not is_video_filename(f.filename)]
    if any((acc.provider or "instagrapi") == "meta" for acc in a.accounts):
        bad.extend(
            f.filename
            for f in videos
            if Path(f.filename or "").suffix.lower() != ".mp4"
        )
    if bad:
        return JSONResponse(
            {"error": f"Arquivo inválido (use vídeo .mp4): {', '.join(dict.fromkeys(bad))}"},
            status_code=400,
        )

    # Salva no R2/disco antes do lock — uploads paralelos não se pisam no storage.
    storage = get_storage()
    new_entries, upload_warnings = _save_uploaded_videos(storage, videos)
    if not new_entries:
        return {
            "ok": True,
            "saved": 0,
            "total": len(parse_videos_json(a.videos_json)),
            "warnings": upload_warnings,
        }

    # Lock na linha: vários uploads ao mesmo tempo não sobrescrevem a playlist.
    locked = db.execute(
        select(Automation)
        .where(Automation.id == automation_id, Automation.user_id == user.id)
        .with_for_update()
    ).scalar_one()
    existing = parse_videos_json(locked.videos_json)
    existing.extend(new_entries)
    locked.videos_json = videos_to_json(existing)
    locked.video_key = existing[0]["video_key"]
    locked.video_original_name = (
        f"{len(existing)} vídeos" if len(existing) > 1 else existing[0]["video_original_name"]
    )
    db.commit()
    return {
        "ok": True,
        "saved": len(new_entries),
        "total": len(existing),
        "warnings": upload_warnings,
    }


@router.post("/{automation_id}/direct-upload-urls")
async def create_direct_upload_urls(
    automation_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Cria URLs temporárias para o navegador enviar os vídeos direto ao R2."""
    a = _get_owned(db, automation_id, user)
    if a.content_type != "reel":
        return JSONResponse({"error": "Esta automação não é de Reels."}, status_code=400)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Dados do upload inválidos."}, status_code=400)
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, list) or not files:
        return JSONResponse({"error": "Nenhum vídeo informado."}, status_code=400)
    if len(files) > MAX_REEL_UPLOAD_FILES:
        return JSONResponse(
            {"error": f"Envie no máximo {MAX_REEL_UPLOAD_FILES} vídeos por seleção."},
            status_code=400,
        )

    storage = get_storage()
    prefix = f"videos/direct/{user.id}/{a.id}/"
    uploads: list[dict[str, object]] = []
    for item in files:
        if not isinstance(item, dict):
            return JSONResponse({"error": "Lista de arquivos inválida."}, status_code=400)
        name = str(item.get("name") or "").strip()
        ext = Path(name).suffix.lower()
        if not name or ext not in DIRECT_UPLOAD_CONTENT_TYPES:
            return JSONResponse({"error": f"Arquivo de vídeo inválido: {name or '?'}"}, status_code=400)
        if (
            any((acc.provider or "instagrapi") == "meta" for acc in a.accounts)
            and ext != ".mp4"
        ):
            return JSONResponse(
                {"error": f"A API oficial aceita Reels em MP4: {name}"},
                status_code=400,
            )
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            return JSONResponse({"error": f"Arquivo vazio: {name}"}, status_code=400)

        content_type = DIRECT_UPLOAD_CONTENT_TYPES[ext]
        key = storage.allocate_key(f"{prefix}{uuid.uuid4().hex}{ext}")
        try:
            url = storage.presign_upload(key, content_type, expires_in=3600)
        except NotImplementedError:
            return JSONResponse(
                {"error": "Upload direto requer STORAGE_BACKEND=s3.", "fallback": True},
                status_code=409,
            )
        uploads.append({
            "key": key,
            "name": name,
            "content_type": content_type,
            "url": url,
        })
    return {"ok": True, "uploads": uploads}


@router.post("/{automation_id}/register-direct-uploads")
async def register_direct_uploads(
    automation_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Registra, na ordem selecionada, os objetos já enviados diretamente ao R2."""
    a = _get_owned(db, automation_id, user)
    if a.content_type != "reel":
        return JSONResponse({"error": "Esta automação não é de Reels."}, status_code=400)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Dados do upload inválidos."}, status_code=400)
    items = payload.get("uploads") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        return JSONResponse({"error": "Nenhum vídeo enviado para registrar."}, status_code=400)
    if len(items) > MAX_REEL_UPLOAD_FILES:
        return JSONResponse({"error": "Quantidade de vídeos inválida."}, status_code=400)

    prefix = f"videos/direct/{user.id}/{a.id}/"
    allowed_prefixes = (prefix, f"b2/{prefix}")
    new_entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            return JSONResponse({"error": "Lista de uploads inválida."}, status_code=400)
        key = str(item.get("key") or "")
        name = str(item.get("name") or "").strip()
        if (
            not any(key.startswith(p) for p in allowed_prefixes)
            or key in seen
            or not is_video_filename(name)
        ):
            return JSONResponse({"error": "Referência de upload inválida."}, status_code=400)
        seen.add(key)
        new_entries.append({"video_key": key, "video_original_name": name})

    locked = db.execute(
        select(Automation)
        .where(Automation.id == automation_id, Automation.user_id == user.id)
        .with_for_update()
    ).scalar_one()
    existing = parse_videos_json(locked.videos_json)
    existing.extend(new_entries)
    locked.videos_json = videos_to_json(existing)
    locked.video_key = existing[0]["video_key"]
    locked.video_original_name = (
        f"{len(existing)} vídeos" if len(existing) > 1 else existing[0]["video_original_name"]
    )
    db.commit()
    return {"ok": True, "saved": len(new_entries), "total": len(existing)}


@router.post("/{automation_id}/upload-finish")
def finish_reel_batch_upload(
    automation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    a = _get_owned(db, automation_id, user)
    entries = parse_videos_json(a.videos_json)
    if not entries:
        return JSONResponse({"error": "Nenhum vídeo foi salvo nessa automação."}, status_code=400)
    a.video_key = entries[0]["video_key"]
    a.video_original_name = f"{len(entries)} vídeos" if len(entries) > 1 else entries[0]["video_original_name"]
    a.current_index = 0
    accounts = list(a.accounts)
    start_mode = a.start_mode or (
        "calendar" if a.schedule_type == "calendar" else "recurring"
    )

    if not accounts:
        a.status = "paused"
        a.next_run_at = None
    elif start_mode == "now":
        # Upload concluído não significa publicação concluída. A task só muda
        # para completed depois de todas as respostas de sucesso das contas.
        a.status = "active"
        a.next_run_at = None
    elif start_mode == "calendar":
        a.status = "active"
        a.next_run_at = next_calendar_run(
            parse_calendar_days(a.calendar_days or "[]"),
            a.calendar_time or "",
        ) or dt.datetime.utcnow()
    else:
        a.status = "active"
        a.next_run_at = dt.datetime.utcnow()
    db.commit()

    if accounts and start_mode == "now":
        countdown = 0
        n_accounts = len(accounts)
        prefs = get_anti_farm_prefs(user)
        use_stagger, stagger_lo, stagger_hi = resolve_stagger_config(a, prefs)
        by_acc = bool(prefs.get("caption_rotate_by_account", True)) and bool(
            getattr(a, "caption_rotate_by_account", True)
        )
        by_reel = bool(prefs.get("caption_rotate_by_reel", False)) and bool(
            getattr(a, "caption_rotate_by_reel", False)
        )
        for index, entry in enumerate(entries):
            for account_index, account in enumerate(accounts):
                stagger = (
                    account_publish_countdown(
                        account_index,
                        n_accounts,
                        min_minutes=stagger_lo,
                        max_minutes=stagger_hi,
                    )
                    if use_stagger
                    else 0
                )
                publish_to_account.apply_async(
                    args=[a.id, account.id, entry["video_key"], index],
                    kwargs={"account_slot": account_index if by_acc else 0},
                    countdown=countdown + stagger,
                )
            wave = (
                account_publish_countdown(
                    max(n_accounts - 1, 0),
                    n_accounts,
                    min_minutes=stagger_lo,
                    max_minutes=stagger_hi,
                )
                if use_stagger
                else 0
            )
            countdown += wave + max(90, 60)
        redirect = f"/logs?watch=1&n={len(entries)}"
    else:
        state = "1" if accounts else "draft"
        redirect = f"/automations?ok={state}&n={len(entries)}"

    return {
        "ok": True,
        "total": len(entries),
        "redirect": redirect,
    }


def _get_owned(db: Session, automation_id: int, user: User) -> Automation:
    a = db.get(Automation, automation_id)
    if not a or a.user_id != user.id:
        raise HTTPException(status_code=404, detail="Automação não encontrada")
    return a


@router.post("/{automation_id}/pause")
def pause_automation(
    automation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    a = _get_owned(db, automation_id, user)
    a.status = "paused"
    db.commit()
    return RedirectResponse("/automations", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{automation_id}/resume")
def resume_automation(
    automation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from sqlalchemy import text

    from app.utils.automation_videos import playlist_items

    a = _get_owned(db, automation_id, user)
    if not a.accounts:
        return RedirectResponse("/automations?error=no_accounts", status_code=status.HTTP_303_SEE_OTHER)
    items = playlist_items(a)
    if not items:
        return RedirectResponse("/automations?error=no_videos", status_code=status.HTTP_303_SEE_OTHER)

    # Se terminou a playlist, recomeça do zero (como postagemIG ciclo novo)
    if a.status == "completed" or (len(items) > 1 and int(a.current_index or 0) >= len(items)):
        db.execute(
            text("UPDATE automations SET current_index = 0, status = 'active' WHERE id = :id"),
            {"id": a.id},
        )
        a.current_index = 0

    a.status = "active"
    a.next_run_at = _activation_next_run(a)
    db.commit()
    return RedirectResponse("/automations", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{automation_id}/skip-video")
def skip_playlist_video(
    automation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Força pular o vídeo atual da playlist."""
    from sqlalchemy import text

    from app.utils.automation_videos import playlist_items

    a = _get_owned(db, automation_id, user)
    items = playlist_items(a)
    if len(items) <= 1:
        return RedirectResponse("/automations", status_code=status.HTTP_303_SEE_OTHER)

    cur = int(a.current_index or 0)
    new_idx = cur + 1
    if new_idx >= len(items):
        db.execute(
            text(
                "UPDATE automations SET current_index = :idx, status = 'completed', "
                "next_run_at = NULL WHERE id = :id"
            ),
            {"idx": new_idx, "id": a.id},
        )
    else:
        db.execute(
            text(
                "UPDATE automations SET current_index = :idx, status = 'active', "
                "next_run_at = :nxt WHERE id = :id"
            ),
            {"idx": new_idx, "id": a.id, "nxt": dt.datetime.utcnow()},
        )
    db.commit()
    return RedirectResponse("/automations?skipped=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{automation_id}/reset-playlist")
def reset_playlist(
    automation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Volta a playlist para o 1º vídeo (índice 0) e reativa."""
    from sqlalchemy import text

    from app.utils.automation_videos import playlist_items

    a = _get_owned(db, automation_id, user)
    items = playlist_items(a)
    if len(items) <= 1:
        return RedirectResponse("/automations", status_code=status.HTTP_303_SEE_OTHER)

    db.execute(
        text(
            "UPDATE automations SET current_index = 0, status = 'active', "
            "next_run_at = :nxt WHERE id = :id"
        ),
        {"id": a.id, "nxt": dt.datetime.utcnow()},
    )
    db.commit()
    return RedirectResponse("/automations?reset=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{automation_id}/duplicate")
def duplicate_automation(
    automation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Clona a automação reusando as mesmas keys de vídeo/capa no storage."""
    src = db.execute(
        select(Automation)
        .where(Automation.id == automation_id)
        .options(selectinload(Automation.accounts))
    ).scalar_one_or_none()
    if not src or src.user_id != user.id:
        raise HTTPException(status_code=404, detail="Automação não encontrada")

    base_name = (src.name or "Automação").strip()
    copy_name = f"{base_name} (cópia)"
    if len(copy_name) > 255:
        copy_name = copy_name[:255]

    clone = Automation(
        user_id=user.id,
        name=copy_name,
        content_type=src.content_type or "reel",
        caption=src.caption or "",
        captions_json=getattr(src, "captions_json", None),
        story_link=src.story_link,
        story_sticker_text=src.story_sticker_text,
        story_layout_json=getattr(src, "story_layout_json", None),
        video_key=src.video_key,
        video_original_name=src.video_original_name,
        thumb_key=src.thumb_key,
        thumb_original_name=src.thumb_original_name,
        camouflage_cover_key=getattr(src, "camouflage_cover_key", None),
        camouflage_opacity=float(getattr(src, "camouflage_opacity", 0.10) or 0.10),
        videos_json=src.videos_json,
        current_index=0,
        interval_minutes=src.interval_minutes,
        schedule_type=src.schedule_type or "interval",
        start_mode=src.start_mode or "recurring",
        calendar_days=src.calendar_days,
        calendar_time=src.calendar_time,
        jitter_enabled=bool(getattr(src, "jitter_enabled", False)),
        jitter_minutes=int(getattr(src, "jitter_minutes", 10) or 10),
        stagger_enabled=bool(getattr(src, "stagger_enabled", True)),
        stagger_min_minutes=int(getattr(src, "stagger_min_minutes", DEFAULT_STAGGER_MIN) or DEFAULT_STAGGER_MIN),
        stagger_max_minutes=int(getattr(src, "stagger_max_minutes", DEFAULT_STAGGER_MAX) or DEFAULT_STAGGER_MAX),
        caption_rotate_by_account=bool(getattr(src, "caption_rotate_by_account", True)),
        caption_rotate_by_reel=bool(getattr(src, "caption_rotate_by_reel", False)),
        posts_per_batch=int(getattr(src, "posts_per_batch", 0) or 0),
        rest_minutes=int(getattr(src, "rest_minutes", 0) or 0),
        posts_in_batch=0,
        status="paused",
        next_run_at=None,
        last_run_at=None,
        total_runs=0,
    )
    clone.accounts = list(src.accounts)
    db.add(clone)
    db.commit()
    return RedirectResponse(
        "/automations?ok=duplicated",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{automation_id}/edit")
async def edit_automation(
    automation_id: int,
    caption: str = Form(""),
    captions_alt: list[str] = Form(default=[]),
    content_type: str = Form("reel"),
    interval_minutes: int = Form(...),
    account_ids: list[int] = Form(default=[]),
    jitter_enabled: str = Form(""),
    jitter_minutes: int = Form(10),
    posts_per_batch: int = Form(0),
    rest_minutes: int = Form(0),
    stagger_enabled: str = Form(""),
    stagger_min_minutes: int = Form(DEFAULT_STAGGER_MIN),
    stagger_max_minutes: int = Form(DEFAULT_STAGGER_MAX),
    caption_rotate_by_account: str = Form(""),
    caption_rotate_by_reel: str = Form(""),
    thumb: UploadFile | None = File(None),
    remove_thumb: bool = Form(False),
    camouflage_enabled: str = Form(""),
    camouflage_cover: UploadFile | None = File(None),
    remove_camouflage: bool = Form(False),
    camouflage_opacity_pct: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    a = _get_owned(db, automation_id, user)
    if content_type not in CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Tipo inválido")
    if interval_minutes not in ALLOWED_INTERVALS:
        raise HTTPException(status_code=400, detail="Intervalo inválido")

    accounts = db.scalars(
        select(InstagramAccount).where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.id.in_(account_ids),
            InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
        )
    ).all()
    if len(accounts) != len(set(account_ids)):
        raise HTTPException(status_code=400, detail="Conta inválida")
    iv_err = validate_interval_for_accounts(
        interval_minutes,
        accounts,
        meta_warmup_enabled=get_anti_farm_prefs(user).get("meta_warmup_enabled", True),
    )
    if iv_err:
        raise HTTPException(status_code=400, detail=iv_err)

    storage = get_storage()
    if remove_thumb and a.thumb_key:
        old_thumb = a.thumb_key
        a.thumb_key = None
        a.thumb_original_name = None
        if not media_key_referenced_elsewhere(db, old_thumb, exclude_automation_id=a.id):
            try:
                storage.delete(old_thumb)
            except Exception:
                pass

    if thumb and thumb.filename:
        old_thumb = a.thumb_key
        thumb_ext = Path(thumb.filename).suffix or ".jpg"
        a.thumb_key = storage.save(thumb.file, suggested_ext=thumb_ext)
        a.thumb_original_name = thumb.filename
        if old_thumb and not media_key_referenced_elsewhere(
            db, old_thumb, exclude_automation_id=a.id
        ):
            try:
                storage.delete(old_thumb)
            except Exception:
                pass

    if content_type != "reel":
        if a.camouflage_cover_key:
            old_camu = a.camouflage_cover_key
            a.camouflage_cover_key = None
            if not media_key_referenced_elsewhere(db, old_camu, exclude_automation_id=a.id):
                try:
                    storage.delete(old_camu)
                except Exception:
                    pass
        a.camouflage_opacity = 0.15
    else:
        want_camu = _want_camouflage(camouflage_enabled, camouflage_cover)
        if camouflage_opacity_pct not in ("", None):
            a.camouflage_opacity = _clamp_camouflage_opacity(
                float(camouflage_opacity_pct) / 100.0
            )
        if remove_camouflage or not want_camu:
            if a.camouflage_cover_key:
                old_camu = a.camouflage_cover_key
                a.camouflage_cover_key = None
                if not media_key_referenced_elsewhere(db, old_camu, exclude_automation_id=a.id):
                    try:
                        storage.delete(old_camu)
                    except Exception:
                        pass
        if want_camu and camouflage_cover and camouflage_cover.filename:
            old_camu = a.camouflage_cover_key
            a.camouflage_cover_key = _save_camouflage_cover(storage, camouflage_cover)
            if old_camu and not media_key_referenced_elsewhere(
                db, old_camu, exclude_automation_id=a.id
            ):
                try:
                    storage.delete(old_camu)
                except Exception:
                    pass
        if want_camu and not a.camouflage_cover_key:
            raise HTTPException(
                status_code=400,
                detail="Marcou aplicar camuflagem — envie a imagem de camuflagem.",
            )
        if want_camu and a.camouflage_opacity < 0.05:
            a.camouflage_opacity = 0.25

    humanize = _schedule_humanize_fields(
        jitter_enabled=jitter_enabled,
        jitter_minutes=jitter_minutes,
        posts_per_batch=posts_per_batch,
        rest_minutes=rest_minutes,
        stagger_enabled=stagger_enabled,
        stagger_min_minutes=stagger_min_minutes,
        stagger_max_minutes=stagger_max_minutes,
        caption_rotate_by_account=caption_rotate_by_account,
        caption_rotate_by_reel=caption_rotate_by_reel,
    )
    a.caption = caption
    a.captions_json = captions_to_json(captions_from_form(captions_alt))
    a.content_type = content_type
    a.interval_minutes = interval_minutes
    a.jitter_enabled = bool(humanize["jitter_enabled"])
    a.jitter_minutes = int(humanize["jitter_minutes"])  # type: ignore[arg-type]
    a.posts_per_batch = int(humanize["posts_per_batch"])  # type: ignore[arg-type]
    a.rest_minutes = int(humanize["rest_minutes"])  # type: ignore[arg-type]
    a.stagger_enabled = bool(humanize["stagger_enabled"])
    a.stagger_min_minutes = int(humanize["stagger_min_minutes"])  # type: ignore[arg-type]
    a.stagger_max_minutes = int(humanize["stagger_max_minutes"])  # type: ignore[arg-type]
    a.caption_rotate_by_account = bool(humanize["caption_rotate_by_account"])
    a.caption_rotate_by_reel = bool(humanize["caption_rotate_by_reel"])
    a.accounts = list(accounts)
    if not accounts:
        a.status = "paused"
        a.next_run_at = None
    db.commit()
    return RedirectResponse("/automations", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{automation_id}/delete")
def delete_automation(
    automation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    a = _get_owned(db, automation_id, user)
    storage = get_storage()
    keys = media_keys_for_automation(a)
    # Apaga do storage só keys que nenhuma outra automação ainda usa (cópias compartilham arquivo)
    for key in keys:
        if media_key_referenced_elsewhere(db, key, exclude_automation_id=a.id):
            continue
        try:
            storage.delete(key)
        except Exception:
            pass
    db.delete(a)
    db.commit()
    return RedirectResponse("/automations", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{automation_id}/logs")
def automation_logs(
    request: Request,
    automation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    a = _get_owned(db, automation_id, user)
    logs = db.scalars(
        select(PublishLog)
        .where(PublishLog.automation_id == a.id)
        .options(selectinload(PublishLog.account))
        .order_by(desc(PublishLog.created_at))
        .limit(200)
    ).all()
    return templates.TemplateResponse(
        "automation_logs.html",
        {"request": request, "user": user, "automation": a, "logs": logs},
    )
