"""Painel administrativo — lista de usuários com privacidade do owner."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import get_admin_user, get_owner_user
from app.utils.account_limits import account_limit_label
from app.templating import templates
from app.utils.platform_settings import (
    META_SETUP_YOUTUBE_URL,
    META_TOKEN_YOUTUBE_URL,
    get_platform_setting,
)
from core.database import get_db
from models.models import Automation, InstagramAccount, User

router = APIRouter(prefix="/admin", tags=["admin"])


def _is_owner(user: User) -> bool:
    return bool(getattr(user, "is_owner", False))


def _is_owner_private(user: User) -> bool:
    return bool(getattr(user, "owner_private", False))


def _admin_can_see(viewer: User, target: User) -> bool:
    """Owner vê todos; outros admins não veem usuários marcados como privados do owner."""
    if _is_owner(viewer):
        return True
    if _is_owner(target):
        return False
    return not _is_owner_private(target)


def _require_visible(viewer: User, target: User | None) -> User:
    if not target:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if not _admin_can_see(viewer, target):
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return target


@router.get("")
def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    is_owner = _is_owner(admin)
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    rows = []
    for u in users:
        if not _admin_can_see(admin, u):
            continue
        ig_count = db.scalar(
            select(func.count(InstagramAccount.id)).where(InstagramAccount.user_id == u.id)
        ) or 0
        auto_count = db.scalar(
            select(func.count(Automation.id)).where(Automation.user_id == u.id)
        ) or 0
        rows.append({
            "user": u,
            "ig_count": ig_count,
            "auto_count": auto_count,
            "limit_label": account_limit_label(u.account_limit),
        })

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "user": admin,
            "is_owner": is_owner,
            "rows": rows,
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("error"),
            "meta_setup_youtube_url": get_platform_setting(db, META_SETUP_YOUTUBE_URL)
            if is_owner
            else "",
            "meta_token_youtube_url": get_platform_setting(db, META_TOKEN_YOUTUBE_URL)
            if is_owner
            else "",
        },
    )


@router.post("/platform/meta-youtube")
def save_meta_youtube_urls(
    meta_setup_youtube_url: str = Form(""),
    meta_token_youtube_url: str = Form(""),
    db: Session = Depends(get_db),
    admin: User = Depends(get_owner_user),
):
    from app.utils.platform_settings import set_platform_setting

    set_platform_setting(db, META_SETUP_YOUTUBE_URL, meta_setup_youtube_url)
    set_platform_setting(db, META_TOKEN_YOUTUBE_URL, meta_token_youtube_url)
    return RedirectResponse(
        "/admin?ok=meta_youtube",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/broadcast")
def broadcast_notification(
    title: str = Form(""),
    message: str = Form(...),
    db: Session = Depends(get_db),
    admin: User = Depends(get_owner_user),
):
    """Envia uma notificação (sino + push) para todos os usuários ativos."""
    from core.notifications import create_notification

    body = message.strip()
    if not body:
        return RedirectResponse(
            "/admin?error=broadcast_empty",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    heading = title.strip() or "Aviso da plataforma"

    user_ids = db.scalars(
        select(User.id).where(User.is_active.is_(True))
    ).all()
    sent = 0
    for uid in user_ids:
        if create_notification(
            uid,
            heading[:255],
            body[:1000],
            kind="announce",
            force=True,
            send_push=True,
        ):
            sent += 1

    return RedirectResponse(
        f"/admin?ok=broadcast&n={sent}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/users/{user_id}/limit")
def set_account_limit(
    user_id: int,
    account_limit: str = Form("0"),
    unlimited: str = Form(""),
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    target = _require_visible(admin, db.get(User, user_id))

    if unlimited == "1":
        target.account_limit = None
    else:
        try:
            n = int(account_limit.strip())
        except ValueError:
            return RedirectResponse(
                "/admin?error=limit_invalid",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        if n < 0:
            n = 0
        target.account_limit = n

    db.commit()
    return RedirectResponse(
        "/admin?ok=limit",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/users/{user_id}/toggle-private")
def toggle_user_private(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_owner_user),
):
    """Marca/desmarca usuário como 'meu' — outros admins não veem."""
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if target.id == admin.id or _is_owner(target):
        return RedirectResponse(
            "/admin?error=private_self",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    target.owner_private = not _is_owner_private(target)
    db.commit()
    return RedirectResponse(
        "/admin?ok=private",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/users/{user_id}/toggle-admin")
def toggle_user_admin(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_owner_user),
):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    if user_id == admin.id and target.is_admin:
        return RedirectResponse(
            "/admin?error=self_admin",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if _is_owner(target):
        return RedirectResponse(
            "/admin?error=owner_admin",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if target.is_admin:
        other_admins = db.scalar(
            select(func.count(User.id)).where(
                User.is_admin.is_(True),
                User.id != target.id,
            )
        ) or 0
        if other_admins < 1:
            return RedirectResponse(
                "/admin?error=last_admin",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        target.is_admin = False
    else:
        target.is_admin = True

    db.commit()
    return RedirectResponse(
        "/admin?ok=admin",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/users/{user_id}/delete")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_owner_user),
):
    if user_id == admin.id:
        return RedirectResponse(
            "/admin?error=self",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if _is_owner(target):
        return RedirectResponse(
            "/admin?error=owner_delete",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    db.delete(target)
    db.commit()
    return RedirectResponse(
        "/admin?ok=deleted",
        status_code=status.HTTP_303_SEE_OTHER,
    )
