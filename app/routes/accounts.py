"""CRUD de contas do Instagram conectadas."""
from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import get_current_user
from app.security import decrypt_secret, encrypt_secret
from app.templating import templates
from app.config import get_settings
from app.utils.account_health import offline_accounts
from app.utils.proxy import (
    account_proxy_ip,
    clean_sessionid,
    diagnose_proxy,
    normalize_proxy,
    proxy_host,
)
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
    login_with_imported_settings,
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
    form: dict | None = None,
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
        "form": form or {},
    }


def _set_account_proxy(acc: InstagramAccount, normalized: str, meta: dict) -> None:
    acc.proxy = normalized
    acc.proxy_ip = meta.get("ip") or proxy_host(normalized)
    acc.proxy_geo = meta.get("geo")


def _backfill_proxy_meta(db: Session, accounts: list[InstagramAccount]) -> None:
    dirty = False
    for acc in accounts:
        if not acc.proxy or (acc.proxy_ip and acc.proxy_geo):
            continue
        if not acc.proxy_ip:
            acc.proxy_ip = proxy_host(acc.proxy)
            dirty = True
        if acc.proxy_ip and not acc.proxy_geo:
            from app.utils.proxy import lookup_ip_geo
            geo = lookup_ip_geo(acc.proxy_ip)
            if geo:
                acc.proxy_geo = geo["label"]
                dirty = True
    if dirty:
        db.commit()


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
    _backfill_proxy_meta(db, accounts)
    ok_key = request.query_params.get("ok")
    ok_msg = {
        "paused": "Conta pausada.",
        "resumed": "Conta retomada.",
        "proxy_updated": "Proxy atualizado com sucesso!",
    }.get(ok_key or "")
    err_key = request.query_params.get("error")
    err_msg = {
        "proxy_vazio": "Informe um proxy válido.",
        "proxy_invalid": "Proxy inválido ou fora do ar. Teste antes de salvar.",
    }.get(err_key or "")
    offline = offline_accounts(db, user.id)
    return templates.TemplateResponse(
        "accounts.html",
        {
            **_accounts_page_context(request, user, accounts, ok=ok_msg, error=err_msg or None),
            "offline_accounts": offline,
        },
    )


@router.post("/add")
def add_account(
    request: Request,
    auth_method: str = Form("password"),
    username: str = Form(""),
    password: str = Form(""),
    verification_code: str = Form(""),
    sessionid: str = Form(""),
    session_json: str = Form(""),
    proxy: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    username = username.strip().lstrip("@")
    proxy_raw = proxy
    proxy = normalize_proxy(proxy)
    sid = clean_sessionid(sessionid)
    use_sessionid = auth_method == "sessionid"
    use_import = auth_method == "import"
    sessionid_only = bool(sid) and not password.strip()
    form_state = {
        "auth_method": "sessionid" if (use_sessionid or sessionid_only) else auth_method,
        "username": username,
        "sessionid": sid or sessionid.strip(),
        "session_json": session_json.strip(),
        "proxy": proxy_raw.strip() or proxy,
    }

    if not proxy:
        accounts = _load_user_accounts(db, user)
        return templates.TemplateResponse(
            "accounts.html",
            _accounts_page_context(
                request,
                user,
                accounts,
                error="Proxy é obrigatório. Informe um proxy válido antes de conectar a conta.",
                form=form_state,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    proxy_meta = diagnose_proxy(proxy_raw.strip() or proxy)
    if not proxy_meta["ok"]:
        accounts = _load_user_accounts(db, user)
        return templates.TemplateResponse(
            "accounts.html",
            _accounts_page_context(
                request,
                user,
                accounts,
                error=proxy_meta.get("error") or "Proxy inválido ou fora do ar.",
                form=form_state,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    encrypted_pw = None
    try:
        if use_import:
            if not session_json.strip():
                raise InstagramAuthError("Cole o conteúdo do session.json exportado pelo instagrapi.")
            if not username:
                raise InstagramAuthError("Informe o @ da conta para importar a sessão.")
            try:
                imported = json.loads(session_json)
            except json.JSONDecodeError as exc:
                raise InstagramAuthError("JSON inválido. Cole o session.json completo do instagrapi.") from exc
            if not isinstance(imported, dict):
                raise InstagramAuthError("session.json deve ser um objeto JSON.")
            settings_dict = login_with_imported_settings(
                imported,
                proxy=proxy,
                username=username,
                password=password or None,
            )
            encrypted_pw = encrypt_secret(password) if password else None
        elif use_sessionid or sessionid_only:
            if not sid:
                raise InstagramAuthError("Cole o Session ID do Multilogin/navegador.")
            settings_dict, resolved_user = login_with_sessionid(
                sid, proxy=proxy, username_hint=username or None
            )
            username = resolved_user
            encrypted_pw = encrypt_secret(password) if password else None
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
            {**_accounts_page_context(request, user, accounts, error=str(exc), form=form_state), "needs_2fa": True},
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
                form=form_state,
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
        _set_account_proxy(existing, proxy, proxy_meta)
        existing.status = "active"
        existing.last_login_at = dt.datetime.utcnow()
        existing.last_error = None
    else:
        new_acc = InstagramAccount(
            user_id=user.id,
            username=username,
            encrypted_password=encrypted_pw,
            proxy=proxy,
            session_json=serialize_settings(settings_dict),
            status="active",
            last_login_at=dt.datetime.utcnow(),
        )
        _set_account_proxy(new_acc, proxy, proxy_meta)
        db.add(new_acc)
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


@router.post("/test-proxy")
def test_proxy(proxy: str = Form(...)):
    """Testa proxy sem salvar (AJAX)."""
    result = diagnose_proxy(proxy)
    return JSONResponse(result)


@router.post("/{account_id}/update-proxy")
def update_account_proxy(
    account_id: int,
    proxy: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    acc = _get_owned_account(db, account_id, user)
    normalized = normalize_proxy(proxy)
    if not normalized:
        return RedirectResponse(
            "/accounts?error=proxy_vazio",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    diag = diagnose_proxy(proxy)
    if not diag["ok"]:
        return RedirectResponse(
            "/accounts?error=proxy_invalid",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    _set_account_proxy(acc, normalized, diag)
    if acc.status == "proxy_down":
        acc.status = "active"
    acc.last_error = None
    acc.last_health_check_at = None
    db.commit()
    return RedirectResponse(
        "/accounts?ok=proxy_updated",
        status_code=status.HTTP_303_SEE_OTHER,
    )


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
