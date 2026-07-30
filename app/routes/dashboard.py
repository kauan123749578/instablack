"""Dashboard premium instablack."""
from __future__ import annotations

import datetime as dt
import time
from zoneinfo import ZoneInfo

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import desc, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, load_only, selectinload

from app.deps import get_current_user, maybe_current_user, maybe_effective_user
from app.templating import templates
from app.config import settings
from app.utils.charts import attach_chart_paths
from app.utils.official_analytics import user_official_insights_summary
from app.utils.timezone import brt_now
from core.database import get_db
from models.models import Automation, InstagramAccount, PublishLog, User, automation_accounts

router = APIRouter(tags=["dashboard"])
log = logging.getLogger(__name__)
BRT = ZoneInfo("America/Sao_Paulo")
WEEKDAY_LABELS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
ALLOWED_CHART_DAYS = {7, 15, 30}
VISIBLE_ACCOUNT_STATUSES = ("active", "paused", "needs_login", "proxy_down", "banned")
_AUTOMATION_DASH_COLS = (
    Automation.id,
    Automation.name,
    Automation.content_type,
    Automation.interval_minutes,
    Automation.status,
    Automation.next_run_at,
    Automation.video_key,
    Automation.thumb_key,
    Automation.video_original_name,
)
_RANK_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
_RANK_CACHE_TTL = 120.0
_PROFILE_PIC_ENQUEUE_AT: dict[int, float] = {}
_PROFILE_PIC_ENQUEUE_COOLDOWN = 90.0


def _maybe_enqueue_profile_pics(user_id: int, accounts: list[InstagramAccount]) -> None:
    """Dispara backfill de fotos Meta se alguma conta ainda não tem /media/avatar."""
    missing = [
        int(a.id)
        for a in accounts
        if getattr(a, "provider", None) == "meta"
        and not (getattr(a, "profile_pic_url", None) or "").startswith("/media/")
    ]
    if not missing:
        return
    now = time.monotonic()
    last = _PROFILE_PIC_ENQUEUE_AT.get(user_id, 0.0)
    if now - last < _PROFILE_PIC_ENQUEUE_COOLDOWN:
        return
    _PROFILE_PIC_ENQUEUE_AT[user_id] = now
    try:
        from celery_app.tasks.insights import refresh_missing_profile_pics

        refresh_missing_profile_pics.delay(missing[:40])
        log.info(
            "enfileirou refresh_missing_profile_pics user=%s n=%s",
            user_id,
            min(len(missing), 40),
        )
    except Exception as exc:
        log.warning("falha ao enfileirar profile pics: %s", exc)


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


def _visible_account_ids(db: Session, user_id: int) -> list[int]:
    return list(
        db.scalars(
            select(InstagramAccount.id).where(
                InstagramAccount.user_id == user_id,
                InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
            )
        ).all()
    )


def _automation_account_counts(db: Session, automation_ids: list[int]) -> dict[int, int]:
    if not automation_ids:
        return {}
    return dict(
        db.execute(
            select(automation_accounts.c.automation_id, func.count())
            .where(automation_accounts.c.automation_id.in_(automation_ids))
            .group_by(automation_accounts.c.automation_id)
        ).all()
    )


def _count_logs_by_accounts(
    db: Session,
    account_ids: list[int],
    *,
    status: str | None = None,
    day: dt.date | None = None,
) -> int:
    if not account_ids:
        return 0
    q = select(func.count(PublishLog.id)).where(PublishLog.account_id.in_(account_ids))
    if status:
        q = q.where(PublishLog.status == status)
    if day is not None:
        start, end = _brt_day_bounds(day)
        q = q.where(
            PublishLog.created_at >= _utc_naive(start),
            PublishLog.created_at < _utc_naive(end),
        )
    return db.scalar(q) or 0


def _count_logs(
    db: Session,
    user_id: int,
    *,
    status: str | None = None,
    day: dt.date | None = None,
    account_ids: list[int] | None = None,
) -> int:
    ids = account_ids if account_ids is not None else _visible_account_ids(db, user_id)
    return _count_logs_by_accounts(db, ids, status=status, day=day)


