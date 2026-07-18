"""Helpers para reconhecer sessão expirada a partir dos logs do Instagram."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from models.models import InstagramAccount, PublishLog

AUTH_REQUIRED_MARKERS = (
    "login_required",
    "challenge_required",
    "checkpoint_required",
    "session expired",
    "sessão expirada",
)


def looks_auth_required(value: object) -> bool:
    text = " ".join(
        part
        for part in (str(value), repr(value), repr(getattr(value, "args", "")))
        if part
    ).lower()
    return any(marker in text for marker in AUTH_REQUIRED_MARKERS)


def auth_status_reason(reason: str) -> str:
    if reason.lower().startswith("sess"):
        return reason[:1000]
    return f"Sessão expirada no upload: {reason}"[:1000]


def latest_auth_failure_reason(
    db: Session,
    account_id: int,
    *,
    max_age_hours: int = 24,
) -> str | None:
    since = dt.datetime.utcnow() - dt.timedelta(hours=max_age_hours)
    account = db.get(InstagramAccount, account_id)
    if account and account.last_login_at:
        last_login = account.last_login_at
        if last_login.tzinfo is not None:
            last_login = last_login.astimezone(dt.timezone.utc).replace(tzinfo=None)
        if last_login > since:
            since = last_login
    latest = db.scalar(
        select(PublishLog)
        .where(
            PublishLog.account_id == account_id,
            PublishLog.created_at >= since,
        )
        .order_by(desc(PublishLog.created_at))
        .limit(1)
    )
    if not latest or latest.status != "failed" or not latest.error:
        return None
    if not looks_auth_required(latest.error):
        return None
    return latest.error[:1000]


def mark_account_from_latest_auth_failure(db: Session, account: InstagramAccount) -> bool:
    if account.status in ("deleted", "paused"):
        return False
    if (getattr(account, "provider", "instagrapi") or "instagrapi") == "meta":
        return False
    reason = latest_auth_failure_reason(db, account.id)
    if not reason:
        return False
    new_error = auth_status_reason(reason)
    if account.status == "needs_login" and account.last_error == new_error:
        return False
    account.status = "needs_login"
    account.last_error = new_error
    return True


def mark_accounts_from_latest_auth_failures(
    db: Session,
    accounts: list[InstagramAccount],
) -> bool:
    dirty = False
    for account in accounts:
        dirty = mark_account_from_latest_auth_failure(db, account) or dirty
    return dirty
