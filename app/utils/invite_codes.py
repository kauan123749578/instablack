"""Código de convite fixo via variável de ambiente INVITE_CODE."""
from __future__ import annotations

from app.config import settings


def normalize_invite_code(code: str) -> str:
    return code.strip().upper().replace(" ", "")


def is_valid_invite_code(code: str) -> bool:
    expected = normalize_invite_code(settings.invite_code or "")
    if not expected:
        return False
    return normalize_invite_code(code) == expected