def _recent_publish_logs(
    db: Session,
    account_ids: list[int],
    *,
    limit: int = 12,
    status: str | None = None,
    user: User | None = None,
) -> list[PublishLog]:
    """Últimos PublishLog para cards do dashboard (HTML, sem depender do poll JS)."""
    if not account_ids:
        return []
    q = (
        select(PublishLog)
        .where(PublishLog.account_id.in_(account_ids))
        .options(
            selectinload(PublishLog.account),
            selectinload(PublishLog.automation),
        )
        .order_by(PublishLog.id.desc())
        .limit(limit)
    )
    if status:
        q = q.where(PublishLog.status == status)
    cleared_at = getattr(user, "logs_cleared_at", None) if user is not None else None
    if cleared_at is not None:
        q = q.where(PublishLog.created_at > cleared_at)
    return list(db.scalars(q).all())


def _batch_status_counts(
    db: Session,
    account_ids: list[int],
    days: list[dt.date],
) -> dict[dt.date, dict[str, int]]:
    """Uma query (Postgres) ou um scan limitado (SQLite) para todos os dias do gráfico/KPI."""
    out: dict[dt.date, dict[str, int]] = {
        d: {"success": 0, "failed": 0, "skipped": 0} for d in days
    }
    if not account_ids or not days:
        return out

    first_start, _ = _brt_day_bounds(min(days))
    _, last_end = _brt_day_bounds(max(days))
    time_filters = (
        PublishLog.created_at >= _utc_naive(first_start),
        PublishLog.created_at < _utc_naive(last_end),
    )

    # Bucket em Python com o mesmo BRT de _count_logs_by_accounts / Top do Dia.
    # Agrupar no Postgres com timezone('UTC', timestamptz) + America/Sao_Paulo
    # jogava posts da noite de hoje no dia seguinte → KPI 0 e gráfico vazio.
    rows = db.execute(
        select(PublishLog.created_at, PublishLog.status)
        .where(PublishLog.account_id.in_(account_ids), *time_filters)
    ).all()
    for created_at, status in rows:
        day = _brt_date_from_db(created_at)
        if day in out and status in out[day]:
            out[day][status] += 1
    return out


def _status_counts_for_days(
    db: Session,
    user_id: int,
    days: list[dt.date],
    account_ids: list[int] | None = None,
) -> dict[dt.date, dict[str, int]]:
    ids = account_ids if account_ids is not None else _visible_account_ids(db, user_id)
    return _batch_status_counts(db, ids, days)


def _chart_performance(
    db: Session,
    user_id: int,
    days: int = 7,
    account_ids: list[int] | None = None,
) -> list[dict]:
    days = _parse_chart_days(days)
    today = brt_now().date()
    day_list = [today - dt.timedelta(days=i) for i in range(days - 1, -1, -1)]
    ids = account_ids if account_ids is not None else _visible_account_ids(db, user_id)
    by_day = _status_counts_for_days(db, user_id, day_list, account_ids=ids)

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


def _chart_weekly_from_performance(chart: list[dict]) -> list[dict]:
    max_val = max((pt["pubs"] for pt in chart), default=0) or 1
    weekly = []
    for pt in chart:
        copy = dict(pt)
        copy["bar_pct"] = round(copy["pubs"] / max_val * 100, 1)
        weekly.append(copy)
    return weekly


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


def _rank_sees_private_users(viewer: User | None) -> bool:
    """Owner e usuários Meu veem o rank completo; outros admins (ex.: caue) não."""
    if viewer is None:
        return False
    if getattr(viewer, "is_owner", False):
        return True
    return bool(getattr(viewer, "owner_private", False))


