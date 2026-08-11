"""Wrapper sync da aiograpi (async) para o painel / Celery.

Mesmo papel do `core.instagram` (instagrapi), mas com `asyncio.run`.
Provider no banco: `aiograpi`. Sessão em `session_json` (dump_settings).
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

from app.utils.proxy import normalize_proxy
from core.instagram import (
    InstagramAuthError,
    InstagramTwoFactorRequired,
    check_proxy,
)

log = logging.getLogger(__name__)


def _run(coro):
    """Roda coroutine em worker sync (Celery prefork / FastAPI sync route)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # Já existe loop (raro no worker): isola em thread.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def _build_client(
    proxy: Optional[str],
    settings_dict: Optional[dict] = None,
    username: Optional[str] = None,
):
    from aiograpi import Client

    from core.instagram import _stable_uuids

    if not proxy:
        raise InstagramAuthError("Proxy é obrigatório para a API async.")
    cl = Client()
    cl.delay_range = [1, 3]
    normalized = normalize_proxy(proxy)
    try:
        cl.set_proxy(normalized)
    except Exception as exc:
        raise InstagramAuthError(f"Proxy inválido: {exc}") from exc
    if settings_dict:
        try:
            cl.set_settings(settings_dict)
        except Exception as exc:
            log.warning("aiograpi set_settings falhou: %s", exc)
    elif username:
        # Mesmo fingerprint estável do instagrapi — evita “aparelho novo” a cada login.
        try:
            uuids = _stable_uuids(username.lstrip("@").strip())
            if hasattr(cl, "set_uuids"):
                cl.set_uuids(uuids)
            else:
                settings = cl.get_settings() if hasattr(cl, "get_settings") else {}
                settings = dict(settings or {})
                settings["uuids"] = uuids
                cl.set_settings(settings)
        except Exception as exc:
            log.warning("aiograpi set_uuids estável falhou @%s: %s", username, exc)
    return cl


def _map_login_error(exc: Exception, *, proxy: str | None = None) -> Exception:
    low = str(exc).lower()
    name = type(exc).__name__.lower()
    if "twofactor" in name or "two_factor" in low or "two-factor" in low:
        return InstagramTwoFactorRequired(
            "Autenticação de dois fatores necessária. Informe o código do autenticador."
        )
    msg = str(exc) or repr(exc)
    if proxy:
        msg = f"{msg} (proxy ativa)"
    return InstagramAuthError(msg)


async def _login_async(
    username: str,
    password: str,
    *,
    proxy: str,
    verification_code: str | None = None,
    settings_dict: dict | None = None,
) -> dict:
    cl = await _build_client(proxy, settings_dict, username=username)
    try:
        if verification_code:
            await cl.login(username, password, verification_code=verification_code.strip())
        else:
            await cl.login(username, password)
        try:
            await cl.account_info()
        except Exception:
            pass
        return cl.get_settings()
    except Exception as exc:
        raise _map_login_error(exc, proxy=proxy) from exc


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
    if not proxy or not str(proxy).strip():
        raise InstagramAuthError("Proxy é obrigatório.")
    if not check_proxy(proxy):
        raise InstagramAuthError(
            "Proxy vazando IP do servidor. Teste o proxy antes — formato: ip:porta:usuario:senha"
        )
    return _run(
        _login_async(
            username,
            password,
            proxy=proxy,
            verification_code=verification_code,
        )
    )


async def _ready_async(
    settings_dict: dict,
    proxy: str,
    username: str | None = None,
    password: str | None = None,
) -> dict:
    cl = await _build_client(proxy, settings_dict, username=username)
    try:
        await cl.account_info()
        return cl.get_settings()
    except Exception:
        if username and password:
            return await _login_async(
                username,
                password,
                proxy=proxy,
                settings_dict=settings_dict,
            )
        raise InstagramAuthError(
            "Sessão aiograpi expirada. Reconecte com usuário e senha."
        )


