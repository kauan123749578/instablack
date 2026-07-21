"""Bootstrap inicial em produção (primeiro admin)."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.security import hash_password
from core.database import session_scope
from models.models import User

log = logging.getLogger(__name__)


def _owner_username() -> str:
    return (settings.owner_username or "").strip().lower()


def sync_owner() -> None:
    """OWNER_USERNAME é o único owner: marca ele e desmarca qualquer outro.

    Se o username configurado não existir no banco, NÃO mexe em is_owner
    (evita demotar o dono real quando OWNER_USERNAME está errado/desatualizado).
    """
    owner = _owner_username()
    if not owner:
        return
    with session_scope() as db:
        user = db.scalar(select(User).where(User.username == owner))
        if not user:
            log.warning(
                "OWNER_USERNAME='%s' não existe no banco — flags is_owner intactas. "
                "Ajuste OWNER_USERNAME no Railway para o seu @ de login.",
                owner,
            )
            return
        others = db.scalars(
            select(User).where(User.is_owner.is_(True), User.username != owner)
        ).all()
        for u in others:
            u.is_owner = False
            log.info("Flag de owner removida de '%s' (owner atual: '%s').", u.username, owner)
        if not getattr(user, "is_owner", False) or not user.is_admin:
            user.is_owner = True
            user.is_admin = True
            log.info("Usuário '%s' marcado como owner da plataforma.", owner)


def bootstrap_admin() -> None:
    """Cria usuário admin se BOOTSTRAP_ADMIN_* estiver definido e não existir ninguém.

    Tolera corrida entre múltiplos workers (o segundo que tentar inserir apenas
    encontra o admin já criado).
    """
    username = (settings.bootstrap_admin_username or "").strip().lower()
    password = settings.bootstrap_admin_password or ""
    if not username or not password:
        sync_owner()
        return

    with session_scope() as db:
        existing = db.scalar(select(User).where(User.username == username))
        if existing:
            if settings.bootstrap_admin_is_admin and not existing.is_admin:
                existing.is_admin = True
            if username == _owner_username() and not getattr(existing, "is_owner", False):
                existing.is_owner = True
                existing.is_admin = True
            if settings.bootstrap_admin_reset:
                existing.password_hash = hash_password(password)
                existing.is_active = True
                log.warning(
                    "Senha do usuário '%s' foi RESETADA via BOOTSTRAP_ADMIN_RESET. "
                    "Desligue essa variável após recuperar o acesso.",
                    username,
                )
            sync_owner()
            return

        db.add(
            User(
                username=username,
                password_hash=hash_password(password),
                display_name=username,
                is_admin=settings.bootstrap_admin_is_admin,
                is_owner=(username == _owner_username()),
                account_limit=None,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            log.info("Admin já criado por outro worker; seguindo.")
            sync_owner()
            return
        log.info("Usuário bootstrap criado: %s", username)
    sync_owner()
