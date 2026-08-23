"""Cliente HTTP para provisionar/login no Backspace (TheZwiss/backspace)."""
from __future__ import annotations

import logging
import re
import secrets
from typing import Any

import httpx

from app.config import settings
from app.security import decrypt_secret, encrypt_secret

log = logging.getLogger(__name__)

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{2,32}$")


def backspace_enabled() -> bool:
    return bool(settings.backspace_enabled and (settings.backspace_url or "").strip())


def backspace_base_url() -> str:
    return (settings.backspace_url or "").strip().rstrip("/")


def _api_url(path: str) -> str:
    base = backspace_base_url()
    if not base:
        raise RuntimeError("BACKSPACE_URL não configurado.")
    p = path if path.startswith("/") else f"/{path}"
    return f"{base}{p}"


def sanitize_backspace_username(username: str) -> str:
    """Backspace exige username alfanumérico; Instablack pode ter @ ou ponto."""
    raw = (username or "").strip().lstrip("@").lower()
    cleaned = re.sub(r"[^a-z0-9_]", "_", raw)[:32]
    if len(cleaned) < 2:
        cleaned = f"u{cleaned or 'ser'}"[:32]
    return cleaned


def generate_backspace_password() -> str:
    return secrets.token_urlsafe(24)


def get_stored_backspace_password(user) -> str | None:
    enc = getattr(user, "backspace_password_enc", None)
    if not enc:
        return None
    return decrypt_secret(enc)


def store_backspace_password(user, plain: str) -> None:
    user.backspace_password_enc = encrypt_secret(plain)


async def _post_json(path: str, payload: dict[str, Any], token: str | None = None) -> tuple[int, dict]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        res = await client.post(_api_url(path), json=payload, headers=headers)
        try:
            data = res.json()
        except Exception:
            data = {"detail": res.text[:500]}
        return res.status_code, data if isinstance(data, dict) else {"detail": str(data)}


async def register_user(
    username: str,
    password: str,
    display_name: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"username": username, "password": password}
    if display_name:
        payload["displayName"] = display_name[:64]
    status, data = await _post_json("/api/auth/register", payload)
    if status not in (200, 201):
        raise RuntimeError(data.get("detail") or data.get("error") or f"register HTTP {status}")
    return data


async def login_user(username: str, password: str) -> dict[str, Any]:
    status, data = await _post_json("/api/auth/login", {"username": username, "password": password})
    if status != 200:
        raise RuntimeError(data.get("detail") or data.get("error") or f"login HTTP {status}")
    return data


async def ensure_backspace_account(user, db) -> dict[str, Any]:
    """
    Garante conta no Backspace para o usuário Instablack.
    Retorna { token, user, bs_username, created }.
    """
    if not backspace_enabled():
        raise RuntimeError("Backspace não está habilitado (BACKSPACE_ENABLED + BACKSPACE_URL).")

    bs_username = sanitize_backspace_username(user.username)
    if not _USERNAME_RE.match(bs_username):
        bs_username = f"u{user.id}"[:32]

    password = get_stored_backspace_password(user)
    created = False

    if not password:
        password = generate_backspace_password()
        store_backspace_password(user, password)
        db.add(user)
        db.commit()
        created = True
        try:
            display = (getattr(user, "display_name", None) or user.username or bs_username).strip()
            data = await register_user(bs_username, password, display_name=display)
            log.info("backspace: conta criada user_id=%s bs=%s", user.id, bs_username)
            return {
                "token": data.get("token"),
                "user": data.get("user"),
                "bs_username": bs_username,
                "created": True,
            }
        except RuntimeError as exc:
            msg = str(exc).lower()
            if "taken" in msg or "exist" in msg or "409" in msg:
                log.info("backspace: conta já existe bs=%s, tentando login", bs_username)
            else:
                log.warning("backspace: register falhou bs=%s — %s", bs_username, exc)

    data = await login_user(bs_username, password)
    return {
        "token": data.get("token"),
        "user": data.get("user"),
        "bs_username": bs_username,
        "created": created,
    }
