"""CRUD de automações recorrentes."""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.deps import get_current_user
from app.templating import templates
from app.utils.calendar_schedule import (
    days_to_json,
    format_calendar_times_label,
    next_calendar_run,
    parse_calendar_days,
    parse_calendar_times,
    times_to_storage,
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
from app.utils.intervals import ALLOWED_INTERVALS, interval_label
from celery_app.tasks.publish import publish_once, publish_to_account
from core.database import get_db
from core.storage import get_storage
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
    thumb_ext = Path(thumb.filename).suffix or ".jpg"
    return storage.save(thumb.file, suggested_ext=thumb_ext), thumb.filename


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
) -> dict[str, object]:
    return {
        "jitter_enabled": parse_jitter_enabled(jitter_enabled),
        "jitter_minutes": parse_jitter_minutes(jitter_minutes),
        "posts_per_batch": parse_posts_per_batch(posts_per_batch),
        "rest_minutes": parse_rest_minutes(rest_minutes),
        "posts_in_batch": 0,
    }


@router.get("")
def list_automations(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
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
        },
    )


@router.get("/new")
def new_automation_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
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
            "content_types": CONTENT_TYPES,
            "default_content_type": default_type,
            "error": err_msg,
        },
    )


@router.get("/new/story")
def new_story_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
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
            "content_types": CONTENT_TYPES,
            "default_content_type": "story",
            "error": None,
        },
    )


@router.get("/media-library")
def media_library(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
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
    user: User = Depends(get_current_user),
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
            # A Content Publishing API oficial é mais restritiva que o
            # Instagrapi. Validamos antes de salvar para evitar falha tardia.
            if content_type == "reel":
                bad = [
                    f.filename for f in upload_files
                    if Path(f.filename or "").suffix.lower() != ".mp4"
                ]
                if bad:
                    error = "A API oficial aceita Reels em MP4: " + ", ".join(bad)
            elif content_type == "story":
                meta_story_ext = {".jpg", ".jpeg", ".mp4"}
                bad = [
                    f.filename for f in upload_files
                    if Path(f.filename or "").suffix.lower() not in meta_story_ext
                ]
                if bad:
                    error = "Para contas oficiais, Stories devem ser JPG ou MP4: " + ", ".join(bad)
            elif content_type == "photo":
                ext = Path(upload_files[0].filename or "").suffix.lower()
                if ext not in {".jpg", ".jpeg"}:
                    error = "A API oficial aceita fotos em JPG."

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
    story_link: str = Form(""),
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
    thumb: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    humanize = _schedule_humanize_fields(
        jitter_enabled=jitter_enabled,
        jitter_minutes=jitter_minutes,
        posts_per_batch=posts_per_batch,
        rest_minutes=rest_minutes,
    )
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
    warn_q = f"&warn={len(upload_warnings)}" if upload_warnings else ""
    has_accounts = bool(accounts)

    if schedule_mode == "now" and has_accounts:
        countdown = 0
        for v_idx, entry in enumerate(video_entries):
            for acc_idx, acc in enumerate(accounts):
                publish_once.apply_async(
                    args=[
                        acc.id,
                        entry["video_key"],
                        thumb_key,
                        caption,
                        content_type,
                        _story_link_value(content_type, story_link),
                    ],
                    countdown=countdown + acc_idx * 5,
                )
            if len(video_entries) > 1:
                # Espaça cada vídeo da fila (um por vez, sem repetir)
                countdown += max(45, len(accounts) * 8)
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
            video_key=video_key,
            video_original_name=video_original_name,
            videos_json=videos_json,
            current_index=0,
            thumb_key=thumb_key,
            thumb_original_name=thumb_original_name,
            story_link=_story_link_value(content_type, story_link),
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
        video_key=video_key,
        video_original_name=video_original_name,
        videos_json=videos_json,
        current_index=0,
        thumb_key=thumb_key,
        thumb_original_name=thumb_original_name,
        story_link=_story_link_value(content_type, story_link),
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
    thumb: UploadFile | None = File(None),
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

    humanize = _schedule_humanize_fields(
        jitter_enabled=jitter_enabled,
        jitter_minutes=jitter_minutes,
        posts_per_batch=posts_per_batch,
        rest_minutes=rest_minutes,
    )
    storage = get_storage()
    thumb_key, thumb_original_name = _save_thumb(storage, thumb)
    automation = Automation(
        user_id=user.id,
        name=name.strip(),
        content_type="reel",
        caption=caption,
        video_key="",
        video_original_name="0 vídeos",
        videos_json=videos_to_json([]),
        current_index=0,
        thumb_key=thumb_key,
        thumb_original_name=thumb_original_name,
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
        # O upload terminou: despacha cada item uma única vez e mantém o
        # registro como concluído para histórico, sem transformar em loop.
        a.status = "completed"
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
        for index, entry in enumerate(entries):
            for account_index, account in enumerate(accounts):
                publish_to_account.apply_async(
                    args=[a.id, account.id, entry["video_key"], index],
                    countdown=countdown + account_index * 5,
                )
            countdown += max(45, len(accounts) * 8)
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
        story_link=src.story_link,
        story_sticker_text=src.story_sticker_text,
        video_key=src.video_key,
        video_original_name=src.video_original_name,
        thumb_key=src.thumb_key,
        thumb_original_name=src.thumb_original_name,
        videos_json=src.videos_json,
        current_index=0,
        interval_minutes=src.interval_minutes,
        schedule_type=src.schedule_type or "interval",
        start_mode=src.start_mode or "recurring",
        calendar_days=src.calendar_days,
        calendar_time=src.calendar_time,
        jitter_enabled=bool(getattr(src, "jitter_enabled", False)),
        jitter_minutes=int(getattr(src, "jitter_minutes", 10) or 10),
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
    content_type: str = Form("reel"),
    interval_minutes: int = Form(...),
    account_ids: list[int] = Form(default=[]),
    jitter_enabled: str = Form(""),
    jitter_minutes: int = Form(10),
    posts_per_batch: int = Form(0),
    rest_minutes: int = Form(0),
    thumb: UploadFile | None = File(None),
    remove_thumb: bool = Form(False),
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

    humanize = _schedule_humanize_fields(
        jitter_enabled=jitter_enabled,
        jitter_minutes=jitter_minutes,
        posts_per_batch=posts_per_batch,
        rest_minutes=rest_minutes,
    )
    a.caption = caption
    a.content_type = content_type
    a.interval_minutes = interval_minutes
    a.jitter_enabled = bool(humanize["jitter_enabled"])
    a.jitter_minutes = int(humanize["jitter_minutes"])  # type: ignore[arg-type]
    a.posts_per_batch = int(humanize["posts_per_batch"])  # type: ignore[arg-type]
    a.rest_minutes = int(humanize["rest_minutes"])  # type: ignore[arg-type]
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
    user: User = Depends(get_current_user),
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
