"""Sincroniza visualizações dos Reels publicados."""
from __future__ import annotations

import datetime as dt
import logging
import time

from sqlalchemy import or_, select

from app.security import decrypt_secret
from celery_app.config import celery_app
from core.database import session_scope
from core.instagram import (
    InstagramAuthError,
    check_proxy,
    deserialize_settings,
    fetch_media_stats,
    get_ready_client,
    serialize_settings,
)
from models.models import InstagramAccount, PublishLog

log = logging.getLogger(__name__)

STALE_HOURS = 1
MAX_LOGS_PER_RUN = 80


@celery_app.task(name="celery_app.tasks.insights.sync_all_views")
def sync_all_views() -> dict:
    """Atualiza play_count dos reels publicados recentemente."""
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=30)
    stale_before = dt.datetime.utcnow() - dt.timedelta(hours=STALE_HOURS)
    updated = 0
    errors = 0

    with session_scope() as db:
        logs = db.scalars(
            select(PublishLog)
            .where(
                PublishLog.status == "success",
                PublishLog.media_id.is_not(None),
                PublishLog.created_at >= cutoff,
                or_(
                    PublishLog.insights_fetched_at.is_(None),
                    PublishLog.insights_fetched_at < stale_before,
                ),
            )
            .order_by(PublishLog.created_at.desc())
            .limit(MAX_LOGS_PER_RUN)
        ).all()
        log_ids = [(log.id, log.account_id, log.media_id) for log in logs]

    for log_id, account_id, media_id in log_ids:
        try:
            ok = _sync_one_log(log_id, account_id, media_id)
            if ok:
                updated += 1
            else:
                errors += 1
        except Exception as exc:
            log.warning("insights log %s: %s", log_id, exc)
            errors += 1
        time.sleep(2)

    log.info("insights: %d atualizados, %d falhas", updated, errors)
    return {"updated": updated, "errors": errors}


def _sync_one_log(log_id: int, account_id: int, media_id: str) -> bool:
    with session_scope() as db:
        account = db.get(InstagramAccount, account_id)
        log_row = db.get(PublishLog, log_id)
        if not account or not log_row:
            return False
        if account.status in ("banned", "paused", "proxy_down", "needs_login"):
            return False
        if not account.proxy or not check_proxy(account.proxy):
            return False
        settings_dict = deserialize_settings(account.session_json)
        if not settings_dict:
            return False
        proxy = account.proxy
        username = account.username
        password = decrypt_secret(account.encrypted_password)

    try:
        cl = get_ready_client(
            settings_dict=settings_dict,
            proxy=proxy,
            username=username,
            password=password,
        )
        stats = fetch_media_stats(cl, media_id)
    except InstagramAuthError:
        return False
    except Exception as exc:
        log.warning("fetch stats %s: %s", media_id, exc)
        return False

    with session_scope() as db:
        log_row = db.get(PublishLog, log_id)
        acc = db.get(InstagramAccount, account_id)
        if not log_row:
            return False
        if stats.get("play_count") is not None:
            log_row.play_count = stats["play_count"]
        if stats.get("like_count") is not None:
            log_row.like_count = stats["like_count"]
        log_row.insights_fetched_at = dt.datetime.utcnow()
        if acc:
            acc.session_json = serialize_settings(cl.get_settings())
    return True
