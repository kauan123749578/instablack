"""Responder comentários de Reels e feed — API oficial Meta."""
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
from core.meta_instagram import (
    MetaInstagramError,
    list_ig_media,
    list_media_comments,
    reply_to_comment,
)
from models.models import InstagramAccount, User

router = APIRouter(prefix="/accounts/comments", tags=["accounts-comments"])


class ReplyCommentBody(BaseModel):
    account_id: int
    comment_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2200)


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
        raise HTTPException(status_code=400, detail="Só contas da API oficial Meta.")
    if acc.status not in VISIBLE_ACCOUNT_STATUSES:
        raise HTTPException(status_code=400, detail="Conta indisponível.")
    if not acc.meta_ig_user_id or not acc.encrypted_meta_access_token:
        raise HTTPException(status_code=400, detail="Reconecte esta conta Meta.")
    return acc


def _clean_graph_id(value: str) -> str:
    return "".join(ch for ch in (value or "").strip() if ch.isalnum() or ch in "_-")


@router.get("")
def comments_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
    account: int | None = None,
):
    accounts = _meta_accounts(db, user)
    release_db_transaction(db)
    selected = account if account and any(a.id == account for a in accounts) else None
    return templates.TemplateResponse(
        "accounts_comments.html",
        {
            "request": request,
            "user": user,
            "accounts": accounts,
            "selected_account_id": selected,
        },
    )


@router.get("/media")
def comments_media_list(
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
        payload = list_ig_media(
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


@router.get("/list")
def comments_list(
    account_id: int = Query(...),
    media_id: str = Query(...),
    after: str = Query(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    acc = _load_meta_account(db, user, account_id)
    token = decrypt_secret(acc.encrypted_meta_access_token or "")
    proxy = acc.proxy
    media_id = _clean_graph_id(media_id)
    release_db_transaction(db)
    if not token:
        raise HTTPException(status_code=400, detail="Token Meta ausente. Reconecte a conta.")
    if not media_id:
        raise HTTPException(status_code=400, detail="ID da mídia inválido.")
    try:
        payload = list_media_comments(
            token,
            media_id,
            after=after or None,
            limit=50,
            proxy=proxy,
        )
    except MetaInstagramError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(
        {
            "ok": True,
            "account_id": account_id,
            "media_id": media_id,
            "username": acc.username,
            **payload,
        }
    )


@router.post("/reply")
def comments_reply(
    body: ReplyCommentBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    acc = _load_meta_account(db, user, body.account_id)
    token = decrypt_secret(acc.encrypted_meta_access_token or "")
    proxy = acc.proxy
    comment_id = _clean_graph_id(body.comment_id)
    release_db_transaction(db)
    if not token:
        raise HTTPException(status_code=400, detail="Token Meta ausente. Reconecte a conta.")
    if not comment_id:
        raise HTTPException(status_code=400, detail="ID do comentário inválido.")
    try:
        result = reply_to_comment(token, comment_id, body.message, proxy=proxy)
    except MetaInstagramError as exc:
        return JSONResponse(
            {"ok": False, "detail": str(exc), "username": acc.username},
            status_code=400,
        )
    return {
        "ok": True,
        "comment_id": comment_id,
        "reply_id": result.get("id"),
        "message": result.get("message"),
        "username": acc.username,
    }
