from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..auth import get_current_user_api
from ..crud import (
    complete_task,
    create_task,
    get_task,
    get_task_summary_counts,
    list_tasks,
    restore_task,
    soft_delete_task,
    update_task,
)
from ..db import get_db
from ..schemas import TaskCompleteResponse, TaskCreate, TaskOut, TaskSummaryOut, TaskUpdate


router = APIRouter()


@router.get("/", response_model=list[TaskOut])
def api_list_tasks(
    include_archived: bool = Query(default=False),
    status: str | None = Query(default=None, description="Filter by status: active/completed/deleted/archived"),
    tag: str | None = Query(default=None),
    task_type: str | None = Query(default=None, description="Filter by exact task_type"),
    sort: str = Query(default="due_date", description="Sort by: due_date, task_type, name. Prefix with '-' for desc."),
    user_id: int | None = Query(default=None, description="Admin-only: filter tasks by user_id"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user_api),
):
    try:
        return list_tasks(
            db,
            current_user=current_user,
            include_archived=include_archived,
            status=status,
            tag=tag,
            user_id=user_id,
            task_type=task_type,
            sort=sort,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/summary", response_model=TaskSummaryOut)
def api_task_summary(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user_api),
):
    return get_task_summary_counts(db, current_user=current_user)


@router.post("/", response_model=TaskOut)
def api_create_task(
    payload: TaskCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user_api),
):
    try:
        task = create_task(
            db,
            owner=current_user,
            name=payload.name,
            task_type=payload.task_type,
            description=payload.description,
            url=payload.url,
            due_date=payload.due_date,
            recurrence_type=payload.recurrence_type,
            recurrence_interval=payload.recurrence_interval,
            recurrence_times=payload.recurrence_times,
            tags=payload.tags,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return task


@router.get("/{task_id}", response_model=TaskOut)
def api_get_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user_api),
):
    task = get_task(db, task_id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not current_user.is_admin and task.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not allowed")
    return task


@router.put("/{task_id}", response_model=TaskOut)
def api_update_task(
    task_id: int,
    payload: TaskUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user_api),
):
    task = get_task(db, task_id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    try:
        updated = update_task(
            db,
            task=task,
            current_user=current_user,
            name=payload.name,
            task_type=payload.task_type,
            description=payload.description,
            url=payload.url,
            due_date=payload.due_date,
            recurrence_type=payload.recurrence_type,
            recurrence_interval=payload.recurrence_interval,
            recurrence_times=payload.recurrence_times,
            tags=payload.tags,
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not allowed")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return updated


@router.delete("/{task_id}", response_model=TaskOut)
def api_delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user_api),
):
    task = get_task(db, task_id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    when = datetime.utcnow().replace(tzinfo=None)
    try:
        deleted = soft_delete_task(db, task=task, current_user=current_user, when_utc=when)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not allowed")
    return deleted


@router.post("/{task_id}/complete", response_model=TaskCompleteResponse)
def api_complete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user_api),
):
    task = get_task(db, task_id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    when = datetime.utcnow().replace(tzinfo=None)
    try:
        completed, spawned = complete_task(db, task=task, current_user=current_user, when_utc=when)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not allowed")

    return TaskCompleteResponse(completed_task=completed, spawned_task=spawned)


@router.post("/{task_id}/restore", response_model=TaskOut)
def api_restore_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user_api),
):
    task = get_task(db, task_id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    try:
        restored = restore_task(db, task=task, current_user=current_user)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not allowed")

    return restored
