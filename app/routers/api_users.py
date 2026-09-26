from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import get_current_user_api, require_admin_api
from ..crud import (
    create_user,
    delete_user,
    get_user,
    get_user_by_username,
    list_users,
    update_user_admin,
    update_user_me,
)
from ..db import get_db
from ..schemas import UserAdminUpdate, UserCreate, UserMeUpdate, UserOut


router = APIRouter()


@router.get("/", response_model=list[UserOut])
def api_list_users(
    db: Session = Depends(get_db),
    admin=Depends(require_admin_api),
):
    return list_users(db)


@router.post("/", response_model=UserOut)
def api_create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    admin=Depends(require_admin_api),
):
    if get_user_by_username(db, payload.username):
        raise HTTPException(status_code=400, detail="Username already exists")
    try:
        return create_user(
            db,
            username=payload.username,
            password=payload.password,
            is_admin=payload.is_admin,
            email=payload.email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/me", response_model=UserOut)
def api_get_me(current_user=Depends(get_current_user_api)):
    return current_user


@router.patch("/me", response_model=UserOut)
def api_update_me(
    payload: UserMeUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user_api),
):
    try:
        updated = update_user_me(
            db,
            user=current_user,
            theme=payload.theme,
            purge_days=payload.purge_days,
            email=payload.email,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return updated


@router.patch("/{user_id}", response_model=UserOut)
def api_update_user(
    user_id: int,
    payload: UserAdminUpdate,
    db: Session = Depends(get_db),
    admin=Depends(require_admin_api),
):
    # Prevent demoting oneself via API by default (safety).
    if admin.id == user_id and payload.is_admin is False:
        raise HTTPException(status_code=400, detail="Cannot remove admin from the currently authenticated user")

    try:
        updated = update_user_admin(db, user_id=user_id, is_admin=payload.is_admin, email=payload.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    return updated


@router.delete("/{user_id}")
def api_delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin=Depends(require_admin_api),
):
    # Prevent deleting oneself through this endpoint (optional safety).
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete the currently authenticated user")
    delete_user(db, user_id=user_id)
    return {"status": "deleted"}


