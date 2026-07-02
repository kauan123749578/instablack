"""Formatadores para templates."""
from __future__ import annotations


def format_interval(minutes: int) -> str:
    if minutes < 60:
        return f"A cada {minutes} min"
    if minutes == 60:
        return "A cada 1 hora"
    if minutes < 1440:
        h = minutes // 60
        return f"A cada {h} horas"
    return "A cada 24 horas"


def status_label(status: str) -> str:
    return {
        "active": "Ativa",
        "paused": "Pausada",
        "needs_login": "Sessão expirando",
        "proxy_down": "Desconectada",
        "banned": "Banida",
        "success": "Sucesso",
        "failed": "Erro",
        "skipped": "Ignorada",
    }.get(status, status)


def status_badge_class(status: str) -> str:
    if status in ("active", "success"):
        return "badge-green"
    if status in ("paused", "skipped", "needs_login"):
        return "badge-yellow"
    if status in ("failed", "proxy_down", "banned"):
        return "badge-red"
    return "badge-neutral"
