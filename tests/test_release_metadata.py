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
    assert re.search(r"^## (\d{2}\.\d{2}\.\d{2})", changelog, re.MULTILINE)[1] == data["version"]
    assert "BREAKING" in changelog
    assert "#30" in changelog
    notes = (ROOT / f'RELEASE_NOTES_{data["version"]}.md').read_text()
    assert "## Commit notes" in notes and "rollback" in notes.lower()


def test_release_job_is_main_only_and_gated():
    workflow = yaml.safe_load((ROOT / ".github/workflows/audit.yml").read_text())
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["release"]
    assert set(job["needs"]) == {"regression", "container", "release-intent"}
    assert "needs.release-intent.outputs.publish == 'true'" in job["if"]
    intent = workflow["jobs"]["release-intent"]
    assert intent["permissions"] == {"contents": "read"}
    assert set(intent["needs"]) == {"regression", "container"}
    for guarded_job in (intent, job):
        assert guarded_job["steps"][0]["with"]["fetch-depth"] == 0
        assert guarded_job["steps"][0]["with"]["persist-credentials"] is False
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


@pytest.fixture
def release_repo(publisher, tmp_path, monkeypatch):
    """Real Git history reproduces a documentation push after a tagged release."""
    import subprocess

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, text=True, capture_output=True, check=True
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "Release regression test")
    git("config", "user.email", "release-test@example.invalid")
    (tmp_path / "app").mkdir()
    (tmp_path / "app/version.py").write_text('APP_VERSION = "00.13.00"\n')
    (tmp_path / "old-report.md").write_text("Historical report\n")
    git("add", ".")
    git("commit", "-m", "Release 00.13.00 fixture")
    before = git("rev-parse", "HEAD")
    git("tag", "v00.13.00")
    (tmp_path / "old-report.md").unlink()
    git("add", "-u")
    git("commit", "-m", "Remove an old report without requesting a release")
    after = git("rev-parse", "HEAD")
    event_path = tmp_path / "event.json"
    event = {"before": before, "after": after, "ref": "refs/heads/main"}
    event_path.write_text(json.dumps(event))
    env = {
        "GITHUB_REPOSITORY": "paulkakell/timeboardapp",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_SHA": after,
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_OUTPUT": str(tmp_path / "output.txt"),
        "GITHUB_RUN_ID": "123",
        "GH_TOKEN": "unit-test-only",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(publisher, "ROOT", tmp_path)
    return {"git": git, "path": tmp_path, "env": env, "event": event, "before": before, "after": after}


def update_release_event(repo, **updates):
    repo["event"].update(updates)
    Path(repo["env"]["GITHUB_EVENT_PATH"]).write_text(json.dumps(repo["event"]))


def commit_version(repo, monkeypatch, version="00.13.01"):
    (repo["path"] / "app/version.py").write_text(f'APP_VERSION = "{version}"\n')
    repo["git"]("add", "app/version.py")
    repo["git"]("commit", "-m", "Change release version")
    sha = repo["git"]("rev-parse", "HEAD")
    repo["env"]["GITHUB_SHA"] = sha
    monkeypatch.setenv("GITHUB_SHA", sha)
    update_release_event(repo, after=sha)
    return sha


def forbid_github(*args, **kwargs):
    raise AssertionError("No GitHub API or publication operation is allowed")


def test_documentation_push_skips_publication_and_preserves_tag(publisher, release_repo, monkeypatch, capsys):
    monkeypatch.setattr(publisher, "api", forbid_github)
    publisher.main()
    assert "No release requested" in capsys.readouterr().out
    assert release_repo["git"]("rev-parse", "v00.13.00") == release_repo["before"]
    assert not (release_repo["path"] / "release-dist").exists()


def test_readonly_plan_skips_unchanged_version_without_github_calls(publisher, release_repo, monkeypatch):
    monkeypatch.setattr(publisher, "api", forbid_github)
    publisher.plan()
    assert Path(release_repo["env"]["GITHUB_OUTPUT"]).read_text() == "publish=false\n"


def test_version_bump_across_multi_commit_push_requests_publication(publisher, release_repo, monkeypatch):
    sha = commit_version(release_repo, monkeypatch)
    # The event spans both the documentation commit and the version increase.
    assert publisher.publication_requested(release_repo["env"], sha) is True
    monkeypatch.setattr(publisher, "api", forbid_github)
    publisher.plan()
    assert Path(release_repo["env"]["GITHUB_OUTPUT"]).read_text() == "publish=true\n"


def test_subsequent_commit_in_version_bump_push_is_not_mistaken_for_ci_only(publisher, release_repo, monkeypatch):
    commit_version(release_repo, monkeypatch)
    (release_repo["path"] / "notes.md").write_text("Release notes added after the version commit")
    release_repo["git"]("add", "notes.md")
    release_repo["git"]("commit", "-m", "Finish versioned release notes")
    sha = release_repo["git"]("rev-parse", "HEAD")
    update_release_event(release_repo, after=sha)
    assert publisher.publication_requested(release_repo["env"], sha) is True


def test_explicit_manual_run_requests_publication(publisher, release_repo, monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setattr(publisher, "api", forbid_github)
    publisher.plan()
    assert Path(release_repo["env"]["GITHUB_OUTPUT"]).read_text() == "publish=true\n"


@pytest.mark.parametrize("before", [None, "", "main", "0" * 40, "g" * 40, 123])
def test_release_intent_rejects_missing_initial_or_invalid_previous_commit(publisher, release_repo, before):
    update_release_event(release_repo, before=before)
    with pytest.raises(ValueError, match="previous push commit"):
        publisher.publication_requested(release_repo["env"], release_repo["after"])


@pytest.mark.parametrize("updates", [{"after": "a" * 40}, {"ref": "refs/heads/not-main"}])
def test_release_intent_rejects_mismatched_event(publisher, release_repo, updates):
    update_release_event(release_repo, **updates)
    with pytest.raises(ValueError, match="match the checked"):
        publisher.publication_requested(release_repo["env"], release_repo["after"])


def test_release_intent_rejects_missing_payload(publisher, release_repo):
    del release_repo["env"]["GITHUB_EVENT_PATH"]
    with pytest.raises(ValueError, match="payload is required"):
        publisher.publication_requested(release_repo["env"], release_repo["after"])


@pytest.mark.parametrize("payload", ["not json", "[]", "null"])
def test_release_intent_rejects_corrupt_payload(publisher, release_repo, payload):
    Path(release_repo["env"]["GITHUB_EVENT_PATH"]).write_text(payload)
    with pytest.raises(ValueError):
        publisher.publication_requested(release_repo["env"], release_repo["after"])


def test_release_intent_rejects_missing_history(publisher, release_repo):
    import subprocess

    update_release_event(release_repo, before="a" * 40)
    with pytest.raises(subprocess.CalledProcessError):
        publisher.publication_requested(release_repo["env"], release_repo["after"])


def test_release_intent_rejects_non_ancestor_history(publisher, release_repo):
    import subprocess

    git = release_repo["git"]
    git("checkout", "--orphan", "unrelated")
    git("add", ".")
    git("commit", "-m", "Unrelated root")
    unrelated = git("rev-parse", "HEAD")
    git("checkout", "main")
    update_release_event(release_repo, before=unrelated)
    with pytest.raises(subprocess.CalledProcessError):
        publisher.publication_requested(release_repo["env"], release_repo["after"])


def test_release_intent_rejects_version_regression(publisher, release_repo, monkeypatch):
    sha = commit_version(release_repo, monkeypatch, "00.12.99")
    with pytest.raises(ValueError, match="must not decrease"):
        publisher.publication_requested(release_repo["env"], sha)


@pytest.mark.parametrize("source", [
    '', 'APP_VERSION = "0.13.0"', 'APP_VERSION = 13',
    'APP_VERSION = "00.13.00"\nAPP_VERSION = "00.13.01"',
    'APP_VERSION = str("00.13.00")',
])
def test_source_version_requires_one_literal(publisher, source):
    with pytest.raises(ValueError):
        publisher.source_version(source)


def test_old_source_is_parsed_not_executed(publisher):
    assert publisher.source_version('raise RuntimeError("never execute")\nAPP_VERSION = "00.13.00"') == (0, 13, 0)


def test_plan_requires_output_path(publisher, release_repo, monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT")
    with pytest.raises(ValueError, match="GITHUB_OUTPUT"):
        publisher.plan()


def mock_release_data(publisher, monkeypatch):
    data = {"version": "00.13.01", "tag": "v00.13.01", "date": "2026-09-26", "previous_version": "00.13.00"}
    monkeypatch.setattr(publisher.runpy, "run_path", lambda path: {"release_data": lambda: data})
    return data


def test_requested_release_still_refuses_conflicting_tag(publisher, release_repo, monkeypatch):
    sha = commit_version(release_repo, monkeypatch)
    mock_release_data(publisher, monkeypatch)

    def api(path, **kwargs):
        if path == "git/ref/heads/main":
            return {"object": {"sha": sha, "type": "commit"}}
        if path == "git/ref/tags/v00.13.01":
            return {"object": {"sha": release_repo["before"], "type": "commit"}}
        raise AssertionError("Conflicting tag must fail before any publication")

    monkeypatch.setattr(publisher, "api", api)
    with pytest.raises(ValueError, match="refusing to move"):
        publisher.main()


def test_requested_release_rejects_stale_main(publisher, release_repo, monkeypatch):
    commit_version(release_repo, monkeypatch)
    monkeypatch.setattr(publisher, "api", lambda path: {"object": {"sha": release_repo["before"]}})
    with pytest.raises(ValueError, match="Main moved"):
        publisher.main()


@pytest.mark.parametrize("draft", [True, False])
def test_existing_release_handling_still_preserves_safety(publisher, release_repo, monkeypatch, draft):
    sha = commit_version(release_repo, monkeypatch)
    mock_release_data(publisher, monkeypatch)

    def api(path, **kwargs):
        if path.startswith("git/ref/"):
            return {"object": {"sha": sha, "type": "commit"}}
        if path == "releases/tags/v00.13.01":
            return {"draft": draft}
        raise AssertionError("Existing releases must not be overwritten")

    monkeypatch.setattr(publisher, "api", api)
    if draft:
        with pytest.raises(ValueError, match="operator review"):
            publisher.main()
    else:
        publisher.main()
        assert not (release_repo["path"] / "release-dist").exists()


def test_annotated_tag_is_peeled_and_conflicts_remain_errors(publisher, monkeypatch):
    monkeypatch.setattr(publisher, "api", lambda path: {"object": {"type": "commit", "sha": "a" * 40}})
    tag = {"object": {"type": "tag", "sha": "c" * 40}}
    publisher.require_tag_commit(tag, "a" * 40)
    with pytest.raises(ValueError, match="refusing to move"):
        publisher.require_tag_commit(tag, "b" * 40)


def test_tag_cycle_fails_closed(publisher, monkeypatch):
    tag = {"object": {"type": "tag", "sha": "c" * 40}}
    monkeypatch.setattr(publisher, "api", lambda path: tag)
    with pytest.raises(ValueError, match="does not resolve"):
        publisher.require_tag_commit(tag, "a" * 40)


def test_versioned_publication_packages_exact_commit_with_mocked_github(publisher, release_repo, monkeypatch):
    import hashlib
    import zipfile

    sha = commit_version(release_repo, monkeypatch)
    mock_release_data(publisher, monkeypatch)
    root = release_repo["path"]
    for name in ["RELEASE_NOTES_00.13.01.md", "VALIDATION_REPORT_00.13.01.md", "VERSIONING.md"]:
        (root / name).write_text("Fixture release documentation\n")
    release_repo["git"]("add", "*.md")
    release_repo["git"]("commit", "-m", "Complete release documentation")
    sha = release_repo["git"]("rev-parse", "HEAD")
    monkeypatch.setenv("GITHUB_SHA", sha)
    update_release_event(release_repo, after=sha)
    state = {"tag": None, "release": None}
    writes = []
    original_command = publisher.command

    def api(path, *, fields=None, missing_ok=False):
        if path == "git/ref/heads/main":
            return {"object": {"sha": sha, "type": "commit"}}
        if path == "git/ref/tags/v00.13.01":
            return state["tag"]
        if path == "releases/tags/v00.13.01":
            return state["release"]
        if path == "git/refs":
            assert fields == {"ref": "refs/tags/v00.13.01", "sha": sha}
            writes.append(path)
            state["tag"] = {"object": {"sha": sha, "type": "commit"}}
            return state["tag"]
        raise AssertionError(path)

    def command(args):
        if args[:3] == ["gh", "release", "create"]:
            assert args[3] == "v00.13.01" and "--verify-tag" in args
            writes.append("release")
            state["release"] = {"draft": False, "html_url": "https://example.invalid/release"}
            return ""
        assert args[0] == "git"
        return original_command(args)

    monkeypatch.setattr(publisher, "api", api)
    monkeypatch.setattr(publisher, "command", command)
    publisher.main()
    dist = root / "release-dist"
    manifest = json.loads((dist / "RELEASE_MANIFEST.json").read_text())
    assert manifest["commit"] == sha
    assert writes == ["git/refs", "release"]
    with zipfile.ZipFile(dist / "timeboardapp-00.13.01.zip") as archive:
        assert archive.read("timeboardapp-00.13.01/app/version.py") == b'APP_VERSION = "00.13.01"\n'
        assert "timeboardapp-00.13.01/event.json" not in archive.namelist()
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((dist / name).read_bytes()).hexdigest() == digest
    publisher.main()
    assert writes == ["git/refs", "release"]  # Same-commit retry performs no writes.
