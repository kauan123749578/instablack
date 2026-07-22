"""Rotas Web Push + página de aquecimento de contas."""
from __future__ import annotations

import datetime as dt
import json
import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.deps import get_current_user, maybe_current_user
from app.templating import templates
from core.database import get_db
from core.webpush import send_test_push, vapid_configured
from models.models import (
    AppNotification,
    InstagramAccount,
    PublishLog,
    PushSubscription,
    User,
    WarmupJob,
    WarmupLog,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["notifications-warmup"])
VISIBLE_ACCOUNT_STATUSES = ("active", "paused", "needs_login", "proxy_down", "banned")

# Evita spam de push quando vários abas fazem poll do mesmo log
_recent_push_log_ids: set[int] = set()
_recent_push_notif_ids: set[int] = set()
_RECENT_PUSH_MAX = 200
_FALLBACK_PUSH_KINDS = frozenset({
    "offline",
    "account",
    "warning",
    "error",
    "fail",
    "warmup",
    "publish",
})


class SubscriptionIn(BaseModel):
    endpoint: str
    keys: dict


def _maybe_push_for_new_logs(user_id: int, rows: list[PublishLog]) -> None:
    """Fallback de push quando o worker não enviou (ex.: VAPID só no web)."""
    global _recent_push_log_ids
    if not rows:
        return
    from core.webpush import notify_user_publish_success

    for plog in rows:
        if plog.status != "success":
            continue
        if plog.id in _recent_push_log_ids:
            continue
        _recent_push_log_ids.add(plog.id)
        if len(_recent_push_log_ids) > _RECENT_PUSH_MAX:
            _recent_push_log_ids = set(list(_recent_push_log_ids)[-_RECENT_PUSH_MAX:])
        uname = plog.account.username if plog.account else "?"
        ct = _resolve_log_content_type(plog)
        try:
            notify_user_publish_success(user_id, uname, content_type=ct)
        except Exception:
            log.exception("Fallback push falhou log=%s user=%s", plog.id, user_id)


def _maybe_push_for_inapp_notifications(user_id: int, rows: list[AppNotification]) -> None:
    """Fallback de push para offline/erros/warmup se o worker não tinha VAPID."""
    global _recent_push_notif_ids
    if not rows:
        return
    from core.webpush import notify_user_push, vapid_configured

    if not vapid_configured():
        return

    cutoff = dt.datetime.utcnow() - dt.timedelta(minutes=30)
    for n in rows:
        if n.id in _recent_push_notif_ids or n.is_read:
            continue
        kind = (n.kind or "").lower()
        if kind not in _FALLBACK_PUSH_KINDS:
            continue
        created = n.created_at
        if created is not None:
            created_naive = created.replace(tzinfo=None) if created.tzinfo else created
            if created_naive < cutoff:
                continue

        _recent_push_notif_ids.add(n.id)
        if len(_recent_push_notif_ids) > _RECENT_PUSH_MAX:
            _recent_push_notif_ids = set(list(_recent_push_notif_ids)[-_RECENT_PUSH_MAX:])
        try:
            notify_user_push(
                user_id,
                {
                    "title": (n.title or "instablack")[:120],
                    "body": (n.body or "")[:200],
                    "url": n.link or "/",
                    "tag": f"fallback-{kind}-{n.id}",
                },
                kind=kind,
                force=False,
            )
        except Exception:
            log.exception("Fallback push notif falhou id=%s user=%s", n.id, user_id)



@router.get("/api/vapid-public-key")
def api_vapid_public_key(user: User = Depends(get_current_user)):
    if not vapid_configured():
        return JSONResponse({"configured": False, "publicKey": ""})
    return {"configured": True, "publicKey": settings.vapid_public_key}


