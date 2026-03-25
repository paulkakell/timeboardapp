from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, joinedload

from .auth import hash_password, verify_password
from .config import get_settings
from .models import NotificationEvent, PasswordResetToken, RecurrenceType, Tag, Task, TaskFollow, TaskStatus, Theme, User
from .notifications import EVENT_ARCHIVED, EVENT_COMPLETED, EVENT_CREATED, EVENT_UPDATED, notify_task_event
from .recurrence import (
    RecurrenceError,
    compute_next_due_utc,
    parse_duration_to_seconds,
    parse_fixed_calendar_rule,
    parse_times_csv,
)


logger = logging.getLogger("timeboardapp.crud")
from .utils.time_utils import from_local_to_utc_naive


def normalize_datetime_to_utc_naive(dt: datetime) -> datetime:
    """Normalize a datetime to naive UTC.

    - If `dt` is timezone-aware, convert to UTC and drop tzinfo.
    - If `dt` is naive, interpret it in app timezone and convert to UTC.
    """
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return from_local_to_utc_naive(dt)


def normalize_email(email: str | None) -> str | None:
    if email is None:
        return None
    e = str(email).strip().lower()
    return e or None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------- In-app notifications ----------------------


IN_APP_SERVICE_TYPE = "in_app"


_UNSET = object()


def _now_utc_naive() -> datetime:
    return datetime.utcnow().replace(tzinfo=None)


def create_in_app_notification(
    db: Session,
    *,
    user_id: int,
    event_type: str,
    title: str,
    message: str | None = None,
    task_id: int | None = None,
    event_key: str | None = None,
) -> NotificationEvent:
    ev = NotificationEvent(
        user_id=int(user_id),
        task_id=(int(task_id) if task_id is not None else None),
        service_id=None,
        service_type=IN_APP_SERVICE_TYPE,
        event_type=str(event_type),
        event_key=event_key,
        title=str(title)[:255],
        message=message,
        created_at=_now_utc_naive(),
        delivery_status=None,
        delivery_error=None,
        delivery_attempts=None,
        last_attempt_at_utc=None,
        delivered_at_utc=None,
        cleared_at_utc=None,
    )
    db.add(ev)
    return ev


def count_in_app_unread(db: Session, *, user_id: int) -> int:
    n = (
        db.query(func.count(NotificationEvent.id))
        .filter(NotificationEvent.user_id == int(user_id))
        .filter(NotificationEvent.service_type == IN_APP_SERVICE_TYPE)
        .filter(NotificationEvent.cleared_at_utc.is_(None))
        .scalar()
    )
    return int(n or 0)


def list_in_app_notifications(
    db: Session,
    *,
    user_id: int,
    include_cleared: bool = True,
    limit: int = 50,
) -> list[NotificationEvent]:
    q = (
        db.query(NotificationEvent)
        .filter(NotificationEvent.user_id == int(user_id))
        .filter(NotificationEvent.service_type == IN_APP_SERVICE_TYPE)
    )
    if not include_cleared:
        q = q.filter(NotificationEvent.cleared_at_utc.is_(None))
    q = q.order_by(NotificationEvent.id.desc())
    try:
        lim = int(limit)
        if lim > 0:
            q = q.limit(min(lim, 200))
    except Exception:
        q = q.limit(50)
    return q.all()


def clear_in_app_unread(db: Session, *, user_id: int, when_utc: datetime | None = None) -> int:
    when = (when_utc or _now_utc_naive()).replace(tzinfo=None)
    q = (
        db.query(NotificationEvent)
        .filter(NotificationEvent.user_id == int(user_id))
        .filter(NotificationEvent.service_type == IN_APP_SERVICE_TYPE)
        .filter(NotificationEvent.cleared_at_utc.is_(None))
    )
    count = q.update({NotificationEvent.cleared_at_utc: when}, synchronize_session=False)
    db.commit()
    return int(count or 0)


# ---------------------- Users ----------------------


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    return db.query(User).filter(User.username == username).first()


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    e = normalize_email(email)
    if not e:
        return None
    return db.query(User).filter(func.lower(User.email) == e).first()


def get_user(db: Session, user_id: int) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def list_users(db: Session) -> list[User]:
    return db.query(User).order_by(User.username.asc()).all()


def is_manager_of(db: Session, *, manager_user_id: int, subordinate_user_id: int) -> bool:
    """Return True if `manager_user_id` is in the manager chain for `subordinate_user_id`."""

    if int(manager_user_id) == int(subordinate_user_id):
        return False

    visited: set[int] = set()
    cur = get_user(db, user_id=int(subordinate_user_id))
    while cur is not None and getattr(cur, "manager_id", None):
        try:
            mid = int(cur.manager_id)
        except Exception:
            break
        if mid == int(manager_user_id):
            return True
        if mid in visited:
            break
        visited.add(mid)
        cur = get_user(db, user_id=mid)
    return False


