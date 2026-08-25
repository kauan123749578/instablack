"""Usuários demo para marketing (Top do Dia / prints).

Só o owner cria: User(is_demo) + InstagramAccount fictícia + PublishLog success
no dia BRT atual — o rank conta esses posts.
"""
from __future__ import annotations

import random
import re
import secrets
import string
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.media_access import signed_media_path
from app.security import hash_password
from core.storage import get_storage
from models.models import InstagramAccount, PublishLog, User

_AVATAR_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_AVATAR_CT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

BRT = ZoneInfo("America/Sao_Paulo")

# Teto de posts fictícios por demo no dia BRT (Top do Dia).
MAX_DEMO_POSTS_TODAY = 5000
# Tick de crescimento: a cada ~5 min (ver beat), demos “postam” de novo.
DEMO_GROWTH_MAX_ADD_PER_TICK = 18

_FIRST = (
    "Ana", "Bruno", "Camila", "Diego", "Elena", "Felipe", "Gabriela", "Henrique",
    "Isabela", "João", "Karina", "Lucas", "Marina", "Nicolas", "Olivia", "Pedro",
    "Rafaela", "Sofia", "Thiago", "Valentina", "Amanda", "Caio", "Larissa", "Mateus",
)
_LAST = (
    "Silva", "Santos", "Oliveira", "Souza", "Lima", "Pereira", "Costa", "Ferreira",
    "Almeida", "Rodrigues", "Martins", "Araujo", "Ribeiro", "Carvalho", "Gomes",
)


def clear_rank_cache() -> None:
    try:
        from app.routes.dashboard import _RANK_CACHE

        _RANK_CACHE.clear()
    except Exception:
        pass


