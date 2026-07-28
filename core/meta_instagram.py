"""Cliente mínimo da Instagram API oficial (Business Login for Instagram)."""
from __future__ import annotations

import datetime as dt
import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from urllib.parse import quote, urlencode, urlsplit

import requests

from app.config import settings

OAUTH_AUTHORIZE_URL = "https://api.instagram.com/oauth/authorize"
OAUTH_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
GRAPH_BASE_URL = "https://graph.instagram.com"

_log = logging.getLogger(__name__)
# Proxy residencial da conta — todas as calls Graph/OAuth saem por ele (não pelo IP do Railway).
_meta_proxy_cv: ContextVar[str | None] = ContextVar("meta_http_proxy", default=None)


def proxies_for(proxy: str | None = None) -> dict[str, str] | None:
    """Monta dict proxies= do requests a partir do proxy da conta."""
    raw = proxy if proxy is not None else _meta_proxy_cv.get()
    if not raw or not str(raw).strip():
        return None
    from app.utils.proxy import normalize_proxy

    url = normalize_proxy(str(raw).strip())
    if not url:
        return None
    return {"http": url, "https": url}


@contextmanager
def meta_proxy_scope(proxy: str | None) -> Iterator[None]:
    """Define o proxy das HTTP Graph. Se proxy=None, herda o escopo atual (não limpa)."""
    if proxy is None:
        yield
        return
    token = _meta_proxy_cv.set(str(proxy).strip() or None)
    try:
        yield
    finally:
        _meta_proxy_cv.reset(token)


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


def _http(method: str, url: str, *, proxy: str | None = None, **kwargs) -> requests.Response:
    """HTTP para a Meta — usa proxy da conta (nunca cai no IP do Railway se houver proxy)."""
    raw = proxy if proxy is not None else _meta_proxy_cv.get()
    if raw:
        px = proxies_for(raw)
        if not px:
            raise MetaInstagramError(
                "Proxy da conta inválida para a API oficial. Atualize a proxy residencial."
            )
        kwargs.setdefault("proxies", px)
        _log.debug("META %s via proxy → %s", method.upper(), url[:96])
    return requests.request(method, url, **kwargs)


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
    """Normaliza caption para a Meta: UTF-8 limpo, sem controle/ZWSP invisível."""
    import unicodedata

    text = str(caption or "").replace("\r\n", "\n").replace("\r", "\n")
    cleaned: list[str] = []
    for ch in text:
        if ch in "\n\t":
            cleaned.append(ch)
            continue
        # Remove controles (C*), BOM, zero-width — Meta descarta caption “sujo”
        if ord(ch) in (0xFEFF, 0x200B, 0x200C, 0x200D, 0x2060, 0x00AD):
            continue
        if unicodedata.category(ch)[0] == "C":
            continue
        cleaned.append(ch)
    text = unicodedata.normalize("NFC", "".join(cleaned))
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    if not text:
        return ""
    if len(text) > META_CAPTION_MAX:
        text = text[: META_CAPTION_MAX - 1].rstrip() + "…"
    return text


def _caption_plain_fallback(caption: str) -> str:
    """Fallback se a Meta descartar a legenda original (emoji/UTF problemático).

    Mantém letras/números/pontuação básica e quebras de linha; remove símbolos
    (emoji) e reduz #/@ excessivos — último recurso antes de abortar.
    """
    import re
    import unicodedata

    text = _prepare_meta_caption(caption)
    out: list[str] = []
    for ch in text:
        if ch in "\n .,;:!?()-'\"/%&":
            out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat.startswith(("L", "N")) or cat in ("Pd", "Pe", "Ps", "Pi", "Pf", "Po"):
            out.append(ch)
        # pula So/Sk (emoji e símbolos)
    plain = "".join(out)
    plain = re.sub(r"[ \t]+\n", "\n", plain)
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    plain = re.sub(r"[#@]{3,}", "", plain)
    return _prepare_meta_caption(plain)


def uniquify_caption_for_account(caption: str, account_slot: int = 0) -> str:
    """Compat: não altera mais a legenda (ZWSP quebrava caption na Meta)."""
    _ = account_slot
    return _prepare_meta_caption(caption)


