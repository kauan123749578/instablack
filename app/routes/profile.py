"""Perfil do usuário do painel (SaaS)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.deps import get_current_user
from app.security import hash_password
from app.templating import templates
from core.database import get_db
from core.storage import get_storage
from models.models import User

router = APIRouter(prefix="/perfil", tags=["perfil"])


@router.get("")
def profile_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return templates.TemplateResponse(
        "profile.html",
        {"request": request, "user": user, "error": None, "ok": None},
    )


@router.post("")
async def profile_update(
    request: Request,
    display_name: str = Form(""),
    avatar: UploadFile | None = File(None),
    new_password: str = Form(""),
    new_password_confirm: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    error: str | None = None
    if new_password or new_password_confirm:
        if len(new_password) < 8:
            error = "A nova senha precisa ter pelo menos 8 caracteres."
        elif new_password != new_password_confirm:
            error = "As senhas não conferem."

    if error:
        return templates.TemplateResponse(
            "profile.html",
            {"request": request, "user": user, "error": error, "ok": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user.display_name = display_name.strip() or None

    if new_password:
        user.password_hash = hash_password(new_password)

    if avatar and avatar.filename:
        storage = get_storage()
        ext = Path(avatar.filename).suffix or ".jpg"
        if user.avatar_key:
            try:
                storage.delete(user.avatar_key)
            except Exception:
                pass
        user.avatar_key = storage.save(avatar.file, suggested_ext=ext)

    db.commit()
    db.refresh(user)
    return templates.TemplateResponse(
        "profile.html",
        {"request": request, "user": user, "error": None, "ok": "Perfil atualizado com sucesso!"},
    )
