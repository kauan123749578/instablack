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
) -> tuple[int, int]:
    """Envia push para todas as subscriptions do usuário. Retorna (enviados, falhas)."""
    if not vapid_configured():
        return 0, 0

    from core.database import session_scope
    from core.notification_prefs import can_notify_push, get_notification_prefs_by_id
    from models.models import PushSubscription

    with session_scope() as db:
        prefs = get_notification_prefs_by_id(db, user_id)
    if not can_notify_push(kind, prefs):
        return 0, 0

    rows = _load_user_subscriptions(user_id)
    if not rows:
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

    return sent, failed


def notify_user_publish_success(user_id: int, username: str, content_type: str = "reel") -> None:
    label = {"reel": "Reel", "story": "Story", "photo": "Foto"}.get(content_type, "Post")
    notify_user_push(
        user_id,
        {
            "title": "Post enviado com sucesso",
            "body": f"{label} publicado em @{username}",
            "url": "/logs",
            "tag": f"publish-{username}",
        },
        kind="publish",
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
    )
