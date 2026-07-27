"""Celery Beat tick: a cada N segundos varre automações vencidas."""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select, text

from app.utils.automation_schedule import compute_next_run_after_dispatch
from app.utils.calendar_schedule import next_calendar_run, parse_calendar_days
from app.utils.intervals import effective_meta_min_interval
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
    healed = 0
    # (automation_id, scheduled_at ISO) — scheduled_at = slot que disparou
    to_run: list[tuple[int, str | None]] = []

    with session_scope() as db:
        # Automações por intervalo ativas sem Próxima nunca disparam.
        # Calendário: cura para o próximo slot real (nunca "agora").
        stuck = db.scalars(
            select(Automation).where(
                Automation.status == "active",
                Automation.next_run_at.is_(None),
            )
        ).all()
        for a in stuck:
            mode = (a.start_mode or "").strip().lower()
            if mode == "now":
                continue
            if (
                (a.schedule_type or "") == "calendar"
                and a.calendar_days
                and a.calendar_time
            ):
                nxt = next_calendar_run(
                    parse_calendar_days(a.calendar_days),
                    a.calendar_time,
                    now,
                ) or (now + dt.timedelta(days=1))
                db.execute(
                    text("UPDATE automations SET next_run_at = :nxt WHERE id = :id"),
                    {"nxt": nxt, "id": a.id},
                )
                healed += 1
                log.warning(
                    "tick heal: calendar automation=%s sem next_run_at — próximo slot %s",
                    a.id,
                    nxt.isoformat(),
                )
                continue
            db.execute(
                text("UPDATE automations SET next_run_at = :nxt WHERE id = :id"),
                {"nxt": now, "id": a.id},
            )
            healed += 1
            log.warning(
                "tick heal: automation=%s sem next_run_at — reagendada agora (mode=%s)",
                a.id,
                mode or a.schedule_type or "recurring",
            )

        due = db.scalars(
            select(Automation).where(
                Automation.status == "active",
                Automation.next_run_at.is_not(None),
                Automation.next_run_at <= now,
            )
        ).all()

        for a in due:
            scheduled_at = a.next_run_at
            calendar_next = None
            if a.schedule_type == "calendar" and a.calendar_days and a.calendar_time:
                calendar_next = next_calendar_run(
                    parse_calendar_days(a.calendar_days),
                    a.calendar_time,
                    now,
                ) or (now + dt.timedelta(days=1))

            meta_floor = 0
            if (a.schedule_type or "") != "calendar":
                meta_floor = effective_meta_min_interval(a.accounts or [])
            nxt, posts_in_batch = compute_next_run_after_dispatch(
                a,
                now,
                calendar_next=calendar_next,
                min_gap_minutes=meta_floor,
            )

            # Após redeploy/downtime: NÃO “recuperar” slots atrasados (vira spam).
            # Só reagenda o próximo e pula o disparo se estiver muito atrasado.
            skip_catchup = False
            if scheduled_at is not None and (a.schedule_type or "") != "calendar":
                interval_m = max(int(a.interval_minutes or 60), 1)
                if meta_floor > 0:
                    interval_m = max(interval_m, meta_floor)
                overdue_sec = (now - scheduled_at).total_seconds()
                # Limite: 1.5× intervalo, mínimo 20 min / máximo 3 h
                max_overdue = min(max(interval_m * 90, 20 * 60), 3 * 3600)
                if overdue_sec > max_overdue:
                    skip_catchup = True
                    log.warning(
                        "tick skip catch-up automation=%s overdue=%.0fs (limite=%ss) — só reagenda",
                        a.id,
                        overdue_sec,
                        max_overdue,
                    )

            db.execute(
                text(
                    "UPDATE automations SET next_run_at = :nxt, posts_in_batch = :pib WHERE id = :id"
                ),
                {"nxt": nxt, "pib": posts_in_batch, "id": a.id},
            )
            if skip_catchup:
                continue
            to_run.append(
                (
                    a.id,
                    scheduled_at.isoformat() if scheduled_at is not None else None,
                )
            )

    for aid, scheduled_iso in to_run:
        execute_automation.delay(aid, scheduled_iso)
        dispatched.append(aid)

    log.info("tick: %d automações disparadas heal=%d", len(dispatched), healed)
    return {"now": now.isoformat(), "dispatched": dispatched, "healed": healed}
