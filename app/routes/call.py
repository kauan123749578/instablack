"""Sala Call global (LiveKit Cloud) — voz + compartilhar tela + chat."""
from __future__ import annotations

import logging
import re
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.deps import get_auth_user, get_db
from app.media_access import signed_media_path
from app.security import hash_password, verify_password
from app.templating import templates
from app.utils.avatars import user_avatar_url
from app.utils.voice_room import can_access_voice_room
from models.models import CallChatMessage, CallRoom, User

router = APIRouter(prefix="/call", tags=["call"])
log = logging.getLogger(__name__)

IDENTITY_RE = re.compile(r"^u(\d+)-")


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


def _livekit_api_http_url() -> str:
    url = (settings.livekit_url or "").strip()
    if url.startswith("wss://"):
        return "https://" + url[6:]
    if url.startswith("ws://"):
        return "http://" + url[5:]
    return url


def _display_name(user: User) -> str:
    name = (getattr(user, "display_name", None) or "").strip()
    return name or f"@{user.username}"


def _safe_device_id(raw: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", (raw or "").strip())[:24]
    return cleaned or secrets.token_hex(4)


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:48]
    return base or secrets.token_hex(4)


def _user_id_from_identity(identity: str) -> int | None:
    m = IDENTITY_RE.match((identity or "").strip())
    return int(m.group(1)) if m else None


def _avatar_for_user_id(db: Session, user_id: int) -> str | None:
    u = db.get(User, user_id)
    if not u:
        return None
    return user_avatar_url(u)


def _global_room_name() -> str:
    return (settings.livekit_room_name or "instablack-global").strip()


def _resolve_call_room(db: Session, slug: str | None) -> tuple[str, CallRoom | None]:
    """Retorna (livekit_room_name, CallRoom ou None para sala global)."""
    if not slug or slug in ("global", "instablack-global"):
        return _global_room_name(), None
    room = db.scalar(select(CallRoom).where(CallRoom.slug == slug))
    if not room:
        raise HTTPException(status_code=404, detail="Sala não encontrada.")
    return room.livekit_room, room


def _anonymize(name: str, blur: bool) -> str:
    if not blur:
        return name
    return "Participante"


async def _lk_participants(livekit_room: str, db: Session, blur: bool = False) -> list[dict]:
    from livekit import api as lk_api

    async with lk_api.LiveKitAPI(
        url=_livekit_api_http_url(),
        api_key=(settings.livekit_api_key or "").strip(),
        api_secret=(settings.livekit_api_secret or "").strip(),
    ) as lk:
        resp = await lk.room.list_participants(
            lk_api.ListParticipantsRequest(room=livekit_room)
        )
    out = []
    for p in resp.participants:
        ident = (p.identity or "").strip()
        if ident.endswith("-screen"):
            continue
        uid = _user_id_from_identity(ident)
        raw_name = (p.name or ident or "Alguém").strip()
        out.append({
            "identity": ident,
            "name": _anonymize(raw_name, blur),
            "avatar_url": _avatar_for_user_id(db, uid) if uid else None,
            "user_id": uid,
        })
    return out


@router.get("")
def call_page(
    request: Request,
    user: User = Depends(get_auth_user),
    db: Session = Depends(get_db),
):
    """Call usa o usuário autenticado (não o alvo do Ver como)."""
    _require_call_user(user)
    room_slug = (request.query_params.get("room") or "").strip()
    call_room = None
    if room_slug:
        call_room = db.scalar(select(CallRoom).where(CallRoom.slug == room_slug))
    my_rooms = db.scalars(
        select(CallRoom).where(CallRoom.owner_id == user.id).order_by(CallRoom.created_at.desc())
    ).all()
    return templates.TemplateResponse(
        "call.html",
        {
            "request": request,
            "user": user,
            "livekit_ready": _livekit_ready(),
            "livekit_url": (settings.livekit_url or "").strip(),
            "room_name": _global_room_name(),
            "room_slug": room_slug or "",
            "call_room": call_room,
            "my_rooms": my_rooms,
            "my_avatar_url": user_avatar_url(user) or "",
            "is_owner": bool(getattr(user, "is_owner", False)),
        },
    )


