"""Helpers para resposta automática a comentários (Reels + fotos feed)."""
from __future__ import annotations

import json
import random
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from models.models import Automation

AUTO_REPLY_CONTENT_TYPES = ("reel", "photo")
MAX_MESSAGES = 20
MAX_MESSAGE_LEN = 500


def parse_messages_raw(text: str) -> list[str]:
    """Uma mensagem por linha no textarea."""
    out: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        out.append(s[:MAX_MESSAGE_LEN])
        if len(out) >= MAX_MESSAGES:
            break
    return out


def stored_messages(automation: Automation | object) -> list[str]:
    raw_json = getattr(automation, "comment_auto_reply_messages", None)
    if raw_json:
        try:
            data = json.loads(raw_json)
            if isinstance(data, list):
                return [
                    str(x).strip()[:MAX_MESSAGE_LEN]
                    for x in data
                    if str(x).strip()
                ][:MAX_MESSAGES]
        except (json.JSONDecodeError, TypeError):
            pass
    single = (getattr(automation, "comment_auto_reply_message", None) or "").strip()
    return [single[:MAX_MESSAGE_LEN]] if single else []


def messages_for_textarea(automation: Automation | object) -> str:
    return "\n".join(stored_messages(automation))


def pick_reply_message(automation: Automation | object) -> str | None:
    msgs = stored_messages(automation)
    if not msgs:
        return None
    return random.choice(msgs)


def serialize_messages(messages: Iterable[str]) -> tuple[str | None, str | None]:
    """Retorna (json, primeira_mensagem) para persistência."""
    cleaned = [m.strip()[:MAX_MESSAGE_LEN] for m in messages if (m or "").strip()][:MAX_MESSAGES]
    if not cleaned:
        return None, None
    return json.dumps(cleaned, ensure_ascii=False), cleaned[0]


def comment_auto_reply_from_form(
    *,
    enabled_raw: object,
    message_raw: object,
    delay_raw: object,
    content_type: str,
) -> dict[str, object]:
    enabled = str(enabled_raw or "").strip().lower() in ("1", "true", "on", "yes")
    messages = parse_messages_raw(str(message_raw or ""))
    messages_json, first = serialize_messages(messages)
    try:
        delay = int(str(delay_raw or "5").strip() or 5)
    except (TypeError, ValueError):
        delay = 5
    delay = max(3, min(120, delay))
    if content_type not in AUTO_REPLY_CONTENT_TYPES:
        enabled = False
        messages_json = None
        first = None
    return {
        "comment_auto_reply_enabled": enabled,
        "comment_auto_reply_message": first,
        "comment_auto_reply_messages": messages_json,
        "comment_auto_reply_delay_seconds": delay,
    }
