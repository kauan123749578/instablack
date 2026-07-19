"""Cliente mínimo da Instagram API oficial (Business Login for Instagram)."""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path
from urllib.parse import quote, urlencode

import requests

from app.config import settings

OAUTH_AUTHORIZE_URL = "https://api.instagram.com/oauth/authorize"
OAUTH_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
GRAPH_BASE_URL = "https://graph.instagram.com"
META_SCOPES = (
    "instagram_business_basic",
    "instagram_business_content_publish",
)
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}


class MetaInstagramError(RuntimeError):
    pass


def _graph_url(path: str) -> str:
    version = settings.meta_instagram_graph_version.strip().lstrip("/")
    prefix = f"/{version}" if version else ""
    return f"{GRAPH_BASE_URL}{prefix}/{path.lstrip('/')}"


def is_configured() -> bool:
    return bool(
        settings.meta_instagram_app_id
        and settings.meta_instagram_app_secret
        and settings.meta_instagram_redirect_uri
    )


def authorization_url(state: str) -> str:
    if not is_configured():
        raise MetaInstagramError("Instagram API oficial ainda não foi configurada.")
    params = {
        "client_id": settings.meta_instagram_app_id,
        "redirect_uri": settings.meta_instagram_redirect_uri,
        "response_type": "code",
        "scope": ",".join(META_SCOPES),
        "state": state,
    }
    return f"{OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


def _json_or_error(response: requests.Response, action: str) -> dict:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not response.ok or payload.get("error"):
        error = payload.get("error") or {}
        detail = (
            error.get("message")
            if isinstance(error, dict)
            else str(error)
        ) or response.text[:500]
        raise MetaInstagramError(f"{action}: {detail}")
    return payload


def exchange_code(code: str) -> tuple[str, dt.datetime | None]:
    """Troca code por token longo; retorna (token, expiração)."""
    short_response = requests.post(
        OAUTH_TOKEN_URL,
        data={
            "client_id": settings.meta_instagram_app_id,
            "client_secret": settings.meta_instagram_app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": settings.meta_instagram_redirect_uri,
            "code": code,
        },
        timeout=30,
    )
    short = _json_or_error(short_response, "Falha ao trocar código OAuth")
    short_token = str(short.get("access_token") or "")
    if not short_token:
        raise MetaInstagramError("A Meta não retornou access_token.")

    long_response = requests.get(
        f"{GRAPH_BASE_URL}/access_token",
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": settings.meta_instagram_app_secret,
            "access_token": short_token,
        },
        timeout=30,
    )
    long_data = _json_or_error(long_response, "Falha ao gerar token longo")
    token = str(long_data.get("access_token") or short_token)
    expires_in = int(long_data.get("expires_in") or 0)
    expires_at = (
        dt.datetime.utcnow() + dt.timedelta(seconds=expires_in)
        if expires_in > 0
        else None
    )
    return token, expires_at


def account_profile(access_token: str) -> dict[str, str]:
    response = requests.get(
        _graph_url("me"),
        params={
            "fields": "user_id,username",
            "access_token": access_token,
        },
        timeout=30,
    )
    data = _json_or_error(response, "Falha ao consultar conta Instagram")
    user_id = str(data.get("user_id") or data.get("id") or "")
    username = str(data.get("username") or "")
    if not user_id or not username:
        raise MetaInstagramError("A Meta não retornou user_id/username da conta.")
    return {"id": user_id, "username": username}


def validate_token(access_token: str) -> dict[str, str]:
    return account_profile(access_token)


def refresh_access_token(access_token: str) -> tuple[str, dt.datetime | None]:
    response = requests.get(
        f"{GRAPH_BASE_URL}/refresh_access_token",
        params={
            "grant_type": "ig_refresh_token",
            "access_token": access_token,
        },
        timeout=30,
    )
    data = _json_or_error(response, "Falha ao renovar token oficial")
    token = str(data.get("access_token") or access_token)
    expires_in = int(data.get("expires_in") or 0)
    expires_at = (
        dt.datetime.utcnow() + dt.timedelta(seconds=expires_in)
        if expires_in > 0
        else None
    )
    return token, expires_at


def public_media_url(key: str) -> str:
    # Em R2/S3 a Meta baixa direto do bucket, sem atravessar a Railway.
    try:
        from core.storage import get_storage

        return get_storage().presign_download(key, expires_in=3600)
    except NotImplementedError:
        pass

    base = settings.public_base_url.strip().rstrip("/")
    if not base:
        raise MetaInstagramError(
            "PUBLIC_BASE_URL não configurada; a Meta precisa acessar a mídia por HTTPS."
        )
    return f"{base}/media/{quote(key, safe='/')}"


def _wait_container(container_id: str, access_token: str) -> None:
    for _ in range(60):
        response = requests.get(
            _graph_url(container_id),
            params={"fields": "status_code,status", "access_token": access_token},
            timeout=30,
        )
        data = _json_or_error(response, "Falha ao consultar processamento da mídia")
        status = str(data.get("status_code") or data.get("status") or "").upper()
        if status in ("FINISHED", "PUBLISHED"):
            return
        if status in ("ERROR", "EXPIRED"):
            raise MetaInstagramError(f"Container da Meta terminou com status {status}.")
        time.sleep(5)
    raise MetaInstagramError("A Meta demorou mais de 5 minutos para processar a mídia.")


def publish_media(
    *,
    access_token: str,
    ig_user_id: str,
    media_key: str,
    content_type: str,
    caption: str = "",
    cover_key: str | None = None,
) -> dict[str, str | None]:
    """Cria container, aguarda o processamento e publica."""
    media_url = public_media_url(media_key)
    is_video = Path(media_key).suffix.lower() in VIDEO_EXTENSIONS
    payload: dict[str, str] = {"access_token": access_token}

    if content_type == "reel":
        payload.update({"media_type": "REELS", "video_url": media_url})
        if caption:
            payload["caption"] = caption
        if cover_key:
            payload["cover_url"] = public_media_url(cover_key)
    elif content_type == "story":
        payload["media_type"] = "STORIES"
        payload["video_url" if is_video else "image_url"] = media_url
    elif content_type == "photo":
        payload["image_url"] = media_url
        if caption:
            payload["caption"] = caption
    else:
        raise MetaInstagramError(f"Tipo de conteúdo não suportado: {content_type}")

    create_response = requests.post(
        _graph_url(f"{ig_user_id}/media"),
        data=payload,
        timeout=60,
    )
    created = _json_or_error(create_response, "Falha ao criar container da Meta")
    container_id = str(created.get("id") or "")
    if not container_id:
        raise MetaInstagramError("A Meta não retornou o ID do container.")

    if is_video:
        _wait_container(container_id, access_token)

    publish_response = requests.post(
        _graph_url(f"{ig_user_id}/media_publish"),
        data={"creation_id": container_id, "access_token": access_token},
        timeout=60,
    )
    published = _json_or_error(publish_response, "Falha ao publicar container da Meta")
    media_id = str(published.get("id") or "")
    if not media_id:
        raise MetaInstagramError("A Meta não retornou o ID da publicação.")
    return {"id": media_id, "code": None, "url": None}
