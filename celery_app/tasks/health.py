"""Verificação periódica de saúde das contas Instagram."""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select, text

from app.security import decrypt_secret, encrypt_secret
from app.utils.auth_failures import auth_status_reason, latest_auth_failure_reason
from app.utils.proxy import clean_sessionid
from app.utils.totp import TotpError, current_totp_code, normalize_totp_secret
from celery_app.config import celery_app
from core import aiograpi_client as aio_ig
from core.database import session_scope
from core.instagram import (
    InstagramAuthError,
    InstagramTwoFactorRequired,
    check_proxy,
    deserialize_settings,
    extract_sessionid_from_settings,
    get_ready_client,
    login_with_credentials,
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
from models.models import Automation, InstagramAccount, PublishLog

log = logging.getLogger(__name__)

OFFLINE_STATUSES = frozenset({"needs_login", "proxy_down", "banned"})

# Recovery one-shot após bug health+Phantom (Meta/aiograpi marcadas needs_login à toa).
RECOVER_REDIS_KEY = "recover:post_phantom_publish_v1"
RECOVER_REDIS_TTL = 30 * 24 * 3600

# Senha do cofre no máximo 1× a cada 6h por conta (Redis). Evita martelar login.
VAULT_REVIVE_COOLDOWN_SEC = 6 * 60 * 60


def _looks_like_native_challenge(msg: str | None) -> bool:
    low = (msg or "").lower()
    return any(
        x in low
        for x in (
            "challenge",
            "checkpoint",
            "manual verification",
            "challenge_code_handler",
            "select_contact",
            "submit_phone",
        )
    )


def _totp_from_encrypted(encrypted: str | None) -> str | None:
    plain = decrypt_secret(encrypted)
    if not plain:
        return None
    try:
        secret = normalize_totp_secret(plain)
        code, _ = current_totp_code(secret)
        return code
    except TotpError:
        return None


def _vault_revive_allowed(account_id: int) -> bool:
    """True se ainda não tentamos senha do cofre recentemente."""
    try:
        from redis import Redis

        from app.config import settings

        r = Redis.from_url(
            settings.redis_url, socket_connect_timeout=2, socket_timeout=2
        )
        key = f"health:vault_revive:{account_id}"
        # SET NX: só permite se a chave ainda não existir
        return bool(r.set(key, "1", nx=True, ex=VAULT_REVIVE_COOLDOWN_SEC))
    except Exception as exc:
        log.warning("vault revive cooldown Redis falhou account=%s: %s", account_id, exc)
        # Sem Redis: tenta mesmo assim (melhor que nunca reviver)
        return True


def _try_vault_password_revive(
    *,
    account_id: int,
    username: str,
    proxy: str,
    provider: str,
    encrypted_password: str | None,
    encrypted_totp: str | None,
    last_error: str | None,
) -> dict | None:
    """Tenta login com senha+TOTP do cofre. None = não tentou / sem credencial."""
    if not encrypted_password:
        return None
    if _looks_like_native_challenge(last_error):
        log.info(
            "health skip vault revive account=%s — challenge nativo (só reconectar manual)",
            account_id,
        )
        return None
    if not _vault_revive_allowed(account_id):
        log.info("health skip vault revive account=%s — cooldown 6h", account_id)
        return None

    password = decrypt_secret(encrypted_password)
    if not password:
        return None

    code = _totp_from_encrypted(encrypted_totp)
    backend = (provider or "instagrapi").lower()
    login_fn = (
        aio_ig.login_with_credentials
        if backend == "aiograpi"
        else login_with_credentials
    )
    try:
        settings = login_fn(
            username=username,
            password=password,
            verification_code=code,
            proxy=proxy,
        )
        log.info("health vault revive OK account=%s @%s via=%s", account_id, username, backend)
        return {"settings": settings, "via": "vault_password"}
    except InstagramTwoFactorRequired as exc:
        if code:
            log.warning("health vault revive 2FA falhou account=%s: %s", account_id, exc)
            return {"error": f"2FA inválido: {exc}"}
        # Sem TOTP no cofre — precisa código no painel
        log.info("health vault revive precisa 2FA account=%s", account_id)
        return {"error": "2FA necessário — salve o Authenticator no Cofre ou reconecte no painel"}
    except InstagramAuthError as exc:
        log.warning("health vault revive auth falhou account=%s: %s", account_id, exc)
        return {"error": str(exc)}
    except Exception as exc:
        log.exception("health vault revive erro account=%s", account_id)
        return {"error": str(exc)[:300]}


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


def _check_aiograpi_health(
    *,
    account_id: int,
    username: str,
    proxy: str,
    settings_dict: dict | None,
    encrypted_password: str | None,
    encrypted_totp: str | None,
    last_error: str | None,
    now: dt.datetime,
) -> dict:
    """Health da aiograpi — NÃO usar instagrapi/Phantom (sessão e client são outros)."""
    password = decrypt_secret(encrypted_password) if encrypted_password else None

    def _persist_active(new_settings: dict) -> dict:
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
        return {"account_id": account_id, "status": "active", "provider": "aiograpi"}

    try:
        new_settings = aio_ig.try_refresh_session(
            settings_dict=settings_dict,
            proxy=proxy,
            username=username,
            password=password,
        )
        return _persist_active(new_settings)
    except InstagramTwoFactorRequired as exc:
        err = f"2FA necessário: {exc}"
    except InstagramAuthError as exc:
        vault = _try_vault_password_revive(
            account_id=account_id,
            username=username,
            proxy=proxy,
            provider="aiograpi",
            encrypted_password=encrypted_password,
            encrypted_totp=encrypted_totp,
            last_error=last_error,
        )
        if vault and vault.get("settings"):
            return _persist_active(vault["settings"])
        err = (vault or {}).get("error") or str(exc)
    except Exception as exc:
        log.warning("health aiograpi account=%s: %s", account_id, exc)
        err = str(exc)[:1000]

    with session_scope() as db:
        acc = db.get(InstagramAccount, account_id)
        if not acc or acc.status in ("paused", "deleted"):
            return {"account_id": account_id, "status": "needs_login", "provider": "aiograpi"}
        prev = acc.status
        acc.status = "needs_login"
        acc.last_error = err[:1000]
        acc.last_health_check_at = now
        uid, uname = acc.user_id, acc.username
    _notify_offline_if_changed(
        new_status="needs_login",
        reason=err[:200],
        prev_status=prev,
        user_id=uid,
        username=uname,
    )
    return {"account_id": account_id, "status": "needs_login", "provider": "aiograpi", "error": err}


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


def _clear_meta_publish_locks(account_id: int) -> None:
    """Libera cooldown/inflight Meta que possa ter ficado preso sem publish."""
    try:
        from redis import Redis

        from app.config import settings

        r = Redis.from_url(
            settings.redis_url, socket_connect_timeout=2, socket_timeout=2
        )
        for prefix in (
            "meta:cooldown:",
            "meta:inflight:",
            "meta:defer_sched:",
        ):
            try:
                r.delete(f"{prefix}{account_id}")
            except Exception:
                pass
    except Exception as exc:
        log.warning("clear meta locks account=%s: %s", account_id, exc)


@celery_app.task(name="celery_app.tasks.health.recover_publish_after_phantom")
def recover_publish_after_phantom(*, force: bool = False) -> dict:
    """Reativa Meta/aiograpi em needs_login (token/sessão ainda válidos) e dispara automações.

    Roda 1× após deploy (Redis NX). Sem isso o rank fica vazio: automações active
    mas contas needs_login → execute_automation pula tudo.
    """
    from app.config import settings

    try:
        from redis import Redis

        r = Redis.from_url(
            settings.redis_url, socket_connect_timeout=2, socket_timeout=2
        )
        if not force and not r.set(RECOVER_REDIS_KEY, "1", nx=True, ex=RECOVER_REDIS_TTL):
            return {"skipped": True, "reason": "already_ran"}
    except Exception as exc:
        log.warning("recover Redis gate falhou — seguindo: %s", exc)

    now = dt.datetime.utcnow()
    revived_meta: list[int] = []
    revived_aio: list[int] = []
    failed: list[dict] = []
    nudged_autos: list[int] = []

    with session_scope() as db:
        stuck = db.scalars(
            select(InstagramAccount).where(
                InstagramAccount.status == "needs_login",
                InstagramAccount.provider.in_(("meta", "aiograpi")),
            )
        ).all()
        candidates = [
            {
                "id": a.id,
                "provider": (a.provider or "").lower(),
                "proxy": a.proxy,
                "username": a.username,
                "meta_token": decrypt_secret(a.encrypted_meta_access_token),
                "meta_app": a.user_meta_app_id,
                "session": deserialize_settings(a.session_json),
                "password": decrypt_secret(a.encrypted_password),
                "last_error": a.last_error,
            }
            for a in stuck
        ]

    for row in candidates:
        aid = row["id"]
        provider = row["provider"]
        try:
            if provider == "meta":
                if not row["meta_token"] or not row["meta_app"]:
                    failed.append({"id": aid, "reason": "meta_token_or_app_missing"})
                    continue
                if _looks_like_native_challenge(row["last_error"]):
                    # Meta OAuth inválido de verdade — não forçar.
                    failed.append({"id": aid, "reason": "challenge_or_oauth"})
                    continue
                meta_proxy = (row["proxy"] or "").strip() or None
                if meta_proxy and not check_proxy(meta_proxy):
                    meta_proxy = None
                validate_meta_token(row["meta_token"], proxy=meta_proxy)
                with session_scope() as db:
                    acc = db.get(InstagramAccount, aid)
                    if acc and acc.status == "needs_login":
                        acc.status = "active"
                        acc.last_error = None
                        acc.last_health_check_at = now
                _clear_meta_publish_locks(aid)
                revived_meta.append(aid)
                log.info("recover Meta OK account=%s @%s", aid, row["username"])
            elif provider == "aiograpi":
                if not row["session"] and not row["password"]:
                    failed.append({"id": aid, "reason": "aiograpi_no_session"})
                    continue
                if not row["proxy"] or not str(row["proxy"]).strip():
                    failed.append({"id": aid, "reason": "proxy_missing"})
                    continue
                if not check_proxy(row["proxy"]):
                    failed.append({"id": aid, "reason": "proxy_down"})
                    continue
                if _looks_like_native_challenge(row["last_error"]):
                    failed.append({"id": aid, "reason": "challenge"})
                    continue
                new_settings = aio_ig.try_refresh_session(
                    settings_dict=row["session"],
                    proxy=row["proxy"],
                    username=row["username"],
                    password=row["password"],
                )
                with session_scope() as db:
                    acc = db.get(InstagramAccount, aid)
                    if acc and acc.status == "needs_login":
                        acc.session_json = serialize_settings(new_settings)
                        acc.status = "active"
                        acc.last_error = None
                        acc.last_login_at = now
                        acc.last_health_check_at = now
                revived_aio.append(aid)
                log.info("recover aiograpi OK account=%s @%s", aid, row["username"])
        except Exception as exc:
            failed.append({"id": aid, "reason": str(exc)[:200]})
            log.warning("recover falhou account=%s: %s", aid, exc)

    # Automações active → next_run_at agora (intervalo/recurring; calendário: só se null).
    with session_scope() as db:
        autos = db.scalars(
            select(Automation).where(Automation.status == "active")
        ).all()
        for auto in autos:
            mode = (auto.schedule_type or "").strip().lower()
            if mode == "calendar":
                if auto.next_run_at is None and auto.calendar_days and auto.calendar_time:
                    from app.utils.calendar_schedule import next_calendar_run, parse_calendar_days

                    nxt = next_calendar_run(
                        parse_calendar_days(auto.calendar_days),
                        auto.calendar_time,
                        now,
                    ) or (now + dt.timedelta(minutes=5))
                    db.execute(
                        text("UPDATE automations SET next_run_at = :nxt WHERE id = :id"),
                        {"nxt": nxt, "id": auto.id},
                    )
                    nudged_autos.append(auto.id)
                continue
            # Intervalo / postar agora: dispara no próximo tick
            db.execute(
                text("UPDATE automations SET next_run_at = :nxt WHERE id = :id"),
                {"nxt": now, "id": auto.id},
            )
            nudged_autos.append(auto.id)

    # Prioriza health nas contas ainda needs_login (instagrapi etc.)
    with session_scope() as db:
        leftover = list(
            db.scalars(
                select(InstagramAccount.id)
                .where(InstagramAccount.status == "needs_login")
                .order_by(InstagramAccount.id.asc())
                .limit(80)
            ).all()
        )
    for idx, account_id in enumerate(leftover):
        check_account_health.apply_async(args=[account_id], countdown=idx * 5)

    result = {
        "ok": True,
        "revived_meta": revived_meta,
        "revived_aiograpi": revived_aio,
        "nudged_automations": len(nudged_autos),
        "failed": len(failed),
        "health_queued": len(leftover),
    }
    log.warning(
        "recover_publish_after_phantom: meta=%s aio=%s autos=%s failed=%s health_q=%s",
        len(revived_meta),
        len(revived_aio),
        len(nudged_autos),
        len(failed),
        len(leftover),
    )
    return result


@celery_app.task(name="celery_app.tasks.health.check_all_accounts")
def check_all_accounts() -> dict:
    """Enfileira verificação de um lote de contas (round-robin), sem engolir publish."""
    # 1× pós-deploy: reativa Meta/aiograpi e dispara automações active.
    try:
        recover_publish_after_phantom.delay()
    except Exception as exc:
        log.warning("enqueue recover_publish_after_phantom: %s", exc)

    limit = 50
    with session_scope() as db:
        # Prioriza needs_login (voltar ao ar) antes de contas já active.
        account_ids = list(
            db.scalars(
                select(InstagramAccount.id)
                .where(InstagramAccount.status.notin_(("paused", "deleted")))
                .order_by(
                    # needs_login primeiro
                    InstagramAccount.status.desc(),
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
        encrypted_password = account.encrypted_password
        encrypted_totp = getattr(account, "encrypted_totp_secret", None)
        last_error = account.last_error

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

    if provider == "aiograpi":
        return _check_aiograpi_health(
            account_id=account_id,
            username=username,
            proxy=proxy,
            settings_dict=settings_dict,
            encrypted_password=encrypted_password,
            encrypted_totp=encrypted_totp,
            last_error=last_error,
            now=now,
        )

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

        # Sem sessão e cookies falharam → senha+TOTP do cofre (com cooldown).
        vault = _try_vault_password_revive(
            account_id=account_id,
            username=username,
            proxy=proxy,
            provider=provider,
            encrypted_password=encrypted_password,
            encrypted_totp=encrypted_totp,
            last_error=last_error,
        )
        if vault and vault.get("settings"):
            new_settings = vault["settings"]
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
                    acc.status = "active"
                    acc.last_error = None
                    acc.last_login_at = now
                    acc.last_health_check_at = now
            return {
                "account_id": account_id,
                "status": "active",
                "reconnected": True,
                "via": vault.get("via") or "vault_password",
            }

        offline_reason = (
            (vault or {}).get("error")
            or "Sessão expirada — reconecte com sessionid, cookies web ou senha no Cofre"
        )
        with session_scope() as db:
            acc = db.get(InstagramAccount, account_id)
            if not acc or acc.status in ("paused", "deleted"):
                return {"account_id": account_id, "status": "needs_login"}
            prev = acc.status
            acc.status = "needs_login"
            acc.last_error = offline_reason[:1000]
            acc.last_health_check_at = now
            uid, uname = acc.user_id, acc.username
        _notify_offline_if_changed(
            new_status="needs_login",
            reason=offline_reason[:200],
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
            # Sessão morta — cookies web, depois senha+TOTP do cofre.
            revived = _settings_from_web_cookies(
                encrypted_web_cookies,
                proxy=proxy,
                username=username,
            )
            vault = None
            if revived:
                new_settings, resolved_user = revived
                log.info("health revive via cookies web OK account=%s", account_id)
            else:
                vault = _try_vault_password_revive(
                    account_id=account_id,
                    username=username,
                    proxy=proxy,
                    provider=provider,
                    encrypted_password=encrypted_password,
                    encrypted_totp=encrypted_totp,
                    last_error=last_error,
                )
                if not vault or not vault.get("settings"):
                    if vault and vault.get("error"):
                        raise InstagramAuthError(vault["error"])
                    raise
                new_settings = vault["settings"]
                resolved_user = None
                log.info(
                    "health revive via vault OK account=%s via=%s",
                    account_id,
                    vault.get("via"),
                )
            cl = get_ready_client(
                settings_dict=new_settings,
                proxy=proxy,
                username=resolved_user or username,
                password=None,
            )
            cl.account_info()
            if hasattr(cl, "get_settings"):
                new_settings = cl.get_settings()
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


@celery_app.task(name="celery_app.tasks.health.purge_old_publish_logs")
def purge_old_publish_logs() -> dict:
    """Remove publish_logs antigos (retenção configurável). Roda na fila health."""
    from sqlalchemy import delete

    from app.config import settings

    days = int(getattr(settings, "publish_logs_retention_days", 0) or 0)
    if days <= 0:
        return {"skipped": True, "reason": "retention_disabled"}

    cutoff = dt.datetime.utcnow() - dt.timedelta(days=days)
    with session_scope() as db:
        result = db.execute(delete(PublishLog).where(PublishLog.created_at < cutoff))
        deleted = int(result.rowcount or 0)
    log.info("purge_old_publish_logs: deleted=%s older_than_days=%s", deleted, days)
    return {"deleted": deleted, "retention_days": days}
