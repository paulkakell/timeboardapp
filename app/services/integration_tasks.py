"""Owner-scoped task operations shared by GPT Actions and MCP."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from .. import crud
from ..models import Task, TaskStatus, User
from ..schemas import TaskCreate, TaskUpdate
from ..urls import safe_task_url


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ActionCreate(TaskCreate):
    model_config = ConfigDict(extra="forbid")
    description: str | None = Field(default=None, max_length=1000)
    due_date: AwareDatetime | None = None
    parent_task_id: int | None = Field(default=None, ge=1)
    tags: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        default_factory=list, max_length=20
    )
    recurrence_interval: str | None = Field(default=None, max_length=255)
    recurrence_times: str | None = Field(default=None, max_length=255)


class ActionUpdate(TaskUpdate):
    model_config = ConfigDict(extra="forbid")
    description: str | None = Field(default=None, max_length=1000)
    due_date: AwareDatetime | None = None
    tags: list[Annotated[str, Field(min_length=1, max_length=128)]] | None = Field(
        default=None, max_length=20
    )
    recurrence_interval: str | None = Field(default=None, max_length=255)
    recurrence_times: str | None = Field(default=None, max_length=255)


class ActionTask(BaseModel):
    id: int
    parent_task_id: int | None
    name: str
    task_type: str
    description: str
    url: str
    due_date_utc: datetime
    status: str
    recurrence_type: str
    tags: list[str]
    content_truncated: bool


class TaskPage(BaseModel):
    items: list[ActionTask]
    next_offset: int | None


class Completion(BaseModel):
    completed_task: ActionTask
    spawned_task: ActionTask | None


def _out(task: Task) -> ActionTask:
    tags = sorted(t.name for t in task.tags)
    description = task.description or ""
    return ActionTask(
        id=task.id,
        parent_task_id=task.parent_task_id,
        name=task.name[:255],
        task_type=task.task_type[:128],
        description=description[:1000],
        url=safe_task_url(task.url),
        due_date_utc=task.due_date_utc.replace(tzinfo=timezone.utc),
        status=task.status,
        recurrence_type=task.recurrence_type,
        tags=[t[:128] for t in tags[:20]],
        content_truncated=len(description) > 1000
        or len(tags) > 20
        or any(len(t) > 128 for t in tags)
        or len(task.name) > 255
        or len(task.task_type) > 128,
    )


def _owner_view(user: User):
    # Do not mutate the ORM user or inherit admin/manager cross-user permissions.
    return SimpleNamespace(id=user.id, is_admin=False)


def _task(db: Session, user: User, task_id: int) -> Task:
    item = db.query(Task).filter(Task.id == task_id, Task.user_id == user.id).first()
    if not item:
        raise HTTPException(404, "Task not found")
    return item


def _error(exc: Exception):
    if isinstance(exc, crud.OpenSubtasksError):
        raise HTTPException(
            409,
            {
                "code": "open_subtasks",
                "message": "Close subtasks first or confirm cascading in the website",
            },
        ) from None
    raise HTTPException(400, str(exc)) from None


def list_tasks(
    search: str | None = None,
    status: Literal["active", "completed", "deleted", "archived"] | None = None,
    include_archived: bool = False,
    limit: int = 10,
    offset: int = 0,
    *,
    db: Session,
    user: User,
):
    rows = crud.list_tasks(
        db,
        current_user=_owner_view(user),
        search=search,
        status=status,
        include_archived=include_archived,
        limit=limit + 1,
        offset=offset,
    )
    # Bound the serialized response even when stored strings contain escapes.
    items = []
    size = 64
    for row in rows[:limit]:
        item = _out(row)
        item_size = (
            len(json.dumps(item.model_dump(mode="json"), ensure_ascii=False)) + 2
        )
        if items and size + item_size > 80000:
            break
        items.append(item)
        size += item_size
    return TaskPage(
        items=items, next_offset=offset + len(items) if len(rows) > len(items) else None
    )


def summary(*, db: Session, user: User):
    return crud.get_task_summary_counts(db, current_user=_owner_view(user))


def get_task(task_id: int, *, db: Session, user: User):
    return _out(_task(db, user, task_id))


def create_task(payload: ActionCreate, *, db: Session, user: User, audit=None):
    try:
        return _out(
            crud.create_task(
                db, owner=user, integration_audit=audit, **payload.model_dump()
            )
        )
    except ValueError as exc:
        _error(exc)


def update_task(task_id: int, payload: ActionUpdate, *, db: Session, user: User):
    task = _task(db, user, task_id)
    changes = payload.model_dump(exclude_unset=True)
    for key in ("description", "url"):
        if key in changes and changes[key] is None:
            changes[key] = ""
    if (
        "recurrence_type" not in changes
        and {"recurrence_interval", "recurrence_times"} & changes.keys()
    ):
        changes["recurrence_type"] = task.recurrence_type
    try:
        return _out(
            crud.update_task(db, task=task, current_user=_owner_view(user), **changes)
        )
    except ValueError as exc:
        _error(exc)


def archive_task(task_id: int, *, db: Session, user: User):
    try:
        return _out(
            crud.soft_delete_task(
                db,
                task=_task(db, user, task_id),
                current_user=_owner_view(user),
                when_utc=_now(),
            )
        )
    except (crud.OpenSubtasksError, ValueError) as exc:
        _error(exc)


def complete_task(task_id: int, *, db: Session, user: User):
    task = _task(db, user, task_id)
    if task.status != TaskStatus.active:
        raise HTTPException(409, "Task is not active; do not retry completion")
    try:
        done, spawned = crud.complete_task(
            db, task=task, current_user=_owner_view(user), when_utc=_now()
        )
        return Completion(
            completed_task=_out(done), spawned_task=_out(spawned) if spawned else None
        )
    except (crud.OpenSubtasksError, ValueError) as exc:
        _error(exc)


def restore_task(task_id: int, *, db: Session, user: User):
    return _out(
        crud.restore_task(
            db, task=_task(db, user, task_id), current_user=_owner_view(user)
        )
    )
