"""Conexão com o banco (SQLAlchemy 2.x) compartilhada entre FastAPI e Celery."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

log = logging.getLogger(__name__)


def _is_already_exists(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "already exists" in msg or "duplicate" in msg


def _engine_kwargs() -> dict:
    if settings.is_sqlite:
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True, "pool_size": 10, "max_overflow": 20}


engine = create_engine(settings.database_url, future=True, **_engine_kwargs())

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """Dependency do FastAPI."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sess\u00e3o transacional para uso fora do FastAPI (ex.: tasks Celery)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
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
        if "calendar_days" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN calendar_days TEXT"))
        if "calendar_time" not in cols:
            conn.execute(text("ALTER TABLE automations ADD COLUMN calendar_time VARCHAR(8)"))
        if "users" in insp.get_table_names():
            ucols = {c["name"] for c in insp.get_columns("users")}
            if "display_name" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN display_name VARCHAR(255)"))
            if "avatar_key" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN avatar_key VARCHAR(512)"))
            if "is_admin" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0"))
            conn.execute(text("UPDATE users SET is_admin = 1 WHERE username = 'admin'"))


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
            if "calendar_days" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN calendar_days TEXT"))
            if "calendar_time" not in cols:
                conn.execute(text("ALTER TABLE automations ADD COLUMN calendar_time VARCHAR(8)"))
        if "users" in tables:
            ucols = {c["name"] for c in insp.get_columns("users")}
            if "display_name" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN display_name VARCHAR(255)"))
            if "avatar_key" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN avatar_key VARCHAR(512)"))
            if "is_admin" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE"))
            conn.execute(text("UPDATE users SET is_admin = TRUE WHERE username = 'admin' AND is_admin IS NOT TRUE"))


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
