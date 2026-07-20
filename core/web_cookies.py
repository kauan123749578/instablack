"""Parse e armazenamento de cookies web do Instagram (Cookie-Editor / header).

Necessário para Story com link via API web (csrftoken, mid, ig_did, etc.).
O sessionid sozinho (instagrapi) não basta — o Instagram rejeita sem csrftoken.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote

from app.security import decrypt_secret, encrypt_secret

# Cookies úteis para a API web (Story link / rupload / configure).
PREFERRED_COOKIE_NAMES = (
    "sessionid",
    "csrftoken",
    "ds_user_id",
    "mid",
    "ig_did",
    "rur",
    "datr",
    "ps_l",
    "ps_n",
    "wd",
    "dpr",
)


class WebCookiesError(ValueError):
    pass


def _clean_cookie_value(value: Any) -> str:
    text = unquote(str(value or "").strip())
    # Cookie-Editor às vezes serializa rur com aspas escapadas.
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    return text.replace('\\"', '"').replace("\\054", ",")


def parse_cookie_header(raw: str) -> dict[str, str]:
    text = (raw or "").strip()
    if text.upper().startswith("INSTAGRAM_COOKIES="):
        text = text.split("=", 1)[1].strip()
    cookies: dict[str, str] = {}
    for part in text.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if not name:
            continue
        cookies[name] = _clean_cookie_value(value)
    return cookies


def parse_cookie_editor_json(raw: str) -> dict[str, str]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WebCookiesError(
            "JSON inválido. Cole o export do Cookie-Editor (lista de cookies)."
        ) from exc

    items: list[Any]
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("cookies"), list):
        items = payload["cookies"]
    elif isinstance(payload, dict) and all(
        isinstance(v, (str, int, float)) for v in payload.values()
    ):
        # Mapa simples {name: value}
        return {
            str(k): _clean_cookie_value(v)
            for k, v in payload.items()
            if str(k).strip()
        }
    else:
        raise WebCookiesError(
            "Formato não reconhecido. Use o JSON do Cookie-Editor ou um mapa name→value."
        )

    cookies: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        domain = str(item.get("domain") or "").lower()
        if domain and "instagram.com" not in domain:
            continue
        cookies[name] = _clean_cookie_value(item.get("value"))
    return cookies


def parse_web_cookies_blob(raw: str) -> dict[str, str]:
    """Aceita JSON do Cookie-Editor OU header Cookie / INSTAGRAM_COOKIES=..."""
    text = (raw or "").strip()
    if not text:
        raise WebCookiesError("Cole os cookies do Instagram (JSON ou header).")

    if text[0] in "[{":
        cookies = parse_cookie_editor_json(text)
    else:
        cookies = parse_cookie_header(text)

    if not cookies.get("sessionid"):
        raise WebCookiesError("Cookies sem sessionid. Exporte de novo do Instagram logado.")
    if not cookies.get("csrftoken"):
        raise WebCookiesError(
            "Cookies sem csrftoken. Sem ele o Story web falha. "
            "Exporte o JSON completo do Cookie-Editor."
        )
    return cookies


def cookies_to_storage_json(cookies: dict[str, str]) -> str:
    """Guarda preferencialmente os cookies usados na API web (+ quaisquer extras)."""
    ordered: dict[str, str] = {}
    for name in PREFERRED_COOKIE_NAMES:
        if cookies.get(name):
            ordered[name] = cookies[name]
    for name, value in cookies.items():
        if name not in ordered and value:
            ordered[name] = value
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))


def encrypt_web_cookies(cookies: dict[str, str]) -> str:
    return encrypt_secret(cookies_to_storage_json(cookies))


def decrypt_web_cookies(token: str | None) -> dict[str, str] | None:
    raw = decrypt_secret(token)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return {
        str(k): _clean_cookie_value(v)
        for k, v in payload.items()
        if str(k).strip() and v is not None
    }


def web_cookies_status(token: str | None) -> dict[str, Any]:
    cookies = decrypt_web_cookies(token) or {}
    return {
        "has_cookies": bool(cookies),
        "has_sessionid": bool(cookies.get("sessionid")),
        "has_csrftoken": bool(cookies.get("csrftoken")),
        "count": len(cookies),
    }
