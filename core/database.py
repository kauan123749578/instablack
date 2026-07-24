"""Conexão com o banco (SQLAlchemy 2.x) compartilhada entre FastAPI e Celery."""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

log = logging.getLogger(__name__)

_COMMIT_RETRIES = 5
_COMMIT_RETRY_BASE_SEC = 0.15


def _is_locked_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _is_already_exists(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "already exists" in msg or "duplicate" in msg


def _engine_kwargs() -> dict:
    if settings.is_sqlite:
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True, "pool_size": 10, "max_overflow": 20}


engine = create_engine(settings.database_url, future=True, **_engine_kwargs())


if settings.is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _connection_record) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


class Base(DeclarativeBase):
    pass


def _commit_with_retry(db: Session) -> None:
    for attempt in range(_COMMIT_RETRIES):
        try:
            db.commit()
            return
        except OperationalError as exc:
            db.rollback()
            if settings.is_sqlite and _is_locked_error(exc) and attempt < _COMMIT_RETRIES - 1:
                time.sleep(_COMMIT_RETRY_BASE_SEC * (attempt + 1))
                continue
            raise


def get_db() -> Iterator[Session]:
    """Dependency do FastAPI."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sessão transacional para uso fora do FastAPI (ex.: tasks Celery)."""
    db = SessionLocal()
    try:
        yield db
        _commit_with_retry(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _sqlite_migrate() -> None:
    """Adiciona colunas novas em SQLite sem Alembic."""
    if not settings.is_sqlite:
        return
    insp = inspect(engine)
    if "automations" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("automations")}
    with engine.begin() as conn:
        if "content_type" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN content_type VARCHAR(16) DEFAULT 'reel'"))
        if "schedule_type" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN schedule_type VARCHAR(16) DEFAULT 'interval'"))
        if "start_mode" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN start_mode VARCHAR(16) DEFAULT 'recurring'"))
        if "calendar_days" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN calendar_days TEXT"))
        if "calendar_time" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN calendar_time VARCHAR(8)"))
        if "story_link" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN story_link VARCHAR(512)"))
        if "story_sticker_text" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN story_sticker_text VARCHAR(64)"))
        if "story_layout_json" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN story_layout_json TEXT"))
        if "videos_json" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN videos_json TEXT"))
        if "captions_json" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN captions_json TEXT"))
        if "caption_rotate_by_account" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN caption_rotate_by_account BOOLEAN DEFAULT 1"))
        if "caption_rotate_by_reel" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN caption_rotate_by_reel BOOLEAN DEFAULT 0"))
        if "current_index" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN current_index INTEGER DEFAULT 0"))
        if "jitter_enabled" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN jitter_enabled BOOLEAN DEFAULT 0"))
        if "jitter_minutes" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN jitter_minutes INTEGER DEFAULT 10"))
        if "stagger_enabled" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN stagger_enabled BOOLEAN DEFAULT 1"))
        if "stagger_min_minutes" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN stagger_min_minutes INTEGER DEFAULT 2"))
        if "stagger_max_minutes" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN stagger_max_minutes INTEGER DEFAULT 8"))
        if "camouflage_cover_key" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN camouflage_cover_key VARCHAR(512)"))
        if "camouflage_opacity" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN camouflage_opacity REAL DEFAULT 0.10"))
        if "posts_per_batch" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN posts_per_batch INTEGER DEFAULT 0"))
        if "rest_minutes" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN rest_minutes INTEGER DEFAULT 0"))
        if "posts_in_batch" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN posts_in_batch INTEGER DEFAULT 0"))
        if "users" in insp.get_table_names():
            ucols = {c["name"] for c in insp.get_columns("users")}
            if "display_name" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN display_name VARCHAR(255)"))
            if "avatar_key" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN avatar_key VARCHAR(512)"))
            if "is_admin" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0"))
            if "is_owner" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN is_owner BOOLEAN DEFAULT 0"))
            if "owner_private" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN owner_private BOOLEAN DEFAULT 0"))
            if "account_limit" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN account_limit INTEGER"))
            if "notification_prefs_json" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN notification_prefs_json TEXT"))
            if "anti_farm_prefs_json" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN anti_farm_prefs_json TEXT"))
            conn.execute(text("UPDATE users SET is_admin = 1 WHERE username = 'admin'"))
        if "instagram_accounts" in insp.get_table_names():
            acols = {c["name"] for c in insp.get_columns("instagram_accounts")}
            if "provider" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN provider VARCHAR(24) DEFAULT 'instagrapi'"))
            if "meta_ig_user_id" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN meta_ig_user_id VARCHAR(64)"))
            if "encrypted_meta_access_token" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN encrypted_meta_access_token TEXT"))
            if "meta_token_expires_at" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN meta_token_expires_at DATETIME"))
            if "last_health_check_at" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN last_health_check_at DATETIME"))
            if "proxy_ip" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN proxy_ip VARCHAR(45)"))
            if "proxy_geo" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN proxy_geo VARCHAR(64)"))
            if "encrypted_web_cookies" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN encrypted_web_cookies TEXT"))
            if "user_meta_app_id" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN user_meta_app_id INTEGER"))
            if "followers_count" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN followers_count INTEGER"))
            if "followers_updated_at" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN followers_updated_at DATETIME"))
            if "warmup_enabled" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN warmup_enabled BOOLEAN DEFAULT 0"))
            if "warmup_days" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN warmup_days INTEGER DEFAULT 7"))
            if "warmup_started_at" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN warmup_started_at DATETIME"))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_instagram_accounts_user_status "
                    "ON instagram_accounts (user_id, status)"
                )
            )
        if "publish_logs" in insp.get_table_names():
            pcols = {c["name"] for c in insp.get_columns("publish_logs")}
            if "play_count" not in pcols:
                conn.execute(text("ALTER TABLE publish_logs ADD COLUMN play_count INTEGER"))
            if "like_count" not in pcols:
                conn.execute(text("ALTER TABLE publish_logs ADD COLUMN like_count INTEGER"))
            if "insights_fetched_at" not in pcols:
                conn.execute(text("ALTER TABLE publish_logs ADD COLUMN insights_fetched_at DATETIME"))
            if "content_type" not in pcols:
                conn.execute(text("ALTER TABLE publish_logs ADD COLUMN content_type VARCHAR(16)"))
            if "video_key" not in pcols:
                conn.execute(text("ALTER TABLE publish_logs ADD COLUMN video_key VARCHAR(512)"))
            if "metadata_fingerprint" not in pcols:
                conn.execute(text("ALTER TABLE publish_logs ADD COLUMN metadata_fingerprint VARCHAR(64)"))
            if "raw_sha256" not in pcols:
                conn.execute(text("ALTER TABLE publish_logs ADD COLUMN raw_sha256 VARCHAR(64)"))
            if "clean_sha256" not in pcols:
                conn.execute(text("ALTER TABLE publish_logs ADD COLUMN clean_sha256 VARCHAR(64)"))
            if "clean_size" not in pcols:
                conn.execute(text("ALTER TABLE publish_logs ADD COLUMN clean_size INTEGER"))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_publish_logs_account_created "
                    "ON publish_logs (account_id, created_at)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_publish_logs_account_status "
                    "ON publish_logs (account_id, status)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_publish_logs_status_created "
                    "ON publish_logs (status, created_at)"
                )
            )
        if "app_notifications" in insp.get_table_names():
            ncols = {c["name"] for c in insp.get_columns("app_notifications")}
            if "publish_log_id" not in ncols:
                conn.execute(text("ALTER TABLE app_notifications ADD COLUMN publish_log_id INTEGER"))
        if "warmup_jobs" in insp.get_table_names():
            wcols = {c["name"] for c in insp.get_columns("warmup_jobs")}
            if "duration_minutes" not in wcols:
                conn.execute(text("ALTER TABLE warmup_jobs ADD COLUMN duration_minutes INTEGER DEFAULT 60"))
            if "ends_at" not in wcols:
                conn.execute(text("ALTER TABLE warmup_jobs ADD COLUMN ends_at DATETIME"))


