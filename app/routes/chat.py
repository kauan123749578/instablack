"""Chat / voz via Backspace (https://github.com/TheZwiss/backspace)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.deps import get_auth_user, get_db
from app.templating import templates
from app.utils.voice_room import can_access_voice_room
from core.backspace_client import backspace_base_url, backspace_enabled, ensure_backspace_account
from models.models import User

router = APIRouter(prefix="/chat", tags=["chat"])
log = logging.getLogger(__name__)


def _require_chat_user(user: User) -> User:
    if not can_access_voice_room(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chat não liberado para sua conta. Peça ao dono no Admin.",
        )
    return user


@router.get("")
async def chat_page(
    request: Request,
    user: User = Depends(get_auth_user),
    db: Session = Depends(get_db),
):
    """Shell Instablack + Backspace (iframe ou redirect)."""
    _require_chat_user(user)
    if not backspace_enabled():
        return templates.TemplateResponse(
            "chat_setup.html",
            {
                "request": request,
                "user": user,
                "backspace_enabled": False,
                "backspace_url": "",
            },
        )

    bs_url = backspace_base_url()
    session = None
    session_error = ""
    try:
        session = await ensure_backspace_account(user, db)
    except Exception as exc:
        log.exception("backspace ensure failed user=%s", user.id)
        session_error = str(exc)

    return templates.TemplateResponse(
        "chat.html",
        {
            "request": request,
            "user": user,
            "backspace_url": bs_url,
            "backspace_token": (session or {}).get("token") or "",
            "backspace_username": (session or {}).get("bs_username") or "",
            "backspace_created": bool((session or {}).get("created")),
            "session_error": session_error,
        },
    )


@router.get("/session")
async def chat_session(
    user: User = Depends(get_auth_user),
    db: Session = Depends(get_db),
):
    """Token Backspace para SSO (mesma origem no iframe helper)."""
    _require_chat_user(user)
    if not backspace_enabled():
        raise HTTPException(status_code=503, detail="Backspace não configurado.")
    try:
        data = await ensure_backspace_account(user, db)
    except Exception as exc:
        log.exception("backspace session")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse({
        "token": data.get("token"),
        "username": data.get("bs_username"),
        "backspace_url": backspace_base_url(),
        "created": bool(data.get("created")),
    })


@router.get("/open")
async def chat_open_external(
    user: User = Depends(get_auth_user),
    db: Session = Depends(get_db),
):
    """Abre Backspace em tela cheia (subdomínio recomendado)."""
    _require_chat_user(user)
    if not backspace_enabled():
        return RedirectResponse("/chat", status_code=302)
    try:
        await ensure_backspace_account(user, db)
    except Exception as exc:
        log.warning("backspace open ensure: %s", exc)
    return RedirectResponse(backspace_base_url(), status_code=302)
