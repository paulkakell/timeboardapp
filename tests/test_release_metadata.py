from __future__ import annotations

import importlib.util
import json
import re
import runpy
from pathlib import Path
from xml.etree import ElementTree

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = runpy.run_path(str(ROOT / "scripts/release_metadata.py"))


def test_release_version_identity():
    data = GENERATOR["release_data"]()
    assert data["tag"] == "v" + data["version"]
    assert re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{2}", data["version"])
    assert data["npm_version"] == ".".join(str(int(part)) for part in data["version"].split("."))
    assert json.loads((ROOT / "package.json").read_text())["version"] == data["npm_version"]
    lock = json.loads((ROOT / "package-lock.json").read_text())
    assert lock["version"] == lock["packages"][""]["version"] == data["npm_version"]
    assert f'org.opencontainers.image.version="{data["version"]}"' in (ROOT / "Dockerfile").read_text()
    assert yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["timeboardapp"]["image"] == f'timeboardapp:{data["version"]}'
    project = json.loads((ROOT / "docs/assets/project-data.json").read_text())
    assert project["version"] == data["version"]
    assert project["release_tag"] == data["tag"]
    assert project["release_status"] == "source release"


def test_generated_release_badges_are_current():
    for path, content in GENERATOR["outputs"]().items():
        assert path.read_text() == content, f"Regenerate {path}"
        if path.suffix == ".html":
            assert content.count(GENERATOR["START"]) == 1
            assert 'branch=main&amp;event=push' in content
    svg = ElementTree.parse(ROOT / "docs/assets/badges/version.svg")
    assert svg.getroot().attrib["aria-label"] == "source version: " + GENERATOR["release_data"]()["version"]


def test_badge_generator_is_idempotent_and_rejects_duplicates():
    text = GENERATOR["decorate_html"]("<html><body><main>Test</main></body></html>")
    assert GENERATOR["decorate_html"](text) == text
    with pytest.raises(ValueError):
        GENERATOR["decorate_html"](text + GENERATOR["START"])
    with pytest.raises(ValueError):
        GENERATOR["decorate_html"]("<html><body>No main</body></html>")


def test_current_docs_do_not_claim_an_unreleased_old_version():
    for path in (ROOT / "docs").rglob("*.html"):
        text = path.read_text().lower()
        assert "unreleased maintenance" not in text, path
        assert "base application version 00.12.03" not in text, path
    readme = (ROOT / "README.md").read_text()
    assert "branch=main&event=push" in readme
    assert "private%20GPT%20Actions" in readme
    assert "docker-image.yml/badge" not in readme


def test_versioned_notes_and_changelog_exist():
    data = GENERATOR["release_data"]()
    for prefix in ("RELEASE_NOTES", "VALIDATION_REPORT"):
        assert (ROOT / f'{prefix}_{data["version"]}.md').is_file()
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert re.search(r"^## (\d{2}\.\d{2}\.\d{2})", changelog, re.M)[1] == data["version"]
    assert "BREAKING" in changelog
    assert "#30" in changelog
    notes = (ROOT / f'RELEASE_NOTES_{data["version"]}.md').read_text()
    assert "## Commit notes" in notes and "rollback" in notes.lower()


def test_release_job_is_main_only_and_gated():
    workflow = yaml.safe_load((ROOT / ".github/workflows/audit.yml").read_text())
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["release"]
    assert set(job["needs"]) == {"regression", "container"}
    assert "github.ref == 'refs/heads/main'" in job["if"]
    assert "github.event_name == 'push'" in job["if"]
    assert "github.repository == 'paulkakell/timeboardapp'" in job["if"]
    assert job["permissions"] == {"contents": "write"}
    assert workflow["jobs"]["regression"]["strategy"]["matrix"]["python"] == GENERATOR["release_data"]()["python_versions"]


@pytest.fixture
def publisher():
    spec = importlib.util.spec_from_file_location("release_publisher", ROOT / "scripts/publish_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("key,value", [
    ("GITHUB_REPOSITORY", "someone/fork"),
    ("GITHUB_REF", "refs/heads/feature"),
    ("GITHUB_EVENT_NAME", "pull_request"),
    ("GITHUB_EVENT_NAME", "pull_request_target"),
    ("GITHUB_SHA", "not-a-sha"),
    ("GH_TOKEN", ""),
])
def test_publication_rejects_unsafe_context(publisher, key, value):
    env = {"GITHUB_REPOSITORY": "paulkakell/timeboardapp", "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "push", "GITHUB_SHA": "a" * 40, "GH_TOKEN": "test-only"}
    env[key] = value
    with pytest.raises(ValueError):
        publisher.validate_context(env, "a" * 40)


def test_publication_accepts_checked_main_and_rejects_mismatched_checkout(publisher):
    env = {"GITHUB_REPOSITORY": "paulkakell/timeboardapp", "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "push", "GITHUB_SHA": "a" * 40, "GH_TOKEN": "test-only"}
    assert publisher.validate_context(env, "a" * 40) == "a" * 40
    with pytest.raises(ValueError):
        publisher.validate_context(env, "b" * 40)


def test_existing_tag_cannot_move(publisher):
    publisher.require_tag_commit({"object": {"type": "commit", "sha": "a" * 40}}, "a" * 40)
    with pytest.raises(ValueError):
        publisher.require_tag_commit({"object": {"type": "commit", "sha": "b" * 40}}, "a" * 40)
    with pytest.raises(ValueError):
        publisher.require_tag_commit({"object": {"type": "tree", "sha": "a" * 40}}, "a" * 40)
