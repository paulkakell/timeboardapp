"""Exercise real HTTP authentication and MCP transport, never bypassing auth."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from app import crud
from app.auth import hash_password
from app.browser_security import BrowserSecurityMiddleware
from app.config import get_settings
from app.db import Base, get_db
from app.db_admin import export_db_json
from app.mcp.auth import SCOPES, digest
from app.mcp.models import MCPAudit, MCPClient, MCPConnection, MCPCredential, MCPGrant
from app.mcp.server import build_mcp
from app.models import NotificationEvent, Task, TaskFollow, User
from app.routers import chatgpt

BASE = "https://timeboard.example"
REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
PASSWORD = "Synthetic-password-for-tests-123"
VERIFIER = "v" * 64
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest())
    .decode()
    .rstrip("=")
)


@pytest.fixture
def system(tmp_path, monkeypatch):
    config = tmp_path / "settings.yml"
    config.write_text(
        json.dumps(
            {
                "app": {"base_url": BASE},
                "database": {"path": str(tmp_path / "test.db")},
                "mcp": {"enabled": True},
            }
        )
    )
    monkeypatch.setenv("TIMEBOARDAPP_SETTINGS", str(config))
    monkeypatch.delenv("TIMEBOARDAPP_BASE_URL", raising=False)
    monkeypatch.delenv("TIMEBOARDAPP_MCP_ENABLED", raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_engine(
        "sqlite:///" + str(tmp_path / "test.db"),
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(conn, record):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    with sessions() as db:
        manager = crud.create_user(
            db, username="manager", email="manager@example.invalid", password=PASSWORD
        )
        member = crud.create_user(
            db,
            username="member",
            email="member@example.invalid",
            password=PASSWORD,
            manager_id=manager.id,
        )
        outsider = crud.create_user(
            db, username="outsider", email="outsider@example.invalid", password=PASSWORD
        )
        admin = crud.create_user(db, username="admin", password=PASSWORD, is_admin=True)
        ids = {u.username: u.id for u in [manager, member, outsider, admin]}
    mcp, transport, browser, auth_routes, provider = build_mcp(settings, sessions)

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    application = FastAPI(lifespan=lifespan)
    application.add_middleware(
        BrowserSecurityMiddleware, base_url=BASE, mcp_enabled=True
    )
    application.add_middleware(
        SessionMiddleware, secret_key=settings.security.session_secret, https_only=True
    )
    application.include_router(browser)
    application.include_router(chatgpt.router)
    application.include_router(chatgpt.tokens_router)
    application.router.routes.extend(auth_routes)
    application.mount("/", transport)

    def db_dependency():
        with sessions() as db:
            yield db

    application.dependency_overrides[get_db] = db_dependency
    with TestClient(application, base_url=BASE, follow_redirects=False) as client:
        yield {
            "client": client,
            "sessions": sessions,
            "ids": ids,
            "provider": provider,
            "settings": settings,
            "app": application,
            "config": config,
        }
    engine.dispose()
    get_settings.cache_clear()


def csrf(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


def registration(s, scopes=SCOPES, method="none"):
    response = s["client"].post(
        "/register",
        json={
            "client_name": "Test ChatGPT",
            "redirect_uris": [REDIRECT],
            "scope": " ".join(scopes),
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": method,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def authorize(s, registration, user="manager", scopes=SCOPES):
    c = s["client"]
    c.cookies.clear()
    response = c.get(
        "/authorize",
        params={
            "client_id": registration["client_id"],
            "response_type": "code",
            "redirect_uri": REDIRECT,
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
            "state": "test-state",
            "resource": BASE + "/mcp",
            "scope": " ".join(scopes),
        },
    )
    assert response.status_code == 302, response.text
    consent_url = response.headers["location"]
    page = c.get(consent_url)
    assert page.status_code == 200, page.text
    response = c.post(
        consent_url,
        data={
            "action": "login",
            "username": user,
            "password": PASSWORD,
            "csrf_token": csrf(page),
        },
    )
    assert response.status_code == 303, response.text
    page = c.get(consent_url)
    assert "Allow access" in page.text
    response = c.post(consent_url, data={"action": "approve", "csrf_token": csrf(page)})
    assert response.status_code == 303, response.text
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["state"] == ["test-state"]
    return query["code"][0]


def exchange(s, reg, code, **changes):
    data = {
        "grant_type": "authorization_code",
        "client_id": reg["client_id"],
        "code": code,
        "code_verifier": VERIFIER,
        "redirect_uri": REDIRECT,
        "resource": BASE + "/mcp",
    }
    headers = {}
    if reg["token_endpoint_auth_method"] == "client_secret_post":
        data["client_secret"] = reg["client_secret"]
    elif reg["token_endpoint_auth_method"] == "client_secret_basic":
        headers["Authorization"] = (
            "Basic "
            + base64.b64encode(
                (reg["client_id"] + ":" + reg["client_secret"]).encode()
            ).decode()
        )
        del data["client_id"]  # RFC 6749 permits Basic authentication alone.
    data.update(changes)
    return s["client"].post("/token", data=data, headers=headers)


def connect(s, user="manager", scopes=SCOPES, method="none"):
    reg = registration(s, scopes, method)
    code = authorize(s, reg, user, scopes)
    response = exchange(s, reg, code)
    assert response.status_code == 200, response.text
    return reg, response.json()


def rpc(s, token, method, params=None, **headers):
    h = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-11-25",
    }
    if token:
        h["Authorization"] = "Bearer " + token
    h.update(headers)
    return s["client"].post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers=h,
    )


def call(s, tokens, name, arguments=None):
    r = rpc(
        s,
        tokens["access_token"],
        "tools/call",
        {"name": name, "arguments": arguments or {}},
    )
    assert r.status_code == 200, r.text
    assert "result" in r.json(), r.text
    return r.json()["result"]


def create(s, tokens, key="create-key-0001", **fields):
    return call(
        s,
        tokens,
        "create_task",
        {
            "request_key": key,
            "payload": {"name": "MCP task", "task_type": "work", **fields},
        },
    )


def value(result):
    assert not result.get("isError"), result
    return result["structuredContent"]


@pytest.mark.parametrize(
    "method", ["none", "client_secret_post", "client_secret_basic"]
)
def test_oauth_flow_and_discovery(system, method):
    s = system
    _, tokens = connect(s, method=method)
    discovery = s["client"].get("/.well-known/oauth-authorization-server").json()
    resource = s["client"].get("/.well-known/oauth-protected-resource/mcp").json()
    assert resource["resource"] == BASE + "/mcp"
    assert resource["authorization_servers"] == [discovery["issuer"]]
    assert resource["scopes_supported"] == SCOPES
    assert "S256" in discovery["code_challenge_methods_supported"]
    assert method in discovery["token_endpoint_auth_methods_supported"]
    assert tokens["access_token"].startswith("tbm_a_")
    assert tokens["refresh_token"].startswith("tbm_r_")
    init = rpc(
        s,
        tokens["access_token"],
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    )
    assert init.status_code == 200, init.text
    tools = rpc(s, tokens["access_token"], "tools/list").json()["result"]["tools"]
    assert len(tools) == 11
    assert all(t.get("inputSchema") and t.get("outputSchema") for t in tools)
    assert all(t.get("_meta", {}).get("securitySchemes") for t in tools)
    profile = value(call(s, tokens, "get_profile"))
    assert profile["id"] == str(s["ids"]["manager"])
    with s["sessions"]() as db:
        data = json.dumps([r.metadata_json for r in db.query(MCPClient)])
        assert 'client_secret"' not in data
        assert tokens["access_token"] not in json.dumps(export_db_json(db))
        assert db.get(MCPCredential, digest(tokens["access_token"])) is not None


def test_cookies_and_wrong_tokens_cannot_authenticate_mcp(system):
    _, tokens = connect(system)
    for token in [None, "tba_old-actions-token", "not-a-token"]:
        response = rpc(system, token, "tools/list")
        assert response.status_code == 401
        assert "resource_metadata=" in response.headers["www-authenticate"]
    response = rpc(
        system, tokens["access_token"], "tools/list", Origin="https://attacker.example"
    )
    assert response.status_code == 403
    response = rpc(
        system, tokens["access_token"], "tools/list", Host="attacker.example"
    )
    assert response.status_code == 421


def test_read_scope_on_post_and_write_denied(system):
    _, tokens = connect(system, scopes=["tasks:read"])
    assert value(call(system, tokens, "list_tasks"))["items"] == []
    denied = create(system, tokens)
    assert denied["isError"]
    assert "mcp/www_authenticate" in denied["_meta"]
    with system["sessions"]() as db:
        assert db.query(Task).count() == db.query(MCPAudit).count() == 0


@pytest.mark.parametrize(
    "change",
    [
        {"code_verifier": "wrong" * 13},
        {"resource": "https://other.example/mcp"},
        {"redirect_uri": "https://attacker.example/callback"},
    ],
)
def test_token_exchange_rejects_changed_binding(system, change):
    reg = registration(system)
    code = authorize(system, reg)
    response = exchange(system, reg, code, **change)
    assert response.status_code == 400, response.text
    assert exchange(system, reg, code).status_code == 200


def test_code_replay_revokes_connection(system):
    reg = registration(system)
    code = authorize(system, reg)
    tokens = exchange(system, reg, code).json()
    assert exchange(system, reg, code).status_code == 400
    assert rpc(system, tokens["access_token"], "tools/list").status_code == 401


def test_refresh_rotation_scope_and_replay(system):
    reg, tokens = connect(system, scopes=["tasks:read"])
    data = {
        "grant_type": "refresh_token",
        "client_id": reg["client_id"],
        "refresh_token": tokens["refresh_token"],
        "resource": BASE + "/mcp",
    }
    assert (
        system["client"]
        .post("/token", data={**data, "scope": "tasks:write"})
        .status_code
        == 400
    )
    assert (
        system["client"].post("/token", data={**data, "resource": "wrong"}).status_code
        == 400
    )
    refreshed = system["client"].post("/token", data=data)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["refresh_token"] != tokens["refresh_token"]
    assert system["client"].post("/token", data=data).status_code == 400
    assert (
        rpc(system, refreshed.json()["access_token"], "tools/list").status_code == 401
    )


@pytest.mark.parametrize("revocation", ["password", "connection", "expiry"])
def test_credential_changes_revoke_access(system, revocation):
    _, tokens = connect(system)
    with system["sessions"]() as db:
        if revocation == "password":
            db.get(User, system["ids"]["manager"]).hashed_password = hash_password(
                "changed-password-123"
            )
        elif revocation == "connection":
            db.query(MCPConnection).update({"revoked": True})
        else:
            db.query(MCPCredential).update({"expires_at": int(time.time()) - 1})
        db.commit()
    assert rpc(system, tokens["access_token"], "tools/list").status_code == 401


def test_create_idempotency_update_and_recurrence(system):
    _, tokens = connect(system)
    made = value(
        create(
            system, tokens, recurrence_type="post_completion", recurrence_interval="1h"
        )
    )
    assert (
        value(
            create(
                system,
                tokens,
                recurrence_type="post_completion",
                recurrence_interval="1h",
            )
        )["id"]
        == made["id"]
    )
    assert create(system, tokens, name="Changed arguments")["isError"]
    updated = value(
        call(
            system,
            tokens,
            "update_task",
            {
                "task_id": made["id"],
                "changes": {"description": "hello", "tags": ["mcp"]},
            },
        )
    )
    assert updated["description"] == "hello"
    assert (
        value(
            call(
                system,
                tokens,
                "update_task",
                {"task_id": made["id"], "changes": {"description": None}},
            )
        )["description"]
        == ""
    )
    done = value(call(system, tokens, "complete_task", {"task_id": made["id"]}))
    assert done["completed_task"]["status"] == "completed"
    assert done["spawned_task"]["id"] != made["id"]
    assert call(system, tokens, "complete_task", {"task_id": made["id"]})["isError"]
    with system["sessions"]() as db:
        assert db.query(Task).count() == 2
        assert db.query(MCPAudit).filter_by(tool="create_task").count() == 1
        assert db.query(MCPAudit).filter_by(tool="complete_task").count() == 1


def test_parallel_creation_retry_is_atomic(system):
    _, tokens = connect(system)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(system, tokens), range(2)))
    ids = {value(r)["id"] for r in results}
    assert len(ids) == 1
    with system["sessions"]() as db:
        assert db.query(Task).count() == db.query(MCPAudit).count() == 1


def test_other_users_tasks_are_private_even_for_admin(system):
    _, tokens = connect(system)
    task_id = value(create(system, tokens))["id"]
    _, admin = connect(system, user="admin")
    assert value(call(system, admin, "list_tasks"))["items"] == []
    assert call(system, admin, "get_task", {"task_id": task_id})["isError"]
    assert call(
        system,
        admin,
        "update_task",
        {"task_id": task_id, "changes": {"name": "stolen"}},
    )["isError"]
    assert call(
        system,
        admin,
        "assign_task",
        {
            "task_id": task_id,
            "assignee_user_id": system["ids"]["admin"],
            "request_key": "assign-key-0001",
        },
    )["isError"]


def test_assignment_permissions_and_retry(system):
    _, tokens = connect(system)
    users = value(call(system, tokens, "list_assignable_users"))["items"]
    assert {u["username"] for u in users} == {"manager", "member"}
    task_id = value(create(system, tokens))["id"]
    with system["sessions"]() as db:
        db.add(TaskFollow(task_id=task_id, follower_user_id=system["ids"]["manager"]))
        db.commit()
    args = {
        "task_id": task_id,
        "assignee_user_id": system["ids"]["outsider"],
        "request_key": "assign-key-0001",
    }
    assert call(system, tokens, "assign_task", args)["isError"]
    args["assignee_user_id"] = system["ids"]["member"]
    result = value(call(system, tokens, "assign_task", args))
    assert value(call(system, tokens, "assign_task", args)) == result
    assert call(system, tokens, "get_task", {"task_id": task_id})["isError"]
    with system["sessions"]() as db:
        assert db.get(Task, task_id).user_id == system["ids"]["member"]
        assert db.query(NotificationEvent).filter_by(event_type="assigned").count() == 1
        assert db.query(TaskFollow).filter_by(task_id=task_id).count() == 0
        db.get(User, system["ids"]["member"]).manager_id = None
        db.commit()
    assert call(system, tokens, "assign_task", args)["isError"]


def test_open_subtasks_and_assignment_tree_protection(system):
    _, tokens = connect(system)
    parent = value(create(system, tokens))["id"]
    child = value(create(system, tokens, key="child-key-0001", parent_task_id=parent))[
        "id"
    ]
    assert call(system, tokens, "complete_task", {"task_id": parent})["isError"]
    for task_id in [parent, child]:
        assert call(
            system,
            tokens,
            "assign_task",
            {
                "task_id": task_id,
                "assignee_user_id": system["ids"]["member"],
                "request_key": "assign-key-0001",
            },
        )["isError"]
    value(call(system, tokens, "complete_task", {"task_id": child}))
    value(call(system, tokens, "complete_task", {"task_id": parent}))


def test_archive_restore_and_schema_validation(system):
    _, tokens = connect(system)
    assert create(system, tokens, due_date="2026-10-01T12:00:00")["isError"]
    made = value(create(system, tokens, due_date="2026-10-01T12:00:00-06:00"))
    assert made["due_date_utc"] == "2026-10-01T18:00:00Z"
    assert (
        value(call(system, tokens, "archive_task", {"task_id": made["id"]}))["status"]
        == "deleted"
    )
    assert (
        value(call(system, tokens, "restore_task", {"task_id": made["id"]}))["status"]
        == "active"
    )
    assert call(system, tokens, "list_tasks", {"limit": 500})["isError"]


def test_consent_csrf_and_unregistered_redirect(system):
    reg = registration(system)
    c = system["client"]
    params = {
        "client_id": reg["client_id"],
        "response_type": "code",
        "redirect_uri": REDIRECT,
        "resource": BASE + "/mcp",
        "code_challenge": CHALLENGE,
    }
    response = c.get(
        "/authorize", params={**params, "redirect_uri": "https://attacker.example"}
    )
    assert response.status_code == 400 and "location" not in response.headers
    response = c.get("/authorize", params=params)
    url = response.headers["location"]
    page = c.get(url)
    assert (
        c.post(
            url, data={"action": "login", "username": "manager", "password": PASSWORD}
        ).status_code
        == 403
    )
    assert (
        c.post(url, data={"action": "approve", "csrf_token": csrf(page)}).status_code
        == 401
    )
    assert (
        c.post(
            "/register",
            json={
                "redirect_uris": ["https://attacker.example"],
                "token_endpoint_auth_method": "none",
            },
        ).status_code
        == 400
    )
    assert c.post("/mcp/unknown", json={}).status_code == 403


def test_user_can_disconnect_only_own_connection(system):
    _, tokens = connect(system)
    c = system["client"]
    page = c.get("/profile/connections")
    assert page.status_code == 200
    with system["sessions"]() as db:
        row = db.query(MCPConnection).first()
        cid = row.id
    assert c.post(f"/profile/connections/{cid}/revoke").status_code == 403
    assert (
        c.post(
            f"/profile/connections/{cid}/revoke", data={"csrf_token": csrf(page)}
        ).status_code
        == 303
    )
    assert rpc(system, tokens["access_token"], "tools/list").status_code == 401


def test_restart_keeps_registered_clients_and_tokens(system):
    reg, tokens = connect(system, method="client_secret_post")
    from app.mcp.auth import TimeboardOAuth

    restarted = TimeboardOAuth(system["settings"], system["sessions"])
    with system["sessions"]() as db:
        assert (
            restarted.client(db, reg["client_id"]).client_secret == reg["client_secret"]
        )
        assert restarted.access(db, tokens["access_token"]).subject == str(
            system["ids"]["manager"]
        )


@pytest.mark.parametrize(
    "method", ["none", "client_secret_post", "client_secret_basic"]
)
def test_protocol_revocation_supports_each_client_method(system, method):
    reg, tokens = connect(system, method=method)
    data = {"token": tokens["refresh_token"]}
    headers = {}
    if method == "client_secret_basic":
        credentials = base64.b64encode(
            (reg["client_id"] + ":" + reg["client_secret"]).encode()
        ).decode()
        headers["Authorization"] = "Basic " + credentials
    else:
        data["client_id"] = reg["client_id"]
        if method == "client_secret_post":
            data["client_secret"] = reg["client_secret"]
    response = system["client"].post("/revoke", data=data, headers=headers)
    assert response.status_code == 200, response.text
    assert rpc(system, tokens["access_token"], "tools/list").status_code == 401
    assert (
        system["client"].post("/revoke", data=data, headers=headers).status_code == 200
    )


def test_wrong_client_cannot_revoke_or_exchange(system):
    reg, tokens = connect(system)
    other = registration(system)
    c = system["client"]
    assert (
        c.post(
            "/revoke",
            data={"client_id": other["client_id"], "token": tokens["refresh_token"]},
        ).status_code
        == 200
    )
    assert rpc(system, tokens["access_token"], "tools/list").status_code == 200
    code = authorize(system, reg)
    assert exchange(system, other, code).status_code == 400
    assert exchange(system, reg, code).status_code == 200


def test_disconnect_does_not_revoke_another_users_connection(system):
    _, manager = connect(system)
    with system["sessions"]() as db:
        cid = db.query(MCPConnection).first().id
    _, member = connect(system, user="member")
    c = system["client"]
    page = c.get("/profile/connections")
    assert (
        c.post(
            f"/profile/connections/{cid}/revoke", data={"csrf_token": csrf(page)}
        ).status_code
        == 303
    )
    assert rpc(system, manager["access_token"], "tools/list").status_code == 200
    assert rpc(system, member["access_token"], "tools/list").status_code == 200


def test_purged_creation_keeps_idempotency_tombstone(system):
    _, tokens = connect(system)
    task_id = value(create(system, tokens))["id"]
    with system["sessions"]() as db:
        db.delete(db.get(Task, task_id))
        db.commit()
        assert db.query(MCPAudit).first().task_id is None
    assert create(system, tokens)["isError"]
    with system["sessions"]() as db:
        assert db.query(Task).count() == 0


def test_authorization_expiry_and_consent_browser_binding(system):
    reg = registration(system)
    c = system["client"]
    response = c.get(
        "/authorize",
        params={
            "client_id": reg["client_id"],
            "response_type": "code",
            "redirect_uri": REDIRECT,
            "resource": BASE + "/mcp",
            "code_challenge": CHALLENGE,
        },
    )
    url = response.headers["location"]
    assert c.get(url).status_code == 200
    c.cookies.clear()
    assert c.get(url).status_code == 400
    code = authorize(system, reg)
    with system["sessions"]() as db:
        db.get(MCPGrant, digest(code)).expires_at = int(time.time()) - 1
        db.commit()
    assert exchange(system, reg, code).status_code == 400


def test_machine_body_limits_and_client_limit(system):
    c = system["client"]
    assert c.post("/register", content=b"x" * 16385).status_code == 413
    assert c.post("/mcp", content=b"x" * (1024 * 1024 + 1)).status_code == 413
    system["settings"].mcp.max_clients = 1
    registration(system)
    assert (
        c.post(
            "/register",
            json={"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none"},
        ).status_code
        == 400
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"app": {"base_url": "http://example.com"}},
        {"app": {"base_url": BASE + "/prefix"}},
        {"demo": {"enabled": True}},
        {
            "mcp": {
                "enabled": True,
                "allowed_redirect_uris": ["https://*.example.com/callback"],
            }
        },
        {
            "mcp": {
                "enabled": True,
                "allowed_redirect_uris": ["https://example.com/callback#fragment"],
            }
        },
    ],
)
def test_invalid_enabled_configuration_fails_closed(system, changes):
    data = yaml.safe_load(system["config"].read_text())
    data.update(changes)
    system["config"].write_text(json.dumps(data))
    get_settings.cache_clear()
    with pytest.raises(ValueError):
        get_settings()
