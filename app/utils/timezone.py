"""Conversão de datas UTC (naive) para horário de Brasília."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

BRT = ZoneInfo("America/Sao_Paulo")
DEFAULT_FMT = "%d/%m/%Y %H:%M"

MONTHS_PT = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]


def format_date_header() -> str:
    """Ex.: '4 DE JULHO DE 2026' em BRT (uppercase)."""
    now = brt_now()
    month = MONTHS_PT[now.month - 1].upper()
    return f"{now.day} DE {month} DE {now.year}"


def _greeting_from_hour(hour: int) -> str:
    if hour < 6:
        return "Boa madrugada"
    if hour < 12:
        return "Bom dia"
    if hour < 18:
        return "Boa tarde"
    return "Boa noite"


def to_brt(value: dt.datetime | None, fmt: str = DEFAULT_FMT) -> str:
    """Converte datetime UTC naive (ou aware) para string em BRT."""
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    local = value.astimezone(BRT)
    return local.strftime(fmt)


def brt_now() -> dt.datetime:
    """Datetime aware em BRT (útil para saudação)."""
    return dt.datetime.now(BRT)


def greeting_period() -> str:
    """Retorna saudação conforme horário BRT."""
    return _greeting_from_hour(brt_now().hour)


def greeting_for_user(username: str, display_name: str | None = None) -> str:
    """Retorna 'Bom dia/tarde/noite/madrugada, {nome}'."""
    name = (display_name or username).strip()
    return f"{_greeting_from_hour(brt_now().hour)}, {name}"
