"""Lista de vídeos em playlist (um por ciclo; para ao terminar se N>1)."""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from models.models import Automation

log = logging.getLogger(__name__)

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


def compute_next_playlist_index(db: Session, automation: Automation) -> int:
    """Próximo índice baseado no que JÁ foi publicado com sucesso — não confia no current_index.

    Regra: anda a playlist em ordem; o próximo é o primeiro video_key que ainda
    não tem nenhum PublishLog success. Várias contas postando o mesmo vídeo
    contam como 1 (distinct por key).
    """
    from sqlalchemy import func, select

    from models.models import PublishLog

    items = playlist_items(automation)
    if not items:
        return 0
    if len(items) == 1:
        return 0

    keys = [it["video_key"] for it in items]
    posted_keys = set(
        db.scalars(
            select(PublishLog.video_key)
            .where(
                PublishLog.automation_id == automation.id,
                PublishLog.status == "success",
                PublishLog.video_key.in_(keys),
            )
            .distinct()
        ).all()
    )
    posted_keys.discard(None)

    if posted_keys:
        for i, key in enumerate(keys):
            if key not in posted_keys:
                return i
        return len(keys)  # todos já saíram

    # Logs antigos sem video_key: usa total de sucessos / nº de contas
    n_ok = db.scalar(
        select(func.count())
        .select_from(PublishLog)
        .where(
            PublishLog.automation_id == automation.id,
            PublishLog.status == "success",
        )
    ) or 0
    n_acc = max(1, len(getattr(automation, "accounts", None) or []) or 1)
    approx = int(n_ok) // n_acc
    return min(max(approx, 0), len(items))


def sync_playlist_cursor(db: Session, automation: Automation) -> int:
    """Grava current_index = próximo vídeo real (SQL cru, sem ORM overwrite)."""
    from sqlalchemy import text

    items = playlist_items(automation)
    if len(items) <= 1:
        return int(getattr(automation, "current_index", 0) or 0)

    next_idx = compute_next_playlist_index(db, automation)
    cur = int(getattr(automation, "current_index", 0) or 0)

    if next_idx != cur:
        log.warning(
            "sync_playlist_cursor automation=%s %s → %s (de %s vídeos)",
            automation.id,
            cur,
            next_idx,
            len(items),
        )

    if next_idx >= len(items):
        db.execute(
            text(
                "UPDATE automations SET current_index = :idx, status = 'completed', "
                "next_run_at = NULL WHERE id = :id"
            ),
            {"idx": next_idx, "id": automation.id},
        )
        automation.current_index = next_idx
        automation.status = "completed"
        automation.next_run_at = None
    else:
        db.execute(
            text("UPDATE automations SET current_index = :idx WHERE id = :id"),
            {"idx": next_idx, "id": automation.id},
        )
        automation.current_index = next_idx

    return next_idx
