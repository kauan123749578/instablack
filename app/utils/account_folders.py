"""Pastas de contas Instagram — contexto de template e validação."""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from models.models import AccountFolder, InstagramAccount

log = logging.getLogger(__name__)


def normalize_folder_name(raw: str) -> str:
    name = " ".join((raw or "").strip().split())
    return name[:40]


def load_folders(db: Session, user_id: int) -> list[AccountFolder]:
    return list(
        db.scalars(
            select(AccountFolder)
            .where(AccountFolder.user_id == user_id)
            .order_by(AccountFolder.sort_order.asc(), AccountFolder.name.asc())
        ).all()
    )


def folders_template_context(
    db: Session,
    user_id: int,
    accounts: list[InstagramAccount] | None = None,
) -> dict:
    empty = {
        "folders": [],
        "folder_account_ids": {},
        "unfiled_account_ids": [int(acc.id) for acc in (accounts or [])],
    }
    try:
        folders = load_folders(db, user_id)
    except (ProgrammingError, OperationalError):
        log.exception("Pastas de contas indisponíveis (tabela account_folders?)")
        try:
            db.rollback()
        except Exception:
            pass
        return empty
    ids_by_folder: dict[int, list[int]] = {f.id: [] for f in folders}
    unfiled: list[int] = []
    for acc in accounts or []:
        fid = getattr(acc, "folder_id", None)
        if fid and fid in ids_by_folder:
            ids_by_folder[fid].append(acc.id)
        else:
            unfiled.append(acc.id)
    return {
        "folders": folders,
        "folder_account_ids": ids_by_folder,
        "unfiled_account_ids": unfiled,
    }


def next_folder_sort(db: Session, user_id: int) -> int:
    current = db.scalar(
        select(func.max(AccountFolder.sort_order)).where(AccountFolder.user_id == user_id)
    )
    return int(current or 0) + 1


def folder_name_taken(db: Session, user_id: int, name: str, *, exclude_id: int | None = None) -> bool:
    q = select(AccountFolder.id).where(
        AccountFolder.user_id == user_id,
        func.lower(AccountFolder.name) == name.lower(),
    )
    if exclude_id is not None:
        q = q.where(AccountFolder.id != exclude_id)
    return db.scalar(q) is not None


def get_user_folder(db: Session, user_id: int, folder_id: int) -> AccountFolder | None:
    return db.scalar(
        select(AccountFolder).where(
            AccountFolder.id == folder_id,
            AccountFolder.user_id == user_id,
        )
    )
