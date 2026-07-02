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
    """Ex.: '29 de junho de 2026' em BRT."""
    now = brt_now()
    return f"{now.day} de {MONTHS_PT[now.month - 1]} de {now.year}"


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
    """Retorna 'Bom dia', 'Boa tarde' ou 'Boa noite'."""
    hour = brt_now().hour
    if hour < 12:
        return "Bom dia"
    if hour < 18:
        return "Boa tarde"
    return "Boa noite"


def greeting_for_user(username: str, display_name: str | None = None) -> str:
    """Retorna 'Bom dia/tarde/noite, {nome}'."""
    name = (display_name or username).strip()
    hour = brt_now().hour
    if hour < 12:
        period = "Bom dia"
    elif hour < 18:
        period = "Boa tarde"
    else:
        period = "Boa noite"
    return f"{period}, {name}"
