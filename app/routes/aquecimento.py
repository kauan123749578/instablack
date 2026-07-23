"""Página Aquecimento / proteção anti-farm (explicação + toggles)."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_current_user
from app.templating import templates
from app.utils.intervals import (
    META_MIN_INTERVAL,
    META_WARMUP_DAYS,
    META_WARMUP_MIN_INTERVAL,
    is_meta_in_warmup,
    meta_min_interval_for_account,
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
        in_warmup = is_meta_in_warmup(acc, now=now)
        floor = meta_min_interval_for_account(acc, now=now)
        if not prefs.get("meta_warmup_enabled", True):
            floor = META_MIN_INTERVAL
            in_warmup = False
        created = acc.created_at
        age_days = None
        if created:
            c = created
            if c.tzinfo is not None:
                c = c.astimezone(dt.timezone.utc).replace(tzinfo=None)
            age_days = max(0, int((now - c).total_seconds() // 86400))
        warmup_rows.append(
            {
                "username": acc.username,
                "status": acc.status,
                "in_warmup": in_warmup,
                "min_interval": floor,
                "age_days": age_days,
            }
        )

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
        },
    )


@router.post("/prefs")
def save_aquecimento_prefs(
    stagger_enabled: str = Form(""),
    media_rotate_enabled: str = Form(""),
    caption_rotate_enabled: str = Form(""),
    meta_warmup_enabled: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    prefs = prefs_from_form(
        stagger_enabled=stagger_enabled,
        media_rotate_enabled=media_rotate_enabled,
        caption_rotate_enabled=caption_rotate_enabled,
        meta_warmup_enabled=meta_warmup_enabled,
    )
    save_anti_farm_prefs(db, user, prefs)
    db.commit()
    return RedirectResponse(
        "/aquecimento?saved=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )
