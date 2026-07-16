"""Lista de vídeos em playlist (um por ciclo; para ao terminar se N>1)."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.models import Automation

VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v", ".mkv"}


def parse_videos_json(raw: str | None) -> list[dict[str, str]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        key = str(item.get("video_key") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({
            "video_key": key,
            "video_original_name": str(item.get("video_original_name") or ""),
        })
    return out


def videos_to_json(entries: list[dict[str, str]]) -> str:
    cleaned = parse_videos_json(json.dumps(entries or []))
    return json.dumps(cleaned, ensure_ascii=False)


def playlist_items(automation: Automation) -> list[dict[str, str]]:
    """Playlist completa; se videos_json vazio, usa o video_key único."""
    items = parse_videos_json(getattr(automation, "videos_json", None))
    if items:
        return items
    key = getattr(automation, "video_key", None)
    if not key:
        return []
    return [{
        "video_key": str(key),
        "video_original_name": str(getattr(automation, "video_original_name", None) or ""),
    }]


def video_count(automation: Automation) -> int:
    return len(playlist_items(automation))


def resolve_video_key(automation: Automation) -> str:
    items = playlist_items(automation)
    if not items:
        return automation.video_key
    idx = int(getattr(automation, "current_index", 0) or 0)
    if idx < 0:
        idx = 0
    if idx >= len(items):
        return items[-1]["video_key"]
    return items[idx]["video_key"]


def resolve_video_label(automation: Automation) -> str:
    items = playlist_items(automation)
    if not items:
        return automation.video_original_name or automation.video_key
    idx = min(int(getattr(automation, "current_index", 0) or 0), len(items) - 1)
    if idx < 0:
        idx = 0
    name = items[idx].get("video_original_name") or f"vídeo {idx + 1}"
    if len(items) > 1:
        return f"{name} ({idx + 1}/{len(items)})"
    return name


def all_video_keys(automation: Automation) -> list[str]:
    items = playlist_items(automation)
    if items:
        return [e["video_key"] for e in items]
    return [automation.video_key]


def is_video_filename(name: str | None) -> bool:
    if not name:
        return False
    from pathlib import Path
    return Path(name).suffix.lower() in VIDEO_EXTENSIONS


def playlist_is_exhausted(automation: Automation) -> bool:
    items = playlist_items(automation)
    if len(items) <= 1:
        return False
    return int(getattr(automation, "current_index", 0) or 0) >= len(items)


def media_keys_for_automation(automation: Automation) -> list[str]:
    """Todas as keys de mídia referenciadas (vídeos + capa)."""
    keys = list(all_video_keys(automation))
    thumb = getattr(automation, "thumb_key", None)
    if thumb and thumb not in keys:
        keys.append(str(thumb))
    return [k for k in keys if k]


def media_key_referenced_elsewhere(db, key: str, *, exclude_automation_id: int) -> bool:
    """True se outra automação ainda usa esta key (evita apagar mídia compartilhada)."""
    from sqlalchemy import or_, select

    from models.models import Automation

    if not key:
        return False
    q = select(Automation.id).where(
        Automation.id != exclude_automation_id,
        or_(
            Automation.video_key == key,
            Automation.thumb_key == key,
            Automation.videos_json.contains(key),
        ),
    ).limit(1)
    return db.scalar(q) is not None
