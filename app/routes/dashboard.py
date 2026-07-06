"""Dashboard premium instablack."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, selectinload

from app.deps import get_current_user, maybe_current_user
from app.templating import templates
from app.utils.account_health import offline_accounts
from app.utils.timezone import brt_now
from core.database import get_db
from models.models import Automation, InstagramAccount, PublishLog, User

router = APIRouter(tags=["dashboard"])
BRT = ZoneInfo("America/Sao_Paulo")
WEEKDAY_LABELS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]


def _brt_day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(day, dt.time.min, tzinfo=BRT)
    end = start + dt.timedelta(days=1)
    return start, end


def _utc_naive(d: dt.datetime) -> dt.datetime:
    if d.tzinfo is None:
        return d
    return d.astimezone(dt.timezone.utc).replace(tzinfo=None)


def _count_logs(
    db: Session,
    user_id: int,
    *,
    status: str | None = None,
    day: dt.date | None = None,
) -> int:
    q = (
        select(func.count(PublishLog.id))
        .join(PublishLog.account)
        .where(InstagramAccount.user_id == user_id)
    )
    if status:
        q = q.where(PublishLog.status == status)
    if day is not None:
        start, end = _brt_day_bounds(day)
        q = q.where(
            PublishLog.created_at >= _utc_naive(start),
            PublishLog.created_at < _utc_naive(end),
        )
    return db.scalar(q) or 0


def _chart_performance_7d(db: Session, user_id: int) -> list[dict]:
    today = brt_now().date()
    days = [today - dt.timedelta(days=i) for i in range(6, -1, -1)]

    rows = db.execute(
        select(
            func.date(PublishLog.created_at).label("day"),
            PublishLog.status,
            func.count(PublishLog.id).label("cnt"),
        )
        .join(PublishLog.account)
        .where(
            InstagramAccount.user_id == user_id,
            PublishLog.created_at >= _utc_naive(_brt_day_bounds(days[0])[0]),
        )
        .group_by(func.date(PublishLog.created_at), PublishLog.status)
    ).all()

    by_day: dict[str, dict[str, int]] = {}
    for r in rows:
        key = str(r.day)
        by_day.setdefault(key, {"success": 0, "failed": 0, "skipped": 0})
        by_day[key][r.status] = r.cnt

    max_val = 1
    chart = []
    for d in days:
        key = str(d)
        stats = by_day.get(key, {"success": 0, "failed": 0, "skipped": 0})
        pubs = stats["success"] + stats["failed"] + stats["skipped"]
        max_val = max(max_val, pubs, stats["success"], stats["failed"])
        chart.append({
            "label": WEEKDAY_LABELS[d.weekday()],
            "date": d.strftime("%d/%m"),
            "pubs": pubs,
            "success": stats["success"],
            "failed": stats["failed"],
            "skipped": stats["skipped"],
        })

    for pt in chart:
        m = max_val or 1
        pt["pubs_pct"] = round(pt["pubs"] / m * 100, 1)
        pt["success_pct"] = round(pt["success"] / m * 100, 1)
        pt["failed_pct"] = round(pt["failed"] / m * 100, 1)

    return chart


def _chart_weekly_bars(db: Session, user_id: int) -> list[dict]:
    chart = _chart_performance_7d(db, user_id)
    max_val = max((pt["pubs"] for pt in chart), default=0) or 1
    for pt in chart:
        pt["bar_pct"] = round(pt["pubs"] / max_val * 100, 1)
    return chart


def _growth_pct(current: int, previous: int) -> float | None:
    if previous == 0:
        return 100.0 if current > 0 else None
    return round((current - previous) / previous * 100, 1)


def _top_platform_players(db: Session, start: dt.datetime, end: dt.datetime) -> list[dict]:
    """Top 5 usuários da plataforma por publicações no período."""
    rows = db.execute(
        select(
            User.id,
            User.username,
            User.display_name,
            User.avatar_key,
            func.count(PublishLog.id).label("post_count"),
        )
        .join(InstagramAccount, InstagramAccount.user_id == User.id)
        .join(PublishLog, PublishLog.account_id == InstagramAccount.id)
        .where(
            PublishLog.status == "success",
            PublishLog.created_at >= _utc_naive(start),
            PublishLog.created_at < _utc_naive(end),
        )
        .group_by(User.id, User.username, User.display_name, User.avatar_key)
        .order_by(desc(func.count(PublishLog.id)))
        .limit(5)
    ).all()
    return [
        {
            "user_id": r.id,
            "username": r.username,
            "display_name": (r.display_name or r.username),
            "avatar_url": f"/media/{r.avatar_key}" if r.avatar_key else None,
            "post_count": int(r.post_count),
        }
        for r in rows
    ]


def _top_platform_players_today(db: Session, day: dt.date) -> list[dict]:
    start, end = _brt_day_bounds(day)
    items = _top_platform_players(db, start, end)
    return [{**item, "posts_today": item["post_count"]} for item in items]


@router.get("/")
def home(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(maybe_current_user),
):
    if user is None:
        return RedirectResponse("/login", status_code=303)

    today = brt_now().date()
    yesterday = today - dt.timedelta(days=1)
    month_start = today.replace(day=1)

    accounts = db.scalars(
        select(InstagramAccount)
        .where(InstagramAccount.user_id == user.id)
        .order_by(InstagramAccount.username.asc())
    ).all()

    accounts_count = len(accounts)
    active_automations = db.scalar(
        select(func.count(Automation.id)).where(
            Automation.user_id == user.id,
            Automation.status == "active",
        )
    ) or 0
    total_automations = db.scalar(
        select(func.count(Automation.id)).where(Automation.user_id == user.id)
    ) or 0

    pubs_today = _count_logs(db, user.id, day=today)
    pubs_yesterday = _count_logs(db, user.id, day=yesterday)
    pubs_growth = _growth_pct(pubs_today, pubs_yesterday)

    success_today = _count_logs(db, user.id, status="success", day=today)
    failed_today = _count_logs(db, user.id, status="failed", day=today)
    total_logs_today = success_today + failed_today + _count_logs(
        db, user.id, status="skipped", day=today
    )
    success_rate = round(success_today / total_logs_today * 100, 1) if total_logs_today else 0.0

    success_yesterday = _count_logs(db, user.id, status="success", day=yesterday)
    failed_yesterday = _count_logs(db, user.id, status="failed", day=yesterday)
    total_yesterday = success_yesterday + failed_yesterday
    rate_yesterday = round(success_yesterday / total_yesterday * 100, 1) if total_yesterday else 0.0
    rate_delta = round(success_rate - rate_yesterday, 1) if total_yesterday or total_logs_today else None

    new_accounts_month = db.scalar(
        select(func.count(InstagramAccount.id)).where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.created_at >= _utc_naive(_brt_day_bounds(month_start)[0]),
        )
    ) or 0

    new_automations_month = db.scalar(
        select(func.count(Automation.id)).where(
            Automation.user_id == user.id,
            Automation.created_at >= _utc_naive(_brt_day_bounds(month_start)[0]),
        )
    ) or 0

    automations = db.scalars(
        select(Automation)
        .where(Automation.user_id == user.id)
        .options(selectinload(Automation.accounts))
        .order_by(desc(Automation.created_at))
    ).all()

    next_publications = sorted(
        [a for a in automations if a.status == "active" and a.next_run_at],
        key=lambda a: a.next_run_at or dt.datetime.max.replace(tzinfo=dt.timezone.utc),
    )[:6]

    account_publish_counts: dict[int, int] = dict(
        db.execute(
            select(PublishLog.account_id, func.count(PublishLog.id))
            .join(PublishLog.account)
            .where(
                InstagramAccount.user_id == user.id,
                PublishLog.status == "success",
            )
            .group_by(PublishLog.account_id)
        ).all()
    )

    account_views: dict[int, int] = dict(
        db.execute(
            select(PublishLog.account_id, func.coalesce(func.sum(PublishLog.play_count), 0))
            .join(PublishLog.account)
            .where(
                InstagramAccount.user_id == user.id,
                PublishLog.status == "success",
                PublishLog.play_count.is_not(None),
            )
            .group_by(PublishLog.account_id)
        ).all()
    )

    total_views = db.scalar(
        select(func.coalesce(func.sum(PublishLog.play_count), 0))
        .join(PublishLog.account)
        .where(
            InstagramAccount.user_id == user.id,
            PublishLog.status == "success",
            PublishLog.play_count.is_not(None),
        )
    ) or 0

    accounts_data = [
        {
            "account": acc,
            "publish_count": account_publish_counts.get(acc.id, 0),
            "total_views": int(account_views.get(acc.id, 0) or 0),
        }
        for acc in accounts
    ]
    accounts_data.sort(key=lambda x: x["publish_count"], reverse=True)

    top_players = _top_platform_players_today(db, today)
    month_start = today.replace(day=1)
    top_players_month = _top_platform_players(
        db, _brt_day_bounds(month_start)[0], _brt_day_bounds(today)[1]
    )

    top_reels = db.scalars(
        select(PublishLog)
        .join(PublishLog.account)
        .where(
            InstagramAccount.user_id == user.id,
            PublishLog.status == "success",
            PublishLog.play_count.is_not(None),
        )
        .options(selectinload(PublishLog.account), selectinload(PublishLog.automation))
        .order_by(desc(PublishLog.play_count))
        .limit(8)
    ).all()

    pending_views = db.scalar(
        select(func.count(PublishLog.id))
        .join(PublishLog.account)
        .where(
            InstagramAccount.user_id == user.id,
            PublishLog.status == "success",
            PublishLog.media_id.is_not(None),
            PublishLog.play_count.is_(None),
        )
    ) or 0
    if pending_views:
        try:
            from celery_app.tasks.insights import sync_all_views
            sync_all_views.delay()
        except Exception:
            pass

    recent_logs = db.scalars(
        select(PublishLog)
        .join(PublishLog.account)
        .where(InstagramAccount.user_id == user.id)
        .options(selectinload(PublishLog.account), selectinload(PublishLog.automation))
        .order_by(desc(PublishLog.created_at))
        .limit(12)
    ).all()

    chart_performance = _chart_performance_7d(db, user.id)
    chart_weekly = _chart_weekly_bars(db, user.id)
    offline = offline_accounts(db, user.id)

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "accounts_count": accounts_count,
            "accounts_data": accounts_data,
            "active_automations": active_automations,
            "total_automations": total_automations,
            "automations": automations,
            "pubs_today": pubs_today,
            "pubs_growth": pubs_growth,
            "success_rate": success_rate,
            "rate_delta": rate_delta,
            "new_accounts_month": new_accounts_month,
            "new_automations_month": new_automations_month,
            "next_publications": next_publications,
            "recent_logs": recent_logs,
            "chart_performance": chart_performance,
            "chart_weekly": chart_weekly,
            "now_brt": brt_now(),
            "offline_accounts": offline,
            "total_views": int(total_views),
            "top_players": top_players,
            "top_players_month": top_players_month,
            "top_reels": top_reels,
        },
    )


@router.get("/analytics")
def analytics_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(maybe_current_user),
):
    if user is None:
        return RedirectResponse("/login", status_code=303)

    today = brt_now().date()
    yesterday = today - dt.timedelta(days=1)

    accounts = db.scalars(
        select(InstagramAccount)
        .where(InstagramAccount.user_id == user.id)
        .order_by(InstagramAccount.username.asc())
    ).all()

    pubs_today = _count_logs(db, user.id, day=today)
    pubs_yesterday = _count_logs(db, user.id, day=yesterday)
    pubs_growth = _growth_pct(pubs_today, pubs_yesterday)

    success_today = _count_logs(db, user.id, status="success", day=today)
    failed_today = _count_logs(db, user.id, status="failed", day=today)
    skipped_today = _count_logs(db, user.id, status="skipped", day=today)
    total_today = success_today + failed_today + skipped_today
    success_rate = round(success_today / total_today * 100, 1) if total_today else 0.0

    success_total = _count_logs(db, user.id, status="success")
    failed_total = _count_logs(db, user.id, status="failed")
    skipped_total = _count_logs(db, user.id, status="skipped")

    account_stats = []
    for acc in accounts:
        ok = db.scalar(
            select(func.count(PublishLog.id)).where(
                PublishLog.account_id == acc.id,
                PublishLog.status == "success",
            )
        ) or 0
        fail = db.scalar(
            select(func.count(PublishLog.id)).where(
                PublishLog.account_id == acc.id,
                PublishLog.status == "failed",
            )
        ) or 0
        account_stats.append({"account": acc, "success": ok, "failed": fail})

    chart_performance = _chart_performance_7d(db, user.id)
    chart_weekly = _chart_weekly_bars(db, user.id)

    return templates.TemplateResponse(
        "analytics.html",
        {
            "request": request,
            "user": user,
            "accounts_count": len(accounts),
            "pubs_today": pubs_today,
            "pubs_growth": pubs_growth,
            "success_rate": success_rate,
            "success_today": success_today,
            "failed_today": failed_today,
            "skipped_today": skipped_today,
            "success_total": success_total,
            "failed_total": failed_total,
            "skipped_total": skipped_total,
            "account_stats": account_stats,
            "chart_performance": chart_performance,
            "chart_weekly": chart_weekly,
        },
    )
