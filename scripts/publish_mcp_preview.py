"""Publish one checked branch prerelease, never a stable release or deployment."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "paulkakell/timeboardapp"
BRANCH = "fix/mcp-oauth-issuer-identification"
TAG = "mcp-issuer-test.1"
REQUIRED_JOBS = {"regression (3.12)", "regression (3.13)", "container"}


def command(args: list[str]) -> str:
    return subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def api(path: str, *, missing_ok: bool = False) -> Any:
    result = subprocess.run(
        ["gh", "api", f"repos/{REPOSITORY}/{path}"], cwd=ROOT,
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        if missing_ok and "(HTTP 404)" in result.stderr:
            return None
        raise RuntimeError(f"GitHub read failed: {path}")
    return json.loads(result.stdout)


def validate_context(env: Mapping[str, str], checkout: str) -> str:
    if env.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise ValueError("Unexpected repository")
    if env.get("GITHUB_REF") != f"refs/heads/{BRANCH}":
        raise ValueError("Only the authorized testing branch can publish this preview")
    if env.get("GITHUB_EVENT_NAME") not in {"push", "workflow_dispatch"}:
        raise ValueError("Pull requests and untrusted events cannot publish")
    sha = env.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or checkout != sha:
        raise ValueError("Checkout must match the exact workflow commit")
    if not env.get("GH_TOKEN") or not re.fullmatch(r"[0-9]+", env.get("GITHUB_RUN_ID", "")):
        raise ValueError("A scoped Actions token and run ID are required")
    return sha


def validate_checks(run: dict, jobs: list[dict], sha: str) -> None:
    if (run.get("head_sha") != sha or run.get("head_branch") != BRANCH
            or run.get("event") not in {"push", "workflow_dispatch"}
            or run.get("path") != ".github/workflows/audit.yml"):
        raise ValueError("Validation run does not match this branch commit")
    for name in REQUIRED_JOBS:
        matches = [job for job in jobs if job.get("name") == name]
        if len(matches) != 1 or matches[0].get("conclusion") != "success" or matches[0].get("status") != "completed":
            raise ValueError(f"Required validation did not pass: {name}")


def require_tag(ref: dict, sha: str) -> None:
    if ref.get("object") != {"type": "commit", "sha": sha}:
        obj = ref.get("object", {})
        if obj.get("type") != "commit" or obj.get("sha") != sha:
            raise ValueError("Existing preview tag does not match; refusing to move it")


def main() -> None:
    sha = validate_context(os.environ, command(["git", "rev-parse", "HEAD"]))
    run_id = os.environ["GITHUB_RUN_ID"]
    run = api(f"actions/runs/{run_id}")
    jobs = api(f"actions/runs/{run_id}/jobs?filter=latest&per_page=100")["jobs"]
    validate_checks(run, jobs, sha)
    if api(f"git/ref/heads/{BRANCH}")["object"]["sha"] != sha:
        raise ValueError("Testing branch moved; refusing stale publication")
    ref = api(f"git/ref/tags/{TAG}", missing_ok=True)
    if ref is not None:
        require_tag(ref, sha)
    existing = api(f"releases/tags/{TAG}", missing_ok=True)
    if existing is not None:
        if ref is None or existing.get("draft") or not existing.get("prerelease"):
            raise ValueError("Existing release requires review; refusing overwrite")
        print(f"Preview already published at {sha}: {existing['html_url']}")
        return
    dist = ROOT / "preview-dist"
    dist.mkdir(exist_ok=True)
    files = []
    for extension in ["zip", "tar.gz"]:
        target = dist / f"timeboardapp-{TAG}.{extension}"
        command(["git", "archive", f"--format={extension}", f"--prefix=timeboardapp-{TAG}/", "-o", str(target), sha])
        files.append(target)
    notes = dist / "MCP_ISSUER_TESTING.md"
    notes.write_text(command(["git", "show", f"{sha}:MCP_ISSUER_TESTING.md"]) + "\n", encoding="utf-8")
    files.append(notes)
    workflow_url = f"https://github.com/{REPOSITORY}/actions/runs/{run_id}"
    manifest = dist / "PREVIEW_MANIFEST.json"
    manifest.write_text(json.dumps({
        "tag": TAG, "commit": sha, "branch": BRANCH, "prerelease": True,
        "application_version": "00.14.00", "workflow_run": workflow_url,
        "validation": {name: "success" for name in sorted(REQUIRED_JOBS)},
        "scope": "Testing source only; no main merge, stable release, registry image or server deployment",
        "live_chatgpt_acceptance": "not performed",
        "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
    }, indent=2) + "\n", encoding="utf-8")
    files.append(manifest)
    sums = dist / "SHA256SUMS"
    sums.write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in files), encoding="utf-8")
    files.append(sums)
    body = dist / "release-body.md"
    body.write_text(
        f"Testing prerelease from `{BRANCH}` at `{sha}`.\n\n"
        f"Full Python 3.12/3.13 regression and container checks passed in [this exact-commit CI run]({workflow_url}). "
        "The Python 3.13 job also runs Chromium application/OAuth flows, audits and documentation checks.\n\n"
        "Implements OAuth authorization-response issuer identification for ChatGPT. "
        "The application still reports 00.14.00; the preview tag and commit identify this fix. "
        "The existing v00.14.00 tag is unchanged. No production deployment or live ChatGPT acceptance test is claimed.\n\n"
        "Build from this preview tag rather than v00.14.00. Read the attached MCP_ISSUER_TESTING.md for setup, "
        "isolation, Windows verification commands and rollback. Source archives and a checksum manifest are included.\n",
        encoding="utf-8",
    )
    if api(f"git/ref/heads/{BRANCH}")["object"]["sha"] != sha:
        raise ValueError("Testing branch moved during packaging")
    if ref is None:
        command(["gh", "api", "--method", "POST", f"repos/{REPOSITORY}/git/refs", "-f", f"ref=refs/tags/{TAG}", "-f", f"sha={sha}"])
    require_tag(api(f"git/ref/tags/{TAG}"), sha)
    command(["gh", "release", "create", TAG, "--repo", REPOSITORY, "--verify-tag",
             "--prerelease", "--latest=false", "--title", "TimeboardApp MCP OAuth issuer testing preview 1",
             "--notes-file", str(body), *[str(p) for p in files]])
    require_tag(api(f"git/ref/tags/{TAG}"), sha)
    result = api(f"releases/tags/{TAG}")
    if result.get("draft") or not result.get("prerelease"):
        raise RuntimeError("Release status was not the requested published prerelease")
    print(f"Published testing prerelease at {sha}: {result['html_url']}")


if __name__ == "__main__":
    main()
