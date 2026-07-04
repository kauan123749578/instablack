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


def bootstrap_admin() -> None:
    """Cria usuário admin se BOOTSTRAP_ADMIN_* estiver definido e não existir ninguém.

    Tolera corrida entre múltiplos workers (o segundo que tentar inserir apenas
    encontra o admin já criado).
    """
    username = (settings.bootstrap_admin_username or "").strip().lower()
    password = settings.bootstrap_admin_password or ""
    if not username or not password:
        return

    with session_scope() as db:
        existing = db.scalar(select(User).where(User.username == username))
        if existing:
            if settings.bootstrap_admin_is_admin and not existing.is_admin:
                existing.is_admin = True
            return

        db.add(
            User(
                username=username,
                password_hash=hash_password(password),
                display_name=username,
                is_admin=settings.bootstrap_admin_is_admin,
                account_limit=None,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            log.info("Admin já criado por outro worker; seguindo.")
            return
        log.info("Usuário bootstrap criado: %s", username)
