"""Normalização de strings de proxy para o formato aceito pelo instagrapi."""
from __future__ import annotations

import re
from urllib.parse import quote, unquote, urlparse


def normalize_proxy(raw: str) -> str:
    """Converte formatos comuns (ip:porta:user:senha, socks5, etc.) para URL válida."""
    s = (raw or "").strip()
    if not s:
        return ""

    if re.match(r"^(https?|socks5h?|socks4)://", s, re.I):
        return s

    if "@" in s:
        return f"http://{s}"

    parts = s.split(":")
    if len(parts) >= 4 and parts[1].isdigit():
        host, port, user = parts[0], parts[1], parts[2]
        passwd = ":".join(parts[3:])
        user_q = quote(user, safe="")
        pass_q = quote(passwd, safe="")
        return f"http://{user_q}:{pass_q}@{host}:{port}"

    if len(parts) == 2 and parts[1].isdigit():
        return f"http://{parts[0]}:{parts[1]}"

    if "://" not in s:
        return f"http://{s}"

    return s


def proxy_label(url: str) -> str:
    """Texto curto para UI (esconde senha)."""
    if not url:
        return ""
    try:
        p = urlparse(url if "://" in url else f"http://{url}")
        host = p.hostname or "?"
        port = f":{p.port}" if p.port else ""
        user = p.username or ""
        if user:
            return f"{user}@{host}{port}"
        return f"{host}{port}"
    except Exception:
        return "proxy"


def clean_sessionid(raw: str) -> str:
    """Limpa sessionid colado do navegador (URL-encoded ou cookie completo)."""
    sid = unquote(raw.strip())
    lower = sid.lower()
    if lower.startswith("sessionid="):
        sid = sid.split("=", 1)[1].strip()
    return sid
