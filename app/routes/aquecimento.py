"""Página Aquecimento / proteção anti-farm (explicação + toggles + aquecimento manual)."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_current_user
from app.templating import templates
from app.utils.intervals import (
    META_MIN_INTERVAL,
    META_WARMUP_DAYS,
    META_WARMUP_MIN_INTERVAL,
    clamp_warmup_days,
    is_meta_in_warmup,
    meta_min_interval_for_account,
    warmup_days_left,
)
from core.anti_farm_prefs import (
    PREF_LABELS,
    get_anti_farm_prefs,
    prefs_from_form,
    save_anti_farm_prefs,
)
from core.database import get_db
from models.models import InstagramAccount, User

router = APIRouter(prefix="/aquecimento", tags=["aquecimento"])

VISIBLE = ("active", "paused", "needs_login", "proxy_down")


def _owned_meta(db: Session, account_id: int, user: User) -> InstagramAccount:
    acc = db.get(InstagramAccount, account_id)
    if (
        not acc
        or acc.user_id != user.id
        or (acc.provider or "") != "meta"
        or acc.status == "deleted"
    ):
        raise HTTPException(status_code=404, detail="Conta não encontrada")
    return acc


@router.get("")
def aquecimento_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    prefs = get_anti_farm_prefs(user)
    accounts = list(
        db.scalars(
            select(InstagramAccount)
            .where(
                InstagramAccount.user_id == user.id,
                InstagramAccount.provider == "meta",
                InstagramAccount.status.in_(VISIBLE),
            )
            .order_by(InstagramAccount.username.asc())
        ).all()
    )
    now = dt.datetime.utcnow()
    warmup_rows = []
    for acc in accounts:
        # Expira automaticamente se passou o prazo
        if bool(getattr(acc, "warmup_enabled", False)) and not is_meta_in_warmup(acc, now=now):
            acc.warmup_enabled = False
            acc.warmup_started_at = None
        in_warmup = is_meta_in_warmup(acc, now=now)
        floor = meta_min_interval_for_account(acc, now=now)
        if not prefs.get("meta_warmup_enabled", True):
            floor = META_MIN_INTERVAL
        left = warmup_days_left(acc, now=now)
        warmup_rows.append(
            {
                "id": acc.id,
                "username": acc.username,
                "status": acc.status,
                "in_warmup": in_warmup,
                "min_interval": floor,
                "warmup_days": int(getattr(acc, "warmup_days", META_WARMUP_DAYS) or META_WARMUP_DAYS),
                "days_left": left,
            }
        )
    db.commit()

    return templates.TemplateResponse(
        "aquecimento.html",
        {
            "request": request,
            "user": user,
            "prefs": prefs,
            "pref_labels": PREF_LABELS,
            "warmup_rows": warmup_rows,
            "meta_warmup_days": META_WARMUP_DAYS,
            "meta_warmup_min_interval": META_WARMUP_MIN_INTERVAL,
            "meta_min_interval": META_MIN_INTERVAL,
            "saved": request.query_params.get("saved") == "1",
            "warmup_ok": request.query_params.get("warmup"),
        },
    )


@router.post("/prefs")
def save_aquecimento_prefs(
    stagger_enabled: str = Form(""),
    stagger_min_minutes: int = Form(2),
    stagger_max_minutes: int = Form(8),
    media_rotate_enabled: str = Form(""),
    caption_rotate_by_account: str = Form(""),
    caption_rotate_by_reel: str = Form(""),
    meta_warmup_enabled: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    prefs = prefs_from_form(
        stagger_enabled=stagger_enabled,
        stagger_min_minutes=stagger_min_minutes,
        stagger_max_minutes=stagger_max_minutes,
        media_rotate_enabled=media_rotate_enabled,
        caption_rotate_by_account=caption_rotate_by_account,
        caption_rotate_by_reel=caption_rotate_by_reel,
        meta_warmup_enabled=meta_warmup_enabled,
    )
    save_anti_farm_prefs(db, user, prefs)
    db.commit()
    return RedirectResponse(
        "/aquecimento?saved=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/accounts/{account_id}/enable")
def enable_account_warmup(
    account_id: int,
    warmup_days: int = Form(META_WARMUP_DAYS),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    acc = _owned_meta(db, account_id, user)
    days = clamp_warmup_days(warmup_days)
    acc.warmup_enabled = True
    acc.warmup_days = days
    acc.warmup_started_at = dt.datetime.utcnow()
    db.commit()
    return RedirectResponse(
        "/aquecimento?warmup=on",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/accounts/{account_id}/disable")
def disable_account_warmup(
    account_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    acc = _owned_meta(db, account_id, user)
    acc.warmup_enabled = False
    acc.warmup_started_at = None
    db.commit()
    return RedirectResponse(
        "/aquecimento?warmup=off",
        status_code=status.HTTP_303_SEE_OTHER,
    )
