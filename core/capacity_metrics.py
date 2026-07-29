"""Métricas de capacidade (filas Celery, schedule_lag, duração publish)."""
from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

_SAMPLE_KEY_LAG = "metrics:schedule_lag_seconds"
_SAMPLE_KEY_DUR = "metrics:publish_duration_seconds"
_SAMPLE_KEY_OK = "metrics:uploads_ok_ts"
_SAMPLE_KEY_FAIL = "metrics:uploads_fail_ts"
_DEFER_KEY = "metrics:meta_defer"
_MAX_SAMPLES = 2000


def _redis():
    from redis import Redis

    from app.config import settings

    return Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
        decode_responses=True,
    )


def _percentile(sorted_vals: list[float], pct: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


def _summarize(samples: list[float]) -> dict[str, Any]:
    if not samples:
        return {"count": 0, "p50": None, "p95": None, "p99": None, "max": None}
    vals = sorted(float(x) for x in samples)
    return {
        "count": len(vals),
        "p50": round(_percentile(vals, 50) or 0, 2),
        "p95": round(_percentile(vals, 95) or 0, 2),
        "p99": round(_percentile(vals, 99) or 0, 2),
        "max": round(vals[-1], 2),
    }


def record_publish_sample(
    *,
    schedule_lag_seconds: float | None,
    duration_seconds: float | None,
    status: str,
) -> None:
    """Registra amostra após um publish (success/failed)."""
    try:
        client = _redis()
        pipe = client.pipeline()
        if schedule_lag_seconds is not None and schedule_lag_seconds >= 0:
            pipe.lpush(_SAMPLE_KEY_LAG, f"{schedule_lag_seconds:.3f}")
            pipe.ltrim(_SAMPLE_KEY_LAG, 0, _MAX_SAMPLES - 1)
        if duration_seconds is not None and duration_seconds >= 0:
            pipe.lpush(_SAMPLE_KEY_DUR, f"{duration_seconds:.3f}")
            pipe.ltrim(_SAMPLE_KEY_DUR, 0, _MAX_SAMPLES - 1)
        now = time.time()
        if status == "success":
            pipe.lpush(_SAMPLE_KEY_OK, str(now))
            pipe.ltrim(_SAMPLE_KEY_OK, 0, 5000)
        elif status == "failed":
            pipe.lpush(_SAMPLE_KEY_FAIL, str(now))
            pipe.ltrim(_SAMPLE_KEY_FAIL, 0, 5000)
        pipe.execute()
    except Exception as exc:
        log.debug("record_publish_sample falhou: %s", exc)


def record_meta_defer(reason: str) -> None:
    try:
        client = _redis()
        key = f"{_DEFER_KEY}:{reason.split(':')[0][:40]}"
        client.incr(key)
        client.expire(key, 86400)
    except Exception:
        pass


def queue_depths() -> dict[str, int]:
    """Profundidade das filas Celery no Redis (listas por nome)."""
    out = {q: 0 for q in ("publish", "beat", "health", "default")}
    try:
        client = _redis()
        for q in out:
            # Celery Redis: chave = nome da fila
            n = client.llen(q)
            out[q] = int(n or 0)
            # unacked / priority variants comuns
            for suffix in ("",):
                _ = suffix
    except Exception as exc:
        log.warning("queue_depths falhou: %s", exc)
    return out


def _count_recent(key: str, window_sec: int = 60) -> int:
    try:
        client = _redis()
        vals = client.lrange(key, 0, 5000)
        cutoff = time.time() - window_sec
        return sum(1 for v in vals if float(v) >= cutoff)
    except Exception:
        return 0


def meta_inflight_counts() -> dict[str, Any]:
    try:
        client = _redis()
        global_n = int(client.zcard("meta:global_active") or 0)
        # Conta chaves user active (scan limitado)
        user_keys = 0
        user_members = 0
        for key in client.scan_iter(match="meta:user_active:*", count=50):
            user_keys += 1
            user_members += int(client.zcard(key) or 0)
            if user_keys >= 40:
                break
        return {
            "meta_inflight_global": global_n,
            "meta_user_active_keys": user_keys,
            "meta_inflight_by_user_sum": user_members,
        }
    except Exception as exc:
        return {"error": str(exc)[:120]}


def redis_latency_ms() -> float | None:
    try:
        client = _redis()
        t0 = time.perf_counter()
        client.ping()
        return round((time.perf_counter() - t0) * 1000, 2)
    except Exception:
        return None


def collect_capacity_metrics() -> dict[str, Any]:
    """Snapshot para GET /admin/metrics."""
    lag_samples: list[float] = []
    dur_samples: list[float] = []
    defer: dict[str, int] = {}
    try:
        client = _redis()
        lag_samples = [float(x) for x in client.lrange(_SAMPLE_KEY_LAG, 0, _MAX_SAMPLES - 1)]
        dur_samples = [float(x) for x in client.lrange(_SAMPLE_KEY_DUR, 0, _MAX_SAMPLES - 1)]
        for key in client.scan_iter(match=f"{_DEFER_KEY}:*", count=50):
            reason = str(key).split(":", 2)[-1]
            defer[reason] = int(client.get(key) or 0)
    except Exception as exc:
        log.warning("collect_capacity_metrics redis: %s", exc)

    workers: dict[str, Any] = {"note": "use Flower/Celery inspect; approx via queue depth"}
    try:
        from celery_app.config import celery_app

        insp = celery_app.control.inspect(timeout=1.0)
        active = insp.active() or {}
        stats = insp.stats() or {}
        busy = sum(len(v or []) for v in active.values())
        pool_size = 0
        for st in stats.values():
            pool = (st or {}).get("pool") or {}
            pool_size += int(pool.get("max-concurrency") or 0)
        workers = {
            "workers_busy": busy,
            "workers_reported": len(stats),
            "pool_max_concurrency_sum": pool_size,
            "workers_idle_approx": max(0, pool_size - busy) if pool_size else None,
        }
    except Exception as exc:
        workers = {"error": str(exc)[:160]}

    return {
        "ok": True,
        "queue_depth": queue_depths(),
        "schedule_lag_seconds": _summarize(lag_samples),
        "publish_duration_seconds": _summarize(dur_samples),
        "uploads_per_minute": _count_recent(_SAMPLE_KEY_OK, 60),
        "failed_uploads_per_minute": _count_recent(_SAMPLE_KEY_FAIL, 60),
        "meta_defer_count": defer,
        **meta_inflight_counts(),
        "workers": workers,
        "redis_latency_ms": redis_latency_ms(),
        "slo": {
            "schedule_lag_p95_target_sec": 120,
            "schedule_lag_p99_target_sec": 300,
        },
    }


def ffmpeg_slot(*, wait_timeout_sec: float = 120.0) -> Any:
    """Context manager Redis: limita overlays ffmpeg simultâneos."""
    from contextlib import contextmanager
    import uuid

    from app.config import settings

    @contextmanager
    def _cm():
        limit = int(getattr(settings, "ffmpeg_max_concurrent", 2) or 0)
        if limit <= 0:
            yield
            return
        token = str(uuid.uuid4())
        key = "ffmpeg:camu:slots"
        client = None
        acquired = False
        deadline = time.time() + wait_timeout_sec
        try:
            client = _redis()
            while time.time() < deadline:
                # ZSET score = expiry
                now = time.time()
                client.zremrangebyscore(key, 0, now)
                if client.zcard(key) < limit:
                    client.zadd(key, {token: now + wait_timeout_sec + 30})
                    acquired = True
                    break
                time.sleep(0.4)
            if not acquired:
                raise RuntimeError(
                    f"ffmpeg_max_concurrent={limit} esgotado após {wait_timeout_sec:.0f}s"
                )
            yield
        finally:
            if acquired and client is not None:
                try:
                    client.zrem(key, token)
                except Exception:
                    pass

    return _cm()
