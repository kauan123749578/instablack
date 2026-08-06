"""Rate limit de autenticação (login/register) via Redis."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_LOGIN_MAX = 10
_LOGIN_WINDOW_SEC = 600  # 10 minutos
_REGISTER_MAX = 20
_REGISTER_WINDOW_SEC = 3600


def _redis() -> Any | None:
    try:
        from redis import Redis

        from app.config import settings

        return Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
    except Exception as exc:
        log.warning("rate_limit: Redis indisponível — %s", exc)
        return None


def _client_ip(request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded[:64]
    if request.client and request.client.host:
        return str(request.client.host)[:64]
    return "unknown"


def check_auth_rate_limit(
    request,
    *,
    action: str,
    username: str = "",
) -> tuple[bool, int]:
    """Retorna (allowed, retry_after_seconds).

    Se Redis cair, libera (fail-open) para não derrubar o painel — mas loga.
    """
    client = _redis()
    if client is None:
        return True, 0

    ip = _client_ip(request)
    user_part = (username or "").strip().lower()[:64] or "-"
    if action == "login":
        key = f"auth:rl:login:{ip}:{user_part}"
        limit, window = _LOGIN_MAX, _LOGIN_WINDOW_SEC
    else:
        key = f"auth:rl:register:{ip}"
        limit, window = _REGISTER_MAX, _REGISTER_WINDOW_SEC

    try:
        count = int(client.incr(key))
        if count == 1:
            client.expire(key, window)
        ttl = int(client.ttl(key) or window)
        if count > limit:
            return False, max(1, ttl)
        return True, 0
    except Exception as exc:
        log.warning("rate_limit check falhou: %s", exc)
        return True, 0


def clear_login_rate_limit(request, username: str) -> None:
    """Limpa contador após login bem-sucedido."""
    client = _redis()
    if client is None:
        return
    ip = _client_ip(request)
    user_part = (username or "").strip().lower()[:64] or "-"
    try:
        client.delete(f"auth:rl:login:{ip}:{user_part}")
    except Exception:
        pass
