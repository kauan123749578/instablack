"""API da extensão Chrome: envia cookies IG completos + fingerprint do browser."""
from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import get_auth_user, reject_view_as_secrets
from app.extension_token import (
    generate_extension_token,
    hash_extension_token,
    parse_extension_token_user_id,
    verify_extension_token,
)
from app.utils.account_limits import can_add_instagram_account
from app.utils.proxy import (
    clean_sessionid,
    diagnose_proxy,
    normalize_proxy,
    proxy_host,
    validate_proxy_url,
)
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
    account_id: int | None = None
    cookies: list[dict[str, Any]] | dict[str, Any] | str = Field(
        ...,
        description="Jar completo do chrome.cookies (lista Cookie-Editor) ou mapa/header",
    )
    browser: dict[str, Any] = Field(default_factory=dict)
    proxy: str = ""


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
            detail="Token da extensão ausente. Abra o painel → Extensão Chrome → Gerar token.",
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
    from app.utils.billing import is_panel_blocked

    if is_panel_blocked(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Painel bloqueado: fale com o suporte para pagar a mensalidade.",
        )
    return user


def _cookies_blob(raw: list | dict | str) -> str:
    if isinstance(raw, str):
        return raw
    return json.dumps(raw, ensure_ascii=False)


def _set_proxy_fields(acc: InstagramAccount, normalized: str, meta: dict) -> None:
    acc.proxy = normalized
    acc.proxy_ip = meta.get("ip") or proxy_host(normalized)
    acc.proxy_geo = meta.get("geo")


def _resolve_proxy_for_push(
    acc: InstagramAccount | None,
    proxy_raw: str,
) -> tuple[str, dict]:
    """Proxy da conta, do payload, ou default do servidor."""
    candidates = [
        (proxy_raw or "").strip(),
        (acc.proxy if acc else "") or "",
        normalize_proxy(get_settings().default_proxy or ""),
    ]
    chosen = next((c for c in candidates if c.strip()), "")
    if not chosen:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Proxy obrigatória para conectar do zero. Cole na extensão (ip:porta:user:senha).",
        )
    try:
        normalized = validate_proxy_url(chosen)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    meta = diagnose_proxy(normalized)
    if not meta.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=meta.get("error") or "Proxy falhou no teste.",
        )
    return normalized, meta


def _find_account_by_username(
    db: Session, user_id: int, username: str
) -> InstagramAccount | None:
    uname = (username or "").strip().lstrip("@").lower()
    if not uname:
        return None
    rows = db.scalars(
        select(InstagramAccount).where(
            InstagramAccount.user_id == user_id,
            InstagramAccount.status != "deleted",
        )
    ).all()
    for row in rows:
        if (row.username or "").strip().lstrip("@").lower() == uname:
            return row
    return None


def _push_session(
    db: Session,
    user: User,
    *,
    account_id: int | None,
    cookies_raw: list | dict | str,
    browser: dict[str, Any],
    proxy_raw: str,
) -> dict[str, Any]:
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

    acc: InstagramAccount | None = None
    created = False
    if account_id is not None:
        acc = db.get(InstagramAccount, account_id)
        if acc is None or acc.user_id != user.id or acc.status == "deleted":
            raise HTTPException(status_code=404, detail="Conta não encontrada")
        if (acc.provider or "instagrapi") == "meta":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Conta Meta oficial não usa cookies web da extensão.",
            )

    proxy, proxy_meta = _resolve_proxy_for_push(acc, proxy_raw)
    sid = clean_sessionid(parsed["sessionid"])
    username_hint = acc.username if acc else None
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

    username = (resolved_user or username_hint or "").strip().lstrip("@")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não deu pra descobrir o @ da sessão. Logue no Instagram e tente de novo.",
        )

    prior_id = acc.id if acc is not None else None
    acc = db.get(InstagramAccount, prior_id) if prior_id else None

    if acc is None:
        existing = _find_account_by_username(db, user.id, username)
        if existing:
            if (existing.provider or "instagrapi") == "meta":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"@{username} já está como Meta oficial no painel.",
                )
            acc = existing
        else:
            current_count = (
                db.scalar(
                    select(func.count(InstagramAccount.id)).where(
                        InstagramAccount.user_id == user.id,
                        InstagramAccount.status.in_(VISIBLE),
                    )
                )
                or 0
            )
            allowed, limit_msg = can_add_instagram_account(user, current_count)
            if not allowed:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=limit_msg or "Limite de contas atingido.",
                )
            acc = InstagramAccount(
                user_id=user.id,
                username=username,
                provider="instagrapi",
                status="active",
            )
            db.add(acc)
            created = True

    acc.provider = "instagrapi"
    acc.username = username
    acc.session_json = serialize_settings(settings_dict)
    acc.encrypted_web_cookies = encrypt_web_cookies(parsed)
    acc.encrypted_web_browser = browser_token
    _set_proxy_fields(acc, proxy, proxy_meta)
    acc.status = "active"
    acc.last_login_at = dt.datetime.utcnow()
    acc.last_error = None
    db.commit()
    db.refresh(acc)

    return {
        "ok": True,
        "created": created,
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
        "default_proxy_configured": bool(
            normalize_proxy(get_settings().default_proxy or "")
        ),
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
    result = _push_session(
        db,
        user,
        account_id=body.account_id,
        cookies_raw=body.cookies,
        browser=body.browser or {},
        proxy_raw=body.proxy or "",
    )
    log.info(
        "extension push-session user=%s account=%s created=%s cookies=%s",
        user.id,
        result["account_id"],
        result.get("created"),
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

    return RedirectResponse(
        "/accounts/extension?issued=1",
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
