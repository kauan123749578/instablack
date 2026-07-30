"""Sincroniza visualizações dos Reels publicados."""
from __future__ import annotations

import datetime as dt
import logging
import time
from io import BytesIO

import requests
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
    fetch_media_permalink,
)
from core.storage import get_storage
from models.models import InstagramAccount, PublishLog

log = logging.getLogger(__name__)

STALE_HOURS = 1
MAX_LOGS_PER_RUN = 80
MAX_META_ACCOUNTS_PER_RUN = 40
MAX_PROFILE_PIC_PER_RUN = 60


def _cache_profile_picture(account_id: int, remote_url: str) -> str | None:
    """Baixa a foto da Meta e salva no R2/local → URL /media/... (estável no painel)."""
    url = (remote_url or "").strip()
    if not url.startswith("http"):
        return None
    try:
        resp = requests.get(
            url,
            timeout=25,
            headers={"User-Agent": "instablack-profile-sync/1.0"},
        )
        if resp.status_code != 200 or len(resp.content) < 80:
            log.warning(
                "profile pic download falhou account=%s http=%s bytes=%s",
                account_id,
                resp.status_code,
                len(resp.content or b""),
            )
            return url[:1024]
        ctype = (resp.headers.get("Content-Type") or "").lower()
        ext = ".jpg"
        if "png" in ctype:
            ext = ".png"
        elif "webp" in ctype:
            ext = ".webp"
        key = f"avatars/ig/{int(account_id)}{ext}"
        storage = get_storage()
        storage.save_at_key(
            key,
            BytesIO(resp.content),
            content_type=ctype.split(";")[0].strip() or None,
        )
        return f"/media/{key}"
    except Exception as exc:
        log.warning("cache profile pic account=%s: %s", account_id, exc)
        return url[:1024]


@celery_app.task(name="celery_app.tasks.insights.sync_all_views")
def sync_all_views() -> dict:
    """Atualiza play_count dos reels publicados recentemente."""
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=30)
    stale_before = dt.datetime.utcnow() - dt.timedelta(hours=STALE_HOURS)
    updated = 0
    errors = 0

    with session_scope() as db:
        # Prioriza Meta sem views (métrica `plays` antiga falhava; precisa re-sync com `views`)
        logs = db.scalars(
            select(PublishLog)
            .join(InstagramAccount, PublishLog.account_id == InstagramAccount.id)
            .where(
                PublishLog.status == "success",
                PublishLog.media_id.is_not(None),
                PublishLog.created_at >= cutoff,
                or_(
                    PublishLog.insights_fetched_at.is_(None),
                    PublishLog.insights_fetched_at < stale_before,
                    # Meta sem views ou sem link (sync antigo quebrava com métrica `plays`)
                    (
                        (InstagramAccount.provider == "meta")
                        & (
                            PublishLog.play_count.is_(None)
                            | PublishLog.media_url.is_(None)
                        )
                    ),
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
                        InstagramAccount.profile_pic_url.is_(None),
                        InstagramAccount.profile_pic_url == "",
                        ~InstagramAccount.profile_pic_url.startswith("/media/"),
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
        proxy = (account.proxy or "").strip() or None
        if not token or not ig_user_id:
            return False

    # check_proxy FORA do session — não segura conexão no PgBouncer.
    if proxy and not check_proxy(proxy):
        proxy = None

    try:
        metrics = fetch_ig_user_metrics(token, ig_user_id, proxy=proxy)
    except MetaInstagramError as exc:
        log.warning("meta followers %s: %s", account_id, exc)
        return False

    # Download da foto FORA do session — não segura conexão no PgBouncer.
    cached_pic: str | None = None
    pic = metrics.get("profile_picture_url")
    if pic:
        cached_pic = _cache_profile_picture(account_id, str(pic))
    else:
        log.info(
            "meta followers account=%s sem profile_picture_url na Graph",
            account_id,
        )

    with session_scope() as db:
        acc = db.get(InstagramAccount, account_id)
        if not acc:
            return False
        if metrics.get("followers_count") is not None:
            acc.followers_count = metrics["followers_count"]
        if cached_pic:
            acc.profile_pic_url = cached_pic[:1024]
        acc.followers_updated_at = dt.datetime.utcnow()
    return True


@celery_app.task(name="celery_app.tasks.insights.refresh_missing_profile_pics")
def refresh_missing_profile_pics(account_ids: list[int] | None = None) -> dict:
    """Backfill rápido de fotos de perfil Meta (dashboard dispara quando faltam)."""
    ids: list[int]
    if account_ids:
        ids = [int(x) for x in account_ids][:MAX_PROFILE_PIC_PER_RUN]
    else:
        with session_scope() as db:
            ids = list(
                db.scalars(
                    select(InstagramAccount.id)
                    .where(
                        InstagramAccount.provider == "meta",
                        InstagramAccount.status.notin_(("paused", "deleted", "banned")),
                        or_(
                            InstagramAccount.profile_pic_url.is_(None),
                            InstagramAccount.profile_pic_url == "",
                            ~InstagramAccount.profile_pic_url.startswith("/media/"),
                        ),
                    )
                    .order_by(InstagramAccount.id.asc())
                    .limit(MAX_PROFILE_PIC_PER_RUN)
                ).all()
            )
    updated = 0
    for aid in ids:
        try:
            if _sync_one_meta_followers(aid):
                updated += 1
        except Exception as exc:
            log.warning("refresh profile pic %s: %s", aid, exc)
        time.sleep(0.4)
    return {"requested": len(ids), "updated": updated}


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
        if not account:
            return False
        if account.status in ("banned", "paused", "proxy_down", "needs_login"):
            return False
        if not account.proxy:
            return False
        settings_dict = deserialize_settings(account.session_json)
        if not settings_dict:
            return False
        proxy = account.proxy
        username = account.username
        password = decrypt_secret(account.encrypted_password)

    if not check_proxy(proxy):
        return False

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
        proxy = (account.proxy or "").strip() or None
        if not token:
            return False

    # check_proxy FORA do session — evita idle in transaction.
    if proxy and not check_proxy(proxy):
        proxy = None

    try:
        stats = fetch_media_insights(token, media_id, proxy=proxy)
    except MetaInstagramError as exc:
        log.warning("meta insights %s: %s", media_id, exc)
        return False

    permalink: str | None = None
    try:
        permalink = fetch_media_permalink(token, media_id, proxy=proxy)
    except MetaInstagramError:
        permalink = None

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
        if permalink and not log_row.media_url:
            log_row.media_url = permalink[:512]
        log_row.insights_fetched_at = dt.datetime.utcnow()
    return True
