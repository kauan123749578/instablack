"""Cálculo de próximo agendamento com variação e descanso por lote."""
from __future__ import annotations

import datetime as dt
import random
from typing import Any


def _clamp_int(value: Any, *, default: int, min_v: int, max_v: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(min_v, min(max_v, n))


def parse_jitter_enabled(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true", "on", "yes")


def parse_jitter_minutes(raw: Any) -> int:
    return _clamp_int(raw, default=10, min_v=1, max_v=120)


def parse_posts_per_batch(raw: Any) -> int:
    return _clamp_int(raw, default=0, min_v=0, max_v=500)


def parse_rest_minutes(raw: Any) -> int:
    return _clamp_int(raw, default=0, min_v=0, max_v=10080)


def apply_time_jitter(when: dt.datetime, *, enabled: bool, jitter_minutes: int) -> dt.datetime:
    """Desloca o horário em ±N minutos aleatórios (anti-padrão no Instagram)."""
    if not enabled:
        return when
    max_m = max(1, min(int(jitter_minutes or 0), 120))
    delta = random.randint(-max_m, max_m)
    return when + dt.timedelta(minutes=delta)


def compute_next_run_after_dispatch(
    automation,
    now: dt.datetime,
    *,
    calendar_next: dt.datetime | None = None,
    min_gap_minutes: int = 0,
) -> tuple[dt.datetime, int]:
    """Calcula next_run_at e o novo posts_in_batch após disparar um ciclo.

    Conta 1 avanço de playlist por disparo. Se posts_per_batch > 0 e
    rest_minutes > 0, após N posts agenda o descanso e zera o contador.

    min_gap_minutes: piso (ex.: 60 Meta) — jitter negativo não pode furar esse intervalo.
    """
    posts_per_batch = int(getattr(automation, "posts_per_batch", 0) or 0)
    rest_minutes = int(getattr(automation, "rest_minutes", 0) or 0)
    posts_in_batch = int(getattr(automation, "posts_in_batch", 0) or 0) + 1

    jitter_on = bool(getattr(automation, "jitter_enabled", False))
    jitter_m = int(getattr(automation, "jitter_minutes", 10) or 10)
    floor_gap = max(0, int(min_gap_minutes or 0))

    def _finalize(nxt: dt.datetime) -> dt.datetime:
        nxt = apply_time_jitter(nxt, enabled=jitter_on, jitter_minutes=jitter_m)
        if floor_gap > 0:
            floor_at = now + dt.timedelta(minutes=floor_gap)
            if nxt < floor_at:
                nxt = floor_at
        if nxt <= now:
            nxt = now + dt.timedelta(minutes=1)
        return nxt

    if posts_per_batch > 0 and rest_minutes > 0 and posts_in_batch >= posts_per_batch:
        nxt = now + dt.timedelta(minutes=rest_minutes)
        return _finalize(nxt), 0

    if getattr(automation, "schedule_type", "interval") == "calendar" and calendar_next is not None:
        nxt = calendar_next
    else:
        interval = max(int(getattr(automation, "interval_minutes", 60) or 60), 1)
        if floor_gap > 0:
            interval = max(interval, floor_gap)
        hold = max(interval * 60, 90)
        nxt = now + dt.timedelta(seconds=hold)

    return _finalize(nxt), posts_in_batch
