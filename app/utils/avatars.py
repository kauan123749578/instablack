"""Helpers de avatar do usuário do painel."""
from __future__ import annotations

from app.media_access import signed_media_path
from models.models import User


def user_avatar_url(user: User) -> str | None:
    if user.avatar_key:
        return signed_media_path(user.avatar_key)
    return None


def user_display_name(user: User) -> str:
    return (user.display_name or user.username).strip()
