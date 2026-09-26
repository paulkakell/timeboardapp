# Validation scope for 00.13.01

This patch corrects release-intent detection. The original failing publication job is 108459388374 in run 36261828606. Its application regression, browser/security and container jobs succeeded; publication alone failed on an existing tag targeting the prior release commit. Do not describe that original workflow run as fully successful.

## Required evidence

The patch PR preserves exact tested commit IDs, full pytest/JUnit outputs, application coverage, dependency inventories/audits, static scans, release-tooling lint/type results, browser smoke output and container evidence. Versioned release publication and GitHub Pages deployment happen only after merging; a passing PR does not establish either event.

Run the following before review:

```sh
python -m pytest -q
python scripts/sync_docs.py --check
python scripts/release_metadata.py --check
python -m compileall -q app scripts
```

The CI workflow repeats complete regression tests on Python 3.12 and 3.13. It installs the hash-locked dependencies, checks pip compatibility, runs current dependency audits and the existing medium/high Bandit gate, verifies npm/vendor reproducibility, runs real Chromium flows, and builds/checks the non-root read-only container with matching version/revision labels. Ruff and Pyright remain scoped to release tooling and its tests; no whole-application strict typing claim is made.

## Added regression cases

Read-only release planning and direct publisher invocation both skip documentation-only pushes with an unchanged version. Tests create local Git histories, including the existing v00.13.00 tag at the earlier commit. New version pushes, multi-commit push ranges, and manual retries are covered. Missing, initial, malformed and non-ancestor histories fail closed, as do decreasing versions. Tag conflicts, annotated-tag cycles, incomplete releases and stale-main publication still fail. Exact-commit source packaging and idempotent retries use mocked GitHub writes, not actual test releases.

## Compatibility, security and rollback review

No application logic, authentication/authorization, database schema, configuration contract or dependency version changes are introduced. The current dependency locks and vendored package bytes are retained. Old version files are parsed, never executed. Read-only planning has no GitHub API calls. Only the gated publication job receives contents-write permission, and it rechecks release intent itself. Existing tag and release protection is not disabled.

No core application I/O or query paths change, so no new load test is claimed. Existing structured application logs, health checks and limitations remain. Publication planning emits a commit and boolean decision to logs and the Actions output file, never credentials. Rollback is a reviewed source revert/new patch release; all published tags stay intact. The application-data migration precautions of 00.13.00 remain applicable when upgrading from older versions.

See the patch PR for the final executed counts and outstanding limitations. Prior 00.13.00 reports remain historical and are not substituted for the new run.