def post_media_comment(access_token: str, media_id: str, message: str) -> bool:
    """Último recurso: posta a legenda como 1º comentário se a Meta dropou o caption.

    A Graph não edita caption depois do publish. Comentário salva o texto no post
    quando delete falha (code 100/33) e o Reel já está no ar sem legenda.
    """
    import logging

    _log = logging.getLogger(__name__)
    text = _prepare_meta_caption(message)
    if not media_id or not text:
        return False
    # Comentários IG: limite prático ~2200; corta com margem.
    if len(text) > 2100:
        text = text[:2099].rstrip() + "…"
    try:
        response = _http(
            "POST",
            _graph_url(f"{media_id}/comments"),
            data={"message": text, "access_token": access_token},
            timeout=45,
        )
        data = response.json() if response.content else {}
    except Exception as exc:
        _log.warning("META comment falhou media=%s: %s", media_id, exc)
        return False
    ok = bool(response.ok and (data.get("id") or not data.get("error")))
    _log.info(
        "META comment caption-fallback media=%s http=%s ok=%s id=%s err=%s",
        media_id,
        response.status_code,
        ok,
        data.get("id"),
        (data.get("error") or {}).get("message") if isinstance(data, dict) else None,
    )
    return ok


def delete_media(access_token: str, media_id: str, *, rounds: int = 4) -> bool:
    """Tenta apagar mídia publicada. True se a Meta confirmou.

    Reels recém-publicados costumam rejeitar delete imediato (code 100/33).
    Faz várias rodadas com espera crescente antes de desistir.
    """
    import logging

    _log = logging.getLogger(__name__)
    if not media_id:
        return False
    url = _graph_url(media_id)
    methods = (
        {"method": "delete", "params": {"access_token": access_token}},
        {
            "method": "post",
            "params": {"access_token": access_token, "method": "delete"},
        },
    )
    waits = (3.0, 8.0, 15.0, 25.0)
    for round_i in range(max(1, int(rounds))):
        time.sleep(waits[round_i] if round_i < len(waits) else waits[-1])
        for attempt in methods:
            try:
                if attempt["method"] == "delete":
                    response = _http("DELETE", url, params=attempt["params"], timeout=30)
                else:
                    response = _http("POST", url, params=attempt["params"], timeout=30)
                try:
                    data = response.json() if response.content else {}
                except ValueError:
                    data = {}
                ok = bool(
                    response.ok
                    and (
                        data.get("success") is True
                        or data.get("id")
                        or (isinstance(data, dict) and not data.get("error"))
                    )
                )
                _log.info(
                    "META delete media=%s round=%s via=%s http=%s ok=%s raw=%s",
                    media_id,
                    round_i + 1,
                    attempt["method"],
                    response.status_code,
                    ok,
                    data or response.text[:300],
                )
                if ok:
                    return True
            except Exception as exc:
                _log.warning(
                    "META delete media=%s round=%s via=%s falhou: %s",
                    media_id,
                    round_i + 1,
                    attempt["method"],
                    exc,
                )
    return False


