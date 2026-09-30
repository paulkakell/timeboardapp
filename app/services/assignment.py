"""Shared rules for choosing task assignees."""

from .. import crud
from ..models import User


def can_assign_to(db, actor, target_user_id: int) -> bool:
    return bool(
        actor.is_admin
        or actor.id == target_user_id
        or crud.is_manager_of(
            db, manager_user_id=actor.id, subordinate_user_id=target_user_id
        )
    )


def assignable_users(db, actor, limit=100, offset=0):
    query = db.query(User)
    if not actor.is_admin:
        ids = [actor.id] + crud.list_subordinate_user_ids(db, manager_user_id=actor.id)
        query = query.filter(User.id.in_(ids))
    return query.order_by(User.username, User.id).offset(offset).limit(limit).all()