@router.get("/presence")
async def call_presence(
    request: Request,
    user: User = Depends(get_auth_user),
    db: Session = Depends(get_db),
):
    """Quem está na sala — ou todas as salas se ?all=1."""
    _require_call_user(user)
    if not _livekit_ready():
        return JSONResponse({"participants": [], "count": 0, "blur_names": False, "rooms": []})

    room_slug = (request.query_params.get("room") or "").strip()
    show_all = (request.query_params.get("all") or "") == "1" or not room_slug

    if show_all:
        rooms_out = []
        try:
            global_parts = await _lk_participants(_global_room_name(), db, False)
            rooms_out.append({
                "slug": "",
                "name": "Sala global",
                "count": len(global_parts),
                "participants": global_parts,
                "url": "/call",
            })
            for cr in db.scalars(select(CallRoom).order_by(CallRoom.name)).all():
                blur = bool(cr.blur_names)
                parts = await _lk_participants(cr.livekit_room, db, blur)
                rooms_out.append({
                    "slug": cr.slug,
                    "name": cr.name,
                    "count": len(parts),
                    "participants": parts,
                    "blur_names": blur,
                    "url": f"/call?room={cr.slug}",
                    "owner_id": cr.owner_id,
                })
        except Exception as exc:
            log.warning("call presence all: %s", exc)
            return JSONResponse({"participants": [], "count": 0, "rooms": [], "blur_names": False})

        active = room_slug
        current = next((r for r in rooms_out if r["slug"] == active), rooms_out[0] if rooms_out else None)
        return JSONResponse({
            "participants": current["participants"] if current else [],
            "count": current["count"] if current else 0,
            "blur_names": bool(current and current.get("blur_names")),
            "rooms": rooms_out,
            "active_slug": active,
        })

    try:
        livekit_room, call_room = _resolve_call_room(db, room_slug or None)
    except HTTPException:
        return JSONResponse({"participants": [], "count": 0, "blur_names": False, "rooms": []})

    blur = bool(call_room and call_room.blur_names)
    try:
        participants = await _lk_participants(livekit_room, db, blur)
        return JSONResponse({
            "participants": participants,
            "count": len(participants),
            "blur_names": blur,
            "rooms": [],
        })
    except Exception as exc:
        log.warning("call presence: %s", exc)
        return JSONResponse({"participants": [], "count": 0, "blur_names": blur, "rooms": []})


@router.get("/roster")
def call_roster(
    request: Request,
    user: User = Depends(get_auth_user),
    db: Session = Depends(get_db),
):
    """Mapa identity → avatar/nome (para tiles enquanto conectado)."""
    _require_call_user(user)
    ids_param = (request.query_params.get("ids") or "").strip()
    identities = [x.strip() for x in ids_param.split(",") if x.strip()]
    blur = (request.query_params.get("blur") or "") == "1"
    my_prefix = f"u{user.id}-"
    out = {}
    for ident in identities:
        uid = _user_id_from_identity(ident)
        if not uid:
            continue
        u = db.get(User, uid)
        if not u:
            continue
        name = _display_name(u)
        if blur and not ident.startswith(my_prefix):
            name = _anonymize(name, True)
        out[ident] = {
            "name": name,
            "avatar_url": user_avatar_url(u),
            "user_id": uid,
        }
    return JSONResponse({"roster": out, "blur_names": blur})


@router.get("/rooms")
def list_my_rooms(user: User = Depends(get_auth_user), db: Session = Depends(get_db)):
    _require_call_user(user)
    rooms = db.scalars(
        select(CallRoom).order_by(CallRoom.name)
    ).all()
    return JSONResponse({
        "rooms": [
            {
                "slug": r.slug,
                "name": r.name,
                "has_password": bool(r.password_hash),
                "blur_names": r.blur_names,
                "url": f"/call?room={r.slug}",
                "owner_id": r.owner_id,
                "is_mine": r.owner_id == user.id,
            }
            for r in rooms
        ]
    })


@router.delete("/rooms/{slug}")
def delete_call_room(
    slug: str,
    user: User = Depends(get_auth_user),
    db: Session = Depends(get_db),
):
    _require_call_user(user)
    room = db.scalar(select(CallRoom).where(CallRoom.slug == slug))
    if not room:
        raise HTTPException(status_code=404, detail="Sala não encontrada.")
    if room.owner_id != user.id and not getattr(user, "is_owner", False):
        raise HTTPException(status_code=403, detail="Só o dono pode excluir esta sala.")
    db.delete(room)
    db.commit()
    return JSONResponse({"ok": True})


