"""Ações de aquecimento de conta Instagram (interações humanizadas)."""
from __future__ import annotations

import logging
import random
import time
from typing import Any

from instagrapi import Client

log = logging.getLogger(__name__)

COMMENT_POOL = [
    "🔥",
    "Top demais!",
    "Muito bom 👏",
    "Conteúdo top",
    "Incrível!",
    "Show 💯",
    "Mandou bem",
    "Amei isso",
]

ACTIONS = (
    "follow",
    "like_post",
    "comment",
    "scroll_feed",
    "view_story",
    "like_story",
)


def _pause(a: float = 4.0, b: float = 14.0) -> None:
    time.sleep(random.uniform(a, b))


def _resolve_user_id(cl: Client, username: str) -> int | None:
    try:
        return int(cl.user_id_from_username(username.lstrip("@").strip()))
    except Exception as exc:
        log.warning("user_id_from_username(@%s): %s", username, exc)
        return None


def action_follow(cl: Client, username: str) -> dict[str, Any]:
    uid = _resolve_user_id(cl, username)
    if not uid:
        return {"ok": False, "detail": "usuário não encontrado"}
    cl.user_follow(uid)
    return {"ok": True, "detail": f"seguiu @{username}"}


def action_like_post(cl: Client, username: str) -> dict[str, Any]:
    uid = _resolve_user_id(cl, username)
    if not uid:
        return {"ok": False, "detail": "usuário não encontrado"}
    medias = cl.user_medias(uid, amount=random.randint(3, 8))
    if not medias:
        return {"ok": False, "detail": "sem posts"}
    media = random.choice(medias)
    cl.media_like(media.id)
    return {"ok": True, "detail": f"curtiu post de @{username}"}


def action_comment(cl: Client, username: str) -> dict[str, Any]:
    uid = _resolve_user_id(cl, username)
    if not uid:
        return {"ok": False, "detail": "usuário não encontrado"}
    medias = cl.user_medias(uid, amount=5)
    if not medias:
        return {"ok": False, "detail": "sem posts"}
    media = random.choice(medias)
    text = random.choice(COMMENT_POOL)
    cl.media_comment(media.id, text)
    return {"ok": True, "detail": f"comentou em @{username}: {text}"}


def action_scroll_feed(cl: Client, _username: str | None = None) -> dict[str, Any]:
    """Simula rolar o feed / explorar."""
    try:
        if hasattr(cl, "get_timeline_feed"):
            cl.get_timeline_feed()
        elif hasattr(cl, "explore_page"):
            cl.explore_page()
        else:
            # fallback: timeline via private API
            cl.private_request("feed/timeline/", params={"max_id": ""})
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:200]}
    _pause(2, 6)
    return {"ok": True, "detail": "rolou o feed"}


def action_view_story(cl: Client, username: str) -> dict[str, Any]:
    uid = _resolve_user_id(cl, username)
    if not uid:
        return {"ok": False, "detail": "usuário não encontrado"}
    stories = cl.user_stories(uid)
    if not stories:
        return {"ok": False, "detail": "sem stories"}
    sample = stories[: min(len(stories), random.randint(1, 3))]
    try:
        if hasattr(cl, "story_seen"):
            cl.story_seen([s.pk for s in sample])
        else:
            for s in sample:
                cl.media_seen([s.id] if hasattr(s, "id") else [s.pk])
    except Exception:
        # só "abrir" já aquece; seen pode falhar em algumas versões
        pass
    return {"ok": True, "detail": f"viu {len(sample)} story(ies) de @{username}"}


def action_like_story(cl: Client, username: str) -> dict[str, Any]:
    uid = _resolve_user_id(cl, username)
    if not uid:
        return {"ok": False, "detail": "usuário não encontrado"}
    stories = cl.user_stories(uid)
    if not stories:
        return {"ok": False, "detail": "sem stories"}
    story = random.choice(stories)
    try:
        if hasattr(cl, "story_like"):
            cl.story_like(story.pk)
        else:
            cl.media_like(story.pk)
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:200]}
    return {"ok": True, "detail": f"curtiu story de @{username}"}


ACTION_FNS = {
    "follow": action_follow,
    "like_post": action_like_post,
    "comment": action_comment,
    "scroll_feed": action_scroll_feed,
    "view_story": action_view_story,
    "like_story": action_like_story,
}


def run_random_action(cl: Client, influencers: list[str]) -> tuple[str, str | None, dict]:
    """Escolhe ação + alvo aleatórios e executa com pausa humana."""
    action = random.choice(ACTIONS)
    target = None
    if action != "scroll_feed":
        if not influencers:
            action = "scroll_feed"
        else:
            target = random.choice(influencers).lstrip("@").strip()

    fn = ACTION_FNS[action]
    _pause(3, 10)
    try:
        if action == "scroll_feed":
            result = fn(cl, None)
        else:
            result = fn(cl, target or "")
    except Exception as exc:
        result = {"ok": False, "detail": str(exc)[:300]}
    _pause(8, 25)
    return action, target, result
