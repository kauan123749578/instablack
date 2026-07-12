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
    return max(1, len(playlist_items(automation)))


def pick_next_playlist_entry(
    automation: Automation,
    last_video_key: str | None = None,
) -> tuple[dict[str, str], int] | None:
    """Próximo vídeo da fila — usa o último publicado com sucesso (estilo postagemIG).

    Retorna None quando a playlist acabou (sem loop).
    """
    items = playlist_items(automation)
    if not items:
        return None
    if len(items) == 1:
        return items[0], 0

    keys = [it["video_key"] for it in items]

    if last_video_key and last_video_key in keys:
        next_idx = keys.index(last_video_key) + 1
    else:
        next_idx = int(getattr(automation, "current_index", 0) or 0)
        if next_idx < 0:
            next_idx = 0
        if next_idx >= len(items):
            return None

    if next_idx >= len(items):
        return None
    return items[next_idx], next_idx


def resolve_video_key(automation: Automation) -> str:
    picked = pick_next_playlist_entry(automation)
    if picked:
        return picked[0]["video_key"]
    items = playlist_items(automation)
    if items:
        return items[-1]["video_key"]
    return automation.video_key


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


def playlist_is_exhausted(automation: Automation, last_video_key: str | None = None) -> bool:
    """True quando há vários vídeos e não há próximo na fila."""
    items = playlist_items(automation)
    if len(items) <= 1:
        return False
    return pick_next_playlist_entry(automation, last_video_key) is None
