"""Intervalos permitidos para automações recorrentes (minutos)."""
from __future__ import annotations

import datetime as dt
from typing import Any, Iterable

ALLOWED_INTERVALS = [
    10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60,
    90, 120, 180, 240, 360, 480, 720, 1080, 1440,
]

# API oficial Meta: mínimo 1 hora entre publicações
META_MIN_INTERVAL = 60
# Contas Meta novas (<7 dias): piso automático de 3 horas
META_WARMUP_DAYS = 7
META_WARMUP_MIN_INTERVAL = 180


def interval_label(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} minutos"
    if minutes == 60:
        return "1 hora"
    if minutes < 1440:
        h = minutes / 60
        if h == int(h):
            return f"{int(h)} horas"
        return f"{minutes} minutos"
    return "24 horas"


def intervals_for_meta(include_meta: bool) -> list[int]:
    """Lista de intervalos; com Meta, só opções >= 1 hora."""
    if include_meta:
        return [m for m in ALLOWED_INTERVALS if m >= META_MIN_INTERVAL]
    return list(ALLOWED_INTERVALS)


def accounts_include_meta(accounts: Iterable) -> bool:
    for acc in accounts:
        if getattr(acc, "provider", None) == "meta":
            return True
    return False


def _as_utc_naive(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is not None:
        return value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return value


def is_meta_in_warmup(account: Any, *, now: dt.datetime | None = None) -> bool:
    """True se conta Meta foi criada há menos de META_WARMUP_DAYS."""
    if getattr(account, "provider", None) != "meta":
        return False
    created = getattr(account, "created_at", None)
    if created is None:
        return False
    if not isinstance(created, dt.datetime):
        return False
    ref = now or dt.datetime.utcnow()
    age = _as_utc_naive(ref) - _as_utc_naive(created)
    return age.total_seconds() < META_WARMUP_DAYS * 86400


def meta_min_interval_for_account(account: Any, *, now: dt.datetime | None = None) -> int:
    """Piso efetivo (minutos) para uma conta Meta; 0 se não for Meta."""
    if getattr(account, "provider", None) != "meta":
        return 0
    if is_meta_in_warmup(account, now=now):
        return META_WARMUP_MIN_INTERVAL
    return META_MIN_INTERVAL


def effective_meta_min_interval(accounts: Iterable, *, now: dt.datetime | None = None) -> int:
    """Maior piso Meta exigido pelas contas selecionadas (0 se nenhuma Meta)."""
    floor = 0
    for acc in accounts:
        floor = max(floor, meta_min_interval_for_account(acc, now=now))
    return floor


def accounts_include_meta_warmup(accounts: Iterable, *, now: dt.datetime | None = None) -> bool:
    return any(is_meta_in_warmup(acc, now=now) for acc in accounts)


def validate_interval_for_accounts(
    interval_minutes: int,
    accounts: Iterable,
    *,
    meta_warmup_enabled: bool = True,
) -> str | None:
    """Retorna mensagem de erro ou None se ok."""
    if interval_minutes not in ALLOWED_INTERVALS:
        return "Intervalo inválido."
    floor = 0
    for acc in accounts:
        if getattr(acc, "provider", None) != "meta":
            continue
        if meta_warmup_enabled and is_meta_in_warmup(acc):
            floor = max(floor, META_WARMUP_MIN_INTERVAL)
        else:
            floor = max(floor, META_MIN_INTERVAL)
    if floor and interval_minutes < floor:
        if floor >= META_WARMUP_MIN_INTERVAL:
            return (
                "Contas Meta com menos de 7 dias exigem intervalo mínimo de 3 horas "
                "entre posts. Desligue o aquecimento em Aquecimento se quiser só 1h."
            )
        return "Com contas da API oficial, o intervalo mínimo é 1 hora."
    return None
