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
from app.utils.charts import attach_chart_paths
from app.utils.timezone import brt_now
from core.database import get_db
from models.models import Automation, InstagramAccount, PublishLog, User, WarmupJob

router = APIRouter(tags=["dashboard"])
BRT = ZoneInfo("America/Sao_Paulo")
WEEKDAY_LABELS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
ALLOWED_CHART_DAYS = {7, 15, 30}
VISIBLE_ACCOUNT_STATUSES = ("active", "paused", "needs_login", "proxy_down", "banned")


def _parse_chart_days(raw: str | int | None) -> int:
    try:
        days = int(raw or 7)
    except (TypeError, ValueError):
        return 7
    return days if days in ALLOWED_CHART_DAYS else 7


def _brt_day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(day, dt.time.min, tzinfo=BRT)
    end = start + dt.timedelta(days=1)
    return start, end


def _utc_naive(d: dt.datetime) -> dt.datetime:
    if d.tzinfo is None:
        return d
    return d.astimezone(dt.timezone.utc).replace(tzinfo=None)


def _brt_date_from_db(value: dt.datetime) -> dt.date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(BRT).date()


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


def _status_counts_for_days(
    db: Session,
    user_id: int,
    days: list[dt.date],
) -> dict[dt.date, dict[str, int]]:
    if not days:
        return {}

    first_start, _ = _brt_day_bounds(min(days))
    _, last_end = _brt_day_bounds(max(days))
    rows = db.execute(
        select(
            PublishLog.created_at,
            PublishLog.status,
        )
        .join(PublishLog.account)
        .where(
            InstagramAccount.user_id == user_id,
            PublishLog.created_at >= _utc_naive(first_start),
            PublishLog.created_at < _utc_naive(last_end),
        )
    ).all()

    out = {d: {"success": 0, "failed": 0, "skipped": 0} for d in days}
    for row in rows:
        day = _brt_date_from_db(row.created_at)
        if day is None:
            continue
        if day not in out:
            continue
        out[day][row.status] = out[day].get(row.status, 0) + 1
    return out