def _top_platform_players(
    db: Session,
    start: dt.datetime,
    end: dt.datetime,
    viewer: User | None = None,
    *,
    limit: int = 50,
) -> list[dict]:
    """Top usuários da plataforma por publicações no período.

    Usuários Meu (owner_private) só ficam ocultos no rank para quem não é Owner nem Meu.
    """
    query = (
        select(
            User.id,
            User.username,
            User.display_name,
            User.avatar_key,
            func.count(PublishLog.id).label("post_count"),
            func.coalesce(func.sum(PublishLog.play_count), 0).label("view_count"),
        )
        .join(InstagramAccount, InstagramAccount.user_id == User.id)
        .join(PublishLog, PublishLog.account_id == InstagramAccount.id)
        .where(
            PublishLog.status == "success",
            PublishLog.created_at >= _utc_naive(start),
            PublishLog.created_at < _utc_naive(end),
        )
    )
    if not _rank_sees_private_users(viewer):
        query = query.where(User.owner_private.isnot(True))
    rows = db.execute(
        query
        .group_by(User.id, User.username, User.display_name, User.avatar_key)
        .order_by(desc(func.count(PublishLog.id)))
        .limit(max(1, min(int(limit or 50), 100)))
    ).all()
    return [
        {
            "user_id": r.id,
            "username": r.username,
            "display_name": (r.display_name or r.username),
            "avatar_url": f"/media/{r.avatar_key}" if r.avatar_key else None,
            "post_count": int(r.post_count),
            "view_count": int(r.view_count or 0),
            "tier": _rank_tier(int(r.post_count)),
        }
        for r in rows
    ]


def _cached_top_platform_players(
    db: Session,
    start: dt.datetime,
    end: dt.datetime,
    viewer: User | None = None,
    *,
    limit: int = 50,
) -> list[dict]:
    viewer_key = (
        viewer.id if viewer else None,
        _rank_sees_private_users(viewer),
    )
    key = (start.isoformat(), end.isoformat(), viewer_key, limit)
    now = time.time()
    hit = _RANK_CACHE.get(key)
    if hit and now - hit[0] < _RANK_CACHE_TTL:
        return hit[1]
    data = _top_platform_players(db, start, end, viewer=viewer, limit=limit)
    _RANK_CACHE[key] = (now, data)
    if len(_RANK_CACHE) > 48:
        _RANK_CACHE.clear()
    return data


def _rank_tier(posts: int) -> str:
    """Faixas calibradas para ranking diário."""
    if posts >= 50:
        return "LENDA"
    if posts >= 25:
        return "ELITE"
    if posts >= 10:
        return "PRO"
    if posts >= 3:
        return "RISING"
    return "PLAYER"


def _viewer_rank_entry(
    db: Session,
    start: dt.datetime,
    end: dt.datetime,
    viewer: User,
) -> dict | None:
    """Posição do usuário logado no ranking (mesmo fora do top)."""
    my_count = db.scalar(
        select(func.count(PublishLog.id))
        .join(InstagramAccount, PublishLog.account_id == InstagramAccount.id)
        .where(
            InstagramAccount.user_id == viewer.id,
            PublishLog.status == "success",
            PublishLog.created_at >= _utc_naive(start),
            PublishLog.created_at < _utc_naive(end),
        )
    ) or 0
    my_count = int(my_count)
    my_views = db.scalar(
        select(func.coalesce(func.sum(PublishLog.play_count), 0))
        .join(InstagramAccount, PublishLog.account_id == InstagramAccount.id)
        .where(
            InstagramAccount.user_id == viewer.id,
            PublishLog.status == "success",
            PublishLog.created_at >= _utc_naive(start),
            PublishLog.created_at < _utc_naive(end),
        )
    ) or 0
    my_views = int(my_views)

    better_filters = [
        PublishLog.status == "success",
        PublishLog.created_at >= _utc_naive(start),
        PublishLog.created_at < _utc_naive(end),
    ]
    if not _rank_sees_private_users(viewer):
        better_filters.append(User.owner_private.isnot(True))
    better_q = (
        select(func.count())
        .select_from(
            select(User.id)
            .join(InstagramAccount, InstagramAccount.user_id == User.id)
            .join(PublishLog, PublishLog.account_id == InstagramAccount.id)
            .where(*better_filters)
            .group_by(User.id)
            .having(func.count(PublishLog.id) > my_count)
            .subquery()
        )
    )
    better = int(db.scalar(better_q) or 0)
    return {
        "user_id": viewer.id,
        "username": viewer.username,
        "display_name": (viewer.display_name or viewer.username),
        "avatar_url": f"/media/{viewer.avatar_key}" if viewer.avatar_key else None,
        "post_count": my_count,
        "view_count": my_views,
        "rank": better + 1 if my_count > 0 else None,
        "tier": _rank_tier(my_count),
    }


