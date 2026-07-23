"""Cliente mínimo da Instagram API oficial (Business Login for Instagram)."""
from __future__ import annotations

import datetime as dt
import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import requests

from app.config import settings

OAUTH_AUTHORIZE_URL = "https://api.instagram.com/oauth/authorize"
OAUTH_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
GRAPH_BASE_URL = "https://graph.instagram.com"
META_SCOPES = (
    "instagram_business_basic",
    "instagram_business_content_publish",
    "instagram_business_manage_insights",
)
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}
DEFAULT_PUBLIC_BASE_URL = "https://instablack-production.up.railway.app"


@dataclass(frozen=True)
class MetaAppCredentials:
    ig_app_id: str
    ig_app_secret: str
    redirect_uri: str


class MetaInstagramError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        subcode: int | None = None,
        error_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.subcode = subcode
        self.error_type = error_type


def _graph_url(path: str) -> str:
    version = settings.meta_instagram_graph_version.strip().lstrip("/")
    prefix = f"/{version}" if version else ""
    return f"{GRAPH_BASE_URL}{prefix}/{path.lstrip('/')}"


def meta_app_urls(instablack_app_id: int) -> dict[str, str]:
    origin = public_origin()
    base = f"{origin}/accounts/meta"
    suffix = str(instablack_app_id)
    return {
        "callback": f"{base}/callback/{suffix}",
        "deauthorize": f"{base}/deauthorize/{suffix}",
        "data_deletion": f"{base}/data-deletion/{suffix}",
    }


