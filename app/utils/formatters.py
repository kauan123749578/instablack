"""Formatadores para templates."""
from __future__ import annotations


def format_interval(minutes: int) -> str:
    from app.utils.intervals import interval_label
    return "A cada " + interval_label(minutes).lower()


def status_label(status: str) -> str:
    return {
        "active": "Ativa",
        "paused": "Pausada",
        "needs_login": "Sessão expirada",
        "proxy_down": "Proxy offline",
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
