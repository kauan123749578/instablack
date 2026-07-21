"""Configurações globais key-value (owner)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from models.models import PlatformSetting

META_SETUP_YOUTUBE_URL = "meta_setup_youtube_url"
META_TOKEN_YOUTUBE_URL = "meta_token_youtube_url"


def get_platform_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(PlatformSetting, key)
    if not row or not row.value:
        return default
    return row.value.strip()


def set_platform_setting(db: Session, key: str, value: str) -> None:
    cleaned = (value or "").strip()
    row = db.get(PlatformSetting, key)
    if row:
        row.value = cleaned
    else:
        db.add(PlatformSetting(key=key, value=cleaned))
    db.commit()
