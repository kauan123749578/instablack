"""Schemas do banco (SQLAlchemy 2.x)."""
from __future__ import annotations

import datetime as dt
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Column,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base


# --------- Tabela de jun\u00e7\u00e3o: automa\u00e7\u00e3o <-> contas Instagram ---------
automation_accounts = Table(
    "automation_accounts",
    Base.metadata,
    Column("automation_id", ForeignKey("automations.id", ondelete="CASCADE"), primary_key=True),
    Column("account_id", ForeignKey("instagram_accounts.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    avatar_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    account_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)
    notification_prefs_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    instagram_accounts: Mapped[List["InstagramAccount"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    automations: Mapped[List["Automation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class InviteCode(Base):
    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    created_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    used_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    used_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_by: Mapped[Optional["User"]] = relationship(foreign_keys=[created_by_id])
    used_by: Mapped[Optional["User"]] = relationship(foreign_keys=[used_by_id])


class InstagramAccount(Base):
    __tablename__ = "instagram_accounts"
    __table_args__ = (UniqueConstraint("user_id", "username", name="uq_user_ig_username"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    username: Mapped[str] = mapped_column(String(255))

    # password \u00e9 opcional: se vier null, n\u00e3o conseguimos re-logar automaticamente.
    encrypted_password: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    proxy: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    proxy_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    proxy_geo: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # ex: BR - Brasil
    session_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(32), default="active")  # active | paused | needs_login | proxy_down | banned
    last_login_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_health_check_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="instagram_accounts")
    automations: Mapped[List["Automation"]] = relationship(
        secondary=automation_accounts, back_populates="accounts"
    )
    publish_logs: Mapped[List["PublishLog"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class Automation(Base):
    __tablename__ = "automations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(16), default="reel")  # reel | story | photo
    caption: Mapped[str] = mapped_column(Text, default="")
    story_link: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    story_sticker_text: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    video_key: Mapped[str] = mapped_column(String(512))  # mídia principal no storage
    video_original_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    thumb_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    thumb_original_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    videos_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # playlist: [{video_key, video_original_name}]
    current_index: Mapped[int] = mapped_column(Integer, default=0)

    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    schedule_type: Mapped[str] = mapped_column(String(16), default="interval")  # interval | calendar
    calendar_days: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON [1,5,15]
    calendar_time: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)  # HH:MM BRT
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active | paused | completed

    next_run_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_run_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    total_runs: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="automations")
    accounts: Mapped[List["InstagramAccount"]] = relationship(
        secondary=automation_accounts, back_populates="automations"
    )
    publish_logs: Mapped[List["PublishLog"]] = relationship(
        back_populates="automation", cascade="all, delete-orphan"
    )


class PublishLog(Base):
    __tablename__ = "publish_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    automation_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("automations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("instagram_accounts.id", ondelete="CASCADE"), index=True
    )

    status: Mapped[str] = mapped_column(String(16))  # success | failed | skipped
    content_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # reel | story | photo
    media_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    media_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    play_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    like_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    insights_fetched_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    video_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    automation: Mapped[Optional["Automation"]] = relationship(back_populates="publish_logs")
    account: Mapped["InstagramAccount"] = relationship(back_populates="publish_logs")


class PushSubscription(Base):
    """Subscription Web Push do navegador/celular do usuário do painel."""

    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("endpoint", name="uq_push_endpoint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(Text)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship()


class AppNotification(Base):
    """Notificações in-app (card do sino na dashboard)."""

    __tablename__ = "app_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(32), default="info")
    # info | success | warning | metadata | warmup | publish
    link: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    publish_log_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("publish_logs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    user: Mapped["User"] = relationship()


class WarmupJob(Base):
    """Aquecimento de conta: interações randomizadas com seguidores de influenciadores."""

    __tablename__ = "warmup_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("instagram_accounts.id", ondelete="CASCADE"), index=True
    )
    influencers_json: Mapped[str] = mapped_column(Text, default="[]")  # ["user1","user2"]
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # pending | running | paused | done | failed
    actions_done: Mapped[int] = mapped_column(Integer, default=0)
    actions_target: Mapped[int] = mapped_column(Integer, default=40)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    ends_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_action: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship()
    account: Mapped["InstagramAccount"] = relationship()


class WarmupLog(Base):
    __tablename__ = "warmup_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("warmup_jobs.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    job: Mapped["WarmupJob"] = relationship()
