"""Painel administrativo — gerenciar usuários do SaaS."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.deps import get_admin_user
from app.templating import templates
from app.utils.account_limits import account_limit_label
from app.utils.invite_codes import generate_invite_code
from core.database import get_db
from models.models import Automation, InstagramAccount, InviteCode, User

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("")
def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    users = db.scalars(
        select(User).order_by(User.created_at.desc())
    ).all()

    rows = []
    for u in users:
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

    invites = db.scalars(
        select(InviteCode)
        .options(selectinload(InviteCode.used_by), selectinload(InviteCode.created_by))
        .order_by(InviteCode.created_at.desc())
        .limit(100)
    ).all()

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "user": admin,
            "rows": rows,
            "invites": invites,
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("error"),
            "new_codes": request.query_params.getlist("code"),
        },
    )


@router.post("/invites/create")
def create_invite_codes(
    quantity: int = Form(1),
    max_uses: int = Form(1),
    note: str = Form(""),
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    qty = max(1, min(quantity, 20))
    uses = max(1, min(max_uses, 100))
    note_text = note.strip() or None
    created_codes: list[str] = []

    for _ in range(qty):
        for _attempt in range(10):
            code = generate_invite_code()
            if db.scalar(select(InviteCode).where(InviteCode.code == code)) is not None:
                continue
            db.add(
                InviteCode(
                    code=code,
                    created_by_id=admin.id,
                    max_uses=uses,
                    note=note_text,
                )
            )
            created_codes.append(code)
            break

    if not created_codes:
        return RedirectResponse(
            "/admin?error=invite_failed",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    db.commit()
    params = "&".join(f"code={c}" for c in created_codes)
    return RedirectResponse(
        f"/admin?ok=invite&{params}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/invites/{invite_id}/delete")
def delete_invite_code(
    invite_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    invite = db.get(InviteCode, invite_id)
    if invite:
        db.delete(invite)
        db.commit()
    return RedirectResponse(
        "/admin?ok=invite_deleted",
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
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

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


@router.post("/users/{user_id}/delete")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    if user_id == admin.id:
        return RedirectResponse(
            "/admin?error=self",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    db.delete(target)
    db.commit()
    return RedirectResponse(
        "/admin?ok=deleted",
        status_code=status.HTTP_303_SEE_OTHER,
    )
