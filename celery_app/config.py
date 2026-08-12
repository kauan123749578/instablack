"""Instância + configuração do Celery (broker Redis, beat tick por segundos)."""
from __future__ import annotations

import ssl

from celery import Celery
from celery.schedules import schedule
from celery.signals import task_postrun, task_prerun, worker_process_init, worker_ready

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

# Import explícito: garante registro mesmo se o worker subir só com -Q parcial
from celery_app.tasks import health as _health  # noqa: E402,F401
from celery_app.tasks import insights as _insights  # noqa: E402,F401
from celery_app.tasks import publish as _publish  # noqa: E402,F401
from celery_app.tasks import warmup as _warmup  # noqa: E402,F401
import celery_app.beat as _beat  # noqa: E402,F401

celery_conf: dict = {
    "timezone": "UTC",
    "task_acks_late": True,
    "worker_prefetch_multiplier": 1,
    "task_default_queue": "default",
    "task_routes": {
        "celery_app.tasks.publish.publish_to_account": {"queue": "publish"},
        "celery_app.tasks.publish.publish_once": {"queue": "publish"},
        "celery_app.tasks.publish.execute_automation": {"queue": "publish"},
        "celery_app.tasks.publish.meta_poll_container": {"queue": "publish"},
        "celery_app.beat.tick": {"queue": "beat"},
        "celery_app.tasks.health.check_all_accounts": {"queue": "beat"},
        "celery_app.tasks.health.check_account_health": {"queue": "health"},
        "celery_app.tasks.health.recover_publish_after_phantom": {"queue": "health"},
        "celery_app.tasks.health.purge_old_publish_logs": {"queue": "health"},
        "celery_app.tasks.insights.sync_all_views": {"queue": "default"},
        "celery_app.tasks.insights.refresh_missing_profile_pics": {"queue": "default"},
        "celery_app.tasks.warmup.run_warmup_job": {"queue": "default"},
    },
    "broker_connection_retry_on_startup": True,
    "result_expires": 60 * 60,
}

if settings.redis_url.startswith("rediss://"):
    # CERT_REQUIRED por padrão. Escape hatch: REDIS_SSL_INSECURE=1 (provedores
    # com cert quebrado). Em produção prefira certificado válido.
    import os

    insecure = (os.getenv("REDIS_SSL_INSECURE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    ssl_opts = {
        "ssl_cert_reqs": ssl.CERT_NONE if insecure else ssl.CERT_REQUIRED,
    }
    celery_conf["broker_use_ssl"] = ssl_opts
    celery_conf["redis_backend_use_ssl"] = ssl_opts

celery_app.conf.update(**celery_conf)


@celery_app.on_after_configure.connect
def _setup_worker_db(**_kwargs) -> None:
    """Migra em background — worker aceita tasks na hora (sem travar no Postgres)."""
    import logging
    import threading

    def _run() -> None:
        try:
            from core.database import init_db

            init_db()
        except Exception:
            logging.getLogger(__name__).exception("init_db no worker falhou")

    threading.Thread(target=_run, name="init-db", daemon=True).start()


@worker_process_init.connect
def _dispose_db_pool_on_prefork(**_kwargs) -> None:
    """Após fork do prefork, descarta conexões herdadas do processo pai."""
    try:
        from core.database import clear_pg_app_context, engine

        engine.dispose(close=True)
        clear_pg_app_context()
    except Exception:
        import logging

        logging.getLogger(__name__).exception("dispose engine no fork falhou")


@worker_ready.connect
def _enqueue_recover_on_misc_worker_ready(sender=None, **_kwargs) -> None:
    """Garante recovery após deploy mesmo se tick antigo expirou na fila beat."""
    import logging
    import os

    role = (os.getenv("APP_ROLE") or "").strip().lower()
    if role not in ("worker-misc", "worker", ""):
        return
    # worker legado (todas filas) também roda recover
    try:
        queues = set()
        consumer = getattr(sender, "consumer", None)
        if consumer and getattr(consumer, "queues", None):
            queues = {q.name for q in consumer.queues}
        if queues and not queues.intersection({"health", "beat", "default"}):
            return
        from celery_app.tasks.health import recover_publish_after_phantom

        recover_publish_after_phantom.apply_async(countdown=8)
        logging.getLogger(__name__).info(
            "worker_ready: enqueued recover_publish_after_phantom (role=%s queues=%s)",
            role or "default",
            sorted(queues) or "?",
        )
    except Exception:
        logging.getLogger(__name__).exception("worker_ready recover enqueue falhou")


@task_prerun.connect
def _pg_app_context_on_task(task=None, **_kwargs) -> None:
    """pg_stat_activity.application_name = celery:<task> (db-health)."""
    try:
        from core.database import set_pg_app_context

        name = getattr(task, "name", None) or ""
        short = name.rsplit(".", 1)[-1] if name else "task"
        set_pg_app_context(f"celery:{short}")
    except Exception:
        pass


@task_postrun.connect
def _pg_app_context_after_task(**_kwargs) -> None:
    try:
        from core.database import clear_pg_app_context

        clear_pg_app_context()
    except Exception:
        pass


celery_app.conf.beat_schedule = {
    "tick-every-N-seconds": {
        "task": "celery_app.beat.tick",
        "schedule": schedule(run_every=settings.beat_tick_seconds),
    },
    # 1× após deploy (Redis NX): reativa Meta/aiograpi needs_login + dispara automações.
    "recover-publish-after-phantom-once": {
        "task": "celery_app.tasks.health.recover_publish_after_phantom",
        "schedule": schedule(run_every=60),
    },
    "account-health-every-15-min": {
        "task": "celery_app.tasks.health.check_all_accounts",
        "schedule": schedule(run_every=900),
    },
    "reel-views-every-15-min": {
        "task": "celery_app.tasks.insights.sync_all_views",
        "schedule": schedule(run_every=900),
    },
    "purge-publish-logs-daily": {
        "task": "celery_app.tasks.health.purge_old_publish_logs",
        "schedule": schedule(run_every=86400),
    },
}
