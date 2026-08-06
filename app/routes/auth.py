"""Login / Registro / Logout."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.rate_limit import check_auth_rate_limit, clear_login_rate_limit
from app.security import hash_password, verify_password
from app.security_http import ensure_csrf_token
from app.templating import templates
from app.utils.invite_codes import consume_invite, is_valid_invite_code, normalize_invite_code
from core.database import get_db
from models.models import User

router = APIRouter(tags=["auth"])
log = logging.getLogger(__name__)


def _auth_page_ctx(request: Request, **extra):
    ensure_csrf_token(request)
    ctx = {
        "request": request,
        "csrf_token": request.session.get("csrf_token", ""),
        "allow_registration": settings.allow_registration,
    }
    ctx.update(extra)
    return ctx


@router.get("/login")
def login_page(request: Request, db: Session = Depends(get_db)):
    """Mostra login. Nunca redireciona pra / só por cookie — isso + DB down = loop."""
    uid = request.session.get("user_id")
    if uid:
        try:
            user = db.get(User, int(uid))
            if user is not None and user.is_active:
                cookie_ver = request.session.get("session_version")
                db_ver = int(getattr(user, "session_version", 0) or 0)
                try:
                    ok_ver = int(cookie_ver) if cookie_ver is not None else 0
                except (TypeError, ValueError):
                    ok_ver = -1
                if ok_ver == db_ver:
                    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
        except Exception as exc:
            log.warning("login_page: sessão presente mas DB falhou — limpando cookie: %s", exc)
            try:
                db.rollback()
            except Exception:
                pass
        request.session.clear()
    return templates.TemplateResponse(
        "login.html",
        _auth_page_ctx(request, error=None),
    )


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    username_norm = username.strip().lower()
    allowed, retry_after = check_auth_rate_limit(
        request, action="login", username=username_norm
    )
    if not allowed:
        return templates.TemplateResponse(
            "login.html",
            _auth_page_ctx(
                request,
                error=(
                    f"Muitas tentativas. Aguarde {max(1, retry_after // 60)} min "
                    f"({retry_after}s) e tente de novo."
                ),
            ),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after)},
        )

    try:
        user = db.scalar(select(User).where(User.username == username_norm))
    except Exception as exc:
        log.warning("login DB erro: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return templates.TemplateResponse(
            "login.html",
            _auth_page_ctx(
                request,
                error="Banco temporariamente indisponível. Tente de novo em alguns segundos.",
            ),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if not user or not verify_password(password, user.password_hash) or not user.is_active:
        return templates.TemplateResponse(
            "login.html",
            _auth_page_ctx(request, error="Usuário ou senha inválidos."),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    clear_login_rate_limit(request, username_norm)
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["session_version"] = int(getattr(user, "session_version", 0) or 0)
    ensure_csrf_token(request)
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/register")
def register_page(request: Request):
    if not settings.allow_registration:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    invite_prefill = normalize_invite_code(request.query_params.get("invite") or "")
    return templates.TemplateResponse(
        "register.html",
        _auth_page_ctx(request, error=None, invite_prefill=invite_prefill),
    )


@router.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    invite_code: str = Form(...),
    db: Session = Depends(get_db),
):
    if not settings.allow_registration:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    allowed, retry_after = check_auth_rate_limit(request, action="register")
    if not allowed:
        invite_norm = normalize_invite_code(invite_code)
        return templates.TemplateResponse(
            "register.html",
            _auth_page_ctx(
                request,
                error=f"Muitas tentativas de cadastro. Aguarde {retry_after}s.",
                invite_prefill=invite_norm,
            ),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after)},
        )

    username_norm = username.strip().lower()
    invite_norm = normalize_invite_code(invite_code)
    error: str | None = None

    if not is_valid_invite_code(db, invite_norm):
        error = "Código de convite inválido ou esgotado."

    if not error:
        if not username_norm or len(username_norm) < 3:
            error = "Informe um usuário com pelo menos 3 caracteres."
        elif len(password) < 8:
            error = "A senha precisa ter pelo menos 8 caracteres."
        elif password != password_confirm:
            error = "As senhas não conferem."
        elif db.scalar(select(User).where(User.username == username_norm)) is not None:
            error = "Já existe um usuário com esse nome."

    if error:
        return templates.TemplateResponse(
            "register.html",
            _auth_page_ctx(request, error=error, invite_prefill=invite_norm),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user = User(
        username=username_norm,
        password_hash=hash_password(password),
        account_limit=settings.default_account_limit,
        session_version=0,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    consume_invite(db, invite_norm, user)
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["session_version"] = int(getattr(user, "session_version", 0) or 0)
    ensure_csrf_token(request)
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
