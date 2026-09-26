# TimeboardApp 00.13.02

Date: September 26, 2026. Intended release tag: `v00.13.02`. Previous published release: `v00.13.00`. PR: #32. The 00.13.01 candidate was not published and is superseded by this expanded maintenance patch.

## Community additions

Add `CODE_OF_CONDUCT.md` with participation standards, private reporting routes, fair enforcement, and reconsideration. It is an original project policy, not an unattributed copy of another code. Add `CONTRIBUTING.md` with isolated development setup, reporting examples, complete validation, dependency/security practices, versioning, compatibility/migration review, documentation, and rollback instructions.

Add three GitHub issue forms: bug report, feature request, and documentation correction. The issue chooser directs security and conduct concerns to their policies and disables blank reports. Fields request actionable public information and warn against credentials or private data. No new labels, automatic assignees, external reporting mailbox, or secrets are configured.

Add `.github/pull_request_template.md` covering scope, classification, issue references, version/changelog, test evidence, security, dependencies, configuration, observability, performance, database/API compatibility, documentation, release artifacts, rollback, and copyable commit notes. README and a new website community guide link the canonical files.

## Included release fix

Keep the 00.13.01 correction: all main pushes run CI, but publication is requested only for a version increase across the complete push range or an explicit manual run. Existing tags and assets are not overwritten. The historical publication failure in run 36261828606 was caused by an overly broad trigger, not failing application tests. The immutable-tag guard remains enabled.

## Version and compatibility

Synchronize application/UI/OpenAPI version 00.13.02, npm 0.13.2, container metadata, documentation, generated reference, and badges. No application business logic, dependency versions, authentication, configuration contract, or database schema changes are introduced. The contributor workflow changes by offering structured issue forms instead of blank issues. GitHub makes these templates available after they are merged into the default branch; no live form submission is claimed before then.

## Validation and rollback

The full CI matrix validates Python 3.12 and 3.13, existing application/regression tests, new community-file tests, scoped lint/types, dependency/static audits, website/OpenAPI checks, Chromium, and container build/runtime behavior. Community tests cover form structure, required inputs, privacy guidance, links, template placement, and setup-example syntax. Consult PR #32 and exact-commit artifacts for executed counts and limitations.

Preserve v00.13.00 and its artifacts. A rollback is a reviewed revert/new forward-moving version, not a moved tag. There is no new database migration in this patch. Reverting the community additions removes the chooser/templates; reverting the included publisher correction restores the old release-trigger limitation. Upgrades from before 00.13.00 still require the matching database/settings/image backups documented for that release. No running application, registry image, or production data is modified by this source update.

## Commit notes

```text
chore(community): add contribution policies and templates (00.13.02)

Add Code of Conduct and project-specific Contributing Guidelines.
Add bug, feature and documentation forms plus a pull request checklist.
Keep sensitive reports out of public issues and link canonical policies.
Test community files and synchronize version, website, notes and badges.
Retain the release-intent fix and existing tag protections.

Refs: #32
Previous published release: v00.13.00
Supersedes unpublished candidate: 00.13.01
```
