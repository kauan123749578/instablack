"""Logs globais de publicação."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, selectinload

from app.deps import get_current_user, get_effective_user
from app.templating import templates
from core.database import get_db
from models.models import InstagramAccount, PublishLog, User

router = APIRouter(prefix="/logs", tags=["logs"])
VISIBLE_ACCOUNT_STATUSES = ("active", "paused", "needs_login", "proxy_down", "banned")


def _logs_visible_after(user: User) -> dt.datetime | None:
    """Cutoff da aba Logs — rank/insights ignoram este filtro."""
    return getattr(user, "logs_cleared_at", None)


@router.get("")
def user_logs(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    status_filter = request.query_params.get("status", "").strip()
    account_filter = request.query_params.get("account_id", "").strip()
    cleared_at = _logs_visible_after(user)

    q = (
        select(PublishLog)
        .join(PublishLog.account)
        .where(InstagramAccount.user_id == user.id)
        .options(selectinload(PublishLog.account), selectinload(PublishLog.automation))
        .order_by(desc(PublishLog.created_at))
        .limit(500)
    )
    if cleared_at is not None:
        q = q.where(PublishLog.created_at > cleared_at)
    if status_filter in ("success", "failed", "skipped"):
        q = q.where(PublishLog.status == status_filter)
    if account_filter.isdigit():
        q = q.where(PublishLog.account_id == int(account_filter))

    logs = db.scalars(q).all()
    accounts = db.scalars(
        select(InstagramAccount)
        .where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
        )
        .order_by(InstagramAccount.username.asc())
    ).all()

    counts_q = (
        select(PublishLog.status, func.count(PublishLog.id))
        .join(PublishLog.account)
        .where(InstagramAccount.user_id == user.id)
        .group_by(PublishLog.status)
    )
    if cleared_at is not None:
        counts_q = counts_q.where(PublishLog.created_at > cleared_at)
    counts = dict(db.execute(counts_q).all())

    return templates.TemplateResponse(
        "logs.html",
        {
            "request": request,
            "user": user,
            "logs": logs,
            "accounts": accounts,
            "status_filter": status_filter,
            "account_filter": int(account_filter) if account_filter.isdigit() else None,
            "counts": counts,
            "ok": request.query_params.get("ok"),
        },
    )


@router.post("/clear")
def clear_user_logs(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Limpa só a aba Logs — NÃO apaga PublishLog (rank e views permanecem)."""
    db_user = db.get(User, user.id)
    if db_user is not None:
        db_user.logs_cleared_at = dt.datetime.utcnow()
        db.commit()
    wants_json = "application/json" in (request.headers.get("accept") or "").lower()
    if wants_json or request.headers.get("x-requested-with") == "XMLHttpRequest":
        from fastapi.responses import JSONResponse

        return JSONResponse({"ok": True, "redirect": "/logs?ok=cleared"})
    return RedirectResponse(
        "/logs?ok=cleared",
        status_code=status.HTTP_303_SEE_OTHER,
    )