@router.get("/chat")
def get_chat_messages(
    request: Request,
    user: User = Depends(get_auth_user),
    db: Session = Depends(get_db),
):
    _require_call_user(user)
    room_slug = (request.query_params.get("room") or "").strip()
    since_id = int(request.query_params.get("since_id") or 0)
    try:
        q = (
            select(CallChatMessage)
            .where(CallChatMessage.room_slug == room_slug)
            .where(CallChatMessage.id > since_id)
            .order_by(CallChatMessage.id.asc())
            .limit(100)
        )
        rows = db.scalars(q).all()
    except Exception as exc:
        log.exception("call chat get failed")
        raise HTTPException(
            status_code=503,
            detail="Chat indisponível — aguarde o servidor migrar e recarregue.",
        ) from exc
    return JSONResponse({
        "messages": [
            {
                "id": m.id,
                "author": m.author_name,
                "text": m.text,
                "user_id": m.user_id,
                "at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in rows
        ],
    })


@router.post("/chat")
async def post_chat_message(
    request: Request,
    user: User = Depends(get_auth_user),
    db: Session = Depends(get_db),
):
    _require_call_user(user)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    room_slug = str(body.get("room_slug") or "").strip()
    text = str(body.get("text") or "").strip()[:500]
    if not text:
        raise HTTPException(status_code=400, detail="Mensagem vazia.")
    if room_slug:
        if not db.scalar(select(CallRoom.id).where(CallRoom.slug == room_slug)):
            raise HTTPException(status_code=404, detail="Sala não encontrada.")
    try:
        msg = CallChatMessage(
            room_slug=room_slug,
            user_id=user.id,
            author_name=_display_name(user),
            text=text,
        )
        db.add(msg)
        db.commit()
        db.refresh(msg)
    except Exception as exc:
        log.exception("call chat post failed")
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Não foi possível salvar a mensagem — recarregue a página.",
        ) from exc
    return JSONResponse({
        "id": msg.id,
        "author": msg.author_name,
        "text": msg.text,
        "user_id": msg.user_id,
    })


@router.post("/rooms")
async def create_call_room(
    request: Request,
    user: User = Depends(get_auth_user),
    db: Session = Depends(get_db),
):
    _require_call_user(user)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    name = str(body.get("name") or "").strip()[:128]
    if not name:
        raise HTTPException(status_code=400, detail="Nome da sala obrigatório.")
    password = str(body.get("password") or "").strip()
    blur_names = bool(body.get("blur_names"))
    slug = _slugify(name)
    base_slug = slug
    n = 0
    while db.scalar(select(CallRoom.id).where(CallRoom.slug == slug)):
        n += 1
        slug = f"{base_slug}-{n}"[:64]
    livekit_room = f"ib-{slug}"[:128]
    while db.scalar(select(CallRoom.id).where(CallRoom.livekit_room == livekit_room)):
        n += 1
        livekit_room = f"ib-{slug}-{n}"[:128]
    room = CallRoom(
        slug=slug,
        name=name,
        password_hash=hash_password(password) if password else None,
        owner_id=user.id,
        blur_names=blur_names,
        livekit_room=livekit_room,
    )
    try:
        db.add(room)
        db.commit()
        db.refresh(room)
    except Exception as exc:
        log.exception("call room create failed")
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Não foi possível criar a sala — recarregue e tente de novo.",
        ) from exc
    return JSONResponse({
        "slug": room.slug,
        "name": room.name,
        "blur_names": room.blur_names,
        "url": f"/call?room={room.slug}",
    })


@router.post("/token")
async def call_token(
    request: Request,
    user: User = Depends(get_auth_user),
    db: Session = Depends(get_db),
):
    """JWT LiveKit para entrar na sala global ou privada."""
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
    room_slug = ""
    password = ""
    try:
        body = await request.json()
        if isinstance(body, dict):
            device_raw = str(body.get("device_id") or "")
            role = str(body.get("role") or "").strip().lower()
            room_slug = str(body.get("room_slug") or "").strip()
            password = str(body.get("password") or "")
    except Exception:
        device_raw = ""

    livekit_room, call_room = _resolve_call_room(db, room_slug or None)
    if call_room and call_room.password_hash:
        if not password or not verify_password(password, call_room.password_hash):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Senha da sala incorreta.")

    try:
        from livekit.api import AccessToken, VideoGrants
    except ImportError as exc:
        log.exception("livekit-api não instalado")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Pacote livekit-api ausente no deploy.",
        ) from exc

    device = _safe_device_id(device_raw)
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
                    room=livekit_room,
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
            "room": livekit_room,
            "room_slug": call_room.slug if call_room else "",
            "blur_names": bool(call_room and call_room.blur_names),
            "identity": identity,
            "name": name,
            "avatar_url": user_avatar_url(user),
        }
    )


@router.get("/screen-host")
def call_screen_host_page(
    request: Request,
    user: User = Depends(get_auth_user),
):
    """Janela auxiliar — mantém screen share ao atualizar a aba principal."""
    _require_call_user(user)
    room_slug = (request.query_params.get("room") or "").strip()
    return templates.TemplateResponse(
        "call_screen_host.html",
        {
            "request": request,
            "user": user,
            "livekit_ready": _livekit_ready(),
            "room_slug": room_slug,
        },
    )
