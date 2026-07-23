"""Painel administrativo — lista de usuários com privacidade do owner."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.deps import get_admin_user, get_owner_user
from app.utils.account_limits import account_limit_label
from app.templating import templates
from app.utils.invite_codes import (
    create_invite,
    deactivate_invite,
    delete_invites,
    invite_is_exhausted,
    invite_public_url,
    list_invites,
)
from app.utils.platform_settings import (
    META_SETUP_YOUTUBE_URL,
    META_TOKEN_YOUTUBE_URL,
    get_platform_setting,
)
from core.database import get_db
from models.models import Automation, InstagramAccount, InviteCode, User, UserMetaApp, automation_accounts

router = APIRouter(prefix="/admin", tags=["admin"])


def _is_owner(user: User) -> bool:
    return bool(getattr(user, "is_owner", False))


def _is_owner_private(user: User) -> bool:
    return bool(getattr(user, "owner_private", False))


def _admin_can_see(viewer: User, target: User) -> bool:
    """Owner vê todos; outros admins não veem Owner nem usuários Meu (owner_private)."""
    if _is_owner(viewer):
        return True
    if _is_owner(target):
        return False
    return not _is_owner_private(target)


def _admin_can_moderate(viewer: User, target: User) -> bool:
    """Ban/excluir: visível para o admin, mas nunca owner, self ou usuários Meu (para não-owner)."""
    if target.id == viewer.id:
        return False
    if _is_owner(target):
        return False
    if not _admin_can_see(viewer, target):
        return False
    if _is_owner_private(target) and not _is_owner(viewer):
        return False
    return True


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
            "invites": list_invites(db),
            "invite_base": str(request.base_url).rstrip("/"),
            "new_invite_url": request.query_params.get("link") or "",
            "meta_setup_youtube_url": get_platform_setting(db, META_SETUP_YOUTUBE_URL)
            if is_owner
            else "",
            "meta_token_youtube_url": get_platform_setting(db, META_TOKEN_YOUTUBE_URL)
            if is_owner
            else "",
        },
    )


@router.post("/invites/create")
def admin_create_invite(
    request: Request,
    max_uses: int = Form(1),
    note: str = Form(""),
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    invite = create_invite(db, created_by=admin, max_uses=max_uses, note=note)
    link = invite_public_url(str(request.base_url), invite.code)
    from urllib.parse import quote

    return RedirectResponse(
        f"/admin?ok=invite&link={quote(link, safe='')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/invites/{invite_id}/deactivate")
def admin_deactivate_invite(
    invite_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    deactivate_invite(db, invite_id)
    return RedirectResponse(
        "/admin?ok=invite_off",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/invites/delete")
def admin_delete_invites(
    invite_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    ids = [int(x) for x in (invite_ids or [])]
    # Só apaga esgotados/inativos
    rows = list(db.scalars(select(InviteCode).where(InviteCode.id.in_(ids))).all()) if ids else []
    to_delete = [r.id for r in rows if invite_is_exhausted(r)]
    n = delete_invites(db, to_delete)
    return RedirectResponse(
        f"/admin?ok=invite_deleted&n={n}",
        status_code=status.HTTP_303_SEE_OTHER,
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
    """Marca/desmarca usuário como 'Meu' — outros admins não veem na lista."""
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


@router.get("/users/{user_id}/accounts")
def admin_user_accounts(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    target = _require_visible(admin, db.get(User, user_id))
    # Só API oficial ativa — esta tela é só para repasse Meta
    accounts = db.scalars(
        select(InstagramAccount)
        .where(
            InstagramAccount.user_id == target.id,
            InstagramAccount.provider == "meta",
            InstagramAccount.status != "deleted",
        )
        .order_by(InstagramAccount.username.asc())
    ).all()
    return templates.TemplateResponse(
        "admin_user_accounts.html",
        {
            "request": request,
            "user": admin,
            "target": target,
            "accounts": accounts,
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("error"),
            "moved": request.query_params.get("n"),
            "skipped": request.query_params.get("skip"),
        },
    )


def _ensure_dest_meta_app(
    db: Session,
    *,
    dest_user_id: int,
    source_app: UserMetaApp | None,
) -> UserMetaApp | None:
    """Garante que o destino tem o mesmo app Meta (reusa ou clona)."""
    if source_app is None:
        return None
    existing = db.scalar(
        select(UserMetaApp).where(
            UserMetaApp.user_id == dest_user_id,
            UserMetaApp.ig_app_id == source_app.ig_app_id,
        )
    )
    if existing:
        return existing
    clone = UserMetaApp(
        user_id=dest_user_id,
        name=source_app.name or f"App {source_app.ig_app_id}",
        ig_app_id=source_app.ig_app_id,
        encrypted_ig_app_secret=source_app.encrypted_ig_app_secret,
    )
    db.add(clone)
    db.flush()
    return clone


@router.post("/users/{user_id}/transfer-meta")
def transfer_meta_accounts(
    user_id: int,
    account_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Repassa contas Meta selecionadas do usuário origem para o admin logado."""
    target = _require_visible(admin, db.get(User, user_id))
    if target.id == admin.id:
        return RedirectResponse(
            f"/admin/users/{user_id}/accounts?error=self",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    ids = [int(x) for x in (account_ids or []) if str(x).isdigit() or isinstance(x, int)]
    if not ids:
        return RedirectResponse(
            f"/admin/users/{user_id}/accounts?error=none",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    accounts = list(
        db.scalars(
            select(InstagramAccount).where(
                InstagramAccount.user_id == target.id,
                InstagramAccount.id.in_(ids),
                InstagramAccount.provider == "meta",
            )
        ).all()
    )
    if not accounts:
        return RedirectResponse(
            f"/admin/users/{user_id}/accounts?error=none",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    moved = 0
    skipped = 0
    for acc in accounts:
        conflict = db.scalar(
            select(InstagramAccount.id).where(
                InstagramAccount.user_id == admin.id,
                InstagramAccount.username == acc.username,
            )
        )
        if conflict:
            skipped += 1
            continue

        source_app = None
        if acc.user_meta_app_id:
            source_app = db.get(UserMetaApp, acc.user_meta_app_id)
        dest_app = _ensure_dest_meta_app(db, dest_user_id=admin.id, source_app=source_app)

        db.execute(
            delete(automation_accounts).where(
                automation_accounts.c.account_id == acc.id
            )
        )
        acc.user_id = admin.id
        acc.user_meta_app_id = dest_app.id if dest_app else None
        moved += 1

    db.commit()
    return RedirectResponse(
        f"/admin/users/{user_id}/accounts?ok=transferred&n={moved}&skip={skipped}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/users/{user_id}/toggle-ban")
def toggle_user_ban(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    target = db.get(User, user_id)
    if not target or not _admin_can_moderate(admin, target):
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    target.is_active = not target.is_active
    db.commit()
    return RedirectResponse(
        "/admin?ok=ban" if not target.is_active else "/admin?ok=unban",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/users/{user_id}/delete")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    target = db.get(User, user_id)
    if not target or not _admin_can_moderate(admin, target):
        return RedirectResponse(
            "/admin?error=protected",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    db.delete(target)
    db.commit()
    return RedirectResponse(
        "/admin?ok=deleted",
        status_code=status.HTTP_303_SEE_OTHER,
    )
