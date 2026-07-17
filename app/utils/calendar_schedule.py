"""Cálculo de próxima execução para automações por calendário (dias do mês + horários BRT)."""
from __future__ import annotations

import datetime as dt
import json
import re
from zoneinfo import ZoneInfo

BRT = ZoneInfo("America/Sao_Paulo")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


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


def _normalize_hhmm(value: str) -> str | None:
    m = _TIME_RE.match(value.strip())
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def parse_calendar_times(raw: str | None) -> list[str]:
    """Aceita '10:00', '10:00,14:00' ou JSON ['10:00','14:00']."""
    if not raw:
        return []
    text = raw.strip()
    candidates: list[str] = []
    if text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        if isinstance(data, list):
            candidates = [str(x) for x in data]
        elif isinstance(data, str):
            candidates = [data]
    elif "," in text:
        candidates = [p.strip() for p in text.split(",") if p.strip()]
    else:
        candidates = [text]

    out: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        norm = _normalize_hhmm(item)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return sorted(out)


def times_to_storage(times: list[str]) -> str:
    cleaned = parse_calendar_times(",".join(times) if times else "")
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return json.dumps(cleaned, ensure_ascii=False)


def format_calendar_times_label(raw: str | None) -> str:
    times = parse_calendar_times(raw)
    if not times:
        return "—"
    return ", ".join(times)


def next_calendar_run(
    days: list[int],
    time_hhmm: str,
    after: dt.datetime | None = None,
) -> dt.datetime | None:
    """Próximo slot em UTC naive. `time_hhmm` pode ser um ou vários horários."""
    if not days:
        return None
    times = parse_calendar_times(time_hhmm)
    if not times:
        return None

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
        for hhmm in times:
            hour, minute = map(int, hhmm.split(":"))
            slot = dt.datetime(
                day_date.year, day_date.month, day_date.day,
                hour, minute, 0, tzinfo=BRT,
            )
            if slot > now_brt:
                return slot.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return None
