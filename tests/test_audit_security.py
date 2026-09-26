from __future__ import annotations

import base64
import hashlib
import json
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware

from app.auth import authenticate_user, create_access_token, get_current_user_api, hash_password, verify_password
from app.browser_security import BrowserSecurityMiddleware
from app.chatgpt_schema import build_chatgpt_schema
from app.config import get_settings
from app.crud import create_task, create_user
from app.db import Base, get_db
from app.models import IntegrationToken, Task, User
from app.routers import api_auth, api_tasks, api_users, chatgpt


@pytest.fixture
def store(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.yml"
    settings_file.write_text(f"database:\n  path: {tmp_path / 'app.db'}\n")
    monkeypatch.setenv("TIMEBOARDAPP_SETTINGS", str(settings_file))
    get_settings.cache_clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    yield db
    db.close()
    engine.dispose()
    get_settings.cache_clear()


@pytest.fixture
def api(store):
    application = FastAPI()
    application.include_router(api_auth.router, prefix="/api/auth")
    application.include_router(api_tasks.router, prefix="/api/tasks")
    application.include_router(api_users.router, prefix="/api/users")
    application.include_router(chatgpt.router)
    application.include_router(chatgpt.tokens_router)
    application.dependency_overrides[get_db] = lambda: store
    return TestClient(application)


def auth_header(user):
    return {"Authorization": "Bearer " + create_access_token(subject=user.username, is_admin=user.is_admin, user=user)}


def integration(api, user, write=False):
    response = api.post("/api/integrations/tokens", headers=auth_header(user), json={"allow_write": write})
    assert response.status_code == 201, response.text
    return response.json(), {"Authorization": "Bearer " + response.json()["token"]}


def test_password_migrates_legacy_hash_without_promoting_user(store):
    password = "Old Unicode password ☃"
    salt = b"\xfb\xff\xfflegacy-salt"
    enc = lambda b: base64.b64encode(b).decode().rstrip("=").replace("+", ".")
    legacy = "$pbkdf2-sha256$200000$" + enc(salt) + "$" + enc(hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200000))
    user = create_user(store, username="admin", email="admin@example.invalid", password="original-password", is_admin=False)
    user.hashed_password = legacy
    store.commit()
    assert authenticate_user(store, "ADMIN", password).id == user.id
    assert user.hashed_password.startswith("$argon2id$")
    assert verify_password(password, user.hashed_password)
    assert not user.is_admin
    assert store.query(User).count() == 1


def test_failed_login_does_not_create_recovery_admin(store):
    assert authenticate_user(store, "admin", "wrong password") is None
    assert store.query(User).count() == 0


@pytest.mark.parametrize("encoded", ["garbage", "$pbkdf2-sha256$9999999999$YQ$YQ", "$pbkdf2-sha256$200000$!$!!", "$argon2id$bad"])
def test_malformed_password_hash_fails_closed(encoded):
    assert not verify_password("password", encoded)


def test_password_length_is_bounded():
    assert not verify_password("x" * 1025, "malformed")
    with pytest.raises(ValueError):
        hash_password("x" * 1025)


def test_password_change_revokes_jwt_and_integration_token(store, api):
    user = create_user(store, username="alice", email="alice@example.invalid", password="password123")
    access = auth_header(user)
    item, token = integration(api, user)
    assert api.get("/api/chatgpt/tasks", headers=token).status_code == 200
    assert api.patch("/api/users/me", headers=access, json={"current_password": "password123", "new_password": "newpassword123"}).status_code == 200
    assert api.get("/api/auth/me", headers=access).status_code == 401
    assert api.get("/api/chatgpt/tasks", headers=token).status_code == 401


