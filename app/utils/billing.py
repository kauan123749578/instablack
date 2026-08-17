"""Bloqueio de mensalidade: painel travado até o suporte liberar."""
from __future__ import annotations

from typing import Any


def is_panel_blocked(user: Any | None) -> bool:
    """Owner nunca trava. Demais: flag billing_blocked no admin."""
    if user is None:
        return False
    if bool(getattr(user, "is_owner", False)):
        return False
    return bool(getattr(user, "billing_blocked", False))
