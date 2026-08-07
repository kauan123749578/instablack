"""Login / Registro / Logout."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, status
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


def _issue_exclusive_session(request: Request, db: Session, user: User) -> None:
    """Novo login invalida todas as sessões anteriores (anti-compartilhamento)."""
    user.session_version = int(getattr(user, "session_version", 0) or 0) + 1
    db.add(user)
    db.commit()
    db.refresh(user)
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["session_version"] = int(user.session_version or 0)
    ensure_csrf_token(request)


SESSION_KICKED_MSG = (
    "Sua sessão foi encerrada porque alguém entrou nesta conta em outro dispositivo. "
    "Se não foi você, troque a senha no perfil."
)

async def _form_str(request: Request, key: str, default: str = "") -> str:
    """Lê campo do form. Preferência: request.form(); fallback: body urlencoded."""
    try:
        form = await request.form()
        raw = form.get(key)
        if raw is not None and not hasattr(raw, "filename"):
            val = str(raw)
            if val != "" or key in form:
                return val
    except Exception:
        pass
    try:
        from urllib.parse import parse_qs

        body = await request.body()
        if not body:
            return default
        parsed = parse_qs(body.decode("utf-8", errors="ignore"), keep_blank_values=True)
        vals = parsed.get(key) or []
        return str(vals[0]) if vals else default
    except Exception:
        return default


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
    error = None
    if (request.query_params.get("reason") or "").strip().lower() == "session":
        error = SESSION_KICKED_MSG
    return templates.TemplateResponse(
        "login.html",
        _auth_page_ctx(request, error=error),
    )


@router.post("/login")
async def login(
    request: Request,
    db: Session = Depends(get_db),
):
    username = await _form_str(request, "username")
    password = await _form_str(request, "password")
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
    _issue_exclusive_session(request, db, user)
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
async def register(
    request: Request,
    db: Session = Depends(get_db),
):
    if not settings.allow_registration:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    username = await _form_str(request, "username")
    password = await _form_str(request, "password")
    password_confirm = await _form_str(request, "password_confirm")
    invite_code = await _form_str(request, "invite_code")

    allowed, retry_after = check_auth_rate_limit(request, action="register")
    invite_norm = normalize_invite_code(invite_code)
    if not allowed:
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
    error: str | None = None

    if not (username or password or invite_code):
        error = "Formulário incompleto. Recarregue a página do convite e tente de novo."
    elif not is_valid_invite_code(db, invite_norm):
        error = "Código de convite inválido ou esgotado."
    elif not username_norm or len(username_norm) < 3:
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
    _issue_exclusive_session(request, db, user)
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
