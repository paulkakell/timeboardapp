# Validation — 00.13.03 nested subtasks

## Scope and reproduction

Baseline source: 75197342d977013b8cb2637e4df3a23d1051ab05 (v00.13.02).
A root with A, B, A-child, B-child and A-grandchild reproduced the original breadth-first/indentation mismatch. Expected presentation: A, A-child, A-grandchild, B, B-child. A validation error also removed the parent/subtask context.

## Local evidence

Python 3.13.5: all 24 added tests passed. The broader local application-test run had 204 passes, one skipped check and one failure caused by missing APScheduler while importing app.main in the existing security-header test. Local packages differ from the reviewed locks; this is not a clean release validation. The later version/document metadata edits must be checked in CI as well.

The new tests cover branch order and depth, iterative 3,000-node presentation traversal (not a database load benchmark), cycles/duplicates, HTML escaping, desktop/mobile parent links, CSRF and owner/administrator/manager permissions, saved multi-level creation and edits, error context, cascade confirmation, cloning, recurrence, cross-owner metadata suppression and parent prefetching.

## Required exact-commit CI evidence

The unchanged CI workflow installs the hash-locked dependencies on Python 3.12 and 3.13, runs the full suite/coverage and source/website checks, audits dependencies, scans the application, verifies vendored assets, and builds/smokes the non-root read-only container. The Chromium smoke now creates both branches through actual Add subtask controls, reloads and edits descendants, verifies statuses and branch order, follows parent navigation in mobile mode, and confirms a cascading completion. Synthetic-only screenshots are saved to the CI evidence artifact.

Use the pull request's final check results and artifacts (including source.zip, commit.txt, pytest.xml, coverage.xml and browser.txt) as the authoritative acceptance evidence. This checked-in report describes reproduction, local evidence and the CI contract; it does not predetermine a passing CI result or claim deployment. No production database, external notification delivery or exhaustive security/performance certification is included.
