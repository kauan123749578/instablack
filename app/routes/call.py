"""Sala Call global (LiveKit Cloud) — voz + compartilhar tela + chat."""
from __future__ import annotations

import logging
import re
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.config import settings
from app.deps import get_auth_user
from app.templating import templates
from app.utils.voice_room import can_access_voice_room
from models.models import User

router = APIRouter(prefix="/call", tags=["call"])
log = logging.getLogger(__name__)


def _require_call_user(user: User) -> User:
    if not can_access_voice_room(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Call não liberada para sua conta. Peça ao dono no Admin.",
        )
    return user


def _livekit_ready() -> bool:
    return bool(
        (settings.livekit_url or "").strip()
        and (settings.livekit_api_key or "").strip()
        and (settings.livekit_api_secret or "").strip()
    )


def _display_name(user: User) -> str:
    name = (getattr(user, "display_name", None) or "").strip()
    return name or f"@{user.username}"


def _safe_device_id(raw: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", (raw or "").strip())[:24]
    return cleaned or secrets.token_hex(4)


@router.get("")
def call_page(
    request: Request,
    user: User = Depends(get_auth_user),
):
    """Call usa o usuário autenticado (não o alvo do Ver como)."""
    _require_call_user(user)
    return templates.TemplateResponse(
        "call.html",
        {
            "request": request,
            "user": user,
            "livekit_ready": _livekit_ready(),
            "livekit_url": (settings.livekit_url or "").strip(),
            "room_name": (settings.livekit_room_name or "instablack-global").strip(),
            "is_owner": bool(getattr(user, "is_owner", False)),
        },
    )


@router.post("/token")
async def call_token(
    request: Request,
    user: User = Depends(get_auth_user),
):
    """JWT LiveKit para entrar na sala global."""
    _require_call_user(user)
    if not _livekit_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "LiveKit não configurado. No Railway, defina LIVEKIT_URL, "
                "LIVEKIT_API_KEY e LIVEKIT_API_SECRET (cloud.livekit.io)."
            ),
        )

    device_raw = ""
    role = ""
    try:
        body = await request.json()
        if isinstance(body, dict):
            device_raw = str(body.get("device_id") or "")
            role = str(body.get("role") or "").strip().lower()
    except Exception:
        device_raw = ""

    try:
        from livekit.api import AccessToken, VideoGrants
    except ImportError as exc:
        log.exception("livekit-api não instalado")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Pacote livekit-api ausente no deploy.",
        ) from exc

    room = (settings.livekit_room_name or "instablack-global").strip()
    device = _safe_device_id(device_raw)
    # Identidade única por aparelho: PC e celular ficam na sala juntos.
    # Janela auxiliar de tela usa sufixo -screen (sobrevive F5 na aba principal).
    identity = f"u{user.id}-{device}"
    if role == "screen":
        identity = f"{identity}-screen"
    name = _display_name(user)
    try:
        token = (
            AccessToken(
                (settings.livekit_api_key or "").strip(),
                (settings.livekit_api_secret or "").strip(),
            )
            .with_identity(identity)
            .with_name(name)
            .with_ttl(timedelta(hours=6))
            .with_grants(
                VideoGrants(
                    room_join=True,
                    room=room,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
            .to_jwt()
        )
    except Exception as exc:
        log.exception("Falha ao gerar token LiveKit")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Falha ao gerar token: {exc}",
        ) from exc

    return JSONResponse(
        {
            "token": token,
            "url": (settings.livekit_url or "").strip(),
            "room": room,
            "identity": identity,
            "name": name,
        }
    )


@router.get("/screen-host")
def call_screen_host_page(
    request: Request,
    user: User = Depends(get_auth_user),
):
    """Janela auxiliar — mantém screen share ao atualizar a aba principal."""
    _require_call_user(user)
    return templates.TemplateResponse(
        "call_screen_host.html",
        {
            "request": request,
            "user": user,
            "livekit_ready": _livekit_ready(),
        },
    )