def fetch_media_caption(access_token: str, media_id: str) -> str | None:
    """Lê a caption publicada (None se a Meta não devolver o campo)."""
    response = _http(
        "GET",
        _graph_url(media_id),
        params={
            "fields": "caption,media_type,media_product_type,permalink",
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
    """Confirma que o Instagram gravou legenda. Retry longo (Graph atrasa sob carga).

    Returns:
        True  — caption presente e não-vazia
        False — confirmado vazio OU campo nunca apareceu após todas as tentativas
    """
    import logging

    _log = logging.getLogger(__name__)
    # Graph atrasa caption sob carga; janela ~4–5 min.
    delays = (5.0, 8.0, 12.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0)
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


def exchange_code(
    creds: MetaAppCredentials,
    code: str,
    *,
    proxy: str | None = None,
) -> tuple[str, dt.datetime | None]:
    """Troca code por token longo; retorna (token, expiração)."""
    with meta_proxy_scope(proxy):
        return _exchange_code_inner(creds, code)


def _exchange_code_inner(
    creds: MetaAppCredentials, code: str
) -> tuple[str, dt.datetime | None]:
    short_response = _http(
        "POST",
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

    long_response = _http(
        "GET",
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


def account_profile(access_token: str, *, proxy: str | None = None) -> dict[str, str]:
    with meta_proxy_scope(proxy):
        response = _http(
            "GET",
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


def validate_token(access_token: str, *, proxy: str | None = None) -> dict[str, str]:
    return account_profile(access_token, proxy=proxy)


def refresh_access_token(
    access_token: str, *, proxy: str | None = None
) -> tuple[str, dt.datetime | None]:
    with meta_proxy_scope(proxy):
        response = _http(
            "GET",
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


def fetch_ig_user_metrics(
    access_token: str,
    ig_user_id: str,
    *,
    proxy: str | None = None,
) -> dict[str, int | None]:
    with meta_proxy_scope(proxy):
        response = _http(
            "GET",
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


def fetch_media_insights(
    access_token: str,
    media_id: str,
    *,
    proxy: str | None = None,
) -> dict[str, int | None]:
    """Busca views do media. Sem `likes` — Meta rejeita likes em Reels (code 100)."""
    metric_sets = (
        "views,comments,shares,saved,reach,total_interactions",
        "views,reach,total_interactions",
        "views,reach",
        "views",
    )
    parsed: dict[str, int] = {}
    last_error: MetaInstagramError | None = None
    with meta_proxy_scope(proxy):
        for metrics in metric_sets:
            response = _http(
                "GET",
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


def fetch_media_permalink(
    access_token: str, media_id: str, *, proxy: str | None = None
) -> str | None:
    """Permalink público do post (para abrir no Instagram)."""
    with meta_proxy_scope(proxy):
        response = _http(
            "GET",
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
        response = _http(
            "GET",
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
            publish_response = _http(
                "POST",
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
    proxy: str | None = None,
) -> dict[str, object]:
    """Cria container, publica e EXIGE legenda em Reel/Foto.

    Caption SEMPRE no POST /{ig-user-id}/media (form UTF-8) — nunca no
    media_publish. Query string NÃO é usada (trunca legenda longa → Reel
    sem caption).

    Reel: capa (cover_url) + caption no mesmo POST. A API oficial NÃO deixa
    apagar Reel recém-publicado (100/33). Se a Graph não confirmar caption,
    abortamos (sem fallback em comentário).

    proxy: proxy residencial da conta — obrigatório nas calls Graph (IP fora do Railway).
    """
    if not (proxy or "").strip():
        raise MetaInstagramError(
            "Proxy residencial obrigatória para a API oficial. "
            "Configure a proxy da conta antes de publicar."
        )
    with meta_proxy_scope(proxy):
        return _publish_media_inner(
            access_token=access_token,
            ig_user_id=ig_user_id,
            media_key=media_key,
            content_type=content_type,
            caption=caption,
            cover_key=cover_key,
        )


def _publish_media_inner(
    *,
    access_token: str,
    ig_user_id: str,
    media_key: str,
    content_type: str,
    caption: str = "",
    cover_key: str | None = None,
) -> dict[str, object]:
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
    cover_skip_reason: str | None = None
    if content_type == "reel":
        if not cover_key:
            cover_skip_reason = "thumb_key ausente na automação"
            _log.warning("META capa SKIP: %s media=%s", cover_skip_reason, media_key)
        else:
            cover_url = public_media_url(cover_key)
            try:
                _validate_public_media_url(
                    cover_url,
                    expected_prefix="image/",
                    label="Capa",
                )
                _log.info(
                    "META capa READY cover_key=%s url=%s",
                    cover_key,
                    cover_url[:120],
                )
            except MetaInstagramError as cover_exc:
                cover_skip_reason = str(cover_exc)[:240]
                _log.warning(
                    "META capa INVÁLIDA cover_key=%s — seguindo sem capa: %s",
                    cover_key,
                    cover_skip_reason,
                )
                cover_url = None

    if caption_text:
        _log.info(
            "META publish caption BEFORE /media content_type=%s len=%s utf8_bytes=%s preview=%r",
            content_type,
            len(caption_text),
            len(caption_text.encode("utf-8")),
            caption_text[:80],
        )

    def _build_payload(
        *,
        use_cover: bool,
        caption_override: str | None = None,
    ) -> dict[str, str]:
        # Caption SEMPRE no /media (nunca no media_publish).
        # Form body UTF-8 — query string trunca legendas longas e a Meta
        # cria o container SEM caption (explica “às vezes vai, às vezes não”).
        cap = caption_text if caption_override is None else caption_override
        body: dict[str, str] = {"access_token": access_token}
        if content_type == "reel":
            body.update(
                {
                    "media_type": "REELS",
                    "video_url": media_url,
                    "caption": cap,
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
            body["caption"] = cap
        return body

    def _create_container(body: dict[str, str]) -> str:
        cap = body.get("caption") or ""
        if content_type in ("reel", "photo") and not cap.strip():
            raise MetaInstagramError(
                "Abortado: caption vazia no payload do POST /media (não publica)."
            )
        _log.info(
            "META POST /{ig}/media caption_len=%s utf8_bytes=%s share_to_feed=%s cover=%s preview=%r",
            len(cap),
            len(cap.encode("utf-8")),
            body.get("share_to_feed"),
            "cover_url" in body,
            cap[:80],
        )
        # application/x-www-form-urlencoded; charset=UTF-8 (doc + Postman)
        encoded = urlencode(body, doseq=True, encoding="utf-8", errors="strict")
        create_response = _http(
            "POST",
            _graph_url(f"{ig_user_id}/media"),
            data=encoded.encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
            timeout=90,
        )
        created = _json_or_error(create_response, "Falha ao criar container da Meta")
        cid = str(created.get("id") or "")
        if not cid:
            raise MetaInstagramError("A Meta não retornou o ID do container.")
        _log.info(
            "META container created id=%s caption_sent_len=%s cover=%s",
            cid,
            len(cap),
            "cover_url" in body,
        )
        return cid

    def _one_publish(
        *,
        use_cover: bool,
        caption_override: str | None = None,
    ) -> tuple[str, str | None, bool]:
        """Retorna (media_id, permalink, cover_applied)."""
        body = _build_payload(use_cover=use_cover, caption_override=caption_override)
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
            body = _build_payload(use_cover=False, caption_override=caption_override)
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
            # Caption de Reel indexa depois do media_publish — sob carga a Graph atrasa bem.
        time.sleep(12.0)
        link: str | None = None
        try:
            link = fetch_media_permalink(access_token, mid)
        except MetaInstagramError:
            link = None
        # Log imediato do GET caption (diagnóstico do usuário)
        try:
            raw_cap = fetch_media_caption(access_token, mid)
            _log.info(
                "META GET /{media-id}?fields=caption media=%s field=%s len=%s preview=%r",
                mid,
                "missing" if raw_cap is None else "present",
                len(raw_cap or ""),
                (raw_cap or "")[:80],
            )
        except MetaInstagramError as exc:
            _log.warning("META GET caption pós-publish falhou media=%s: %s", mid, exc)
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

    cover_error: str | None = cover_skip_reason
    cover_applied = False
    want_cover = bool(cover_url)
    permalink: str | None = None
    media_id = ""

    def _abort_missing_caption(mid: str, link: str | None) -> None:
        # NÃO postar legenda como comentário — o usuário quer caption no Reel.
        # Graph não edita caption e quase nunca deixa apagar Reel novo.
        raise MetaInstagramError(
            "Abortado: Instagram publicou o Reel/Foto SEM legenda. "
            "Enviamos caption no POST /media, mas a Graph não confirmou o campo. "
            "NÃO deixamos como sucesso (sem fallback em comentário).",
            code=None,
            subcode=None,
            error_type="caption_missing_abort",
        )

    # Preferir caption no post: se houver capa, tenta caption+cover; se a Graph
    # dropar a caption, NÃO aceitamos comentário — aborta (e libera slot).
    if want_cover:
        _log.info(
            "META capa ATTEMPT cover_key=%s (caption+cover juntos)",
            cover_key,
        )
        media_id, permalink, cover_applied = _one_publish(use_cover=True)
    else:
        _log.info(
            "META capa NÃO usada reason=%s — publish só com caption",
            cover_skip_reason or "sem cover_url",
        )
        media_id, permalink, cover_applied = _one_publish(use_cover=False)

    caption_ok = verify_published_caption(
        access_token,
        media_id,
        expected_min_len=1,
        attempts=10,
    )
    if not caption_ok:
        _log.warning(
            "META caption ainda ausente media=%s — espera extra e re-verifica (sem comentário)",
            media_id,
        )
        time.sleep(25.0)
        caption_ok = verify_published_caption(
            access_token,
            media_id,
            expected_min_len=1,
            attempts=6,
        )

    if not caption_ok:
        _log.error(
            "META caption AUSENTE media=%s (enviamos len=%s) — abort sem comentário",
            media_id,
            len(caption_text),
        )
        _abort_missing_caption(media_id, permalink)

    _log.info(
        "META capa RESULT media=%s cover_applied=%s cover_error=%r caption_ok=True",
        media_id,
        cover_applied,
        cover_error,
    )
    return {
        "id": media_id,
        "code": None,
        "url": permalink,
        "cover_applied": cover_applied,
        "cover_error": cover_error if not cover_applied and want_cover else None,
        "caption_sent_len": len(caption_text),
        "caption_verified": True,
    }
