"""Instância + configuração do Celery (broker Redis, beat tick por segundos)."""
from __future__ import annotations

import ssl

from celery import Celery
from celery.schedules import schedule

from app.config import settings

celery_app = Celery(
    "reels_scheduler",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "celery_app.tasks.publish",
        "celery_app.tasks.health",
        "celery_app.tasks.insights",
        "celery_app.tasks.warmup",
        "celery_app.beat",
    ],
)

celery_conf: dict = {
    "timezone": "UTC",
    "task_acks_late": True,
    "worker_prefetch_multiplier": 1,
    "task_default_queue": "default",
    "task_routes": {
        "celery_app.tasks.publish.publish_to_account": {"queue": "publish"},
        "celery_app.tasks.publish.publish_once": {"queue": "publish"},
        "celery_app.tasks.publish.execute_automation": {"queue": "publish"},
        "celery_app.beat.tick": {"queue": "beat"},
        "celery_app.tasks.health.check_all_accounts": {"queue": "beat"},
        "celery_app.tasks.health.check_account_health": {"queue": "default"},
        "celery_app.tasks.insights.sync_all_views": {"queue": "default"},
        "celery_app.tasks.warmup.run_warmup_job": {"queue": "default"},
    },
    "broker_connection_retry_on_startup": True,
    "result_expires": 60 * 60,
}

if settings.redis_url.startswith("rediss://"):
    ssl_opts = {"ssl_cert_reqs": ssl.CERT_NONE}
    celery_conf["broker_use_ssl"] = ssl_opts
    celery_conf["redis_backend_use_ssl"] = ssl_opts

celery_app.conf.update(**celery_conf)


@celery_app.on_after_configure.connect
def _setup_worker_db(**_kwargs) -> None:
    """Garante tabelas/migrações no boot do worker (app_notifications, push, etc.)."""
    try:
        from core.database import init_db

        init_db()
    except Exception:
        import logging

        logging.getLogger(__name__).exception("init_db no worker falhou")


celery_app.conf.beat_schedule = {
    "tick-every-N-seconds": {
        "task": "celery_app.beat.tick",
        "schedule": schedule(run_every=settings.beat_tick_seconds),
    },
    "account-health-every-15-min": {
        "task": "celery_app.tasks.health.check_all_accounts",
        "schedule": schedule(run_every=900),
    },
    "reel-views-every-15-min": {
        "task": "celery_app.tasks.insights.sync_all_views",
        "schedule": schedule(run_every=900),
    },
}
