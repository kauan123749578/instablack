"""Acesso à API não oficial (Instagrapi: senha / sessionid / session.json).

Só o dono e usuários liberados no admin (`allow_instagrapi`) podem conectar
por esses métodos. Meta e cookies web continuam abertos para todos.

Usuários com contas Instagrapi legadas (sem cookies web) veem aviso de
sessão expirada / API fora do ar e as contas são marcadas needs_login.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.models import InstagramAccount, User

INSTAGRAPI_AUTH_METHODS = frozenset({"password", "sessionid", "import"})

INSTAGRAPI_DOWN_MSG = (
    "API não oficial (Instagrapi) indisponível — sessão expirada / API caiu. "
    "Use API oficial (Meta) ou cookies web."
)


def can_use_instagrapi(user: User | None) -> bool:
    if user is None:
        return False
    if bool(getattr(user, "is_owner", False)):
        return True
    return bool(getattr(user, "allow_instagrapi", False))


def is_instagrapi_auth_method(method: str | None) -> bool:
    return (method or "").strip().lower() in INSTAGRAPI_AUTH_METHODS


def is_instagrapi_mobile_account(acc: InstagramAccount) -> bool:
    """Conta que depende do Instagrapi (não Meta e sem cookies web completos)."""
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


def sync_instagrapi_down_notice(db: Session, user: User | None) -> dict | None:
    """Marca contas Instagrapi legadas como expiradas e monta o aviso do painel.

    Retorna None se o usuário pode usar Instagrapi ou não tem essas contas.
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
        if acc.status != "needs_login" or (acc.last_error or "") != INSTAGRAPI_DOWN_MSG:
            acc.status = "needs_login"
            acc.last_error = INSTAGRAPI_DOWN_MSG
            dirty = True
    if dirty:
        db.commit()

    usernames = [a.username for a in affected if a.username]
    return {
        "count": len(affected),
        "usernames": usernames[:12],
        "message": INSTAGRAPI_DOWN_MSG,
    }
