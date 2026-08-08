"""Bloco de notas: guarda user/senha/2FA de contas IG (lote colado)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_effective_user, reject_view_as_secrets
from app.security import decrypt_secret, encrypt_secret
from app.templating import templates
from app.utils.account_notes_parse import parse_account_notes_blob
from app.utils.totp import TotpError, current_totp_code, normalize_totp_secret
from core.database import get_db, release_db_transaction
from models.models import AccountNote, User

router = APIRouter(prefix="/accounts/notes", tags=["account-notes"])


def _note_row(note: AccountNote, *, reveal: bool = False) -> dict:
    password = decrypt_secret(note.encrypted_password) if reveal else None
    has_totp = bool(note.encrypted_totp_secret)
    code = None
    remaining = None
    if reveal and has_totp:
        plain = decrypt_secret(note.encrypted_totp_secret)
        if plain:
            try:
                secret = normalize_totp_secret(plain)
                code, remaining = current_totp_code(secret)
            except TotpError:
                code, remaining = None, None
    return {
        "id": note.id,
        "username": note.username,
        "has_password": bool(note.encrypted_password),
        "has_totp": has_totp,
        "note": note.note or "",
        "password": password or "",
        "code": code,
        "remaining": remaining,
    }


@router.get("")
def notes_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    reject_view_as_secrets(request)
    notes = db.scalars(
        select(AccountNote)
        .where(AccountNote.user_id == user.id)
        .order_by(func.lower(AccountNote.username))
    ).all()
    release_db_transaction(db)
    return templates.TemplateResponse(
        "account_notes.html",
        {
            "request": request,
            "user": user,
            "notes": [_note_row(n) for n in notes],
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("error"),
            "imported": request.query_params.get("n"),
            "updated": request.query_params.get("u"),
        },
    )


@router.post("/import")
async def notes_import(
    request: Request,
    blob: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    reject_view_as_secrets(request)
    entries, warnings = parse_account_notes_blob(blob)
    if not entries:
        return templates.TemplateResponse(
            "account_notes.html",
            {
                "request": request,
                "user": user,
                "notes": [
                    _note_row(n)
                    for n in db.scalars(
                        select(AccountNote)
                        .where(AccountNote.user_id == user.id)
                        .order_by(func.lower(AccountNote.username))
                    ).all()
                ],
                "error": warnings[0] if warnings else "Nada para importar.",
                "warnings": warnings,
                "ok": None,
                "imported": None,
                "updated": None,
            },
            status_code=400,
        )

    existing = {
        n.username.lower(): n
        for n in db.scalars(
            select(AccountNote).where(AccountNote.user_id == user.id)
        ).all()
    }
    created = 0
    updated = 0
    for item in entries:
        key = item["username"].lower()
        note = existing.get(key)
        if note is None:
            note = AccountNote(user_id=user.id, username=item["username"])
            db.add(note)
            existing[key] = note
            created += 1
        else:
            updated += 1
            note.username = item["username"]
        note.encrypted_password = encrypt_secret(item["password"])
        if item.get("totp_secret"):
            note.encrypted_totp_secret = encrypt_secret(item["totp_secret"])
    db.commit()
    return RedirectResponse(
        f"/accounts/notes?ok=import&n={created}&u={updated}",
        status_code=303,
    )


@router.get("/codes")
def notes_codes(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    reject_view_as_secrets(request)
    notes = db.scalars(
        select(AccountNote).where(
            AccountNote.user_id == user.id,
            AccountNote.encrypted_totp_secret.isnot(None),
        )
    ).all()
    out = []
    for note in notes:
        plain = decrypt_secret(note.encrypted_totp_secret)
        if not plain:
            continue
        try:
            secret = normalize_totp_secret(plain)
            code, remaining = current_totp_code(secret)
        except TotpError:
            continue
        out.append(
            {
                "id": note.id,
                "username": note.username,
                "code": code,
                "remaining": remaining,
            }
        )
    return {"codes": out}


@router.get("/{note_id}/reveal")
def notes_reveal(
    note_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    reject_view_as_secrets(request)
    note = db.scalar(
        select(AccountNote).where(
            AccountNote.id == note_id,
            AccountNote.user_id == user.id,
        )
    )
    if note is None:
        raise HTTPException(404, detail="Conta não encontrada")
    return _note_row(note, reveal=True)


@router.post("/{note_id}/delete")
def notes_delete(
    note_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    reject_view_as_secrets(request)
    note = db.scalar(
        select(AccountNote).where(
            AccountNote.id == note_id,
            AccountNote.user_id == user.id,
        )
    )
    if note is None:
        raise HTTPException(404, detail="Conta não encontrada")
    db.delete(note)
    db.commit()
    if "application/json" in (request.headers.get("accept") or ""):
        return JSONResponse({"ok": True})
    return RedirectResponse("/accounts/notes?ok=deleted", status_code=303)
