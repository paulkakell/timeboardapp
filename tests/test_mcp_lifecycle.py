"""Exercise actual startup, additive schema migration, and feature rollback."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_actual_app_disabled_enabled_and_rolled_back(tmp_path):
    config = tmp_path / "settings.yml"
    config.write_text(
        json.dumps(
            {
                "app": {"base_url": "https://timeboard.example"},
                "database": {"path": str(tmp_path / "legacy.db")},
                "email": {"reminder_interval_minutes": 0},
            }
        )
    )
    root = Path(__file__).resolve().parents[1]
    for mode in ["seed", "enabled", "rollback"]:
        env = dict(
            os.environ,
            TIMEBOARDAPP_SETTINGS=str(config),
            TIMEBOARDAPP_BASE_URL="",
            TIMEBOARDAPP_MCP_ENABLED="true" if mode == "enabled" else "false",
            MCP_TEST_MODE=mode,
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import os
from sqlalchemy import inspect
from fastapi.testclient import TestClient
from app.main import app
from app.db import engine, SessionLocal
from app import crud
from app.models import Task
mode = os.environ["MCP_TEST_MODE"]
with TestClient(app, base_url="https://timeboard.example") as client:
    assert client.get("/healthz").status_code == 200
    assert client.get("/login").status_code == 200
    assert client.get("/openapi-chatgpt.json").status_code == 200
    enabled = mode == "enabled"
    assert client.get("/mcp").status_code == (401 if enabled else 404)
    assert client.get("/.well-known/oauth-authorization-server").status_code == (200 if enabled else 404)
    assert client.get("/profile/connections", follow_redirects=False).status_code == (303 if enabled else 404)
    if enabled:
        consent = client.get("/mcp/consent?request_id=invalid")
        assert consent.status_code == 400
        assert "form-action 'self' https://chatgpt.com" in consent.headers["content-security-policy"]
        assert "https://chatgpt.com" not in client.get("/login").headers["content-security-policy"]
    tables = set(inspect(engine).get_table_names())
    assert ("mcp_connections" in tables) == (mode != "seed")
    with SessionLocal() as db:
        if mode == "seed":
            owner = crud.create_user(db, username="migration-test", email="migration@example.invalid", password="Synthetic-migration-password-123")
            crud.create_task(db, owner=owner, name="Keep this legacy task", task_type="test", due_date=None)
        assert db.query(Task).filter_by(name="Keep this legacy task").count() == 1
print("PASS", mode)
""",
            ],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