@router.post("/api/push/subscribe")
async def api_push_subscribe(
    body: SubscriptionIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not vapid_configured():
        return JSONResponse({"ok": False, "error": "vapid_not_configured"}, status_code=400)
    p256dh = (body.keys or {}).get("p256dh") or ""
    auth = (body.keys or {}).get("auth") or ""
    if not body.endpoint or not p256dh or not auth:
        return JSONResponse({"ok": False, "error": "invalid"}, status_code=400)

    existing = db.scalar(
        select(PushSubscription).where(PushSubscription.endpoint == body.endpoint)
    )
    ua = (request.headers.get("user-agent") or "")[:500]
    if existing:
        existing.user_id = user.id
        existing.p256dh = p256dh
        existing.auth = auth
        existing.user_agent = ua
    else:
        db.add(
            PushSubscription(
                user_id=user.id,
                endpoint=body.endpoint,
                p256dh=p256dh,
                auth=auth,
                user_agent=ua,
            )
        )
    db.commit()
    return {"ok": True}


@router.post("/api/push/unsubscribe")
async def api_push_unsubscribe(
    body: SubscriptionIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sub = db.scalar(
        select(PushSubscription).where(
            PushSubscription.endpoint == body.endpoint,
            PushSubscription.user_id == user.id,
        )
    )
    if sub:
        db.delete(sub)
        db.commit()
    return {"ok": True}


@router.post("/api/push/test")
def api_push_test(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not vapid_configured():
        return JSONResponse({"ok": False, "error": "vapid_not_configured"}, status_code=400)
    sub_count = db.scalar(
        select(func.count())
        .select_from(PushSubscription)
        .where(PushSubscription.user_id == user.id)
    ) or 0
    if sub_count < 1:
        return JSONResponse(
            {"ok": False, "error": "no_subscription", "message": "Ative as notificações neste dispositivo primeiro."},
            status_code=400,
        )
    sent, failed = send_test_push(user.id)
    return {"ok": True, "sent": sent, "failed": failed}


@router.get("/api/logs/latest")
def api_logs_latest(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    since_id: int = 0,
):
    """Polling leve para atualizar logs após post imediato."""
    q = (
        select(PublishLog)
        .join(PublishLog.account)
        .where(InstagramAccount.user_id == user.id)
        .options(selectinload(PublishLog.account), selectinload(PublishLog.automation))
        .order_by(PublishLog.created_at.desc())
        .limit(20)
    )
    if since_id > 0:
        q = q.where(PublishLog.id > since_id)
    rows = list(db.scalars(q).all())
    # Só dispara push em deltas (since_id>0), não no carregamento inicial
    if since_id > 0 and rows:
        _maybe_push_for_new_logs(user.id, rows)
    return {
        "items": [
            {
                "id": r.id,
                "status": r.status,
                "username": r.account.username if r.account else "",
                "automation": r.automation.name if r.automation else "Post imediato",
                "content_type": r.content_type
                or (r.automation.content_type if r.automation else None)
                or "reel",
                "error": (r.error or "")[:200],
                "media_url": r.media_url,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "latest_id": rows[0].id if rows else since_id,
    }


def _resolve_log_content_type(plog: PublishLog) -> str:
    if plog.content_type:
        return plog.content_type
    if plog.automation and plog.automation.content_type:
        return plog.automation.content_type
    return "reel"


def _sync_notifications_from_logs(db: Session, user: User) -> list[PublishLog]:
    """Cria notificações in-app para logs de sucesso recentes sem notificação vinculada."""
    from core.notification_prefs import format_publish_copy, get_notification_prefs

    since = dt.datetime.utcnow() - dt.timedelta(hours=6)
    recent_ok = list(
        db.scalars(
            select(PublishLog)
            .join(PublishLog.account)
            .outerjoin(
                AppNotification,
                (AppNotification.publish_log_id == PublishLog.id)
                & (AppNotification.user_id == user.id),
            )
            .where(
                InstagramAccount.user_id == user.id,
                PublishLog.status == "success",
                PublishLog.created_at >= since,
                AppNotification.id.is_(None),
            )
            .options(selectinload(PublishLog.account), selectinload(PublishLog.automation))
            .order_by(PublishLog.created_at.asc())
            .limit(20)
        ).all()
    )
    if not recent_ok:
        return []

    for plog in recent_ok:
        uname = plog.account.username if plog.account else "?"
        title, body = format_publish_copy(
            get_notification_prefs(user),
            uname,
            _resolve_log_content_type(plog),
        )
        db.add(
            AppNotification(
                user_id=user.id,
                title=title,
                body=body,
                kind="publish",
                link="/logs",
                publish_log_id=plog.id,
                is_read=False,
                created_at=plog.created_at,
            )
        )
    db.commit()
    return recent_ok


@router.get("/api/notifications")
def api_list_notifications(
    push_fallback: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    synced = _sync_notifications_from_logs(db, user)
    if synced:
        _maybe_push_for_new_logs(user.id, synced)

    rows = list(
        db.scalars(
            select(AppNotification)
            .where(AppNotification.user_id == user.id)
            .order_by(AppNotification.created_at.desc())
            .limit(40)
        ).all()
    )
    # Fallback opcional; o poll normal do sino deve continuar leve.
    if push_fallback:
        _maybe_push_for_inapp_notifications(user.id, rows)
    unread = db.scalar(
        select(func.count(AppNotification.id)).where(
            AppNotification.user_id == user.id,
            AppNotification.is_read.is_(False),
        )
    ) or 0
    return {
        "unread": int(unread),
        "items": [
            {
                "id": n.id,
                "title": n.title,
                "body": n.body or "",
                "kind": n.kind,
                "link": n.link,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in rows
        ],
    }


@router.post("/api/notifications/read")
def api_mark_notifications_read(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = db.scalars(
        select(AppNotification).where(
            AppNotification.user_id == user.id,
            AppNotification.is_read.is_(False),
        )
    ).all()
    for n in rows:
        n.is_read = True
    db.commit()
    return {"ok": True, "marked": len(rows)}


@router.post("/api/notifications/clear")
def api_clear_notifications(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = db.scalars(
        select(AppNotification).where(AppNotification.user_id == user.id)
    ).all()
    cleared = len(rows)
    for n in rows:
        db.delete(n)
    db.commit()
    # Resposta imediata sem sync — o GET seguinte também não recria histórico
    return {"ok": True, "cleared": cleared, "items": [], "unread": 0}


@router.get("/warmup")
def warmup_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(maybe_current_user),
    ok: str | None = None,
    error: str | None = None,
):
    if user is None:
        return RedirectResponse("/login", status_code=303)
    accounts = db.scalars(
        select(InstagramAccount)
        .where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
            InstagramAccount.provider == "instagrapi",
        )
        .order_by(InstagramAccount.username.asc())
    ).all()
    jobs = db.scalars(
        select(WarmupJob)
        .where(WarmupJob.user_id == user.id)
        .options(selectinload(WarmupJob.account))
        .order_by(WarmupJob.created_at.desc())
        .limit(20)
    ).all()
    return templates.TemplateResponse(
        "warmup.html",
        {
            "request": request,
            "user": user,
            "accounts": accounts,
            "jobs": jobs,
            "ok": ok,
            "error": error,
            "vapid_ready": vapid_configured(),
        },
    )


@router.post("/warmup/start")
def warmup_start(
    request: Request,
    account_id: int = Form(...),
    influencers: str = Form(""),
    actions_target: int = Form(80),
    duration_minutes: int = Form(60),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    import datetime as dt

    acc = db.get(InstagramAccount, account_id)
    if (
        acc is None
        or acc.user_id != user.id
        or acc.status == "deleted"
        or (acc.provider or "instagrapi") != "instagrapi"
    ):
        return RedirectResponse("/warmup?error=conta", status_code=303)

    names = []
    for line in influencers.replace(",", "\n").splitlines():
        u = line.strip().lstrip("@")
        if u and u not in names:
            names.append(u)
    if len(names) < 1:
        return RedirectResponse("/warmup?error=lista", status_code=303)

    allowed_durations = {5, 10, 15, 20, 30, 60, 120, 240, 480}
    duration = int(duration_minutes or 60)
    if duration not in allowed_durations:
        duration = 60
    # teto alto — o tempo é o limite principal
    target = max(10, min(int(actions_target or 80), 500))
    ends = dt.datetime.utcnow() + dt.timedelta(minutes=duration)
    job = WarmupJob(
        user_id=user.id,
        account_id=acc.id,
        influencers_json=json.dumps(names, ensure_ascii=False),
        status="pending",
        actions_target=target,
        actions_done=0,
        duration_minutes=duration,
        ends_at=ends,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    from celery_app.tasks.warmup import run_warmup_job

    run_warmup_job.delay(job.id)
    return RedirectResponse(f"/warmup?ok=started&job={job.id}", status_code=303)


@router.post("/warmup/{job_id}/pause")
def warmup_pause(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    job = db.get(WarmupJob, job_id)
    if job and job.user_id == user.id and job.status == "running":
        job.status = "paused"
        db.commit()
    return RedirectResponse("/warmup", status_code=303)


@router.post("/warmup/{job_id}/resume")
def warmup_resume(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    job = db.get(WarmupJob, job_id)
    if job and job.user_id == user.id and job.status == "paused":
        job.status = "pending"
        db.commit()
        from celery_app.tasks.warmup import run_warmup_job

        run_warmup_job.delay(job.id)
    return RedirectResponse("/warmup", status_code=303)


@router.get("/warmup/{job_id}")
def warmup_detail(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(maybe_current_user),
):
    if user is None:
        return RedirectResponse("/login", status_code=303)
    job = db.scalar(
        select(WarmupJob)
        .where(WarmupJob.id == job_id, WarmupJob.user_id == user.id)
        .options(selectinload(WarmupJob.account))
    )
    if job is None:
        return RedirectResponse("/warmup", status_code=303)
    logs = db.scalars(
        select(WarmupLog)
        .where(WarmupLog.job_id == job.id)
        .order_by(WarmupLog.created_at.desc())
        .limit(100)
    ).all()
    try:
        influencers = json.loads(job.influencers_json or "[]")
    except json.JSONDecodeError:
        influencers = []
    return templates.TemplateResponse(
        "warmup_detail.html",
        {
            "request": request,
            "user": user,
            "job": job,
            "logs": logs,
            "influencers": influencers,
        },
    )
