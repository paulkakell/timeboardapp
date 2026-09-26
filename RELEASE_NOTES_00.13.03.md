# TimeboardApp 00.13.03 — Nested subtasks

Date: September 26, 2026. Previous release: v00.13.02.

## Repair

The detail view previously rendered breadth-first traversal order with depth-only indentation. With two branches this placed A's descendants below B. It now renders each branch contiguously, in stable creation/ID order. Traversal is iterative and skips duplicate, cyclic and disconnected entries.

Subtask details keep their parent context on invalid edits. Child edits and list actions return to the parent view. Each child shows its status; owners and administrators can complete/delete active children with existing CSRF protection and cascade confirmation. Authorized managers can add subtasks but do not gain edit/complete permissions. Validation errors are not rendered to unauthorized editors.

Desktop and mobile dashboard entries identify and link their parent and provide Add subtask shortcuts. The dashboard intentionally remains a flat filtered, sorted, paginated list; open a task to see the complete nested hierarchy. Parent metadata is prefetched, and inconsistent cross-owner metadata is hidden.

## Compatibility and deployment

No database migration, dependency change, API contract change, or rewrite of existing task relationships. Creation, cloning, recurrence, and cascade rules are retained and regression-tested. Upgrading source does not upgrade a running deployment. Back up the database/settings and current image, install the reviewed source and rebuild/restart the application through the established deployment procedure. Earlier 00.13.00 migration precautions still apply to older installations.

## Validation

See VALIDATION_REPORT_00.13.03.md and the exact-commit CI artifacts attached to the pull request. Local tests alone are not release acceptance. The existing main-only publication gates remain unchanged; a candidate branch neither publishes a release nor deploys a website/application.

## Rollback

Retain v00.13.02 and the matching image and backups. Revert this change on a reviewed branch with a new forward-moving patch version; do not move published tags. No schema rollback is needed, but preserve/reconcile tasks changed after an application upgrade before restoring older data.

## Commit notes

```text
fix(subtasks): restore branch ordering and parent navigation (00.13.03)

Keep descendants beneath the correct parent in task detail views.
Retain hierarchy on validation errors and preserve nested return links.
Add desktop/mobile parent links, child statuses and protected actions.
Add 24 focused regression cases and Chromium hierarchy coverage.
Synchronize version, docs, badges and release metadata.
```
