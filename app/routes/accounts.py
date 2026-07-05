"""CRUD de contas do Instagram conectadas."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import get_current_user
from app.security import decrypt_secret, encrypt_secret
from app.templating import templates
from app.config import get_settings
from app.utils.proxy import normalize_proxy
from app.utils.account_limits import (
    account_limit_label,
    accounts_remaining,
    can_add_instagram_account,
)
from core.database import get_db
from core.instagram import (
    InstagramAuthError,
    InstagramTwoFactorRequired,
    check_proxy,
    deserialize_settings,
    get_account_profile,
    get_ready_client,
    login_with_credentials,
    login_with_sessionid,
    serialize_settings,
    update_account_profile,
)
from core.storage import get_storage

from models.models import InstagramAccount, User

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _accounts_page_context(
    request: Request,
    user: User,
    accounts: list[InstagramAccount],
    *,
    error: str | None = None,
    ok: str | None = None,
) -> dict:
    count = len(accounts)
    remaining = accounts_remaining(user, count)
    can_add = remaining is None or remaining > 0
    return {
        "request": request,
        "user": user,
        "accounts": accounts,
        "error": error,
        "ok": ok,
        "account_limit_label": account_limit_label(user.account_limit),
        "accounts_remaining": remaining,
        "can_add_account": can_add,
        "default_proxy": normalize_proxy(get_settings().default_proxy),
    }


def _load_user_accounts(db: Session, user: User) -> list[InstagramAccount]:
    return db.scalars(
        select(InstagramAccount)
        .where(InstagramAccount.user_id == user.id)
        .order_by(InstagramAccount.username.asc())
    ).all()


@router.get("")
def list_accounts(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    accounts = _load_user_accounts(db, user)
    ok_key = request.query_params.get("ok")
    ok_msg = {"paused": "Conta pausada.", "resumed": "Conta retomada."}.get(ok_key or "")
    offline = offline_accounts(db, user.id)
    return templates.TemplateResponse(
        "accounts.html",
        {**_accounts_page_context(request, user, accounts, ok=ok_msg), "offline_accounts": offline},
    )


@router.post("/add")
def add_account(
    request: Request,
    auth_method: str = Form("password"),
    username: str = Form(""),
    password: str = Form(""),
    verification_code: str = Form(""),
    sessionid: str = Form(""),
    proxy: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    username = username.strip().lstrip("@")
    proxy = normalize_proxy(proxy)

    if not proxy:
        accounts = _load_user_accounts(db, user)
        return templates.TemplateResponse(
            "accounts.html",
            _accounts_page_context(
                request,
                user,
                accounts,
                error="Proxy é obrigatório. Informe um proxy válido antes de conectar a conta.",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not check_proxy(proxy):
        accounts = _load_user_accounts(db, user)
        return templates.TemplateResponse(
            "accounts.html",
            _accounts_page_context(
                request,
                user,
                accounts,
                error="Proxy inválido ou fora do ar. A conexão foi bloqueada para evitar vazamento do banimento.",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        if auth_method == "sessionid":
            sid = sessionid.strip()
            if not sid:
                raise InstagramAuthError("Cole o sessionid do navegador.")
            settings_dict, username = login_with_sessionid(sid, proxy=proxy)
            encrypted_pw = None
        else:
            if not username or not password:
                raise InstagramAuthError("Usuário e senha são obrigatórios.")
            settings_dict = login_with_credentials(
                username=username,
                password=password,
                verification_code=verification_code.strip() or None,
                proxy=proxy,
            )
            encrypted_pw = encrypt_secret(password)

    except InstagramTwoFactorRequired as exc:
        if request.headers.get("X-Requested-With") == "fetch":
            return JSONResponse(
                {"needs_2fa": True, "message": str(exc)},
                status_code=status.HTTP_403_FORBIDDEN,
            )
        accounts = _load_user_accounts(db, user)
        return templates.TemplateResponse(
            "accounts.html",
            {**_accounts_page_context(request, user, accounts, error=str(exc)), "needs_2fa": True},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    except InstagramAuthError as exc:
        accounts = _load_user_accounts(db, user)
        return templates.TemplateResponse(
            "accounts.html",
            _accounts_page_context(
                request,
                user,
                accounts,
                error=f"Falha no login: {exc}",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    existing = db.scalar(
        select(InstagramAccount).where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.username == username,
        )
    )
    if not existing:
        current_count = db.scalar(
            select(func.count(InstagramAccount.id)).where(InstagramAccount.user_id == user.id)
        ) or 0
        allowed, limit_msg = can_add_instagram_account(user, current_count)
        if not allowed:
            accounts = _load_user_accounts(db, user)
            return templates.TemplateResponse(
                "accounts.html",
                _accounts_page_context(request, user, accounts, error=limit_msg),
                status_code=status.HTTP_403_FORBIDDEN,
            )

    if existing:
        existing.session_json = serialize_settings(settings_dict)
        if encrypted_pw:
            existing.encrypted_password = encrypted_pw
        existing.proxy = proxy
        existing.status = "active"
        existing.last_login_at = dt.datetime.utcnow()
        existing.last_error = None
    else:
        db.add(
            InstagramAccount(
                user_id=user.id,
                username=username,
                encrypted_password=encrypted_pw,
                proxy=proxy,
                session_json=serialize_settings(settings_dict),
                status="active",
                last_login_at=dt.datetime.utcnow(),
            )
        )
    db.commit()
    return RedirectResponse("/accounts", status_code=status.HTTP_303_SEE_OTHER)


def _get_owned_account(db: Session, account_id: int, user: User) -> InstagramAccount:
    acc = db.get(InstagramAccount, account_id)
    if not acc or acc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conta não encontrada")
    return acc


def _client_for_account(acc: InstagramAccount):
    if not acc.proxy or not check_proxy(acc.proxy):
        raise InstagramAuthError("Proxy inválido ou fora do ar")
    settings_dict = deserialize_settings(acc.session_json)
    if not settings_dict:
        raise InstagramAuthError("Sessão expirada — reconecte a conta")
    password = decrypt_secret(acc.encrypted_password)
    cl = get_ready_client(
        settings_dict=settings_dict,
        proxy=acc.proxy,
        username=acc.username,
        password=password,
    )
    return cl


@router.get("/{account_id}/edit")
def edit_account_page(
    request: Request,
    account_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    acc = _get_owned_account(db, account_id, user)
    profile = None
    error = None
    try:
        cl = _client_for_account(acc)
        profile = get_account_profile(cl)
        acc.session_json = serialize_settings(cl.get_settings())
        db.commit()
    except InstagramAuthError as exc:
        error = str(exc)

    return templates.TemplateResponse(
        "account_edit.html",
        {
            "request": request,
            "user": user,
            "account": acc,
            "profile": profile,
            "error": error,
            "ok": None,
        },
    )


@router.post("/{account_id}/edit")
async def edit_account_submit(
    request: Request,
    account_id: int,
    biography: str = Form(""),
    external_url: str = Form(""),
    profile_picture: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    acc = _get_owned_account(db, account_id, user)
    pic_path = None
    tmp_pic = None
    try:
        cl = _client_for_account(acc)
        if profile_picture and profile_picture.filename:
            import tempfile
            from pathlib import Path
            storage = get_storage()
            ext = Path(profile_picture.filename).suffix or ".jpg"
            key = storage.save(profile_picture.file, suggested_ext=ext)
            tmp_pic = Path(tempfile.mkdtemp()) / f"pic{ext}"
            storage.download_to(key, tmp_pic)
            pic_path = tmp_pic

        profile = update_account_profile(
            cl,
            biography=biography,
            external_url=external_url,
            profile_picture_path=pic_path,
        )
        acc.session_json = serialize_settings(cl.get_settings())
        acc.status = "active"
        acc.last_error = None
        db.commit()
        return templates.TemplateResponse(
            "account_edit.html",
            {
                "request": request,
                "user": user,
                "account": acc,
                "profile": profile,
                "error": None,
                "ok": "Perfil atualizado com sucesso!",
            },
        )
    except InstagramAuthError as exc:
        return templates.TemplateResponse(
            "account_edit.html",
            {
                "request": request,
                "user": user,
                "account": acc,
                "profile": None,
                "error": str(exc),
                "ok": None,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    finally:
        if tmp_pic and tmp_pic.exists():
            try:
                tmp_pic.unlink()
            except OSError:
                pass


@router.post("/{account_id}/pause")
def pause_account(
    account_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    acc = _get_owned_account(db, account_id, user)
    acc.status = "paused"
    acc.last_error = None
    db.commit()
    return RedirectResponse("/accounts?ok=paused", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{account_id}/resume")
def resume_account(
    account_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    acc = _get_owned_account(db, account_id, user)
    if acc.status == "paused":
        acc.status = "active"
        acc.last_error = None
    db.commit()
    return RedirectResponse("/accounts?ok=resumed", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{account_id}/delete")
def delete_account(
    account_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    acc = db.get(InstagramAccount, account_id)
    if not acc or acc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conta não encontrada")
    db.delete(acc)
    db.commit()
    return RedirectResponse("/accounts", status_code=status.HTTP_303_SEE_OTHER)