def get_ready_settings(
    settings_dict: dict,
    proxy: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> dict:
    if not proxy:
        raise InstagramAuthError("Proxy é obrigatório.")
    return _run(
        _ready_async(
            settings_dict,
            proxy,
            username=username,
            password=password,
        )
    )


def try_refresh_session(
    *,
    settings_dict: dict | None,
    proxy: str,
    username: str,
    password: str | None = None,
    verification_code: str | None = None,
) -> dict:
    username = (username or "").strip().lstrip("@")
    if settings_dict:
        try:
            return get_ready_settings(
                settings_dict,
                proxy=proxy,
                username=username,
                password=password,
            )
        except (InstagramAuthError, InstagramTwoFactorRequired):
            if not password:
                raise
    if not password:
        raise InstagramAuthError("Sem sessão aiograpi e sem senha para renovar.")
    return login_with_credentials(
        username,
        password,
        verification_code=verification_code,
        proxy=proxy,
    )


def _media_result(media: Any) -> dict:
    pk = getattr(media, "pk", None) or getattr(media, "id", None)
    code = getattr(media, "code", None)
    url = None
    if code:
        url = f"https://www.instagram.com/p/{code}/"
    return {
        "id": str(pk) if pk is not None else None,
        "code": code,
        "url": url,
        "provider": "aiograpi",
    }


async def _publish_reel_async(
    settings_dict: dict,
    proxy: str,
    video_path: Path,
    caption: str,
    thumbnail_path: Path | None = None,
) -> dict:
    cl = await _build_client(proxy, settings_dict)
    kwargs: dict = {}
    if thumbnail_path is not None:
        kwargs["thumbnail"] = thumbnail_path
    media = await cl.clip_upload(video_path, caption or "", **kwargs)
    return _media_result(media)


async def _publish_photo_async(
    settings_dict: dict,
    proxy: str,
    image_path: Path,
    caption: str,
) -> dict:
    cl = await _build_client(proxy, settings_dict)
    media = await cl.photo_upload(image_path, caption or "")
    return _media_result(media)


async def _publish_story_async(
    settings_dict: dict,
    proxy: str,
    media_path: Path,
    link_url: str | None = None,
    thumbnail_path: Path | None = None,
) -> dict:
    from aiograpi.types import StoryLink

    cl = await _build_client(proxy, settings_dict)
    ext = media_path.suffix.lower()
    is_video = ext in (".mp4", ".mov", ".webm")
    kwargs: dict = {}
    url = (link_url or "").strip()
    if url:
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        kwargs["links"] = [
            StoryLink(webUri=url, x=0.5, y=0.8, z=0, width=0.5, height=0.14, rotation=0.0)
        ]
    if is_video:
        if thumbnail_path is not None:
            kwargs["thumbnail"] = thumbnail_path
        media = await cl.video_upload_to_story(media_path, **kwargs)
    else:
        media = await cl.photo_upload_to_story(media_path, **kwargs)
    return _media_result(media)


def publish_reel(
    settings_dict: dict,
    proxy: str,
    video_path: Path,
    caption: str,
    thumbnail_path: Path | None = None,
) -> dict:
    return _run(
        _publish_reel_async(
            settings_dict, proxy, video_path, caption, thumbnail_path=thumbnail_path
        )
    )


def publish_photo(
    settings_dict: dict,
    proxy: str,
    image_path: Path,
    caption: str,
) -> dict:
    return _run(_publish_photo_async(settings_dict, proxy, image_path, caption))


def publish_story(
    settings_dict: dict,
    proxy: str,
    media_path: Path,
    link_url: str | None = None,
    thumbnail_path: Path | None = None,
) -> dict:
    return _run(
        _publish_story_async(
            settings_dict,
            proxy,
            media_path,
            link_url=link_url,
            thumbnail_path=thumbnail_path,
        )
    )


async def _set_biography_async(settings_dict: dict, proxy: str, biography: str) -> dict:
    cl = await _build_client(proxy, settings_dict)
    await cl.account_set_biography(biography or "")
    return cl.get_settings()


async def _change_picture_async(
    settings_dict: dict, proxy: str, image_path: Path
) -> dict:
    cl = await _build_client(proxy, settings_dict)
    await cl.account_change_picture(image_path)
    return cl.get_settings()


def set_biography(settings_dict: dict, proxy: str, biography: str) -> dict:
    return _run(_set_biography_async(settings_dict, proxy, biography))


def change_profile_picture(
    settings_dict: dict, proxy: str, image_path: Path
) -> dict:
    if not image_path.exists():
        raise FileNotFoundError(f"Imagem não encontrada: {image_path}")
    return _run(_change_picture_async(settings_dict, proxy, image_path))
