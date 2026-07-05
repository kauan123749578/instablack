"""Wrapper do instagrapi com sessão persistida no banco e proxy obrigatório."""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

import requests
from instagrapi import Client
from instagrapi.exceptions import BadPassword, LoginRequired, TwoFactorRequired
from instagrapi.types import StoryLink

from app.utils.proxy import normalize_proxy

log = logging.getLogger(__name__)

IPIFY_URL = "https://api.ipify.org"
IP_CHECK_TIMEOUT = 8


class InstagramAuthError(RuntimeError):
    pass


class InstagramTwoFactorRequired(InstagramAuthError):
    """Conta exige código 2FA — o cliente deve solicitar ao usuário."""


def _friendly_auth_error(raw: str, proxy: str | None = None) -> str:
    low = raw.lower()
    if "blacklist" in low or ("password" in low and "incorrect" in low):
        msg = (
            "Instagram bloqueou login por senha neste IP (comum no Railway). "
            "Tente Session ID do Multilogin — mesma proxy — como no PostagemIG local."
        )
    elif "login_required" in low or "467" in raw:
        msg = "Sessão expirada ou recusada. Cole um sessionid novo do navegador (Multilogin)."
    elif "403" in raw:
        msg = "Sessão recusada pelo Instagram. Gere um sessionid novo."
    elif "challenge" in low:
        msg = "Instagram pediu verificação. Confirme no app e tente de novo."
    elif "redirect" in low and "exceeded" in low:
        msg = "Proxy inválido ou instável. Tente socks5:// ou revise host:porta:user:senha."
    else:
        msg = raw

    if proxy:
        proxy_ip = get_public_ip(proxy)
        if proxy_ip:
            msg = f"{msg} (IP da proxy: {proxy_ip})"
    return msg


def _build_client(proxy: Optional[str], settings_dict: Optional[dict]) -> Client:
    if not proxy:
        raise InstagramAuthError("Proxy é obrigatório. Nenhuma requisição será feita sem proxy.")
    cl = Client()
    cl.delay_range = [2, 5]
    if settings_dict:
        cl.set_settings(settings_dict)
    normalized = normalize_proxy(proxy)
    try:
        cl.set_proxy(normalized)
    except Exception as exc:
        raise InstagramAuthError(f"Proxy inválido: {exc}") from exc
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
    except BadPassword as exc:
        raise InstagramAuthError(_friendly_auth_error(str(exc), proxy=proxy)) from exc
    except Exception as exc:
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
    if not image_path.exists():
        raise FileNotFoundError(f"Imagem não encontrada: {image_path}")
    media = cl.photo_upload(image_path, caption)
    url = f"https://www.instagram.com/p/{media.code}/" if media.code else None
    return {"id": str(media.pk), "code": media.code, "url": url}


def get_account_profile(cl: Client) -> dict:
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
