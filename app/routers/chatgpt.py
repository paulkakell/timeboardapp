"""Least-privilege GPT Actions interface; never accepts an administrator JWT."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..auth import credential_fingerprint, get_current_user_api
from ..db import get_db
from ..models import IntegrationToken, User
from ..schemas import TaskSummaryOut
from ..services import integration_tasks as tasks
from ..services.integration_tasks import (
    ActionCreate,
    ActionTask,
    ActionUpdate,
    Completion,
    TaskPage,
)

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




@router.get("/tasks", response_model=TaskPage, operation_id="timeboard_list_tasks", summary="List or search your tasks")
def list_tasks(search: str | None = Query(None, max_length=255), status: Literal["active", "completed", "deleted", "archived"] | None = None,
               include_archived: bool = False, limit: int = Query(10, ge=1, le=10), offset: int = Query(0, ge=0, le=1000000),
               db: Session = Depends(get_db), user: User = Depends(integration_user)):
    return tasks.list_tasks(search=search, status=status, include_archived=include_archived, limit=limit, offset=offset, db=db, user=user)


@router.get("/tasks/summary", response_model=TaskSummaryOut, operation_id="timeboard_task_summary", summary="Count your tasks by due-date bucket")
def summary(db: Session = Depends(get_db), user: User = Depends(integration_user)):
    return tasks.summary(db=db, user=user)


@router.get("/tasks/{task_id}", response_model=ActionTask, operation_id="timeboard_get_task", summary="Read one of your tasks")
def get_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(integration_user)):
    return tasks.get_task(task_id=task_id, db=db, user=user)


@router.post("/tasks", response_model=ActionTask, status_code=201, operation_id="timeboard_create_task", summary="Create a task or subtask", openapi_extra={"x-openai-isConsequential": True})
def create_task(payload: ActionCreate, db: Session = Depends(get_db), user: User = Depends(integration_user)):
    return tasks.create_task(payload=payload, db=db, user=user)


@router.patch("/tasks/{task_id}", response_model=ActionTask, operation_id="timeboard_update_task", summary="Update your task; null clears description or URL", openapi_extra={"x-openai-isConsequential": True})
def update_task(task_id: int, payload: ActionUpdate, db: Session = Depends(get_db), user: User = Depends(integration_user)):
    return tasks.update_task(task_id=task_id, payload=payload, db=db, user=user)


@router.delete("/tasks/{task_id}", response_model=ActionTask, operation_id="timeboard_archive_task", summary="Archive your task; does not permanently erase data", openapi_extra={"x-openai-isConsequential": True})
def archive_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(integration_user)):
    return tasks.archive_task(task_id=task_id, db=db, user=user)


@router.post("/tasks/{task_id}/complete", response_model=Completion, operation_id="timeboard_complete_task", summary="Complete your task and schedule its recurrence", openapi_extra={"x-openai-isConsequential": True})
def complete_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(integration_user)):
    return tasks.complete_task(task_id=task_id, db=db, user=user)


@router.post("/tasks/{task_id}/restore", response_model=ActionTask, operation_id="timeboard_restore_task", summary="Restore your archived task", openapi_extra={"x-openai-isConsequential": True})
def restore_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(integration_user)):
    return tasks.restore_task(task_id=task_id, db=db, user=user)
