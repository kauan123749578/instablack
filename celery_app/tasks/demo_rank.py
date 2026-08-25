"""Crescimento automático de posts dos usuários demo (Top do Dia / marketing)."""
from __future__ import annotations

import logging

from celery_app.config import celery_app
from core.database import session_scope

log = logging.getLogger(__name__)


@celery_app.task(name="celery_app.tasks.demo_rank.grow_demo_posts", bind=True, expires=240)
def grow_demo_posts(self) -> dict:
    """A cada poucos minutos, demos 'postam' de novo — rank muda ao longo do dia."""
    from app.utils.demo_users import grow_demo_posts_tick

    with session_scope() as db:
        result = grow_demo_posts_tick(db)
    if result.get("added") or result.get("seeded"):
        log.info(
            "demo rank grow: demos=%s added=%s seeded=%s",
            result.get("demos"),
            result.get("added"),
            result.get("seeded"),
        )
    return result
