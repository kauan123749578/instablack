"""Acesso à API não oficial (Instagrapi: senha / sessionid / session.json).

A UI fica visível para todos. O login de verdade só funciona para o dono
e usuários com `allow_instagrapi`. Demais recebem falha de login “realista”
após alguns segundos (parece API fora / credenciais rejeitadas).

Contas Instagrapi já conectadas de quem não está liberado são marcadas
como sessão expirada (needs_login) e a sessão mobile é invalidada.
"""
from __future__ import annotations

import random
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.models import InstagramAccount, User

INSTAGRAPI_AUTH_METHODS = frozenset({"password", "sessionid", "import", "aiograpi"})

INSTAGRAPI_EXPIRED_MSG = (
    "Sessão expirada — o login clássico não está disponível. "
    "Reconecte pela API oficial (Meta) ou cookies web."
)

# Mensagens que parecem falha real do Instagram (não revelam o gate).
FAKE_LOGIN_ERRORS = (
    "Login falhou: usuário ou senha incorretos, ou a sessão foi rejeitada pelo Instagram.",
    "Não foi possível autenticar no Instagram (login failed). Tente novamente mais tarde.",
    "Falha no login: challenge/sessão expirada. O Instagram não aceitou a autenticação.",
    "Erro ao conectar: o serviço de login não respondeu a tempo. Tente de novo ou use a API oficial (Meta).",
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


def is_instagrapi_mobile_account(acc: InstagramAccount) -> bool:
    """Conta que depende do Instagrapi mobile (não Meta e sem cookies web)."""
    if (getattr(acc, "provider", None) or "instagrapi") == "meta":
        return False
    try:
        from core.web_cookies import web_cookies_status

        st = web_cookies_status(getattr(acc, "encrypted_web_cookies", None))
        if st.get("has_csrftoken"):
            return False
    except Exception:
        pass
    return True


def revoke_unauthorized_instagrapi_accounts(
    db: Session,
    user: User | None,
) -> dict | None:
    """Desliga contas Instagrapi de quem não está liberado.

    Marca needs_login, apaga session_json (sessão mobile) e devolve aviso
    para o painel. Meta e cookies web não são tocados.
    """
    if user is None or can_use_instagrapi(user):
        return None

    from app.utils.account_health import VISIBLE_ACCOUNT_STATUSES

    accounts = list(
        db.scalars(
            select(InstagramAccount).where(
                InstagramAccount.user_id == user.id,
                InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
            )
        ).all()
    )
    affected = [a for a in accounts if is_instagrapi_mobile_account(a)]
    if not affected:
        return None

    dirty = False
    for acc in affected:
        changed = False
        if acc.status != "needs_login":
            acc.status = "needs_login"
            changed = True
        if (acc.last_error or "") != INSTAGRAPI_EXPIRED_MSG:
            acc.last_error = INSTAGRAPI_EXPIRED_MSG
            changed = True
        if acc.session_json:
            acc.session_json = None
            changed = True
        if changed:
            dirty = True
    if dirty:
        db.commit()

    usernames = [a.username for a in affected if a.username]
    return {
        "count": len(affected),
        "usernames": usernames[:12],
        "message": INSTAGRAPI_EXPIRED_MSG,
    }


def revoke_all_unauthorized_instagrapi(db: Session) -> int:
    """Revoga Instagrapi de todos os usuários sem allow_instagrapi (exceto owners)."""
    users = list(
        db.scalars(
            select(User).where(
                User.is_active.is_(True),
                User.is_owner.is_(False),
            )
        ).all()
    )
    total = 0
    for u in users:
        if can_use_instagrapi(u):
            continue
        notice = revoke_unauthorized_instagrapi_accounts(db, u)
        if notice:
            total += int(notice.get("count") or 0)
    return total
