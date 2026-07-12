"""Wrapper do instagrapi com sessão persistida no banco e proxy obrigatório."""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Optional

import requests
from instagrapi import Client
from instagrapi.exceptions import (
    BadPassword,
    ChallengeRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
    TwoFactorRequired,
)
from instagrapi.types import StoryLink

from app.utils.proxy import normalize_proxy

log = logging.getLogger(__name__)

IPIFY_URL = "https://api.ipify.org"
IP_CHECK_TIMEOUT = 8


def _stable_uuids(username: str) -> dict[str, str]:
    """Mesmo @ → mesmo device fingerprint (evita 'aparelho novo' a cada tentativa)."""
    seed = hashlib.sha256(f"instablack:{username.lower()}".encode()).hexdigest()

    def _u(n: int) -> str:
        h = hashlib.md5(f"{seed}:{n}".encode()).hexdigest()
        return str(uuid.UUID(h))

    phone = _u(1)
    return {
        "phone_id": phone,
        "uuid": _u(2),
        "client_session_id": _u(3),
        "advertising_id": _u(4),
        "android_device_id": f"android-{seed[:16]}",
        "request_id": _u(5),
        "tray_session_id": _u(6),
    }


def _extract_link_from_tap_models(tap_models_raw) -> dict | None:
    """Pega o link que o instagrapi colocou em tap_models e vira story_link_stickers (estilo INSSIST)."""
    if not tap_models_raw:
        return None
    taps = tap_models_raw
    if isinstance(taps, str):
        try:
            taps = json.loads(taps)
        except json.JSONDecodeError:
            return None
    if not isinstance(taps, list):
        return None
    for item in taps:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not url and isinstance(item.get("story_link"), dict):
            url = item["story_link"].get("url")
        if not url and isinstance(item.get("extra"), dict):
            url = item["extra"].get("url")
        if item.get("type") in ("story_link", "link") or url:
            if not url:
                continue
            return {
                "x": float(item.get("x", 0.5)),
                "y": float(item.get("y", 0.8)),
                "width": float(item.get("width", 0.5)),
                "height": float(item.get("height", 0.14)),
                "rotation": float(item.get("rotation", 0.0)),
                "link_type": item.get("link_type") or "web",
                "url": str(url),
            }
    return None


def _extract_link_from_kwargs(links) -> dict | None:
    """Fallback: monta story_link_stickers a partir de links=[StoryLink(...)] do upload."""
    if not links:
        return None
    link = links[0]
    url = getattr(link, "webUri", None) or getattr(link, "url", None)
    if isinstance(link, dict):
        url = url or link.get("webUri") or link.get("url")
    if not url:
        return None
    return {
        "x": float(getattr(link, "x", 0.5) if not isinstance(link, dict) else link.get("x", 0.5) or 0.5),
        "y": float(getattr(link, "y", 0.8) if not isinstance(link, dict) else link.get("y", 0.8) or 0.8),
        "width": float(
            getattr(link, "width", 0.5) if not isinstance(link, dict) else link.get("width", 0.5) or 0.5
        ),
        "height": float(
            getattr(link, "height", 0.14) if not isinstance(link, dict) else link.get("height", 0.14) or 0.14
        ),
        "rotation": float(
            getattr(link, "rotation", 0.0) if not isinstance(link, dict) else link.get("rotation", 0.0) or 0.0
        ),
        "link_type": "web",
        "url": str(url),
    }