@pytest.mark.parametrize("claim,value", [("exp", 0), ("iss", "other"), ("aud", "other"), ("kind", "integration"), ("sub", "admin"), ("pv", "wrong")])
def test_required_jwt_claims_checked(store, claim, value):
    user = create_user(store, username="alice", email="alice@example.invalid", password="password123")
    valid = create_access_token(subject=user.username, is_admin=False, user=user)
    data = jwt.decode(valid, options={"verify_signature": False})
    data[claim] = value
    bad = jwt.encode(data, get_settings().security.jwt_secret, algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        get_current_user_api(db=store, token=bad)
    assert exc.value.status_code == 401


def test_jwt_requires_exp_and_rejects_algorithm_confusion(store):
    user = create_user(store, username="alice", email="alice@example.invalid", password="password123")
    valid = create_access_token(subject=user.username, is_admin=False, user=user)
    data = jwt.decode(valid, options={"verify_signature": False})
    del data["exp"]
    for algorithm in ("HS256", "HS384"):
        bad = jwt.encode(data, get_settings().security.jwt_secret, algorithm=algorithm)
        with pytest.raises(HTTPException):
            get_current_user_api(db=store, token=bad)


def test_current_database_role_controls_authorization(store, api):
    user = create_user(store, username="admin", email="admin@example.invalid", password="password123", is_admin=True)
    header = auth_header(user)
    assert api.get("/api/users/", headers=header).status_code == 200
    user.is_admin = False
    store.commit()
    assert api.get("/api/users/", headers=header).status_code == 403


def test_integration_is_hashed_revocable_and_never_admin(store, api):
    admin = create_user(store, username="admin", email="admin@example.invalid", password="password123", is_admin=True)
    other = create_user(store, username="other", email="other@example.invalid", password="password123")
    mine = create_task(store, owner=admin, name="mine", task_type="ops", due_date=None, send_notifications=False)
    theirs = create_task(store, owner=other, name="other", task_type="ops", due_date=None, send_notifications=False)
    item, headers = integration(api, admin)
    record = store.get(IntegrationToken, item["id"])
    assert record.token_hash == hashlib.sha256(item["token"].encode()).hexdigest()
    assert item["token"] not in repr(record.__dict__)
    assert [t["id"] for t in api.get("/api/chatgpt/tasks", headers=headers).json()["items"]] == [mine.id]
    assert api.get(f"/api/chatgpt/tasks/{theirs.id}", headers=headers).status_code == 404
    assert api.get("/api/chatgpt/tasks/summary", headers=headers).json()["past_due"] == 1
    assert api.get("/api/users/", headers=headers).status_code == 401
    assert api.get("/api/chatgpt/tasks", headers=auth_header(admin)).status_code == 401
    assert api.post("/api/chatgpt/tasks", headers=headers, json={"name": "no", "task_type": "ops"}).status_code == 403
    assert api.delete(f"/api/integrations/tokens/{item['id']}", headers=auth_header(other)).status_code == 404
    assert api.delete(f"/api/integrations/tokens/{item['id']}", headers=auth_header(admin)).status_code == 204
    assert api.get("/api/chatgpt/tasks", headers=headers).status_code == 401


def test_expired_integration_token_rejected(store, api):
    user = create_user(store, username="alice", email="alice@example.invalid", password="password123")
    item, headers = integration(api, user)
    store.get(IntegrationToken, item["id"]).expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
    store.commit()
    assert api.get("/api/chatgpt/tasks", headers=headers).status_code == 401


def test_actions_crud_subtasks_recurrence_and_nullable_fields(store, api):
    user = create_user(store, username="alice", email="alice@example.invalid", password="password123")
    item, headers = integration(api, user, write=True)
    created = api.post("/api/chatgpt/tasks", headers=headers, json={"name": "Parent", "task_type": "ops", "description": "clear me", "url": "https://example.com", "due_date": "2026-09-26T12:00:00Z", "recurrence_type": "post_completion", "recurrence_interval": "1d"})
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    child = api.post("/api/chatgpt/tasks", headers=headers, json={"name": "Child", "task_type": "ops", "parent_task_id": task_id})
    assert child.status_code == 201
    for suffix in (f"/api/chatgpt/tasks/{task_id}/complete",):
        assert api.post(suffix, headers=headers).status_code == 409
    assert api.delete(f"/api/chatgpt/tasks/{task_id}", headers=headers).status_code == 409
    assert api.patch(f"/api/chatgpt/tasks/{task_id}", headers=headers, json={"description": None, "url": None}).json()["url"] == ""
    assert api.post(f"/api/chatgpt/tasks/{child.json()['id']}/complete", headers=headers).status_code == 200
    done = api.post(f"/api/chatgpt/tasks/{task_id}/complete", headers=headers)
    assert done.status_code == 200, done.text
    assert done.json()["spawned_task"] is not None
    assert api.post(f"/api/chatgpt/tasks/{task_id}/complete", headers=headers).status_code == 409
    assert api.post(f"/api/chatgpt/tasks/{task_id}/restore", headers=headers).status_code == 200


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,hello", "file:///etc/passwd", "https://user:pass@example.com", "//example.com"])
def test_task_urls_cannot_execute_code(store, url):
    user = create_user(store, username="alice", email="alice@example.invalid", password="password123")
    with pytest.raises(ValueError):
        create_task(store, owner=user, name="test", task_type="ops", due_date=None, url=url, send_notifications=False)
    from app.urls import safe_task_url
    assert safe_task_url(url) == ""


def test_actions_pagination_schema_and_payload_bound(store, api):
    user = create_user(store, username="alice", email="alice@example.invalid", password="password123")
    for i in range(12):
        create_task(store, owner=user, name=f"Task {i}", task_type="ops", description="x" * 20000, due_date=None, send_notifications=False)
    item, headers = integration(api, user)
    first = api.get("/api/chatgpt/tasks", headers=headers)
    assert len(first.text) < 100000
    assert len(first.json()["items"]) == 10
    assert first.json()["next_offset"] == 10
    assert first.json()["items"][0]["content_truncated"] is True
    assert len(api.get("/api/chatgpt/tasks?offset=10", headers=headers).json()["items"]) == 2
    assert api.get("/api/chatgpt/tasks?limit=11", headers=headers).status_code == 422
    assert api.get("/api/chatgpt/tasks?offset=-1", headers=headers).status_code == 422
    schema = build_chatgpt_schema(api.app, "https://tasks.example.com")
    operations = [(method, operation) for path in schema["paths"].values() for method, operation in path.items()]
    ids = [op["operationId"] for method, op in operations]
    assert len(ids) == len(set(ids)) == 8
    assert all(path.startswith("/api/chatgpt/") for path in schema["paths"])
    assert set(schema["components"]["securitySchemes"]) == {"TimeboardIntegrationToken"}
    for method, operation in operations:
        assert operation["x-openai-isConsequential"] == (method != "get")
        assert len(operation.get("summary", "")) <= 300
        assert operation["security"] == [{"TimeboardIntegrationToken": []}]
    assert schema["servers"] == [{"url": "https://tasks.example.com"}]


def test_actions_require_timezone_aware_dates(store, api):
    user = create_user(store, username="alice", email="alice@example.invalid", password="password123")
    item, headers = integration(api, user, write=True)
    assert api.post("/api/chatgpt/tasks", headers=headers, json={"name": "No zone", "task_type": "ops", "due_date": "2026-09-26T12:00:00"}).status_code == 422


def test_standard_api_open_subtask_conflict_and_pagination(store, api):
    user = create_user(store, username="alice", email="alice@example.invalid", password="password123")
    parent = create_task(store, owner=user, name="parent", task_type="ops", due_date=None, send_notifications=False)
    create_task(store, owner=user, name="child", task_type="ops", due_date=None, parent_task_id=parent.id, send_notifications=False)
    header = auth_header(user)
    assert api.post(f"/api/tasks/{parent.id}/complete", headers=header).status_code == 409
    assert api.delete(f"/api/tasks/{parent.id}", headers=header).status_code == 409
    assert len(api.get("/api/tasks/?limit=1", headers=header).json()) == 1
    assert api.get("/api/tasks/?limit=201", headers=header).status_code == 422
    assert len(api.get("/api/tasks/?search=child", headers=header).json()) == 1


def test_csrf_origin_body_limit_and_secure_cookie():
    application = FastAPI()
    application.add_middleware(BrowserSecurityMiddleware, base_url="https://testserver", max_body_bytes=1024)
    application.add_middleware(SessionMiddleware, secret_key="x" * 48, https_only=True)
    @application.get("/csrf")
    def token(request: Request):
        return {"token": request.session["csrf_token"]}
    @application.post("/change")
    async def change(request: Request):
        return {"value": dict(await request.form()).get("value")}
    client = TestClient(application, base_url="https://testserver")
    first = client.get("/csrf")
    assert "secure" in first.headers["set-cookie"].lower()
    csrf = first.json()["token"]
    assert client.post("/change", data={"value": "x"}).status_code == 403
    assert client.post("/change", data={"csrf_token": csrf, "value": "valid"}).json() == {"value": "valid"}
    assert client.post("/change", headers={"X-CSRF-Token": csrf}, data={"value": "valid"}).status_code == 200
    assert client.post("/change", headers={"Origin": "https://evil.example"}, data={"csrf_token": csrf}).status_code == 403
    assert client.post("/change", headers={"Sec-Fetch-Site": "cross-site"}, data={"csrf_token": csrf}).status_code == 403
    assert client.post("/change", content=b"x" * 1025).status_code == 413


def test_login_throttling_is_bounded():
    from app.rate_limit import LoginLimiter
    limiter = LoginLimiter(limit=2, capacity=1)
    assert limiter.retry_after("a") == 0
    assert limiter.retry_after("a") == 0
    assert limiter.retry_after("a") > 0
    assert limiter.retry_after("b") > 0
    assert len(limiter.entries) == 1


def test_notification_secret_round_trip():
    from app.routers.api_notifications import _redact_cfg, _merge_cfg
    config = {"headers": {"Authorization": "Bearer secret"}, "webhook_url": "https://example.com/secret", "nested": {"api_key": "secret"}}
    redacted = _redact_cfg(config)
    assert "Bearer secret" not in json.dumps(redacted)
    assert redacted["nested"]["api_key"] == "***"
    assert _merge_cfg(config, redacted) == config
    assert "webhook_url" not in _merge_cfg(config, {"webhook_url": None})


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "0.0.0.0", "224.0.0.1", "::1", "fc00::1", "::ffff:8.8.8.8"])
def test_ssrf_rejects_nonpublic_addresses(address, monkeypatch):
    from app import outbound
    monkeypatch.setattr(outbound, "get_settings", lambda: SimpleNamespace(security=SimpleNamespace(outbound_allowed_hosts=[])))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))])
    with pytest.raises(ValueError):
        outbound.resolve_destination("https://notifications.example")


