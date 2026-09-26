from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..auth import get_current_user_api
from ..db import get_db
from ..models import NotificationEvent, User, UserNotificationService
from ..notifications import (
    CHANNEL_TYPES,
    create_user_notification_service,
    delete_user_notification_service,
    list_user_notification_services,
    update_user_notification_service,
)
from ..schemas import NotificationEventOut, NotificationServiceCreate, NotificationServiceOut, NotificationServiceUpdate

router = APIRouter()




def _loads_cfg(s: str | None) -> dict:
    if not s:
        return {}
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _secret_key(key: str) -> bool:
    key = str(key).lower()
    return key in {"headers", "url", "channel_uri"} or any(word in key for word in ("token", "secret", "password", "authorization", "api_key", "webhook"))


def _redact_cfg(cfg: dict) -> dict:
    return {k: "***" if _secret_key(k) and v else (_redact_cfg(v) if isinstance(v, dict) else v) for k, v in (cfg or {}).items()}


def _merge_cfg(existing: dict, incoming: dict) -> dict:
    merged = dict(existing)
    for k, v in incoming.items():
        if v == "***" or (_secret_key(k) and v == ""):
            continue
        if v is None:
            merged.pop(k, None)
        elif isinstance(v, dict):
            merged[k] = _merge_cfg(existing.get(k, {}) if isinstance(existing.get(k), dict) else {}, v)
        else:
            merged[k] = v
    return merged


def _svc_out(svc: UserNotificationService) -> NotificationServiceOut:
    cfg = _loads_cfg(svc.config_json)
    return NotificationServiceOut(
        id=int(svc.id),
        user_id=int(svc.user_id),
        service_type=str(svc.service_type),
        name=svc.name,
        enabled=bool(svc.enabled),
        tag=(svc.tag.name if getattr(svc, "tag", None) is not None else ""),
        config=_redact_cfg(cfg),
        created_at=svc.created_at,
        updated_at=svc.updated_at,
    )


@router.get("/services", response_model=List[NotificationServiceOut])
def list_services(db: Session = Depends(get_db), user: User = Depends(get_current_user_api)):
    services = list_user_notification_services(db, user_id=int(user.id))
    return [_svc_out(s) for s in services]


@router.post("/services", response_model=NotificationServiceOut, status_code=status.HTTP_201_CREATED)
def create_service(
    payload: NotificationServiceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_api),
):
    st = str(payload.service_type).strip().lower()
    if st not in CHANNEL_TYPES:
        raise HTTPException(status_code=400, detail="Invalid service_type")
    svc = create_user_notification_service(
        db,
        user_id=int(user.id),
        service_type=st,
        name=payload.name,
        enabled=bool(payload.enabled),
        config=dict(payload.config or {}),
    )
    return _svc_out(svc)


@router.get("/services/{service_id}", response_model=NotificationServiceOut)
def get_service(
    service_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_api),
):
    svc = (
        db.query(UserNotificationService)
        .filter(UserNotificationService.id == int(service_id))
        .filter(UserNotificationService.user_id == int(user.id))
        .first()
    )
    if not svc:
        raise HTTPException(status_code=404, detail="Not found")
    return _svc_out(svc)


@router.put("/services/{service_id}", response_model=NotificationServiceOut)
def update_service(
    service_id: int,
    payload: NotificationServiceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_api),
):
    svc = (
        db.query(UserNotificationService)
        .filter(UserNotificationService.id == int(service_id))
        .filter(UserNotificationService.user_id == int(user.id))
        .first()
    )
    if not svc:
        raise HTTPException(status_code=404, detail="Not found")

    existing_cfg = _loads_cfg(svc.config_json)

    new_cfg = None
    if payload.config is not None:
        new_cfg = _merge_cfg(existing_cfg, dict(payload.config))

    updated = update_user_notification_service(
        db,
        user_id=int(user.id),
        service_id=int(service_id),
        name=payload.name if payload.name is not None else svc.name,
        enabled=payload.enabled if payload.enabled is not None else bool(svc.enabled),
        config=new_cfg if new_cfg is not None else existing_cfg,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Not found")
    return _svc_out(updated)


@router.delete("/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(
    service_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_api),
):
    ok = delete_user_notification_service(db, user_id=int(user.id), service_id=int(service_id))
    if not ok:
        raise HTTPException(status_code=404, detail="Not found")
    return None


@router.get("/events", response_model=List[NotificationEventOut])
def list_events(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_api),
    limit: int = Query(50, ge=1, le=200),
    before_id: Optional[int] = Query(None, description="Return events with id < before_id"),
    after_id: Optional[int] = Query(None, description="Return events with id > after_id"),
    service_type: Optional[str] = Query(None, description="Filter by service_type"),
    task_id: Optional[int] = Query(None, description="Filter by task_id"),
):
    q = db.query(NotificationEvent).filter(NotificationEvent.user_id == int(user.id))

    if service_type:
        q = q.filter(NotificationEvent.service_type == str(service_type).strip().lower())
    if task_id is not None:
        q = q.filter(NotificationEvent.task_id == int(task_id))
    if before_id is not None:
        q = q.filter(NotificationEvent.id < int(before_id))
    if after_id is not None:
        q = q.filter(NotificationEvent.id > int(after_id))

    rows = q.order_by(NotificationEvent.id.desc()).limit(int(limit)).all()
    return rows
