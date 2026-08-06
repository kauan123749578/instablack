"""Dependências compartilhadas do FastAPI."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.exc import OperationalError, TimeoutError as SATimeoutError
from sqlalchemy.orm import Session

from core.database import get_db
from models.models import User

log = logging.getLogger(__name__)


def _db_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="database_unavailable",
        headers={"Retry-After": "2"},
    )


def get_auth_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    """Usuário real da sessão (nunca o alvo do 'Ver como')."""
    user_id = request.session.get("user_id")
    if not user_id:
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                headers={"Location": "/login"},
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Não autenticado")

    try:
        user = db.get(User, user_id)
    except (OperationalError, SATimeoutError) as exc:
        log.warning("get_auth_user DB indisponível: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        raise _db_unavailable() from exc

    if user is None or not user.is_active:
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    # Cookie antigo após troca de senha → força re-login
    cookie_ver = request.session.get("session_version")
    db_ver = int(getattr(user, "session_version", 0) or 0)
    try:
        cookie_ver_int = int(cookie_ver) if cookie_ver is not None else 0
    except (TypeError, ValueError):
        cookie_ver_int = -1
    if cookie_ver_int != db_ver:
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    return user


def maybe_auth_user(
    request: Request, db: Session = Depends(get_db)
) -> Optional[User]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    try:
        user = db.get(User, user_id)
    except (OperationalError, SATimeoutError) as exc:
        # Não derruba GET / com 500 — trata como sessão ausente e responde rápido.
        log.warning("maybe_auth_user DB indisponível — fallback anon: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return None
    if user is None or not user.is_active:
        return None
    cookie_ver = request.session.get("session_version")
    db_ver = int(getattr(user, "session_version", 0) or 0)
    try:
        cookie_ver_int = int(cookie_ver) if cookie_ver is not None else 0
    except (TypeError, ValueError):
        cookie_ver_int = -1
    if cookie_ver_int != db_ver:
        request.session.clear()
        return None
    return user


def _resolve_effective(request: Request, db: Session, auth_user: User) -> User:
    view_as_id = request.session.get("view_as_user_id")
    if not view_as_id or not getattr(auth_user, "is_admin", False):
        return auth_user
    try:
        target_id = int(view_as_id)
    except (TypeError, ValueError):
        request.session.pop("view_as_user_id", None)
        return auth_user
    try:
        target = db.get(User, target_id)
    except (OperationalError, SATimeoutError) as exc:
        log.warning("view_as resolve DB indisponível: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return auth_user
    if target is None or not target.is_active or target.id == auth_user.id:
        request.session.pop("view_as_user_id", None)
        return auth_user
    # Admin não-owner não vê o owner nem usuários marcados como privados do owner
    if not getattr(auth_user, "is_owner", False):
        if getattr(target, "is_owner", False) or getattr(target, "owner_private", False):
            request.session.pop("view_as_user_id", None)
            return auth_user
    return target


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    auth_user: User = Depends(get_auth_user),
) -> User:
    """Usuário efetivo: alvo do 'Ver como' (se ativo) ou o autenticado.

    Quase todas as rotas do painel devem usar isto para o owner/admin
    enxergar contas, automações, logs etc. do usuário visualizado.
    """
    return _resolve_effective(request, db, auth_user)


def maybe_current_user(
    request: Request, db: Session = Depends(get_db)
) -> Optional[User]:
    auth = maybe_auth_user(request, db)
    if auth is None or not auth.is_active:
        return None
    return _resolve_effective(request, db, auth)


def get_effective_user(
    request: Request,
    db: Session = Depends(get_db),
    auth_user: User = Depends(get_auth_user),
) -> User:
    """Alias de get_current_user (compatibilidade)."""
    return _resolve_effective(request, db, auth_user)


def maybe_effective_user(
    request: Request, db: Session = Depends(get_db)
) -> Optional[User]:
    return maybe_current_user(request, db)


def view_as_active(request: Request) -> bool:
    return bool(request.session.get("view_as_user_id"))


def get_admin_user(
    user: User = Depends(get_auth_user),
) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/"},
        )
    return user


def get_owner_user(
    user: User = Depends(get_auth_user),
) -> User:
    """Só o dono (is_owner) gerencia a lista de usuários."""
    if not user.is_admin or not getattr(user, "is_owner", False):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/admin" if user.is_admin else "/"},
        )
    return user


def get_owner_only(
    user: User = Depends(get_auth_user),
) -> User:
    """Acesso exclusivo ao dono (is_owner). Admin sem owner (ex.: Caue) é bloqueado."""
    if not getattr(user, "is_owner", False):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/"},
        )
    return user
