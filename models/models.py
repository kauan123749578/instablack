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
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    instagram_accounts: Mapped[List["InstagramAccount"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    automations: Mapped[List["Automation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class InstagramAccount(Base):
    __tablename__ = "instagram_accounts"
    __table_args__ = (UniqueConstraint("user_id", "username", name="uq_user_ig_username"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    username: Mapped[str] = mapped_column(String(255))

    # password \u00e9 opcional: se vier null, n\u00e3o conseguimos re-logar automaticamente.
    encrypted_password: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    proxy: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    session_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(32), default="active")  # active | needs_login | banned | proxy_down
    last_login_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
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
    video_key: Mapped[str] = mapped_column(String(512))  # mídia principal no storage
    video_original_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    thumb_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    thumb_original_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    schedule_type: Mapped[str] = mapped_column(String(16), default="interval")  # interval | calendar
    calendar_days: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON [1,5,15]
    calendar_time: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)  # HH:MM BRT
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active | paused

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
    media_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    media_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    automation: Mapped[Optional["Automation"]] = relationship(back_populates="publish_logs")
    account: Mapped["InstagramAccount"] = relationship(back_populates="publish_logs")
