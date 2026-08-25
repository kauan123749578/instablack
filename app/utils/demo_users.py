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
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.security import hash_password
from models.models import InstagramAccount, PublishLog, User

BRT = ZoneInfo("America/Sao_Paulo")

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
    posts_today = max(0, min(int(posts_today or 0), 500))
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


def _inject_posts(db: Session, account_id: int, count: int) -> int:
    """Insere PublishLog success com created_at espalhado no dia BRT atual."""
    count = max(0, min(int(count), 500))
    if count <= 0:
        return 0
    start_n, end_n = _today_bounds_utc_naive()
    span = max(1, int((end_n - start_n).total_seconds()) - 60)
    now_n = dt.datetime.utcnow()
    # Não passa do "agora" (rank é até o fim do dia, mas timestamps futuros confundem)
    max_offset = max(1, int((min(now_n, end_n - dt.timedelta(seconds=1)) - start_n).total_seconds()))
    for i in range(count):
        offset = random.randint(0, max_offset) if count > 1 else max(0, max_offset // 2)
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


def boost_demo_posts(db: Session, user_id: int, add_posts: int) -> int:
    add_posts = max(0, min(int(add_posts or 0), 500))
    user = db.get(User, user_id)
    if not user or not getattr(user, "is_demo", False):
        raise ValueError("Usuário demo não encontrado")
    acc = db.scalar(
        select(InstagramAccount).where(
            InstagramAccount.user_id == user.id,
            InstagramAccount.status == "active",
        )
    )
    if not acc:
        acc = InstagramAccount(
            user_id=user.id,
            username=f"{user.username}_ig"[:40],
            provider="instagrapi",
            status="active",
        )
        db.add(acc)
        db.flush()
    return _inject_posts(db, acc.id, add_posts)


def set_demo_posts_today(db: Session, user_id: int, target: int) -> int:
    """Ajusta posts de sucesso de hoje BRT para exatamente ``target``."""
    target = max(0, min(int(target or 0), 500))
    user = db.get(User, user_id)
    if not user or not getattr(user, "is_demo", False):
        raise ValueError("Usuário demo não encontrado")
    acc = db.scalar(
        select(InstagramAccount).where(InstagramAccount.user_id == user.id)
    )
    if not acc:
        acc = InstagramAccount(
            user_id=user.id,
            username=f"{user.username}_ig"[:40],
            provider="instagrapi",
            status="active",
        )
        db.add(acc)
        db.flush()

    start_n, end_n = _today_bounds_utc_naive()

    current = (
        db.scalar(
            select(func.count(PublishLog.id)).where(
                PublishLog.account_id == acc.id,
                PublishLog.status == "success",
                PublishLog.created_at >= start_n,
                PublishLog.created_at < end_n,
            )
        )
        or 0
    )
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
        _inject_posts(db, acc.id, target - current)
    return target


def delete_demo_user(db: Session, user_id: int) -> bool:
    user = db.get(User, user_id)
    if not user or not getattr(user, "is_demo", False):
        return False
    db.delete(user)
    return True


def delete_all_demo_users(db: Session) -> int:
    demos = db.scalars(select(User).where(User.is_demo.is_(True))).all()
    n = 0
    for u in demos:
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
        out.append({"user": u, "posts_today": int(posts)})
    return out
