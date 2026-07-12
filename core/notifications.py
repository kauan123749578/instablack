"""Notificações in-app (card do sino) + helper para criar."""
from __future__ import annotations

import logging

from core.database import session_scope
from core.notification_prefs import (
    DEFAULT_PREFS,
    can_notify_in_app,
    get_notification_prefs_by_id,
)
from models.models import AppNotification

log = logging.getLogger(__name__)

# Kinds que disparam push no celular (respeitando prefs). metadata fica só no sino.
_PUSH_KINDS = frozenset({
    "publish",
    "warmup",
    "warning",
    "error",
    "fail",
    "offline",
    "account",
})

CONTENT_LABELS = {"reel": "Reel", "story": "Story", "photo": "Foto"}


def content_label(content_type: str | None) -> str:
    return CONTENT_LABELS.get((content_type or "").lower(), "Post")


def create_notification(
    user_id: int,
    title: str,
    body: str = "",
    *,
    kind: str = "info",
    link: str | None = None,
    force: bool = False,
    send_push: bool | None = None,
) -> bool:
    """Persiste notificação no sino e, se aplicável, envia push. Retorna True se salvou."""
    if not user_id:
        log.warning("create_notification sem user_id: %s", title)
        return False
    saved = False
    try:
        with session_scope() as db:
            prefs = DEFAULT_PREFS
            try:
                prefs = get_notification_prefs_by_id(db, user_id) or DEFAULT_PREFS
            except Exception:
                log.exception("Falha ao ler prefs — criando notificação mesmo assim")

            if not force and not can_notify_in_app(kind, prefs):
                log.info(
                    "Notificação in-app bloqueada por prefs user=%s kind=%s title=%s",
                    user_id,
                    kind,
                    title,
                )
                return False

            db.add(
                AppNotification(
                    user_id=user_id,
                    title=title[:255],
                    body=(body or "")[:1000],
                    kind=(kind or "info")[:32],
                    link=(link or None),
                    is_read=False,
                )
            )
        saved = True
        log.info("Notificação in-app criada user=%s kind=%s title=%s", user_id, kind, title)
    except Exception:
        log.exception("Falha ao criar notificação in-app user=%s title=%s", user_id, title)
        return False

    if send_push is None:
        send_push = (kind or "") in _PUSH_KINDS
    if send_push:
        try:
            from core.webpush import notify_user_push

            notify_user_push(
                user_id,
                {
                    "title": title[:120],
                    "body": (body or "")[:200],
                    "url": link or "/",
                    "tag": f"{kind}-{user_id}-{int(__import__('time').time()*1000)}",
                },
                kind=kind,
                force=force,
            )
        except Exception:
            log.exception("Push falhou após notificação in-app user=%s kind=%s", user_id, kind)
    return saved
