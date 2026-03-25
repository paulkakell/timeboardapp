import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, get_current_user_api
from app.crud import create_task, create_user, get_task_summary_counts
from app.db import Base, get_db
from app.migrations import ensure_db_schema
from app.routers import api_tasks




@pytest.fixture
def settings_tmp(tmp_path, monkeypatch):
    path = tmp_path / "settings.yml"
    path.write_text(
        """
app:
  name: "TimeboardApp"
  timezone: "UTC"
  base_url: ""
security:
  session_secret: "test-session-secret"
  jwt_secret: "test-jwt-secret"
database:
  path: "{db}"
purge:
  default_days: 15
  interval_minutes: 5
logging:
  level: "INFO"
email:
  enabled: false
  smtp_host: ""
  smtp_port: 587
  smtp_user: ""
  smtp_password: ""
  from_address: ""
  reminder_interval_minutes: 60
  reset_token_minutes: 60
""".format(db=str(tmp_path / "test.db")).lstrip()
    )
    monkeypatch.setenv("TIMEBOARDAPP_SETTINGS", str(path))
    from app.config import get_settings

    get_settings.cache_clear()
    return path

def make_engine(db_path: str):
    engine = create_engine(
        f"sqlite+pysqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine



def make_session(engine):
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return Session()



def init_full_schema(engine):
    Base.metadata.create_all(bind=engine)
    ensure_db_schema(engine)



def test_get_task_summary_counts_buckets_and_archived(settings_tmp, tmp_path):
    db_path = tmp_path / "task-summary.db"
    engine = make_engine(str(db_path))
    init_full_schema(engine)

    db = make_session(engine)
    try:
        user = create_user(db, username="summary-user", password="password123", email="summary@example.com")
        now = datetime(2026, 3, 24, 12, 0, 0, tzinfo=timezone.utc)

        create_task(db, owner=user, name="past due", task_type="ops", due_date=now - timedelta(minutes=1))
        create_task(db, owner=user, name="due now", task_type="ops", due_date=now)
        create_task(db, owner=user, name="due before 8h", task_type="ops", due_date=now + timedelta(hours=7, minutes=59))
        create_task(db, owner=user, name="due at 8h", task_type="ops", due_date=now + timedelta(hours=8))
        create_task(db, owner=user, name="due before 24h", task_type="ops", due_date=now + timedelta(hours=23, minutes=59))
        create_task(db, owner=user, name="due at 24h", task_type="ops", due_date=now + timedelta(hours=24))

        completed = create_task(db, owner=user, name="completed", task_type="ops", due_date=now + timedelta(days=2))
        deleted = create_task(db, owner=user, name="deleted", task_type="ops", due_date=now + timedelta(days=3))

        from app.crud import complete_task, soft_delete_task

        complete_task(db, task=completed, current_user=user, when_utc=now.replace(tzinfo=None))
        soft_delete_task(db, task=deleted, current_user=user, when_utc=now.replace(tzinfo=None))

        summary = get_task_summary_counts(db, current_user=user, now_utc=now.replace(tzinfo=None))

        assert summary == {
            "archived": 2,
            "past_due": 1,
            "all_upcoming_due": 5,
            "due_in_0_8h": 2,
            "due_in_8_24h": 2,
            "due_in_over_24h": 1,
        }
    finally:
        db.close()



def test_tasks_summary_endpoint_uses_authenticated_user_scope(settings_tmp, tmp_path):
    db_path = tmp_path / "task-summary-api.db"
    engine = make_engine(str(db_path))
    init_full_schema(engine)

    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    seed = Session()
    try:
        user_a = create_user(seed, username="user-a", password="password123", email="a@example.com")
        user_b = create_user(seed, username="user-b", password="password123", email="b@example.com")
        now = datetime.utcnow().replace(tzinfo=timezone.utc)
        create_task(seed, owner=user_a, name="a soon", task_type="ops", due_date=now + timedelta(hours=1))
        create_task(seed, owner=user_a, name="a later", task_type="ops", due_date=now + timedelta(hours=30))
        create_task(seed, owner=user_b, name="b overdue", task_type="ops", due_date=now - timedelta(hours=2))
    finally:
        seed.close()

    app = FastAPI()
    app.include_router(api_tasks.router, prefix="/api/tasks")

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    client = TestClient(app)
    token_a = create_access_token(subject="user-a", is_admin=False)
    token_b = create_access_token(subject="user-b", is_admin=False)

    response_a = client.get("/api/tasks/summary", headers={"Authorization": f"Bearer {token_a}"})
    assert response_a.status_code == 200
    assert response_a.json() == {
        "archived": 0,
        "past_due": 0,
        "all_upcoming_due": 2,
        "due_in_0_8h": 1,
        "due_in_8_24h": 0,
        "due_in_over_24h": 1,
    }

    response_b = client.get("/api/tasks/summary", headers={"Authorization": f"Bearer {token_b}"})
    assert response_b.status_code == 200
    assert response_b.json() == {
        "archived": 0,
        "past_due": 1,
        "all_upcoming_due": 0,
        "due_in_0_8h": 0,
        "due_in_8_24h": 0,
        "due_in_over_24h": 0,
    }

    unauth = client.get("/api/tasks/summary")
    assert unauth.status_code == 401
