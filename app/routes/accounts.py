"""CRUD de contas do Instagram conectadas."""
from __future__ import annotations

import datetime as dt
import json
import secrets

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.deps import get_current_user, get_effective_user, reject_view_as_secrets
from app.security import decrypt_secret, encrypt_secret
from app.templating import templates
from app.config import get_settings
from app.utils.account_health import offline_accounts
from app.utils.totp import TotpError, current_totp_code, normalize_totp_secret
from app.utils.proxy import (
    clean_sessionid,
    diagnose_proxy,
    normalize_proxy,
    proxy_host,
    validate_proxy_url,
)
from app.utils.account_limits import (
    account_limit_label,
    accounts_remaining,
    can_add_instagram_account,
)
from app.utils.auth_failures import mark_accounts_from_latest_auth_failures
from app.utils.instagrapi_access import (
    can_use_instagrapi,
    fake_instagrapi_login_delay,
    fake_instagrapi_login_error,
    is_instagrapi_auth_method,
)
from app.utils.meta_apps import credentials_from_app, get_owned_meta_app, list_user_meta_apps
from app.utils.platform_settings import META_TOKEN_YOUTUBE_URL, get_platform_setting
from core.database import get_db, release_db_transaction
from core.meta_instagram import (
    MetaInstagramError,
    account_profile,
    authorization_url,
    exchange_code,
    parse_signed_request,
    public_origin,
)
from core import aiograpi_client as aio_ig
from core.instagram import (
    InstagramAuthError,
    InstagramTwoFactorRequired,
    extract_sessionid_from_settings,
    login_with_credentials,
    login_with_imported_settings,
    login_with_sessionid,
    serialize_settings,
    try_refresh_session,
    deserialize_settings,
)
from core.web_cookies import (
    WebCookiesError,
    decrypt_web_cookies,
    encrypt_web_cookies,
    merge_sessionid_into_web_cookies,
    parse_web_cookies_blob,
    web_cookies_status,
)

from models.models import InstagramAccount, User

router = APIRouter(prefix="/accounts", tags=["accounts"])
VISIBLE_ACCOUNT_STATUSES = ("active", "paused", "needs_login", "proxy_down", "banned")


class ReconnectApiBody(BaseModel):
    mode: str = "auto"
    password: str = ""
    verification_code: str = ""
    sessionid: str = ""
    web_cookies: str = Field(default="", alias="web_cookies")

    model_config = {"populate_by_name": True}


class CredentialsBody(BaseModel):
    password: str = ""
    totp_secret: str = ""
    login_email: str | None = None
    clear_password: bool = False
    clear_totp: bool = False
    clear_email: bool = False


def _encrypt_totp_secret(raw: str | None) -> str | None:
    if not (raw or "").strip():
        return None
    secret = normalize_totp_secret(raw)
    return encrypt_secret(secret)


def _totp_code_from_encrypted(encrypted: str | None) -> str | None:
    plain = decrypt_secret(encrypted)
    if not plain:
        return None
    try:
        secret = normalize_totp_secret(plain)
        code, _ = current_totp_code(secret)
        return code
    except TotpError:
        return None


def _login_credentials_with_totp_retry(
    *,
    username: str,
    password: str,
    proxy: str,
    verification_code: str | None = None,
    totp_encrypted: str | None = None,
    totp_raw: str | None = None,
    backend: str = "instagrapi",
):
    """Login user/senha; se pedir 2FA e houver TOTP, gera código e tenta 1 vez."""
    login_fn = (
        aio_ig.login_with_credentials
        if (backend or "").strip().lower() == "aiograpi"
        else login_with_credentials
    )
    code = (verification_code or "").strip() or None
    if not code:
        if totp_raw and totp_raw.strip():
            try:
                secret = normalize_totp_secret(totp_raw)
                code, _ = current_totp_code(secret)
            except TotpError:
                code = None
        if not code:
            code = _totp_code_from_encrypted(totp_encrypted)

    try:
        return login_fn(
            username=username,
            password=password,
            verification_code=code,
            proxy=proxy,
        )
    except InstagramTwoFactorRequired:
        if code:
            raise
        # Ainda sem código: tenta gerar de novo do secret salvo/form
        auto = None
        if totp_raw and totp_raw.strip():
            try:
                secret = normalize_totp_secret(totp_raw)
                auto, _ = current_totp_code(secret)
            except TotpError:
                auto = None
        if not auto:
            auto = _totp_code_from_encrypted(totp_encrypted)
        if not auto:
            raise
        return login_fn(
            username=username,
            password=password,
            verification_code=auto,
            proxy=proxy,
        )


def _cred_flags_for_accounts(accounts: list[InstagramAccount]) -> dict[int, dict]:
    return {
        int(acc.id): {
            "has_password": bool(acc.encrypted_password),
            "has_totp": bool(getattr(acc, "encrypted_totp_secret", None)),
            "has_email": bool((getattr(acc, "login_email", None) or "").strip()),
            "login_email": (getattr(acc, "login_email", None) or "").strip() or None,
        }
        for acc in accounts
    }


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
    form_data = dict(form or {})
    if not (form_data.get("auth_method") or "").strip():
        form_data["auth_method"] = "password"
    return {
        "request": request,
        "user": user,
        "accounts": accounts,
        "error": error,
        "ok": ok,
        "account_limit_label": account_limit_label(user.account_limit),
        "accounts_remaining": remaining,
        "can_add_account": can_add,
        "can_use_instagrapi": can_use_instagrapi(user),
        "default_proxy": normalize_proxy(get_settings().default_proxy),
        "form": form_data,
        "needs_2fa": False,
    }


def _set_account_proxy(acc: InstagramAccount, normalized: str, meta: dict) -> None:
    acc.proxy = normalized
    acc.proxy_ip = meta.get("ip") or proxy_host(normalized)
    acc.proxy_geo = meta.get("geo")


