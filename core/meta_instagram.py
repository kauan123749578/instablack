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
    if getattr(settings, "meta_http_mock", False):
        return _mock_meta_http(method, url, **kwargs)
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


def _mock_meta_http(method: str, url: str, **kwargs) -> requests.Response:
    """Stub Graph API para stress test (META_HTTP_MOCK=true)."""
    import json
    import re

    delay_ms = int(getattr(settings, "meta_http_mock_delay_ms", 150) or 0)
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)

    path = urlsplit(url).path or ""
    lower = path.lower()
    payload: dict = {"id": "mock_media_1"}

    if "/media_publish" in lower or lower.endswith("/media_publish"):
        payload = {"id": f"mock_published_{int(time.time())}"}
    elif re.search(r"/media/?$", lower) or "/media?" in (url.lower()):
        payload = {"id": f"mock_container_{int(time.time())}"}
    elif "status_code" in (kwargs.get("params") or {}) or "/?" in url:
        # container status poll
        if re.search(r"/\d+", path) or "mock_container" in path:
            payload = {"status_code": "FINISHED", "status": "FINISHED", "id": "mock_container"}
    elif "permalink" in lower or "fields=" in url.lower():
        if "permalink" in str(kwargs.get("params") or {}).get("fields", "") or "permalink" in url.lower():
            payload = {
                "id": "mock_media_1",
                "permalink": "https://www.instagram.com/reel/MOCK/",
                "caption": (kwargs.get("params") or {}).get("caption") or "ok",
            }
        else:
            payload = {"status_code": "FINISHED", "id": "mock_container"}
    elif method.upper() == "GET":
        payload = {
            "id": "mock_media_1",
            "status_code": "FINISHED",
            "permalink": "https://www.instagram.com/reel/MOCK/",
            "username": "mock_user",
            "followers_count": 100,
        }

    resp = requests.Response()
    resp.status_code = 200
    resp._content = json.dumps(payload).encode("utf-8")
    resp.headers["Content-Type"] = "application/json"
    resp.url = url
    return resp


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
    attempts: int = 5,
) -> str:
    """Confirma caption na Graph. Janela curta (~60–90s) para não segurar o worker.

    Returns:
        "ok"      — caption presente e não-vazia
        "empty"  — campo caption presente mas vazio (abortar)
        "missing"— campo nunca apareceu (Graph atrasada; não abortar)
    """
    import logging

    _log = logging.getLogger(__name__)
    # ~70s de sleep total (5+8+12+15+20) + GETs — evita 6 min de worker preso.
    delays = (5.0, 8.0, 12.0, 15.0, 20.0)
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
            return "ok"
        _log.warning(
            "META caption verify empty media=%s attempt=%s raw_len=%s",
            media_id,
            i + 1,
            len(raw),
        )

    if saw_field:
        _log.error(
            "META caption verify EMPTY media=%s last_raw_len=%s",
            media_id,
            len(last_raw or ""),
        )
        return "empty"

    _log.warning(
        "META caption verify MISSING media=%s (campo não indexou a tempo)",
        media_id,
    )
    return "missing"


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
                "fields": "followers_count,media_count,profile_picture_url,username",
                "access_token": access_token,
            },
            timeout=30,
        )
        data = _json_or_error(response, "Falha ao consultar métricas da conta")
    followers = data.get("followers_count")
    media_count = data.get("media_count")
    pic = str(data.get("profile_picture_url") or "").strip() or None
    return {
        "followers_count": int(followers) if followers is not None else None,
        "media_count": int(media_count) if media_count is not None else None,
        "profile_picture_url": pic,
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

    if getattr(settings, "meta_http_mock", False):
        return

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


def get_container_status(
    container_id: str,
    access_token: str,
    *,
    proxy: str | None = None,
) -> str:
    """Um GET no status do container. Retorna status_code uppercased."""
    with meta_proxy_scope(proxy):
        response = _http(
            "GET",
            _graph_url(container_id),
            params={"fields": "status_code,status", "access_token": access_token},
            timeout=30,
        )
        data = _json_or_error(response, "Falha ao consultar processamento da mídia")
    return str(data.get("status_code") or data.get("status") or "").upper()


def _wait_container(
    container_id: str,
    access_token: str,
    *,
    settle_seconds: float = 2.0,
) -> None:
    """Aguarda status FINISHED (caminho sync / fallback). Preferir poll Celery."""
    for _ in range(60):
        status = get_container_status(container_id, access_token)
        if status in ("FINISHED", "PUBLISHED"):
            settle = 0.0 if getattr(settings, "meta_http_mock", False) else settle_seconds
            if settle > 0:
                time.sleep(settle)
            return
        if status in ("ERROR", "EXPIRED"):
            raise MetaInstagramError(f"Container da Meta terminou com status {status}.")
        time.sleep(0.05 if getattr(settings, "meta_http_mock", False) else 5)
    raise MetaInstagramError("A Meta demorou mais de 5 minutos para processar a mídia.")


def _is_media_not_ready(exc: MetaInstagramError) -> bool:
    if exc.code == 9007 or exc.subcode == 2207027:
        return True
    msg = str(exc).lower()
    return "media id is not available" in msg or "not ready for publishing" in msg


def _publish_container_once(
    *,
    ig_user_id: str,
    container_id: str,
    access_token: str,
) -> dict:
    """Uma tentativa de media_publish (sem sleep longo)."""
    publish_response = _http(
        "POST",
        _graph_url(f"{ig_user_id}/media_publish"),
        data={"creation_id": container_id, "access_token": access_token},
        timeout=60,
    )
    return _json_or_error(publish_response, "Falha ao publicar container da Meta")


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
            return _publish_container_once(
                ig_user_id=ig_user_id,
                container_id=container_id,
                access_token=access_token,
            )
        except MetaInstagramError as exc:
            if not _is_media_not_ready(exc):
                raise
            last_exc = exc
            _wait_container(
                container_id,
                access_token,
                settle_seconds=2.0 + attempt * 2.0,
            )
    assert last_exc is not None
    raise last_exc


def _resolve_cover_url(
    *,
    content_type: str,
    cover_key: str | None,
    media_key: str,
) -> tuple[str | None, str | None]:
    """Retorna (cover_url, cover_skip_reason)."""
    if content_type != "reel":
        return None, None
    if not cover_key:
        reason = "thumb_key ausente na automação"
        _log.warning("META capa SKIP: %s media=%s", reason, media_key)
        return None, reason
    cover_url = public_media_url(cover_key)
    try:
        _validate_public_media_url(
            cover_url,
            expected_prefix="image/",
            label="Capa",
        )
        _log.info("META capa READY cover_key=%s url=%s", cover_key, cover_url[:120])
        return cover_url, None
    except MetaInstagramError as cover_exc:
        reason = str(cover_exc)[:240]
        _log.warning(
            "META capa INVÁLIDA cover_key=%s — seguindo sem capa: %s",
            cover_key,
            reason,
        )
        return None, reason


def _build_media_payload(
    *,
    access_token: str,
    ig_user_id: str,
    media_key: str,
    content_type: str,
    caption_text: str,
    cover_url: str | None,
    use_cover: bool,
) -> dict[str, str]:
    _ = ig_user_id
    is_video = Path(media_key).suffix.lower() in VIDEO_EXTENSIONS
    media_url = public_media_url(media_key)
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
    else:
        body["image_url"] = media_url
        body["caption"] = caption_text
    return body


def _create_media_container(
    *,
    ig_user_id: str,
    content_type: str,
    body: dict[str, str],
) -> str:
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


def submit_media_container(
    *,
    access_token: str,
    ig_user_id: str,
    media_key: str,
    content_type: str,
    caption: str = "",
    cover_key: str | None = None,
    proxy: str | None = None,
) -> dict[str, object]:
    """Valida URL + cria container. NÃO espera processamento (async poll)."""
    with meta_proxy_scope(proxy):
        return _submit_media_container_inner(
            access_token=access_token,
            ig_user_id=ig_user_id,
            media_key=media_key,
            content_type=content_type,
            caption=caption,
            cover_key=cover_key,
        )


def _submit_media_container_inner(
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

    cover_url, cover_skip_reason = _resolve_cover_url(
        content_type=content_type,
        cover_key=cover_key,
        media_key=media_key,
    )
    if caption_text:
        _log.info(
            "META submit caption BEFORE /media content_type=%s len=%s utf8_bytes=%s preview=%r",
            content_type,
            len(caption_text),
            len(caption_text.encode("utf-8")),
            caption_text[:80],
        )

    want_cover = bool(cover_url) and content_type == "reel"
    use_cover = want_cover
    cover_applied = False
    if content_type == "story":
        use_cover = False

    body = _build_media_payload(
        access_token=access_token,
        ig_user_id=ig_user_id,
        media_key=media_key,
        content_type=content_type,
        caption_text=caption_text,
        cover_url=cover_url,
        use_cover=use_cover,
    )
    try:
        container_id = _create_media_container(
            ig_user_id=ig_user_id,
            content_type=content_type,
            body=body,
        )
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
        body = _build_media_payload(
            access_token=access_token,
            ig_user_id=ig_user_id,
            media_key=media_key,
            content_type=content_type,
            caption_text=caption_text,
            cover_url=cover_url,
            use_cover=False,
        )
        container_id = _create_media_container(
            ig_user_id=ig_user_id,
            content_type=content_type,
            body=body,
        )
        cover_applied = False

    submitted_at = dt.datetime.utcnow().isoformat()
    cover_error = None
    if content_type == "reel" and want_cover and not cover_applied:
        cover_error = cover_skip_reason or "capa não aplicada"
    return {
        "container_id": container_id,
        "cover_applied": cover_applied if content_type == "reel" else False,
        "cover_error": cover_error,
        "caption_sent_len": 0 if content_type == "story" else len(caption_text),
        "caption_verified": None if content_type == "story" else True,
        "submitted_at": submitted_at,
        "content_type": content_type,
    }


def finalize_media_publish(
    *,
    access_token: str,
    ig_user_id: str,
    container_id: str,
    proxy: str | None = None,
    settle_seconds: float = 2.0,
    allow_not_ready: bool = False,
) -> dict[str, object]:
    """media_publish + permalink. Se allow_not_ready e 9007 → {not_ready: True}."""
    with meta_proxy_scope(proxy):
        settle = 0.0 if getattr(settings, "meta_http_mock", False) else max(0.0, settle_seconds)
        if settle > 0:
            time.sleep(settle)
        try:
            if allow_not_ready:
                published = _publish_container_once(
                    ig_user_id=ig_user_id,
                    container_id=container_id,
                    access_token=access_token,
                )
            else:
                published = _publish_container(
                    ig_user_id=ig_user_id,
                    container_id=container_id,
                    access_token=access_token,
                )
        except MetaInstagramError as exc:
            if allow_not_ready and _is_media_not_ready(exc):
                return {"not_ready": True, "error": str(exc)[:240]}
            raise
        mid = str(published.get("id") or "")
        if not mid:
            raise MetaInstagramError("A Meta não retornou o ID da publicação.")
        if not getattr(settings, "meta_http_mock", False):
            time.sleep(1.5)
        link: str | None = None
        try:
            link = fetch_media_permalink(access_token, mid)
        except MetaInstagramError:
            link = None
        return {
            "id": mid,
            "code": None,
            "url": link,
            "not_ready": False,
        }


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
    """Caminho sync: submit + wait local + finalize (flag async off / fallback)."""
    with meta_proxy_scope(proxy):
        submitted = _submit_media_container_inner(
            access_token=access_token,
            ig_user_id=ig_user_id,
            media_key=media_key,
            content_type=content_type,
            caption=caption,
            cover_key=cover_key,
        )
        container_id = str(submitted["container_id"])
        _wait_container(container_id, access_token)
        finalized = finalize_media_publish(
            access_token=access_token,
            ig_user_id=ig_user_id,
            container_id=container_id,
            settle_seconds=0.0,
            allow_not_ready=False,
        )
        return {
            "id": finalized.get("id"),
            "code": None,
            "url": finalized.get("url"),
            "cover_applied": submitted.get("cover_applied"),
            "cover_error": submitted.get("cover_error"),
            "caption_sent_len": submitted.get("caption_sent_len"),
            "caption_verified": submitted.get("caption_verified"),
        }
