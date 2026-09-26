from __future__ import annotations

from .clock import utc_now

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .version import APP_VERSION


@dataclass
class MigrationReport:
    previous_db_version: Optional[str]
    current_db_version: str
    applied_steps: List[str]


def _parse_version(v: str) -> Tuple[int, int, int]:
    """Parse a semantic version string like '0.1.0'."""
    try:
        parts = (v or "").strip().split(".")
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
        return major, minor, patch
    except Exception:
        return 0, 0, 0


def _table_exists(conn, table_name: str) -> bool:
    # SQLite-specific check.
    q = text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t")
    row = conn.execute(q, {"t": table_name}).fetchone()
    return bool(row and row[0] == table_name)


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    # SQLite PRAGMA table_info
    rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    for r in rows:
        # (cid, name, type, notnull, dflt_value, pk)
        if len(r) >= 2 and str(r[1]).lower() == column_name.lower():
            return True
    return False


def _get_meta(conn, key: str) -> Optional[str]:
    if not _table_exists(conn, "app_meta"):
        return None
    row = conn.execute(text("SELECT value FROM app_meta WHERE key=:k"), {"k": key}).fetchone()
    return str(row[0]) if row and row[0] is not None else None


def _set_meta(conn, key: str, value: str) -> None:
    # SQLite upsert
    conn.execute(
        text(
            "INSERT INTO app_meta(key, value, updated_at) VALUES (:k, :v, :u) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at"
        ),
        {"k": key, "v": value, "u": utc_now().replace(tzinfo=None)},
    )


