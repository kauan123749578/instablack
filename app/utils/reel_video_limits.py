"""Limite de vídeos Reels por usuário (economia de storage R2)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.utils.automation_videos import video_count
from models.models import Automation, User

# Teto global por usuário em playlists de Reels (todas as automações somadas).
MAX_REEL_VIDEOS_PER_USER = 150


def count_user_reel_videos(db: Session, user_id: int) -> int:
    autos = db.scalars(
        select(Automation).where(
            Automation.user_id == user_id,
            Automation.content_type == "reel",
        )
    ).all()
    return sum(video_count(a) for a in autos)


def reel_video_quota(
    db: Session,
    user: User,
    *,
    adding: int = 0,
) -> dict:
    """Retorna used/limit/remaining e se `adding` cabe no limite."""
    used = count_user_reel_videos(db, user.id)
    limit = MAX_REEL_VIDEOS_PER_USER
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
    limit = quota.get("limit", MAX_REEL_VIDEOS_PER_USER)
    remaining = quota.get("remaining", 0)
    adding = quota.get("adding", 0)
    if remaining <= 0:
        return (
            f"Limite de {limit} vídeos Reels por conta atingido "
            f"({used}/{limit}). Apague vídeos ou automações antigas para liberar espaço no armazenamento."
        )
    return (
        f"Só cabem mais {remaining} vídeo(s) no seu limite de {limit} "
        f"(já usa {used}). Você tentou enviar {adding}."
    )
