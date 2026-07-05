"""Normalização de strings de proxy para o formato aceito pelo instagrapi."""
from __future__ import annotations


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
