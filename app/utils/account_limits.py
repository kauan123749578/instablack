"""Limites de contas Instagram por usuário (controlado pelo admin)."""
from __future__ import annotations

from models.models import User


def account_limit_label(limit: int | None) -> str:
    if limit is None:
        return "Ilimitado"
    return str(limit)


def accounts_remaining(user: User, current_count: int) -> int | None:
    """Retorna quantas contas ainda pode adicionar. None = ilimitado."""
    if user.account_limit is None:
        return None
    return max(0, user.account_limit - current_count)


def can_add_instagram_account(user: User, current_count: int) -> tuple[bool, str | None]:
    """Verifica se o usuário pode adicionar mais uma conta (nova)."""
    if user.account_limit is None:
        return True, None
    if current_count >= user.account_limit:
        return False, (
            f"Limite de {user.account_limit} conta(s) Instagram atingido. "
            "Peça ao administrador para liberar mais vagas."
        )
    return True, None
