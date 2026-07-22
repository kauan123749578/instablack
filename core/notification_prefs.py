"""Preferências de notificação por usuário (painel + push)."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from models.models import User

log = logging.getLogger(__name__)

DEFAULT_BOOL_PREFS: dict[str, bool] = {
    "enabled": True,
    "publish": True,
    "account_offline": True,
    "warmup": True,
    "errors": True,
    "desktop": True,
    "publish_show_username": False,
}

DEFAULT_COPY: dict[str, str] = {
    "publish_title": "{label} publicado",
    "publish_body": "",
}

# Compat: código antigo importa DEFAULT_PREFS como só bools
DEFAULT_PREFS: dict[str, Any] = {**DEFAULT_BOOL_PREFS, **DEFAULT_COPY}

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

_TITLE_MAX = 80
_BODY_MAX = 160
_PLACEHOLDER_RE = re.compile(r"\{(label|username)\}")


def _clean_template(value: Any, *, default: str, max_len: int, allow_empty: bool = False) -> str:
    text = str(value if value is not None else default).strip()
    if not text:
        return "" if allow_empty else default
    text = re.sub(r"[\r\n\t]+", " ", text)
    return text[:max_len]


def _normalize_prefs(raw: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {**DEFAULT_BOOL_PREFS, **DEFAULT_COPY}
    if not raw:
        return out
    for key in DEFAULT_BOOL_PREFS:
        if key in raw:
            out[key] = bool(raw[key])
    out["publish_title"] = _clean_template(
        raw.get("publish_title"), default=DEFAULT_COPY["publish_title"], max_len=_TITLE_MAX
    )
    out["publish_body"] = _clean_template(
        raw.get("publish_body"),
        default=DEFAULT_COPY["publish_body"],
        max_len=_BODY_MAX,
        allow_empty=True,
    )
    return out


def get_notification_prefs(user: User) -> dict[str, Any]:
    if not user.notification_prefs_json:
        return dict(DEFAULT_PREFS)
    try:
        data = json.loads(user.notification_prefs_json)
        if not isinstance(data, dict):
            return dict(DEFAULT_PREFS)
        return _normalize_prefs(data)
    except json.JSONDecodeError:
        return dict(DEFAULT_PREFS)


def get_notification_prefs_by_id(db: Session, user_id: int) -> dict[str, Any]:
    user = db.get(User, user_id)
    if not user:
        return dict(DEFAULT_PREFS)
    return get_notification_prefs(user)


def save_notification_prefs(db: Session, user: User, prefs: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_prefs(prefs)
    user.notification_prefs_json = json.dumps(normalized, ensure_ascii=False)
    db.commit()
    db.refresh(user)
    return normalized


def prefs_from_form(**fields: str) -> dict[str, Any]:
    """Converte checkboxes HTML + templates de texto."""
    bool_keys = set(DEFAULT_BOOL_PREFS)
    out: dict[str, Any] = {}
    for key, val in fields.items():
        if key in bool_keys:
            out[key] = val == "on"
        else:
            out[key] = val
    # Checkboxes ausentes = off
    for key in bool_keys:
        if key not in out:
            out[key] = False
    return out


def render_publish_template(template: str, *, label: str, username: str) -> str:
    safe_label = str(label or "Post")
    safe_user = str(username or "").lstrip("@")

    def _repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key == "label":
            return safe_label
        return safe_user

    return _PLACEHOLDER_RE.sub(_repl, template)


def format_publish_copy(
    prefs: dict[str, Any] | None,
    username: str,
    content_type: str | None,
) -> tuple[str, str]:
    from core.notifications import content_label

    p = _normalize_prefs(prefs)
    label = content_label(content_type)
    title = render_publish_template(
        str(p.get("publish_title") or DEFAULT_COPY["publish_title"]),
        label=label,
        username=username,
    )
    show_user = bool(p.get("publish_show_username"))
    body_template = str(p.get("publish_body") or "")
    if show_user and not body_template.strip():
        body_template = "@{username}"
    elif not show_user:
        body_template = re.sub(r"@\{username\}", "", body_template)
        body_template = re.sub(r"\{username\}", "", body_template)
    body = render_publish_template(body_template, label=label, username=username).strip()
    body = re.sub(r"\s{2,}", " ", body).strip(" ·-|")
    return title[:255], body[:1000]


def can_notify_in_app(kind: str, prefs: dict[str, Any] | None = None) -> bool:
    p = prefs if prefs is not None else DEFAULT_PREFS
    if not p.get("enabled", True):
        return False
    pref_key = _KIND_TO_PREF.get(kind)
    if pref_key:
        return bool(p.get(pref_key, True))
    return True


def can_notify_push(kind: str, prefs: dict[str, Any] | None = None) -> bool:
    p = prefs if prefs is not None else DEFAULT_PREFS
    if not p.get("enabled", True) or not p.get("desktop", True):
        return False
    return can_notify_in_app(kind, p)
