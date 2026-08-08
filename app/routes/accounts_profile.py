"""Editar bio / link / foto de perfil em lote (instagrapi + aiograpi)."""
from __future__ import annotations

import logging
import random
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from starlette.datastructures import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_effective_user, reject_view_as_secrets
from app.security import decrypt_secret
from app.templating import templates
from app.utils.instagrapi_access import can_use_instagrapi
from app.utils.spintax import SpintaxError, has_spintax, spin, validate as validate_spintax
from core import aiograpi_client as aio_ig
from core.database import get_db, release_db_transaction
from core.instagram import (
    InstagramAuthError,
    InstagramTwoFactorRequired,
    change_profile_picture,
    deserialize_settings,
    edit_profile,
    get_ready_client,
    serialize_settings,
    set_biography,
    try_refresh_session,
)
from models.models import InstagramAccount, User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/accounts/profile-edit", tags=["accounts-profile"])

VISIBLE = ("active", "paused", "needs_login", "proxy_down")
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_ACCOUNTS = 40
BIO_MAX = 220


def _normalize_url(raw: str) -> str:
    url = (raw or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def _url_looks_valid(url: str) -> bool:
    rest = url.split("://", 1)[-1]
    return bool(rest) and " " not in rest and "." in rest


def _upload_from_form(form, field: str) -> UploadFile | None:
    """Pega o primeiro arquivo real do multipart (ignora part vazia)."""
    values = form.getlist(field) if hasattr(form, "getlist") else [form.get(field)]
    for value in values:
        if value is None:
            continue
        filename = (getattr(value, "filename", None) or "").strip()
        if not filename:
            continue
        # UploadFile / SpooledTemporaryFile do Starlette
        if hasattr(value, "file") or hasattr(value, "read"):
            return value  # type: ignore[return-value]
    return None


def _eligible_accounts(db: Session, user: User) -> list[InstagramAccount]:
    rows = list(
        db.scalars(
            select(InstagramAccount)
            .where(
                InstagramAccount.user_id == user.id,
                InstagramAccount.status.in_(VISIBLE),
            )
            .order_by(InstagramAccount.username)
        ).all()
    )
    out: list[InstagramAccount] = []
    for acc in rows:
        provider = (acc.provider or "instagrapi").lower()
        if provider == "meta":
            continue
        out.append(acc)
    return out


def _apply_one(
    acc: InstagramAccount,
    *,
    biography: str | None,
    external_url: str | None,
    pic_path: Path | None,
) -> tuple[bool, str]:
    proxy = (acc.proxy or "").strip()
    if not proxy:
        return False, "proxy ausente"
    settings_dict = deserialize_settings(acc.session_json)
    password = decrypt_secret(acc.encrypted_password)
    username = acc.username
    provider = (acc.provider or "instagrapi").lower()
    did: list[str] = []
    if biography is not None:
        did.append("bio")
    if external_url is not None:
        did.append("link")
    if pic_path is not None:
        did.append("foto")

    try:
        if provider == "aiograpi":
            settings_dict = aio_ig.try_refresh_session(
                settings_dict=settings_dict,
                proxy=proxy,
                username=username,
                password=password,
            )
            if external_url is not None:
                # account_edit cobre bio + link numa requisição só.
                settings_dict = aio_ig.edit_profile(
                    settings_dict,
                    proxy,
                    biography=biography,
                    external_url=external_url,
                )
            elif biography is not None:
                settings_dict = aio_ig.set_biography(settings_dict, proxy, biography)
            if pic_path is not None:
                settings_dict = aio_ig.change_profile_picture(
                    settings_dict, proxy, pic_path
                )
            acc.session_json = serialize_settings(settings_dict)
        else:
            settings_dict = try_refresh_session(
                settings_dict=settings_dict,
                proxy=proxy,
                username=username,
                password=password,
            )
            cl = get_ready_client(
                settings_dict=settings_dict,
                proxy=proxy,
                username=username,
                password=password,
            )
            if external_url is not None:
                edit_profile(cl, biography=biography, external_url=external_url)
            elif biography is not None:
                set_biography(cl, biography)
            if pic_path is not None:
                change_profile_picture(cl, pic_path)
            acc.session_json = serialize_settings(cl.get_settings())
        acc.status = "active"
        acc.last_error = None
        return True, "+".join(did) if did else "ok"
    except (InstagramAuthError, InstagramTwoFactorRequired) as exc:
        acc.status = "needs_login"
        acc.last_error = str(exc)[:400]
        return False, str(exc)[:240]
    except Exception as exc:
        log.exception("profile-edit falhou @%s", username)
        return False, str(exc)[:240]


def _render(
    request: Request,
    user: User,
    accounts: list[InstagramAccount],
    *,
    can_edit: bool = True,
    results: list[dict] | None = None,
    error: str | None = None,
    summary: str | None = None,
    bio_value: str = "",
    link_value: str = "",
    status_code: int = 200,
):
    return templates.TemplateResponse(
        "accounts_profile_edit.html",
        {
            "request": request,
            "user": user,
            "accounts": accounts,
            "can_edit": can_edit,
            "results": results,
            "error": error,
            "summary": summary,
            "bio_value": bio_value,
            "link_value": link_value,
        },
        status_code=status_code,
    )


@router.get("")
def profile_edit_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    reject_view_as_secrets(request)
    accounts = _eligible_accounts(db, user)
    release_db_transaction(db)
    return _render(request, user, accounts, can_edit=can_use_instagrapi(user))


@router.post("")
async def profile_edit_apply(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    reject_view_as_secrets(request)
    form = await request.form()
    biography = str(form.get("biography") or "")
    link_raw = str(form.get("external_url") or "")
    remove_link = str(form.get("remove_link") or "") in ("1", "on", "true")
    account_ids_raw = form.getlist("account_ids")
    profile_pic = _upload_from_form(form, "profile_pic")

    accounts_all = _eligible_accounts(db, user)
    by_id = {a.id: a for a in accounts_all}

    if not can_use_instagrapi(user):
        release_db_transaction(db)
        return _render(
            request,
            user,
            accounts_all,
            can_edit=False,
            error="Edição de perfil (API privada) só para contas liberadas.",
            bio_value=biography,
            link_value=link_raw,
            status_code=403,
        )

    bio_template = (biography or "").strip() or None
    if bio_template:
        try:
            validate_spintax(bio_template)
        except SpintaxError as exc:
            return _render(
                request,
                user,
                accounts_all,
                error=f"Spintax inválido na bio: {exc}",
                bio_value=biography,
                link_value=link_raw,
                status_code=400,
            )

    # Link vazio = não mexe. Marcar "remover" limpa o link atual.
    if remove_link:
        external_url: str | None = ""
    elif link_raw.strip():
        external_url = _normalize_url(link_raw)
        if not _url_looks_valid(external_url):
            return _render(
                request,
                user,
                accounts_all,
                error="Link inválido. Use algo como instagram.com/seuperfil.",
                bio_value=biography,
                link_value=link_raw,
                status_code=400,
            )
    else:
        external_url = None

    ids: list[int] = []
    for raw in account_ids_raw:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    ids = list(dict.fromkeys(ids))[:MAX_ACCOUNTS]
    selected = [by_id[i] for i in ids if i in by_id]

    pic_path: Path | None = None
    tmp_dir: Path | None = None
    has_file = profile_pic is not None

    if not selected:
        return _render(
            request,
            user,
            accounts_all,
            error="Selecione ao menos uma conta.",
            bio_value=biography,
            link_value=link_raw,
            status_code=400,
        )

    if bio_template is None and external_url is None and not has_file:
        return _render(
            request,
            user,
            accounts_all,
            error="Informe a bio, o link e/ou envie uma foto de perfil.",
            bio_value=biography,
            link_value=link_raw,
            status_code=400,
        )

    try:
        if has_file and profile_pic is not None:
            suffix = Path(profile_pic.filename or "pic.jpg").suffix.lower() or ".jpg"
            if suffix not in IMAGE_EXT:
                return _render(
                    request,
                    user,
                    accounts_all,
                    error="Foto: use JPG, PNG ou WEBP.",
                    bio_value=biography,
                    link_value=link_raw,
                    status_code=400,
                )
            tmp_dir = Path(tempfile.mkdtemp(prefix="ig_profile_"))
            pic_path = tmp_dir / f"avatar{suffix}"
            fileobj = getattr(profile_pic, "file", None) or profile_pic
            with pic_path.open("wb") as fh:
                if hasattr(fileobj, "seek"):
                    try:
                        fileobj.seek(0)
                    except Exception:
                        pass
                shutil.copyfileobj(fileobj, fh)
            if pic_path.stat().st_size <= 0:
                return _render(
                    request,
                    user,
                    accounts_all,
                    error="Arquivo de foto vazio. Escolha a imagem de novo.",
                    bio_value=biography,
                    link_value=link_raw,
                    status_code=400,
                )

        jobs = [(a.id, a.username) for a in selected]
        release_db_transaction(db)

        rng = random.Random()
        spun_bio = has_spintax(bio_template)
        results: list[dict] = []
        for account_id, username in jobs:
            acc = db.get(InstagramAccount, account_id)
            if not acc or acc.user_id != user.id or acc.status == "deleted":
                results.append(
                    {
                        "username": username,
                        "ok": False,
                        "detail": "conta não encontrada",
                    }
                )
                continue
            bio = spin(bio_template, rng)[:BIO_MAX] if bio_template else None
            ok, detail = _apply_one(
                acc,
                biography=bio,
                external_url=external_url,
                pic_path=pic_path,
            )
            db.commit()
            results.append(
                {
                    "username": username,
                    "ok": ok,
                    "detail": detail,
                    "bio": bio if (ok and spun_bio) else None,
                }
            )

        accounts_all = _eligible_accounts(db, user)
        release_db_transaction(db)
        ok_n = sum(1 for r in results if r["ok"])
        return _render(
            request,
            user,
            accounts_all,
            results=results,
            summary=f"{ok_n}/{len(results)} conta(s) atualizada(s).",
            bio_value=biography,
            link_value=link_raw,
        )
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
