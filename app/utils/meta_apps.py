"""Helpers para apps Meta cadastrados por usuário."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.security import decrypt_secret
from core.meta_instagram import MetaAppCredentials, meta_app_urls
from models.models import UserMetaApp


def mask_ig_secret(secret: str) -> str:
    secret = (secret or "").strip()
    if len(secret) <= 8:
        return "••••"
    return f"{secret[:4]}••••{secret[-3:]}"


def credentials_from_app(app: UserMetaApp) -> MetaAppCredentials:
    return MetaAppCredentials(
        ig_app_id=app.ig_app_id.strip(),
        ig_app_secret=decrypt_secret(app.encrypted_ig_app_secret),
        redirect_uri=meta_app_urls(app.id)["callback"],
    )


def get_owned_meta_app(db: Session, user_id: int, app_id: int) -> UserMetaApp | None:
    app = db.get(UserMetaApp, app_id)
    if not app or app.user_id != user_id:
        return None
    return app


def list_user_meta_apps(db: Session, user_id: int) -> list[UserMetaApp]:
    return list(
        db.scalars(
            select(UserMetaApp)
            .where(UserMetaApp.user_id == user_id)
            .order_by(UserMetaApp.name.asc(), UserMetaApp.id.asc())
        ).all()
    )
