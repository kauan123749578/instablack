"""Ações de aquecimento — interage com SEGUIDORES dos influenciadores (não com eles).

Tudo randomizado, com pausas longas. Sem ações em massa.
"""
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
    "Olha isso 👀",
    "Demais!",
]

ACTIONS = (
    "follow",
    "like_post",
    "comment",
    "scroll_feed",
    "view_story",
    "like_story",
)


def _pause(a: float = 18.0, b: float = 75.0) -> None:
    """Pausa humana longa — evita padrão de ação em massa."""
    time.sleep(random.uniform(a, b))


def _resolve_user_id(cl: Client, username: str) -> int | None:
    try:
        return int(cl.user_id_from_username(username.lstrip("@").strip()))
    except Exception as exc:
        log.warning("user_id_from_username(@%s): %s", username, exc)
        return None


def extract_follower_pool(
    cl: Client,
    influencers: list[str],
    *,
    per_influencer: int | None = None,
) -> list[str]:
    """Extrai usernames de seguidores dos influenciadores (amostra pequena)."""
    pool: list[str] = []
    seen: set[str] = set()
    for inf in influencers:
        name = inf.lstrip("@").strip()
        if not name:
            continue
        uid = _resolve_user_id(cl, name)
        if not uid:
            continue
        amount = per_influencer or random.randint(15, 40)
        try:
            followers = cl.user_followers(uid, amount=amount)
        except Exception as exc:
            log.warning("user_followers(@%s): %s", name, exc)
            _pause(20, 45)
            continue
        for _fid, user in (followers or {}).items():
            uname = getattr(user, "username", None) or ""
            uname = str(uname).lstrip("@").strip()
            if uname and uname.lower() not in seen and uname.lower() != name.lower():
                seen.add(uname.lower())
                pool.append(uname)
        # pausa longa entre cada influenciador
        _pause(25, 70)
    random.shuffle(pool)
    return pool


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
    medias = cl.user_medias(uid, amount=random.randint(2, 5))
    if not medias:
        return {"ok": False, "detail": "sem posts"}
    media = random.choice(medias)
    cl.media_like(media.id)
    return {"ok": True, "detail": f"curtiu post de @{username}"}


def action_comment(cl: Client, username: str) -> dict[str, Any]:
    uid = _resolve_user_id(cl, username)
    if not uid:
        return {"ok": False, "detail": "usuário não encontrado"}
    medias = cl.user_medias(uid, amount=3)
    if not medias:
        return {"ok": False, "detail": "sem posts"}
    # comentário é mais arriscado — só ~30% das vezes chega aqui via pesos
    media = random.choice(medias)
    text = random.choice(COMMENT_POOL)
    cl.media_comment(media.id, text)
    return {"ok": True, "detail": f"comentou em @{username}: {text}"}


def action_scroll_feed(cl: Client, _username: str | None = None) -> dict[str, Any]:
    try:
        if hasattr(cl, "get_timeline_feed"):
            cl.get_timeline_feed()
        else:
            cl.private_request("feed/timeline/", params={"max_id": ""})
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:200]}
    _pause(8, 20)
    return {"ok": True, "detail": "rolou o feed"}


def action_view_story(cl: Client, username: str) -> dict[str, Any]:
    uid = _resolve_user_id(cl, username)
    if not uid:
        return {"ok": False, "detail": "usuário não encontrado"}
    stories = cl.user_stories(uid)
    if not stories:
        return {"ok": False, "detail": "sem stories"}
    sample = stories[: min(len(stories), random.randint(1, 2))]
    try:
        if hasattr(cl, "story_seen"):
            cl.story_seen([s.pk for s in sample])
    except Exception:
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

# Pesos: comentário raro; scroll e like mais comuns
ACTION_WEIGHTS = {
    "follow": 2,
    "like_post": 4,
    "comment": 1,
    "scroll_feed": 3,
    "view_story": 3,
    "like_story": 2,
}


def _pick_action() -> str:
    names = list(ACTION_WEIGHTS.keys())
    weights = [ACTION_WEIGHTS[n] for n in names]
    return random.choices(names, weights=weights, k=1)[0]


def run_random_action(cl: Client, targets: list[str]) -> tuple[str, str | None, dict]:
    """Escolhe ação + alvo (seguidor) aleatórios com pausas longas."""
    action = _pick_action()
    target = None
    if action != "scroll_feed":
        if not targets:
            action = "scroll_feed"
        else:
            target = random.choice(targets).lstrip("@").strip()

    fn = ACTION_FNS[action]
    _pause(12, 40)  # antes
    try:
        if action == "scroll_feed":
            result = fn(cl, None)
        else:
            result = fn(cl, target or "")
    except Exception as exc:
        result = {"ok": False, "detail": str(exc)[:300]}
    _pause(25, 90)  # depois — bem espaçado
    return action, target, result