def test_ssrf_explicit_lan_exception_and_mixed_dns(monkeypatch):
    from app import outbound
    monkeypatch.setattr(outbound, "get_settings", lambda: SimpleNamespace(security=SimpleNamespace(outbound_allowed_hosts=["gotify.internal"])))
    local = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))
    public = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [local])
    assert outbound.resolve_destination("https://gotify.internal")[0] == "gotify.internal"
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [public, local])
    with pytest.raises(ValueError):
        outbound.resolve_destination("https://public.example")


def test_outbound_does_not_redirect_or_leak_response(monkeypatch):
    from app import outbound
    address = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
    monkeypatch.setattr(outbound, "resolve_destination", lambda url: ("example.com", "/", 443, [address]))
    calls = []
    class Connection:
        def __init__(self, *a, **kw):
            calls.append(kw["address"])
        def request(self, *a, **kw): pass
        def getresponse(self): return SimpleNamespace(status=302, read=lambda n: b"secret")
        def close(self): pass
    monkeypatch.setattr(outbound, "_PinnedConnection", Connection)
    with pytest.raises(RuntimeError, match="HTTP 302") as exc:
        outbound.send_request(url="https://example.com")
    assert "secret" not in str(exc.value)
    assert calls == [address]
    with pytest.raises(ValueError, match="header"):
        outbound.send_request(url="https://example.com", headers={"Host": "evil"})


