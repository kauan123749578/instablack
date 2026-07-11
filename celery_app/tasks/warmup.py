"""Task Celery: aquecimento por duração — interage com seguidores dos influenciadores."""
from __future__ import annotations

import datetime as dt
import json
import logging

from app.security import decrypt_secret
from celery_app.config import celery_app
from core.database import session_scope
from core.instagram import (
    InstagramAuthError,
    check_proxy,
    deserialize_settings,
    get_ready_client,
    serialize_settings,
)
from core.notifications import create_notification
from core.warmup import extract_follower_pool, run_random_action
from models.models import InstagramAccount, PublishLog, WarmupJob, WarmupLog

log = logging.getLogger(__name__)


def _log_publish_style(
    account_id: int,
    automation_id: int | None,
    *,
    status: str,
    detail: str,
) -> None:
    """Espelha ações de aquecimento também em /logs (PublishLog)."""
    with session_scope() as db:
        db.add(
            PublishLog(
                automation_id=automation_id,
                account_id=account_id,
                status=status,
                error=f"[warmup] {detail}"[:2000],
            )
        )


@celery_app.task(name="celery_app.tasks.warmup.run_warmup_job", bind=True, max_retries=0)
def run_warmup_job(self, job_id: int) -> dict:
    with session_scope() as db:
        job = db.get(WarmupJob, job_id)
        if job is None:
            return {"error": "job_not_found"}
        if job.status in ("paused", "done", "failed"):
            return {"skipped": True, "status": job.status}
        job.status = "running"
        duration = max(15, int(job.duration_minutes or 60))
        if not job.ends_at:
            job.ends_at = dt.datetime.utcnow() + dt.timedelta(minutes=duration)
        ends_at = job.ends_at
        if ends_at.tzinfo is not None:
            ends_at = ends_at.astimezone(dt.timezone.utc).replace(tzinfo=None)

        account = db.get(InstagramAccount, job.account_id)
        if account is None:
            job.status = "failed"
            job.last_error = "conta removida"
            return {"error": "account_missing"}
        settings_dict = deserialize_settings(account.session_json)
        proxy = account.proxy
        password = decrypt_secret(account.encrypted_password)
        username = account.username
        owner_id = job.user_id
        account_id = job.account_id
        try:
            influencers = json.loads(job.influencers_json or "[]")
        except json.JSONDecodeError:
            influencers = []
        influencers = [str(u).lstrip("@").strip() for u in influencers if str(u).strip()]
        target_cap = int(job.actions_target or 9999)
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

    create_notification(
        owner_id,
        "Aquecimento iniciado",
        f"@{username}: extraindo seguidores de {len(influencers)} influenciador(es)…",
        kind="warmup",
        link=f"/warmup/{job_id}",
    )
    _log_publish_style(
        account_id,
        None,
        status="skipped",
        detail=f"iniciado — extraindo seguidores de {', '.join('@'+i for i in influencers[:5])}",
    )

    # Extrai pool de seguidores (com pausas internas)
    try:
        targets = extract_follower_pool(cl, influencers)
    except Exception as exc:
        with session_scope() as db:
            job = db.get(WarmupJob, job_id)
            if job:
                job.status = "failed"
                job.last_error = f"falha ao extrair seguidores: {exc}"[:500]
        return {"error": "followers"}

    if not targets:
        with session_scope() as db:
            job = db.get(WarmupJob, job_id)
            if job:
                job.status = "failed"
                job.last_error = "nenhum seguidor extraído — tente outros @ ou reconecte a sessão"
        create_notification(
            owner_id,
            "Aquecimento falhou",
            f"@{username}: não foi possível extrair seguidores.",
            kind="warning",
            link=f"/warmup/{job_id}",
        )
        return {"error": "empty_pool"}

    create_notification(
        owner_id,
        "Pool de seguidores pronto",
        f"@{username}: {len(targets)} perfis para interagir (aleatório + pausas longas).",
        kind="warmup",
        link=f"/warmup/{job_id}",
        send_push=False,
    )
    _log_publish_style(
        account_id,
        None,
        status="skipped",
        detail=f"pool com {len(targets)} seguidores pronto",
    )

    while done < target_cap:
        now = dt.datetime.utcnow()
        if ends_at and now >= ends_at:
            break

        with session_scope() as db:
            job = db.get(WarmupJob, job_id)
            if job is None or job.status == "paused":
                return {"paused": True, "done": done}
            if job.status == "failed":
                return {"failed": True}

        action, target_user, result = run_random_action(cl, targets)
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

        # Espelha no log global (skipped = não conta como publicação)
        _log_publish_style(
            account_id,
            None,
            status="skipped",
            detail=("ok · " if ok else "erro · ")
            + f"{action}"
            + (f" @{target_user}" if target_user else "")
            + f" — {detail}",
        )

        # Notifica a cada 5 ações (só no sino — evita spam no celular)
        if done % 5 == 0:
            create_notification(
                owner_id,
                f"Aquecimento @{username}",
                f"{done} ações · última: {action}" + (f" @{target_user}" if target_user else ""),
                kind="warmup",
                link=f"/warmup/{job_id}",
                send_push=False,
            )

        log.info("warmup #%s action=%s ok=%s done=%s", job_id, action, ok, done)

    with session_scope() as db:
        job = db.get(WarmupJob, job_id)
        if job and job.status == "running":
            job.status = "done"
            job.last_action = "concluído (tempo ou meta)"

    create_notification(
        owner_id,
        "Aquecimento concluído",
        f"@{username}: {done} ações realizadas.",
        kind="warmup",
        link=f"/warmup/{job_id}",
    )
    return {"ok": True, "done": done}
