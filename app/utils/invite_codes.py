"""Códigos de convite para registro no SaaS."""
from __future__ import annotations

import datetime as dt
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.models import InviteCode


def normalize_invite_code(code: str) -> str:
    return code.strip().upper().replace(" ", "")


def generate_invite_code() -> str:
    """Gera código legível: IB-A1B2C3D4."""
    return f"IB-{secrets.token_hex(4).upper()}"


def get_valid_invite(db: Session, code: str) -> InviteCode | None:
    norm = normalize_invite_code(code)
    if not norm:
        return None
    invite = db.scalar(select(InviteCode).where(InviteCode.code == norm))
    if invite is None or not invite.is_active:
        return None
    if invite.use_count >= invite.max_uses:
        return None
    return invite


def consume_invite(db: Session, invite: InviteCode, user_id: int) -> None:
    invite.use_count += 1
    if invite.use_count >= invite.max_uses:
        invite.is_active = False
    if invite.used_by_id is None:
        invite.used_by_id = user_id
    invite.used_at = dt.datetime.utcnow()
