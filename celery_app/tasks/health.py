"""Verificação periódica de saúde das contas Instagram."""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select

from app.security import decrypt_secret, encrypt_secret
from app.utils.auth_failures import auth_status_reason, latest_auth_failure_reason
from app.utils.proxy import clean_sessionid
from celery_app.config import celery_app
from core.database import session_scope
from core.instagram import (
    InstagramAuthError,
    InstagramTwoFactorRequired,
    check_proxy,
    deserialize_settings,
    extract_sessionid_from_settings,
    get_ready_client,
    login_with_sessionid,
    serialize_settings,
    try_refresh_session,
)
from core.meta_instagram import (
    MetaInstagramError,
    refresh_access_token as refresh_meta_token,
    validate_token as validate_meta_token,
)
from core.notifications import create_notification
from core.web_cookies import decrypt_web_cookies, merge_sessionid_into_web_cookies
from models.models import InstagramAccount

log = logging.getLogger(__name__)

OFFLINE_STATUSES = frozenset({"needs_login", "proxy_down", "banned"})


def _settings_from_web_cookies(
    encrypted_web_cookies: str | None,
    *,
    proxy: str,
    username: str | None,
) -> tuple[dict, str | None] | None:
    """Tenta reviver sessão instagrapi a partir do sessionid nos cookies web salvos."""
    cookies = decrypt_web_cookies(encrypted_web_cookies)
    sid = clean_sessionid((cookies or {}).get("sessionid") or "")
    if not sid:
        return None
    settings_dict, resolved_user = login_with_sessionid(
        sid,
        proxy=proxy,
        username_hint=username,
    )
    return settings_dict, resolved_user


def _notify_offline_if_changed(
    *,
    new_status: str,
    reason: str,
    prev_status: str | None,
    user_id: int | None,
    username: str | None,
) -> None:
    if not user_id or not username:
        return
    if prev_status == new_status:
        return
    if new_status not in OFFLINE_STATUSES:
        return
    create_notification(
        user_id,
        f"Conta @{username} fora do ar",
        reason[:200],
        kind="offline",
        link="/accounts",
    )


@celery_app.task(name="celery_app.tasks.health.check_all_accounts")
def check_all_accounts() -> dict:
    """Enfileira verificação de um lote de contas (round-robin), sem engolir publish."""
    limit = 50
    with session_scope() as db:
        account_ids = list(
            db.scalars(
                select(InstagramAccount.id)
                .where(InstagramAccount.status.notin_(("paused", "deleted")))
                .order_by(
                    InstagramAccount.last_health_check_at.asc().nullsfirst(),
                    InstagramAccount.id.asc(),
                )
                .limit(limit)
            ).all()
        )
    # 8s entre checks → ~6–7 min para o lote; não compete com publish na fila default.
    for idx, account_id in enumerate(account_ids):
        check_account_health.apply_async(args=[account_id], countdown=idx * 8)
    return {"queued": len(account_ids), "limit": limit}


