"""Task Celery: aquecimento de contas Instagram."""
from __future__ import annotations

import json
import logging

from celery_app.config import celery_app
from core.database import session_scope
from core.instagram import (
    InstagramAuthError,
    check_proxy,
    deserialize_settings,
    get_ready_client,
    serialize_settings,
)
from core.warmup import run_random_action
from models.models import InstagramAccount, WarmupJob, WarmupLog
from app.security import decrypt_secret

log = logging.getLogger(__name__)


@celery_app.task(name="celery_app.tasks.warmup.run_warmup_job", bind=True, max_retries=0)
def run_warmup_job(self, job_id: int) -> dict:
    with session_scope() as db:
        job = db.get(WarmupJob, job_id)
        if job is None:
            return {"error": "job_not_found"}
        if job.status in ("paused", "done", "failed"):
            return {"skipped": True, "status": job.status}
        job.status = "running"
        account = db.get(InstagramAccount, job.account_id)
        if account is None:
            job.status = "failed"
            job.last_error = "conta removida"
            return {"error": "account_missing"}
        settings_dict = deserialize_settings(account.session_json)
        proxy = account.proxy
        password = decrypt_secret(account.encrypted_password)
        username = account.username
        try:
            influencers = json.loads(job.influencers_json or "[]")
        except json.JSONDecodeError:
            influencers = []
        influencers = [str(u).lstrip("@").strip() for u in influencers if str(u).strip()]
        target = int(job.actions_target or 40)
        done = int(job.actions_done or 0)

    if not proxy or not check_proxy(proxy):
        with session_scope() as db:
            job = db.get(WarmupJob, job_id)
            if job:
                job.status = "failed"
                job.last_error = "proxy inválida ou fora"
        return {"error": "proxy"}

    if not settings_dict:
        with session_scope() as db:
            job = db.get(WarmupJob, job_id)
            if job:
                job.status = "failed"
                job.last_error = "sem sessão — reconecte a conta"
        return {"error": "no_session"}

    try:
        cl = get_ready_client(
            settings_dict=settings_dict,
            proxy=proxy,
            username=username,
            password=password,
        )
    except InstagramAuthError as exc:
        with session_scope() as db:
            job = db.get(WarmupJob, job_id)
            if job:
                job.status = "failed"
                job.last_error = str(exc)[:500]
        return {"error": "auth"}

    while done < target:
        with session_scope() as db:
            job = db.get(WarmupJob, job_id)
            if job is None or job.status == "paused":
                return {"paused": True, "done": done}
            if job.status == "failed":
                return {"failed": True}

        action, target_user, result = run_random_action(cl, influencers)
        ok = bool(result.get("ok"))
        detail = str(result.get("detail") or "")[:500]
        done += 1

        with session_scope() as db:
            job = db.get(WarmupJob, job_id)
            if job is None:
                return {"gone": True}
            job.actions_done = done
            job.last_action = f"{action}" + (f" @{target_user}" if target_user else "")
            if not ok:
                job.last_error = detail
            db.add(
                WarmupLog(
                    job_id=job_id,
                    action=action,
                    target=target_user,
                    ok=ok,
                    detail=detail,
                )
            )
            acc = db.get(InstagramAccount, job.account_id)
            if acc:
                try:
                    acc.session_json = serialize_settings(cl.get_settings())
                except Exception:
                    pass

        log.info("warmup #%s action=%s ok=%s done=%s/%s", job_id, action, ok, done, target)

    with session_scope() as db:
        job = db.get(WarmupJob, job_id)
        if job and job.status == "running":
            job.status = "done"
            job.last_action = "concluído"

    return {"ok": True, "done": done, "target": target}
