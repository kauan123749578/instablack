"""CRUD de automações recorrentes."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.deps import get_current_user
from app.templating import templates
from app.utils.calendar_schedule import days_to_json, next_calendar_run, parse_calendar_days
from celery_app.tasks.publish import publish_once
from core.database import get_db
from core.storage import get_storage
from models.models import Automation, InstagramAccount, PublishLog, User

router = APIRouter(prefix="/automations", tags=["automations"])

ALLOWED_INTERVALS = [30, 60, 120, 240, 360, 720, 1440]
CONTENT_TYPES = ["reel", "story", "photo"]


def _story_link_value(content_type: str, story_link: str) -> str | None:
    if content_type != "story":
        return None
    link = story_link.strip()
    return link or None


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
        select(InstagramAccount).where(InstagramAccount.user_id == user.id)
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
        .where(InstagramAccount.user_id == user.id)
        .order_by(InstagramAccount.username.asc())
    ).all()
    default_type = request.query_params.get("type", "reel")
    if default_type not in CONTENT_TYPES:
        default_type = "reel"
    return templates.TemplateResponse(
        "new_automation.html",
        {
            "request": request,
            "user": user,
            "accounts": accounts,
            "intervals": ALLOWED_INTERVALS,
            "content_types": CONTENT_TYPES,
            "default_content_type": default_type,
            "error": None,
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
        .where(InstagramAccount.user_id == user.id)
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


@router.get("/new/calendar")
def new_calendar_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    accounts = db.scalars(
        select(InstagramAccount)
        .where(InstagramAccount.user_id == user.id)
        .order_by(InstagramAccount.username.asc())
    ).all()
    return templates.TemplateResponse(
        "new_calendar_automation.html",
        {
            "request": request,
            "user": user,
            "accounts": accounts,
            "content_types": CONTENT_TYPES,
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
    calendar_time: str = Form(...),
    account_ids: list[int] = Form(default=[]),
    video: UploadFile = File(...),
    thumb: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    days = parse_calendar_days(calendar_days)
    error: str | None = None
    if content_type not in CONTENT_TYPES:
        error = "Tipo de conteúdo inválido."
    elif not days:
        error = "Selecione pelo menos um dia do mês."
    elif not calendar_time:
        error = "Informe o horário de publicação."
    elif not account_ids:
        error = "Selecione pelo menos uma conta."
    elif not video or not video.filename:
        error = "Envie o arquivo de mídia."

    accounts: list[InstagramAccount] = []
    if not error:
        accounts = list(db.scalars(
            select(InstagramAccount).where(
                InstagramAccount.user_id == user.id,
                InstagramAccount.id.in_(account_ids),
            )
        ).all())
        if len(accounts) != len(set(account_ids)):
            error = "Alguma conta selecionada não existe."

    if error:
        all_accounts = db.scalars(
            select(InstagramAccount).where(InstagramAccount.user_id == user.id)
        ).all()
        return templates.TemplateResponse(
            "new_calendar_automation.html",
            {
                "request": request,
                "user": user,
                "accounts": all_accounts,
                "content_types": CONTENT_TYPES,
                "error": error,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    storage = get_storage()
    ext = Path(video.filename).suffix or ".mp4"
    video_key = storage.save(video.file, suggested_ext=ext)

    thumb_key = None
    thumb_original_name = None
    if thumb and thumb.filename:
        thumb_ext = Path(thumb.filename).suffix or ".jpg"
        thumb_key = storage.save(thumb.file, suggested_ext=thumb_ext)
        thumb_original_name = thumb.filename

    nxt = next_calendar_run(days, calendar_time) or dt.datetime.utcnow()

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
        calendar_days=days_to_json(days),
        calendar_time=calendar_time,
        interval_minutes=1440,
        status="active",
        next_run_at=nxt,
    )
    automation.accounts = accounts
    db.add(automation)
    db.commit()
    return RedirectResponse("/automations?ok=calendar", status_code=status.HTTP_303_SEE_OTHER)


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
    account_ids: list[int] = Form(default=[]),
    video: UploadFile = File(...),
    thumb: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
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
        elif not calendar_time.strip():
            error = "Informe o horário de publicação."

    if not error and not account_ids:
        error = "Selecione pelo menos uma conta."
    if not error and (not video or not video.filename):
        error = "Envie o arquivo de mídia."

    accounts: list[InstagramAccount] = []
    if not error:
        accounts = list(db.scalars(
            select(InstagramAccount).where(
                InstagramAccount.user_id == user.id,
                InstagramAccount.id.in_(account_ids),
            )
        ).all())
        if len(accounts) != len(set(account_ids)):
            error = "Alguma conta selecionada não existe."

    if error:
        all_accounts = db.scalars(
            select(InstagramAccount).where(InstagramAccount.user_id == user.id)
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
    ext = Path(video.filename).suffix or ".mp4"
    video_key = storage.save(video.file, suggested_ext=ext)

    thumb_key = None
    thumb_original_name = None
    if thumb and thumb.filename:
        thumb_ext = Path(thumb.filename).suffix or ".jpg"
        thumb_key = storage.save(thumb.file, suggested_ext=thumb_ext)
        thumb_original_name = thumb.filename

    if schedule_mode == "now":
        for idx, acc in enumerate(accounts):
            publish_once.apply_async(
                args=[
                    acc.id,
                    video_key,
                    thumb_key,
                    caption,
                    content_type,
                    _story_link_value(content_type, story_link),
                ],
                countdown=idx * 5,
            )
        return RedirectResponse(
            "/automations?posted=1",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    now = dt.datetime.utcnow()

    if schedule_mode == "calendar":
        days = parse_calendar_days(calendar_days)
        nxt = next_calendar_run(days, calendar_time.strip()) or now
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
            calendar_days=days_to_json(days),
            calendar_time=calendar_time.strip(),
            interval_minutes=1440,
            status="active",
            next_run_at=nxt,
        )
        automation.accounts = accounts
        db.add(automation)
        db.commit()
        return RedirectResponse(
            "/automations?ok=calendar",
            status_code=status.HTTP_303_SEE_OTHER,
        )

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
        schedule_type="interval",
        interval_minutes=interval_minutes,
        status="active",
        next_run_at=now,
    )
    automation.accounts = accounts
    db.add(automation)
    db.commit()
    return RedirectResponse("/automations", status_code=status.HTTP_303_SEE_OTHER)


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
    a = _get_owned(db, automation_id, user)
    a.status = "active"
    if a.next_run_at is None:
        a.next_run_at = dt.datetime.utcnow()
    db.commit()
    return RedirectResponse("/automations", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{automation_id}/edit")
async def edit_automation(
    automation_id: int,
    caption: str = Form(""),
    content_type: str = Form("reel"),
    interval_minutes: int = Form(...),
    account_ids: list[int] = Form(default=[]),
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
    if not account_ids:
        raise HTTPException(status_code=400, detail="Selecione ao menos uma conta")

    accounts = db.scalars(
        select(InstagramAccount).where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.id.in_(account_ids),
        )
    ).all()
    if len(accounts) != len(set(account_ids)):
        raise HTTPException(status_code=400, detail="Conta inválida")

    storage = get_storage()
    if remove_thumb and a.thumb_key:
        try:
            storage.delete(a.thumb_key)
        except Exception:
            pass
        a.thumb_key = None
        a.thumb_original_name = None

    if thumb and thumb.filename:
        if a.thumb_key:
            try:
                storage.delete(a.thumb_key)
            except Exception:
                pass
        thumb_ext = Path(thumb.filename).suffix or ".jpg"
        a.thumb_key = storage.save(thumb.file, suggested_ext=thumb_ext)
        a.thumb_original_name = thumb.filename

    a.caption = caption
    a.content_type = content_type
    a.interval_minutes = interval_minutes
    a.accounts = list(accounts)
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
    try:
        storage.delete(a.video_key)
        if a.thumb_key:
            storage.delete(a.thumb_key)
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