def test_outbound_response_size_bounded(monkeypatch):
    from app import outbound
    monkeypatch.setattr(outbound, "resolve_destination", lambda url: ("example.com", "/", 443, [None]))
    class Connection:
        def __init__(self, *a, **kw): pass
        def request(self, *a, **kw): pass
        def getresponse(self): return SimpleNamespace(status=200, read=lambda n: b"x" * n)
        def close(self): pass
    monkeypatch.setattr(outbound, "_PinnedConnection", Connection)
    with pytest.raises(RuntimeError, match="64 KiB"):
        outbound.send_request(url="https://example.com")


def test_wns_only_sends_credentials_to_microsoft(monkeypatch):
    from app import notifications
    calls = []
    monkeypatch.setattr(notifications, "_http_request", lambda **kw: calls.append(kw))
    with pytest.raises(ValueError):
        notifications._send_wns_toast(channel_uri="https://notify.windows.com.evil.example", access_token="secret", title="test", message="test")
    assert not calls
    notifications._send_wns_toast(channel_uri="https://sub.notify.windows.com/path", access_token="secret", title="test", message="test")
    assert len(calls) == 1


def test_private_files_have_owner_only_permissions(tmp_path):
    from app.private_files import write_private_text
    path = tmp_path / "secret"
    write_private_text(path, "old")
    path.chmod(0o644)
    write_private_text(path, "new")
    assert path.read_text() == "new"
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [path]


def test_templates_compile_and_all_post_forms_have_csrf():
    from app.routers.ui import templates
    import re
    for path in (Path(__file__).parents[1] / "app/templates").glob("*.html"):
        templates.get_template(path.name)
        for form in re.findall(r'<form\b[^>]*method=[\"\']post[\"\'][^>]*>(.*?)</form>', path.read_text(), re.I | re.S):
            assert 'name="csrf_token"' in form, path.name


def test_actions_schema_validates_as_openapi(api):
    validator = pytest.importorskip('openapi_spec_validator')
    validator.validate_spec(build_chatgpt_schema(api.app, 'https://tasks.example.com'))


