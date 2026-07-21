"""Apps Meta cadastrados pelo usuário (Meus Apps)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import get_current_user
from app.security import encrypt_secret
from app.templating import templates
from app.utils.meta_apps import (
    get_owned_meta_app,
    list_user_meta_apps,
    mask_ig_secret,
)
from app.utils.platform_settings import META_SETUP_YOUTUBE_URL, get_platform_setting
from core.database import get_db
from core.meta_instagram import meta_app_urls, public_origin
from models.models import InstagramAccount, User, UserMetaApp

router = APIRouter(prefix="/accounts/meta-apps", tags=["meta-apps"])


@router.get("")
def meta_apps_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tab = request.query_params.get("tab") or "apps"
    apps = list_user_meta_apps(db, user.id)
    youtube_url = get_platform_setting(db, META_SETUP_YOUTUBE_URL)
    error_msg = {
        "name": "Informe um nome para o app.",
        "ig_app_id": "Informe o Instagram App ID.",
        "ig_app_secret": "Informe o App Secret.",
        "duplicate": "Você já cadastrou este App ID.",
        "not_found": "App não encontrado.",
        "linked": "Remova ou reconecte as contas ligadas a este app antes de excluir.",
    }.get(request.query_params.get("error") or "")
    ok_msg = {
        "created": "App cadastrado com sucesso!",
        "updated": "App atualizado.",
        "deleted": "App removido.",
    }.get(request.query_params.get("ok") or "")

    app_rows = []
    from app.security import decrypt_secret

    for app in apps:
        app_rows.append(
            {
                "app": app,
                "secret_masked": mask_ig_secret(decrypt_secret(app.encrypted_ig_app_secret)),
                "urls": meta_app_urls(app.id),
            }
        )

    edit_id = request.query_params.get("edit")
    edit_app = None
    if edit_id:
        try:
            edit_app = get_owned_meta_app(db, user.id, int(edit_id))
        except ValueError:
            edit_app = None

    return templates.TemplateResponse(
        "meta_apps.html",
        {
            "request": request,
            "user": user,
            "tab": tab if tab in ("apps", "setup") else "apps",
            "apps": apps,
            "app_rows": app_rows,
            "public_origin": public_origin(),
            "youtube_url": youtube_url,
            "error": error_msg,
            "ok": ok_msg,
            "edit_app": edit_app,
        },
    )


@router.post("")
def create_meta_app(
    name: str = Form(...),
    ig_app_id: str = Form(...),
    ig_app_secret: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    name = name.strip()
    ig_app_id = ig_app_id.strip()
    ig_app_secret = ig_app_secret.strip()
    if not name:
        return RedirectResponse(
            "/accounts/meta-apps?error=name",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if not ig_app_id:
        return RedirectResponse(
            "/accounts/meta-apps?error=ig_app_id",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if len(ig_app_secret) < 8:
        return RedirectResponse(
            "/accounts/meta-apps?error=ig_app_secret",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    exists = db.scalar(
        select(UserMetaApp.id).where(
            UserMetaApp.user_id == user.id,
            UserMetaApp.ig_app_id == ig_app_id,
        )
    )
    if exists:
        return RedirectResponse(
            "/accounts/meta-apps?error=duplicate",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    db.add(
        UserMetaApp(
            user_id=user.id,
            name=name,
            ig_app_id=ig_app_id,
            encrypted_ig_app_secret=encrypt_secret(ig_app_secret),
        )
    )
    db.commit()
    return RedirectResponse(
        "/accounts/meta-apps?ok=created",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{app_id}/update")
def update_meta_app(
    app_id: int,
    name: str = Form(...),
    ig_app_id: str = Form(...),
    ig_app_secret: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    app = get_owned_meta_app(db, user.id, app_id)
    if not app:
        return RedirectResponse(
            "/accounts/meta-apps?error=not_found",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    name = name.strip()
    ig_app_id = ig_app_id.strip()
    secret = ig_app_secret.strip()
    if not name or not ig_app_id:
        return RedirectResponse(
            f"/accounts/meta-apps?edit={app_id}&error=name",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    conflict = db.scalar(
        select(UserMetaApp.id).where(
            UserMetaApp.user_id == user.id,
            UserMetaApp.ig_app_id == ig_app_id,
            UserMetaApp.id != app_id,
        )
    )
    if conflict:
        return RedirectResponse(
            f"/accounts/meta-apps?edit={app_id}&error=duplicate",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    app.name = name
    app.ig_app_id = ig_app_id
    if secret:
        if len(secret) < 8:
            return RedirectResponse(
                f"/accounts/meta-apps?edit={app_id}&error=ig_app_secret",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        app.encrypted_ig_app_secret = encrypt_secret(secret)
    db.commit()
    return RedirectResponse(
        "/accounts/meta-apps?ok=updated",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{app_id}/delete")
def delete_meta_app(
    app_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    app = get_owned_meta_app(db, user.id, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="App não encontrado")
    linked = db.scalar(
        select(func.count(InstagramAccount.id)).where(
            InstagramAccount.user_meta_app_id == app_id,
            InstagramAccount.status != "deleted",
        )
    ) or 0
    if linked:
        return RedirectResponse(
            "/accounts/meta-apps?error=linked",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    db.delete(app)
    db.commit()
    return RedirectResponse(
        "/accounts/meta-apps?ok=deleted",
        status_code=status.HTTP_303_SEE_OTHER,
    )
