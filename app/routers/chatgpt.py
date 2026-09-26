"""Least-privilege GPT Actions interface; never accepts an administrator JWT."""
from __future__ import annotations

import json
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from .. import crud
from ..auth import credential_fingerprint, get_current_user_api
from ..db import get_db
from ..models import IntegrationToken, Task, TaskStatus, User
from ..schemas import TaskCreate, TaskSummaryOut, TaskUpdate
from ..urls import safe_task_url

router = APIRouter(prefix="/api/chatgpt", tags=["ChatGPT Actions"])
tokens_router = APIRouter(prefix="/api/integrations/tokens", tags=["integration credentials"])
bearer = HTTPBearer(scheme_name="TimeboardIntegrationToken", auto_error=False)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="Private ChatGPT", min_length=1, max_length=128)
    expires_days: int = Field(default=30, ge=1, le=90)
    allow_write: bool = False


class TokenInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    scopes: str
    expires_at: datetime
    revoked: bool


class TokenIssued(TokenInfo):
    token: str


@tokens_router.post("", response_model=TokenIssued, status_code=201)
def issue_token(payload: TokenCreate, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user_api)):
    count = db.query(IntegrationToken).filter(IntegrationToken.user_id == user.id, IntegrationToken.revoked.is_(False), IntegrationToken.expires_at > _now()).count()
    if count >= 20:
        raise HTTPException(409, "Revoke an existing integration token before creating another")
    raw = "tba_" + secrets.token_urlsafe(32)
    item = IntegrationToken(user_id=user.id, name=payload.name, token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                            scopes="tasks:read tasks:write" if payload.allow_write else "tasks:read",
                            expires_at=_now() + timedelta(days=payload.expires_days),
                            credential_fingerprint=credential_fingerprint(user))
    db.add(item)
    db.commit()
    db.refresh(item)
    response.headers["Cache-Control"] = "no-store"
    return TokenIssued(**TokenInfo.model_validate(item).model_dump(), token=raw)


@tokens_router.get("", response_model=list[TokenInfo])
def list_tokens(db: Session = Depends(get_db), user: User = Depends(get_current_user_api)):
    return db.query(IntegrationToken).filter(IntegrationToken.user_id == user.id).order_by(IntegrationToken.id.desc()).limit(200).all()


@tokens_router.delete("/{token_id}", status_code=204)
def revoke_token(token_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user_api)):
    item = db.query(IntegrationToken).filter(IntegrationToken.id == token_id, IntegrationToken.user_id == user.id).first()
    if not item:
        raise HTTPException(404, "Integration token not found")
    item.revoked = True
    db.commit()
    return Response(status_code=204)


def integration_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> User:
    error = HTTPException(401, "Invalid or expired integration token", headers={"WWW-Authenticate": "Bearer"})
    if not credentials or credentials.scheme.lower() != "bearer" or not credentials.credentials.startswith("tba_") or len(credentials.credentials) > 128:
        raise error
    digest = hashlib.sha256(credentials.credentials.encode()).hexdigest()
    item = db.query(IntegrationToken).filter(IntegrationToken.token_hash == digest, IntegrationToken.revoked.is_(False), IntegrationToken.expires_at > _now()).first()
    user = db.get(User, item.user_id) if item else None
    if not user or not hmac.compare_digest(item.credential_fingerprint, credential_fingerprint(user)):
        raise error
    required = "tasks:read" if request.method == "GET" else "tasks:write"
    if required not in item.scopes.split():
        raise HTTPException(403, "This integration token is read-only")
    return user


class ActionCreate(TaskCreate):
    model_config = ConfigDict(extra="forbid")
    description: str | None = Field(default=None, max_length=1000)
    due_date: AwareDatetime | None = None
    parent_task_id: int | None = Field(default=None, ge=1)
    tags: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(default_factory=list, max_length=20)
    recurrence_interval: str | None = Field(default=None, max_length=255)
    recurrence_times: str | None = Field(default=None, max_length=255)


class ActionUpdate(TaskUpdate):
    model_config = ConfigDict(extra="forbid")
    description: str | None = Field(default=None, max_length=1000)
    due_date: AwareDatetime | None = None
    tags: list[Annotated[str, Field(min_length=1, max_length=128)]] | None = Field(default=None, max_length=20)
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
    return ActionTask(id=task.id, parent_task_id=task.parent_task_id, name=task.name[:255], task_type=task.task_type[:128],
                      description=description[:1000], url=safe_task_url(task.url), due_date_utc=task.due_date_utc.replace(tzinfo=timezone.utc),
                      status=task.status, recurrence_type=task.recurrence_type, tags=[t[:128] for t in tags[:20]],
                      content_truncated=len(description) > 1000 or len(tags) > 20 or any(len(t) > 128 for t in tags) or len(task.name) > 255 or len(task.task_type) > 128)


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
        raise HTTPException(409, {"code": "open_subtasks", "message": "Close subtasks first or confirm cascading in the website"}) from None
    raise HTTPException(400, str(exc)) from None


