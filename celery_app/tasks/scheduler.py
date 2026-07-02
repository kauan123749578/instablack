"""Helpers chamados pelo FastAPI para mexer em automa\u00e7\u00f5es (atalho program\u00e1tico).

As rotas HTTP j\u00e1 fazem isso direto via SQLAlchemy; estes helpers existem para
uso em scripts/CLI/outras integra\u00e7\u00f5es futuras.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from core.database import session_scope
from models.models import Automation


def pause(automation_id: int) -> bool:
    with session_scope() as db:
        a = db.get(Automation, automation_id)
        if not a:
            return False
        a.status = "paused"
        return True


def resume(automation_id: int) -> bool:
    with session_scope() as db:
        a = db.get(Automation, automation_id)
        if not a:
            return False
        a.status = "active"
        if a.next_run_at is None:
            a.next_run_at = dt.datetime.utcnow()
        return True


def update_interval(automation_id: int, interval_minutes: int) -> bool:
    with session_scope() as db:
        a = db.get(Automation, automation_id)
        if not a:
            return False
        a.interval_minutes = interval_minutes
        a.next_run_at = dt.datetime.utcnow() + dt.timedelta(minutes=interval_minutes)
        return True


def start_now(automation_id: int) -> bool:
    """Marca a automa\u00e7\u00e3o para sair no pr\u00f3ximo tick."""
    with session_scope() as db:
        a = db.get(Automation, automation_id)
        if not a:
            return False
        a.status = "active"
        a.next_run_at = dt.datetime.utcnow()
        return True


def list_due(now: dt.datetime | None = None):
    now = now or dt.datetime.utcnow()
    with session_scope() as db:
        return db.scalars(
            select(Automation).where(
                Automation.status == "active",
                Automation.next_run_at.is_not(None),
                Automation.next_run_at <= now,
            )
        ).all()
