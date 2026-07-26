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
# Limite da Graph API / Instagram para caption de Reel/Foto.
META_CAPTION_MAX = 2200


def _prepare_meta_caption(caption: str | None) -> str:
    """Normaliza caption exatamente como a Meta espera (texto puro, sem gambiarra)."""
    text = str(caption or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    if len(text) > META_CAPTION_MAX:
        text = text[: META_CAPTION_MAX - 1].rstrip() + "…"
    return text


def uniquify_caption_for_account(caption: str, account_slot: int = 0) -> str:
    """Compat: não altera mais a legenda (ZWSP quebrava caption na Meta)."""
    _ = account_slot
    return _prepare_meta_caption(caption)


def delete_media(access_token: str, media_id: str) -> bool:
    """Tenta apagar mídia publicada (best-effort). True se a Meta confirmou."""
    import logging

    _log = logging.getLogger(__name__)
    if not media_id:
        return False
    try:
        response = requests.delete(
            _graph_url(media_id),
            params={"access_token": access_token},
            timeout=30,
        )
        data = _json_or_error(response, "Falha ao apagar mídia da Meta")
        ok = bool(data.get("success") is True or data.get("id") or response.ok)
        _log.info("META delete media=%s ok=%s raw=%s", media_id, ok, data)
        return ok
    except MetaInstagramError as exc:
        _log.warning("META delete media=%s falhou: %s", media_id, exc)
        return False


def fetch_media_caption(access_token: str, media_id: str) -> str | None:
    """Lê a caption publicada (None se a Meta não devolver o campo)."""
    response = requests.get(
        _graph_url(media_id),
        params={
            "fields": "caption,media_type,media_product_type",
            "access_token": access_token,
        },
        timeout=30,
    )
    data = _json_or_error(response, "Falha ao consultar caption da mídia")
    if "caption" not in data:
        return None
    return str(data.get("caption") or "")


def verify_published_caption(
    access_token: str,
    media_id: str,
    *,
    expected_min_len: int = 1,
    attempts: int = 8,
) -> bool:
    """Confirma que o Instagram gravou legenda. Retry agressivo (Reels demoram a indexar).

    Returns:
        True  — caption presente e não-vazia
        False — confirmado vazio OU campo nunca apareceu após todas as tentativas
    """
    import logging

    _log = logging.getLogger(__name__)
    # Espera crescente: ~2+3+4+5+6+8+10+12 ≈ 50s no pior caso
    delays = (2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0)
    last_raw: str | None = None
    saw_field = False

    for i in range(max(1, attempts)):
        wait = delays[i] if i < len(delays) else delays[-1]
        time.sleep(wait)
        try:
            raw = fetch_media_caption(access_token, media_id)
        except MetaInstagramError as exc:
            _log.warning(
                "META caption verify attempt=%s/%s media=%s err=%s",
                i + 1,
                attempts,
                media_id,
                exc,
            )
            continue

        if raw is None:
            _log.info(
                "META caption verify attempt=%s/%s media=%s field_missing",
                i + 1,
                attempts,
                media_id,
            )
            continue

        saw_field = True
        last_raw = raw
        got = raw.replace("\u200b", "").strip()
        if len(got) >= max(1, int(expected_min_len or 1)):
            _log.info(
                "META caption verify ok media=%s attempt=%s got_len=%s",
                media_id,
                i + 1,
                len(got),
            )
            return True
        _log.warning(
            "META caption verify empty media=%s attempt=%s raw_len=%s",
            media_id,
            i + 1,
            len(raw),
        )

    _log.error(
        "META caption verify FAILED media=%s saw_field=%s last_raw_len=%s",
        media_id,
        saw_field,
        len(last_raw or ""),
    )
    return False


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
        message_l = (str(error.get("message") or "") if isinstance(error, dict) else "").lower()
        if (
            "permission" in message_l
            or "instagram_content_publish" in message_l
            or "instagram_business_content_publish" in message_l
            or code in (10, 200)
        ):
            detail = (
                "Sem permissão para publicar (instagram_content_publish). "
                "Reconecte a conta Meta concedendo publicação de conteúdo. "
                f"Detalhe: {detail}"
            )
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


def _wait_container(
    container_id: str,
    access_token: str,
    *,
    settle_seconds: float = 2.0,
) -> None:
    """Aguarda status FINISHED antes do media_publish (docs Meta / erro 9007)."""
    for _ in range(60):
        response = requests.get(
            _graph_url(container_id),
            params={"fields": "status_code,status", "access_token": access_token},
            timeout=30,
        )
        data = _json_or_error(response, "Falha ao consultar processamento da mídia")
        status = str(data.get("status_code") or data.get("status") or "").upper()
        if status in ("FINISHED", "PUBLISHED"):
            # Mesmo com FINISHED a Meta às vezes ainda rejeita publish (race 9007).
            if settle_seconds > 0:
                time.sleep(settle_seconds)
            return
        if status in ("ERROR", "EXPIRED"):
            raise MetaInstagramError(f"Container da Meta terminou com status {status}.")
        time.sleep(5)
    raise MetaInstagramError("A Meta demorou mais de 5 minutos para processar a mídia.")


def _is_media_not_ready(exc: MetaInstagramError) -> bool:
    if exc.code == 9007 or exc.subcode == 2207027:
        return True
    msg = str(exc).lower()
    return "media id is not available" in msg or "not ready for publishing" in msg


def _publish_container(
    *,
    ig_user_id: str,
    container_id: str,
    access_token: str,
) -> dict:
    """Chama media_publish; re-tenta se a Meta ainda não liberou o container (9007)."""
    last_exc: MetaInstagramError | None = None
    for attempt in range(8):
        try:
            publish_response = requests.post(
                _graph_url(f"{ig_user_id}/media_publish"),
                data={"creation_id": container_id, "access_token": access_token},
                timeout=60,
            )
            return _json_or_error(
                publish_response, "Falha ao publicar container da Meta"
            )
        except MetaInstagramError as exc:
            if not _is_media_not_ready(exc):
                raise
            last_exc = exc
            # Reconfirma FINISHED e espera um pouco mais a cada tentativa.
            _wait_container(
                container_id,
                access_token,
                settle_seconds=2.0 + attempt * 2.0,
            )
    assert last_exc is not None
    raise last_exc


def publish_media(
    *,
    access_token: str,
    ig_user_id: str,
    media_key: str,
    content_type: str,
    caption: str = "",
    cover_key: str | None = None,
) -> dict[str, object]:
    """Cria container, publica e EXIGE legenda em Reel/Foto.

    Se a Meta publicar sem caption confirmada: apaga o post e tenta de novo
    (sem capa). Se ainda falhar: apaga de novo e ABORTA — não deixa Reel sem
    legenda como sucesso.
    """
    import logging

    _log = logging.getLogger(__name__)
    media_url = public_media_url(media_key)
    is_video = Path(media_key).suffix.lower() in VIDEO_EXTENSIONS
    caption_text = _prepare_meta_caption(caption)
    _validate_public_media_url(
        media_url,
        expected_prefix="video/" if is_video else "image/",
        label="Vídeo" if is_video else "Imagem",
    )

    if content_type not in ("reel", "story", "photo"):
        raise MetaInstagramError(f"Tipo de conteúdo não suportado: {content_type}")
    if content_type in ("reel", "photo") and not caption_text:
        raise MetaInstagramError(
            "Abortado: Reel/Foto sem legenda — a Meta não recebe caption vazio."
        )

    cover_url: str | None = None
    if content_type == "reel" and cover_key:
        cover_url = public_media_url(cover_key)
        try:
            _validate_public_media_url(
                cover_url,
                expected_prefix="image/jpeg",
                label="Capa",
            )
        except MetaInstagramError as cover_exc:
            _log.warning("META capa inválida, seguindo sem capa: %s", cover_exc)
            cover_url = None

    if caption_text:
        _log.info(
            "META publish caption content_type=%s len=%s",
            content_type,
            len(caption_text),
        )

    def _build_payload(*, use_cover: bool) -> dict[str, str]:
        # Formato da doc Meta / Postman Instagram Login:
        # media_type=REELS&video_url=...&caption=...&share_to_feed=true
        body: dict[str, str] = {"access_token": access_token}
        if content_type == "reel":
            body.update(
                {
                    "media_type": "REELS",
                    "video_url": media_url,
                    "caption": caption_text,
                    "share_to_feed": "true",
                }
            )
            if use_cover and cover_url:
                body["cover_url"] = cover_url
        elif content_type == "story":
            body["media_type"] = "STORIES"
            body["video_url" if is_video else "image_url"] = media_url
        else:  # photo
            body["image_url"] = media_url
            body["caption"] = caption_text
        return body

    def _create_container(body: dict[str, str]) -> str:
        # requests data=dict = form urlencoded UTF-8 (igual aos exemplos Meta)
        create_response = requests.post(
            _graph_url(f"{ig_user_id}/media"),
            data=body,
            timeout=60,
        )
        created = _json_or_error(create_response, "Falha ao criar container da Meta")
        cid = str(created.get("id") or "")
        if not cid:
            raise MetaInstagramError("A Meta não retornou o ID do container.")
        # Loga se a caption foi de fato enviada no create
        _log.info(
            "META container created id=%s caption_in_payload=%s len=%s share_to_feed=%s cover=%s",
            cid,
            "caption" in body and bool(body.get("caption")),
            len(body.get("caption") or ""),
            body.get("share_to_feed"),
            "cover_url" in body,
        )
        return cid

    def _one_publish(*, use_cover: bool) -> tuple[str, str | None, bool]:
        """Retorna (media_id, permalink, cover_applied)."""
        body = _build_payload(use_cover=use_cover)
        try:
            container_id = _create_container(body)
            cover_applied = bool(use_cover and cover_url and "cover_url" in body)
        except MetaInstagramError as exc:
            detail = str(exc).lower()
            cover_rejected = any(
                marker in detail
                for marker in ("cover_url", "cover photo", "thumbnail", "thumb image")
            )
            if not (use_cover and cover_url and cover_rejected):
                raise
            _log.warning("META capa rejeitada no create — retry sem capa: %s", exc)
            body = _build_payload(use_cover=False)
            container_id = _create_container(body)
            cover_applied = False

        _wait_container(container_id, access_token)
        published = _publish_container(
            ig_user_id=ig_user_id,
            container_id=container_id,
            access_token=access_token,
        )
        mid = str(published.get("id") or "")
        if not mid:
            raise MetaInstagramError("A Meta não retornou o ID da publicação.")
        link: str | None = None
        try:
            link = fetch_media_permalink(access_token, mid)
        except MetaInstagramError:
            link = None
        return mid, link, cover_applied

    # Story: sem exigência de caption
    if content_type == "story":
        media_id, permalink, cover_applied = _one_publish(use_cover=False)
        return {
            "id": media_id,
            "code": None,
            "url": permalink,
            "cover_applied": False,
            "cover_error": None,
            "caption_sent_len": 0,
            "caption_verified": None,
        }

    # Reel/Foto: publicar → verificar caption → se falhar, APAGAR e republicar
    cover_error: str | None = None
    media_id, permalink, cover_applied = _one_publish(use_cover=bool(cover_url))
    caption_ok = verify_published_caption(
        access_token,
        media_id,
        expected_min_len=1,
        attempts=6,
    )

    if not caption_ok:
        _log.error(
            "META caption AUSENTE no 1º publish media=%s — apagando e republicando",
            media_id,
        )
        deleted = delete_media(access_token, media_id)
        _log.info("META delete 1º post media=%s deleted=%s", media_id, deleted)

        # 2ª tentativa: SEM capa (capa às vezes faz a Meta dropar caption)
        media_id, permalink, cover_applied = _one_publish(use_cover=False)
        cover_applied = False
        cover_error = "Republicado sem capa para forçar legenda"
        caption_ok = verify_published_caption(
            access_token,
            media_id,
            expected_min_len=1,
            attempts=8,
        )

    if not caption_ok:
        _log.error(
            "META caption AUSENTE após retry media=%s — apagando e abortando",
            media_id,
        )
        deleted = delete_media(access_token, media_id)
        raise MetaInstagramError(
            "Abortado: Instagram publicou o Reel/Foto SEM legenda. "
            f"Tentamos apagar o post (ok={deleted}) e NÃO deixamos como sucesso. "
            "Tente de novo em alguns minutos."
        )

    return {
        "id": media_id,
        "code": None,
        "url": permalink,
        "cover_applied": cover_applied,
        "cover_error": cover_error,
        "caption_sent_len": len(caption_text),
        "caption_verified": True,
    }
