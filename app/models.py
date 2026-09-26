from __future__ import annotations

from .clock import utc_now

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Theme(str, enum.Enum):
    light = "light"
    dark = "dark"
    system = "system"


class TaskStatus(str, enum.Enum):
    active = "active"
    completed = "completed"
    deleted = "deleted"


class RecurrenceType(str, enum.Enum):
    none = "none"
    post_completion = "post_completion"  # interval after completion
    multi_slot_daily = "multi_slot_daily"  # list of times each day
    fixed_clock = "fixed_clock"  # anchored interval regardless of completion


# Many-to-many association table
TaskTag = Table(
    "task_tags",
    Base.metadata,
    Column("task_id", ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        UniqueConstraint("email", name="uq_users_email"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Optional user hierarchy.
    # - Admins may assign a manager to any user.
    # - Managers are users who have one or more subordinates.
    manager_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    theme: Mapped[str] = mapped_column(String(16), default=Theme.system.value, nullable=False)
    purge_days: Mapped[int] = mapped_column(Integer, default=15, nullable=False)

    # UI preferences persisted per-user (JSON string).
    # Kept nullable and schema-upgradable via lightweight migrations.
    ui_prefs_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    tasks: Mapped[list["Task"]] = relationship(
        "Task",
        back_populates="user",
        foreign_keys="Task.user_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # Self-referential relationship.
    manager: Mapped["User"] = relationship(
        "User",
        remote_side=[id],
        back_populates="subordinates",
        foreign_keys=[manager_id],
    )
    subordinates: Mapped[list["User"]] = relationship(
        "User",
        back_populates="manager",
        foreign_keys=[manager_id],
        passive_deletes=True,
    )


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # Store only a hash of the token so DB disclosure does not leak live tokens.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    expires_at_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    used_at_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    user: Mapped[User] = relationship("User")


class AppMeta(Base):
    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now, nullable=False
    )


class UserNotificationTag(Base):
    """A user's subscription to a tag for notifications.

    If a task owned by the user has ANY subscribed tag, it can trigger
    notifications on task lifecycle changes.
    """

    __tablename__ = "user_notification_tags"

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class UserNotificationChannel(Base):
    """Per-user notification channel configuration.

    `channel_type` is a short string (e.g. 'email', 'gotify').
    `config_json` stores channel-specific settings.
    """

    __tablename__ = "user_notification_channels"
    __table_args__ = (UniqueConstraint("user_id", "channel_type", name="uq_user_notification_channels"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now, nullable=False
    )


class UserNotificationService(Base):
    """Per-user notification service entry.

    Each service entry generates a dedicated Tag (tag_id). Any task that
    includes that tag will send notifications for task lifecycle events to this
    specific service entry.

    This enables multiple services per user (including multiple entries of the
    same service_type).
    """

    __tablename__ = "user_notification_services"
    __table_args__ = (
        UniqueConstraint("tag_id", name="uq_user_notification_services_tag_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # e.g. 'email', 'browser', 'gotify', 'ntfy', 'webhook', 'generic_api', 'wns'
    service_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # User-visible label (optional).
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Dedicated notification routing tag.
    tag_id: Mapped[int] = mapped_column(Integer, ForeignKey("tags.id", ondelete="CASCADE"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    # Optional relationship for convenience.
    tag: Mapped["Tag"] = relationship("Tag", lazy="joined")


class NotificationEvent(Base):
    """A record of a notification-worthy task event.

    These rows back browser notifications (SSE) and also provide deduping for
    scheduler-driven events like `past_due`.
    """

    __tablename__ = "notification_events"
    __table_args__ = (UniqueConstraint("event_key", name="uq_notification_events_event_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # Optional link back to the notification service entry that produced this
    # event. (May be NULL for legacy records or if the service is deleted.)
    service_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("user_notification_services.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Snapshot of service_type at send time for filtering without joins.
    service_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Optional de-duplication key. Multiple NULLs are allowed.
    event_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Delivery metadata (best-effort). Populated for non-browser services when
    # sends are performed asynchronously.
    #
    # delivery_status values are small strings like:
    #   - queued
    #   - sending
    #   - sent
    #   - failed
    #   - skipped
    delivery_status: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    delivery_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_attempts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_attempt_at_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_at_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # In-app notifications use `cleared_at_utc` for "new" badge state.
    # Uncleared notifications persist indefinitely. Cleared notifications are
    # eligible for purge using the same retention policy as archived tasks.
    cleared_at_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("name", name="uq_tags_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)

    tasks: Mapped[list["Task"]] = relationship(
        "Task",
        secondary=TaskTag,
        back_populates="tags",
    )


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # Nested subtasks.
    parent_task_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Task assignment attribution (optional).
    # When a manager assigns a task to a subordinate, `user_id` is the assignee
    # and `assigned_by_user_id` is the manager.
    assigned_by_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    task_type: Mapped[str] = mapped_column(String(128), nullable=False)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    due_date_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    recurrence_type: Mapped[str] = mapped_column(
        Enum(RecurrenceType),
        default=RecurrenceType.none,
        nullable=False,
    )

    # For post_completion or fixed_clock interval recurrence.
    recurrence_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # For multi_slot_daily: comma-separated list of HH:MM times (24h).
    recurrence_times: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(Enum(TaskStatus), default=TaskStatus.active, nullable=False, index=True)

    completed_at_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    user: Mapped[User] = relationship(
        "User",
        back_populates="tasks",
        foreign_keys=[user_id],
    )

    parent: Mapped["Task"] = relationship(
        "Task",
        remote_side=[id],
        back_populates="children",
        foreign_keys=[parent_task_id],
    )
    children: Mapped[list["Task"]] = relationship(
        "Task",
        back_populates="parent",
        foreign_keys=[parent_task_id],
        passive_deletes=True,
    )

    assigned_by: Mapped[User] = relationship(
        "User",
        foreign_keys=[assigned_by_user_id],
        lazy="joined",
    )

    tags: Mapped[list[Tag]] = relationship(
        "Tag",
        secondary=TaskTag,
        back_populates="tasks",
    )

    def archived_at_utc(self) -> datetime | None:
        if self.status == TaskStatus.completed:
            return self.completed_at_utc
        if self.status == TaskStatus.deleted:
            return self.deleted_at_utc
        return None


class TaskFollow(Base):
    """A user's subscription to another user's task.

    Used for manager "follow" notifications.
    """

    __tablename__ = "task_follows"
    __table_args__ = (
        UniqueConstraint("follower_user_id", "task_id", name="uq_task_follows_follower_task"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    follower_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)



class IntegrationToken(Base):
    """Revocable, hashed, narrowly scoped integration credentials (not exported)."""
    __tablename__ = "integration_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes: Mapped[str] = mapped_column(String(128))
    credential_fingerprint: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