def list_subordinate_user_ids(db: Session, *, manager_user_id: int) -> list[int]:
    """Return all direct+indirect subordinate user IDs for a manager."""

    manager_id = int(manager_user_id)
    out: list[int] = []
    seen: set[int] = set()
    queue: list[int] = [manager_id]

    while queue:
        mid = queue.pop(0)
        rows = db.query(User.id).filter(User.manager_id == int(mid)).all()
        for (uid,) in rows:
            try:
                sid = int(uid)
            except Exception:
                continue
            if sid in seen:
                continue
            seen.add(sid)
            out.append(sid)
            queue.append(sid)
    return out


def _validate_manager_assignment(db: Session, *, user: User, manager_id: int | None) -> int | None:
    if manager_id is None:
        return None
    try:
        mid = int(manager_id)
    except Exception as e:
        raise ValueError("Invalid manager") from e
    if mid == int(user.id):
        raise ValueError("User cannot be their own manager")
    mgr = get_user(db, user_id=mid)
    if not mgr:
        raise ValueError("Manager not found")
    # Prevent cycles: the new manager cannot be a subordinate of this user.
    if is_manager_of(db, manager_user_id=int(user.id), subordinate_user_id=mid):
        raise ValueError("Invalid manager assignment (cycle)")
    return mid


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    is_admin: bool = False,
    email: str | None = None,
    manager_id: int | None = None,
) -> User:
    settings = get_settings()

    uname = (username or "").strip()
    if not uname:
        raise ValueError("Username is required")

    existing_username = db.query(User).filter(User.username == uname).first()
    if existing_username:
        raise ValueError("Username already exists")

    norm_email = normalize_email(email)

    # Email is required for all non-admin users.
    if not bool(is_admin) and not norm_email:
        raise ValueError("Email is required for non-admin users")

    if norm_email:
        existing = db.query(User).filter(func.lower(User.email) == norm_email).first()
        if existing:
            raise ValueError("Email already exists")

    mgr_id: int | None = None
    if manager_id is not None:
        try:
            mid = int(manager_id)
        except Exception as e:
            raise ValueError("Invalid manager") from e
        mgr = get_user(db, user_id=mid)
        if not mgr:
            raise ValueError("Manager not found")
        mgr_id = mid

    user = User(
        username=uname,
        email=norm_email,
        hashed_password=hash_password(password),
        is_admin=bool(is_admin),
        purge_days=int(settings.purge.default_days),
        theme=Theme.system.value,
        manager_id=mgr_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def delete_user(
    db: Session,
    *,
    user_id: int,
    reassign_completed_to_user_id: int | None = None,
) -> None:
    user = get_user(db, user_id)
    if not user:
        return

    # Optional: preserve completed tasks by reassigning them before deleting the user.
    if reassign_completed_to_user_id is not None:
        target = get_user(db, user_id=int(reassign_completed_to_user_id))
        if not target:
            raise ValueError("Reassignment target user not found")

        (
            db.query(Task)
            .filter(Task.user_id == int(user_id))
            .filter(Task.status == TaskStatus.completed)
            .update(
                {
                    Task.user_id: int(target.id),
                    # Detach from any hierarchy to avoid referencing tasks
                    # that will be deleted with the user.
                    Task.parent_task_id: None,
                },
                synchronize_session=False,
            )
        )
        db.commit()

    db.delete(user)
    db.commit()


def update_user_me(
    db: Session,
    *,
    user: User,
    username: Optional[str] = None,
    theme: Optional[str] = None,
    purge_days: Optional[int] = None,
    email: Optional[str] = None,
    current_password: Optional[str] = None,
    new_password: Optional[str] = None,
) -> User:
    if username is not None:
        uname = (username or "").strip()
        if not uname:
            raise ValueError("Username is required")
        if len(uname) > 64:
            raise ValueError("Username must be 64 characters or less")
        existing_username = db.query(User).filter(User.username == uname).filter(User.id != user.id).first()
        if existing_username:
            raise ValueError("Username already exists")
        user.username = uname

    if theme is not None:
        if theme not in {Theme.light.value, Theme.dark.value, Theme.system.value}:
            raise ValueError("Invalid theme")
        user.theme = theme

    if purge_days is not None:
        if purge_days < 1 or purge_days > 3650:
            raise ValueError("purge_days must be between 1 and 3650")
        user.purge_days = int(purge_days)

    # Email is required for all non-admin users.
    new_email = normalize_email(email) if email is not None else user.email
    if not bool(user.is_admin) and not new_email:
        raise ValueError("Email is required for non-admin users")

    if email is not None:
        norm = new_email
        if norm:
            existing = (
                db.query(User)
                .filter(func.lower(User.email) == norm)
                .filter(User.id != user.id)
                .first()
            )
            if existing:
                raise ValueError("Email already exists")
        user.email = norm

    if new_password is not None:
        if not current_password:
            raise ValueError("current_password is required to change password")
        if not verify_password(current_password, user.hashed_password):
            raise ValueError("current_password is incorrect")
        user.hashed_password = hash_password(new_password)

    db.add(user)
    db.commit()
    db.refresh(user)
    return user



def update_user_admin(
    db: Session,
    *,
    user_id: int,
    username: Optional[str] = None,
    is_admin: Optional[bool] = None,
    email: Optional[str] = None,
    manager_id: int | None | object = _UNSET,
    theme: Optional[str] = None,
    purge_days: Optional[int] = None,
    new_password: Optional[str] = None,
) -> Optional[User]:
    user = get_user(db, user_id)
    if not user:
        return None

    if username is not None:
        uname = (username or "").strip()
        if not uname:
            raise ValueError("Username is required")
        if len(uname) > 64:
            raise ValueError("Username must be 64 characters or less")
        existing_username = db.query(User).filter(User.username == uname).filter(User.id != user.id).first()
        if existing_username:
            raise ValueError("Username already exists")
        user.username = uname

    if theme is not None:
        if theme not in {Theme.light.value, Theme.dark.value, Theme.system.value}:
            raise ValueError("Invalid theme")
        user.theme = theme

    if purge_days is not None:
        if purge_days < 1 or purge_days > 3650:
            raise ValueError("purge_days must be between 1 and 3650")
        user.purge_days = int(purge_days)

    new_is_admin = bool(is_admin) if is_admin is not None else bool(user.is_admin)
    new_email = normalize_email(email) if email is not None else user.email

    # Email is required for all non-admin users.
    if not new_is_admin and not new_email:
        raise ValueError("Email is required for non-admin users")

    if email is not None:
        norm = new_email
        if norm:
            existing = (
                db.query(User)
                .filter(func.lower(User.email) == norm)
                .filter(User.id != user.id)
                .first()
            )
            if existing:
                raise ValueError("Email already exists")
        user.email = norm

    if is_admin is not None:
        user.is_admin = bool(is_admin)

    if new_password is not None:
        if len(new_password or "") < 8:
            raise ValueError("Password must be at least 8 characters")
        user.hashed_password = hash_password(new_password)

    if manager_id is not _UNSET:
        mid = _validate_manager_assignment(db, user=user, manager_id=(None if manager_id is None else int(manager_id)))
        user.manager_id = mid

    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ---------------------- Password reset ----------------------


def create_password_reset_token(
    db: Session,
    *,
    user: User,
    token: str,
    expires_at_utc: datetime,
) -> PasswordResetToken:
    tr = PasswordResetToken(
        user_id=user.id,
        token_hash=_hash_token(token),
        expires_at_utc=expires_at_utc.replace(tzinfo=None),
        used_at_utc=None,
    )
    db.add(tr)
    db.commit()
    db.refresh(tr)
    return tr


def get_password_reset_token(db: Session, *, token: str) -> Optional[PasswordResetToken]:
    th = _hash_token(token)
    return db.query(PasswordResetToken).options(joinedload(PasswordResetToken.user)).filter(PasswordResetToken.token_hash == th).first()


def verify_password_reset_token(db: Session, *, token: str, now_utc: datetime) -> Optional[PasswordResetToken]:
    tr = get_password_reset_token(db, token=token)
    if not tr:
        return None
    if tr.used_at_utc is not None:
        return None
    if tr.expires_at_utc < now_utc.replace(tzinfo=None):
        return None
    return tr


def consume_password_reset_token(db: Session, *, token: str, new_password: str, now_utc: datetime) -> bool:
    tr = verify_password_reset_token(db, token=token, now_utc=now_utc)
    if not tr:
        return False

    user = tr.user
    user.hashed_password = hash_password(new_password)
    tr.used_at_utc = now_utc.replace(tzinfo=None)

    db.add(user)
    db.add(tr)
    db.commit()
    return True


# ---------------------- Tags ----------------------


def _normalize_tag_name(tag: str) -> str:
    return tag.strip().lower()


def get_or_create_tags(db: Session, tag_names: Iterable[str]) -> list[Tag]:
    tags: list[Tag] = []
    for raw in tag_names:
        name = _normalize_tag_name(raw)
        if not name:
            continue
        existing = db.query(Tag).filter(func.lower(Tag.name) == name).first()
        if existing:
            tags.append(existing)
            continue
        t = Tag(name=name)
        db.add(t)
        db.flush()
        tags.append(t)
    return tags


def list_tags_for_user(db: Session, *, user: User) -> list[Tag]:
    # Only tags that appear on the user's tasks.
    q = (
        db.query(Tag)
        .join(Tag.tasks)
        .filter(Task.user_id == user.id)
        .distinct()
        .order_by(Tag.name.asc())
    )
    return q.all()


# ---------------------- Tasks ----------------------


def _apply_recurrence_fields(
    *,
    recurrence_type: str,
    recurrence_interval: Optional[str],
    recurrence_times: Optional[str],
) -> tuple[str, Optional[int], Optional[str]]:
    try:
        rtype = RecurrenceType(recurrence_type)
    except Exception as e:
        raise ValueError("Invalid recurrence_type") from e

    if rtype == RecurrenceType.post_completion:
        if not recurrence_interval:
            raise ValueError("recurrence_interval is required for this recurrence type")
        seconds = parse_duration_to_seconds(recurrence_interval)
        return rtype.value, seconds, None

    if rtype == RecurrenceType.fixed_clock:
        # Fixed clock scheduling supports two formats:
        #   1) Legacy interval: "8h", "1d", "2 weeks"...
        #   2) Fixed calendar rule: "Every Tuesday", "Mon, Wed, Fri", "10th of every month", "First Monday", "January 5"
        if (recurrence_interval is None or not str(recurrence_interval).strip()) and (
            recurrence_times is None or not str(recurrence_times).strip()
        ):
            raise ValueError("recurrence_interval is required for fixed_clock")

        # Prefer recurrence_interval for backwards compatibility with existing clients/UI.
        raw = (recurrence_interval or "").strip() if recurrence_interval is not None else ""

        if raw:
            try:
                seconds = parse_duration_to_seconds(raw)
                return rtype.value, seconds, None
            except RecurrenceError:
                # Not a duration; treat it as a fixed calendar rule.
                rule_canonical = parse_fixed_calendar_rule(raw)
                return rtype.value, None, rule_canonical

        # Fallback: allow supplying the rule in recurrence_times for API clients.
        rule_canonical = parse_fixed_calendar_rule(str(recurrence_times))
        return rtype.value, None, rule_canonical

    if rtype == RecurrenceType.multi_slot_daily:
        if not recurrence_times:
            raise ValueError("recurrence_times is required for multi_slot_daily")
        canonical = parse_times_csv(recurrence_times)
        return rtype.value, None, canonical

    # none
    return RecurrenceType.none.value, None, None


def create_task(
    db: Session,
    *,
    owner: User,
    name: str,
    task_type: str,
    due_date: datetime | None,
    description: Optional[str] = None,
    url: Optional[str] = None,
    recurrence_type: str = RecurrenceType.none.value,
    recurrence_interval: Optional[str] = None,
    recurrence_times: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    parent_task_id: int | None = None,
    assigned_by_user_id: int | None = None,
    send_notifications: bool = True,
) -> Task:
    # Allow tasks with no due date. If omitted, use creation time.
    if due_date is None:
        due_date = datetime.now(timezone.utc)

    due_utc = normalize_datetime_to_utc_naive(due_date)
    rtype, interval_seconds, times_canonical = _apply_recurrence_fields(
        recurrence_type=recurrence_type,
        recurrence_interval=recurrence_interval,
        recurrence_times=recurrence_times,
    )

    if parent_task_id is not None:
        parent = get_task(db, task_id=int(parent_task_id))
        if not parent:
            raise ValueError("Parent task not found")
        if int(parent.user_id) != int(owner.id):
            raise ValueError("Parent task must belong to the same user")

    task = Task(
        user_id=owner.id,
        parent_task_id=(int(parent_task_id) if parent_task_id is not None else None),
        assigned_by_user_id=(int(assigned_by_user_id) if assigned_by_user_id is not None else None),
        name=name,
        task_type=task_type,
        description=description,
        url=url,
        due_date_utc=due_utc,
        recurrence_type=rtype,
        recurrence_interval_seconds=interval_seconds,
        recurrence_times=times_canonical,
        status=TaskStatus.active,
    )

    if tags:
        task.tags = get_or_create_tags(db, tags)

    db.add(task)
    db.commit()
    db.refresh(task)

    if send_notifications:
        # Task notifications (tag-based) are best-effort; failures should not block
        # task creation.
        try:
            notify_task_event(db, task=task, event_type=EVENT_CREATED)
        except Exception:
            logger.exception("Failed to send task-created notification")

    # Always create an in-app assignment notification for the assignee.
    try:
        if assigned_by_user_id is not None and int(assigned_by_user_id) != int(owner.id):
            assigner = get_user(db, user_id=int(assigned_by_user_id))
            assigner_name = assigner.username if assigner else f"user:{int(assigned_by_user_id)}"
            create_in_app_notification(
                db,
                user_id=int(owner.id),
                task_id=int(task.id),
                event_type="assigned",
                title=f"New task assigned: {task.name}",
                message=f"Assigned by {assigner_name}",
            )
            db.commit()
    except Exception:
        logger.exception("Failed to create in-app assignment notification")
    return task


def get_task(db: Session, *, task_id: int) -> Optional[Task]:
    return (
        db.query(Task)
        .options(joinedload(Task.tags))
        .filter(Task.id == task_id)
        .first()
    )


class OpenSubtasksError(Exception):
    """Raised when attempting to archive a task that has open descendant tasks."""

    def __init__(self, open_tasks: list[Task]):
        self.open_tasks = list(open_tasks or [])
        super().__init__("Task has open subtasks")


def list_descendant_tasks(db: Session, *, root_task_id: int) -> list[Task]:
    """Return all descendant tasks (children, grandchildren, ...) of `root_task_id`.

    Breadth-first search using repeated IN() queries.
    """

    root_id = int(root_task_id)
    out: list[Task] = []
    seen: set[int] = {root_id}
    frontier: list[int] = [root_id]

    while frontier:
        rows = (
            db.query(Task)
            .options(joinedload(Task.tags), joinedload(Task.user))
            .filter(Task.parent_task_id.in_([int(x) for x in frontier]))
            .all()
        )
        frontier = []
        for t in rows:
            tid = int(t.id)
            if tid in seen:
                continue
            seen.add(tid)
            out.append(t)
            frontier.append(tid)
    return out


def list_open_descendant_tasks(db: Session, *, root_task_id: int) -> list[Task]:
    return [t for t in list_descendant_tasks(db, root_task_id=int(root_task_id)) if t.status == TaskStatus.active]


def is_following_task(db: Session, *, follower_user_id: int, task_id: int) -> bool:
    row = (
        db.query(TaskFollow.id)
        .filter(TaskFollow.follower_user_id == int(follower_user_id))
        .filter(TaskFollow.task_id == int(task_id))
        .first()
    )
    return bool(row)


def follow_task(db: Session, *, follower: User, task: Task) -> TaskFollow:
    if int(task.user_id) == int(follower.id):
        raise PermissionError("Cannot follow your own task")

    if not follower.is_admin and not is_manager_of(db, manager_user_id=int(follower.id), subordinate_user_id=int(task.user_id)):
        raise PermissionError("Not allowed")

    existing = (
        db.query(TaskFollow)
        .filter(TaskFollow.follower_user_id == int(follower.id))
        .filter(TaskFollow.task_id == int(task.id))
        .first()
    )
    if existing:
        return existing

    tf = TaskFollow(
        follower_user_id=int(follower.id),
        task_id=int(task.id),
        created_at=_now_utc_naive(),
    )
    db.add(tf)
    db.commit()
    db.refresh(tf)
    return tf


def unfollow_task(db: Session, *, follower: User, task: Task) -> bool:
    q = (
        db.query(TaskFollow)
        .filter(TaskFollow.follower_user_id == int(follower.id))
        .filter(TaskFollow.task_id == int(task.id))
    )
    row = q.first()
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def _notify_task_followers_in_app(db: Session, *, task: Task, event_type: str) -> int:
    """Create in-app notifications for followers of `task`.

    The task owner is excluded.
    """

    rows = (
        db.query(TaskFollow.follower_user_id)
        .filter(TaskFollow.task_id == int(task.id))
        .all()
    )
    if not rows:
        return 0

    et = str(event_type or "").strip().lower()
    if et not in {"updated", "completed", "deleted"}:
        et = "updated"

    if et == "completed":
        title = f"Task completed: {task.name}"
    elif et == "deleted":
        title = f"Task deleted: {task.name}"
    else:
        title = f"Task updated: {task.name}"

    msg = f"Owner: {getattr(task.user, 'username', task.user_id)}"
    created = 0
    for (uid,) in rows:
        try:
            fid = int(uid)
        except Exception:
            continue
        if fid == int(task.user_id):
            continue
        create_in_app_notification(
            db,
            user_id=fid,
            task_id=int(task.id),
            event_type=et,
            title=title,
            message=msg,
        )
        created += 1

    if created:
        db.commit()
    return created


def clone_task_tree(
    db: Session,
    *,
    source_task: Task,
    new_owner_user_id: int | None = None,
    new_parent_task_id: int | None = None,
    due_date_delta: timedelta | None = None,
    name_suffix: str = " (Copy)",
) -> Task:
    """Clone a task and all descendants.

    All cloned tasks are reset to active and timestamps are cleared.
    """

    src = source_task
    owner_id = int(new_owner_user_id) if new_owner_user_id is not None else int(src.user_id)
    delta = due_date_delta or timedelta(0)

    def _clone_node(node: Task, parent_new_id: int | None) -> Task:
        ndue = None
        if node.due_date_utc is not None:
            try:
                ndue = (node.due_date_utc + delta).replace(tzinfo=None)
            except Exception:
                ndue = node.due_date_utc

        new_name = str(node.name or "").strip()
        if node.id == src.id and name_suffix:
            # Only suffix the root task name.
            new_name = (new_name + name_suffix)[:255]

        nt = Task(
            user_id=owner_id,
            parent_task_id=parent_new_id,
            assigned_by_user_id=(int(node.assigned_by_user_id) if getattr(node, "assigned_by_user_id", None) else None),
            name=new_name,
            task_type=node.task_type,
            description=node.description,
            url=node.url,
            due_date_utc=ndue,
            recurrence_type=node.recurrence_type,
            recurrence_interval_seconds=node.recurrence_interval_seconds,
            recurrence_times=node.recurrence_times,
            status=TaskStatus.active,
            completed_at_utc=None,
            deleted_at_utc=None,
        )
        try:
            nt.tags = list(node.tags or [])
        except Exception:
            nt.tags = []
        db.add(nt)
        db.flush()  # assign id

        # Clone children
        kids = (
            db.query(Task)
            .options(joinedload(Task.tags))
            .filter(Task.parent_task_id == int(node.id))
            .order_by(Task.id.asc())
            .all()
        )
        for ch in kids:
            _clone_node(ch, int(nt.id))
        return nt

    root_parent_id = int(new_parent_task_id) if new_parent_task_id is not None else (int(src.parent_task_id) if src.parent_task_id else None)
    new_root = _clone_node(src, root_parent_id)
    db.commit()
    db.refresh(new_root)
    return new_root


def _tasks_base_query(
    db: Session,
    *,
    current_user: User,
    include_archived: bool,
    search: Optional[str],
    tag: Optional[str],
    user_id: Optional[int],
    task_type: Optional[str],
    status: Optional[str],
    include_assigned_by_me: bool = False,
):
    """Build a filtered Task query without ordering/limit/offset."""
    q = db.query(Task)

    # Permissions and user scoping
    if current_user.is_admin:
        if user_id:
            q = q.filter(Task.user_id == int(user_id))
    else:
        if include_assigned_by_me:
            subs = list_subordinate_user_ids(db, manager_user_id=int(current_user.id))
            if subs:
                q = q.filter(
                    or_(
                        Task.user_id == int(current_user.id),
                        and_(
                            Task.assigned_by_user_id == int(current_user.id),
                            Task.user_id.in_([int(x) for x in subs]),
                        ),
                    )
                )
            else:
                q = q.filter(Task.user_id == int(current_user.id))
        else:
            q = q.filter(Task.user_id == int(current_user.id))

    # Status filtering
    if status:
        if status == "archived":
            q = q.filter(Task.status.in_([TaskStatus.completed, TaskStatus.deleted]))
        else:
            try:
                st = TaskStatus(status)
            except Exception as e:
                raise ValueError("Invalid status") from e
            q = q.filter(Task.status == st)
    else:
        if not include_archived:
            q = q.filter(Task.status == TaskStatus.active)

    if task_type:
        q = q.filter(Task.task_type == task_type)

    joined_tags = False
    if tag:
        tnorm = _normalize_tag_name(tag)
        q = q.join(Task.tags).filter(func.lower(Tag.name) == tnorm)
        joined_tags = True

    if search:
        term = (search or "").strip().lower()
        if term:
            pat = f"%{term}%"
            if not joined_tags:
                q = q.outerjoin(Task.tags)
            q = q.filter(
                or_(
                    func.lower(Task.name).like(pat),
                    func.lower(Task.task_type).like(pat),
                    func.lower(Task.description).like(pat),
                    func.lower(Task.url).like(pat),
                    func.lower(Tag.name).like(pat),
                )
            ).distinct()

    return q


def count_tasks(
    db: Session,
    *,
    current_user: User,
    include_archived: bool = False,
    search: Optional[str] = None,
    tag: Optional[str] = None,
    user_id: Optional[int] = None,
    task_type: Optional[str] = None,
    status: Optional[str] = None,
    include_assigned_by_me: bool = False,
) -> int:
    """Count tasks matching the same filters as list_tasks()."""
    q = _tasks_base_query(
        db,
        current_user=current_user,
        include_archived=include_archived,
        search=search,
        tag=tag,
        user_id=user_id,
        task_type=task_type,
        status=status,
        include_assigned_by_me=include_assigned_by_me,
    )
    try:
        n = q.with_entities(func.count(func.distinct(Task.id))).scalar()
        return int(n or 0)
    except Exception:
        # Fall back to a safe, if slightly slower, count.
        return int(len(q.all()))


def list_tasks(
    db: Session,
    *,
    current_user: User,
    include_archived: bool = False,
    search: Optional[str] = None,
    tag: Optional[str] = None,
    user_id: Optional[int] = None,
    task_type: Optional[str] = None,
    status: Optional[str] = None,
    sort: str = "due_date",
    include_assigned_by_me: bool = False,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
) -> list[Task]:
    # Base query
    q = _tasks_base_query(
        db,
        current_user=current_user,
        include_archived=include_archived,
        search=search,
        tag=tag,
        user_id=user_id,
        task_type=task_type,
        status=status,
        include_assigned_by_me=include_assigned_by_me,
    ).options(joinedload(Task.tags), joinedload(Task.user))

    # Sorting
    desc = False
    key = (sort or "").strip()
    if key.startswith("-"):
        desc = True
        key = key[1:]

    if key in {"task_type", "type"}:
        primary = Task.task_type
        secondary = Task.due_date_utc
    elif key in {"name"}:
        primary = Task.name
        secondary = Task.due_date_utc
    elif key in {"archived_at"}:
        # Archived sort: use completed_at_utc/deleted_at_utc where available.
        # Fall back to updated_at.
        # Note: SQLite lacks GREATEST across NULLs reliably; order by updated_at.
        primary = Task.updated_at
        secondary = Task.due_date_utc
    else:
        primary = Task.due_date_utc
        secondary = Task.task_type

    if desc:
        q = q.order_by(primary.desc(), secondary.desc())
    else:
        q = q.order_by(primary.asc(), secondary.asc())

    if offset is not None:
        try:
            o = int(offset)
            if o > 0:
                q = q.offset(o)
        except Exception:
            pass

    if limit is not None:
        try:
            l = int(limit)
            if l > 0:
                q = q.limit(l)
        except Exception:
            pass

    return q.all()


def update_task(
    db: Session,
    *,
    task: Task,
    current_user: User,
    name: Optional[str] = None,
    task_type: Optional[str] = None,
    description: Optional[str] = None,
    url: Optional[str] = None,
    due_date: Optional[datetime] = None,
    recurrence_type: Optional[str] = None,
    recurrence_interval: Optional[str] = None,
    recurrence_times: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
) -> Task:
    if not current_user.is_admin and task.user_id != current_user.id:
        raise PermissionError("Not allowed")

    # Capture old tags so updates that remove a subscribed tag can still trigger
    # a notification.
    try:
        old_tag_ids = {int(t.id) for t in (task.tags or [])}
    except Exception:
        old_tag_ids = set()

    if name is not None:
        task.name = name
    if task_type is not None:
        task.task_type = task_type
    if description is not None:
        task.description = description
    if url is not None:
        task.url = url
    if due_date is not None:
        task.due_date_utc = normalize_datetime_to_utc_naive(due_date)

    if recurrence_type is not None:
        rtype, interval_seconds, times_canonical = _apply_recurrence_fields(
            recurrence_type=recurrence_type,
            recurrence_interval=recurrence_interval,
            recurrence_times=recurrence_times,
        )
        task.recurrence_type = rtype
        task.recurrence_interval_seconds = interval_seconds
        task.recurrence_times = times_canonical

    if tags is not None:
        task.tags = get_or_create_tags(db, tags)

    db.add(task)
    db.commit()
    db.refresh(task)

    try:
        new_tag_ids = {int(t.id) for t in (task.tags or [])}
    except Exception:
        new_tag_ids = set()
    relevant = set(old_tag_ids) | set(new_tag_ids)

    try:
        notify_task_event(db, task=task, event_type=EVENT_UPDATED, relevant_tag_ids=relevant)
    except Exception:
        logger.exception("Failed to send task-updated notification")

    # In-app follow notifications (manager/subordinate).
    try:
        _notify_task_followers_in_app(db, task=task, event_type="updated")
    except Exception:
        logger.exception("Failed to create in-app follower notifications")
    return task


def soft_delete_task(
    db: Session,
    *,
    task: Task,
    current_user: User,
    when_utc: datetime,
    cascade_subtasks: bool = False,
) -> Task:
    if not current_user.is_admin and task.user_id != current_user.id:
        raise PermissionError("Not allowed")

    open_desc = list_open_descendant_tasks(db, root_task_id=int(task.id))
    if open_desc and not cascade_subtasks:
        raise OpenSubtasksError(open_desc)

    if open_desc and cascade_subtasks:
        # Archive open descendants first.
        for ch in open_desc:
            ch.status = TaskStatus.deleted
            ch.deleted_at_utc = when_utc
            db.add(ch)
        db.commit()
        # Best-effort notifications for cascaded descendants.
        for ch in open_desc:
            try:
                notify_task_event(db, task=ch, event_type=EVENT_ARCHIVED)
            except Exception:
                logger.exception("Failed to send cascaded subtask-archived notification")
            try:
                _notify_task_followers_in_app(db, task=ch, event_type="deleted")
            except Exception:
                logger.exception("Failed to create cascaded follower notifications")

    task.status = TaskStatus.deleted
    task.deleted_at_utc = when_utc

    db.add(task)
    db.commit()
    db.refresh(task)

    try:
        notify_task_event(db, task=task, event_type=EVENT_ARCHIVED)
    except Exception:
        logger.exception("Failed to send task-archived notification")

    try:
        _notify_task_followers_in_app(db, task=task, event_type="deleted")
    except Exception:
        logger.exception("Failed to create in-app follower notifications")
    return task


def restore_task(db: Session, *, task: Task, current_user: User) -> Task:
    if not current_user.is_admin and task.user_id != current_user.id:
        raise PermissionError("Not allowed")

    task.status = TaskStatus.active
    task.completed_at_utc = None
    task.deleted_at_utc = None

    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def complete_task(
    db: Session,
    *,
    task: Task,
    current_user: User,
    when_utc: datetime,
    cascade_subtasks: bool = False,
    spawn_recurrence: bool = True,
) -> tuple[Task, Optional[Task]]:
    if not current_user.is_admin and task.user_id != current_user.id:
        raise PermissionError("Not allowed")

    open_desc = list_open_descendant_tasks(db, root_task_id=int(task.id))
    if open_desc and not cascade_subtasks:
        raise OpenSubtasksError(open_desc)

    if open_desc and cascade_subtasks:
        # Complete open descendants first, but do not spawn recurrence tasks for
        # them. Parent recurrence (if any) will rebuild the child tree.
        for ch in open_desc:
            ch.status = TaskStatus.completed
            ch.completed_at_utc = when_utc
            db.add(ch)
        db.commit()
        for ch in open_desc:
            try:
                notify_task_event(db, task=ch, event_type=EVENT_COMPLETED)
            except Exception:
                logger.exception("Failed to send cascaded subtask-completed notification")
            try:
                _notify_task_followers_in_app(db, task=ch, event_type="completed")
            except Exception:
                logger.exception("Failed to create cascaded follower notifications")

    # Mark complete
    task.status = TaskStatus.completed
    task.completed_at_utc = when_utc

    spawned: Optional[Task] = None
    next_due = None
    if spawn_recurrence:
        try:
            next_due = compute_next_due_utc(task, when_utc)
        except RecurrenceError:
            next_due = None

    if next_due is not None:
        spawned = Task(
            user_id=task.user_id,
            parent_task_id=(int(task.parent_task_id) if getattr(task, "parent_task_id", None) else None),
            assigned_by_user_id=(int(task.assigned_by_user_id) if getattr(task, "assigned_by_user_id", None) else None),
            name=task.name,
            task_type=task.task_type,
            description=task.description,
            url=task.url,
            due_date_utc=next_due,
            recurrence_type=task.recurrence_type,
            recurrence_interval_seconds=task.recurrence_interval_seconds,
            recurrence_times=task.recurrence_times,
            status=TaskStatus.active,
        )
        spawned.tags = list(task.tags)
        db.add(spawned)

    db.add(task)
    db.commit()
    db.refresh(task)
    if spawned:
        db.refresh(spawned)

    try:
        notify_task_event(db, task=task, event_type=EVENT_COMPLETED)
    except Exception:
        logger.exception("Failed to send task-completed notification")

    try:
        _notify_task_followers_in_app(db, task=task, event_type="completed")
    except Exception:
        logger.exception("Failed to create in-app follower notifications")

    if spawned is not None:
        try:
            notify_task_event(db, task=spawned, event_type=EVENT_CREATED)
        except Exception:
            logger.exception("Failed to send recurrence task-created notification")

        # Rebuild full child task tree when recurring.
        try:
            # Shift all descendant due dates by the delta between the old and new
            # parent due dates.
            try:
                delta = spawned.due_date_utc - task.due_date_utc
            except Exception:
                delta = timedelta(0)

            children = (
                db.query(Task)
                .options(joinedload(Task.tags))
                .filter(Task.parent_task_id == int(task.id))
                .order_by(Task.id.asc())
                .all()
            )
            for ch in children:
                clone_task_tree(
                    db,
                    source_task=ch,
                    new_owner_user_id=int(spawned.user_id),
                    new_parent_task_id=int(spawned.id),
                    due_date_delta=delta,
                    name_suffix="",
                )
        except Exception:
            logger.exception("Failed to rebuild subtask tree for recurring task")

    return task, spawned


# ---------------------- Purge ----------------------


def purge_archived_tasks(db: Session) -> int:
    """Permanently delete archived tasks older than each user's purge window."""
    now = datetime.utcnow().replace(tzinfo=None)
    users = db.query(User).all()
    total_deleted = 0
    for u in users:
        cutoff = now - timedelta(days=int(u.purge_days))
        q = (
            db.query(Task)
            .filter(Task.user_id == u.id)
            .filter(Task.status.in_([TaskStatus.completed, TaskStatus.deleted]))
            .filter(or_(Task.completed_at_utc < cutoff, Task.deleted_at_utc < cutoff))
        )
        # Bulk delete
        count = q.delete(synchronize_session=False)
        total_deleted += int(count or 0)

        # Purge cleared in-app notifications on the same retention window.
        try:
            ncount = (
                db.query(NotificationEvent)
                .filter(NotificationEvent.user_id == int(u.id))
                .filter(NotificationEvent.service_type == IN_APP_SERVICE_TYPE)
                .filter(NotificationEvent.cleared_at_utc.is_not(None))
                .filter(NotificationEvent.cleared_at_utc < cutoff)
                .delete(synchronize_session=False)
            )
            total_deleted += int(ncount or 0)
        except Exception:
            # If the notification schema isn't present yet, ignore.
            pass
    db.commit()
    return total_deleted
