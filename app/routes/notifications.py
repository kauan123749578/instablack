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
from core.webpush import vapid_configured
from models.models import (
    AppNotification,
    InstagramAccount,
    PushSubscription,
    User,
    WarmupJob,
    WarmupLog,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["notifications-warmup"])


class SubscriptionIn(BaseModel):
    endpoint: str
    keys: dict


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


@router.get("/api/notifications")
def api_list_notifications(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
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

    allowed_durations = {30, 60, 120, 240, 480}
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
