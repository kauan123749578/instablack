"""Acesso a /media: URLs assinadas (HMAC) + checagem de proprietário na sessão."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from urllib.parse import quote, urlencode

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from models.models import Automation, InstagramAccount, User

log = logging.getLogger(__name__)

# Meta pode demorar minutos no container; UI só precisa enquanto a aba está aberta.
DEFAULT_META_TTL = 6 * 3600
DEFAULT_UI_TTL = 2 * 3600


def _signing_key() -> bytes:
    return (settings.secret_key or "").encode("utf-8")


def media_signature(file_key: str, exp: int) -> str:
    msg = f"{file_key}\n{exp}".encode("utf-8")
    return hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()


def verify_media_signature(file_key: str, exp: int | None, sig: str | None) -> bool:
    if exp is None or not sig:
        return False
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_i < int(time.time()):
        return False
    expected = media_signature(file_key, exp_i)
    return hmac.compare_digest(expected, str(sig).strip().lower())


def signed_media_path(file_key: str, *, ttl: int = DEFAULT_UI_TTL) -> str:
    """Path relativo /media/...?exp=&sig= para o painel e workers."""
    key = (file_key or "").lstrip("/")
    if not key or ".." in key:
        raise ValueError("chave de mídia inválida")
    ttl = max(60, int(ttl))
    exp = int(time.time()) + ttl
    sig = media_signature(key, exp)
    qs = urlencode({"exp": str(exp), "sig": sig})
    return f"/media/{quote(key, safe='/')}?{qs}"


def absolute_signed_media_url(file_key: str, *, ttl: int = DEFAULT_META_TTL) -> str:
    """URL HTTPS absoluta assinada (Meta Graph / probes externos)."""
    import os

    base = (settings.public_base_url or "").strip()
    if not base:
        railway_url = os.getenv("RAILWAY_STATIC_URL", "").strip()
        railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
        base = railway_url or (f"https://{railway_domain}" if railway_domain else "")
    if not base:
        base = "https://instablack-production.up.railway.app"
    path = signed_media_path(file_key, ttl=ttl)
    return f"{base.rstrip('/')}{path}"


def extract_media_key(url_or_key: str | None) -> str | None:
    """Extrai a storage key de '/media/...' ou devolve a key crua."""
    if not url_or_key:
        return None
    s = str(url_or_key).strip()
    if not s:
        return None
    if s.startswith("http://") or s.startswith("https://"):
        # URL externa (CDN Meta) — não assinar
        return None
    if s.startswith("/media/"):
        s = s[len("/media/") :]
    return s.split("?", 1)[0].lstrip("/") or None


def signed_media_url(url_or_key: str | None, *, ttl: int = DEFAULT_UI_TTL) -> str | None:
    """Assina path local; passa adiante URLs http(s) externas."""
    if not url_or_key:
        return None
    s = str(url_or_key).strip()
    if not s:
        return None
    if s.startswith("http://") or s.startswith("https://"):
        return s
    key = extract_media_key(s)
    if not key:
        return None
    try:
        return signed_media_path(key, ttl=ttl)
    except ValueError:
        return None


def user_owns_media(db: Session, user: User, file_key: str) -> bool:
    """True se a chave pertence ao usuário (avatar, conta IG ou automação)."""
    key = (file_key or "").lstrip("/")
    if not key or user is None:
        return False

    if getattr(user, "avatar_key", None) == key:
        return True

    # Foto de perfil cacheada: /media/avatars/ig/{account_id}.ext ou URL armazenada
    pic_match = f"/media/{key}"
    owned_pic = db.scalar(
        select(InstagramAccount.id).where(
            InstagramAccount.user_id == user.id,
            or_(
                InstagramAccount.profile_pic_url == pic_match,
                InstagramAccount.profile_pic_url == key,
                InstagramAccount.profile_pic_url.like(f"/media/{key}?%"),
            ),
        ).limit(1)
    )
    if owned_pic is not None:
        return True

    # Atalho estável: avatars/ig/{account_id}.*
    if key.startswith("avatars/ig/"):
        stem = key.rsplit("/", 1)[-1]
        acc_id_str = stem.split(".", 1)[0]
        if acc_id_str.isdigit():
            acc = db.get(InstagramAccount, int(acc_id_str))
            if acc is not None and acc.user_id == user.id:
                return True

    hit = db.scalar(
        select(Automation.id).where(
            Automation.user_id == user.id,
            or_(Automation.video_key == key, Automation.thumb_key == key),
        ).limit(1)
    )
    if hit is not None:
        return True

    for row in db.scalars(
        select(Automation.videos_json).where(
            Automation.user_id == user.id,
            Automation.videos_json.is_not(None),
            Automation.videos_json.contains(key),
        ).limit(40)
    ).all():
        try:
            items = json.loads(row or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, dict) and it.get("video_key") == key:
                return True
            if isinstance(it, str) and it == key:
                return True

    return False
