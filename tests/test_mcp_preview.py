"""Publication boundaries for the explicitly authorized source preview."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def publisher():
    spec = importlib.util.spec_from_file_location("preview_publisher", ROOT / "scripts/publish_mcp_preview.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def context(p):
    return {"GITHUB_REPOSITORY": p.REPOSITORY, "GITHUB_REF": f"refs/heads/{p.BRANCH}",
            "GITHUB_EVENT_NAME": "push", "GITHUB_SHA": "a" * 40,
            "GH_TOKEN": "synthetic-test-token", "GITHUB_RUN_ID": "123"}


@pytest.mark.parametrize("key,value", [
    ("GITHUB_REPOSITORY", "other/fork"), ("GITHUB_REF", "refs/heads/main"),
    ("GITHUB_REF", "refs/heads/another-branch"), ("GITHUB_EVENT_NAME", "pull_request"),
    ("GITHUB_EVENT_NAME", "pull_request_target"), ("GITHUB_SHA", "bad"),
    ("GH_TOKEN", ""), ("GITHUB_RUN_ID", "bad"),
])
def test_preview_rejects_untrusted_context(publisher, key, value):
    env = context(publisher)
    env[key] = value
    with pytest.raises(ValueError):
        publisher.validate_context(env, "a" * 40)


def test_preview_accepts_exact_branch_commit_only(publisher):
    env = context(publisher)
    assert publisher.validate_context(env, "a" * 40) == "a" * 40
    with pytest.raises(ValueError):
        publisher.validate_context(env, "b" * 40)


def checks(p):
    run = {"head_sha": "a" * 40, "head_branch": p.BRANCH, "event": "push",
           "path": ".github/workflows/audit.yml"}
    jobs = [{"name": name, "status": "completed", "conclusion": "success"} for name in sorted(p.REQUIRED_JOBS)]
    return run, jobs


def test_preview_requires_all_successful_jobs(publisher):
    run, jobs = checks(publisher)
    publisher.validate_checks(run, jobs, "a" * 40)
    with pytest.raises(ValueError):
        publisher.validate_checks(run, jobs[:-1], "a" * 40)
    with pytest.raises(ValueError):
        publisher.validate_checks(run, jobs + [jobs[0]], "a" * 40)


@pytest.mark.parametrize("conclusion", ["failure", "skipped", "cancelled", None])
def test_preview_failed_or_skipped_check_blocks_publication(publisher, conclusion):
    run, jobs = checks(publisher)
    jobs[0]["conclusion"] = conclusion
    with pytest.raises(ValueError):
        publisher.validate_checks(run, jobs, "a" * 40)


@pytest.mark.parametrize("key,value", [("head_sha", "b" * 40), ("head_branch", "main"),
                                        ("event", "pull_request"), ("path", "other.yml")])
def test_preview_requires_its_own_workflow_run(publisher, key, value):
    run, jobs = checks(publisher)
    run[key] = value
    with pytest.raises(ValueError):
        publisher.validate_checks(run, jobs, "a" * 40)


def test_preview_tag_is_never_moved(publisher):
    publisher.require_tag({"object": {"type": "commit", "sha": "a" * 40, "url": "ignored"}}, "a" * 40)
    with pytest.raises(ValueError):
        publisher.require_tag({"object": {"type": "commit", "sha": "b" * 40}}, "a" * 40)
    with pytest.raises(ValueError):
        publisher.require_tag({"object": {"type": "tag", "sha": "a" * 40}}, "a" * 40)


def test_preview_workflow_is_separate_from_main_release(publisher):
    workflow = yaml.safe_load((ROOT / ".github/workflows/audit.yml").read_text())
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["mcp-preview"]
    assert set(job["needs"]) == {"regression", "container"}
    assert f"github.ref == 'refs/heads/{publisher.BRANCH}'" in job["if"]
    assert "github.event_name == 'push'" in job["if"]
    assert "github.repository == 'paulkakell/timeboardapp'" in job["if"]
    assert job["permissions"] == {"contents": "write", "actions": "read"}
    assert job["steps"][0]["with"]["persist-credentials"] is False
    assert "github.ref == 'refs/heads/main'" in workflow["jobs"]["release"]["if"]
    assert not publisher.TAG.startswith("v")