def _top_platform_players_today(db: Session, day: dt.date, viewer: User | None = None) -> list[dict]:
    """Top da plataforma só no dia (BRT)."""
    start, end = _brt_day_bounds(day)
    items = _cached_top_platform_players(db, start, end, viewer=viewer, limit=50)
    return [{**item, "posts_today": item["post_count"]} for item in items]


@router.get("/api/dashboard/rank")
def api_dashboard_rank(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Ranking diário fora do GET / — evita travar o login no dashboard."""
    today = brt_now().date()
    top_players = _top_platform_players_today(db, today, viewer=user)
    today_start, today_end = _brt_day_bounds(today)
    my_rank = _viewer_rank_entry(db, today_start, today_end, user)
    return {
        "top_players": [
            {
                "display_name": p["display_name"],
                "avatar_url": p.get("avatar_url"),
                "tier": p["tier"],
                "posts_today": p["posts_today"],
                "view_count": int(p.get("view_count") or 0),
            }
            for p in top_players
        ],
        "my_rank": my_rank,
    }


@router.get("/api/dashboard/kpi")
def api_dashboard_kpi(
    days: int = 7,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """KPIs do painel — carregados após GET / para não travar o login."""
    chart_days = _parse_chart_days(days)
    return JSONResponse(_dashboard_fast_context(db, user, chart_days))


@router.get("/api/dashboard/load")
def api_dashboard_load(
    request: Request,
    days: int = 7,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """HTML do painel pesado — carregado após login para não travar GET /."""
    chart_days = _parse_chart_days(days)
    ctx = _dashboard_heavy_context(db, user, chart_days)
    ctx["request"] = request
    ctx["user"] = user
    return templates.TemplateResponse("partials/dashboard_heavy.html", ctx)


def _dashboard_shell_context(chart_days: int) -> dict:
    """Shell instantâneo — zero queries além da sessão."""
    return {
        "chart_days": chart_days,
        "kpi_lazy": True,
        "dash_lazy": True,
        "accounts_count": 0,
        "active_automations": 0,
        "total_automations": 0,
        "pubs_today": 0,
        "pubs_growth": None,
        "success_rate": 0,
        "rate_delta": None,
        "new_accounts_month": 0,
        "new_automations_month": 0,
        "now_brt": brt_now(),
        "recent_activity": [],
        "recent_failures": [],
        "latest_log_id": 0,
    }


def _dashboard_safe_scalar(db: Session, stmt, *, default=0, label: str = "kpi"):
    """KPI leve: nunca derruba o painel se o Postgres cancelar por timeout/lock."""
    try:
        if not settings.is_sqlite:
            # lock_timeout: sob FOR UPDATE do worker, falha em ms — não espera 2.5s.
            db.execute(text("SET LOCAL lock_timeout = '500ms'"))
            db.execute(text("SET LOCAL statement_timeout = '1500ms'"))
        return db.scalar(stmt) or default
    except OperationalError as exc:
        log.warning("dashboard %s timeout/erro — fallback %s: %s", label, default, exc)
        try:
            db.rollback()
        except Exception:
            pass
        return default


def _dashboard_context(db: Session, user: User, chart_days: int) -> dict:
    """Monta o painel inteiro com o mínimo de round-trips ao Postgres."""
    if not settings.is_sqlite:
        # lock_timeout: COUNT/SELECT não esperam o worker soltar FOR UPDATE.
        db.execute(text("SET LOCAL lock_timeout = '500ms'"))
        # 4s: Insights agregados + gráfico; 1.5s estourava e o painel vinha vazio.
        db.execute(text("SET LOCAL statement_timeout = '4000ms'"))

    today = brt_now().date()
    yesterday = today - dt.timedelta(days=1)
    month_start = today.replace(day=1)
    chart_days = _parse_chart_days(chart_days)
    day_list = [today - dt.timedelta(days=i) for i in range(chart_days - 1, -1, -1)]

    try:
        account_ids = _visible_account_ids(db, user.id)
        log_by_day = _batch_status_counts(db, account_ids, day_list)
        accounts = db.scalars(
            select(InstagramAccount)
            .where(
                InstagramAccount.user_id == user.id,
                InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
            )
            .order_by(InstagramAccount.username.asc())
        ).all()
    except OperationalError as exc:
        log.warning("dashboard accounts/batch timeout — shell mínimo: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        if not settings.is_sqlite:
            try:
                db.execute(text("SET LOCAL lock_timeout = '500ms'"))
                db.execute(text("SET LOCAL statement_timeout = '4000ms'"))
            except Exception:
                pass
        account_ids = []
        log_by_day = {}
        accounts = []

    # NÃO fazer COUNT(*) em automations — sob FOR UPDATE do worker isso trava
    # o painel (QueryCanceled em loop). KPI vem da lista limitada abaixo.
    new_accounts_month = _dashboard_safe_scalar(
        db,
        select(func.count(InstagramAccount.id)).where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
            InstagramAccount.created_at >= _utc_naive(_brt_day_bounds(month_start)[0]),
        ),
        label="new_accounts_month",
    )
    new_automations_month = 0

    # KPI do dia: bounds BRT (igual /logs e Top do Dia), não só o dict do gráfico.
    pubs_today = _count_logs_by_accounts(db, account_ids, status="success", day=today)
    pubs_yesterday = _count_logs_by_accounts(db, account_ids, status="success", day=yesterday)
    failed_today = _count_logs_by_accounts(db, account_ids, status="failed", day=today)
    skipped_today = _count_logs_by_accounts(db, account_ids, status="skipped", day=today)
    failed_yesterday = _count_logs_by_accounts(db, account_ids, status="failed", day=yesterday)
    skipped_yesterday = _count_logs_by_accounts(db, account_ids, status="skipped", day=yesterday)
    pubs_growth = _growth_pct(pubs_today, pubs_yesterday)
    total_logs_today = pubs_today + failed_today + skipped_today
    total_yesterday = pubs_yesterday + failed_yesterday + skipped_yesterday
    success_rate = round(pubs_today / total_logs_today * 100, 1) if total_logs_today else 0.0
    rate_yesterday = (
        round(pubs_yesterday / total_yesterday * 100, 1) if total_yesterday else 0.0
    )
    rate_delta = round(success_rate - rate_yesterday, 1) if total_yesterday or total_logs_today else None

    try:
        automations = db.scalars(
            select(Automation)
            .where(Automation.user_id == user.id, Automation.status == "active")
            .options(load_only(*_AUTOMATION_DASH_COLS))
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
            .options(load_only(*_AUTOMATION_DASH_COLS))
            .order_by(Automation.next_run_at.asc())
            .limit(6)
        ).all()
        auto_ids = list({a.id for a in automations} | {a.id for a in next_publications})
        automation_account_counts = _automation_account_counts(db, auto_ids)
    except OperationalError as exc:
        log.warning("dashboard automations list timeout — lista vazia: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        automations = []
        next_publications = []
        automation_account_counts = {}

    active_automations = len(automations)
    total_automations = active_automations

    # Contagem de posts por conta (30d). Timeout local já está em 1500ms.
    account_publish_counts: dict[int, int] = {}
    if account_ids:
        try:
            account_publish_counts = dict(
                db.execute(
                    select(PublishLog.account_id, func.count(PublishLog.id))
                    .where(
                        PublishLog.account_id.in_(account_ids),
                        PublishLog.status == "success",
                        PublishLog.created_at
                        >= _utc_naive(_brt_day_bounds(today - dt.timedelta(days=30))[0]),
                    )
                    .group_by(PublishLog.account_id)
                ).all()
            )
        except OperationalError as exc:
            log.warning("dashboard account publish counts timeout: %s", exc)
            try:
                db.rollback()
            except Exception:
                pass
            if not settings.is_sqlite:
                try:
                    db.execute(text("SET LOCAL lock_timeout = '500ms'"))
                    db.execute(text("SET LOCAL statement_timeout = '1500ms'"))
                except Exception:
                    pass

    accounts_data = [
        {"account": acc, "publish_count": account_publish_counts.get(acc.id, 0)}
        for acc in accounts
    ]
    accounts_data.sort(key=lambda x: (-x["publish_count"], x["account"].username.lower()))
    _maybe_enqueue_profile_pics(user.id, accounts)

    max_val = 1
    chart_performance: list[dict] = []
    for d in day_list:
        stats = log_by_day.get(d, {"success": 0, "failed": 0, "skipped": 0})
        pubs = stats["success"]
        max_val = max(max_val, pubs, stats["failed"])
        label = WEEKDAY_LABELS[d.weekday()] if chart_days <= 7 else d.strftime("%d/%m")
        chart_performance.append(
            {
                "label": label,
                "date": d.strftime("%d/%m"),
                "pubs": pubs,
                "success": stats["success"],
                "failed": stats["failed"],
                "skipped": stats["skipped"],
            }
        )
    for pt in chart_performance:
        m = max_val or 1
        pt["pubs_pct"] = round(pt["pubs"] / m * 100, 1)
        pt["success_pct"] = round(pt["success"] / m * 100, 1)
        pt["failed_pct"] = round(pt["failed"] / m * 100, 1)

    chart_performance, chart_line_path, chart_area_path, chart_max_val = attach_chart_paths(
        chart_performance
    )

    try:
        official = user_official_insights_summary(
            db,
            user.id,
            reel_views_days=chart_days,
            include_recent_reels=False,
        )
    except OperationalError as exc:
        log.warning("dashboard official insights timeout: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        from app.utils.official_analytics import empty_official_summary

        official = empty_official_summary(reel_views_days=chart_days)

    recent_activity: list[PublishLog] = []
    recent_failures: list[PublishLog] = []
    try:
        recent_activity = _recent_publish_logs(db, account_ids, limit=12, user=user)
        recent_failures = _recent_publish_logs(
            db, account_ids, limit=8, status="failed", user=user
        )
    except OperationalError as exc:
        log.warning("dashboard recent logs timeout: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass

    latest_log_id = recent_activity[0].id if recent_activity else 0

    return {
        "chart_days": chart_days,
        "accounts_count": len(account_ids),
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
        "automation_account_counts": automation_account_counts,
        "recent_activity": recent_activity,
        "recent_failures": recent_failures,
        "latest_log_id": latest_log_id,
        "chart_performance": chart_performance,
        "chart_line_path": chart_line_path,
        "chart_area_path": chart_area_path,
        "chart_max_val": chart_max_val,
        "now_brt": brt_now(),
        "official": official,
    }


def _dashboard_fast_context(db: Session, user: User, chart_days: int) -> dict:
    if not settings.is_sqlite:
        db.execute(text("SET LOCAL lock_timeout = '500ms'"))
        db.execute(text("SET LOCAL statement_timeout = '1500ms'"))

    today = brt_now().date()
    yesterday = today - dt.timedelta(days=1)
    month_start = today.replace(day=1)
    try:
        account_ids = _visible_account_ids(db, user.id)
    except OperationalError as exc:
        log.warning("dashboard fast account_ids timeout — fallback []: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        account_ids = []

    accounts_count = len(account_ids)
    # Sem COUNT em automations (lock do worker). KPI fica 0 aqui; lista no HTML.
    active_automations = 0
    total_automations = 0
    new_automations_month = 0

    pubs_today = _count_logs_by_accounts(db, account_ids, status="success", day=today)
    pubs_yesterday = _count_logs_by_accounts(db, account_ids, status="success", day=yesterday)
    pubs_growth = _growth_pct(pubs_today, pubs_yesterday)

    failed_today = _count_logs_by_accounts(db, account_ids, status="failed", day=today)
    skipped_today = _count_logs_by_accounts(db, account_ids, status="skipped", day=today)
    total_logs_today = pubs_today + failed_today + skipped_today
    success_rate = round(pubs_today / total_logs_today * 100, 1) if total_logs_today else 0.0

    failed_yesterday = _count_logs_by_accounts(db, account_ids, status="failed", day=yesterday)
    skipped_yesterday = _count_logs_by_accounts(db, account_ids, status="skipped", day=yesterday)
    total_yesterday = pubs_yesterday + failed_yesterday + skipped_yesterday
    rate_yesterday = round(pubs_yesterday / total_yesterday * 100, 1) if total_yesterday else 0.0
    rate_delta = round(success_rate - rate_yesterday, 1) if total_yesterday or total_logs_today else None

    new_accounts_month = _dashboard_safe_scalar(
        db,
        select(func.count(InstagramAccount.id)).where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
            InstagramAccount.created_at >= _utc_naive(_brt_day_bounds(month_start)[0]),
        ),
        label="new_accounts_month",
    )

    return {
        "chart_days": chart_days,
        "kpi_lazy": False,
        "accounts_count": accounts_count,
        "active_automations": active_automations,
        "total_automations": total_automations,
        "pubs_today": pubs_today,
        "pubs_growth": pubs_growth,
        "success_rate": success_rate,
        "rate_delta": rate_delta,
        "new_accounts_month": new_accounts_month,
        "new_automations_month": new_automations_month,
        "now_brt": brt_now(),
    }


def _dashboard_heavy_context(db: Session, user: User, chart_days: int) -> dict:
    today = brt_now().date()
    account_ids = _visible_account_ids(db, user.id)

    accounts = db.scalars(
        select(InstagramAccount)
        .where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
        )
        .order_by(InstagramAccount.username.asc())
    ).all()

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

    account_publish_counts: dict[int, int] = {}
    if account_ids:
        account_publish_counts = dict(
            db.execute(
                select(PublishLog.account_id, func.count(PublishLog.id))
                .where(
                    PublishLog.account_id.in_(account_ids),
                    PublishLog.status == "success",
                    PublishLog.created_at
                    >= _utc_naive(_brt_day_bounds(today - dt.timedelta(days=30))[0]),
                )
                .group_by(PublishLog.account_id)
            ).all()
        )

    accounts_data = [
        {"account": acc, "publish_count": account_publish_counts.get(acc.id, 0)}
        for acc in accounts
    ]
    accounts_data.sort(key=lambda x: x["publish_count"], reverse=True)
    _maybe_enqueue_profile_pics(user.id, accounts)

    recent_activity: list[PublishLog] = []
    recent_failures: list[PublishLog] = []
    try:
        recent_activity = _recent_publish_logs(db, account_ids, limit=12, user=user)
        recent_failures = _recent_publish_logs(
            db, account_ids, limit=8, status="failed", user=user
        )
    except OperationalError as exc:
        log.warning("dashboard heavy recent logs timeout: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass

    latest_log_id = recent_activity[0].id if recent_activity else 0

    chart_performance = _chart_performance(db, user.id, chart_days, account_ids=account_ids)
    chart_performance, chart_line_path, chart_area_path, chart_max_val = attach_chart_paths(
        chart_performance
    )

    return {
        "chart_days": chart_days,
        "accounts_data": accounts_data,
        "automations": automations,
        "next_publications": next_publications,
        "recent_activity": recent_activity,
        "recent_failures": recent_failures,
        "latest_log_id": latest_log_id,
        "chart_performance": chart_performance,
        "chart_line_path": chart_line_path,
        "chart_area_path": chart_area_path,
        "chart_max_val": chart_max_val,
        "official": user_official_insights_summary(
            db,
            user.id,
            reel_views_days=chart_days,
            include_recent_reels=False,
        ),
    }


@router.get("/")
def home(
    request: Request,
    days: int = 7,
    db: Session = Depends(get_db),
    user: User | None = Depends(maybe_effective_user),
):
    if user is None:
        return RedirectResponse("/login", status_code=303)

    chart_days = _parse_chart_days(days)
    ctx = _dashboard_context(db, user, chart_days)
    ctx["request"] = request
    ctx["user"] = user
    return templates.TemplateResponse("dashboard.html", ctx)


@router.get("/analytics")
def analytics_page(
    request: Request,
    days: int = 7,
    account_id: int | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(maybe_effective_user),
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
        provider = (acc.provider or "instagrapi").lower()
        if provider == "meta":
            provider_label = "API oficial"
        elif getattr(acc, "encrypted_web_cookies", None):
            provider_label = "API web"
        else:
            provider_label = "Instagrapi"
        account_stats.append(
            {
                "account": acc,
                "success": ok,
                "failed": fail,
                "provider_label": provider_label,
            }
        )

    chart_performance = _chart_performance(db, user.id, chart_days)
    chart_performance, chart_line_path, chart_area_path, chart_max_val = attach_chart_paths(
        chart_performance
    )
    chart_weekly = _chart_weekly_bars(db, user.id, 7)
    official = user_official_insights_summary(
        db,
        user.id,
        reel_views_days=chart_days,
        account_id=account_id,
    )

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
            "official": official,
            "selected_meta_account_id": official.get("selected_account_id"),
        },
    )
