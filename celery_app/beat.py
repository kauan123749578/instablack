"""Celery Beat tick: a cada N segundos varre automações vencidas."""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import exists, func, select, text

from app.config import settings
from app.utils.automation_schedule import compute_next_run_after_dispatch
from app.utils.calendar_schedule import next_calendar_run, parse_calendar_days
from app.utils.intervals import META_MIN_INTERVAL
from celery_app.config import celery_app
from celery_app.tasks.publish import execute_automation
from core.database import session_scope
from models.models import Automation, InstagramAccount, automation_accounts

log = logging.getLogger(__name__)


def _as_naive_utc(value: dt.datetime | None) -> dt.datetime | None:
    """Postgres TIMESTAMPTZ vem aware; o tick usa utcnow() naive — unifica."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return value


@celery_app.task(name="celery_app.beat.tick", bind=True, expires=55)
def tick(self) -> dict:
    """Encontra automações ativas vencidas e despacha execute_automation.

    Atualiza next_run_at / posts_in_batch só via SQL cru — nunca regrava current_index
    (evita corrida ORM apagar o CLAIM da playlist).

    Redis lock: se o tick anterior ainda roda, o próximo NÃO empilha (suspeita #3).
    """
    client = None
    try:
        from redis import Redis

        client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        if not client.set("celery:tick:lock", "1", nx=True, ex=55):
            log.info("tick: skip — tick anterior ainda ativo")
            return {"skipped": True, "reason": "tick_lock"}
    except Exception as exc:
        log.warning("tick lock Redis falhou — seguindo sem lock: %s", exc)
        client = None

    now = dt.datetime.utcnow()
    dispatched: list[int] = []
    healed = 0
    # (automation_id, scheduled_at ISO) — scheduled_at = slot que disparou
    to_run: list[tuple[int, str | None]] = []

    try:
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

            max_dispatch = max(1, int(getattr(settings, "beat_tick_max_dispatch", 150) or 150))
            due = db.scalars(
                select(Automation)
                .where(
                    Automation.status == "active",
                    Automation.next_run_at.is_not(None),
                    Automation.next_run_at <= now,
                )
                .order_by(Automation.next_run_at.asc(), Automation.id.asc())
                .limit(max_dispatch)
            ).all()
            deferred_due = 0
            if len(due) >= max_dispatch:
                deferred_due = db.scalar(
                    select(func.count())
                    .select_from(Automation)
                    .where(
                        Automation.status == "active",
                        Automation.next_run_at.is_not(None),
                        Automation.next_run_at <= now,
                    )
                ) or 0
                deferred_due = max(0, int(deferred_due) - len(due))

            for a in due:
                scheduled_at = _as_naive_utc(a.next_run_at)
                calendar_next = None
                if a.schedule_type == "calendar" and a.calendar_days and a.calendar_time:
                    calendar_next = next_calendar_run(
                        parse_calendar_days(a.calendar_days),
                        a.calendar_time,
                        now,
                    ) or (now + dt.timedelta(days=1))

                meta_floor = 0
                if (a.schedule_type or "") != "calendar":
                    # EXISTS — não lazy-load a.accounts (travava automations + painel).
                    has_meta = db.scalar(
                        select(
                            exists().where(
                                automation_accounts.c.automation_id == a.id,
                                InstagramAccount.id == automation_accounts.c.account_id,
                                InstagramAccount.provider == "meta",
                            )
                        )
                    )
                    meta_floor = META_MIN_INTERVAL if has_meta else 0
                nxt, posts_in_batch = compute_next_run_after_dispatch(
                    a,
                    now,
                    calendar_next=calendar_next,
                    min_gap_minutes=meta_floor,
                )

                # Após downtime: dispara 1 ciclo agora (não pula). Só avisa se estava muito atrasada.
                if scheduled_at is not None and (a.schedule_type or "") != "calendar":
                    overdue_sec = (now - scheduled_at).total_seconds()
                    if overdue_sec > 20 * 60:
                        log.warning(
                            "tick overdue automation=%s atrasada=%.0fs — dispara agora e reagenda",
                            a.id,
                            overdue_sec,
                        )

                db.execute(
                    text(
                        "UPDATE automations SET next_run_at = :nxt, posts_in_batch = :pib WHERE id = :id"
                    ),
                    {"nxt": nxt, "pib": posts_in_batch, "id": a.id},
                )
                to_run.append(
                    (
                        a.id,
                        scheduled_at.isoformat() if scheduled_at is not None else None,
                    )
                )

        for aid, scheduled_iso in to_run:
            execute_automation.delay(aid, scheduled_iso)
            dispatched.append(aid)

        log.info(
            "tick: %d automações disparadas heal=%d deferred_due=%d max=%d",
            len(dispatched),
            healed,
            deferred_due,
            max_dispatch,
        )
        return {
            "now": now.isoformat(),
            "dispatched": dispatched,
            "healed": healed,
            "deferred_due": deferred_due,
            "max_dispatch": max_dispatch,
        }
    finally:
        if client is not None:
            try:
                client.delete("celery:tick:lock")
            except Exception:
                pass
