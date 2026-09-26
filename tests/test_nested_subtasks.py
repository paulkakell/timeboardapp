"""Nested-subtask ordering, rendered controls, permissions, and persisted flows."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape

from app.utils.task_tree import build_descendant_rows

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)


def node(task_id, parent_id=None, *, name=None, user_id=1, status="active"):
    return SimpleNamespace(id=task_id, parent_task_id=parent_id, name=name or f"Task {task_id}",
                           user_id=user_id, status=status, task_type="Nested", due_date_utc=NOW,
                           description="", url="", tags=[], parent=None)


def sample_nodes():
    return [node(2, 1), node(3, 1), node(4, 2), node(5, 3), node(6, 4)]


def test_tree_order_keeps_each_branch_contiguous():
    rows = build_descendant_rows(reversed(sample_nodes()), root_task_id=1)
    assert [(row["task"].id, row["depth"]) for row in rows] == [(2, 0), (4, 1), (6, 2), (3, 0), (5, 1)]


def test_tree_handles_deep_nesting_without_recursion():
    rows = build_descendant_rows((node(i, i - 1) for i in range(2, 3002)), root_task_id=1)
    assert len(rows) == 3000
    assert rows[-1]["depth"] == 2999


def test_tree_skips_root_cycles_duplicates_and_disconnected_nodes():
    rows = build_descendant_rows([node(1, 3), node(2, 1), node(3, 2), node(2, 1), node(99, 100)], root_task_id=1)
    assert [row["task"].id for row in rows] == [2, 3]


def test_tree_empty_root():
    assert build_descendant_rows([], root_task_id=1) == []


class Elements(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.elements = []
        self.form_depth = 0
        self.max_form_depth = 0
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))
        if tag == "form":
            self.form_depth += 1
            self.max_form_depth = max(self.max_form_depth, self.form_depth)

    def handle_endtag(self, tag):
        if tag == "form":
            self.form_depth -= 1

    def attrs(self, tag):
        return [attrs for name, attrs in self.elements if name == tag]


def render(template, **extra):
    env = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": "{% block content %}{% endblock %}"}),
        FileSystemLoader(str(ROOT / "app/templates")),
    ]), autoescape=select_autoescape(["html"]))
    env.filters.update(dt_local=str, linkify=str, safe_task_url=str)
    values = dict(mode="edit", can_edit=True, can_follow=False, current_user=SimpleNamespace(id=1, is_admin=False),
                  request=SimpleNamespace(session={"csrf_token": "test-csrf"}, url=SimpleNamespace(path="/tasks/1/edit")),
                  task=node(1), next_url="/dashboard?page=2&tag=work", parent_task=None,
                  name="Root", task_type="Nested", due_date="", description="", url="", recurrence_type="none",
                  recurrence_interval="", recurrence_times="", tags="", error=None,
                  descendant_rows=build_descendant_rows(sample_nodes(), root_task_id=1))
    values.update(extra)
    return env.get_template(template).render(**values)


def test_render_subtask_actions_status_and_return_link():
    nodes = sample_nodes()
    nodes[2].status = "completed"
    text = render("task_form.html", descendant_rows=build_descendant_rows(nodes, root_task_id=1))
    parsed = Elements(text)
    assert [int(a["data-subtask-id"]) for _, a in parsed.elements if "data-subtask-id" in a] == [2, 4, 6, 3, 5]
    assert "completed" in text
    actions = [a.get("action") for a in parsed.attrs("form")]
    assert "/tasks/2/complete" in actions and "/tasks/2/delete" in actions
    assert "/tasks/4/complete" not in actions
    child_link = next(a["href"] for a in parsed.attrs("a") if a.get("href", "").startswith("/tasks/2/edit"))
    assert parse_qs(urlsplit(child_link).query)["next"] == ["/tasks/1/edit?next=/dashboard%3Fpage%3D2%26tag%3Dwork"]
    assert all(a.get("value") == "test-csrf" for a in parsed.attrs("input") if a.get("name") == "csrf_token")
    assert parsed.max_form_depth == 1 and parsed.form_depth == 0


def test_render_read_only_manager_has_no_mutation_forms():
    text = render("task_form.html", current_user=SimpleNamespace(id=9, is_admin=False), can_edit=False, can_follow=True)
    actions = [a.get("action", "") for a in Elements(text).attrs("form")]
    assert not any(a.endswith(("/complete", "/delete", "/clone")) for a in actions)
    assert "Add subtask" in text
    assert ">Save</button>" not in text


def test_render_escapes_nested_names():
    text = render("task_form.html", descendant_rows=[{"task": node(2, 1, name='<script>alert("x")</script>'), "depth": 100}])
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert 'padding-left: 96px' in text


@pytest.mark.parametrize("template", ["dashboard.html", "dashboard_mobile.html"])
def test_render_dashboard_exposes_named_parent_and_creation(template):
    child = node(2, 1)
    child.parent = node(1, name="Parent <one>")
    text = render(template, tasks=[{"task": child, "time_left": "1h", "time_left_class": "tl-0-8"}],
                  return_to="/dashboard?page=2", return_to_q="%2Fdashboard%3Fpage%3D2",
                  page_size_options=[25], total_count=1, page_start=1, page_end=1, total_pages=1)
    assert "Subtask of" in text and "Parent &lt;one&gt;" in text
    links = [a.get("href", "") for a in Elements(text).attrs("a")]
    assert "/tasks/1/edit?next=%2Fdashboard%3Fpage%3D2" in links
    assert "/tasks/new?parent_task_id=2&next=%2Fdashboard%3Fpage%3D2" in links


def test_render_parent_does_not_expose_foreign_owner_metadata():
    child = node(2, 1)
    child.parent = node(1, name="Private parent", user_id=99)
    text = render("_task_parent.html", t=child, return_to_q="%2Fdashboard")
    assert "Private parent" not in text and "unavailable parent" in text
    assert not Elements(text).attrs("a")


# These integration fixtures intentionally import the actual application lazily.
# The pure ordering/rendering tests above can also run without a server install.
@pytest.fixture
def store(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.config import get_settings
    from app.db import Base
    from app import crud

    config = tmp_path / "settings.yml"
    config.write_text(f"app:\n  timezone: UTC\ndatabase:\n  path: {tmp_path / 'test.db'}\n")
    monkeypatch.setenv("TIMEBOARDAPP_SETTINGS", str(config))
    get_settings.cache_clear()
    monkeypatch.setattr(crud, "notify_task_event", lambda *a, **kw: None)
    monkeypatch.setattr(crud, "_notify_task_followers_in_app", lambda *a, **kw: 0)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        yield db
    engine.dispose()
    get_settings.cache_clear()


@pytest.fixture
def users(store):
    from app.crud import create_user
    manager = create_user(store, username="manager", email="manager@example.invalid", password="nested-test-password")
    owner = create_user(store, username="owner", email="owner@example.invalid", password="nested-test-password", manager_id=manager.id)
    stranger = create_user(store, username="stranger", email="stranger@example.invalid", password="nested-test-password")
    return SimpleNamespace(owner=owner, manager=manager, stranger=stranger)


@pytest.fixture
def client_for(store):
    from contextlib import contextmanager
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.middleware.sessions import SessionMiddleware
    from app.browser_security import BrowserSecurityMiddleware
    from app.db import get_db
    from app.routers import ui

    @contextmanager
    def open_client(user):
        application = FastAPI()
        application.include_router(ui.router)
        application.dependency_overrides[get_db] = lambda: store
        application.add_middleware(BrowserSecurityMiddleware)
        application.add_middleware(SessionMiddleware, secret_key="isolated-nested-test-session")
        with TestClient(application, follow_redirects=False) as client:
            login = client.get("/login")
            token = next(a["value"] for a in Elements(login.text).attrs("input") if a.get("name") == "csrf_token")
            client.headers["x-csrf-token"] = token
            response = client.post("/login", data={"username": user.username, "password": "nested-test-password"})
            assert response.status_code == 303, response.text
            dashboard = client.get("/dashboard")
            refreshed = next(a["content"] for a in Elements(dashboard.text).attrs("meta") if a.get("name") == "csrf-token")
            client.headers["x-csrf-token"] = refreshed
            yield client
    return open_client


@pytest.fixture
def tree(store, users):
    from app.crud import create_task
    def add(name, parent=None):
        return create_task(store, owner=users.owner, name=name, task_type="Nested", due_date=NOW,
                           parent_task_id=parent.id if parent else None, send_notifications=False)
    root = add("Root")
    a = add("Branch A", root)
    b = add("Branch B", root)
    aa = add("A child", a)
    bb = add("B child", b)
    aaa = add("A grandchild", aa)
    return SimpleNamespace(root=root, a=a, b=b, aa=aa, bb=bb, aaa=aaa, all=[root, a, b, aa, bb, aaa])


def test_ui_persisted_tree_order_and_nested_creation(store, users, client_for, tree):
    from app.models import Task
    with client_for(users.owner) as client:
        response = client.get(f"/tasks/{tree.root.id}/edit")
        assert response.status_code == 200
        assert [r["task"].id for r in response.context["descendant_rows"]] == [tree.a.id, tree.aa.id, tree.aaa.id, tree.b.id, tree.bb.id]
        response = client.post("/tasks/new", data={"name": "Fourth level", "task_type": "Nested", "parent_task_id": str(tree.aaa.id), "next": f"/tasks/{tree.root.id}/edit"})
        assert response.status_code == 303
        store.expire_all()
        child = store.query(Task).filter_by(name="Fourth level").one()
        assert child.parent_task_id == tree.aaa.id and child.user_id == users.owner.id
        reloaded = client.get(response.headers["location"])
        assert child.id in [r["task"].id for r in reloaded.context["descendant_rows"]]


def test_ui_nested_edit_returns_to_parent(store, users, client_for, tree):
    destination = f"/tasks/{tree.root.id}/edit?next=%2Fdashboard%3Fpage%3D2"
    with client_for(users.owner) as client:
        response = client.post(f"/tasks/{tree.aa.id}/edit", data={"name": "Updated child", "task_type": "Nested", "next": destination})
        assert response.status_code == 303 and response.headers["location"] == destination
        store.expire_all()
        assert tree.aa.parent_task_id == tree.a.id
        assert "Updated child" in client.get(destination).text


@pytest.mark.parametrize("invalid", [{"due_date": "not-a-date"}, {"recurrence_type": "bad-rule"}])
def test_ui_validation_errors_keep_parent_and_descendants(users, client_for, tree, invalid):
    with client_for(users.owner) as client:
        response = client.post(f"/tasks/{tree.a.id}/edit", data={"name": "Branch A", "task_type": "Nested", "next": "/calendar", **invalid})
        assert response.status_code == 400
        assert response.context["parent_task"].id == tree.root.id
        assert [r["task"].id for r in response.context["descendant_rows"]] == [tree.aa.id, tree.aaa.id]
        assert response.context["next_url"] == "/calendar"


@pytest.mark.parametrize("role", ["manager", "stranger"])
def test_ui_unauthorized_edit_error_does_not_disclose_hierarchy(users, client_for, tree, role):
    with client_for(getattr(users, role)) as client:
        response = client.post(f"/tasks/{tree.a.id}/edit", data={"name": "tampered", "task_type": "Nested", "due_date": "not-a-date"})
        assert response.status_code == 303
        assert "A grandchild" not in response.text
        assert tree.a.name == "Branch A"


def test_ui_manager_adds_for_parent_owner_without_mutation_controls(store, users, client_for, tree):
    from app.models import Task
    with client_for(users.manager) as client:
        viewed = client.get(f"/tasks/{tree.root.id}/edit")
        assert viewed.status_code == 200 and not viewed.context["can_edit"]
        assert not any(a.get("action", "").endswith(("/complete", "/delete")) for a in Elements(viewed.text).attrs("form"))
        response = client.post("/tasks/new", data={"name": "Manager child", "task_type": "Nested", "parent_task_id": str(tree.a.id), "assignee_user_id": str(users.stranger.id)})
        assert response.status_code == 303
        child = store.query(Task).filter_by(name="Manager child").one()
        assert child.user_id == users.owner.id and child.parent_task_id == tree.a.id
        client.post(f"/tasks/{tree.a.id}/complete", data={"cascade": "1"})
        store.expire_all()
        assert all(t.status == "active" for t in tree.all)


@pytest.mark.parametrize("action,status", [("complete", "completed"), ("delete", "deleted")])
def test_ui_cascade_requires_confirmation_and_preserves_links(store, users, client_for, tree, action, status):
    before = [(t.id, t.parent_task_id) for t in tree.all]
    with client_for(users.owner) as client:
        url = f"/tasks/{tree.root.id}/{action}"
        blocked = client.post(url, data={"next": f"/tasks/{tree.root.id}/edit"})
        assert blocked.status_code == 409
        assert all(t.status == "active" for t in tree.all)
        assert {t["id"] for t in blocked.context["open_tasks"]} == {t.id for t in tree.all[1:]}
        response = client.post(url, data={"cascade": "1", "next": f"/tasks/{tree.root.id}/edit"})
        assert response.status_code == 303
        store.expire_all()
        assert all(t.status == status for t in tree.all)
        assert [(t.id, t.parent_task_id) for t in tree.all] == before
        assert status in client.get(response.headers["location"]).text


def test_ui_rejects_missing_csrf_for_subtask_action(users, client_for, tree):
    with client_for(users.owner) as client:
        del client.headers["x-csrf-token"]
        response = client.post(f"/tasks/{tree.a.id}/complete", data={"cascade": "1"})
        assert response.status_code == 403
        assert tree.a.status == "active"


def test_ui_clone_preserves_all_nested_relationships(store, users, client_for, tree):
    from app.crud import list_descendant_tasks
    from app.models import Task
    with client_for(users.owner) as client:
        response = client.post(f"/tasks/{tree.root.id}/clone", data={})
        assert response.status_code == 303
        new_id = int(urlsplit(response.headers["location"]).path.split("/")[2])
        cloned = store.get(Task, new_id)
        descendants = list_descendant_tasks(store, root_task_id=new_id)
        assert cloned.name == "Root (Copy)" and len(descendants) == 5
        by_name = {t.name: t for t in descendants}
        assert by_name["A grandchild"].parent_task_id == by_name["A child"].id
        assert by_name["A child"].parent_task_id == by_name["Branch A"].id
        assert not {t.id for t in descendants} & {t.id for t in tree.all}


def test_recurrence_rebuilds_complete_nested_tree(store, users, tree):
    from app.crud import complete_task, list_descendant_tasks
    from app.models import RecurrenceType
    tree.root.recurrence_type = RecurrenceType.post_completion
    tree.root.recurrence_interval_seconds = 86400
    store.commit()
    _, spawned = complete_task(store, task=tree.root, current_user=users.owner, when_utc=NOW.replace(tzinfo=None), cascade_subtasks=True)
    assert spawned is not None
    children = list_descendant_tasks(store, root_task_id=spawned.id)
    assert len(children) == 5 and all(t.status == "active" for t in children)
    by_name = {t.name: t for t in children}
    assert by_name["A grandchild"].parent_task_id == by_name["A child"].id
    assert all(t.due_date_utc == NOW.replace(tzinfo=None) + timedelta(days=1) for t in children)


def test_ui_hides_foreign_owner_branches_in_legacy_data(store, users, client_for, tree):
    tree.a.user_id = users.stranger.id
    store.commit()
    with client_for(users.owner) as client:
        response = client.get(f"/tasks/{tree.root.id}/edit")
        assert [r["task"].id for r in response.context["descendant_rows"]] == [tree.b.id, tree.bb.id]
        assert "A grandchild" not in response.text


def test_list_tasks_prefetches_parent_without_extra_queries(store, users, tree):
    from sqlalchemy import event
    from app.crud import list_tasks
    store.expunge_all()
    owner = store.get(type(users.owner), users.owner.id)
    queries = []
    def record(*args):
        queries.append(args[2])
    event.listen(store.bind, "before_cursor_execute", record)
    try:
        tasks = list_tasks(store, current_user=owner, limit=3)
        count = len(queries)
        for task in tasks:
            if task.parent_task_id:
                assert task.parent.name
        assert len(queries) == count
    finally:
        event.remove(store.bind, "before_cursor_execute", record)
