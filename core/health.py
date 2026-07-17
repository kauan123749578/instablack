"""Verificações de saúde para deploy (Railway healthcheck)."""
from __future__ import annotations

from pathlib import Path

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


def check_storage() -> tuple[bool, str]:
    """Verifica storage local ou R2/S3."""
    if settings.storage_backend == "local":
        base = Path(settings.local_storage_path)
        if not base.is_absolute():
            base = (settings.base_dir / base).resolve()
        try:
            base.mkdir(parents=True, exist_ok=True)
            test = base / ".write_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink(missing_ok=True)
            return True, f"local:{base}"
        except Exception as exc:
            return False, str(exc)[:200]

    missing = [
        name
        for name, val in (
            ("S3_BUCKET", settings.s3_bucket),
            ("S3_ACCESS_KEY_ID", settings.s3_access_key_id),
            ("S3_SECRET_ACCESS_KEY", settings.s3_secret_access_key),
            ("S3_ENDPOINT_URL", settings.s3_endpoint_url),
        )
        if not val
    ]
    if missing:
        return False, f"variáveis faltando: {', '.join(missing)}"

    try:
        from core.storage import DualS3Storage, get_storage

        storage = get_storage()
        ping = getattr(storage, "ping", None)
        if callable(ping):
            ping()
        if isinstance(storage, DualS3Storage):
            return True, f"s3:{settings.s3_bucket}+{settings.s3_bucket_2}"
        return True, f"s3:{settings.s3_bucket}"
    except Exception as exc:
        return False, str(exc)[:200]