def _apply_story_sticker_ids_fix() -> None:
    """1) Junta story_sticker_ids.
    2) Injeta story_link_stickers (mesmo campo do INSSIST) para sticker nativo.
    """
    targets: list[tuple[object, str]] = []
    try:
        from instagrapi.mixins.photo import UploadPhotoMixin

        targets.append((UploadPhotoMixin, "photo_configure_to_story"))
    except Exception:
        log.warning("UploadPhotoMixin indisponível — patch de story sticker não aplicado (foto)")
    try:
        from instagrapi.mixins.video import UploadVideoMixin

        targets.append((UploadVideoMixin, "video_configure_to_story"))
    except Exception:
        log.warning("UploadVideoMixin indisponível — patch de story sticker não aplicado (vídeo)")

    def _wrap(original):
        def wrapper(self, *args, **kwargs):
            real_pr = self.private_request

            def patched_pr(endpoint, data=None, *a, **kw):
                ep = str(endpoint or "")
                if isinstance(data, dict) and (
                    "story_sticker_ids" in data
                    or "tap_models" in data
                    or "configure_to_story" in ep
                ):
                    data = dict(data)

                    # --- A) story_sticker_ids completo ---
                    raw_ids = data.get("story_sticker_ids")
                    ids: list[str] = []
                    if isinstance(raw_ids, (list, tuple)):
                        ids = [str(x) for x in raw_ids if x]
                    elif isinstance(raw_ids, str) and raw_ids.strip():
                        ids = [p.strip() for p in raw_ids.split(",") if p.strip()]

                    def _ensure(name: str) -> None:
                        if name not in ids:
                            ids.append(name)

                    if data.get("story_hashtags"):
                        _ensure("hashtag_sticker")
                    if data.get("reel_mentions"):
                        _ensure("mention_sticker")
                    if data.get("story_polls"):
                        _ensure("polling_sticker_v2")
                    if data.get("story_sliders"):
                        _ensure("slider_sticker")
                    if data.get("story_questions"):
                        _ensure("question_sticker")
                    if data.get("story_quizs"):
                        _ensure("quiz_sticker")
                    if data.get("story_countdowns"):
                        _ensure("countdown_sticker")

                    # --- B) story_link_stickers nativo (INSSIST) ---
                    link_payload = None
                    existing = data.get("story_link_stickers")
                    if existing:
                        # Já veio no formato certo — só garante o id do sticker
                        _ensure("link_sticker_default")
                        log.info("story_link_stickers já presente no payload")
                    else:
                        link_payload = _extract_link_from_tap_models(data.get("tap_models"))
                        if not link_payload:
                            link_payload = _extract_link_from_kwargs(kwargs.get("links"))

                    if link_payload:
                        data["story_link_stickers"] = json.dumps([link_payload])
                        _ensure("link_sticker_default")
                        log.info(
                            "story_link_stickers nativo injetado: %s",
                            link_payload.get("url"),
                        )
                    elif (
                        data.get("story_cta")
                        or data.get("story_link")
                        or data.get("link_text")
                        or any("link_sticker" in x for x in ids)
                        or kwargs.get("links")
                    ):
                        _ensure("link_sticker_default")
                    else:
                        taps = data.get("tap_models")
                        if isinstance(taps, str) and "story_link" in taps:
                            _ensure("link_sticker_default")

                    if ids:
                        data["story_sticker_ids"] = ",".join(ids)
                        log.info(
                            "story configure sticker_ids=%s endpoint=%s",
                            data["story_sticker_ids"],
                            endpoint,
                        )

                return real_pr(endpoint, data, *a, **kw)

            self.private_request = patched_pr
            try:
                return original(self, *args, **kwargs)
            finally:
                self.private_request = real_pr

        return wrapper

    for cls, method_name in targets:
        method = getattr(cls, method_name, None)
        if method is None or getattr(method, "_ib_story_link_native", False):
            continue
        wrapped = _wrap(method)
        wrapped._ib_story_link_native = True  # type: ignore[attr-defined]
        setattr(cls, method_name, wrapped)
        log.info("Patch story sticker + link nativo: %s.%s", cls.__name__, method_name)


_apply_story_sticker_ids_fix()


class InstagramAuthError(RuntimeError):
    pass


class InstagramTwoFactorRequired(InstagramAuthError):
    """Conta exige código 2FA — o cliente deve solicitar ao usuário."""


