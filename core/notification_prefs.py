"""Preferências de notificação por usuário (painel + push)."""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from models.models import User

log = logging.getLogger(__name__)

DEFAULT_PREFS: dict[str, bool] = {
    "enabled": True,
    "publish": True,
    "account_offline": True,
    "warmup": True,
    "errors": True,
    "desktop": True,
}

_KIND_TO_PREF: dict[str, str] = {
    "publish": "publish",
    "warmup": "warmup",
    "error": "errors",
    "fail": "errors",
    "offline": "account_offline",
    "account": "account_offline",
    "warning": "errors",
    "metadata": "publish",
    "success": "warmup",
}


def _normalize_prefs(raw: dict[str, Any] | None) -> dict[str, bool]:
    out = dict(DEFAULT_PREFS)
    if not raw:
        return out
    for key in DEFAULT_PREFS:
        if key in raw:
            out[key] = bool(raw[key])
    return out


def get_notification_prefs(user: User) -> dict[str, bool]:
    if not user.notification_prefs_json:
        return dict(DEFAULT_PREFS)
    try:
        data = json.loads(user.notification_prefs_json)
        if not isinstance(data, dict):
            return dict(DEFAULT_PREFS)
        return _normalize_prefs(data)
    except json.JSONDecodeError:
        return dict(DEFAULT_PREFS)


def get_notification_prefs_by_id(db: Session, user_id: int) -> dict[str, bool]:
    user = db.get(User, user_id)
    if not user:
        return dict(DEFAULT_PREFS)
    return get_notification_prefs(user)


def save_notification_prefs(db: Session, user: User, prefs: dict[str, Any]) -> dict[str, bool]:
    normalized = _normalize_prefs(prefs)
    user.notification_prefs_json = json.dumps(normalized, ensure_ascii=False)
    db.commit()
    db.refresh(user)
    return normalized


def prefs_from_form(**fields: str) -> dict[str, bool]:
    """Converte checkboxes HTML (value on / ausente) em dict bool."""
    return {key: val == "on" for key, val in fields.items()}


def can_notify_in_app(kind: str, prefs: dict[str, bool] | None = None) -> bool:
    p = prefs if prefs is not None else DEFAULT_PREFS
    if not p.get("enabled", True):
        return False
    pref_key = _KIND_TO_PREF.get(kind)
    if pref_key:
        return bool(p.get(pref_key, True))
    return True


def can_notify_push(kind: str, prefs: dict[str, bool] | None = None) -> bool:
    p = prefs if prefs is not None else DEFAULT_PREFS
    if not p.get("enabled", True) or not p.get("desktop", True):
        return False
    return can_notify_in_app(kind, p)
