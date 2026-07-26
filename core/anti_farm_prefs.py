"""Preferências anti-farm / aquecimento Meta por usuário."""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.utils.anti_farm import (
    DEFAULT_STAGGER_MAX,
    DEFAULT_STAGGER_MIN,
    clamp_stagger_minutes,
)
from models.models import User

log = logging.getLogger(__name__)

DEFAULT_ANTI_FARM_PREFS: dict[str, Any] = {
    "stagger_enabled": True,
    "stagger_min_minutes": DEFAULT_STAGGER_MIN,
    "stagger_max_minutes": DEFAULT_STAGGER_MAX,
    "media_rotate_enabled": True,
    # Rotação de legenda removida — sempre OFF (legado no JSON é forçado)
    "caption_rotate_by_account": False,
    "caption_rotate_by_reel": False,
    "meta_warmup_enabled": True,
}

BOOL_KEYS = (
    "stagger_enabled",
    "media_rotate_enabled",
    "meta_warmup_enabled",
)

PREF_LABELS: dict[str, dict[str, str]] = {
    "stagger_enabled": {
        "title": "Espaçamento entre contas",
        "help": (
            "Quando a mesma automação publica em várias contas, espera minutos entre cada @ "
            "(em vez de postar quase no mesmo segundo). Configure o intervalo mínimo/máximo abaixo. "
            "Cada automação também pode ter o próprio valor em Editar."
        ),
    },
    "media_rotate_enabled": {
        "title": "Vídeos diferentes por conta",
        "help": (
            "Se a playlist tiver 2 ou mais vídeos, cada conta do ciclo recebe um arquivo "
            "diferente (roda a lista). Com 1 vídeo só, todas usam o mesmo."
        ),
    },
    "meta_warmup_enabled": {
        "title": "Respeitar modo aquecimento das contas",
        "help": (
            "Se ligado, contas que VOCÊ colocar em aquecimento (abaixo) usam mínimo de 3 horas "
            "entre posts. Contas sem aquecimento continuam com 1 hora."
        ),
    },
}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "on", "true", "yes")


def get_anti_farm_prefs(user: User | None) -> dict[str, Any]:
    out = dict(DEFAULT_ANTI_FARM_PREFS)
    if user is None:
        return out
    raw = getattr(user, "anti_farm_prefs_json", None)
    if not raw:
        return out
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return out
    if not isinstance(data, dict):
        return out
    for key in BOOL_KEYS:
        if key in data:
            out[key] = _truthy(data[key])
    lo, hi = clamp_stagger_minutes(
        data.get("stagger_min_minutes", out["stagger_min_minutes"]),
        data.get("stagger_max_minutes", out["stagger_max_minutes"]),
    )
    out["stagger_min_minutes"] = lo
    out["stagger_max_minutes"] = hi
    # Sempre OFF — rotação de legenda foi removida
    out["caption_rotate_by_account"] = False
    out["caption_rotate_by_reel"] = False
    out["caption_rotate_enabled"] = False
    return out


def get_anti_farm_prefs_by_id(db: Session, user_id: int) -> dict[str, Any]:
    user = db.get(User, user_id)
    return get_anti_farm_prefs(user)


def prefs_from_form(
    *,
    stagger_enabled: str = "",
    stagger_min_minutes: object = DEFAULT_STAGGER_MIN,
    stagger_max_minutes: object = DEFAULT_STAGGER_MAX,
    media_rotate_enabled: str = "",
    caption_rotate_by_account: str = "",
    caption_rotate_by_reel: str = "",
    caption_rotate_enabled: str = "",  # compat (ignorado)
    meta_warmup_enabled: str = "",
) -> dict[str, Any]:
    lo, hi = clamp_stagger_minutes(stagger_min_minutes, stagger_max_minutes)
    _ = (caption_rotate_by_account, caption_rotate_by_reel, caption_rotate_enabled)
    return {
        "stagger_enabled": _truthy(stagger_enabled),
        "stagger_min_minutes": lo,
        "stagger_max_minutes": hi,
        "media_rotate_enabled": _truthy(media_rotate_enabled),
        "caption_rotate_by_account": False,
        "caption_rotate_by_reel": False,
        "meta_warmup_enabled": _truthy(meta_warmup_enabled),
    }


def save_anti_farm_prefs(db: Session, user: User, prefs: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(DEFAULT_ANTI_FARM_PREFS)
    for key in BOOL_KEYS:
        if key in prefs:
            normalized[key] = _truthy(prefs[key])
    lo, hi = clamp_stagger_minutes(
        prefs.get("stagger_min_minutes", normalized["stagger_min_minutes"]),
        prefs.get("stagger_max_minutes", normalized["stagger_max_minutes"]),
    )
    normalized["stagger_min_minutes"] = lo
    normalized["stagger_max_minutes"] = hi
    normalized["caption_rotate_by_account"] = False
    normalized["caption_rotate_by_reel"] = False
    user.anti_farm_prefs_json = json.dumps(normalized, ensure_ascii=False)
    db.add(user)
    return normalized
