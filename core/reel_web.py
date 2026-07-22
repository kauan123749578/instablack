"""Publicação de Reels com capa via API web (fluxo Opalite / INSSIST).

Mesma sessão de cookies do Story link:
  1. rupload_igvideo (vídeo, is_clips_video)
  2. rupload_igphoto (capa, media_type=2, mesmo upload_id)
  3. configure_to_clips
"""
from __future__ import annotations

import json
import logging
import mimetypes
import time
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageOps

from core.story_web import (
    IG_APP_ID,
    build_web_session,
    _format_ig_error,
    _warmup_web_session,
)

log = logging.getLogger(__name__)

UPLOAD_VIDEO_URL = "https://i.instagram.com/rupload_igvideo/{entity_name}"
UPLOAD_PHOTO_URL = "https://i.instagram.com/rupload_igphoto/{entity_name}"
CONFIGURE_REEL_URL = "https://www.instagram.com/api/v1/media/configure_to_clips/"


def prepare_reel_cover(source: Path, output: Path) -> tuple[int, int]:
    """Normaliza capa para JPEG (orientação EXIF, sem metadados extras)."""
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="JPEG", quality=95, subsampling=0)
    return image.size


def _upload_id() -> str:
    return str(int(time.time() * 1000))


def _entity_name(upload_id: str) -> str:
    return f"fb_uploader_{upload_id}"


def upload_reel_video_web(
    session: requests.Session,
    video_path: Path,
    *,
    entity_name: str,
    upload_id: str,
) -> None:
    video_bytes = video_path.read_bytes()
    upload_params = {
        "client-passthrough": "1",
        "is_sidecar": "0",
        "media_type": 2,
        "upload_id": upload_id,
        "for_album": False,
        "is_clips_video": "1",
    }
    content_type = mimetypes.guess_type(video_path.name)[0] or "video/mp4"
    headers = {
        "accept": "*/*",
        "offset": "0",
        "x-entity-name": entity_name,
        "x-entity-length": str(len(video_bytes)),
        "x-ig-app-id": IG_APP_ID,
        "x-instagram-rupload-params": json.dumps(upload_params, separators=(",", ":")),
        "content-type": content_type,
    }
    log.info("Reel web: enviando vídeo upload_id=%s", upload_id)
    response = session.post(
        UPLOAD_VIDEO_URL.format(entity_name=entity_name),
        headers=headers,
        data=video_bytes,
        timeout=300,
    )
    if response.status_code != 200:
        raise RuntimeError(_format_ig_error(response))
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if payload.get("status") == "fail":
        raise RuntimeError(f"Upload do vídeo rejeitado: {payload}")


def upload_reel_cover_web(
    session: requests.Session,
    cover_path: Path,
    *,
    entity_name: str,
    upload_id: str,
    width: int,
    height: int,
) -> None:
    cover_bytes = cover_path.read_bytes()
    upload_params = {
        "upload_id": upload_id,
        "media_type": 2,
        "upload_media_width": width,
        "upload_media_height": height,
    }
    headers = {
        "accept": "*/*",
        "offset": "0",
        "x-entity-name": entity_name,
        "x-entity-length": str(len(cover_bytes)),
        "x-ig-app-id": IG_APP_ID,
        "x-instagram-rupload-params": json.dumps(upload_params, separators=(",", ":")),
        "content-type": "image/jpeg",
    }
    log.info("Reel web: enviando capa (mesmo upload_id=%s)", upload_id)
    last_error: Exception | None = None
    for attempt in range(1, 3):
        try:
            response = session.post(
                UPLOAD_PHOTO_URL.format(entity_name=entity_name),
                headers=headers,
                data=cover_bytes,
                timeout=120,
            )
            if response.status_code != 200:
                raise RuntimeError(_format_ig_error(response))
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if payload.get("status") == "fail":
                raise RuntimeError(f"Upload da capa rejeitado: {payload}")
            return
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2)
    raise RuntimeError(f"Upload da capa falhou: {last_error}")