def _friendly_auth_error(raw: str, proxy: str | None = None) -> str:
    low = raw.lower()
    if "please wait" in low or "few minutes" in low:
        msg = "Instagram pediu para aguardar alguns minutos (muitas tentativas). Espere e tente de novo."
    elif "blacklist" in low or ("ip" in low and "block" in low):
        msg = (
            "Instagram bloqueou este IP para login por senha. "
            "Troque a proxy (IP limpo) ou use Session ID do Multilogin."
        )
    elif "password" in low and "incorrect" in low:
        # Instagram devolve "senha incorreta" também quando desconfia do login (API + proxy)
        msg = (
            "Instagram recusou o login por senha (pode ser senha errada OU bloqueio de confiança). "
            "Mesmo com proxy residencial, login via API costuma falhar. "
            "Solução mais estável: Session ID do Multilogin com a mesma proxy."
        )
    elif "challenge" in low or "checkpoint" in low:
        msg = (
            "Instagram pediu verificação (challenge). Abra a conta no app/navegador "
            "com a mesma proxy, confirme, e use Session ID."
        )
    elif "login_required" in low or "467" in raw:
        msg = "Sessão expirada ou recusada. Cole um sessionid novo do navegador (Multilogin)."
    elif "403" in raw:
        msg = "Sessão recusada pelo Instagram. Gere um sessionid novo."
    elif "two" in low and "factor" in low:
        msg = "Conta com 2FA. Informe o código do autenticador no popup."
    elif "redirect" in low and "exceeded" in low:
        msg = "Proxy inválido ou instável. Tente socks5h:// ou revise host:porta:user:senha."
    else:
        msg = raw

    if proxy:
        proxy_ip = get_public_ip(proxy)
        if proxy_ip:
            msg = f"{msg} (IP da proxy: {proxy_ip})"
    return msg


def _build_client(
    proxy: Optional[str],
    settings_dict: Optional[dict],
    *,
    username_for_device: str | None = None,
) -> Client:
    if not proxy:
        raise InstagramAuthError("Proxy é obrigatório. Nenhuma requisição será feita sem proxy.")
    cl = Client()
    cl.delay_range = [1, 3]
    normalized = normalize_proxy(proxy)
    try:
        cl.set_proxy(normalized)
    except Exception as exc:
        raise InstagramAuthError(f"Proxy inválido: {exc}") from exc
    if settings_dict:
        cl.set_settings(settings_dict)
    elif username_for_device:
        try:
            cl.set_uuids(_stable_uuids(username_for_device))
        except Exception:
            log.debug("Não foi possível fixar UUIDs do device", exc_info=True)
    # Locale BR reduz challenge em contas brasileiras
    try:
        cl.set_locale("pt_BR")
        cl.set_timezone_offset(-3 * 60 * 60)
        cl.set_country("BR")
        cl.set_country_code(55)
    except Exception:
        pass
    return cl


def _after_login(cl: Client) -> None:
    if hasattr(cl, "inject_sessionid_to_public"):
        try:
            cl.inject_sessionid_to_public()
        except Exception:
            pass


@lru_cache(maxsize=1)
def _server_public_ip() -> str | None:
    try:
        resp = requests.get(IPIFY_URL, timeout=IP_CHECK_TIMEOUT)
        resp.raise_for_status()
        return resp.text.strip()
    except Exception as exc:
        log.warning("Não foi possível obter IP do servidor: %s", exc)
        return None


def get_public_ip(proxy: str | None = None) -> str | None:
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        resp = requests.get(IPIFY_URL, proxies=proxies, timeout=IP_CHECK_TIMEOUT)
        resp.raise_for_status()
        return resp.text.strip()
    except Exception as exc:
        log.warning("Falha ao obter IP público (proxy=%s): %s", bool(proxy), exc)
        return None


def check_proxy(proxy: str) -> bool:
    if not proxy or not proxy.strip():
        return False
    normalized = normalize_proxy(proxy)
    proxy_ip = get_public_ip(normalized)
    if not proxy_ip:
        return False
    server_ip = _server_public_ip()
    if server_ip and proxy_ip == server_ip:
        log.error("Proxy vazou IP do servidor (%s). Bloqueando.", server_ip)
        return False
    return True


