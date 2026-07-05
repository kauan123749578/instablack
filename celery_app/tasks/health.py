"""Verificação periódica de saúde das contas Instagram."""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select

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
from models.models import InstagramAccount

log = logging.getLogger(__name__)

OFFLINE_STATUSES = frozenset({"needs_login", "proxy_down", "banned"})


@celery_app.task(name="celery_app.tasks.health.check_all_accounts")
def check_all_accounts() -> dict:
    """Enfileira verificação de todas as contas (exceto pausadas manualmente)."""
    with session_scope() as db:
        account_ids = list(
            db.scalars(
                select(InstagramAccount.id).where(InstagramAccount.status != "paused")
            ).all()
        )
    for idx, account_id in enumerate(account_ids):
        check_account_health.apply_async(args=[account_id], countdown=idx * 4)
    return {"queued": len(account_ids)}


@celery_app.task(name="celery_app.tasks.health.check_account_health", max_retries=0)
def check_account_health(account_id: int) -> dict:
    with session_scope() as db:
        account = db.get(InstagramAccount, account_id)
        if account is None or account.status == "paused":
            return {"skipped": True}

        proxy = account.proxy
        settings_dict = deserialize_settings(account.session_json)
        password = decrypt_secret(account.encrypted_password)
        username = account.username

    now = dt.datetime.utcnow()

    if not proxy or not check_proxy(proxy):
        with session_scope() as db:
            acc = db.get(InstagramAccount, account_id)
            if acc and acc.status != "paused":
                acc.status = "proxy_down"
                acc.last_error = "Proxy inválido ou offline (verificação automática)"
                acc.last_health_check_at = now
        return {"account_id": account_id, "status": "proxy_down"}

    if not settings_dict:
        with session_scope() as db:
            acc = db.get(InstagramAccount, account_id)
            if acc and acc.status != "paused":
                acc.status = "needs_login"
                acc.last_error = "Sessão expirada — reconecte a conta"
                acc.last_health_check_at = now
        return {"account_id": account_id, "status": "needs_login"}

    try:
        cl = get_ready_client(
            settings_dict=settings_dict,
            proxy=proxy,
            username=username,
            password=password,
        )
        cl.account_info()
        with session_scope() as db:
            acc = db.get(InstagramAccount, account_id)
            if acc and acc.status != "paused":
                acc.session_json = serialize_settings(cl.get_settings())
                if acc.status in OFFLINE_STATUSES:
                    acc.status = "active"
                acc.last_error = None
                acc.last_health_check_at = now
        return {"account_id": account_id, "status": "active"}
    except InstagramAuthError as exc:
        with session_scope() as db:
            acc = db.get(InstagramAccount, account_id)
            if acc and acc.status != "paused":
                acc.status = "needs_login"
                acc.last_error = str(exc)[:1000]
                acc.last_health_check_at = now
        return {"account_id": account_id, "status": "needs_login", "error": str(exc)}
    except Exception as exc:
        log.warning("health check account %s: %s", account_id, exc)
        with session_scope() as db:
            acc = db.get(InstagramAccount, account_id)
            if acc and acc.status != "paused":
                acc.last_health_check_at = now
                acc.last_error = str(exc)[:1000]
        return {"account_id": account_id, "status": "error", "error": str(exc)}
