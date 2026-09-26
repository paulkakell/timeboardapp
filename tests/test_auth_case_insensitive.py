from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.auth import authenticate_user, create_access_token, get_current_user_api
from app.crud import create_user, get_user_by_username
from app.db import Base
from app.migrations import ensure_db_schema


def _settings_file(tmp_path) -> str:
    settings_path = tmp_path / "settings.yml"
    settings_path.write_text(
        f"""
app:
  name: "TimeboardApp"
  timezone: "UTC"
  port: 8888
  base_url: ""
security:
  session_secret: "case-test-session-secret-1234567890"
  jwt_secret: "case-test-jwt-secret-12345678901234"
database:
  path: "{tmp_path / 'case.db'}"
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
""".lstrip(),
        encoding="utf-8",
    )
    return str(settings_path)


def _make_session(db_path: str):
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

    Base.metadata.create_all(bind=engine)
    ensure_db_schema(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return Session()


def test_username_lookup_and_authentication_are_case_insensitive(tmp_path, monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("TIMEBOARDAPP_SETTINGS", _settings_file(tmp_path))
    get_settings.cache_clear()

    db = _make_session(str(tmp_path / "case.db"))
    try:
        user = create_user(db, username="MixedCaseUser", password="CasePassword123!", email="case@example.invalid")

        assert get_user_by_username(db, "mixedcaseuser").id == user.id
        assert authenticate_user(db, "MIXEDCASEUSER", "CasePassword123!").id == user.id
        assert authenticate_user(db, "case@example.invalid", "CasePassword123!").id == user.id

        token = create_access_token(subject="MIXEDCASEUSER", is_admin=False, user=user)
        assert get_current_user_api(db=db, token=token).id == user.id
    finally:
        db.close()


def test_case_variant_username_cannot_be_created(tmp_path, monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("TIMEBOARDAPP_SETTINGS", _settings_file(tmp_path))
    get_settings.cache_clear()

    db = _make_session(str(tmp_path / "duplicate.db"))
    try:
        create_user(db, username="DuplicateName", password="CasePassword123!", email="one@example.invalid")
        try:
            create_user(db, username="duplicatename", password="CasePassword123!", email="two@example.invalid")
            raise AssertionError("case-variant duplicate username was allowed")
        except ValueError as exc:
            assert "Username already exists" in str(exc)
    finally:
        db.close()
