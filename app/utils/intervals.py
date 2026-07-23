"""Intervalos permitidos para automações recorrentes (minutos)."""
from __future__ import annotations

from typing import Iterable

ALLOWED_INTERVALS = [
    10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60,
    90, 120, 180, 240, 360, 480, 720, 1080, 1440,
]

# API oficial Meta: mínimo 1 hora entre publicações
META_MIN_INTERVAL = 60


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


def validate_interval_for_accounts(interval_minutes: int, accounts: Iterable) -> str | None:
    """Retorna mensagem de erro ou None se ok."""
    if interval_minutes not in ALLOWED_INTERVALS:
        return "Intervalo inválido."
    if accounts_include_meta(accounts) and interval_minutes < META_MIN_INTERVAL:
        return "Com contas da API oficial, o intervalo mínimo é 1 hora."
    return None
