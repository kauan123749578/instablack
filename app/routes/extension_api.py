"""API da extensão Chrome: envia cookies IG completos + fingerprint do browser."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_auth_user, reject_view_as_secrets
from app.extension_token import (
    generate_extension_token,
    hash_extension_token,
    parse_extension_token_user_id,
    verify_extension_token,
)
from app.utils.proxy import clean_sessionid
from core.database import get_db, release_db_transaction
from core.instagram import (
    InstagramAuthError,
    extract_sessionid_from_settings,
    login_with_sessionid,
    serialize_settings,
)
from core.web_browser import encrypt_web_browser
from core.web_cookies import (
    WebCookiesError,
    encrypt_web_cookies,
    parse_web_cookies_blob,
)
from models.models import InstagramAccount, User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/extension", tags=["extension"])
panel_router = APIRouter(prefix="/accounts/extension", tags=["extension-panel"])

VISIBLE = ("active", "paused", "needs_login", "proxy_down")


class PushSessionBody(BaseModel):
    account_id: int
    cookies: list[dict[str, Any]] | dict[str, Any] | str = Field(
        ...,
        description="Jar completo do chrome.cookies (lista Cookie-Editor) ou mapa/header",
    )
    browser: dict[str, Any] = Field(default_factory=dict)


def _bearer_token(authorization: str | None) -> str:
    raw = (authorization or "").strip()
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return raw


def get_extension_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    token = _bearer_token(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token da extensão ausente. Gere em Contas → Extensão Chrome.",
        )
    user_id = parse_extension_token_user_id(token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido.",
        )
    user = db.get(User, user_id)
    if (
        user is None
        or not user.is_active
        or not verify_extension_token(token, user.extension_token_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou revogado. Gere um novo no painel.",
        )
    return user


def _cookies_blob(raw: list | dict | str) -> str:
    import json

    if isinstance(raw, str):
        return raw
    return json.dumps(raw, ensure_ascii=False)


def _apply_web_session(
    db: Session,
    acc: InstagramAccount,
    *,
    cookies_raw: list | dict | str,
    browser: dict[str, Any],
) -> dict[str, Any]:
    if (acc.provider or "instagrapi") == "meta":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Conta Meta oficial não usa cookies web da extensão.",
        )
    try:
        parsed = parse_web_cookies_blob(_cookies_blob(cookies_raw))
    except WebCookiesError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    browser_token = encrypt_web_browser(browser)
    if not browser_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fingerprint incompleto: user_agent obrigatório.",
        )

    sid = clean_sessionid(parsed["sessionid"])
    proxy = acc.proxy
    username_hint = acc.username
    account_id = acc.id
    release_db_transaction(db)

    try:
        settings_dict, resolved_user = login_with_sessionid(
            sid,
            proxy=proxy,
            username_hint=username_hint,
        )
    except InstagramAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cookies rejeitados no login: {exc}",
        ) from exc

    acc = db.get(InstagramAccount, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="Conta não encontrada")

    acc.session_json = serialize_settings(settings_dict)
    acc.encrypted_web_cookies = encrypt_web_cookies(parsed)
    acc.encrypted_web_browser = browser_token
    if resolved_user:
        acc.username = resolved_user
    acc.status = "active"
    acc.last_login_at = dt.datetime.utcnow()
    acc.last_error = None
    db.commit()

    return {
        "ok": True,
        "account_id": acc.id,
        "username": acc.username,
        "cookies_count": len(parsed),
        "has_browser": True,
        "sessionid_tail": (extract_sessionid_from_settings(settings_dict) or "")[-8:],
    }


@router.get("/accounts")
def extension_list_accounts(
    user: User = Depends(get_extension_user),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(InstagramAccount)
        .where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.status.in_(VISIBLE),
        )
        .order_by(InstagramAccount.username.asc())
    ).all()
    return {
        "panel_user": user.username,
        "accounts": [
            {
                "id": a.id,
                "username": a.username,
                "status": a.status,
                "provider": a.provider or "instagrapi",
                "has_web_cookies": bool(a.encrypted_web_cookies),
                "has_browser": bool(getattr(a, "encrypted_web_browser", None)),
            }
            for a in rows
            if (a.provider or "instagrapi") != "meta"
        ],
    }


@router.post("/push-session")
def extension_push_session(
    body: PushSessionBody,
    user: User = Depends(get_extension_user),
    db: Session = Depends(get_db),
):
    acc = db.get(InstagramAccount, body.account_id)
    if acc is None or acc.user_id != user.id or acc.status == "deleted":
        raise HTTPException(status_code=404, detail="Conta não encontrada")
    result = _apply_web_session(
        db,
        acc,
        cookies_raw=body.cookies,
        browser=body.browser or {},
    )
    log.info(
        "extension push-session user=%s account=%s cookies=%s",
        user.id,
        result["account_id"],
        result["cookies_count"],
    )
    return result


@panel_router.get("")
def extension_setup_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_auth_user),
    _: None = Depends(reject_view_as_secrets),
):
    from app.templating import templates

    has_token = bool(user.extension_token_hash)
    return templates.TemplateResponse(
        "extension_setup.html",
        {
            "request": request,
            "user": user,
            "has_token": has_token,
            "panel_origin": str(request.base_url).rstrip("/"),
        },
    )


@panel_router.post("/token")
def extension_issue_token(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_auth_user),
    _: None = Depends(reject_view_as_secrets),
):
    """Gera (ou rotaciona) o token da extensão — plaintext só nesta resposta."""
    token = generate_extension_token(user.id)
    user.extension_token_hash = hash_extension_token(token)
    db.commit()
    accept = request.headers.get("accept", "")
    if "application/json" in accept or request.headers.get("x-requested-with"):
        return {
            "ok": True,
            "token": token,
            "panel_origin": str(request.base_url).rstrip("/"),
        }
    from fastapi.responses import RedirectResponse

    # Form clássico: mostra na query uma vez (aceitável pro MVP; preferir fetch JSON).
    return RedirectResponse(
        f"/accounts/extension?issued=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@panel_router.post("/token/revoke")
def extension_revoke_token(
    db: Session = Depends(get_db),
    user: User = Depends(get_auth_user),
    _: None = Depends(reject_view_as_secrets),
):
    user.extension_token_hash = None
    db.commit()
    from fastapi.responses import RedirectResponse

    return RedirectResponse(
        "/accounts/extension?revoked=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )
