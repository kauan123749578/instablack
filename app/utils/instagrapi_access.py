"""Acesso à API não oficial (Instagrapi: senha / sessionid / session.json).

A UI fica visível para todos. O login de verdade só funciona para o dono
e usuários com `allow_instagrapi`. Demais recebem falha de login “realista”
após alguns segundos (parece API fora / credenciais rejeitadas).
"""
from __future__ import annotations

import random
import time

from models.models import User

INSTAGRAPI_AUTH_METHODS = frozenset({"password", "sessionid", "import"})

# Mensagens que parecem falha real do Instagram/Instagrapi (não revelam o gate).
FAKE_LOGIN_ERRORS = (
    "Login falhou: usuário ou senha incorretos, ou a sessão foi rejeitada pelo Instagram.",
    "Não foi possível autenticar no Instagram (login failed). Tente novamente mais tarde.",
    "Falha no login Instagrapi: challenge/sessão expirada. O Instagram não aceitou a autenticação.",
    "Erro ao conectar: a API Instagrapi não respondeu a tempo. Tente de novo ou use a API oficial (Meta).",
)


def can_use_instagrapi(user: User | None) -> bool:
    if user is None:
        return False
    if bool(getattr(user, "is_owner", False)):
        return True
    return bool(getattr(user, "allow_instagrapi", False))


def is_instagrapi_auth_method(method: str | None) -> bool:
    return (method or "").strip().lower() in INSTAGRAPI_AUTH_METHODS


def fake_instagrapi_login_delay() -> None:
    """Simula tentativa de login (2,5–4,5s) antes do erro."""
    time.sleep(random.uniform(2.5, 4.5))


def fake_instagrapi_login_error() -> str:
    return random.choice(FAKE_LOGIN_ERRORS)
