"""Códigos / links de convite (banco) + fallback legacy INVITE_CODE."""
from __future__ import annotations

import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from models.models import InviteCode, User


def normalize_invite_code(code: str) -> str:
    return (code or "").strip().upper().replace(" ", "")


def generate_invite_code(length: int = 10) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def create_invite(
    db: Session,
    *,
    created_by: User,
    max_uses: int = 1,
    note: str = "",
) -> InviteCode:
    uses = max(1, min(int(max_uses or 1), 1000))
    for _ in range(12):
        code = generate_invite_code()
        exists = db.scalar(select(InviteCode.id).where(InviteCode.code == code))
        if exists:
            continue
        row = InviteCode(
            code=code,
            created_by_id=created_by.id,
            max_uses=uses,
            use_count=0,
            is_active=True,
            note=(note or "").strip()[:255] or None,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    raise RuntimeError("Não foi possível gerar código de convite único.")


def list_invites(db: Session, *, limit: int = 50) -> list[InviteCode]:
    return list(
        db.scalars(
            select(InviteCode)
            .order_by(InviteCode.created_at.desc())
            .limit(max(1, min(limit, 200)))
        ).all()
    )


def find_active_invite(db: Session, code: str) -> InviteCode | None:
    normalized = normalize_invite_code(code)
    if not normalized:
        return None
    row = db.scalar(select(InviteCode).where(InviteCode.code == normalized))
    if not row or not row.is_active:
        return None
    if int(row.use_count or 0) >= int(row.max_uses or 1):
        return None
    return row


def is_valid_invite_code(db: Session, code: str) -> bool:
    if find_active_invite(db, code) is not None:
        return True
    # Fallback legacy: INVITE_CODE env (uso ilimitado)
    expected = normalize_invite_code(settings.invite_code or "")
    if not expected:
        return False
    return normalize_invite_code(code) == expected


def consume_invite(db: Session, code: str, new_user: User) -> dict[str, Any]:
    """Marca uso do convite DB. Env legacy não precisa marcar."""
    import datetime as dt

    row = find_active_invite(db, code)
    if row is None:
        expected = normalize_invite_code(settings.invite_code or "")
        if expected and normalize_invite_code(code) == expected:
            return {"source": "env"}
        return {"source": "invalid"}
    row.use_count = int(row.use_count or 0) + 1
    row.used_by_id = new_user.id
    row.used_at = dt.datetime.utcnow()
    if row.use_count >= int(row.max_uses or 1):
        row.is_active = False
    db.commit()
    return {"source": "db", "invite_id": row.id}


def deactivate_invite(db: Session, invite_id: int) -> bool:
    row = db.get(InviteCode, invite_id)
    if not row:
        return False
    row.is_active = False
    db.commit()
    return True


def invite_public_url(request_base: str, code: str) -> str:
    base = (request_base or "").rstrip("/")
    return f"{base}/register?invite={normalize_invite_code(code)}"
