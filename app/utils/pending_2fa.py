"""Cache do device/settings entre a 1ª tentativa de login e o envio do 2FA.

Espelha o postagemIG (`_pending_2fa`): sem reaproveitar UUIDs/mid/cookies do
pre-login, o Instagram rejeita o código ou devolve Bloks sem auth payload.
Redis (TTL 10 min) porque o gunicorn tem vários workers.
"""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("app.pending_2fa")

_TTL_SEC = 600
_MEM: dict[str, dict] = {}


def _key(user_id: int, username: str) -> str:
    return f"pending2fa:{int(user_id)}:{(username or '').strip().lstrip('@').lower()}"


def _redis():
    try:
        from redis import Redis
        from app.config import settings

        return Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2)
    except Exception as exc:
        log.debug("pending_2fa redis indisponível: %s", exc)
        return None


def save_pending_2fa(user_id: int, username: str, settings: dict | None) -> None:
    if not settings or not username:
        return
    key = _key(user_id, username)
    payload = json.dumps(settings, ensure_ascii=False, default=str)
    client = _redis()
    if client is not None:
        try:
            client.setex(key, _TTL_SEC, payload)
            return
        except Exception as exc:
            log.warning("pending_2fa save redis falhou: %s", exc)
    _MEM[key] = settings


def load_pending_2fa(user_id: int, username: str) -> dict | None:
    if not username:
        return None
    key = _key(user_id, username)
    client = _redis()
    if client is not None:
        try:
            raw = client.get(key)
            if raw:
                data = json.loads(raw)
                return data if isinstance(data, dict) else None
        except Exception as exc:
            log.warning("pending_2fa load redis falhou: %s", exc)
    data = _MEM.get(key)
    return data if isinstance(data, dict) else None


def clear_pending_2fa(user_id: int, username: str) -> None:
    key = _key(user_id, username)
    client = _redis()
    if client is not None:
        try:
            client.delete(key)
        except Exception:
            pass
    _MEM.pop(key, None)
