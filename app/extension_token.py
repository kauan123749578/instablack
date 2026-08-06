"""Token de pareamento da extensão Chrome ↔ painel Instablack.

Formato: ibxt_{user_id}_{secret} — lookup O(1) pelo user_id embutido.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from app.config import get_settings

TOKEN_PREFIX = "ibxt_"


def _pepper() -> bytes:
    return get_settings().secret_key.encode("utf-8")


def hash_extension_token(token: str) -> str:
    return hashlib.sha256(_pepper() + token.encode("utf-8")).hexdigest()


def generate_extension_token(user_id: int) -> str:
    return f"{TOKEN_PREFIX}{int(user_id)}_{secrets.token_urlsafe(24)}"


def parse_extension_token_user_id(token: str) -> int | None:
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    body = token[len(TOKEN_PREFIX) :]
    if "_" not in body:
        return None
    raw_id, _sep, _rest = body.partition("_")
    try:
        uid = int(raw_id)
    except ValueError:
        return None
    return uid if uid > 0 else None


def verify_extension_token(token: str, stored_hash: str | None) -> bool:
    if not token or not stored_hash:
        return False
    if parse_extension_token_user_id(token) is None:
        return False
    expected = hash_extension_token(token)
    return hmac.compare_digest(expected, stored_hash)
