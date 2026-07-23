"""Anti-farm helpers: stagger entre contas, legendas alternativas."""
from __future__ import annotations

import json
import random
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from models.models import Automation

DEFAULT_STAGGER_MIN = 2
DEFAULT_STAGGER_MAX = 8
STAGGER_MIN_BOUND = 1
STAGGER_MAX_BOUND = 120


def clamp_stagger_minutes(min_minutes: Any, max_minutes: Any) -> tuple[int, int]:
    try:
        lo = int(min_minutes)
    except (TypeError, ValueError):
        lo = DEFAULT_STAGGER_MIN
    try:
        hi = int(max_minutes)
    except (TypeError, ValueError):
        hi = DEFAULT_STAGGER_MAX
    lo = max(STAGGER_MIN_BOUND, min(STAGGER_MAX_BOUND, lo))
    hi = max(STAGGER_MIN_BOUND, min(STAGGER_MAX_BOUND, hi))
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi


def account_publish_countdown(
    index: int,
    account_count: int,
    *,
    min_minutes: int = DEFAULT_STAGGER_MIN,
    max_minutes: int = DEFAULT_STAGGER_MAX,
    extra_seconds_max: int = 90,
) -> int:
    """Countdown em segundos para a conta `index` no fan-out.

    Conta 0 publica já; demais esperam i * (min–max min) + 0–extra_seconds_max s.
    """
    if account_count <= 1 or index <= 0:
        return 0
    lo, hi = clamp_stagger_minutes(min_minutes, max_minutes)
    extra = max(0, int(extra_seconds_max))
    return index * random.randint(lo, hi) * 60 + (random.randint(0, extra) if extra else 0)


def resolve_stagger_config(
    automation: Any | None = None,
    prefs: dict | None = None,
) -> tuple[bool, int, int]:
    """Retorna (enabled, min_minutes, max_minutes) priorizando a automação."""
    prefs = prefs or {}
    user_on = bool(prefs.get("stagger_enabled", True))
    lo_pref = prefs.get("stagger_min_minutes", DEFAULT_STAGGER_MIN)
    hi_pref = prefs.get("stagger_max_minutes", DEFAULT_STAGGER_MAX)

    if automation is None:
        lo, hi = clamp_stagger_minutes(lo_pref, hi_pref)
        return user_on, lo, hi

    auto_on = bool(getattr(automation, "stagger_enabled", True))
    enabled = user_on and auto_on
    lo_raw = getattr(automation, "stagger_min_minutes", None)
    hi_raw = getattr(automation, "stagger_max_minutes", None)
    if lo_raw is None:
        lo_raw = lo_pref
    if hi_raw is None:
        hi_raw = hi_pref
    lo, hi = clamp_stagger_minutes(lo_raw, hi_raw)
    return enabled, lo, hi


def parse_captions_json(raw: str | None) -> list[str]:
    if not raw:
        return []
    text_raw = str(raw).strip()
    if not text_raw:
        return []
    try:
        data = json.loads(text_raw)
    except (json.JSONDecodeError, TypeError):
        # Texto cru (não-JSON) = uma legenda só
        return [text_raw]
    if isinstance(data, str):
        one = data.strip()
        return [one] if one else []
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


def captions_from_form(captions_alt: list[str] | str | None) -> list[str]:
    """Aceita lista de textareas (botão +) ou texto antigo uma-por-linha."""
    if captions_alt is None:
        return []
    if isinstance(captions_alt, str):
        return captions_from_textarea(captions_alt)
    out: list[str] = []
    for item in captions_alt:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def resolve_caption_for_slot(automation: Automation, slot: int) -> str:
    """Compat: só por conta."""
    return resolve_caption(
        automation,
        account_slot=slot,
        reel_index=0,
        by_account=True,
        by_reel=False,
    )


def resolve_caption(
    automation: Automation,
    *,
    account_slot: int = 0,
    reel_index: int = 0,
    by_account: bool = True,
    by_reel: bool = False,
) -> str:
    """Resolve legenda da lista de rotação.

    - sem lista de rotação → sempre a legenda principal (1 legenda)
    - só por conta: captions[account_slot % n]
    - só por reel: captions[reel_index % n]
    - os dois: captions[(account_slot + reel_index) % n]
    - rotação desligada: principal (ou 1ª da lista)
    """
    main = (getattr(automation, "caption", None) or "") or ""
    alts = parse_captions_json(getattr(automation, "captions_json", None))

    # Caso mais comum: só a legenda principal — nunca depender da rotação
    if not alts:
        return main

    if not by_account and not by_reel:
        return main or alts[0]

    # Uma única alternativa = essa legenda para todas as contas/reels
    if len(alts) == 1:
        return alts[0] or main

    idx = 0
    if by_reel:
        idx += max(0, int(reel_index or 0))
    if by_account:
        idx += max(0, int(account_slot or 0))
    chosen = alts[idx % len(alts)]
    # Nunca publicar vazio se ainda houver principal
    return chosen or main or alts[0]
