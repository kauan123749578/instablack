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
        # 404/410 = subscription morta
        if status in (404, 410):
            raise
        return False
    except Exception:
        log.exception("Erro inesperado ao enviar web push")
        return False


def notify_user_publish_success(user_id: int, username: str, content_type: str = "reel") -> None:
    """Notifica todas as subscriptions do usuário sobre post enviado."""
    if not vapid_configured():
        return

    from sqlalchemy import select

    from core.database import session_scope
    from models.models import PushSubscription

    label = {"reel": "Reel", "story": "Story", "photo": "Foto"}.get(content_type, "Post")
    payload = {
        "title": "Post enviado com sucesso",
        "body": f"{label} publicado em @{username}",
        "url": "/logs",
        "tag": f"publish-{username}",
    }

    dead_ids: list[int] = []
    with session_scope() as db:
        subs = db.scalars(
            select(PushSubscription).where(PushSubscription.user_id == user_id)
        ).all()
        rows = [
            {
                "id": s.id,
                "info": {
                    "endpoint": s.endpoint,
                    "keys": {"p256dh": s.p256dh, "auth": s.auth},
                },
            }
            for s in subs
        ]

    for row in rows:
        try:
            ok = send_web_push(row["info"], payload)
            if not ok:
                continue
        except Exception:
            dead_ids.append(row["id"])

    if dead_ids:
        with session_scope() as db:
            for sid in dead_ids:
                sub = db.get(PushSubscription, sid)
                if sub:
                    db.delete(sub)
