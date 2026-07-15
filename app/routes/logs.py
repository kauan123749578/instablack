"""Logs globais de publicação."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, selectinload

from app.deps import get_current_user
from app.templating import templates
from core.database import get_db
from models.models import InstagramAccount, PublishLog, User

router = APIRouter(prefix="/logs", tags=["logs"])
VISIBLE_ACCOUNT_STATUSES = ("active", "paused", "needs_login", "proxy_down", "banned")


@router.get("")
def user_logs(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    status_filter = request.query_params.get("status", "").strip()
    account_filter = request.query_params.get("account_id", "").strip()

    q = (
        select(PublishLog)
        .join(PublishLog.account)
        .where(InstagramAccount.user_id == user.id)
        .options(selectinload(PublishLog.account), selectinload(PublishLog.automation))
        .order_by(desc(PublishLog.created_at))
        .limit(500)
    )
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

    counts = dict(
        db.execute(
            select(PublishLog.status, func.count(PublishLog.id))
            .join(PublishLog.account)
            .where(InstagramAccount.user_id == user.id)
            .group_by(PublishLog.status)
        ).all()
    )

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
        },
    )
