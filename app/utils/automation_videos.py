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
    for item in data:
        if isinstance(item, dict) and item.get("video_key"):
            out.append({
                "video_key": str(item["video_key"]),
                "video_original_name": str(item.get("video_original_name") or ""),
            })
    return out


def videos_to_json(entries: list[dict[str, str]]) -> str:
    return json.dumps(entries, ensure_ascii=False)


def video_count(automation: Automation) -> int:
    items = parse_videos_json(getattr(automation, "videos_json", None))
    return len(items) if items else 1


def resolve_video_key(automation: Automation) -> str:
    items = parse_videos_json(getattr(automation, "videos_json", None))
    if not items:
        return automation.video_key
    idx = int(getattr(automation, "current_index", 0) or 0)
    if idx < 0:
        idx = 0
    if idx >= len(items):
        # Playlist esgotada — não volta ao início
        return items[-1]["video_key"]
    return items[idx]["video_key"]


def resolve_video_label(automation: Automation) -> str:
    items = parse_videos_json(getattr(automation, "videos_json", None))
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
    items = parse_videos_json(getattr(automation, "videos_json", None))
    if items:
        return [e["video_key"] for e in items]
    return [automation.video_key]


def is_video_filename(name: str | None) -> bool:
    if not name:
        return False
    from pathlib import Path
    return Path(name).suffix.lower() in VIDEO_EXTENSIONS


def playlist_is_exhausted(automation: Automation) -> bool:
    """True quando há vários vídeos e o índice já passou do último."""
    items = parse_videos_json(getattr(automation, "videos_json", None))
    if len(items) <= 1:
        return False
    idx = int(getattr(automation, "current_index", 0) or 0)
    return idx >= len(items)
