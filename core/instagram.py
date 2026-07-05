"""Wrapper do instagrapi com sess\u00e3o persistida no banco e proxy obrigat\u00f3rio."""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

import requests
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, TwoFactorRequired
from instagrapi.types import StoryLink

log = logging.getLogger(__name__)

IPIFY_URL = "https://api.ipify.org"
IP_CHECK_TIMEOUT = 8


class InstagramAuthError(RuntimeError):
    pass


class InstagramTwoFactorRequired(InstagramAuthError):
    """Conta exige código 2FA — o cliente deve solicitar ao usuário."""


def _friendly_auth_error(raw: str) -> str:
    low = raw.lower()
    if "467" in raw:
        return (
            "Instagram recusou a sessão (código 467). Use o MESMO proxy do navegador, "
            "gere um Session ID novo (F12 → Application → Cookies → sessionid) "
            "ou conecte com usuário e senha."
        )
    if "login_required" in low or "403" in raw:
        return "Session ID expirado ou inválido. Gere um novo no navegador."
    if "challenge" in low:
        return "Instagram pediu verificação. Abra o app, confirme o login e tente de novo."
    return raw


def _build_client(proxy: Optional[str], settings_dict: Optional[dict]) -> Client:
    if not proxy:
        raise InstagramAuthError("Proxy \u00e9 obrigat\u00f3rio. Nenhuma requisi\u00e7\u00e3o ser\u00e1 feita sem proxy.")
    cl = Client()
    cl.delay_range = [1, 3]
    cl.set_proxy(proxy)
    if settings_dict:
        cl.set_settings(settings_dict)
    return cl


@lru_cache(maxsize=1)
def _server_public_ip() -> str | None:
    """IP p\u00fablico do servidor (sem proxy), cacheado."""
    try:
        resp = requests.get(IPIFY_URL, timeout=IP_CHECK_TIMEOUT)
        resp.raise_for_status()
        return resp.text.strip()
    except Exception as exc:
        log.warning("N\u00e3o foi poss\u00edvel obter IP do servidor: %s", exc)
        return None


def get_public_ip(proxy: str | None = None) -> str | None:
    """Retorna o IP p\u00fablico de sa\u00edda. Com proxy=None usa IP direto do servidor."""
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        resp = requests.get(IPIFY_URL, proxies=proxies, timeout=IP_CHECK_TIMEOUT)
        resp.raise_for_status()
        return resp.text.strip()
    except Exception as exc:
        log.warning("Falha ao obter IP p\u00fablico (proxy=%s): %s", bool(proxy), exc)
        return None


def check_proxy(proxy: str) -> bool:
    """Valida proxy: deve responder e o IP de sa\u00edda N\u00c3O pode ser o do servidor."""
    if not proxy or not proxy.strip():
        return False
    proxy_ip = get_public_ip(proxy)
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
    """Faz login e retorna o dicion\u00e1rio de settings (sess\u00e3o) para persistir.

    Levanta InstagramAuthError em caso de falha.
    """
    cl = _build_client(proxy=proxy, settings_dict=None)
    try:
        if verification_code:
            cl.login(username, password, verification_code=verification_code)
        else:
            cl.login(username, password)
    except TwoFactorRequired as exc:
        raise InstagramTwoFactorRequired(
            "Autenticação de dois fatores necessária. Informe o código do autenticador."
        ) from exc
    except Exception as exc:  # instagrapi tem várias subclasses; tratamos genericamente
        raise InstagramAuthError(_friendly_auth_error(str(exc))) from exc

    return cl.get_settings()


def login_with_sessionid(sessionid: str, proxy: str | None = None) -> tuple[dict, str]:
    """Loga via sessionid do navegador.

    Retorna (settings_dict, username).
    """
    cl = _build_client(proxy=proxy, settings_dict=None)
    try:
        cl.login_by_sessionid(sessionid)
    except Exception as exc:
        raise InstagramAuthError(_friendly_auth_error(str(exc))) from exc

    username = (cl.username or "").strip()
    if not username:
        try:
            username = cl.account_info().username
        except Exception as exc:
            raise InstagramAuthError(_friendly_auth_error(str(exc))) from exc

    return cl.get_settings(), username


