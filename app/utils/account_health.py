"""Helpers para status de contas Instagram."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.models import InstagramAccount

OFFLINE_STATUSES = ("needs_login", "proxy_down", "banned")


def offline_accounts(db: Session, user_id: int) -> list[InstagramAccount]:
    return list(
        db.scalars(
            select(InstagramAccount)
            .where(
                InstagramAccount.user_id == user_id,
                InstagramAccount.status.in_(OFFLINE_STATUSES),
            )
            .order_by(InstagramAccount.username.asc())
        ).all()
    )
