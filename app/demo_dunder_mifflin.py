from __future__ import annotations

from .clock import utc_now

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .crud import create_in_app_notification, create_task, create_user, follow_task
from .db_admin import purge_all_data
from .models import Task, TaskStatus, User
from .notifications import CHANNEL_BROWSER, create_user_notification_service


logger = logging.getLogger("timeboardapp.demo_dunder")


DEMO_PASSWORD = "demo"  # nosec B105 (explicit demo-only password)


def reset_to_dunder_mifflin_demo(db: Session) -> dict[str, int]:
    """Hard-reset the database to a Dunder Mifflin demo state.

    Intended to be called only when demo mode is enabled.
    Preserves AppMeta (DB-backed settings), but clears all operational data.
    """

    purge_all_data(db, preserve_users=False, preserve_app_meta=True)
    return seed_dunder_mifflin_demo(db)


def _u(db: Session, *, username: str, email: str | None, is_admin: bool, manager: User | None = None) -> User:
    user = create_user(
        db,
        username=username,
        password=DEMO_PASSWORD,
        is_admin=bool(is_admin),
        email=email,
    )
    if manager is not None:
        user.manager_id = int(manager.id)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _mk_completed_task(
    db: Session,
    *,
    owner: User,
    name: str,
    task_type: str,
    due_utc: datetime,
    completed_utc: datetime,
    tags: list[str] | None = None,
    description: str | None = None,
    url: str | None = None,
) -> Task:
    t = Task(
        user_id=int(owner.id),
        parent_task_id=None,
        assigned_by_user_id=None,
        name=name,
        task_type=task_type,
        description=description,
        url=url,
        due_date_utc=due_utc.astimezone(timezone.utc).replace(tzinfo=None),
        recurrence_type="none",
        recurrence_interval_seconds=None,
        recurrence_times=None,
        status=TaskStatus.completed,
        completed_at_utc=completed_utc.astimezone(timezone.utc).replace(tzinfo=None),
        deleted_at_utc=None,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    if tags:
        # Re-use create_task tag plumbing by attaching tags post-create.
        # Import locally to avoid circular imports.
        from .crud import get_or_create_tags

        t.tags = get_or_create_tags(db, tags)
        db.add(t)
        db.commit()
        db.refresh(t)
    return t


def seed_dunder_mifflin_demo(db: Session) -> dict[str, int]:
    """Seed a robust demo dataset themed as "Dunder Mifflin".

    This seeds:
      - A small org chart with manager/subordinate relationships
      - Notification service entries (browser enabled; others disabled)
      - A mix of assigned tasks, nested subtasks, and recurring tasks
      - A few in-app notifications to light the bell icon
      - A small set of completed items to demonstrate archival + filters
    """

    now = datetime.now(timezone.utc)

    # Users (manager hierarchy).
    admin = _u(db, username="admin", email="admin@dundermifflin.demo", is_admin=True)

    michael = _u(db, username="michael", email="michael@dundermifflin.demo", is_admin=False)
    dwight = _u(db, username="dwight", email="dwight@dundermifflin.demo", is_admin=False, manager=michael)
    jim = _u(db, username="jim", email="jim@dundermifflin.demo", is_admin=False, manager=michael)
    pam = _u(db, username="pam", email="pam@dundermifflin.demo", is_admin=False, manager=michael)
    angela = _u(db, username="angela", email="angela@dundermifflin.demo", is_admin=False, manager=michael)
    oscar = _u(db, username="oscar", email="oscar@dundermifflin.demo", is_admin=False, manager=michael)
    kevin = _u(db, username="kevin", email="kevin@dundermifflin.demo", is_admin=False, manager=michael)
    stanley = _u(db, username="stanley", email="stanley@dundermifflin.demo", is_admin=False, manager=michael)
    phyllis = _u(db, username="phyllis", email="phyllis@dundermifflin.demo", is_admin=False, manager=michael)
    kelly = _u(db, username="kelly", email="kelly@dundermifflin.demo", is_admin=False, manager=michael)
    ryan = _u(db, username="ryan", email="ryan@dundermifflin.demo", is_admin=False, manager=michael)
    toby = _u(db, username="toby", email="toby@dundermifflin.demo", is_admin=False, manager=michael)
    darryl = _u(db, username="darryl", email="darryl@dundermifflin.demo", is_admin=False, manager=michael)

    users = [admin, michael, dwight, jim, pam, angela, oscar, kevin, stanley, phyllis, kelly, ryan, toby, darryl]

    # Notification services.
    browser_tags: dict[int, str] = {}
    for u in users:
        svc = create_user_notification_service(
            db,
            user_id=int(u.id),
            service_type=CHANNEL_BROWSER,
            name="Browser",
            enabled=True,
            config={},
        )
        browser_tags[int(u.id)] = str(svc.tag.name)

    # Seed a few disabled external services (to show UI, but safe).
    # Use benign placeholder config values.
    disabled_specs = [
        ("gotify", {"url": "https://example.invalid", "token": "DEMO"}),
        ("ntfy", {"topic": "timeboardapp-demo", "server": "https://example.invalid"}),
        ("webhook", {"url": "https://example.invalid/webhook"}),
        ("generic_api", {"url": "https://example.invalid/api", "method": "POST"}),
        ("email", {"to_address": "demo@dundermifflin.demo"}),
        ("discord", {"webhook_url": "https://example.invalid/discord"}),
    ]
    for st, cfg in disabled_specs:
        create_user_notification_service(
            db,
            user_id=int(michael.id),
            service_type=st,
            name=f"(Demo disabled) {st}",
            enabled=False,
            config=cfg,
        )

    # Tasks.
    counts = {"users": len(users), "tasks": 0, "notifications": 0}

    def t(
        owner: User,
        name: str,
        task_type: str,
        hours: int,
        *,
        description: str | None = None,
        tags: list[str] | None = None,
        url: str | None = None,
        recurrence_type: str = "none",
        recurrence_interval: str | None = None,
        recurrence_times: str | None = None,
        parent_task_id: int | None = None,
        assigned_by_user_id: int | None = None,
    ) -> Task:
        task = create_task(
            db,
            owner=owner,
            name=name,
            task_type=task_type,
            due_date=now + timedelta(hours=int(hours)),
            description=description,
            url=url,
            recurrence_type=recurrence_type,
            recurrence_interval=recurrence_interval,
            recurrence_times=recurrence_times,
            tags=tags,
            parent_task_id=parent_task_id,
            assigned_by_user_id=assigned_by_user_id,
            send_notifications=False,
        )
        counts["tasks"] += 1
        return task

    # Welcome and navigation.
    t(
        admin,
        "Welcome: Dunder Mifflin demo environment",
        "Getting Started",
        1,
        description=(
            "This demo imitates a small company install.\n\n"
            "Login passwords for all accounts are: demo\n\n"
            "Notes:\n"
            "- External notifications/webhooks are disabled in demo mode\n"
            "- The database resets on a schedule (see settings.yml demo.reset_interval_minutes)\n"
            "- Try dashboard search, cloning, subtasks, and notifications"
        ),
        tags=["demo", "dunder"],
    )

    # Manager assignments + follow.
    prank = t(
        jim,
        "Prank: Place Dwight's stapler in Jell-O",
        "Sales",
        6,
        tags=["demo", browser_tags[int(jim.id)], "office"],
        assigned_by_user_id=int(michael.id),
        description="Assigned by Michael as 'team building'.",
    )
    try:
        follow_task(db, follower=michael, task=prank)
    except Exception:
        logger.exception("Failed to create demo task follow")

    # Nested subtasks demo.
    qtr = t(
        oscar,
        "Quarterly financial close",
        "Accounting",
        20,
        tags=["demo", "finance", browser_tags[int(oscar.id)]],
        description="Nested subtasks demonstrate dependency trees.",
    )
    qtr_a = t(oscar, "Reconcile vendor invoices", "Accounting", 10, parent_task_id=int(qtr.id), tags=["demo", "finance"])
    qtr_b = t(oscar, "Verify expense reports", "Accounting", 12, parent_task_id=int(qtr.id), tags=["demo", "finance"])
    t(oscar, "Spot-check mileage claims", "Accounting", 8, parent_task_id=int(qtr_b.id), tags=["demo", "finance"])

    # Recurrence types.
    t(
        michael,
        "Daily: Morning check-in",
        "Management",
        2,
        recurrence_type="multi_slot_daily",
        recurrence_times="09:00, 13:00, 16:30",
        tags=["demo", "recurring", browser_tags[int(michael.id)]],
    )
    t(
        dwight,
        "Security patrol",
        "Facilities",
        3,
        recurrence_type="post_completion",
        recurrence_interval="6h",
        tags=["demo", "recurring", "security", browser_tags[int(dwight.id)]],
    )
    t(
        pam,
        "Reception desk: Check voicemail",
        "Operations",
        1,
        recurrence_type="fixed_clock",
        recurrence_interval="Mon, Tue, Wed, Thu, Fri",
        tags=["demo", "recurring", browser_tags[int(pam.id)]],
    )

    # Sales pipeline examples.
    t(jim, "Call: Lackawanna County", "Sales", 5, tags=["demo", "sales", "pipeline"])
    t(phyllis, "Follow up: Vance Refrigeration", "Sales", 7, tags=["demo", "sales", "pipeline"])
    t(stanley, "Renewal: Scranton White Pages", "Sales", 9, tags=["demo", "sales", "pipeline"])
    t(dwight, "Beet farm supply order", "Side Hustle", 14, tags=["demo", "personal"], url="https://example.invalid/beets")

    # Warehouse.
    ship = t(darryl, "Truck loading schedule", "Warehouse", 4, tags=["demo", "warehouse", browser_tags[int(darryl.id)]])
    t(darryl, "Pallet count verification", "Warehouse", 6, parent_task_id=int(ship.id), tags=["demo", "warehouse"])
    t(darryl, "Pick list review", "Warehouse", 7, parent_task_id=int(ship.id), tags=["demo", "warehouse"])

    # HR.
    t(toby, "HR: Update workplace policy binder", "HR", 48, tags=["demo", "hr", browser_tags[int(toby.id)]])
    t(toby, "Schedule annual compliance training", "HR", 72, tags=["demo", "hr"], recurrence_type="fixed_clock", recurrence_interval="1st Monday of every month")

    # Customer support / ops.
    t(pam, "Client visit prep", "Operations", 24, tags=["demo", "ops"], description="Includes linkify tests: https://dundermifflin.com")

    # A few completed tasks (to show default hidden completed/deleted + purge).
    _mk_completed_task(
        db,
        owner=jim,
        name="Closed deal: Blue Cross", 
        task_type="Sales",
        due_utc=now - timedelta(days=2),
        completed_utc=now - timedelta(days=2, hours=-1),
        tags=["demo", "sales"],
    )
    counts["tasks"] += 1

    # Seed a few in-app notifications.
    for title, msg, who in [
        ("New task assigned", "Michael assigned you a task.", jim),
        ("Reminder", "Quarterly close is coming due.", oscar),
        ("FYI", "Warehouse schedule updated.", darryl),
    ]:
        create_in_app_notification(
            db,
            user_id=int(who.id),
            event_type="demo",
            title=title,
            message=msg,
        )
        counts["notifications"] += 1
    db.commit()

    return {
        "seeded": 1,
        "users_created": int(counts["users"]),
        "tasks_created": int(counts["tasks"]),
        "notifications_created": int(counts["notifications"]),
    }