def get_ready_client(
    settings_dict: dict,
    proxy: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> Client:
    """Retorna um Client j\u00e1 logado e validado.

    Se a sess\u00e3o expirou e tivermos user/pass, tenta relogar silenciosamente.
    Levanta InstagramAuthError se n\u00e3o conseguir.
    """
    cl = _build_client(proxy=proxy, settings_dict=settings_dict)
    try:
        cl.account_info()
        return cl
    except LoginRequired:
        if username and password:
            try:
                cl.login(username, password)
                return cl
            except Exception as exc:
                raise InstagramAuthError(f"Re-login falhou: {exc}") from exc
        raise InstagramAuthError("Sess\u00e3o expirada e sem credenciais para re-login.")
    except Exception as exc:
        raise InstagramAuthError(str(exc)) from exc


def publish_reel(
    cl: Client,
    video_path: Path,
    caption: str,
    thumbnail_path: Path | None = None,
) -> dict:
    """Publica o reel e retorna informações básicas (id, code, url)."""
    if not video_path.exists():
        raise FileNotFoundError(f"Vídeo não encontrado: {video_path}")

    media = cl.clip_upload(video_path, caption, thumbnail=thumbnail_path)
    url = f"https://www.instagram.com/reel/{media.code}/" if media.code else None
    return {"id": str(media.pk), "code": media.code, "url": url}


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
    return [
        StoryLink(
            webUri=url,
            x=0.5,
            y=0.85,
            z=1,
            width=0.45,
            height=0.08,
            rotation=0.0,
        )
    ]


def publish_story(cl: Client, media_path: Path, link_url: str | None = None) -> dict:
    """Publica story (foto ou vídeo), opcionalmente com link sticker."""
    if not media_path.exists():
        raise FileNotFoundError(f"Mídia não encontrada: {media_path}")
    links = _story_links(link_url)
    ext = media_path.suffix.lower()
    if ext in (".mp4", ".mov", ".webm"):
        media = cl.video_upload_to_story(media_path, links=links)
    else:
        media = cl.photo_upload_to_story(media_path, links=links)
    return {"id": str(media.pk), "code": getattr(media, "code", None), "url": None}


def publish_photo_feed(cl: Client, image_path: Path, caption: str) -> dict:
    """Publica foto no feed do perfil."""
    if not image_path.exists():
        raise FileNotFoundError(f"Imagem não encontrada: {image_path}")
    media = cl.photo_upload(image_path, caption)
    url = f"https://www.instagram.com/p/{media.code}/" if media.code else None
    return {"id": str(media.pk), "code": media.code, "url": url}


def get_account_profile(cl: Client) -> dict:
    """Retorna bio, link e URL da foto de perfil."""
    info = cl.account_info()
    return {
        "username": info.username,
        "full_name": getattr(info, "full_name", "") or "",
        "biography": getattr(info, "biography", "") or "",
        "external_url": getattr(info, "external_url", "") or "",
        "profile_pic_url": getattr(info, "profile_pic_url", "") or "",
    }


def update_account_profile(
    cl: Client,
    biography: str | None = None,
    external_url: str | None = None,
    profile_picture_path: Path | None = None,
) -> dict:
    """Atualiza bio, link e/ou foto de perfil."""
    if biography is not None:
        cl.account_set_biography(biography)
    if external_url is not None:
        url = external_url.strip()
        if url:
            cl.set_external_url(url)
        else:
            try:
                cl.remove_bio_links()
            except Exception:
                pass
    if profile_picture_path is not None:
        if not profile_picture_path.exists():
            raise FileNotFoundError("Foto de perfil não encontrada")
        cl.account_change_picture(profile_picture_path)
    return get_account_profile(cl)


def serialize_settings(settings_dict: dict) -> str:
    return json.dumps(settings_dict, ensure_ascii=False)


def deserialize_settings(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
