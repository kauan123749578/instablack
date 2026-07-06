"""Instância compartilhada de Jinja2Templates com filtros globais."""
from __future__ import annotations

import json

from fastapi.templating import Jinja2Templates

from app.utils.automation_videos import video_count as automation_video_count
from app.utils.avatars import user_avatar_url, user_display_name
from app.utils.proxy import account_proxy_ip, proxy_label, proxy_to_raw
from app.utils.intervals import interval_label
from app.utils.formatters import format_interval, status_badge_class, status_label
from app.utils.timezone import brt_now, format_date_header, greeting_for_user, greeting_period, to_brt

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
templates.env.globals["status_label"] = status_label
templates.env.globals["status_badge_class"] = status_badge_class
templates.env.globals["automation_video_count"] = automation_video_count
templates.env.globals["proxy_label"] = proxy_label
templates.env.globals["proxy_to_raw"] = proxy_to_raw
templates.env.globals["account_proxy_ip"] = account_proxy_ip
templates.env.globals["interval_label"] = interval_label
