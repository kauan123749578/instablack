"""Métricas agregadas da API oficial Meta para dashboard/analytics."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from models.models import InstagramAccount, PublishLog

VISIBLE = ("active", "paused", "needs_login", "proxy_down", "banned")


def _utc_naive(d: dt.datetime) -> dt.datetime:
    if d.tzinfo is None:
        return d
    return d.astimezone(dt.timezone.utc).replace(tzinfo=None)


def user_official_insights_summary(
    db: Session,
    user_id: int,
    *,
    reel_views_days: int = 7,
    account_id: int | None = None,
) -> dict:
    accounts_q = (
        select(InstagramAccount)
        .where(
            InstagramAccount.user_id == user_id,
            InstagramAccount.status.in_(VISIBLE),
            InstagramAccount.provider == "meta",
        )
        .order_by(InstagramAccount.username.asc())
    )
    all_meta = db.scalars(accounts_q).all()

    selected_id = account_id
    if selected_id is not None and not any(a.id == selected_id for a in all_meta):
        selected_id = None

    accounts = [a for a in all_meta if selected_id is None or a.id == selected_id]

    since = dt.datetime.utcnow() - dt.timedelta(days=max(1, reel_views_days))
    total_followers = 0
    followers_known = 0
    account_rows: list[dict] = []

    for acc in accounts:
        followers = acc.followers_count
        if followers is not None:
            total_followers += followers
            followers_known += 1
        views = db.scalar(
            select(func.coalesce(func.sum(PublishLog.play_count), 0)).where(
                PublishLog.account_id == acc.id,
                PublishLog.status == "success",
                PublishLog.content_type == "reel",
                PublishLog.created_at >= _utc_naive(since),
            )
        ) or 0
        ok_count = db.scalar(
            select(func.count(PublishLog.id)).where(
                PublishLog.account_id == acc.id,
                PublishLog.status == "success",
            )
        ) or 0
        account_rows.append(
            {
                "account": acc,
                "followers": followers,
                "reel_views_period": int(views),
                "success_count": int(ok_count),
            }
        )

    total_reel_views = sum(row["reel_views_period"] for row in account_rows)
    total_success = sum(row["success_count"] for row in account_rows)

    reels_q = (
        select(PublishLog)
        .join(PublishLog.account)
        .where(
            InstagramAccount.user_id == user_id,
            InstagramAccount.provider == "meta",
            PublishLog.status == "success",
            PublishLog.content_type == "reel",
        )
        .options(selectinload(PublishLog.account))
        .order_by(PublishLog.created_at.desc())
        .limit(40)
    )
    if selected_id is not None:
        reels_q = reels_q.where(PublishLog.account_id == selected_id)

    recent_reels = db.scalars(reels_q).all()

    recent_reels_by_account: dict[int, list[PublishLog]] = {}
    for reel in recent_reels:
        acc_id = reel.account.id if reel.account else None
        if acc_id is None:
            continue
        recent_reels_by_account.setdefault(acc_id, []).append(reel)

    selected_account = accounts[0] if selected_id is not None and accounts else None

    return {
        "meta_accounts_count": len(all_meta),
        "meta_accounts": all_meta,
        "selected_account_id": selected_id,
        "selected_account": selected_account,
        "total_followers": total_followers if followers_known else None,
        "total_reel_views_period": total_reel_views,
        "total_success_count": total_success,
        "reel_views_days": reel_views_days,
        "account_rows": account_rows,
        "recent_reels": recent_reels,
        "recent_reels_by_account": recent_reels_by_account,
    }
