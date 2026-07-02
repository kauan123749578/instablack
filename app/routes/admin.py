"""Painel administrativo — gerenciar usuários do SaaS."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import get_admin_user
from app.templating import templates
from core.database import get_db
from models.models import Automation, InstagramAccount, User

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
        })

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "user": admin,
            "rows": rows,
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("error"),
        },
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
