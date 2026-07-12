"""Rotas Web Push + página de aquecimento de contas."""
from __future__ import annotations

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

# Evita spam de push quando vários abas fazem poll do mesmo log
_recent_push_log_ids: set[int] = set()
_RECENT_PUSH_MAX = 200


class SubscriptionIn(BaseModel):
    endpoint: str
    keys: dict


def _maybe_push_for_new_logs(user_id: int, rows: list[PublishLog]) -> None:
    """Envia push pelo processo web (tem VAPID) quando o poll vê sucesso novo.

    O worker Celery às vezes sobe sem as envs VAPID — o teste no perfil funciona,
    mas o push pós-publicação falha. O poll do painel corrige isso.
    """
    for r in rows:
        if r.status != "success" or r.id in _recent_push_log_ids:
            continue
        _recent_push_log_ids.add(r.id)
        if len(_recent_push_log_ids) > _RECENT_PUSH_MAX:
            _recent_push_log_ids.clear()
        try:
            uname = r.account.username if r.account else "conta"
            ctype = r.content_type or (
                r.automation.content_type if r.automation else None
            ) or "reel"
            from core.webpush import notify_user_publish_success

            notify_user_publish_success(user_id, uname, content_type=ctype)
        except Exception:
            log.exception("Push via poll falhou user=%s log=%s", user_id, r.id)


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
    """Cria notificações in-app a partir de PublishLog de sucesso faltantes.

    Cobre worker antigo, falha silenciosa no Celery, ou deploy parcial.
    Retorna os logs novos (mais recente primeiro) para eventual push.
    """
    from core.notifications import content_label

    newest_notif_at = db.scalar(
        select(func.max(AppNotification.created_at)).where(AppNotification.user_id == user.id)
    )
    q = (
        select(PublishLog)
        .join(PublishLog.account)
        .where(
            InstagramAccount.user_id == user.id,
            PublishLog.status == "success",
        )
        .options(selectinload(PublishLog.account), selectinload(PublishLog.automation))
        .order_by(PublishLog.created_at.desc())
        .limit(15)
    )
    if newest_notif_at is not None:
        q = q.where(PublishLog.created_at > newest_notif_at)
    recent_ok = list(db.scalars(q).all())
    if not recent_ok:
        # Sino vazio + logs antigos: backfill dos 10 mais recentes
        existing = db.scalar(
            select(func.count(AppNotification.id)).where(AppNotification.user_id == user.id)
        ) or 0
        if existing > 0:
            return []
        recent_ok = list(
            db.scalars(
                select(PublishLog)
                .join(PublishLog.account)
                .where(
                    InstagramAccount.user_id == user.id,
                    PublishLog.status == "success",
                )
                .options(selectinload(PublishLog.account), selectinload(PublishLog.automation))
                .order_by(PublishLog.created_at.desc())
                .limit(10)
            ).all()
        )
    if not recent_ok:
        return []
    for plog in reversed(recent_ok):
        uname = plog.account.username if plog.account else "?"
        label = content_label(_resolve_log_content_type(plog))
        db.add(
            AppNotification(
                user_id=user.id,
                title=f"{label} publicado",
                body=f"@{uname}",
                kind="publish",
                link="/logs",
                is_read=False,
                created_at=plog.created_at,
            )
        )
    db.commit()
    return recent_ok


@router.get("/api/notifications")
def api_list_notifications(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    synced = _sync_notifications_from_logs(db, user)
    if synced:
        try:
            latest = synced[0]
            uname = latest.account.username if latest.account else "conta"
            from core.webpush import notify_user_publish_success

            notify_user_publish_success(
                user.id,
                uname,
                content_type=_resolve_log_content_type(latest),
            )
        except Exception:
            log.exception("Sync push falhou user=%s", user.id)

    rows = db.scalars(
        select(AppNotification)
        .where(AppNotification.user_id == user.id)
        .order_by(AppNotification.created_at.desc())
        .limit(40)
    ).all()
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
    for n in rows:
        db.delete(n)
    db.commit()
    return {"ok": True, "cleared": len(rows)}


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
        .where(InstagramAccount.user_id == user.id)
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
    if acc is None or acc.user_id != user.id:
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