def ensure_db_schema(engine: Engine) -> MigrationReport:
    """Ensure the database schema is compatible with the current app version.

    TimeboardApp ships without Alembic migrations. For small deployments, we keep
    schema upgrades lightweight and SQLite-friendly by applying additive
    migrations (new tables, new nullable columns, and indexes).

    Returns a report with any applied steps.
    """
    applied: List[str] = []

    with engine.begin() as conn:
        # Ensure the schema version table exists (for pre-versioned DBs).
        if not _table_exists(conn, "app_meta"):
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS app_meta ("
                    "  key VARCHAR(64) PRIMARY KEY,"
                    "  value VARCHAR(255) NOT NULL,"
                    "  updated_at DATETIME NOT NULL"
                    ")"
                )
            )
            applied.append("create_table:app_meta")

        prev = _get_meta(conn, "db_version")

        # Add users.email (nullable).
        if _table_exists(conn, "users") and not _column_exists(conn, "users", "email"):
            conn.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR(255)"))
            applied.append("alter_table:users:add_column:email")

        # Add users.ui_prefs_json (nullable, JSON string) for per-user UI state.
        if _table_exists(conn, "users") and not _column_exists(conn, "users", "ui_prefs_json"):
            conn.execute(text("ALTER TABLE users ADD COLUMN ui_prefs_json TEXT"))
            applied.append("alter_table:users:add_column:ui_prefs_json")

        # Add users.manager_id (nullable) for user hierarchy.
        if _table_exists(conn, "users") and not _column_exists(conn, "users", "manager_id"):
            conn.execute(text("ALTER TABLE users ADD COLUMN manager_id INTEGER NULL"))
            applied.append("alter_table:users:add_column:manager_id")
        if _table_exists(conn, "users"):
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_manager_id ON users(manager_id)"))

        # Ensure unique index for users.email (helps older DBs where constraint didn't exist).
        # NOTE: SQLite allows multiple NULLs in UNIQUE indexes, which is what we want.
        if _table_exists(conn, "users"):
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email_unique ON users(email)"))

        # Ensure password reset token table exists (created via create_all in new DBs).
        if not _table_exists(conn, "password_reset_tokens"):
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS password_reset_tokens ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  user_id INTEGER NOT NULL,"
                    "  token_hash VARCHAR(64) NOT NULL UNIQUE,"
                    "  expires_at_utc DATETIME NOT NULL,"
                    "  used_at_utc DATETIME NULL,"
                    "  created_at DATETIME NOT NULL,"
                    "  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE"
                    ")"
                )
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_prt_user_id ON password_reset_tokens(user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_prt_expires ON password_reset_tokens(expires_at_utc)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_prt_used ON password_reset_tokens(used_at_utc)"))
            applied.append("create_table:password_reset_tokens")

        # User notification tag subscriptions
        if not _table_exists(conn, "user_notification_tags"):
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS user_notification_tags ("
                    "  user_id INTEGER NOT NULL,"
                    "  tag_id INTEGER NOT NULL,"
                    "  created_at DATETIME NOT NULL,"
                    "  PRIMARY KEY(user_id, tag_id),"
                    "  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,"
                    "  FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE"
                    ")"
                )
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_unt_user_id ON user_notification_tags(user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_unt_tag_id ON user_notification_tags(tag_id)"))
            applied.append("create_table:user_notification_tags")

        # User notification channels
        if not _table_exists(conn, "user_notification_channels"):
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS user_notification_channels ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  user_id INTEGER NOT NULL,"
                    "  channel_type VARCHAR(32) NOT NULL,"
                    "  enabled BOOLEAN NOT NULL DEFAULT 0,"
                    "  config_json TEXT NULL,"
                    "  created_at DATETIME NOT NULL,"
                    "  updated_at DATETIME NOT NULL,"
                    "  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE"
                    ")"
                )
            )
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_user_notification_channels ON user_notification_channels(user_id, channel_type)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_unc_user_id ON user_notification_channels(user_id)"))
            applied.append("create_table:user_notification_channels")

        # User notification services (multi-entry, routed by a generated tag)
        if not _table_exists(conn, "user_notification_services"):
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS user_notification_services ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  user_id INTEGER NOT NULL,"
                    "  service_type VARCHAR(32) NOT NULL,"
                    "  name VARCHAR(128) NULL,"
                    "  enabled BOOLEAN NOT NULL DEFAULT 1,"
                    "  config_json TEXT NULL,"
                    "  tag_id INTEGER NOT NULL,"
                    "  created_at DATETIME NOT NULL,"
                    "  updated_at DATETIME NOT NULL,"
                    "  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,"
                    "  FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE"
                    ")"
                )
            )
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_uns_tag_id ON user_notification_services(tag_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_uns_user_id ON user_notification_services(user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_uns_service_type ON user_notification_services(service_type)"))
            applied.append("create_table:user_notification_services")

        # Notification events
        if not _table_exists(conn, "notification_events"):
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS notification_events ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  user_id INTEGER NOT NULL,"
                    "  task_id INTEGER NULL,"
                    "  event_type VARCHAR(32) NOT NULL,"
                    "  event_key VARCHAR(255) NULL UNIQUE,"
                    "  title VARCHAR(255) NOT NULL,"
                    "  message TEXT NULL,"
                    "  created_at DATETIME NOT NULL,"
                    "  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,"
                    "  FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE"
                    ")"
                )
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ne_user_id ON notification_events(user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ne_task_id ON notification_events(task_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ne_created_at ON notification_events(created_at)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ne_event_type ON notification_events(event_type)"))
            applied.append("create_table:notification_events")

        # Ensure notification_events has service_id/service_type columns (added after v00.02.00).
        if _table_exists(conn, "notification_events"):
            if not _column_exists(conn, "notification_events", "service_id"):
                conn.execute(text("ALTER TABLE notification_events ADD COLUMN service_id INTEGER NULL"))
                applied.append("alter_table:notification_events:add_column:service_id")
            if not _column_exists(conn, "notification_events", "service_type"):
                conn.execute(text("ALTER TABLE notification_events ADD COLUMN service_type VARCHAR(32) NULL"))
                applied.append("alter_table:notification_events:add_column:service_type")
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ne_service_id ON notification_events(service_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ne_service_type ON notification_events(service_type)"))

            # Async delivery metadata (added after v00.04.01).
            if not _column_exists(conn, "notification_events", "delivery_status"):
                conn.execute(text("ALTER TABLE notification_events ADD COLUMN delivery_status VARCHAR(16) NULL"))
                applied.append("alter_table:notification_events:add_column:delivery_status")
            if not _column_exists(conn, "notification_events", "delivery_error"):
                conn.execute(text("ALTER TABLE notification_events ADD COLUMN delivery_error TEXT NULL"))
                applied.append("alter_table:notification_events:add_column:delivery_error")
            if not _column_exists(conn, "notification_events", "delivery_attempts"):
                conn.execute(text("ALTER TABLE notification_events ADD COLUMN delivery_attempts INTEGER NULL"))
                applied.append("alter_table:notification_events:add_column:delivery_attempts")
            if not _column_exists(conn, "notification_events", "last_attempt_at_utc"):
                conn.execute(text("ALTER TABLE notification_events ADD COLUMN last_attempt_at_utc DATETIME NULL"))
                applied.append("alter_table:notification_events:add_column:last_attempt_at_utc")
            if not _column_exists(conn, "notification_events", "delivered_at_utc"):
                conn.execute(text("ALTER TABLE notification_events ADD COLUMN delivered_at_utc DATETIME NULL"))
                applied.append("alter_table:notification_events:add_column:delivered_at_utc")
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ne_delivery_status ON notification_events(delivery_status)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ne_delivered_at_utc ON notification_events(delivered_at_utc)"))

            # In-app notifications: cleared_at_utc (added in v00.09.00).
            if not _column_exists(conn, "notification_events", "cleared_at_utc"):
                conn.execute(text("ALTER TABLE notification_events ADD COLUMN cleared_at_utc DATETIME NULL"))
                applied.append("alter_table:notification_events:add_column:cleared_at_utc")
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ne_cleared_at_utc ON notification_events(cleared_at_utc)"))

        # Task nesting + assignment attribution.
        if _table_exists(conn, "tasks"):
            if not _column_exists(conn, "tasks", "parent_task_id"):
                conn.execute(text("ALTER TABLE tasks ADD COLUMN parent_task_id INTEGER NULL"))
                applied.append("alter_table:tasks:add_column:parent_task_id")
            if not _column_exists(conn, "tasks", "assigned_by_user_id"):
                conn.execute(text("ALTER TABLE tasks ADD COLUMN assigned_by_user_id INTEGER NULL"))
                applied.append("alter_table:tasks:add_column:assigned_by_user_id")

            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_parent_task_id ON tasks(parent_task_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_assigned_by_user_id ON tasks(assigned_by_user_id)"))

        # Task follows (manager follow notifications).
        if not _table_exists(conn, "task_follows"):
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS task_follows ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  follower_user_id INTEGER NOT NULL,"
                    "  task_id INTEGER NOT NULL,"
                    "  created_at DATETIME NOT NULL,"
                    "  FOREIGN KEY(follower_user_id) REFERENCES users(id) ON DELETE CASCADE,"
                    "  FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE"
                    ")"
                )
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_task_follows_follower_task "
                    "ON task_follows(follower_user_id, task_id)"
                )
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_task_follows_follower_user_id ON task_follows(follower_user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_task_follows_task_id ON task_follows(task_id)"))
            applied.append("create_table:task_follows")

        # Set DB version to current app version (schema and app version are aligned for now).
        _set_meta(conn, "db_version", APP_VERSION)

        # Also store the app version that last booted against this DB.
        _set_meta(conn, "app_version", APP_VERSION)

    # If DB already had a version and it's newer than our app version, keep prev for reporting.
    # We still write app_version/db_version above to keep deployments consistent.
    return MigrationReport(previous_db_version=prev, current_db_version=APP_VERSION, applied_steps=applied)


def db_needs_upgrade(previous_db_version: Optional[str]) -> bool:
    if previous_db_version is None:
        return True
    return _parse_version(previous_db_version) < _parse_version(APP_VERSION)
