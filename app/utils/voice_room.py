"""Acesso à sala Call (LiveKit) — só owner ou allow_voice_room."""
from __future__ import annotations

from models.models import User


def can_access_voice_room(user: User | None) -> bool:
    if user is None:
        return False
    if bool(getattr(user, "is_owner", False)):
        return True
    return bool(getattr(user, "allow_voice_room", False))
