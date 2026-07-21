"""CRUD de contas do Instagram conectadas."""
from __future__ import annotations

import datetime as dt
import json
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import get_current_user
from app.security import encrypt_secret
from app.templating import templates
from app.config import get_settings
from app.utils.account_health import offline_accounts
from app.utils.proxy import (
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
from app.utils.auth_failures import mark_accounts_from_latest_auth_failures
from app.utils.meta_apps import credentials_from_app, get_owned_meta_app, list_user_meta_apps
from app.utils.platform_settings import META_TOKEN_YOUTUBE_URL, get_platform_setting
from core.database import get_db
from core.meta_instagram import (
    MetaInstagramError,
    account_profile,
    authorization_url,
    exchange_code,
    parse_signed_request,
    public_origin,
)
from core.instagram import (
    InstagramAuthError,
    InstagramTwoFactorRequired,
    login_with_credentials,
    login_with_imported_settings,
    login_with_sessionid,
    serialize_settings,
)
from core.web_cookies import (
    WebCookiesError,
    encrypt_web_cookies,
    parse_web_cookies_blob,
    web_cookies_status,
)

from models.models import InstagramAccount, User

router = APIRouter(prefix="/accounts", tags=["accounts"])
VISIBLE_ACCOUNT_STATUSES = ("active", "paused", "needs_login", "proxy_down", "banned")


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
        "needs_2fa": False,
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
        .where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.status.in_(VISIBLE_ACCOUNT_STATUSES),
        )
        .order_by(InstagramAccount.username.asc())
    ).all()


def _store_meta_account(
    db: Session,
    user: User,
    *,
    token: str,
    expires_at: dt.datetime | None,
    profile: dict[str, str],
    user_meta_app_id: int | None,
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
    existing.session_json = None
    existing.proxy = None
    existing.proxy_ip = None
    existing.proxy_geo = None
    existing.status = "active"
    existing.last_error = None
    existing.last_login_at = dt.datetime.utcnow()
    db.commit()
    return None


@router.get("")
def list_accounts(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
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
    }.get(request.query_params.get("error") or "")
    meta_apps_list = list_user_meta_apps(db, user.id)
    token_youtube_url = get_platform_setting(
        db, META_TOKEN_YOUTUBE_URL, default="https://youtu.be/EA0iEb92sZg"
    )
    return templates.TemplateResponse(
        "accounts.html",
        {
            **_accounts_page_context(request, user, accounts, ok=ok_msg, error=error_msg),
            "meta_apps": meta_apps_list,
            "token_youtube_url": token_youtube_url,
        },
    )


@router.get("/meta/connect")
def connect_meta_account(
    request: Request,
    app_id: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Inicia o Business Login oficial do Instagram."""
    meta_app = get_owned_meta_app(db, user.id, app_id) if app_id else None
    if not meta_app:
        return RedirectResponse(
            "/accounts?error=meta_no_app" if app_id else "/accounts?error=meta_not_configured",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    state = secrets.token_urlsafe(32)
    creds = credentials_from_app(meta_app)
    request.session["meta_oauth_state"] = state
    request.session["meta_oauth_user_id"] = user.id
    request.session["meta_oauth_app_id"] = meta_app.id
    return RedirectResponse(authorization_url(creds, state), status_code=status.HTTP_302_FOUND)


@router.post("/meta/connect-token")
def connect_meta_account_with_token(
    access_token: str = Form(...),
    app_id: int = Form(...),
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
    token = access_token.strip()
    if len(token) < 20:
        return RedirectResponse(
            "/accounts?error=meta_token_invalid",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    try:
        profile = account_profile(token)
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
        user_meta_app_id=meta_app.id,
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

    creds = credentials_from_app(meta_app)
    try:
        token, expires_at = exchange_code(creds, code)
        profile = account_profile(token)
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
    user: User = Depends(get_current_user),
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
    }.get(ok_key or "")
    err_key = request.query_params.get("error")
    err_msg = {
        "proxy_vazio": "Informe um proxy válido.",
        "proxy_invalid": "Proxy inválido ou fora do ar. Teste antes de salvar.",
        "cookies_invalid": "Cookies inválidos. Cole o JSON do Cookie-Editor (precisa ter sessionid e csrftoken).",
        "cookies_login": "Não foi possível validar o sessionid desses cookies. Exporte de novo com a conta logada.",
        "cookies_meta": "Contas da API oficial Meta não usam cookies web.",
    }.get(err_key or "")
    offline = offline_accounts(db, user.id)
    cookie_flags = {
        acc.id: web_cookies_status(acc.encrypted_web_cookies)
        for acc in accounts
        if (acc.provider or "instagrapi") != "meta"
    }
    return templates.TemplateResponse(
        "accounts_connected.html",
        {
            **_accounts_page_context(request, user, accounts, ok=ok_msg, error=err_msg or None),
            "offline_accounts": offline,
            "cookie_flags": cookie_flags,
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
    web_cookies: str = Form(""),
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
    use_cookies = auth_method == "cookies"
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
    encrypted_cookies = None
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
        existing.provider = "instagrapi"
        existing.session_json = serialize_settings(settings_dict)
        if encrypted_pw:
            existing.encrypted_password = encrypted_pw
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
            provider="instagrapi",
            encrypted_password=encrypted_pw,
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
            "/accounts/connected?error=proxy_vazio",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    diag = diagnose_proxy(proxy)
    if not diag["ok"]:
        return RedirectResponse(
            "/accounts/connected?error=proxy_invalid",
            status_code=status.HTTP_303_SEE_OTHER,
        )
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
    try:
        settings_dict, resolved_user = login_with_sessionid(
            sid,
            proxy=acc.proxy,
            username_hint=acc.username,
        )
    except InstagramAuthError:
        return RedirectResponse(
            "/accounts/connected?error=cookies_login",
            status_code=status.HTTP_303_SEE_OTHER,
        )

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
    acc.encrypted_meta_access_token = None
    acc.meta_token_expires_at = None
    acc.last_error = "Conta removida do painel"
    acc.automations.clear()
    db.commit()
    return RedirectResponse("/accounts/connected", status_code=status.HTTP_303_SEE_OTHER)
