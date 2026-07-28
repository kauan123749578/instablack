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


def _is_celery_process() -> bool:
    """Celery prefork não deve usar QueuePool grande (4 procs × 15 = 60 conns)."""
    import sys

    return any("celery" in (arg or "").lower() for arg in sys.argv)


def _behind_pgbouncer() -> bool:
    """True quando DATABASE_URL passa pelo PgBouncer (Railway injeta UNPOOLED)."""
    pooled = (settings.database_url or "").strip()
    direct = (settings.database_unpooled_url or "").strip()
    if direct and pooled and direct != pooled:
        return True
    lower = pooled.lower()
    return "pgbouncer" in lower or ":6432" in lower


def _engine_kwargs() -> dict:
    if settings.is_sqlite:
        return {"connect_args": {"check_same_thread": False}}
    # connect_timeout evita hang infinito se o Postgres estiver inacessível.
    #
    # Celery / atrás do PgBouncer (transaction mode): NullPool.
    # QueuePool + PgBouncer gera "unexpected EOF on client connection with an
    # open transaction" e 502 no proxy quando a conexão é reciclada errado.
    from sqlalchemy.pool import NullPool

    if _is_celery_process() or _behind_pgbouncer():
        return {
            "poolclass": NullPool,
            "connect_args": {"connect_timeout": 8},
        }
    return {
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 5,
        "pool_timeout": 3,
        "pool_recycle": 120,
        "pool_use_lifo": True,
        "pool_reset_on_return": "rollback",
        "connect_args": {"connect_timeout": 5},
    }


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
        try:
            db.rollback()
        except Exception:
            pass
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sessão transacional para uso fora do FastAPI (ex.: tasks Celery)."""
    db = SessionLocal()
    try:
        yield db
        _commit_with_retry(db)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise
    finally:
        db.close()


def _sqlite_migrate(bind=None) -> None:
    """Adiciona colunas novas em SQLite sem Alembic."""
    if not settings.is_sqlite:
        return
    db_engine = bind or engine
    insp = inspect(db_engine)
    if "automations" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("automations")}
    with db_engine.begin() as conn:
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
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_automations_user_status "
                "ON automations (user_id, status)"
            )
        )
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
            if "logs_cleared_at" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN logs_cleared_at DATETIME"))
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
            if "caption_ok" not in pcols:
                conn.execute(text("ALTER TABLE publish_logs ADD COLUMN caption_ok BOOLEAN"))
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


def _postgres_migrate(bind=None) -> None:
    """Adiciona colunas novas em Postgres sem Alembic.

    Usa ADD COLUMN IF NOT EXISTS (leve e seguro sob corrida web/worker/beat)
    em vez de inspect.get_columns(), que no catálogo do PG pode demorar/travar.
    """
    if settings.is_sqlite:
        return
    db_engine = bind or engine

    def _table_exists(conn, name: str) -> bool:
        return bool(
            conn.execute(
                text("SELECT to_regclass(:reg)"),
                {"reg": f"public.{name}"},
            ).scalar()
        )

    def _add_columns(conn, table: str, columns: list[tuple[str, str]]) -> None:
        if not _table_exists(conn, table):
            return
        for col, ddl in columns:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {ddl}"))

    with db_engine.begin() as conn:
        _add_columns(
            conn,
            "automations",
            [
                ("content_type", "VARCHAR(16) DEFAULT 'reel'"),
                ("schedule_type", "VARCHAR(16) DEFAULT 'interval'"),
                ("start_mode", "VARCHAR(16) DEFAULT 'recurring'"),
                ("calendar_days", "TEXT"),
                ("calendar_time", "VARCHAR(8)"),
                ("story_link", "VARCHAR(512)"),
                ("story_sticker_text", "VARCHAR(64)"),
                ("story_layout_json", "TEXT"),
                ("videos_json", "TEXT"),
                ("captions_json", "TEXT"),
                ("caption_rotate_by_account", "BOOLEAN DEFAULT TRUE"),
                ("caption_rotate_by_reel", "BOOLEAN DEFAULT FALSE"),
                ("current_index", "INTEGER DEFAULT 0"),
                ("jitter_enabled", "BOOLEAN DEFAULT FALSE"),
                ("jitter_minutes", "INTEGER DEFAULT 10"),
                ("stagger_enabled", "BOOLEAN DEFAULT TRUE"),
                ("stagger_min_minutes", "INTEGER DEFAULT 2"),
                ("stagger_max_minutes", "INTEGER DEFAULT 8"),
                ("camouflage_cover_key", "VARCHAR(512)"),
                ("camouflage_opacity", "DOUBLE PRECISION DEFAULT 0.10"),
                ("posts_per_batch", "INTEGER DEFAULT 0"),
                ("rest_minutes", "INTEGER DEFAULT 0"),
                ("posts_in_batch", "INTEGER DEFAULT 0"),
            ],
        )
        if _table_exists(conn, "automations"):
            try:
                conn.execute(text("ALTER TABLE automations ALTER COLUMN calendar_time TYPE TEXT"))
            except Exception:
                pass
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_automations_user_status "
                    "ON automations (user_id, status)"
                )
            )

        _add_columns(
            conn,
            "users",
            [
                ("display_name", "VARCHAR(255)"),
                ("avatar_key", "VARCHAR(512)"),
                ("is_admin", "BOOLEAN DEFAULT FALSE"),
                ("is_owner", "BOOLEAN DEFAULT FALSE"),
                ("owner_private", "BOOLEAN DEFAULT FALSE"),
                ("account_limit", "INTEGER"),
                ("notification_prefs_json", "TEXT"),
                ("anti_farm_prefs_json", "TEXT"),
                ("logs_cleared_at", "TIMESTAMPTZ"),
            ],
        )
        if _table_exists(conn, "users"):
            conn.execute(
                text(
                    "UPDATE users SET is_admin = TRUE "
                    "WHERE username = 'admin' AND is_admin IS NOT TRUE"
                )
            )

        _add_columns(
            conn,
            "instagram_accounts",
            [
                ("provider", "VARCHAR(24) DEFAULT 'instagrapi'"),
                ("meta_ig_user_id", "VARCHAR(64)"),
                ("encrypted_meta_access_token", "TEXT"),
                ("meta_token_expires_at", "TIMESTAMPTZ"),
                ("last_health_check_at", "TIMESTAMPTZ"),
                ("proxy_ip", "VARCHAR(45)"),
                ("proxy_geo", "VARCHAR(64)"),
                ("encrypted_web_cookies", "TEXT"),
                ("user_meta_app_id", "INTEGER"),
                ("followers_count", "INTEGER"),
                ("followers_updated_at", "TIMESTAMPTZ"),
                ("warmup_enabled", "BOOLEAN DEFAULT FALSE"),
                ("warmup_days", "INTEGER DEFAULT 7"),
                ("warmup_started_at", "TIMESTAMPTZ"),
            ],
        )
        if _table_exists(conn, "instagram_accounts"):
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_instagram_accounts_user_status "
                    "ON instagram_accounts (user_id, status)"
                )
            )

        _add_columns(
            conn,
            "publish_logs",
            [
                ("play_count", "INTEGER"),
                ("like_count", "INTEGER"),
                ("insights_fetched_at", "TIMESTAMPTZ"),
                ("content_type", "VARCHAR(16)"),
                ("video_key", "VARCHAR(512)"),
                ("metadata_fingerprint", "VARCHAR(64)"),
                ("raw_sha256", "VARCHAR(64)"),
                ("clean_sha256", "VARCHAR(64)"),
                ("clean_size", "INTEGER"),
                ("caption_ok", "BOOLEAN"),
            ],
        )
        if _table_exists(conn, "publish_logs"):
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
                    "CREATE INDEX IF NOT EXISTS ix_publish_logs_account_created_status "
                    "ON publish_logs (account_id, created_at, status)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_publish_logs_status_created "
                    "ON publish_logs (status, created_at)"
                )
            )

        _add_columns(
            conn,
            "app_notifications",
            [("publish_log_id", "INTEGER")],
        )
        _add_columns(
            conn,
            "warmup_jobs",
            [
                ("duration_minutes", "INTEGER DEFAULT 60"),
                ("ends_at", "TIMESTAMPTZ"),
            ],
        )

def init_db() -> None:
    """Cria todas as tabelas (uso simples, sem Alembic).

    Idempotente e tolerante a corrida entre múltiplos workers/services que
    sobem ao mesmo tempo (ex.: gunicorn --workers 2, web + worker + beat).
    No Postgres, só um processo migra por vez (advisory lock); os outros
    pulam e sobem na hora — evita painel/worker travados 60s no boot.

    Com PgBouncer em transaction mode, advisory locks / DDL usam
    DATABASE_UNPOOLED_URL (conexão direta no Postgres).
    """
    from models import models  # noqa: F401
    from core.bootstrap import bootstrap_admin

    migrate_engine = engine
    own_migrate_engine = False

    if not settings.is_sqlite:
        direct = settings.direct_database_url
        if direct and direct != settings.database_url:
            from sqlalchemy.pool import NullPool

            migrate_engine = create_engine(
                direct,
                future=True,
                poolclass=NullPool,
                connect_args={"connect_timeout": 10},
            )
            own_migrate_engine = True
            log.info("init_db: migrando via DATABASE_UNPOOLED_URL (web segue no PgBouncer)")

    lock_conn = None
    lock_held = False
    try:
        # Advisory lock é session-scoped — NÃO funciona via PgBouncer transaction.
        can_lock = own_migrate_engine or not _behind_pgbouncer()
        if not settings.is_sqlite and can_lock:
            lock_conn = migrate_engine.connect()
            try:
                got = lock_conn.execute(
                    text("SELECT pg_try_advisory_lock(87423101)")
                ).scalar()
                lock_conn.commit()
                if not got:
                    log.info("init_db: outro processo já migra; skip neste worker")
                    return
                lock_held = True
            except Exception:
                log.exception("init_db: falha ao obter advisory lock; seguindo sem lock")
                try:
                    lock_conn.close()
                except Exception:
                    pass
                lock_conn = None
        elif not settings.is_sqlite and not can_lock:
            log.warning(
                "init_db: PgBouncer sem DATABASE_UNPOOLED_URL — migrando sem advisory lock"
            )

        try:
            Base.metadata.create_all(bind=migrate_engine, checkfirst=True)
        except (OperationalError, ProgrammingError) as exc:
            if _is_already_exists(exc):
                log.info("Tabelas já existem (corrida entre workers); seguindo.")
            else:
                raise

        try:
            _sqlite_migrate(migrate_engine)
            _postgres_migrate(migrate_engine)
        except (OperationalError, ProgrammingError) as exc:
            if _is_already_exists(exc):
                log.info("Migração já aplicada por outro worker; seguindo.")
            else:
                raise

        try:
            bootstrap_admin()
        except Exception:
            log.exception("bootstrap_admin falhou; seguindo sem criar admin inicial.")
    finally:
        if lock_conn is not None:
            if lock_held:
                try:
                    lock_conn.execute(text("SELECT pg_advisory_unlock(87423101)"))
                    lock_conn.commit()
                except Exception:
                    log.exception("init_db: falha ao liberar advisory lock")
            try:
                lock_conn.close()
            except Exception:
                pass
        if own_migrate_engine:
            try:
                migrate_engine.dispose()
            except Exception:
                pass


def init_db_background() -> None:
    """Roda init_db fora do caminho crítico do boot (web sobe imediatamente)."""
    try:
        init_db()
    except Exception:
        log.exception("init_db em background falhou; tentará de novo no próximo boot")