def _chart_performance(db: Session, user_id: int, days: int = 7) -> list[dict]:
    days = _parse_chart_days(days)
    today = brt_now().date()
    day_list = [today - dt.timedelta(days=i) for i in range(days - 1, -1, -1)]
    by_day = _status_counts_for_days(db, user_id, day_list)

    max_val = 1
    chart = []
    for d in day_list:
        stats = by_day.get(d, {"success": 0, "failed": 0, "skipped": 0})
        pubs = stats["success"]
        max_val = max(max_val, pubs, stats["success"], stats["failed"])
        # 7D: dia da semana; 15/30D: data curta para caber no eixo
        label = WEEKDAY_LABELS[d.weekday()] if days <= 7 else d.strftime("%d/%m")
        chart.append({
            "label": label,
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


def _dashboard_day_totals(db: Session, user_id: int, today: dt.date, yesterday: dt.date) -> dict:
    counts = _status_counts_for_days(db, user_id, [yesterday, today])
    today_counts = counts.get(today, {"success": 0, "failed": 0, "skipped": 0})
    yesterday_counts = counts.get(yesterday, {"success": 0, "failed": 0, "skipped": 0})
    success_today = today_counts.get("success", 0)
    pubs_today = success_today
    success_yesterday = yesterday_counts.get("success", 0)
    pubs_yesterday = success_yesterday
    total_logs_today = sum(today_counts.values())
    total_yesterday = sum(yesterday_counts.values())
    return {
        "pubs_today": pubs_today,
        "pubs_yesterday": pubs_yesterday,
        "success_today": success_today,
        "total_logs_today": total_logs_today,
        "success_yesterday": success_yesterday,
        "total_yesterday": total_yesterday,
    }


def _chart_performance_7d(db: Session, user_id: int) -> list[dict]:
    return _chart_performance(db, user_id, 7)


def _chart_weekly_bars(db: Session, user_id: int, days: int = 7) -> list[dict]:
    chart = _chart_performance(db, user_id, days)
    max_val = max((pt["pubs"] for pt in chart), default=0) or 1
    for pt in chart:
        pt["bar_pct"] = round(pt["pubs"] / max_val * 100, 1)
    return chart


def _growth_pct(current: int, previous: int) -> float | None:
    if previous == 0:
        return 100.0 if current > 0 else None
    return round((current - previous) / previous * 100, 1)


def _top_platform_players(
    db: Session,
    start: dt.datetime,
    end: dt.datetime,
    viewer: User | None = None,
) -> list[dict]:
    """Top 5 usuários da plataforma por publicações no período.

    Usuários marcados como privados do owner só aparecem no rank para o
    próprio owner e para os outros usuários privados dele.
    """
    query = (
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
    )
    viewer_sees_private = bool(
        viewer is not None
        and (getattr(viewer, "is_owner", False) or getattr(viewer, "owner_private", False))
    )
    if not viewer_sees_private:
        query = query.where(User.owner_private.isnot(True))
    rows = db.execute(
        query
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


def _top_platform_players_week(db: Session, day: dt.date, viewer: User | None = None) -> list[dict]:
    """Top 5 dos últimos 7 dias (BRT) — não zera à meia-noite."""
    start_day = day - dt.timedelta(days=6)
    start, _ = _brt_day_bounds(start_day)
    _, end = _brt_day_bounds(day)
    items = _top_platform_players(db, start, end, viewer=viewer)
    return [{**item, "posts_today": item["post_count"]} for item in items]


@router.get("/")
def home(
    request: Request,
    days: int = 7,
    db: Session = Depends(get_db),
    user: User | None = Depends(maybe_current_user),
):
    if user is None:
        return RedirectResponse("/login", status_code=303)

    chart_days = _parse_chart_days(days)
    today = brt_now().date()
    yesterday = today - dt.timedelta(days=1)
    month_start = today.replace(day=1)

    accounts = db.scalars(
        select(InstagramAccount)
        .where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
        )
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

    day_totals = _dashboard_day_totals(db, user.id, today, yesterday)
    pubs_today = day_totals["pubs_today"]
    pubs_yesterday = day_totals["pubs_yesterday"]
    pubs_growth = _growth_pct(pubs_today, pubs_yesterday)

    success_today = day_totals["success_today"]
    total_logs_today = day_totals["total_logs_today"]
    success_rate = round(success_today / total_logs_today * 100, 1) if total_logs_today else 0.0

    success_yesterday = day_totals["success_yesterday"]
    total_yesterday = day_totals["total_yesterday"]
    rate_yesterday = round(success_yesterday / total_yesterday * 100, 1) if total_yesterday else 0.0
    rate_delta = round(success_rate - rate_yesterday, 1) if total_yesterday or total_logs_today else None

    new_accounts_month = db.scalar(
        select(func.count(InstagramAccount.id)).where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
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
        .where(Automation.user_id == user.id, Automation.status == "active")
        .options(selectinload(Automation.accounts))
        .order_by(Automation.next_run_at.asc().nullslast(), desc(Automation.created_at))
        .limit(8)
    ).all()

    next_publications = db.scalars(
        select(Automation)
        .where(
            Automation.user_id == user.id,
            Automation.status == "active",
            Automation.next_run_at.is_not(None),
        )
        .options(selectinload(Automation.accounts))
        .order_by(Automation.next_run_at.asc())
        .limit(6)
    ).all()

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

    accounts_data = [
        {
            "account": acc,
            "publish_count": account_publish_counts.get(acc.id, 0),
        }
        for acc in accounts
    ]
    accounts_data.sort(key=lambda x: x["publish_count"], reverse=True)

    top_players = _top_platform_players_week(db, today, viewer=user)
    month_start = today.replace(day=1)
    top_players_month = _top_platform_players(
        db, _brt_day_bounds(month_start)[0], _brt_day_bounds(today)[1], viewer=user
    )

    warming_jobs = db.scalars(
        select(WarmupJob)
        .where(
            WarmupJob.user_id == user.id,
            WarmupJob.status.in_(("pending", "running", "paused")),
        )
        .options(selectinload(WarmupJob.account))
        .order_by(desc(WarmupJob.updated_at))
        .limit(8)
    ).all()

    failed_videos = db.scalars(
        select(PublishLog)
        .join(PublishLog.account)
        .where(
            InstagramAccount.user_id == user.id,
            PublishLog.status == "failed",
        )
        .options(selectinload(PublishLog.account), selectinload(PublishLog.automation))
        .order_by(desc(PublishLog.created_at))
        .limit(8)
    ).all()

    recent_logs = db.scalars(
        select(PublishLog)
        .join(PublishLog.account)
        .where(InstagramAccount.user_id == user.id)
        .options(selectinload(PublishLog.account), selectinload(PublishLog.automation))
        .order_by(desc(PublishLog.created_at))
        .limit(12)
    ).all()

    chart_performance = _chart_performance(db, user.id, chart_days)
    chart_performance, chart_line_path, chart_area_path, chart_max_val = attach_chart_paths(
        chart_performance
    )
    chart_weekly = _chart_weekly_bars(db, user.id, min(chart_days, 7) if chart_days == 7 else 7)
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
            "chart_line_path": chart_line_path,
            "chart_area_path": chart_area_path,
            "chart_max_val": chart_max_val,
            "chart_days": chart_days,
            "chart_weekly": chart_weekly,
            "now_brt": brt_now(),
            "offline_accounts": offline,
            "total_views": 0,
            "top_players": top_players,
            "top_players_month": top_players_month,
            "top_reels": [],
            "warming_jobs": warming_jobs,
            "failed_videos": failed_videos,
            "pending_views": 0,
        },
    )


@router.get("/analytics")
def analytics_page(
    request: Request,
    days: int = 7,
    db: Session = Depends(get_db),
    user: User | None = Depends(maybe_current_user),
):
    if user is None:
        return RedirectResponse("/login", status_code=303)

    chart_days = _parse_chart_days(days)
    today = brt_now().date()
    yesterday = today - dt.timedelta(days=1)

    accounts = db.scalars(
        select(InstagramAccount)
        .where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
        )
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

    chart_performance = _chart_performance(db, user.id, chart_days)
    chart_performance, chart_line_path, chart_area_path, chart_max_val = attach_chart_paths(
        chart_performance
    )
    chart_weekly = _chart_weekly_bars(db, user.id, 7)

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
            "chart_line_path": chart_line_path,
            "chart_area_path": chart_area_path,
            "chart_max_val": chart_max_val,
            "chart_days": chart_days,
            "chart_weekly": chart_weekly,
        },
    )
