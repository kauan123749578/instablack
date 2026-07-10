"""Notificações in-app (card do sino) + helper para criar."""
from __future__ import annotations

import logging

from core.database import session_scope
from core.notification_prefs import can_notify_in_app, get_notification_prefs_by_id
from models.models import AppNotification

log = logging.getLogger(__name__)


def create_notification(
    user_id: int,
    title: str,
    body: str = "",
    *,
    kind: str = "info",
    link: str | None = None,
) -> None:
    """Persiste notificação para aparecer no card do sino."""
    try:
        with session_scope() as db:
            prefs = get_notification_prefs_by_id(db, user_id)
            if not can_notify_in_app(kind, prefs):
                return
            db.add(
                AppNotification(
                    user_id=user_id,
                    title=title[:255],
                    body=(body or "")[:1000],
                    kind=kind[:32],
                    link=(link or None),
                    is_read=False,
                )
            )
    except Exception:
        log.exception("Falha ao criar notificação in-app")