def login_with_credentials(
    username: str,
    password: str,
    verification_code: str | None = None,
    proxy: str | None = None,
) -> dict:
    username = (username or "").strip().lstrip("@")
    password = (password or "").strip()
    if not username or not password:
        raise InstagramAuthError("Usuário e senha são obrigatórios.")

    # Garante que a proxy está saindo com IP próprio (não vazando Railway)
    if proxy and not check_proxy(proxy):
        raise InstagramAuthError(
            "Proxy fora do ar ou vazando IP do servidor. "
            "Teste o proxy antes — formato: ip:porta:usuario:senha"
        )

    cl = _build_client(proxy=proxy, settings_dict=None, username_for_device=username)
    try:
        if verification_code:
            cl.login(username, password, verification_code=verification_code.strip())
        else:
            cl.login(username, password)
    except TwoFactorRequired as exc:
        raise InstagramTwoFactorRequired(
            "Autenticação de dois fatores necessária. Informe o código do autenticador."
        ) from exc
    except PleaseWaitFewMinutes as exc:
        raise InstagramAuthError(_friendly_auth_error(str(exc), proxy=proxy)) from exc
    except ChallengeRequired as exc:
        raise InstagramAuthError(_friendly_auth_error(str(exc), proxy=proxy)) from exc
    except BadPassword as exc:
        log.warning("BadPassword no login @%s via proxy (raw=%s)", username, exc)
        raise InstagramAuthError(_friendly_auth_error(str(exc), proxy=proxy)) from exc
    except Exception as exc:
        low = str(exc).lower()
        log.warning("Falha login @%s: %s", username, exc)
        if "two_factor" in low or "two-factor" in low:
            raise InstagramTwoFactorRequired(
                "Autenticação de dois fatores necessária. Informe o código do autenticador."
            ) from exc
        raise InstagramAuthError(_friendly_auth_error(str(exc), proxy=proxy)) from exc

    _after_login(cl)
    try:
        cl.account_info()
    except Exception as exc:
        raise InstagramAuthError(_friendly_auth_error(str(exc), proxy=proxy)) from exc
    return cl.get_settings()


def login_with_sessionid(
    sessionid: str,
    proxy: str | None = None,
    username_hint: str | None = None,
) -> tuple[dict, str]:
    """Login via sessionid do navegador (fluxo PostagemIG)."""
    cl = _build_client(proxy=proxy, settings_dict=None)
    try:
        cl.login_by_sessionid(sessionid.strip())
    except Exception as exc:
        raise InstagramAuthError(_friendly_auth_error(str(exc), proxy=proxy)) from exc

    _after_login(cl)
    try:
        info = cl.account_info()
        username = info.username
    except Exception as exc:
        username = (cl.username or (username_hint or "")).strip().lstrip("@")
        if not username:
            raise InstagramAuthError(_friendly_auth_error(str(exc), proxy=proxy)) from exc

    return cl.get_settings(), username


def login_with_imported_settings(
    settings_dict: dict,
    proxy: str,
    username: str,
    password: str | None = None,
) -> dict:
    username = username.strip().lstrip("@")
    cl = _build_client(proxy=proxy, settings_dict=settings_dict)
    try:
        cl.account_info()
        _after_login(cl)
        return cl.get_settings()
    except LoginRequired:
        if not password:
            raise InstagramAuthError(
                "Sessão importada expirada. Informe a senha para renovar (load_settings + login)."
            )
        try:
            cl.login(username, password)
        except TwoFactorRequired as exc:
            raise InstagramTwoFactorRequired(
                "Autenticação de dois fatores necessária. Informe o código do autenticador."
            ) from exc
        except Exception as exc:
            raise InstagramAuthError(_friendly_auth_error(str(exc), proxy=proxy)) from exc
        _after_login(cl)
        return cl.get_settings()
    except Exception as exc:
        raise InstagramAuthError(_friendly_auth_error(str(exc), proxy=proxy)) from exc


