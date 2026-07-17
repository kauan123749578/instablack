"""Celery Beat tick: a cada N segundos varre automações vencidas."""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select, text

from app.utils.automation_schedule import compute_next_run_after_dispatch
from app.utils.calendar_schedule import next_calendar_run, parse_calendar_days
from celery_app.config import celery_app
from celery_app.tasks.publish import execute_automation
from core.database import session_scope
from models.models import Automation

log = logging.getLogger(__name__)


@celery_app.task(name="celery_app.beat.tick")
def tick() -> dict:
    """Encontra automações ativas vencidas e despacha execute_automation.

    Atualiza next_run_at / posts_in_batch só via SQL cru — nunca regrava current_index
    (evita corrida ORM apagar o CLAIM da playlist).
    """
    now = dt.datetime.utcnow()
    dispatched: list[int] = []

    with session_scope() as db:
        due = db.scalars(
            select(Automation).where(
                Automation.status == "active",
                Automation.next_run_at.is_not(None),
                Automation.next_run_at <= now,
            )
        ).all()

        ids_to_dispatch: list[int] = []
        for a in due:
            calendar_next = None
            if a.schedule_type == "calendar" and a.calendar_days and a.calendar_time:
                calendar_next = next_calendar_run(
                    parse_calendar_days(a.calendar_days),
                    a.calendar_time,
                    now,
                ) or (now + dt.timedelta(days=1))

            nxt, posts_in_batch = compute_next_run_after_dispatch(
                a,
                now,
                calendar_next=calendar_next,
            )

            db.execute(
                text(
                    "UPDATE automations SET next_run_at = :nxt, posts_in_batch = :pib WHERE id = :id"
                ),
                {"nxt": nxt, "pib": posts_in_batch, "id": a.id},
            )
            ids_to_dispatch.append(a.id)

    for aid in ids_to_dispatch:
        execute_automation.delay(aid)
        dispatched.append(aid)

    log.info("tick: %d automações disparadas", len(dispatched))
    return {"now": now.isoformat(), "dispatched": dispatched}