@router.get("/tasks", response_model=TaskPage, operation_id="timeboard_list_tasks", summary="List or search your tasks")
def list_tasks(search: str | None = Query(None, max_length=255), status: Literal["active", "completed", "deleted", "archived"] | None = None,
               include_archived: bool = False, limit: int = Query(10, ge=1, le=10), offset: int = Query(0, ge=0, le=1000000),
               db: Session = Depends(get_db), user: User = Depends(integration_user)):
    rows = crud.list_tasks(db, current_user=_owner_view(user), search=search, status=status,
                           include_archived=include_archived, limit=limit + 1, offset=offset)
    # Bound the serialized response even when stored strings contain escapes.
    items = []
    size = 64
    for row in rows[:limit]:
        item = _out(row)
        item_size = len(json.dumps(item.model_dump(mode="json"), ensure_ascii=False)) + 2
        if items and size + item_size > 80000:
            break
        items.append(item)
        size += item_size
    return TaskPage(items=items, next_offset=offset + len(items) if len(rows) > len(items) else None)


@router.get("/tasks/summary", response_model=TaskSummaryOut, operation_id="timeboard_task_summary", summary="Count your tasks by due-date bucket")
def summary(db: Session = Depends(get_db), user: User = Depends(integration_user)):
    return crud.get_task_summary_counts(db, current_user=_owner_view(user))


@router.get("/tasks/{task_id}", response_model=ActionTask, operation_id="timeboard_get_task", summary="Read one of your tasks")
def get_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(integration_user)):
    return _out(_task(db, user, task_id))


@router.post("/tasks", response_model=ActionTask, status_code=201, operation_id="timeboard_create_task", summary="Create a task or subtask", openapi_extra={"x-openai-isConsequential": True})
def create_task(payload: ActionCreate, db: Session = Depends(get_db), user: User = Depends(integration_user)):
    try:
        return _out(crud.create_task(db, owner=user, **payload.model_dump()))
    except ValueError as exc:
        _error(exc)


@router.patch("/tasks/{task_id}", response_model=ActionTask, operation_id="timeboard_update_task", summary="Update your task; null clears description or URL", openapi_extra={"x-openai-isConsequential": True})
def update_task(task_id: int, payload: ActionUpdate, db: Session = Depends(get_db), user: User = Depends(integration_user)):
    task = _task(db, user, task_id)
    changes = payload.model_dump(exclude_unset=True)
    for key in ("description", "url"):
        if key in changes and changes[key] is None:
            changes[key] = ""
    if "recurrence_type" not in changes and {"recurrence_interval", "recurrence_times"} & changes.keys():
        changes["recurrence_type"] = task.recurrence_type
    try:
        return _out(crud.update_task(db, task=task, current_user=_owner_view(user), **changes))
    except ValueError as exc:
        _error(exc)


@router.delete("/tasks/{task_id}", response_model=ActionTask, operation_id="timeboard_archive_task", summary="Archive your task; does not permanently erase data", openapi_extra={"x-openai-isConsequential": True})
def archive_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(integration_user)):
    try:
        return _out(crud.soft_delete_task(db, task=_task(db, user, task_id), current_user=_owner_view(user), when_utc=_now()))
    except (crud.OpenSubtasksError, ValueError) as exc:
        _error(exc)


@router.post("/tasks/{task_id}/complete", response_model=Completion, operation_id="timeboard_complete_task", summary="Complete your task and schedule its recurrence", openapi_extra={"x-openai-isConsequential": True})
def complete_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(integration_user)):
    task = _task(db, user, task_id)
    if task.status != TaskStatus.active:
        raise HTTPException(409, "Task is not active; do not retry completion")
    try:
        done, spawned = crud.complete_task(db, task=task, current_user=_owner_view(user), when_utc=_now())
        return Completion(completed_task=_out(done), spawned_task=_out(spawned) if spawned else None)
    except (crud.OpenSubtasksError, ValueError) as exc:
        _error(exc)


@router.post("/tasks/{task_id}/restore", response_model=ActionTask, operation_id="timeboard_restore_task", summary="Restore your archived task", openapi_extra={"x-openai-isConsequential": True})
def restore_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(integration_user)):
    return _out(crud.restore_task(db, task=_task(db, user, task_id), current_user=_owner_view(user)))