def get_ready_client(
    settings_dict: dict,
    proxy: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> Client:
    cl = _build_client(proxy=proxy, settings_dict=settings_dict)
    _after_login(cl)
    try:
        cl.account_info()
        return cl
    except LoginRequired:
        if username and password:
            try:
                cl.login(username, password)
                _after_login(cl)
                return cl
            except TwoFactorRequired as exc:
                raise InstagramTwoFactorRequired(
                    "Autenticação de dois fatores necessária."
                ) from exc
            except Exception as exc:
                raise InstagramAuthError(f"Re-login falhou: {exc}") from exc
        raise InstagramAuthError(
            "Sessão expirada. Reconecte com sessionid novo ou usuário/senha."
        )
    except Exception as exc:
        raise InstagramAuthError(str(exc)) from exc


def publish_reel(
    cl: Client,
    video_path: Path,
    caption: str,
    thumbnail_path: Path | None = None,
) -> dict:
    if not video_path.exists():
        raise FileNotFoundError(f"Vídeo não encontrado: {video_path}")

    media = cl.clip_upload(video_path, caption, thumbnail=thumbnail_path)
    url = f"https://www.instagram.com/reel/{media.code}/" if media.code else None
    return {"id": str(media.pk), "code": media.code, "url": url}


def fetch_media_stats(cl: Client, media_pk: str) -> dict:
    """Busca visualizações e curtidas de um reel/post."""
    pk = int(str(media_pk).strip())
    media = cl.media_info(pk)
    play_count = getattr(media, "play_count", None)
    if play_count is None:
        play_count = getattr(media, "view_count", None)
    if play_count is None:
        play_count = getattr(media, "ig_play_count", None)
    # Fallback: payload bruto do Instagram
    if play_count is None:
        try:
            raw = cl.private_request(f"media/{pk}/info/")
            items = (raw or {}).get("items") or []
            if items:
                item = items[0]
                play_count = (
                    item.get("play_count")
                    or item.get("view_count")
                    or item.get("ig_play_count")
                    or (item.get("metrics") or {}).get("play_count")
                )
        except Exception:
            log.debug("media info raw falhou pk=%s", pk, exc_info=True)
    like_count = getattr(media, "like_count", None)
    try:
        play_count = int(play_count) if play_count is not None else None
    except (TypeError, ValueError):
        play_count = None
    try:
        like_count = int(like_count) if like_count is not None else None
    except (TypeError, ValueError):
        like_count = None
    return {
        "play_count": play_count if isinstance(play_count, int) and play_count >= 0 else None,
        "like_count": like_count if isinstance(like_count, int) and like_count >= 0 else None,
    }


def _normalize_url(url: str) -> str:
    u = url.strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = f"https://{u}"
    return u


def _story_links(link_url: str | None) -> list[StoryLink]:
    url = _normalize_url(link_url or "")
    if not url:
        return []
    # Posição padrão do sticker de link (centro-inferior) — alinhada ao instagrapi
    return [
        StoryLink(
            webUri=url,
            x=0.5,
            y=0.8,
            z=0.0,
            width=0.5,
            height=0.14,
            rotation=0.0,
        )
    ]


def publish_story(cl: Client, media_path: Path, link_url: str | None = None) -> dict:
    if not media_path.exists():
        raise FileNotFoundError(f"Mídia não encontrada: {media_path}")
    links = _story_links(link_url)
    if links:
        log.info("Publicando story COM link sticker: %s", links[0].webUri)
    elif link_url:
        log.warning("story_link inválido ignorado: %r", link_url)
    else:
        log.info("Publicando story SEM link sticker")

    # Mantém links=[StoryLink] (instagrapi valida URL + monta tap_models).
    # O patch _apply_story_sticker_ids_fix injeta story_link_stickers (formato INSSIST)
    # no configure_to_story para o visual nativo “Acessar link → domínio”.
    # Conta ainda precisa ser elegível (pro/creator). Texto do botão = locale do IG.
    ext = media_path.suffix.lower()
    kwargs: dict = {"links": links}
    if ext in (".mp4", ".mov", ".webm"):
        media = cl.video_upload_to_story(media_path, **kwargs)
    else:
        media = cl.photo_upload_to_story(media_path, **kwargs)
    return {"id": str(media.pk), "code": getattr(media, "code", None), "url": None}


def publish_photo_feed(cl: Client, image_path: Path, caption: str) -> dict:
    if not image_path.exists():
        raise FileNotFoundError(f"Imagem não encontrada: {image_path}")
    media = cl.photo_upload(image_path, caption)
    url = f"https://www.instagram.com/p/{media.code}/" if media.code else None
    return {"id": str(media.pk), "code": media.code, "url": url}


def serialize_settings(settings_dict: dict) -> str:
    return json.dumps(settings_dict, ensure_ascii=False)


def deserialize_settings(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
