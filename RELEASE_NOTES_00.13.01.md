# TimeboardApp 00.13.01

Status: unpublished candidate, superseded by 00.13.02 in PR #32. Retained as preparation history, not a published release.

Date: September 26, 2026. Intended tag: `v00.13.01`. Previous release: `v00.13.00`. Classification: bug fix. No new application compatibility breaks.

## Correction

The release workflow introduced in PR #30 requested publication after every successful main-branch push. Later historical-document deletions kept application version 00.13.00 but advanced main to 81cbec352bfde90ed7861460736ffdb6b53e7c5e. CI run 36261828606 passed its tests and container jobs, then the publisher correctly refused to move the existing release tag. The overly broad publication trigger, not the tag-protection check, was defective.

A read-only release-intent job now compares literal versions at the push event's before and after commits. An unchanged version means CI only. A version increase requests publication after all checks pass. The full push range includes multi-commit releases even when release-note edits follow the version commit. An explicit manual main-branch run can request a retry. The publisher repeats the intent check when invoked directly.

Existing tag-conflict, checkout, repository, fork, PR-event, incomplete-release and stale-main protections remain. No existing tag is moved and no release asset is overwritten. Historical failed runs are not rewritten or reclassified.

## Version and documentation

Application/OpenAPI/UI version: 00.13.01. npm metadata: 0.13.1. Container version labels, Compose tag, README, website notices, generated reference and all version badges are synchronized. CI badges remain live main/push indicators, not static passing images. The obsolete deleted audit-branch push trigger is removed.

## Validation and scope

Regression coverage includes unchanged-version documentation commits, full multi-commit push ranges, manual retries, invalid event payloads, missing/rewritten history, version regressions, tag conflicts, annotated tags, incomplete releases, stale main, and exact-commit packaging with mocked GitHub writes. The real CI run additionally performs the full Python 3.12/3.13 suites, release-tooling lint/types, dependency/static audits, OpenAPI and website checks, browser smoke and container build/runtime checks. Consult the patch PR and its artifacts for executed results; this file does not assert that an unrun job has passed.

Dependencies and application business logic are unchanged. No new database migration, configuration variable, authentication change or application I/O path is introduced. No load test is needed for this release-tooling-only fix. Existing low-severity static findings, legacy warnings, coverage gaps and deployment acceptance requirements remain.

## Upgrade and rollback

This patch changes repository release behavior and displayed version metadata. It does not deploy a running application or publish a registry image. Keep v00.13.00 and its artifacts. Source rollback is a reviewed revert with a new forward-moving version, not tag reassignment; reverting restores the old publication-trigger limitation. No database rollback is needed for this patch alone. Upgrades from before 00.13.00 still require the matching database/settings/image backup precautions described in that release.

## Commit notes

```text
fix(release): publish only for explicit version changes (00.13.01)

Gate write-capable publication with a read-only full-push version check.
Skip ordinary unchanged-version pushes without moving tags or assets.
Preserve explicit retries and existing tag/checkout/repository safeguards.
Add real-Git regression tests and synchronize versions, notes and badges.

Refs: #30; Actions run 36261828606, job 108459388374
Previous release: v00.13.00
```
