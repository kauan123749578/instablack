"""Login / Registro / Logout."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.security import hash_password, verify_password
from app.templating import templates
from core.database import get_db
from models.models import User

router = APIRouter(tags=["auth"])


@router.get("/login")
def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None, "allow_registration": settings.allow_registration},
    )


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    username_norm = username.strip().lower()
    user = db.scalar(select(User).where(User.username == username_norm))
    if not user or not verify_password(password, user.password_hash) or not user.is_active:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Usuário ou senha inválidos.",
                "allow_registration": settings.allow_registration,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/register")
def register_page(request: Request):
    if not settings.allow_registration:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("register.html", {"request": request, "error": None})


@router.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    if not settings.allow_registration:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    username_norm = username.strip().lower()
    error: str | None = None
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
            {"request": request, "error": error},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user = User(username=username_norm, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
