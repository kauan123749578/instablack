"""Acesso à API não oficial (Instagrapi: senha / sessionid / session.json).

Só o dono e usuários liberados no admin (`allow_instagrapi`) podem conectar
por esses métodos. Meta e cookies web continuam abertos para todos.
"""
from __future__ import annotations

from models.models import User

INSTAGRAPI_AUTH_METHODS = frozenset({"password", "sessionid", "import"})


def can_use_instagrapi(user: User | None) -> bool:
    if user is None:
        return False
    if bool(getattr(user, "is_owner", False)):
        return True
    return bool(getattr(user, "allow_instagrapi", False))


def is_instagrapi_auth_method(method: str | None) -> bool:
    return (method or "").strip().lower() in INSTAGRAPI_AUTH_METHODS