def _backfill_proxy_meta(db: Session, accounts: list[InstagramAccount]) -> None:
    """Completa IP/geo faltando — no máx. 1 lookup de rede por request.

    Sempre libera a transação do SELECT antes do HTTP geo; senão o Postgres
    fica `idle in transaction` no SELECT instagram_accounts.
    """
    dirty = False
    need_geo_id: int | None = None
    need_geo_ip: str | None = None
    for acc in accounts:
        if not acc.proxy or (acc.proxy_ip and acc.proxy_geo):
            continue
        if not acc.proxy_ip:
            acc.proxy_ip = proxy_host(acc.proxy)
            dirty = True
        if acc.proxy_ip and not acc.proxy_geo and need_geo_id is None:
            need_geo_id = acc.id
            need_geo_ip = acc.proxy_ip
    if dirty:
        db.commit()
    else:
        release_db_transaction(db)
    if need_geo_id is None or not need_geo_ip:
        return
    from app.utils.proxy import lookup_ip_geo

    geo = lookup_ip_geo(need_geo_ip)
    if not geo:
        return
    acc = db.get(InstagramAccount, need_geo_id)
    if acc and not acc.proxy_geo:
        acc.proxy_geo = geo["label"]
        db.commit()


def _load_user_accounts(db: Session, user: User) -> list[InstagramAccount]:
    return db.scalars(
        select(InstagramAccount)
        .options(selectinload(InstagramAccount.meta_app))
        .where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
        )
        .order_by(InstagramAccount.username.asc())
    ).all()


def _meta_account_display(accounts: list[InstagramAccount]) -> dict[int, dict[str, str]]:
    """App name + token plaintext para UI (embassado no front)."""
    out: dict[int, dict[str, str]] = {}
    for acc in accounts:
        if (acc.provider or "") != "meta":
            continue
        app_name = ""
        if acc.meta_app is not None:
            app_name = (acc.meta_app.name or "").strip() or f"App #{acc.meta_app.id}"
        token = ""
        if acc.encrypted_meta_access_token:
            try:
                token = decrypt_secret(acc.encrypted_meta_access_token)
            except Exception:
                token = ""
        out[acc.id] = {"app_name": app_name, "token": token}
    return out


def _store_meta_account(
    db: Session,
    user: User,
    *,
    token: str,
    expires_at: dt.datetime | None,
    profile: dict[str, str],
    user_meta_app_id: int | None,
    proxy: str | None = None,
    proxy_meta: dict | None = None,
) -> str | None:
    """Cria/atualiza a conta oficial sem registrar o token em logs."""
    existing = db.scalar(
        select(InstagramAccount).where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.meta_ig_user_id == profile["id"],
        )
    )
    if existing is None:
        existing = db.scalar(
            select(InstagramAccount).where(
                InstagramAccount.user_id == user.id,
                InstagramAccount.username == profile["username"],
            )
        )
    if existing is None:
        current_count = len(_load_user_accounts(db, user))
        allowed, _ = can_add_instagram_account(user, current_count)
        if not allowed:
            return "account_limit"
        existing = InstagramAccount(
            user_id=user.id,
            username=profile["username"],
        )
        db.add(existing)

    existing.provider = "meta"
    existing.meta_ig_user_id = profile["id"]
    existing.user_meta_app_id = user_meta_app_id
    existing.encrypted_meta_access_token = encrypt_secret(token)
    existing.meta_token_expires_at = expires_at
    existing.username = profile["username"]
    existing.encrypted_password = None
    existing.encrypted_totp_secret = None
    existing.session_json = None
    if proxy and proxy_meta:
        _set_account_proxy(existing, proxy, proxy_meta)
    existing.status = "active"
    existing.last_error = None
    existing.last_login_at = dt.datetime.utcnow()
    db.commit()
    return None


def _optional_meta_proxy(proxy_raw: str) -> tuple[str | None, dict | None, str | None]:
    """Proxy opcional para API Meta. Vazio = ok (usa IP do servidor). Retorna (norm, meta, error)."""
    if not (proxy_raw or "").strip():
        return None, None, None
    try:
        normalized = validate_proxy_url(proxy_raw)
    except ValueError:
        return None, None, "proxy_invalid"
    if not normalized:
        return None, None, "proxy_invalid"
    diag = diagnose_proxy(proxy_raw.strip() or normalized)
    if not diag.get("ok"):
        return None, None, "proxy_invalid"
    return normalized, diag, None


