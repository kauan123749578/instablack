"""Anti-farm helpers: stagger entre contas, legendas alternativas."""
from __future__ import annotations

import json
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.models import Automation


def account_publish_countdown(index: int, account_count: int) -> int:
    """Countdown em segundos para a conta `index` no fan-out.

    Conta 0 publica já; demais esperam i * (2–8 min) + 0–90 s.
    """
    if account_count <= 1 or index <= 0:
        return 0
    return index * random.randint(2, 8) * 60 + random.randint(0, 90)


def parse_captions_json(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def captions_to_json(captions: list[str] | None) -> str | None:
    cleaned = [str(c or "").strip() for c in (captions or []) if str(c or "").strip()]
    if not cleaned:
        return None
    return json.dumps(cleaned, ensure_ascii=False)


def captions_from_textarea(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [line.strip() for line in str(raw).splitlines() if line.strip()]


def captions_textarea_value(raw_json: str | None) -> str:
    return "\n".join(parse_captions_json(raw_json))


def resolve_caption_for_slot(automation: Automation, slot: int) -> str:
    """Conta `slot` usa captions_json[i % n]; se vazio, usa automation.caption."""
    alts = parse_captions_json(getattr(automation, "captions_json", None))
    if alts:
        idx = slot % len(alts)
        return alts[idx]
    return (getattr(automation, "caption", None) or "") or ""