@pytest.mark.parametrize('verb,path,body', [
    ('get','',None), ('patch','',{'name':'unauthorized'}), ('delete','',None),
    ('post','/complete',None), ('post','/restore',None),
])
def test_actions_every_task_operation_rejects_other_owner(store, api, verb, path, body):
    owner = create_user(store, username='owner', email='owner@example.invalid', password='password123')
    other = create_user(store, username='other', email='other@example.invalid', password='password123')
    task = create_task(store, owner=other, name='private', task_type='ops', due_date=None, send_notifications=False)
    _, header = integration(api, owner, write=True)
    response = api.request(verb, f'/api/chatgpt/tasks/{task.id}{path}', headers=header, json=body)
    assert response.status_code == 404
    store.refresh(task)
    assert task.name == 'private' and task.status == 'active'


def test_api_admin_and_notification_routes_require_authentication(store):
    from app.routers import api_admin, api_notifications
    application = FastAPI()
    application.include_router(api_admin.router, prefix='/api/admin')
    application.include_router(api_notifications.router, prefix='/api/notifications')
    application.dependency_overrides[get_db] = lambda: store
    client = TestClient(application)
    for route in application.routes:
        if getattr(route, 'path', '').startswith('/api/'):
            path = route.path.replace('{service_id}', '1')
            for method in route.methods:
                assert client.request(method, path).status_code == 401, (method, path)


def test_same_environment_signing_keys_fail_closed(tmp_path, monkeypatch):
    config = tmp_path/'settings.yml'
    config.write_text('database:\n  path: /tmp/test.db\n')
    monkeypatch.setenv('TIMEBOARDAPP_SETTINGS', str(config))
    monkeypatch.setenv('TIMEBOARDAPP_SESSION_SECRET', 'shared-key-with-at-least-thirty-two-characters')
    monkeypatch.setenv('TIMEBOARDAPP_JWT_SECRET', 'shared-key-with-at-least-thirty-two-characters')
    get_settings.cache_clear()
    with pytest.raises(ValueError, match='distinct'):
        get_settings()
    get_settings.cache_clear()


def test_sqlite_url_data_directory(tmp_path, monkeypatch):
    from app.paths import data_directory
    config = tmp_path/'settings.yml'
    config.write_text(f'database:\n  path: sqlite:///{tmp_path / "store/app.db"}\n')
    monkeypatch.setenv('TIMEBOARDAPP_SETTINGS', str(config)); get_settings.cache_clear()
    assert data_directory() == tmp_path/'store'
    get_settings.cache_clear()


def test_concurrent_completion_spawns_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app import crud
    engine = create_engine(f'sqlite:///{tmp_path / "race.db"}', connect_args={'check_same_thread':False, 'timeout':10})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(crud, 'notify_task_event', lambda *a, **kw: None)
    monkeypatch.setattr(crud, '_notify_task_followers_in_app', lambda *a, **kw: None)
    with factory() as db:
        user = create_user(db,username='owner',email='owner@example.invalid',password='password123')
        task = create_task(db,owner=user,name='repeat',task_type='ops',due_date=None,recurrence_type='post_completion',recurrence_interval='1h',send_notifications=False)
        user_id, task_id = user.id, task.id
    barrier = Barrier(2)
    def finish(_):
        with factory() as db:
            user, task = db.get(User,user_id), db.get(Task,task_id)
            barrier.wait(timeout=10)
            _, spawned = crud.complete_task(db,task=task,current_user=user,when_utc=datetime.now(timezone.utc).replace(tzinfo=None))
            return spawned.id if spawned else None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(finish, range(2)))
    assert sum(x is not None for x in results) == 1
    with factory() as db:
        assert db.query(Task).count() == 2
    engine.dispose()


def test_serialized_actions_page_stays_bounded(store, api):
    from app.models import Tag
    user = create_user(store,username='owner',email='owner@example.invalid',password='password123')
    tags = [Tag(name=chr(i+1)*127+str(i)) for i in range(20)]
    store.add_all(tags); store.commit()
    for i in range(10):
        item = create_task(store,owner=user,name='task'+str(i),task_type='ops',due_date=None,send_notifications=False)
        item.description='\x01'*1000
        item.tags=tags
    store.commit()
    _, header = integration(api,user)
    response=api.get('/api/chatgpt/tasks',headers=header)
    assert response.status_code == 200
    assert len(response.text)<100000
    assert 0 < len(response.json()['items']) < 10
    assert response.json()['next_offset'] == len(response.json()['items'])
