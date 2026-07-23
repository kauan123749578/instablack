"""Preferências anti-farm / aquecimento Meta por usuário."""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from models.models import User

log = logging.getLogger(__name__)

DEFAULT_ANTI_FARM_PREFS: dict[str, bool] = {
    "stagger_enabled": True,
    "media_rotate_enabled": True,
    "caption_rotate_enabled": True,
    "meta_warmup_enabled": True,
}

PREF_LABELS: dict[str, dict[str, str]] = {
    "stagger_enabled": {
        "title": "Espaçamento entre contas",
        "help": (
            "Quando a mesma automação publica em várias contas, espera alguns minutos "
            "entre cada @ (em vez de postar quase no mesmo segundo). Reduz o padrão de farm."
        ),
    },
    "media_rotate_enabled": {
        "title": "Vídeos diferentes por conta",
        "help": (
            "Se a playlist tiver 2 ou mais vídeos, cada conta do ciclo recebe um arquivo "
            "diferente (roda a lista). Com 1 vídeo só, todas usam o mesmo."
        ),
    },
    "caption_rotate_enabled": {
        "title": "Legendas diferentes por conta",
        "help": (
            "Usa as “Legendas alternativas” da automação (uma por linha). Cada conta pega "
            "uma linha diferente. Se estiver vazio, usa a legenda principal."
        ),
    },
    "meta_warmup_enabled": {
        "title": "Aquecimento de conta Meta nova",
        "help": (
            "Contas Meta com menos de 7 dias no painel usam mínimo de 3 horas entre posts. "
            "Se tentar postar antes, o envio é pulado (aparece como skipped nos Logs)."
        ),
    },
}


def get_anti_farm_prefs(user: User | None) -> dict[str, bool]:
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
    for key in DEFAULT_ANTI_FARM_PREFS:
        if key in data:
            out[key] = bool(data[key])
    return out


def get_anti_farm_prefs_by_id(db: Session, user_id: int) -> dict[str, bool]:
    user = db.get(User, user_id)
    return get_anti_farm_prefs(user)


def prefs_from_form(
    *,
    stagger_enabled: str = "",
    media_rotate_enabled: str = "",
    caption_rotate_enabled: str = "",
    meta_warmup_enabled: str = "",
) -> dict[str, bool]:
    return {
        "stagger_enabled": stagger_enabled in ("1", "on", "true", "True"),
        "media_rotate_enabled": media_rotate_enabled in ("1", "on", "true", "True"),
        "caption_rotate_enabled": caption_rotate_enabled in ("1", "on", "true", "True"),
        "meta_warmup_enabled": meta_warmup_enabled in ("1", "on", "true", "True"),
    }


def save_anti_farm_prefs(db: Session, user: User, prefs: dict[str, Any]) -> dict[str, bool]:
    normalized = dict(DEFAULT_ANTI_FARM_PREFS)
    for key in DEFAULT_ANTI_FARM_PREFS:
        if key in prefs:
            normalized[key] = bool(prefs[key])
    user.anti_farm_prefs_json = json.dumps(normalized, ensure_ascii=False)
    db.add(user)
    return normalized
