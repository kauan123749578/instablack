"""Apagar Reels já publicados — só API oficial Meta."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_effective_user
from app.security import decrypt_secret
from app.templating import templates
from app.utils.account_health import VISIBLE_ACCOUNT_STATUSES
from core.database import get_db, release_db_transaction
from core.meta_instagram import MetaInstagramError, list_ig_reels, try_delete_media
from models.models import InstagramAccount, User

router = APIRouter(prefix="/accounts/reels", tags=["accounts-reels"])


class DeleteReelBody(BaseModel):
    account_id: int
    media_id: str = Field(min_length=1, max_length=128)


def _meta_accounts(db: Session, user: User) -> list[InstagramAccount]:
    return list(
        db.scalars(
            select(InstagramAccount)
            .where(
                InstagramAccount.user_id == user.id,
                InstagramAccount.provider == "meta",
                InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
            )
            .order_by(InstagramAccount.username.asc())
        ).all()
    )


def _load_meta_account(db: Session, user: User, account_id: int) -> InstagramAccount:
    acc = db.get(InstagramAccount, account_id)
    if not acc or acc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")
    if (acc.provider or "") != "meta":
        raise HTTPException(status_code=400, detail="Só contas da API oficial.")
    if acc.status not in VISIBLE_ACCOUNT_STATUSES:
        raise HTTPException(status_code=400, detail="Conta indisponível.")
    if not acc.meta_ig_user_id or not acc.encrypted_meta_access_token:
        raise HTTPException(status_code=400, detail="Reconecte esta conta Meta.")
    return acc


@router.get("")
def reels_cleanup_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
    account: int | None = None,
):
    accounts = _meta_accounts(db, user)
    release_db_transaction(db)
    selected = account if account and any(a.id == account for a in accounts) else None
    return templates.TemplateResponse(
        "accounts_reels.html",
        {
            "request": request,
            "user": user,
            "accounts": accounts,
            "selected_account_id": selected,
        },
    )


@router.get("/list")
def reels_cleanup_list(
    account_id: int = Query(...),
    after: str = Query(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    acc = _load_meta_account(db, user, account_id)
    token = decrypt_secret(acc.encrypted_meta_access_token or "")
    ig_user_id = acc.meta_ig_user_id or ""
    proxy = acc.proxy
    release_db_transaction(db)
    if not token:
        raise HTTPException(status_code=400, detail="Token Meta ausente. Reconecte a conta.")
    try:
        payload = list_ig_reels(
            token,
            ig_user_id,
            after=after or None,
            limit=24,
            proxy=proxy,
        )
    except MetaInstagramError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(
        {
            "ok": True,
            "account_id": account_id,
            "username": acc.username,
            **payload,
        }
    )


@router.post("/delete")
def reels_cleanup_delete(
    body: DeleteReelBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    acc = _load_meta_account(db, user, body.account_id)
    token = decrypt_secret(acc.encrypted_meta_access_token or "")
    proxy = acc.proxy
    username = acc.username
    release_db_transaction(db)
    if not token:
        raise HTTPException(status_code=400, detail="Token Meta ausente. Reconecte a conta.")
    media_id = "".join(ch for ch in body.media_id.strip() if ch.isalnum() or ch in "_-")
    if not media_id:
        raise HTTPException(status_code=400, detail="ID da mídia inválido.")
    ok, message = try_delete_media(token, media_id, proxy=proxy)
    if not ok:
        return JSONResponse({"ok": False, "detail": message, "username": username}, status_code=400)
    return {"ok": True, "media_id": media_id, "username": username}