def _postgres_migrate() -> None:
    """Adiciona colunas novas em Postgres sem Alembic."""
    if settings.is_sqlite:
        return
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        if "automations" in tables:
            cols = {c["name"] for c in insp.get_columns("automations")}
            if "content_type" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN content_type VARCHAR(16) DEFAULT 'reel'"))
            if "schedule_type" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN schedule_type VARCHAR(16) DEFAULT 'interval'"))
            if "start_mode" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN start_mode VARCHAR(16) DEFAULT 'recurring'"))
            if "calendar_days" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN calendar_days TEXT"))
            if "calendar_time" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN calendar_time VARCHAR(8)"))
            if "story_link" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN story_link VARCHAR(512)"))
            if "story_sticker_text" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN story_sticker_text VARCHAR(64)"))
            if "story_layout_json" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN story_layout_json TEXT"))
            if "videos_json" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN videos_json TEXT"))
            if "captions_json" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN captions_json TEXT"))
            if "caption_rotate_by_account" not in cols:
                conn.execute(text(
                    "ALTER TABLE automations ADD COLUMN caption_rotate_by_account BOOLEAN DEFAULT TRUE"
                ))
            if "caption_rotate_by_reel" not in cols:
                conn.execute(text(
                    "ALTER TABLE automations ADD COLUMN caption_rotate_by_reel BOOLEAN DEFAULT FALSE"
                ))
            if "current_index" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN current_index INTEGER DEFAULT 0"))
            if "jitter_enabled" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN jitter_enabled BOOLEAN DEFAULT FALSE"))
            if "jitter_minutes" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN jitter_minutes INTEGER DEFAULT 10"))
            if "stagger_enabled" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN stagger_enabled BOOLEAN DEFAULT TRUE"))
            if "stagger_min_minutes" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN stagger_min_minutes INTEGER DEFAULT 2"))
            if "stagger_max_minutes" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN stagger_max_minutes INTEGER DEFAULT 8"))
            if "camouflage_cover_key" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN camouflage_cover_key VARCHAR(512)"))
            if "camouflage_opacity" not in cols:
                conn.execute(text(
                    "ALTER TABLE automations ADD COLUMN camouflage_opacity DOUBLE PRECISION DEFAULT 0.10"
                ))
            if "posts_per_batch" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN posts_per_batch INTEGER DEFAULT 0"))
            if "rest_minutes" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN rest_minutes INTEGER DEFAULT 0"))
            if "posts_in_batch" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN posts_in_batch INTEGER DEFAULT 0"))
            try:
                conn.execute(text(
                    "ALTER TABLE automations ALTER COLUMN calendar_time TYPE TEXT"
                ))
            except Exception:
                pass
        if "users" in tables:
            ucols = {c["name"] for c in insp.get_columns("users")}
            if "display_name" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN display_name VARCHAR(255)"))
            if "avatar_key" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN avatar_key VARCHAR(512)"))
            if "is_admin" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE"))
            if "is_owner" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN is_owner BOOLEAN DEFAULT FALSE"))
            if "owner_private" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN owner_private BOOLEAN DEFAULT FALSE"))
            if "account_limit" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN account_limit INTEGER"))
            if "notification_prefs_json" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN notification_prefs_json TEXT"))
            if "anti_farm_prefs_json" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN anti_farm_prefs_json TEXT"))
            conn.execute(text("UPDATE users SET is_admin = TRUE WHERE username = 'admin' AND is_admin IS NOT TRUE"))
        if "instagram_accounts" in tables:
            acols = {c["name"] for c in insp.get_columns("instagram_accounts")}
            if "provider" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN provider VARCHAR(24) DEFAULT 'instagrapi'"))
            if "meta_ig_user_id" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN meta_ig_user_id VARCHAR(64)"))
            if "encrypted_meta_access_token" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN encrypted_meta_access_token TEXT"))
            if "meta_token_expires_at" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN meta_token_expires_at TIMESTAMPTZ"))
            if "last_health_check_at" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN last_health_check_at TIMESTAMPTZ"))
            if "proxy_ip" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN proxy_ip VARCHAR(45)"))
            if "proxy_geo" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN proxy_geo VARCHAR(64)"))
            if "encrypted_web_cookies" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN encrypted_web_cookies TEXT"))
            if "user_meta_app_id" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN user_meta_app_id INTEGER"))
            if "followers_count" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN followers_count INTEGER"))
            if "followers_updated_at" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN followers_updated_at TIMESTAMPTZ"))
            if "warmup_enabled" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN warmup_enabled BOOLEAN DEFAULT FALSE"))
            if "warmup_days" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN warmup_days INTEGER DEFAULT 7"))
            if "warmup_started_at" not in acols:
                conn.execute(text("ALTER TABLE instagram_accounts ADD COLUMN warmup_started_at TIMESTAMPTZ"))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_instagram_accounts_user_status "
                    "ON instagram_accounts (user_id, status)"
                )
            )
        if "publish_logs" in tables:
            pcols = {c["name"] for c in insp.get_columns("publish_logs")}
            if "play_count" not in pcols:
                conn.execute(text("ALTER TABLE publish_logs ADD COLUMN play_count INTEGER"))
            if "like_count" not in pcols:
                conn.execute(text("ALTER TABLE publish_logs ADD COLUMN like_count INTEGER"))
            if "insights_fetched_at" not in pcols:
                conn.execute(text("ALTER TABLE publish_logs ADD COLUMN insights_fetched_at TIMESTAMPTZ"))
            if "content_type" not in pcols:
                conn.execute(text("ALTER TABLE publish_logs ADD COLUMN content_type VARCHAR(16)"))
            if "video_key" not in pcols:
                conn.execute(text("ALTER TABLE publish_logs ADD COLUMN video_key VARCHAR(512)"))
            if "metadata_fingerprint" not in pcols:
                conn.execute(text("ALTER TABLE publish_logs ADD COLUMN metadata_fingerprint VARCHAR(64)"))
            if "raw_sha256" not in pcols:
                conn.execute(text("ALTER TABLE publish_logs ADD COLUMN raw_sha256 VARCHAR(64)"))
            if "clean_sha256" not in pcols:
                conn.execute(text("ALTER TABLE publish_logs ADD COLUMN clean_sha256 VARCHAR(64)"))
            if "clean_size" not in pcols:
                conn.execute(text("ALTER TABLE publish_logs ADD COLUMN clean_size INTEGER"))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_publish_logs_account_created "
                    "ON publish_logs (account_id, created_at)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_publish_logs_account_status "
                    "ON publish_logs (account_id, status)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_publish_logs_status_created "
                    "ON publish_logs (status, created_at)"
                )
            )
        if "app_notifications" in tables:
            ncols = {c["name"] for c in insp.get_columns("app_notifications")}
            if "publish_log_id" not in ncols:
                conn.execute(text("ALTER TABLE app_notifications ADD COLUMN publish_log_id INTEGER"))
        if "warmup_jobs" in tables:
            wcols = {c["name"] for c in insp.get_columns("warmup_jobs")}
            if "duration_minutes" not in wcols:
                conn.execute(text("ALTER TABLE warmup_jobs ADD COLUMN duration_minutes INTEGER DEFAULT 60"))
            if "ends_at" not in wcols:
                conn.execute(text("ALTER TABLE warmup_jobs ADD COLUMN ends_at TIMESTAMPTZ"))


def init_db() -> None:
    """Cria todas as tabelas (uso simples, sem Alembic).

    Idempotente e tolerante a corrida entre múltiplos workers/services que
    sobem ao mesmo tempo (ex.: gunicorn --workers 2, web + worker + beat).
    """
    from models import models  # noqa: F401
    from core.bootstrap import bootstrap_admin

    try:
        Base.metadata.create_all(bind=engine, checkfirst=True)
    except (OperationalError, ProgrammingError) as exc:
        if _is_already_exists(exc):
            log.info("Tabelas já existem (corrida entre workers); seguindo.")
        else:
            raise

    try:
        _sqlite_migrate()
        _postgres_migrate()
    except (OperationalError, ProgrammingError) as exc:
        if _is_already_exists(exc):
            log.info("Migração já aplicada por outro worker; seguindo.")
        else:
            raise

    try:
        bootstrap_admin()
    except Exception:
        log.exception("bootstrap_admin falhou; seguindo sem criar admin inicial.")
