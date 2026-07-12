"""Formatadores para templates."""
from __future__ import annotations


def format_interval(minutes: int) -> str:
    from app.utils.intervals import interval_label
    return "A cada " + interval_label(minutes).lower()


def format_count(n: int | None) -> str:
    if n is None:
        return "—"
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{v:.1f}M".replace(".0M", "M")
    if n >= 10_000:
        v = n / 1_000
        return f"{v:.1f}k".replace(".0k", "k")
    if n >= 1_000:
        return f"{n / 1_000:.1f}k".replace(".0k", "k")
    return str(n)


def status_label(status: str) -> str:
    return {
        "active": "Ativa",
        "paused": "Pausada",
        "completed": "Concluída",
        "pending": "Na fila",
        "running": "Aquecendo",
        "done": "Concluído",
        "needs_login": "Sessão expirada",
        "proxy_down": "Proxy offline",
        "banned": "Banida",
        "success": "Sucesso",
        "failed": "Erro",
        "skipped": "Ignorada",
    }.get(status, status)


def status_badge_class(status: str) -> str:
    if status in ("active", "success", "running", "done"):
        return "badge-green"
    if status in ("paused", "skipped", "needs_login", "pending"):
        return "badge-yellow"
    if status in ("failed", "proxy_down", "banned"):
        return "badge-red"
    return "badge-neutral"