@router.get("")
def list_accounts(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    """Página de adicionar conta (só o formulário)."""
    accounts = _load_user_accounts(db, user)
    ok_msg = {
        "account_added": "Conta conectada com sucesso!",
    }.get(request.query_params.get("ok") or "")
    error_msg = {
        "meta_not_configured": "Cadastre um app em Meus Apps antes de conectar pela API oficial.",
        "meta_no_app": "Selecione qual app Meta usar.",
        "meta_app_invalid": "App Meta inválido.",
        "meta_denied": "A autorização do Instagram foi cancelada.",
        "meta_state": "A sessão de autorização expirou. Tente conectar novamente.",
        "meta_exchange": "A Meta recusou a conexão. Confira o app e tente novamente.",
        "meta_token_invalid": "Token oficial inválido ou sem acesso à conta.",
        "account_limit": "Seu limite de contas foi atingido.",
        "proxy_invalid": "Proxy inválida ou fora do ar. Teste antes de conectar.",
    }.get(request.query_params.get("error") or "")
    meta_apps_list = list_user_meta_apps(db, user.id)
    token_youtube_url = get_platform_setting(
        db, META_TOKEN_YOUTUBE_URL, default="https://youtu.be/EA0iEb92sZg"
    )
    release_db_transaction(db)
    return templates.TemplateResponse(
        "accounts.html",
        {
            **_accounts_page_context(request, user, accounts, ok=ok_msg, error=error_msg),
            "meta_apps": meta_apps_list,
            "token_youtube_url": token_youtube_url,
        },
    )


@router.post("/meta/connect")
def connect_meta_account(
    request: Request,
    app_id: int = Form(0),
    proxy: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Inicia o Business Login oficial do Instagram (proxy residencial opcional)."""
    meta_app = get_owned_meta_app(db, user.id, app_id) if app_id else None
    if not meta_app:
        return RedirectResponse(
            "/accounts?error=meta_no_app" if app_id else "/accounts?error=meta_not_configured",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    release_db_transaction(db)
    normalized, proxy_meta, proxy_err = _optional_meta_proxy(proxy)
    if proxy_err:
        return RedirectResponse(
            f"/accounts?error={proxy_err}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    state = secrets.token_urlsafe(32)
    creds = credentials_from_app(meta_app)
    request.session["meta_oauth_state"] = state
    request.session["meta_oauth_user_id"] = user.id
    request.session["meta_oauth_app_id"] = meta_app.id
    if normalized and proxy_meta:
        request.session["meta_oauth_proxy"] = normalized
        request.session["meta_oauth_proxy_meta"] = {
            "ip": proxy_meta.get("ip"),
            "geo": proxy_meta.get("geo"),
            "geo_code": proxy_meta.get("geo_code"),
            "ok": True,
        }
    else:
        request.session.pop("meta_oauth_proxy", None)
        request.session.pop("meta_oauth_proxy_meta", None)
    return RedirectResponse(authorization_url(creds, state), status_code=status.HTTP_302_FOUND)


@router.post("/meta/connect-token")
def connect_meta_account_with_token(
    access_token: str = Form(...),
    app_id: int = Form(...),
    proxy: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Conexão manual com token de longa duração (diagnóstico ou App Review)."""
    meta_app = get_owned_meta_app(db, user.id, app_id)
    if not meta_app:
        return RedirectResponse(
            "/accounts?error=meta_app_invalid",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    meta_app_id = meta_app.id
    release_db_transaction(db)
    normalized, proxy_meta, proxy_err = _optional_meta_proxy(proxy)
    if proxy_err:
        return RedirectResponse(
            f"/accounts?error={proxy_err}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    token = access_token.strip()
    if len(token) < 20:
        return RedirectResponse(
            "/accounts?error=meta_token_invalid",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    try:
        profile = account_profile(token, proxy=normalized)
    except MetaInstagramError:
        return RedirectResponse(
            "/accounts?error=meta_token_invalid",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    error = _store_meta_account(
        db,
        user,
        token=token,
        expires_at=None,
        profile=profile,
        user_meta_app_id=meta_app_id,
        proxy=normalized,
        proxy_meta=proxy_meta,
    )
    if error:
        return RedirectResponse(
            f"/accounts?error={error}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        "/accounts/connected?ok=meta_connected",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/meta/callback/{app_id}")
def meta_oauth_callback(
    app_id: int,
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Conclui OAuth e cria/atualiza uma conta com provider=meta."""
    meta_app = get_owned_meta_app(db, user.id, app_id)
    if not meta_app:
        return RedirectResponse(
            "/accounts?error=meta_app_invalid",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    expected_state = str(request.session.pop("meta_oauth_state", "") or "")
    expected_user_id = request.session.pop("meta_oauth_user_id", None)
    expected_app_id = request.session.pop("meta_oauth_app_id", None)
    if error:
        return RedirectResponse(
            "/accounts?error=meta_denied",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if not code or not state or not secrets.compare_digest(state, expected_state):
        return RedirectResponse(
            "/accounts?error=meta_state",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if expected_user_id != user.id or expected_app_id != app_id:
        raise HTTPException(status_code=403, detail="Sessão OAuth inválida")

    oauth_proxy = str(request.session.pop("meta_oauth_proxy", "") or "") or None
    oauth_proxy_meta = request.session.pop("meta_oauth_proxy_meta", None) or {}

    creds = credentials_from_app(meta_app)
    try:
        token, expires_at = exchange_code(creds, code, proxy=oauth_proxy)
        profile = account_profile(token, proxy=oauth_proxy)
    except MetaInstagramError:
        return RedirectResponse(
            "/accounts?error=meta_exchange",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    store_error = _store_meta_account(
        db,
        user,
        token=token,
        expires_at=expires_at,
        profile=profile,
        user_meta_app_id=meta_app.id,
        proxy=oauth_proxy,
        proxy_meta=oauth_proxy_meta if oauth_proxy and isinstance(oauth_proxy_meta, dict) else None,
    )
    if store_error:
        return RedirectResponse(
            f"/accounts?error={store_error}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        "/accounts/connected?ok=meta_connected",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _revoke_meta_account(
    db: Session,
    *,
    ig_user_id: str | None,
    confirmation_code: str,
    user_meta_app_id: int | None = None,
) -> int:
    """Revoga token Meta e soft-delete das contas oficiais correspondentes."""
    if not ig_user_id:
        return 0
    q = select(InstagramAccount).where(
        InstagramAccount.provider == "meta",
        InstagramAccount.meta_ig_user_id == str(ig_user_id),
        InstagramAccount.status != "deleted",
    )
    if user_meta_app_id is not None:
        q = q.where(InstagramAccount.user_meta_app_id == user_meta_app_id)
    accounts = db.scalars(q).all()
    for acc in accounts:
        acc.status = "deleted"
        acc.encrypted_meta_access_token = None
        acc.meta_token_expires_at = None
        acc.session_json = None
        acc.encrypted_password = None
        acc.encrypted_totp_secret = None
        acc.login_email = None
        acc.last_error = f"Revogado pela Meta ({confirmation_code})"
        acc.automations.clear()
    if accounts:
        db.commit()
    return len(accounts)


@router.post("/meta/deauthorize/{app_id}")
async def meta_deauthorize(
    app_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Callback público: usuário removeu o app nas configurações da Meta."""
    from models.models import UserMetaApp

    meta_app = db.get(UserMetaApp, app_id)
    if not meta_app:
        raise HTTPException(status_code=404, detail="App não encontrado")
    creds = credentials_from_app(meta_app)
    form = await request.form()
    signed = str(form.get("signed_request") or "")
    try:
        payload = parse_signed_request(creds, signed)
    except MetaInstagramError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    user_id = str(payload.get("user_id") or "")
    code = f"deauth-{user_id or secrets.token_hex(6)}"
    _revoke_meta_account(
        db,
        ig_user_id=user_id or None,
        confirmation_code=code,
        user_meta_app_id=app_id,
    )
    return JSONResponse({"ok": True, "confirmation_code": code})


@router.post("/meta/data-deletion/{app_id}")
async def meta_data_deletion(
    app_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Callback público de exclusão de dados exigido pelo App Review."""
    from models.models import UserMetaApp

    meta_app = db.get(UserMetaApp, app_id)
    if not meta_app:
        raise HTTPException(status_code=404, detail="App não encontrado")
    creds = credentials_from_app(meta_app)
    form = await request.form()
    signed = str(form.get("signed_request") or "")
    try:
        payload = parse_signed_request(creds, signed)
    except MetaInstagramError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    user_id = str(payload.get("user_id") or "")
    code = f"del-{user_id or secrets.token_hex(6)}"
    _revoke_meta_account(
        db,
        ig_user_id=user_id or None,
        confirmation_code=code,
        user_meta_app_id=app_id,
    )
    status_url = f"{public_origin()}/data-deletion?code={code}"
    return JSONResponse({"url": status_url, "confirmation_code": code})


@router.get("/connected")
def connected_accounts(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    """Página de gestão das contas já conectadas."""
    accounts = _load_user_accounts(db, user)
    if mark_accounts_from_latest_auth_failures(db, accounts):
        db.commit()
    _backfill_proxy_meta(db, accounts)
    ok_key = request.query_params.get("ok")
    ok_msg = {
        "paused": "Conta pausada.",
        "resumed": "Conta retomada.",
        "proxy_updated": "Proxy atualizado com sucesso!",
        "account_added": "Conta conectada com sucesso!",
        "meta_connected": "Conta conectada pela API oficial da Meta!",
        "cookies_updated": "Cookies web atualizados! Já pode publicar Story com link.",
        "session_reconnected": "Sessão reconectada com sucesso!",
        "session_reconnected_2fa": "Informe o código 2FA e reconecte de novo.",
        "credentials_updated": "Credenciais / 2FA salvos.",
    }.get(ok_key or "")
    err_key = request.query_params.get("error")
    err_msg = {
        "proxy_vazio": "Informe um proxy válido.",
        "proxy_invalid": "Proxy inválido ou fora do ar. Teste antes de salvar.",
        "cookies_invalid": "Cookies inválidos. Cole o JSON do Cookie-Editor (precisa ter sessionid e csrftoken).",
        "cookies_login": "Não foi possível validar o sessionid desses cookies. Exporte de novo com a conta logada.",
        "cookies_meta": "Contas da API oficial Meta não usam cookies web.",
        "reconnect_meta": "Conta Meta: reconecte pela API oficial em Adicionar conta.",
        "reconnect_failed": "Não foi possível reconectar. Cole um sessionid ou cookies web novos.",
        "reconnect_password": "Senha ausente no cofre. Salve a senha em Credenciais / 2FA ou informe no reconectar.",
        "reconnect_sessionid": "Informe um sessionid válido.",
        "reconnect_2fa": "2FA necessário. Salve a chave TOTP em Credenciais / 2FA ou digite o código.",
        "reconnect_proxy": "Proxy ausente ou inválida — atualize a proxy antes de reconectar.",
        "reconnect_instagrapi_locked": (
            "Reconectar com senha só está liberado para contas autorizadas pelo dono. "
            "Use cookies web ou sessionid."
        ),
        "reconnect_login_failed": (
            "Login falhou: não foi possível autenticar no Instagram. "
            "Usuário/senha rejeitados ou serviço de login indisponível."
        ),
        "credentials_totp": "Chave TOTP inválida. Use Base32 ou otpauth:// do Authenticator.",
        "view_as_secrets": "No modo Ver como, cofre / TOTP / cookies / apps Meta ficam bloqueados (exceto o dono, que pode consultar).",
    }.get(err_key or "")
    offline = offline_accounts(db, user.id)
    cookie_flags = {
        acc.id: web_cookies_status(acc.encrypted_web_cookies)
        for acc in accounts
        if (acc.provider or "instagrapi") != "meta"
    }
    meta_display = _meta_account_display(accounts)
    cred_flags = _cred_flags_for_accounts(accounts)
    # Não segurar SELECT instagram_accounts aberto durante o render.
    release_db_transaction(db)
    return templates.TemplateResponse(
        "accounts_connected.html",
        {
            **_accounts_page_context(request, user, accounts, ok=ok_msg, error=err_msg or None),
            "offline_accounts": offline,
            "cookie_flags": cookie_flags,
            "meta_display": meta_display,
            "cred_flags": cred_flags,
        },
    )


@router.get("/vault")
def accounts_vault(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
    _: None = Depends(reject_view_as_secrets),
):
    """Cofre: email / senha / Authenticator por conta (página dedicada)."""
    accounts = _load_user_accounts(db, user)
    cred_flags = _cred_flags_for_accounts(accounts)
    meta_display = _meta_account_display(accounts)
    has_any_totp = any(bool(f.get("has_totp")) for f in cred_flags.values())
    release_db_transaction(db)
    return templates.TemplateResponse(
        "accounts_vault.html",
        {
            **_accounts_page_context(request, user, accounts),
            "cred_flags": cred_flags,
            "meta_display": meta_display,
            "has_any_totp": has_any_totp,
        },
    )


@router.get("/vault/codes")
def accounts_vault_codes(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(reject_view_as_secrets),
):
    """Todos os códigos TOTP do usuário — para o Cofre atualizar sem reload."""
    import time as _time

    accounts = _load_user_accounts(db, user)
    codes = []
    for acc in accounts:
        encrypted = getattr(acc, "encrypted_totp_secret", None)
        if not encrypted:
            continue
        plain = decrypt_secret(encrypted)
        if not plain:
            continue
        try:
            secret = normalize_totp_secret(plain)
            code, remaining = current_totp_code(secret)
        except TotpError:
            continue
        codes.append(
            {
                "account_id": int(acc.id),
                "username": acc.username,
                "code": code,
                "seconds_remaining": remaining,
            }
        )
    release_db_transaction(db)
    return JSONResponse(
        {
            "ok": True,
            "codes": codes,
            "server_time": int(_time.time()),
        }
    )


@router.post("/add")
def add_account(
    request: Request,
    auth_method: str = Form("password"),
    username: str = Form(""),
    password: str = Form(""),
    verification_code: str = Form(""),
    totp_secret: str = Form(""),
    sessionid: str = Form(""),
    session_json: str = Form(""),
    web_cookies: str = Form(""),
    proxy: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    username = username.strip().lstrip("@")
    proxy_raw = proxy
    try:
        proxy = validate_proxy_url(proxy)
    except ValueError:
        proxy = ""
    sid = clean_sessionid(sessionid)
    auth_method = (auth_method or "password").strip().lower()
    use_sessionid = auth_method == "sessionid"
    use_import = auth_method == "import"
    use_cookies = auth_method == "cookies"
    use_aiograpi = auth_method == "aiograpi"
    sessionid_only = bool(sid) and not password.strip() and not use_cookies
    form_state = {
        "auth_method": (
            "sessionid"
            if (use_sessionid or sessionid_only)
            else auth_method
        ),
        "username": username,
        "sessionid": sid or sessionid.strip(),
        "session_json": session_json.strip(),
        "web_cookies": web_cookies.strip(),
        "proxy": proxy_raw.strip() or proxy,
        "totp_secret": totp_secret.strip(),
    }

    # Instagrapi visível pra todos; login real só liberado (owner / allow_instagrapi).
    # Sem liberação: espera uns segundos e devolve erro de login “de verdade”.
    if is_instagrapi_auth_method(auth_method) and not can_use_instagrapi(user):
        release_db_transaction(db)
        fake_instagrapi_login_delay()
        accounts = _load_user_accounts(db, user)
        return templates.TemplateResponse(
            "accounts.html",
            _accounts_page_context(
                request,
                user,
                accounts,
                error=fake_instagrapi_login_error(),
                form=form_state,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

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

    # Auth deps já abriram SELECT; solta antes de testar proxy / instagrapi.
    release_db_transaction(db)
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
    encrypted_cookies = None
    encrypted_totp = None
    if totp_secret.strip():
        try:
            encrypted_totp = _encrypt_totp_secret(totp_secret)
        except TotpError as exc:
            accounts = _load_user_accounts(db, user)
            return templates.TemplateResponse(
                "accounts.html",
                _accounts_page_context(
                    request,
                    user,
                    accounts,
                    error=f"Chave TOTP inválida: {exc}",
                    form=form_state,
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    existing_for_totp = None
    if username:
        existing_for_totp = db.scalar(
            select(InstagramAccount).where(
                InstagramAccount.user_id == user.id,
                InstagramAccount.username == username,
            )
        )
    stored_totp = (
        getattr(existing_for_totp, "encrypted_totp_secret", None)
        if existing_for_totp is not None
        else None
    )

    try:
        if use_cookies:
            try:
                parsed_cookies = parse_web_cookies_blob(web_cookies)
            except WebCookiesError as exc:
                raise InstagramAuthError(str(exc)) from exc
            sid = clean_sessionid(parsed_cookies["sessionid"])
            settings_dict, resolved_user = login_with_sessionid(
                sid, proxy=proxy, username_hint=username or None
            )
            username = resolved_user or username
            if not username and parsed_cookies.get("ds_user_id"):
                # login_with_sessionid normalmente resolve o @; fallback mínimo
                username = username or f"user_{parsed_cookies['ds_user_id']}"
            encrypted_cookies = encrypt_web_cookies(parsed_cookies)
            encrypted_pw = encrypt_secret(password) if password else None
        elif use_import:
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
            settings_dict = _login_credentials_with_totp_retry(
                username=username,
                password=password,
                proxy=proxy,
                verification_code=verification_code.strip() or None,
                totp_encrypted=encrypted_totp or stored_totp,
                totp_raw=totp_secret.strip() or None,
                backend="aiograpi" if use_aiograpi else "instagrapi",
            )
            encrypted_pw = encrypt_secret(password)

    except InstagramTwoFactorRequired as exc:
        if request.headers.get("X-Requested-With") == "fetch":
            return JSONResponse(
                {
                    "needs_2fa": True,
                    "message": str(exc),
                    "has_totp": bool(encrypted_totp or stored_totp),
                },
                status_code=status.HTTP_403_FORBIDDEN,
            )
        accounts = _load_user_accounts(db, user)
        return templates.TemplateResponse(
            "accounts.html",
            {
                **_accounts_page_context(
                    request, user, accounts, error=str(exc), form=form_state
                ),
                "needs_2fa": True,
            },
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
            select(func.count(InstagramAccount.id)).where(
                InstagramAccount.user_id == user.id,
                InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
            )
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
        existing.provider = "aiograpi" if use_aiograpi else "instagrapi"
        existing.session_json = serialize_settings(settings_dict)
        if encrypted_pw:
            existing.encrypted_password = encrypted_pw
        if encrypted_totp:
            existing.encrypted_totp_secret = encrypted_totp
        if encrypted_cookies:
            existing.encrypted_web_cookies = encrypted_cookies
        existing.meta_ig_user_id = None
        existing.encrypted_meta_access_token = None
        existing.meta_token_expires_at = None
        _set_account_proxy(existing, proxy, proxy_meta)
        existing.status = "active"
        existing.last_login_at = dt.datetime.utcnow()
        existing.last_error = None
    else:
        new_acc = InstagramAccount(
            user_id=user.id,
            username=username,
            provider="aiograpi" if use_aiograpi else "instagrapi",
            encrypted_password=encrypted_pw,
            encrypted_totp_secret=encrypted_totp,
            proxy=proxy,
            session_json=serialize_settings(settings_dict),
            encrypted_web_cookies=encrypted_cookies,
            status="active",
            last_login_at=dt.datetime.utcnow(),
        )
        _set_account_proxy(new_acc, proxy, proxy_meta)
        db.add(new_acc)
    db.commit()
    return RedirectResponse("/accounts/connected?ok=account_added", status_code=status.HTTP_303_SEE_OTHER)


def _get_owned_account(db: Session, account_id: int, user: User) -> InstagramAccount:
    acc = db.get(InstagramAccount, account_id)
    if not acc or acc.user_id != user.id or acc.status == "deleted":
        raise HTTPException(status_code=404, detail="Conta não encontrada")
    return acc


@router.post("/test-proxy")
def test_proxy(proxy: str = Form(...)):
    """Testa proxy sem salvar (AJAX)."""
    try:
        validate_proxy_url(proxy)
    except ValueError as exc:
        return JSONResponse({"ok": False, "ip": None, "error": str(exc), "geo": None})
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
    try:
        normalized = validate_proxy_url(proxy)
    except ValueError:
        return RedirectResponse(
            "/accounts/connected?error=proxy_invalid",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if not normalized:
        return RedirectResponse(
            "/accounts/connected?error=proxy_vazio",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    release_db_transaction(db)
    diag = diagnose_proxy(proxy)
    if not diag["ok"]:
        return RedirectResponse(
            "/accounts/connected?error=proxy_invalid",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    acc = _get_owned_account(db, account_id, user)
    _set_account_proxy(acc, normalized, diag)
    if acc.status == "proxy_down":
        acc.status = "active"
    acc.last_error = None
    acc.last_health_check_at = None
    db.commit()
    return RedirectResponse(
        "/accounts/connected?ok=proxy_updated",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{account_id}/update-web-cookies")
def update_account_web_cookies(
    account_id: int,
    web_cookies: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(reject_view_as_secrets),
):
    """Atualiza o jar de cookies web (Cookie-Editor) para Story com link."""
    acc = _get_owned_account(db, account_id, user)
    if (acc.provider or "instagrapi") == "meta":
        return RedirectResponse(
            "/accounts/connected?error=cookies_meta",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    try:
        parsed = parse_web_cookies_blob(web_cookies)
    except WebCookiesError:
        return RedirectResponse(
            "/accounts/connected?error=cookies_invalid",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    sid = clean_sessionid(parsed["sessionid"])
    proxy = acc.proxy
    username_hint = acc.username
    release_db_transaction(db)
    try:
        settings_dict, resolved_user = login_with_sessionid(
            sid,
            proxy=proxy,
            username_hint=username_hint,
        )
    except InstagramAuthError:
        return RedirectResponse(
            "/accounts/connected?error=cookies_login",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    acc = _get_owned_account(db, account_id, user)
    acc.session_json = serialize_settings(settings_dict)
    acc.encrypted_web_cookies = encrypt_web_cookies(parsed)
    if resolved_user:
        acc.username = resolved_user
    acc.status = "active"
    acc.last_login_at = dt.datetime.utcnow()
    acc.last_error = None
    db.commit()
    return RedirectResponse(
        "/accounts/connected?ok=cookies_updated",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{account_id}/reconnect")
def reconnect_account_session(
    account_id: int,
    mode: str = Form("auto"),
    password: str = Form(""),
    verification_code: str = Form(""),
    sessionid: str = Form(""),
    web_cookies: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Reconecta sessão instagrapi / cookies web (form POST — redirect)."""
    acc = _get_owned_account(db, account_id, user)
    mode_norm = (mode or "auto").strip().lower()
    if mode_norm == "password" and not can_use_instagrapi(user):
        release_db_transaction(db)
        fake_instagrapi_login_delay()
        return RedirectResponse(
            "/accounts/connected?error=reconnect_login_failed",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    result = _perform_account_reconnect(
        db,
        acc,
        mode=mode,
        password=password,
        verification_code=verification_code,
        sessionid=sessionid,
        web_cookies=web_cookies,
    )
    if result["status"] == "connected":
        db.commit()
        return RedirectResponse(
            "/accounts/connected?ok=session_reconnected",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    err_map = {
        "needs_2fa": "reconnect_2fa",
        "meta": "reconnect_meta",
        "proxy": "reconnect_proxy",
        "sessionid": "reconnect_sessionid",
        "password": "reconnect_password",
        "cookies_invalid": "cookies_invalid",
    }
    err_key = err_map.get(result.get("error_code") or result["status"], "reconnect_failed")
    return RedirectResponse(
        f"/accounts/connected?error={err_key}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{account_id}/reconnect/api")
def reconnect_account_api(
    account_id: int,
    body: ReconnectApiBody = Body(default_factory=ReconnectApiBody),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Reconecta sessão instagrapi/web via AJAX (estilo postagemIG — botão Reconectar)."""
    acc = _get_owned_account(db, account_id, user)
    mode_norm = (getattr(body, "mode", None) or "auto").strip().lower()
    if mode_norm == "password" and not can_use_instagrapi(user):
        release_db_transaction(db)
        fake_instagrapi_login_delay()
        return JSONResponse(
            {
                "status": "error",
                "error_code": "login_failed",
                "message": fake_instagrapi_login_error(),
            },
            status_code=400,
        )
    result = _perform_account_reconnect(
        db,
        acc,
        mode=body.mode,
        password=body.password,
        verification_code=body.verification_code,
        sessionid=body.sessionid,
        web_cookies=body.web_cookies,
    )
    if result["status"] == "connected":
        db.commit()
    return JSONResponse(result)


def _perform_account_reconnect(
    db: Session,
    acc: InstagramAccount,
    *,
    mode: str = "auto",
    password: str = "",
    verification_code: str = "",
    sessionid: str = "",
    web_cookies: str = "",
) -> dict:
    """Lógica compartilhada de reconexão instagrapi / cookies web.

    Libera a transação do SELECT antes de qualquer login/rede.
    """
    if (acc.provider or "instagrapi") == "meta":
        return {
            "status": "error",
            "error_code": "meta",
            "message": "Conta Meta: reconecte pela API oficial em Adicionar conta.",
        }
    if not acc.proxy or not str(acc.proxy).strip():
        return {
            "status": "error",
            "error_code": "proxy",
            "message": "Proxy ausente — atualize a proxy antes de reconectar.",
        }

    account_id = acc.id
    proxy = acc.proxy
    username = acc.username
    session_json = acc.session_json
    encrypted_web_cookies = acc.encrypted_web_cookies
    encrypted_password = acc.encrypted_password
    encrypted_totp_secret = getattr(acc, "encrypted_totp_secret", None)
    account_provider = (acc.provider or "instagrapi").strip().lower()
    release_db_transaction(db)

    mode_norm = (mode or "auto").strip().lower()
    settings_dict = None
    resolved_user: str | None = None
    new_encrypted_web: str | None = None
    try:
        if mode_norm == "cookies":
            if account_provider == "aiograpi":
                return {
                    "status": "error",
                    "error_code": "mode",
                    "message": "Conta API async: reconecte com senha (ou auto).",
                }
            parsed = parse_web_cookies_blob(web_cookies)
            sid = clean_sessionid(parsed["sessionid"])
            settings_dict, resolved_user = login_with_sessionid(
                sid,
                proxy=proxy,
                username_hint=username,
            )
            new_encrypted_web = encrypt_web_cookies(parsed)
        elif mode_norm == "sessionid":
            if account_provider == "aiograpi":
                return {
                    "status": "error",
                    "error_code": "mode",
                    "message": "Conta API async: reconecte com senha (ou auto).",
                }
            sid = clean_sessionid(sessionid)
            if not sid:
                return {
                    "status": "error",
                    "error_code": "sessionid",
                    "message": "Informe um sessionid válido.",
                }
            settings_dict, resolved_user = login_with_sessionid(
                sid,
                proxy=proxy,
                username_hint=username,
            )
            merged = merge_sessionid_into_web_cookies(encrypted_web_cookies, sid)
            if merged:
                new_encrypted_web = merged
        elif mode_norm in ("auto", "session"):
            try:
                if account_provider == "aiograpi":
                    settings_dict = aio_ig.try_refresh_session(
                        settings_dict=deserialize_settings(session_json),
                        proxy=proxy,
                        username=username,
                        password=None,
                    )
                else:
                    settings_dict = try_refresh_session(
                        settings_dict=deserialize_settings(session_json),
                        proxy=proxy,
                        username=username,
                        password=None,
                    )
            except InstagramAuthError:
                # Sessão morta: tenta senha+TOTP do cofre antes de exigir sessionid.
                stored_pw = decrypt_secret(encrypted_password)
                if stored_pw:
                    try:
                        settings_dict = _login_credentials_with_totp_retry(
                            username=username,
                            password=stored_pw,
                            proxy=proxy,
                            verification_code=(verification_code or "").strip() or None,
                            totp_encrypted=encrypted_totp_secret,
                            backend=account_provider,
                        )
                    except InstagramTwoFactorRequired as exc:
                        return {
                            "status": "needs_2fa",
                            "message": str(exc),
                            "username": username,
                            "has_totp": bool(encrypted_totp_secret),
                        }
                elif account_provider == "aiograpi":
                    return {
                        "status": "error",
                        "error_code": "password",
                        "message": (
                            "Sessão async expirada. Salve senha+TOTP em Credenciais / 2FA "
                            "ou informe a senha no reconectar."
                        ),
                    }
                else:
                    cookies = decrypt_web_cookies(encrypted_web_cookies)
                    sid = clean_sessionid((cookies or {}).get("sessionid") or "")
                    if not sid:
                        return {
                            "status": "error",
                            "error_code": "sessionid",
                            "message": (
                                "Sessão expirada. Salve senha+TOTP em Credenciais / 2FA "
                                "ou cole um sessionid / cookies web."
                            ),
                        }
                    settings_dict, resolved_user = login_with_sessionid(
                        sid,
                        proxy=proxy,
                        username_hint=username,
                    )
            if account_provider != "aiograpi":
                new_sid = extract_sessionid_from_settings(settings_dict)
                merged = merge_sessionid_into_web_cookies(
                    new_encrypted_web or encrypted_web_cookies, new_sid
                )
                if merged:
                    new_encrypted_web = merged
        elif mode_norm == "password":
            pw = (password or "").strip() or decrypt_secret(encrypted_password)
            if not pw:
                return {
                    "status": "error",
                    "error_code": "password",
                    "message": "Senha ausente. Salve em Credenciais / 2FA ou informe no reconectar.",
                }
            settings_dict = _login_credentials_with_totp_retry(
                username=username,
                password=pw,
                proxy=proxy,
                verification_code=(verification_code or "").strip() or None,
                totp_encrypted=encrypted_totp_secret,
                backend=account_provider if account_provider == "aiograpi" else "instagrapi",
            )
        else:
            return {
                "status": "error",
                "error_code": "mode",
                "message": "Modo de reconexão inválido. Use sessionid, cookies ou password.",
            }

        acc = db.get(InstagramAccount, account_id)
        if not acc or acc.status == "deleted":
            return {
                "status": "error",
                "error_code": "auth",
                "message": "Conta não encontrada.",
            }
        acc.session_json = serialize_settings(settings_dict)
        if new_encrypted_web is not None:
            acc.encrypted_web_cookies = new_encrypted_web
        if resolved_user:
            acc.username = resolved_user
        if mode_norm == "password" and (password or "").strip():
            acc.encrypted_password = encrypt_secret(password.strip())
        acc.status = "active"
        acc.last_login_at = dt.datetime.utcnow()
        acc.last_error = None
        return {
            "status": "connected",
            "message": "Sessão reconectada com sucesso.",
            "username": acc.username,
        }
    except InstagramTwoFactorRequired as exc:
        return {
            "status": "needs_2fa",
            "message": str(exc),
            "username": username,
            "has_totp": bool(encrypted_totp_secret),
        }
    except WebCookiesError:
        return {
            "status": "error",
            "error_code": "cookies_invalid",
            "message": "Cookies inválidos. Cole JSON do Cookie-Editor (sessionid + csrftoken).",
        }
    except InstagramAuthError as exc:
        return {
            "status": "error",
            "error_code": "auth",
            "message": str(exc)[:500],
        }


@router.get("/{account_id}/credentials")
def get_account_credentials_status(
    account_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(reject_view_as_secrets),
):
    """Status do cofre (senha/TOTP sem plaintext; email pode voltar)."""
    acc = _get_owned_account(db, account_id, user)
    return JSONResponse(
        {
            "account_id": acc.id,
            "username": acc.username,
            "login_email": (getattr(acc, "login_email", None) or "").strip() or None,
            "has_password": bool(acc.encrypted_password),
            "has_totp": bool(getattr(acc, "encrypted_totp_secret", None)),
            "has_email": bool((getattr(acc, "login_email", None) or "").strip()),
        }
    )


@router.post("/{account_id}/credentials")
def update_account_credentials(
    account_id: int,
    body: CredentialsBody = Body(default_factory=CredentialsBody),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(reject_view_as_secrets),
):
    """Atualiza email, senha e/ou chave TOTP no cofre da conta."""
    acc = _get_owned_account(db, account_id, user)
    if body.clear_password:
        acc.encrypted_password = None
    elif (body.password or "").strip():
        acc.encrypted_password = encrypt_secret(body.password.strip())

    if body.clear_totp:
        acc.encrypted_totp_secret = None
    elif (body.totp_secret or "").strip():
        try:
            encrypted = _encrypt_totp_secret(body.totp_secret)
        except TotpError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)},
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if not encrypted:
            return JSONResponse(
                {"ok": False, "error": "Chave TOTP vazia após normalizar."},
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        acc.encrypted_totp_secret = encrypted

    if body.clear_email:
        acc.login_email = None
    elif body.login_email is not None:
        email = str(body.login_email).strip()[:255]
        acc.login_email = email or None

    try:
        db.commit()
        db.refresh(acc)
    except Exception as exc:
        db.rollback()
        return JSONResponse(
            {
                "ok": False,
                "error": f"Não foi possível gravar no banco: {exc}"[:400],
            },
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return JSONResponse(
        {
            "ok": True,
            "username": acc.username,
            "login_email": (acc.login_email or "").strip() or None,
            "has_password": bool(acc.encrypted_password),
            "has_totp": bool(getattr(acc, "encrypted_totp_secret", None)),
            "has_email": bool((acc.login_email or "").strip()),
        }
    )


@router.get("/{account_id}/totp-code")
def get_account_totp_code(
    account_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(reject_view_as_secrets),
):
    """Código TOTP atual (6 dígitos) — nunca devolve o secret."""
    acc = _get_owned_account(db, account_id, user)
    encrypted = getattr(acc, "encrypted_totp_secret", None)
    if not encrypted:
        return JSONResponse(
            {"ok": False, "error": "TOTP não configurado nesta conta."},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    plain = decrypt_secret(encrypted)
    if not plain:
        return JSONResponse(
            {"ok": False, "error": "Não foi possível ler a chave TOTP."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    try:
        secret = normalize_totp_secret(plain)
        code, remaining = current_totp_code(secret)
    except TotpError as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc)},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return JSONResponse(
        {
            "ok": True,
            "code": code,
            "seconds_remaining": remaining,
            "username": acc.username,
        }
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
    return RedirectResponse("/accounts/connected?ok=paused", status_code=status.HTTP_303_SEE_OTHER)


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
    return RedirectResponse("/accounts/connected?ok=resumed", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{account_id}/delete")
def delete_account(
    account_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    acc = db.get(InstagramAccount, account_id)
    if not acc or acc.user_id != user.id or acc.status == "deleted":
        raise HTTPException(status_code=404, detail="Conta não encontrada")
    # Soft delete: some do painel/automações, mas preserva logs e gráficos históricos.
    acc.status = "deleted"
    acc.session_json = None
    acc.encrypted_password = None
    acc.encrypted_totp_secret = None
    acc.login_email = None
    acc.encrypted_meta_access_token = None
    acc.meta_token_expires_at = None
    acc.last_error = "Conta removida do painel"
    acc.automations.clear()
    db.commit()
    return RedirectResponse("/accounts/connected", status_code=status.HTTP_303_SEE_OTHER)
