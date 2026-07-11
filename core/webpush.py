"""Envio de Web Push (VAPID) via pywebpush."""
from __future__ import annotations

import json
import logging
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)


def vapid_configured() -> bool:
    return bool(settings.vapid_public_key and settings.vapid_private_key)


def send_web_push(subscription_info: dict[str, Any], payload: dict[str, Any]) -> bool:
    """Envia uma notificação. Retorna False se falhar (ex.: subscription expirada)."""
    if not vapid_configured():
        log.debug("VAPID não configurado — push ignorado")
        return False
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        log.warning("pywebpush não instalado")
        return False

    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject},
            ttl=60 * 60,
        )
        return True
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        log.warning("Web push falhou (status=%s): %s", status, exc)
        if status in (404, 410):
            raise
        return False
    except Exception:
        log.exception("Erro inesperado ao enviar web push")
        return False


def _load_user_subscriptions(user_id: int) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from core.database import session_scope
    from models.models import PushSubscription

    with session_scope() as db:
        subs = db.scalars(
            select(PushSubscription).where(PushSubscription.user_id == user_id)
        ).all()
        return [
            {
                "id": s.id,
                "info": {
                    "endpoint": s.endpoint,
                    "keys": {"p256dh": s.p256dh, "auth": s.auth},
                },
            }
            for s in subs
        ]


def notify_user_push(
    user_id: int,
    payload: dict[str, Any],
    *,
    kind: str = "publish",
    force: bool = False,
) -> tuple[int, int]:
    """Envia push para todas as subscriptions do usuário. Retorna (enviados, falhas)."""
    if not vapid_configured():
        log.warning(
            "Push ignorado: VAPID não configurado no worker (defina VAPID_PUBLIC_KEY/PRIVATE_KEY)"
        )
        return 0, 0

    from core.database import session_scope
    from core.notification_prefs import DEFAULT_PREFS, can_notify_push, get_notification_prefs_by_id
    from models.models import PushSubscription

    prefs = DEFAULT_PREFS
    try:
        with session_scope() as db:
            prefs = get_notification_prefs_by_id(db, user_id) or DEFAULT_PREFS
    except Exception:
        log.exception("Falha ao ler prefs de push user=%s — enviando mesmo assim", user_id)

    if not force and not can_notify_push(kind, prefs):
        log.info("Push bloqueado por prefs user=%s kind=%s", user_id, kind)
        return 0, 0

    rows = _load_user_subscriptions(user_id)
    if not rows:
        log.warning("Push: nenhuma subscription para user=%s", user_id)
        return 0, 0

    sent = 0
    failed = 0
    dead_ids: list[int] = []
    for row in rows:
        try:
            if send_web_push(row["info"], payload):
                sent += 1
            else:
                failed += 1
        except Exception:
            failed += 1
            dead_ids.append(row["id"])

    if dead_ids:
        with session_scope() as db:
            for sid in dead_ids:
                sub = db.get(PushSubscription, sid)
                if sub:
                    db.delete(sub)

    log.info("Push user=%s kind=%s sent=%s failed=%s", user_id, kind, sent, failed)
    return sent, failed


def notify_user_publish_success(user_id: int, username: str, content_type: str = "reel") -> None:
    label = {"reel": "Reel", "story": "Story", "photo": "Foto"}.get(content_type, "Post")
    sent, failed = notify_user_push(
        user_id,
        {
            "title": "Post enviado com sucesso",
            "body": f"{label} publicado em @{username}",
            "url": "/logs",
            "tag": f"publish-{username}",
        },
        kind="publish",
        force=False,
    )
    log.info(
        "notify_user_publish_success user=%s @%s type=%s sent=%s failed=%s",
        user_id,
        username,
        content_type,
        sent,
        failed,
    )


def send_test_push(user_id: int) -> tuple[int, int]:
    return notify_user_push(
        user_id,
        {
            "title": "instablack — teste OK",
            "body": "Notificações no celular funcionando!",
            "url": "/perfil",
            "tag": "instablack-test",
        },
        kind="publish",
        force=True,
    )
