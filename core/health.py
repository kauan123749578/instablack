"""Verificações de saúde para deploy (Railway healthcheck)."""
from __future__ import annotations

from sqlalchemy import text

from app.config import settings
from core.database import engine


def check_database() -> tuple[bool, str]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as exc:
        return False, str(exc)[:200]


def check_redis() -> tuple[bool, str]:
    try:
        import redis

        client = redis.from_url(settings.redis_url, socket_connect_timeout=3)
        client.ping()
        return True, "ok"
    except Exception as exc:
        return False, str(exc)[:200]
