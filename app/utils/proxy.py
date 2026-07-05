"""Normalização de strings de proxy para o formato aceito pelo instagrapi."""
from __future__ import annotations

from urllib.parse import unquote


def normalize_proxy(raw: str) -> str:
    """Converte host:porta:user:senha ou host:porta para http://user:pass@host:port."""
    value = raw.strip()
    if not value:
        return ""

    if "://" in value:
        return value

    parts = value.split(":")
    if len(parts) == 4:
        host, port, user, password = parts
        return f"http://{user}:{password}@{host}:{port}"
    if len(parts) == 2:
        host, port = parts
        return f"http://{host}:{port}"

    return value


def clean_sessionid(raw: str) -> str:
    """Limpa sessionid colado do navegador (URL-encoded ou cookie completo)."""
    sid = unquote(raw.strip())
    lower = sid.lower()
    if lower.startswith("sessionid="):
        sid = sid.split("=", 1)[1].strip()
    return sid