def authorization_url(creds: MetaAppCredentials, state: str) -> str:
    params = {
        "client_id": creds.ig_app_id,
        "redirect_uri": creds.redirect_uri,
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
        if isinstance(error, dict):
            parts = [
                str(error.get("message") or "").strip(),
                f"type={error['type']}" if error.get("type") else "",
                f"code={error['code']}" if error.get("code") is not None else "",
                (
                    f"subcode={error['error_subcode']}"
                    if error.get("error_subcode") is not None
                    else ""
                ),
                str(error.get("error_user_title") or "").strip(),
                str(error.get("error_user_msg") or "").strip(),
                f"trace={error['fbtrace_id']}" if error.get("fbtrace_id") else "",
            ]
            detail = " | ".join(part for part in parts if part)
        else:
            detail = str(error)
        detail = detail or response.text[:500]
        code = error.get("code") if isinstance(error, dict) else None
        subcode = error.get("error_subcode") if isinstance(error, dict) else None
        raise MetaInstagramError(
            f"{action}: {detail}",
            code=int(code) if isinstance(code, int) or str(code).isdigit() else None,
            subcode=(
                int(subcode)
                if isinstance(subcode, int) or str(subcode).isdigit()
                else None
            ),
            error_type=(
                str(error.get("type") or "") or None
                if isinstance(error, dict)
                else None
            ),
        )
    return payload


def exchange_code(creds: MetaAppCredentials, code: str) -> tuple[str, dt.datetime | None]:
    """Troca code por token longo; retorna (token, expiração)."""
    short_response = requests.post(
        OAUTH_TOKEN_URL,
        data={
            "client_id": creds.ig_app_id,
            "client_secret": creds.ig_app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": creds.redirect_uri,
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
            "client_secret": creds.ig_app_secret,
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


def _app_media_url(key: str) -> str:
    base = settings.public_base_url.strip()
    if not base:
        railway_url = os.getenv("RAILWAY_STATIC_URL", "").strip()
        railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
        base = railway_url or (f"https://{railway_domain}" if railway_domain else "")
    if not base:
        base = DEFAULT_PUBLIC_BASE_URL
    base = base.rstrip("/")
    return f"{base}/media/{quote(key, safe='/')}"


def public_media_url(key: str) -> str:
    """URL HTTPS estável para a Meta baixar a mídia pelo Instablack.

    O R2 continua sendo apenas o armazenamento interno. Não entregamos sua URL
    assinada à Meta, pois ela pode expirar ou ser recusada durante o container.
    """
    return _app_media_url(key)


def public_origin() -> str:
    """Origem HTTPS pública usada em páginas e callbacks do App Review."""
    return _app_media_url("x").rsplit("/media/", 1)[0]


def parse_signed_request(creds: MetaAppCredentials, signed_request: str) -> dict:
    """Valida o signed_request enviado pela Meta em deauthorize/data-deletion."""
    import base64
    import hashlib
    import hmac
    import json

    if not signed_request or "." not in signed_request:
        raise MetaInstagramError("signed_request inválido.")
    encoded_sig, payload = signed_request.split(".", 1)
    secret = creds.ig_app_secret.strip()
    if not secret:
        raise MetaInstagramError("App Secret não configurado.")

    def _b64url(data: str) -> bytes:
        padding = "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(data + padding)

    expected = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(expected, _b64url(encoded_sig)):
        raise MetaInstagramError("Assinatura do signed_request inválida.")

    try:
        data = json.loads(_b64url(payload).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise MetaInstagramError("Payload do signed_request inválido.") from exc
    if not isinstance(data, dict):
        raise MetaInstagramError("Payload do signed_request inválido.")
    return data


def fetch_ig_user_metrics(access_token: str, ig_user_id: str) -> dict[str, int | None]:
    response = requests.get(
        _graph_url(ig_user_id),
        params={
            "fields": "followers_count,media_count",
            "access_token": access_token,
        },
        timeout=30,
    )
    data = _json_or_error(response, "Falha ao consultar métricas da conta")
    followers = data.get("followers_count")
    media_count = data.get("media_count")
    return {
        "followers_count": int(followers) if followers is not None else None,
        "media_count": int(media_count) if media_count is not None else None,
    }


def _parse_insights_payload(payload: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    rows = payload.get("data")
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        if not name:
            continue
        value = None
        total = row.get("total_value")
        if isinstance(total, dict) and total.get("value") is not None:
            value = total.get("value")
        else:
            values = row.get("values")
            if isinstance(values, list) and values:
                first = values[0]
                if isinstance(first, dict):
                    value = first.get("value")
        if value is None:
            continue
        try:
            out[name] = int(value)
        except (TypeError, ValueError):
            pass
    return out


def fetch_media_insights(access_token: str, media_id: str) -> dict[str, int | None]:
    """Busca views/likes do media. Meta deprecou `plays`/`video_views` — use `views`."""
    metric_sets = (
        "views,likes,comments,shares,saved,reach,total_interactions",
        "views,reach,likes",
        "views,likes",
    )
    parsed: dict[str, int] = {}
    last_error: MetaInstagramError | None = None
    for metrics in metric_sets:
        response = requests.get(
            _graph_url(f"{media_id}/insights"),
            params={"metric": metrics, "access_token": access_token},
            timeout=30,
        )
        try:
            parsed = _parse_insights_payload(
                _json_or_error(response, "Falha ao consultar insights")
            )
            if parsed:
                break
        except MetaInstagramError as exc:
            last_error = exc
            continue
    if not parsed and last_error is not None:
        raise last_error

    plays = (
        parsed.get("views")
        or parsed.get("plays")
        or parsed.get("video_views")
        or parsed.get("impressions")
    )
    return {
        "play_count": int(plays) if plays is not None else None,
        "like_count": parsed.get("likes"),
        "comments": parsed.get("comments"),
        "reach": parsed.get("reach"),
    }


def fetch_media_permalink(access_token: str, media_id: str) -> str | None:
    """Permalink público do post (para abrir no Instagram)."""
    response = requests.get(
        _graph_url(media_id),
        params={
            "fields": "permalink,shortcode",
            "access_token": access_token,
        },
        timeout=30,
    )
    data = _json_or_error(response, "Falha ao consultar permalink da mídia")
    permalink = str(data.get("permalink") or "").strip()
    if permalink:
        return permalink
    shortcode = str(data.get("shortcode") or "").strip()
    if shortcode:
        return f"https://www.instagram.com/reel/{shortcode}/"
    return None


def _validate_public_media_url(
    url: str,
    *,
    expected_prefix: str,
    label: str,
) -> None:
    """Confirma que a URL pública responde (check leve + retry).

    Antes fazia HEAD + GET com timeout 60s; sob carga no Railway isso gerava
    Read timed out em sequência. Agora: só Range GET (1 byte), timeout maior e
    até 3 tentativas com pausa curta.
    """
    import time

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with requests.get(
                url,
                headers={"Range": "bytes=0-0"},
                allow_redirects=True,
                stream=True,
                timeout=(20, 120),
            ) as probe:
                if probe.status_code not in (200, 206):
                    raise MetaInstagramError(
                        f"{label} não aceita download: GET retornou HTTP {probe.status_code}."
                    )
                # Consome no máximo 1 chunk para liberar a conexão
                next(probe.iter_content(chunk_size=64), b"")
                content_type = (probe.headers.get("Content-Type") or "").lower()
                if expected_prefix and content_type and not content_type.startswith(
                    expected_prefix
                ):
                    # Alguns proxies devolvem octet-stream; não bloqueia só por isso
                    if "octet-stream" not in content_type and "binary" not in content_type:
                        raise MetaInstagramError(
                            f"{label} retornou Content-Type {content_type}; "
                            f"esperado {expected_prefix}."
                        )
            return
        except MetaInstagramError:
            raise
        except (OSError, ValueError, requests.RequestException) as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise MetaInstagramError(
                f"Não foi possível validar o download público de {label}: {exc}"
            ) from exc
    if last_exc is not None:
        raise MetaInstagramError(
            f"Não foi possível validar o download público de {label}: {last_exc}"
        ) from last_exc


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
) -> dict[str, object]:
    """Cria container, aguarda o processamento e publica."""
    media_url = public_media_url(media_key)
    is_video = Path(media_key).suffix.lower() in VIDEO_EXTENSIONS
    _validate_public_media_url(
        media_url,
        expected_prefix="video/" if is_video else "image/",
        label="Vídeo" if is_video else "Imagem",
    )
    payload: dict[str, str] = {"access_token": access_token}

    if content_type == "reel":
        payload.update({"media_type": "REELS", "video_url": media_url})
        if caption:
            payload["caption"] = caption
        if cover_key:
            cover_url = public_media_url(cover_key)
            _validate_public_media_url(
                cover_url,
                expected_prefix="image/jpeg",
                label="Capa",
            )
            payload["cover_url"] = cover_url
    elif content_type == "story":
        payload["media_type"] = "STORIES"
        payload["video_url" if is_video else "image_url"] = media_url
    elif content_type == "photo":
        payload["image_url"] = media_url
        if caption:
            payload["caption"] = caption
    else:
        raise MetaInstagramError(f"Tipo de conteúdo não suportado: {content_type}")

    cover_error: str | None = None
    try:
        create_response = requests.post(
            _graph_url(f"{ig_user_id}/media"),
            data=payload,
            timeout=60,
        )
        created = _json_or_error(create_response, "Falha ao criar container da Meta")
    except MetaInstagramError as exc:
        detail = str(exc).lower()
        cover_rejected = any(
            marker in detail
            for marker in ("cover_url", "cover photo", "thumbnail", "thumb image")
        )
        if not payload.get("cover_url") or not cover_rejected:
            raise
        # A capa nunca deve impedir o Reel inteiro. Repete sem cover_url para
        # preservar a publicação e devolve o erro para aviso ao usuário.
        cover_error = str(exc)
        fallback_payload = dict(payload)
        fallback_payload.pop("cover_url", None)
        fallback_payload["thumb_offset"] = "0"
        fallback_response = requests.post(
            _graph_url(f"{ig_user_id}/media"),
            data=fallback_payload,
            timeout=60,
        )
        created = _json_or_error(
            fallback_response,
            f"{exc}; tentativa sem capa também falhou",
        )
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
    permalink: str | None = None
    try:
        permalink = fetch_media_permalink(access_token, media_id)
    except MetaInstagramError:
        permalink = None
    return {
        "id": media_id,
        "code": None,
        "url": permalink,
        "cover_applied": bool(cover_key and not cover_error),
        "cover_error": cover_error,
    }
