"""Limite de mídia (Reels + fotos feed) por usuário — economia de storage R2."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.utils.automation_videos import video_count
from models.models import Automation, User

# Teto global por usuário: soma de todas as playlists Reel + foto (todas automações).
MAX_USER_MEDIA_ITEMS = 150
MAX_REEL_VIDEOS_PER_USER = MAX_USER_MEDIA_ITEMS  # compat legado

MEDIA_QUOTA_CONTENT_TYPES = ("reel", "photo")


def count_user_stored_media(db: Session, user_id: int) -> int:
    autos = db.scalars(
        select(Automation).where(
            Automation.user_id == user_id,
            Automation.content_type.in_(MEDIA_QUOTA_CONTENT_TYPES),
        )
    ).all()
    return sum(video_count(a) for a in autos)


def count_user_reel_videos(db: Session, user_id: int) -> int:
    return count_user_stored_media(db, user_id)


def reel_video_quota(
    db: Session,
    user: User,
    *,
    adding: int = 0,
) -> dict:
    """Retorna used/limit/remaining e se `adding` cabe no limite."""
    used = count_user_stored_media(db, user.id)
    limit = MAX_USER_MEDIA_ITEMS
    remaining = max(0, limit - used)
    ok = int(adding or 0) <= remaining
    return {
        "ok": ok,
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "adding": int(adding or 0),
    }


def reel_video_quota_error(quota: dict) -> str:
    used = quota.get("used", 0)
    limit = quota.get("limit", MAX_USER_MEDIA_ITEMS)
    remaining = quota.get("remaining", 0)
    adding = quota.get("adding", 0)
    if remaining <= 0:
        return (
            f"Limite de {limit} vídeos e fotos por conta atingido "
            f"({used}/{limit}). Apague mídias ou automações antigas para liberar espaço no armazenamento."
        )
    return (
        f"Só cabem mais {remaining} mídia(s) no seu limite de {limit} "
        f"(já usa {used}). Você tentou enviar {adding}."
    )
