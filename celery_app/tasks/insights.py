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
from core.meta_instagram import (
    MetaInstagramError,
    fetch_ig_user_metrics,
    fetch_media_insights,
)
from models.models import InstagramAccount, PublishLog

log = logging.getLogger(__name__)

STALE_HOURS = 1
MAX_LOGS_PER_RUN = 80
MAX_META_ACCOUNTS_PER_RUN = 40


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

    followers_updated = _sync_meta_followers()

    log.info(
        "insights: %d logs atualizados, %d falhas, %d contas followers",
        updated,
        errors,
        followers_updated,
    )
    return {"updated": updated, "errors": errors, "followers_updated": followers_updated}


def _sync_meta_followers() -> int:
    stale_before = dt.datetime.utcnow() - dt.timedelta(hours=6)
    updated = 0
    with session_scope() as db:
        account_ids = list(
            db.scalars(
                select(InstagramAccount.id)
                .where(
                    InstagramAccount.provider == "meta",
                    InstagramAccount.status.notin_(("paused", "deleted", "banned")),
                    or_(
                        InstagramAccount.followers_updated_at.is_(None),
                        InstagramAccount.followers_updated_at < stale_before,
                    ),
                )
                .limit(MAX_META_ACCOUNTS_PER_RUN)
            ).all()
        )

    for account_id in account_ids:
        try:
            if _sync_one_meta_followers(account_id):
                updated += 1
        except Exception as exc:
            log.warning("followers account %s: %s", account_id, exc)
        time.sleep(1)
    return updated


def _sync_one_meta_followers(account_id: int) -> bool:
    with session_scope() as db:
        account = db.get(InstagramAccount, account_id)
        if not account or account.provider != "meta":
            return False
        token = decrypt_secret(account.encrypted_meta_access_token)
        ig_user_id = account.meta_ig_user_id
        if not token or not ig_user_id:
            return False

    try:
        metrics = fetch_ig_user_metrics(token, ig_user_id)
    except MetaInstagramError as exc:
        log.warning("meta followers %s: %s", account_id, exc)
        return False

    with session_scope() as db:
        acc = db.get(InstagramAccount, account_id)
        if not acc:
            return False
        if metrics.get("followers_count") is not None:
            acc.followers_count = metrics["followers_count"]
        acc.followers_updated_at = dt.datetime.utcnow()
    return True


def _sync_one_log(log_id: int, account_id: int, media_id: str) -> bool:
    with session_scope() as db:
        account = db.get(InstagramAccount, account_id)
        log_row = db.get(PublishLog, log_id)
        if not account or not log_row:
            return False
        if account.status in ("banned", "paused", "proxy_down", "needs_login"):
            return False
        provider = account.provider or "instagrapi"

    if provider == "meta":
        return _sync_one_log_meta(log_id, account_id, media_id)

    with session_scope() as db:
        account = db.get(InstagramAccount, account_id)
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
        elif log_row.play_count is None:
            log_row.play_count = 0
        if stats.get("like_count") is not None:
            log_row.like_count = stats["like_count"]
        log_row.insights_fetched_at = dt.datetime.utcnow()
        if acc:
            acc.session_json = serialize_settings(cl.get_settings())
    return True


def _sync_one_log_meta(log_id: int, account_id: int, media_id: str) -> bool:
    with session_scope() as db:
        account = db.get(InstagramAccount, account_id)
        if not account:
            return False
        token = decrypt_secret(account.encrypted_meta_access_token)
        if not token:
            return False

    try:
        stats = fetch_media_insights(token, media_id)
    except MetaInstagramError as exc:
        log.warning("meta insights %s: %s", media_id, exc)
        return False

    with session_scope() as db:
        log_row = db.get(PublishLog, log_id)
        if not log_row:
            return False
        play = stats.get("play_count")
        if play is not None:
            log_row.play_count = play
        elif log_row.play_count is None:
            log_row.play_count = 0
        likes = stats.get("like_count")
        if likes is not None:
            log_row.like_count = likes
        log_row.insights_fetched_at = dt.datetime.utcnow()
    return True