@celery_app.task(name="celery_app.tasks.health.check_account_health", max_retries=0)
def check_account_health(account_id: int) -> dict:
    with session_scope() as db:
        account = db.get(InstagramAccount, account_id)
        if account is None or account.status in ("paused", "deleted"):
            return {"skipped": True}

        proxy = account.proxy
        settings_dict = deserialize_settings(account.session_json)
        encrypted_web_cookies = account.encrypted_web_cookies
        provider = account.provider or "instagrapi"
        meta_token = decrypt_secret(account.encrypted_meta_access_token)
        meta_token_expires_at = account.meta_token_expires_at
        username = account.username

    now = dt.datetime.utcnow()

    if provider == "meta":
        try:
            if not meta_token:
                raise MetaInstagramError("Token oficial ausente")
            with session_scope() as db:
                acc_chk = db.get(InstagramAccount, account_id)
                if acc_chk and not acc_chk.user_meta_app_id:
                    raise MetaInstagramError(
                        "Conta sem app Meta. Cadastre em Meus Apps e reconecte."
                    )
            meta_proxy = (proxy or "").strip() or None
            if meta_proxy and not check_proxy(meta_proxy):
                log.warning(
                    "META health proxy inválida account=%s — validando sem proxy",
                    account_id,
                )
                meta_proxy = None
            validate_meta_token(meta_token, proxy=meta_proxy)
            refreshed_token = None
            refreshed_expires_at = meta_token_expires_at
            expires_cmp = meta_token_expires_at
            if expires_cmp is not None and expires_cmp.tzinfo is not None:
                expires_cmp = expires_cmp.astimezone(dt.timezone.utc).replace(tzinfo=None)
            if (
                expires_cmp is not None
                and expires_cmp <= now + dt.timedelta(days=7)
            ):
                refreshed_token, refreshed_expires_at = refresh_meta_token(
                    meta_token, proxy=meta_proxy
                )
            with session_scope() as db:
                acc = db.get(InstagramAccount, account_id)
                if acc and acc.status not in ("paused", "deleted"):
                    if refreshed_token:
                        acc.encrypted_meta_access_token = encrypt_secret(refreshed_token)
                        acc.meta_token_expires_at = refreshed_expires_at
                    acc.status = "active"
                    acc.last_error = None
                    acc.last_health_check_at = now
            return {"account_id": account_id, "status": "active", "provider": "meta"}
        except MetaInstagramError as exc:
            with session_scope() as db:
                acc = db.get(InstagramAccount, account_id)
                if not acc or acc.status in ("paused", "deleted"):
                    return {"account_id": account_id, "status": "needs_login"}
                prev = acc.status
                acc.status = "needs_login"
                acc.last_error = str(exc)[:1000]
                acc.last_health_check_at = now
                uid, uname = acc.user_id, acc.username
            _notify_offline_if_changed(
                new_status="needs_login",
                reason=str(exc),
                prev_status=prev,
                user_id=uid,
                username=uname,
            )
            return {"account_id": account_id, "status": "needs_login", "provider": "meta"}

    if not proxy or not proxy.strip():
        with session_scope() as db:
            acc = db.get(InstagramAccount, account_id)
            if not acc or acc.status in ("paused", "deleted"):
                return {"account_id": account_id, "status": "proxy_down"}
            prev = acc.status
            acc.status = "proxy_down"
            acc.last_error = "Proxy não configurada"
            acc.last_health_check_at = now
            uid, uname = acc.user_id, acc.username
        _notify_offline_if_changed(
            new_status="proxy_down",
            reason="Proxy não configurada",
            prev_status=prev,
            user_id=uid,
            username=uname,
        )
        return {"account_id": account_id, "status": "proxy_down"}

    if not check_proxy(proxy):
        with session_scope() as db:
            acc = db.get(InstagramAccount, account_id)
            if not acc or acc.status in ("paused", "deleted"):
                return {"account_id": account_id, "status": "proxy_down"}
            prev = acc.status
            acc.status = "proxy_down"
            acc.last_error = "Proxy vazando IP do servidor"
            acc.last_health_check_at = now
            uid, uname = acc.user_id, acc.username
        _notify_offline_if_changed(
            new_status="proxy_down",
            reason="Proxy vazando IP do servidor",
            prev_status=prev,
            user_id=uid,
            username=uname,
        )
        return {"account_id": account_id, "status": "proxy_down"}

    if not settings_dict:
        # Sem session_json: tenta sessionid dos cookies web salvos (sem senha).
        try:
            revived = _settings_from_web_cookies(
                encrypted_web_cookies,
                proxy=proxy,
                username=username,
            )
            if revived:
                new_settings, resolved_user = revived
                with session_scope() as db:
                    acc = db.get(InstagramAccount, account_id)
                    if acc and acc.status not in ("paused", "deleted"):
                        acc.session_json = serialize_settings(new_settings)
                        new_sid = extract_sessionid_from_settings(new_settings)
                        merged = merge_sessionid_into_web_cookies(
                            acc.encrypted_web_cookies, new_sid
                        )
                        if merged:
                            acc.encrypted_web_cookies = merged
                        if resolved_user:
                            acc.username = resolved_user
                        acc.status = "active"
                        acc.last_error = None
                        acc.last_login_at = now
                        acc.last_health_check_at = now
                log.info(
                    "health revive via cookies web OK account=%s (sem session_json)",
                    account_id,
                )
                return {
                    "account_id": account_id,
                    "status": "active",
                    "reconnected": True,
                    "via": "web_cookies",
                }
        except InstagramAuthError as exc:
            log.warning(
                "health revive cookies falhou account=%s: %s", account_id, exc
            )
        with session_scope() as db:
            acc = db.get(InstagramAccount, account_id)
            if not acc or acc.status in ("paused", "deleted"):
                return {"account_id": account_id, "status": "needs_login"}
            prev = acc.status
            acc.status = "needs_login"
            acc.last_error = "Sessão expirada — reconecte com sessionid ou cookies web"
            acc.last_health_check_at = now
            uid, uname = acc.user_id, acc.username
        _notify_offline_if_changed(
            new_status="needs_login",
            reason="Sessão expirada — reconecte com sessionid ou cookies web",
            prev_status=prev,
            user_id=uid,
            username=uname,
        )
        return {"account_id": account_id, "status": "needs_login"}

    try:
        resolved_user: str | None = None
        try:
            new_settings = try_refresh_session(
                settings_dict=settings_dict,
                proxy=proxy,
                username=username,
                password=None,
            )
            cl = get_ready_client(
                settings_dict=new_settings,
                proxy=proxy,
                username=username,
                password=None,
            )
            cl.account_info()
        except InstagramAuthError:
            # Sessão instagrapi morta — tenta cookies web salvos antes de marcar offline.
            revived = _settings_from_web_cookies(
                encrypted_web_cookies,
                proxy=proxy,
                username=username,
            )
            if not revived:
                raise
            new_settings, resolved_user = revived
            cl = get_ready_client(
                settings_dict=new_settings,
                proxy=proxy,
                username=resolved_user or username,
                password=None,
            )
            cl.account_info()
            new_settings = cl.get_settings()
            log.info("health revive via cookies web OK account=%s", account_id)
        needs_login_from_log: tuple[str, str | None, int | None, str | None] | None = None
        with session_scope() as db:
            acc = db.get(InstagramAccount, account_id)
            if acc and acc.status not in ("paused", "deleted"):
                auth_reason = latest_auth_failure_reason(db, account_id)
                if auth_reason:
                    prev = acc.status
                    acc.status = "needs_login"
                    acc.last_error = auth_status_reason(auth_reason)
                    needs_login_from_log = (acc.last_error, acc.username, acc.user_id, prev)
                else:
                    acc.session_json = serialize_settings(new_settings)
                    new_sid = extract_sessionid_from_settings(new_settings)
                    merged = merge_sessionid_into_web_cookies(
                        acc.encrypted_web_cookies, new_sid
                    )
                    if merged:
                        acc.encrypted_web_cookies = merged
                    if resolved_user:
                        acc.username = resolved_user
                    if acc.status in OFFLINE_STATUSES:
                        acc.status = "active"
                    acc.last_error = None
                    acc.last_login_at = now
                acc.last_health_check_at = now
        if needs_login_from_log:
            reason, uname, uid, prev = needs_login_from_log
            _notify_offline_if_changed(
                new_status="needs_login",
                reason=reason,
                prev_status=prev,
                user_id=uid,
                username=uname,
            )
            return {"account_id": account_id, "status": "needs_login", "error": reason}
        return {"account_id": account_id, "status": "active"}
    except InstagramTwoFactorRequired as exc:
        with session_scope() as db:
            acc = db.get(InstagramAccount, account_id)
            if not acc or acc.status in ("paused", "deleted"):
                return {"account_id": account_id, "status": "needs_login", "error": "2fa"}
            prev = acc.status
            acc.status = "needs_login"
            acc.last_error = f"2FA necessário: {exc}"[:1000]
            acc.last_health_check_at = now
            uid, uname = acc.user_id, acc.username
        _notify_offline_if_changed(
            new_status="needs_login",
            reason="2FA necessário — reconecte no painel com sessionid/cookies",
            prev_status=prev,
            user_id=uid,
            username=uname,
        )
        return {"account_id": account_id, "status": "needs_login", "error": "2fa"}
    except InstagramAuthError as exc:
        with session_scope() as db:
            acc = db.get(InstagramAccount, account_id)
            if not acc or acc.status in ("paused", "deleted"):
                return {"account_id": account_id, "status": "needs_login", "error": str(exc)}
            prev = acc.status
            acc.status = "needs_login"
            acc.last_error = str(exc)[:1000]
            acc.last_health_check_at = now
            uid, uname = acc.user_id, acc.username
        _notify_offline_if_changed(
            new_status="needs_login",
            reason=str(exc)[:200],
            prev_status=prev,
            user_id=uid,
            username=uname,
        )
        return {"account_id": account_id, "status": "needs_login", "error": str(exc)}
    except Exception as exc:
        log.warning("health check account %s: %s", account_id, exc)
        with session_scope() as db:
            acc = db.get(InstagramAccount, account_id)
            if acc and acc.status not in ("paused", "deleted"):
                acc.last_health_check_at = now
                acc.last_error = str(exc)[:1000]
        return {"account_id": account_id, "status": "error", "error": str(exc)}