def configure_reel_web(
    session: requests.Session,
    *,
    upload_id: str,
    caption: str,
    share_to_feed: bool = True,
) -> dict:
    data = {
        "is_unified_video": "1",
        "disable_oa_reuse": "false",
        "video_subtitles_enabled": "0",
        "clips_share_preview_to_feed": "1" if share_to_feed else "0",
        "upload_id": upload_id,
        "caption": caption or "",
        "source_type": "library",
        "disable_comments": "0",
        "like_and_view_counts_disabled": "0",
    }
    csrf = (
        session.cookies.get("csrftoken", domain=".instagram.com")
        or session.cookies.get("csrftoken")
        or session.headers.get("x-csrftoken")
        or ""
    )
    headers = {
        "accept": "*/*",
        "content-type": "application/x-www-form-urlencoded",
        "x-csrftoken": csrf,
        "x-ig-app-id": IG_APP_ID,
    }
    log.info("Reel web: configure_to_clips upload_id=%s", upload_id)
    normal_failures = 0
    transcode_polls = 0
    response: requests.Response | None = None

    while normal_failures < 5:
        try:
            response = session.post(
                CONFIGURE_REEL_URL,
                headers=headers,
                data=data,
                timeout=90,
            )
        except requests.RequestException as exc:
            normal_failures += 1
            if normal_failures >= 5:
                raise RuntimeError(f"Configure Reel falhou: {exc}") from exc
            log.warning("Configure Reel rede falhou; retry %s/5", normal_failures)
            time.sleep(3)
            continue

        body = response.text or ""
        if "Transcode not finished" in body:
            transcode_polls += 1
            if transcode_polls >= 120:
                raise TimeoutError("Transcode do Reel não terminou após 10 minutos")
            log.info("Reel web: transcode em andamento (%s)", transcode_polls)
            time.sleep(5)
            continue

        if response.status_code == 200:
            break

        normal_failures += 1
        if normal_failures >= 5:
            raise RuntimeError(_format_ig_error(response))
        log.warning(
            "Configure Reel HTTP %s; retry %s/5",
            response.status_code,
            normal_failures,
        )
        time.sleep(3)

    if response is None:
        raise RuntimeError("Configure Reel não retornou resposta")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Configure Reel resposta inválida: {response.text[:500]}"
        ) from exc
    if payload.get("status") == "fail":
        raise RuntimeError(f"Configure Reel rejeitado: {payload}")
    return payload


def publish_reel_web(
    cl: Any,
    video_path: Path,
    *,
    caption: str = "",
    cover_path: Path | None = None,
    web_cookies: dict[str, str] | None = None,
    share_to_feed: bool = True,
    work_dir: Path | None = None,
) -> dict:
    """Publica Reel via API web com capa customizada (Opalite/INSSIST)."""
    if not video_path.exists():
        raise FileNotFoundError(f"Vídeo não encontrado: {video_path}")

    session = build_web_session(cl, web_cookies)
    _warmup_web_session(session)

    upload_id = _upload_id()
    entity_name = _entity_name(upload_id)
    prepared_cover: Path | None = None

    try:
        upload_reel_video_web(
            session,
            video_path,
            entity_name=entity_name,
            upload_id=upload_id,
        )

        if cover_path and cover_path.exists():
            base = work_dir or video_path.parent
            prepared_cover = base / f"reel-cover-{upload_id}.jpg"
            width, height = prepare_reel_cover(cover_path, prepared_cover)
            upload_reel_cover_web(
                session,
                prepared_cover,
                entity_name=entity_name,
                upload_id=upload_id,
                width=width,
                height=height,
            )
        else:
            log.warning(
                "Reel web sem capa customizada (upload_id=%s); Instagram usará frame do vídeo",
                upload_id,
            )

        result = configure_reel_web(
            session,
            upload_id=upload_id,
            caption=caption,
            share_to_feed=share_to_feed,
        )
    finally:
        if prepared_cover:
            try:
                prepared_cover.unlink(missing_ok=True)
            except OSError:
                pass

    media = result.get("media") or {}
    media_id = media.get("pk") or media.get("id") or upload_id
    code = media.get("code")
    url = f"https://www.instagram.com/reel/{code}/" if code else None
    log.info(
        "Reel web publicado: upload_id=%s media_id=%s code=%s cover=%s",
        upload_id,
        media_id,
        code,
        bool(cover_path),
    )
    return {
        "id": str(media_id),
        "code": code,
        "url": url,
        "provider": "web_reel",
    }
