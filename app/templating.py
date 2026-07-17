"""Instância compartilhada de Jinja2Templates com filtros globais."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.templating import Jinja2Templates

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
        return {"url": f"/media/{thumb_key}", "kind": "image"}

    content_type = (getattr(automation, "content_type", None) or "reel").lower()
    if content_type not in ("story", "photo"):
        return None

    media_key = getattr(automation, "video_key", None)
    if not media_key:
        return None
    ext = _media_suffix(media_key, getattr(automation, "video_original_name", None))
    kind = "video" if ext in VIDEO_EXTENSIONS else "image"
    return {"url": f"/media/{media_key}", "kind": kind}


templates = Jinja2Templates(directory="app/templates")
templates.env.filters["localtime"] = to_brt
templates.env.filters["tojson"] = lambda v: json.dumps(v)
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
templates.env.globals["proxy_label"] = proxy_label
templates.env.globals["proxy_to_raw"] = proxy_to_raw
templates.env.globals["account_proxy_ip"] = account_proxy_ip
templates.env.globals["interval_label"] = interval_label
templates.env.globals["format_calendar_times"] = format_calendar_times_label
