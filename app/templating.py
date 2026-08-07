"""Instância compartilhada de Jinja2Templates com filtros globais."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.requests import Request

from app.media_access import signed_media_path, signed_media_url
from app.utils.anti_farm import captions_textarea_value, parse_captions_json
from app.utils.automation_videos import playlist_items, video_count as automation_video_count
from app.utils.avatars import user_avatar_url, user_display_name
from app.utils.proxy import account_proxy_ip, proxy_label, proxy_to_raw
from app.utils.intervals import interval_label
from app.utils.calendar_schedule import format_calendar_times_label
from app.utils.formatters import format_count, format_interval, status_badge_class, status_label
from app.utils.timezone import brt_now, format_date_header, greeting_for_user, greeting_period, to_brt


def automation_playlist_names(automation) -> list[str]:
    return [
        (it.get("video_original_name") or it.get("video_key") or "vídeo")
        for it in playlist_items(automation)
    ]


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}


def _media_suffix(*values: object) -> str:
    for value in values:
        if not value:
            continue
        suffix = Path(str(value).split("?", 1)[0]).suffix.lower()
        if suffix:
            return suffix
    return ""


def automation_preview_media(automation) -> dict[str, str] | None:
    thumb_key = getattr(automation, "thumb_key", None)
    if thumb_key:
        return {"url": signed_media_path(thumb_key), "kind": "image"}

    content_type = (getattr(automation, "content_type", None) or "reel").lower()
    if content_type not in ("story", "photo"):
        return None

    media_key = getattr(automation, "video_key", None)
    if not media_key:
        return None
    ext = _media_suffix(media_key, getattr(automation, "video_original_name", None))
    kind = "video" if ext in VIDEO_EXTENSIONS else "image"
    return {"url": signed_media_path(media_key), "kind": kind}


class CompatJinja2Templates(Jinja2Templates):
    """Aceita API antiga (name, context) e nova (request, name, context) do Starlette 1.x."""

    def TemplateResponse(self, *args: Any, **kwargs: Any):
        request: Request | None = kwargs.pop("request", None)
        name: str | None = kwargs.pop("name", None)
        context: dict[str, Any] | None = kwargs.pop("context", None)
        status_code: int = kwargs.pop("status_code", 200)
        headers: Mapping[str, str] | None = kwargs.pop("headers", None)
        media_type: str | None = kwargs.pop("media_type", None)
        background: BackgroundTask | None = kwargs.pop("background", None)

        positional = list(args)
        if positional and isinstance(positional[0], Request):
            request = positional.pop(0)
            if positional and isinstance(positional[0], str):
                name = positional.pop(0)
            if positional and isinstance(positional[0], dict):
                context = positional.pop(0)
            if positional and isinstance(positional[0], int):
                status_code = positional.pop(0)
        elif positional and isinstance(positional[0], str):
            # Estilo antigo: TemplateResponse("tpl.html", {"request": request, ...})
            name = positional.pop(0)
            if positional and isinstance(positional[0], dict):
                context = positional.pop(0)
            if positional and isinstance(positional[0], int):
                status_code = positional.pop(0)

        if context is None:
            context = {}
        if request is None:
            maybe = context.get("request")
            if isinstance(maybe, Request):
                request = maybe
        if request is None or not name:
            raise TypeError(
                "TemplateResponse requer request e nome do template "
                '(use TemplateResponse(request, "x.html", context) '
                'ou o legado TemplateResponse("x.html", {"request": request, ...}))'
            )
        if "request" not in context:
            context = {**context, "request": request}

        _inject_instagrapi_down_notice(context)

        return super().TemplateResponse(
            request,
            name,
            context,
            status_code=status_code,
            headers=headers,
            media_type=media_type,
            background=background,
        )


_NOTICE_UNSET = object()


def _inject_instagrapi_down_notice(context: dict[str, Any]) -> None:
    """Aviso global: contas Instagrapi legadas com sessão/API fora."""
    if "instagrapi_down_notice" in context:
        return
    user = context.get("user")
    request = context.get("request")
    if user is None or not isinstance(request, Request):
        context["instagrapi_down_notice"] = None
        return
    path = (request.url.path or "").rstrip("/") or "/"
    if path in ("/login", "/register") or path.startswith("/static"):
        context["instagrapi_down_notice"] = None
        return
    cached = getattr(request.state, "instagrapi_down_notice", _NOTICE_UNSET)
    if cached is not _NOTICE_UNSET:
        context["instagrapi_down_notice"] = cached
        return
    try:
        from core.database import SessionLocal
        from app.utils.instagrapi_access import sync_instagrapi_down_notice

        db = SessionLocal()
        try:
            notice = sync_instagrapi_down_notice(db, user)
        finally:
            db.close()
    except Exception:
        notice = None
    try:
        request.state.instagrapi_down_notice = notice
    except Exception:
        pass
    context["instagrapi_down_notice"] = notice


templates = CompatJinja2Templates(directory="app/templates")
templates.env.filters["localtime"] = to_brt
templates.env.filters["tojson"] = lambda v: json.dumps(v)
templates.env.filters["signed_media"] = signed_media_url
templates.env.globals["greeting_for_user"] = greeting_for_user
templates.env.globals["greeting_period"] = greeting_period
templates.env.globals["brt_now"] = brt_now
templates.env.globals["format_date_header"] = format_date_header
templates.env.globals["user_avatar_url"] = user_avatar_url
templates.env.globals["user_display_name"] = user_display_name
templates.env.globals["format_interval"] = format_interval
templates.env.globals["format_count"] = format_count
templates.env.globals["status_label"] = status_label
templates.env.globals["status_badge_class"] = status_badge_class
templates.env.globals["automation_video_count"] = automation_video_count
templates.env.globals["automation_playlist_names"] = automation_playlist_names
templates.env.globals["automation_preview_media"] = automation_preview_media
templates.env.globals["signed_media_url"] = signed_media_url
templates.env.globals["proxy_label"] = proxy_label
templates.env.globals["proxy_to_raw"] = proxy_to_raw
templates.env.globals["account_proxy_ip"] = account_proxy_ip
templates.env.globals["interval_label"] = interval_label
templates.env.globals["captions_textarea_value"] = captions_textarea_value
templates.env.globals["parse_captions_list"] = parse_captions_json
templates.env.globals["format_calendar_times"] = format_calendar_times_label
