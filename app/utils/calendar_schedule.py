"""Cálculo de próxima execução para automações por calendário (dias do mês + horário BRT)."""
from __future__ import annotations

import datetime as dt
import json
from zoneinfo import ZoneInfo

BRT = ZoneInfo("America/Sao_Paulo")


def parse_calendar_days(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        if raw.strip().startswith("["):
            days = json.loads(raw)
        else:
            days = [int(x.strip()) for x in raw.split(",") if x.strip()]
        return sorted({d for d in days if isinstance(d, int) and 1 <= d <= 31})
    except (ValueError, json.JSONDecodeError):
        return []


def days_to_json(days: list[int]) -> str:
    valid = sorted({d for d in days if 1 <= d <= 31})
    return json.dumps(valid)


def next_calendar_run(
    days: list[int],
    time_hhmm: str,
    after: dt.datetime | None = None,
) -> dt.datetime | None:
    """Próximo slot em UTC naive (compatível com o resto do app)."""
    if not days or not time_hhmm:
        return None

    parts = time_hhmm.strip().split(":")
    if len(parts) < 2:
        return None
    hour, minute = int(parts[0]), int(parts[1])

    if after is None:
        now_brt = dt.datetime.now(BRT)
    elif after.tzinfo is None:
        now_brt = after.replace(tzinfo=dt.timezone.utc).astimezone(BRT)
    else:
        now_brt = after.astimezone(BRT)

    start = now_brt.date()
    for offset in range(62):
        day_date = start + dt.timedelta(days=offset)
        if day_date.day not in days:
            continue
        slot = dt.datetime(
            day_date.year, day_date.month, day_date.day,
            hour, minute, 0, tzinfo=BRT,
        )
        if slot > now_brt:
            return slot.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return None
