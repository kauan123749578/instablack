"""Autenticador TOTP independente — chaves nomeadas pelo usuário."""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_current_user
from app.security import decrypt_secret, encrypt_secret
from app.templating import templates
from app.utils.totp import TotpError, current_totp_code, normalize_totp_secret
from core.database import get_db
from models.models import AuthenticatorEntry, User

router = APIRouter(prefix="/authenticator", tags=["authenticator"])


def _totp_code(secret: str) -> tuple[str, int]:
    """Gera o código TOTP de 6 dígitos e retorna (code, seconds_remaining)."""
    try:
        sec = normalize_totp_secret(secret)
        code, remaining = current_totp_code(sec)
        return code, remaining
    except (TotpError, Exception):
        return "------", 30 - int(time.time()) % 30


# ---------- schemas ----------

class EntryCreate(BaseModel):
    name: str
    secret: str


class EntryRename(BaseModel):
    name: str


# ---------- page ----------

@router.get("/page")
def authenticator_page(
    request: Request,
    user: User = Depends(get_current_user),
):
    return templates.TemplateResponse(
        "vault_authenticator.html",
        {"request": request, "user": user},
    )


# ---------- endpoints ----------

@router.get("")
def list_entries(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    entries = (
        db.query(AuthenticatorEntry)
        .filter(AuthenticatorEntry.user_id == user.id)
        .order_by(AuthenticatorEntry.created_at)
        .all()
    )
    codes: list[dict[str, Any]] = []
    ts = int(time.time())
    remaining = 30 - ts % 30
    for e in entries:
        plain = decrypt_secret(e.encrypted_secret) if e.encrypted_secret else ""
        code, rem = _totp_code(plain) if plain else ("------", remaining)
        codes.append({"id": e.id, "name": e.name, "code": code, "remaining": rem})
    return JSONResponse(codes)


@router.post("")
def create_entry(
    body: EntryCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    name = (body.name or "").strip()
    secret = (body.secret or "").strip().upper().replace(" ", "")
    if not name:
        raise HTTPException(400, "Nome é obrigatório.")
    if not secret:
        raise HTTPException(400, "Chave secreta é obrigatória.")
    # Valida o secret
    code, _ = _totp_code(secret)
    if code == "------":
        raise HTTPException(400, "Chave secreta inválida. Cole a chave Base32, não o código de 6 dígitos.")
    entry = AuthenticatorEntry(
        user_id=user.id,
        name=name,
        encrypted_secret=encrypt_secret(secret),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    ts = int(time.time())
    remaining = 30 - ts % 30
    c, rem = _totp_code(secret)
    return JSONResponse({"id": entry.id, "name": entry.name, "code": c, "remaining": rem})


@router.patch("/{entry_id}/rename")
def rename_entry(
    entry_id: int,
    body: EntryRename,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    entry = db.query(AuthenticatorEntry).filter(
        AuthenticatorEntry.id == entry_id,
        AuthenticatorEntry.user_id == user.id,
    ).first()
    if not entry:
        raise HTTPException(404, "Entrada não encontrada.")
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Nome não pode ficar vazio.")
    entry.name = name
    db.commit()
    return JSONResponse({"ok": True, "id": entry.id, "name": entry.name})


@router.delete("/{entry_id}")
def delete_entry(
    entry_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    entry = db.query(AuthenticatorEntry).filter(
        AuthenticatorEntry.id == entry_id,
        AuthenticatorEntry.user_id == user.id,
    ).first()
    if not entry:
        raise HTTPException(404, "Entrada não encontrada.")
    db.delete(entry)
    db.commit()
    return JSONResponse({"ok": True})


@router.get("/codes")
def live_codes(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Endpoint de polling — retorna todos os códigos TOTP atualizados."""
    entries = (
        db.query(AuthenticatorEntry)
        .filter(AuthenticatorEntry.user_id == user.id)
        .order_by(AuthenticatorEntry.created_at)
        .all()
    )
    ts = int(time.time())
    remaining = 30 - ts % 30
    codes = []
    for e in entries:
        plain = decrypt_secret(e.encrypted_secret) if e.encrypted_secret else ""
        code, rem = _totp_code(plain) if plain else ("------", remaining)
        codes.append({"id": e.id, "name": e.name, "code": code, "remaining": rem})
    return JSONResponse(codes)