def _slug(name: str) -> str:
    raw = name.lower().strip()
    raw = (
        raw.replace("á", "a")
        .replace("à", "a")
        .replace("ã", "a")
        .replace("â", "a")
        .replace("é", "e")
        .replace("ê", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ô", "o")
        .replace("õ", "o")
        .replace("ú", "u")
        .replace("ç", "c")
    )
    raw = re.sub(r"[^a-z0-9]+", "", raw)
    return raw[:18] or "user"


def _unique_username(db: Session, base: str) -> str:
    for _ in range(40):
        suffix = "".join(random.choices(string.digits, k=random.randint(2, 4)))
        candidate = f"{base}{suffix}"[:30]
        exists = db.scalar(select(User.id).where(User.username == candidate))
        if not exists:
            return candidate
    return f"demo{secrets.token_hex(4)}"


def brt_now() -> dt.datetime:
    return dt.datetime.now(BRT)


def make_demo_user(
    db: Session,
    *,
    display_name: str | None = None,
    username: str | None = None,
    posts_today: int = 12,
) -> User:
    """Cria usuário demo + conta IG fantasma + N PublishLog success (hoje BRT)."""
    posts_today = max(0, min(int(posts_today or 0), MAX_DEMO_POSTS_TODAY))
    if display_name and display_name.strip():
        name = display_name.strip()[:80]
    else:
        name = f"{random.choice(_FIRST)} {random.choice(_LAST)}"

    if username and username.strip():
        uname = _slug(username.strip().lstrip("@"))
        if db.scalar(select(User.id).where(User.username == uname)):
            uname = _unique_username(db, uname)
    else:
        uname = _unique_username(db, _slug(name.split()[0]))

    user = User(
        username=uname,
        password_hash=hash_password(secrets.token_urlsafe(24)),
        display_name=name,
        is_admin=False,
        is_owner=False,
        owner_private=False,
        is_demo=True,
        is_active=True,
        allow_instagrapi=False,
        account_limit=0,
    )
    db.add(user)
    db.flush()

    ig_name = f"{uname}_ig"[:40]
    acc = InstagramAccount(
        user_id=user.id,
        username=ig_name,
        provider="instagrapi",
        status="active",
    )
    db.add(acc)
    db.flush()

    if posts_today:
        _inject_posts(db, acc.id, posts_today)

    return user


def _today_bounds_utc_naive() -> tuple[dt.datetime, dt.datetime]:
    """Mesma janela do Top do Dia (dashboard)."""
    now = brt_now()
    start = dt.datetime.combine(now.date(), dt.time.min, tzinfo=BRT)
    end = start + dt.timedelta(days=1)

    def _naive_utc(d: dt.datetime) -> dt.datetime:
        return d.astimezone(dt.timezone.utc).replace(tzinfo=None)

    return _naive_utc(start), _naive_utc(end)


def _inject_posts(
    db: Session,
    account_id: int,
    count: int,
    *,
    near_now: bool = False,
) -> int:
    """Insere PublishLog success com created_at no dia BRT atual.

    ``near_now=True`` concentra timestamps nos últimos minutos (crescimento ao vivo).
    """
    count = max(0, min(int(count), MAX_DEMO_POSTS_TODAY))
    if count <= 0:
        return 0
    start_n, end_n = _today_bounds_utc_naive()
    now_n = dt.datetime.utcnow()
    # Não passa do "agora" (rank é até o fim do dia, mas timestamps futuros confundem)
    latest = min(now_n, end_n - dt.timedelta(seconds=1))
    max_offset = max(1, int((latest - start_n).total_seconds()))
    for i in range(count):
        if near_now:
            # Últimos ~12 min — parece postagem recente no rank
            window = min(720, max_offset)
            offset = max(0, max_offset - random.randint(0, window))
        elif count > 1:
            offset = random.randint(0, max_offset)
        else:
            offset = max(0, max_offset // 2)
        created = start_n + dt.timedelta(seconds=offset)
        db.add(
            PublishLog(
                automation_id=None,
                account_id=account_id,
                status="success",
                content_type="reel",
                media_id=f"demo_{account_id}_{i}_{secrets.token_hex(3)}",
                play_count=random.randint(800, 45000),
                created_at=created,
            )
        )
    return count


def _demo_account(db: Session, user: User) -> InstagramAccount:
    acc = db.scalar(
        select(InstagramAccount).where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.status == "active",
        )
    )
    if acc:
        return acc
    acc = db.scalar(select(InstagramAccount).where(InstagramAccount.user_id == user.id))
    if acc:
        return acc
    acc = InstagramAccount(
        user_id=user.id,
        username=f"{user.username}_ig"[:40],
        provider="instagrapi",
        status="active",
    )
    db.add(acc)
    db.flush()
    return acc


def _posts_today_for_account(db: Session, account_id: int) -> int:
    start_n, end_n = _today_bounds_utc_naive()
    return (
        db.scalar(
            select(func.count(PublishLog.id)).where(
                PublishLog.account_id == account_id,
                PublishLog.status == "success",
                PublishLog.created_at >= start_n,
                PublishLog.created_at < end_n,
            )
        )
        or 0
    )


def boost_demo_posts(db: Session, user_id: int, add_posts: int) -> int:
    add_posts = max(0, min(int(add_posts or 0), MAX_DEMO_POSTS_TODAY))
    user = db.get(User, user_id)
    if not user or not getattr(user, "is_demo", False):
        raise ValueError("Usuário demo não encontrado")
    acc = _demo_account(db, user)
    current = _posts_today_for_account(db, acc.id)
    room = max(0, MAX_DEMO_POSTS_TODAY - current)
    return _inject_posts(db, acc.id, min(add_posts, room), near_now=True)


def set_demo_posts_today(db: Session, user_id: int, target: int) -> int:
    """Ajusta posts de sucesso de hoje BRT para exatamente ``target``."""
    target = max(0, min(int(target or 0), MAX_DEMO_POSTS_TODAY))
    user = db.get(User, user_id)
    if not user or not getattr(user, "is_demo", False):
        raise ValueError("Usuário demo não encontrado")
    acc = _demo_account(db, user)

    start_n, end_n = _today_bounds_utc_naive()
    current = _posts_today_for_account(db, acc.id)
    if current > target:
        ids = db.scalars(
            select(PublishLog.id)
            .where(
                PublishLog.account_id == acc.id,
                PublishLog.status == "success",
                PublishLog.created_at >= start_n,
                PublishLog.created_at < end_n,
            )
            .order_by(PublishLog.created_at.desc())
            .limit(current - target)
        ).all()
        if ids:
            db.execute(delete(PublishLog).where(PublishLog.id.in_(ids)))
        return target
    if current < target:
        _inject_posts(db, acc.id, target - current, near_now=False)
    return target


def grow_demo_posts_tick(db: Session) -> dict:
    """Faz demos 'postarem' ao longo do dia — rank muda de hora em hora.

    Ritmo diferente por demo (e um pouco de aleatório) para o Top do Dia
    parecer gente real, estilo marketing.
    """
    demos = db.scalars(select(User).where(User.is_demo.is_(True))).all()
    if not demos:
        return {"demos": 0, "added": 0, "seeded": 0}

    now = brt_now()
    hour = now.hour
    added_total = 0
    seeded = 0

    for user in demos:
        acc = _demo_account(db, user)
        current = _posts_today_for_account(db, acc.id)

        # Meia-noite BRT zera o dia — se ainda não postou hoje, dá um boost inicial
        if current <= 0:
            morning = 8 + ((user.id * 13) % 40)  # 8–47
            morning = min(morning, MAX_DEMO_POSTS_TODAY)
            added_total += _inject_posts(db, acc.id, morning, near_now=False)
            seeded += 1
            continue

        if current >= MAX_DEMO_POSTS_TODAY:
            continue

        # ~25% dos ticks um demo “fica quieto” — ranking não sobe todo mundo junto
        skip_chance = 0.22 + ((user.id + hour) % 5) * 0.03
        if random.random() < skip_chance:
            continue

        # Ritmo base por demo (1–DEMO_GROWTH_MAX) + variação por hora
        pace = 1 + ((user.id * 17 + hour * 3) % DEMO_GROWTH_MAX_ADD_PER_TICK)
        # De madrugada postam menos; pico tarde/noite
        if hour < 7:
            pace = max(1, pace // 3)
        elif 11 <= hour <= 14 or 18 <= hour <= 22:
            pace = min(DEMO_GROWTH_MAX_ADD_PER_TICK, pace + random.randint(1, 4))

        add = random.randint(1, max(1, pace))
        room = MAX_DEMO_POSTS_TODAY - current
        add = min(add, room)
        if add <= 0:
            continue
        added_total += _inject_posts(db, acc.id, add, near_now=True)

    if added_total or seeded:
        clear_rank_cache()
    return {"demos": len(demos), "added": added_total, "seeded": seeded}


def set_demo_avatar(db: Session, user_id: int, file_obj, *, filename: str) -> str:
    """Salva foto de perfil do demo (mesma chave estável de /perfil)."""
    user = db.get(User, user_id)
    if not user or not getattr(user, "is_demo", False):
        raise ValueError("Usuário demo não encontrado")
    ext = Path(filename or "").suffix.lower() or ".jpg"
    if ext not in _AVATAR_EXTS:
        raise ValueError("Use foto .jpg, .png ou .webp.")
    try:
        file_obj.seek(0)
    except Exception:
        pass
    storage = get_storage()
    key = f"avatars/user/{user.id}{ext}"
    if hasattr(storage, "save_at_key"):
        new_key = storage.save_at_key(
            key,
            file_obj,
            content_type=_AVATAR_CT.get(ext, "image/jpeg"),
        )
    else:
        new_key = storage.save(file_obj, suggested_ext=ext)
    old_key = user.avatar_key
    user.avatar_key = new_key
    if old_key and old_key != new_key:
        try:
            storage.delete(old_key)
        except Exception:
            pass
    return new_key


def _delete_avatar_file(user: User) -> None:
    key = getattr(user, "avatar_key", None)
    if not key:
        return
    try:
        get_storage().delete(key)
    except Exception:
        pass


def delete_demo_user(db: Session, user_id: int) -> bool:
    user = db.get(User, user_id)
    if not user or not getattr(user, "is_demo", False):
        return False
    _delete_avatar_file(user)
    db.delete(user)
    return True


def delete_all_demo_users(db: Session) -> int:
    demos = db.scalars(select(User).where(User.is_demo.is_(True))).all()
    n = 0
    for u in demos:
        _delete_avatar_file(u)
        db.delete(u)
        n += 1
    return n


def list_demo_users_with_posts(db: Session) -> list[dict]:
    start_n, end_n = _today_bounds_utc_naive()
    demos = db.scalars(
        select(User).where(User.is_demo.is_(True)).order_by(User.created_at.desc())
    ).all()
    out = []
    for u in demos:
        posts = (
            db.scalar(
                select(func.count(PublishLog.id))
                .select_from(PublishLog)
                .join(InstagramAccount, InstagramAccount.id == PublishLog.account_id)
                .where(
                    InstagramAccount.user_id == u.id,
                    PublishLog.status == "success",
                    PublishLog.created_at >= start_n,
                    PublishLog.created_at < end_n,
                )
            )
            or 0
        )
        out.append({
            "user": u,
            "posts_today": int(posts),
            "avatar_url": signed_media_path(u.avatar_key) if u.avatar_key else None,
        })
    return out
