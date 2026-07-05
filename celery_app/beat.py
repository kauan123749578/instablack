"""Celery Beat tick: a cada N segundos varre automa\u00e7\u00f5es vencidas."""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.utils.calendar_schedule import next_calendar_run, parse_calendar_days
from celery_app.config import celery_app
from celery_app.tasks.publish import execute_automation
from core.database import session_scope
from models.models import Automation

log = logging.getLogger(__name__)


@celery_app.task(name="celery_app.beat.tick")
def tick() -> dict:
    """Encontra automa\u00e7\u00f5es ativas vencidas e despacha execute_automation."""
    now = dt.datetime.utcnow()
    dispatched: list[int] = []

    with session_scope() as db:
        due = db.scalars(
            select(Automation)
            .where(
                Automation.status == "active",
                Automation.next_run_at.is_not(None),
                Automation.next_run_at <= now,
            )
            .options(selectinload(Automation.accounts))
        ).all()

        for a in due:
            if a.schedule_type == "calendar" and a.calendar_days and a.calendar_time:
                nxt = next_calendar_run(
                    parse_calendar_days(a.calendar_days),
                    a.calendar_time,
                    now,
                )
                a.next_run_at = nxt or (now + dt.timedelta(days=1))
            else:
                interval = max(int(a.interval_minutes or 60), 1)
                hold = max(interval * 60, 90)
                a.next_run_at = now + dt.timedelta(seconds=hold)

        # commit antes de despachar para garantir que o estado est\u00e1 salvo
        db.flush()
        ids_to_dispatch = [a.id for a in due]

    for aid in ids_to_dispatch:
        execute_automation.delay(aid)
        dispatched.append(aid)

    log.info("tick: %d automa\u00e7\u00f5es disparadas", len(dispatched))
    return {"now": now.isoformat(), "dispatched": dispatched}
