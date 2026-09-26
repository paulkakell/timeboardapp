# Versioning and release procedure

## Version identity

`app/version.py:APP_VERSION` is the authoritative application version. Display it as `xx.xx.xx` (release.feature.fix) and tag it as `vxx.xx.xx`. Maintenance patch `00.13.03` repairs nested-subtask presentation and navigation without a database migration, API change or dependency update. Its previous published release is `00.13.02`. Source release `00.13.00` advanced the feature component from `00.12.03` and resets the fix component because PR #30 adds the private GPT Actions API and release infrastructure. Breaking authentication, pagination, and container changes are explicitly documented.

While the release component is `00`, feature updates may include documented breaking changes; a patch must not introduce new compatibility breaks. Advancing the release component is an explicitly planned release-line change. This padded project format is not strict SemVer syntax. npm requires the unpadded equivalent: `00.13.03` maps to `0.13.3`. Both npm manifest roots must agree; vendored dependency versions remain unchanged by an application version bump.

## Prepare a release

1. Update `APP_VERSION`, `release.json` (date, previous version, classification and PR), both npm manifest roots, the Docker OCI version label, and the Compose image tag.
2. Add a classified changelog entry, `RELEASE_NOTES_<version>.md`, validation guidance, rollback requirements, and copyable commit notes. Preserve historical reports and tags.
3. Update current README/website source-version notices. Run `python scripts/sync_docs.py`, then `python scripts/release_metadata.py`. These generate route/dependency metadata, the sitemap, an accessible local version badge, and consistent badge blocks in the README and every website HTML page.
4. Run `python -m pytest -q`, `python scripts/sync_docs.py --check`, and `python scripts/release_metadata.py --check`. CI repeats the full suite on Python 3.12/3.13 plus audits, browser checks, and a fresh container build.
5. Merge only the checked PR head. All pushes to `main` still run full CI. After those checks, a read-only release-intent job compares the literal application versions at the push event's `before` and `after` commits. If the version increased, the gated write-capable release job publishes `v<APP_VERSION>` at the exact checked commit with source archives, notes, and a checksum manifest. If the version is unchanged, publication is skipped; no existing tag or asset is inspected for replacement or modified. Source commits after a published release remain unreleased until a new version is assigned. A conflicting tag, incomplete release, non-ancestor push history, version decrease, or stale main branch still blocks a release attempt. Existing releases are never overwritten.

## Release-intent examples and recovery

- Ordinary push: deleting historical documentation while keeping `APP_VERSION = "00.13.01"` runs all CI checks and emits `publish=false`. No release is requested.
- New release: a push that advances `00.13.01` to `00.13.02` emits `publish=true`, including when the version change is followed by release-note commits within that same push. Update all release metadata before merging.
- Explicit retry: select `workflow_dispatch` on `main` to request publication after all checks. A release already published at that exact commit is a no-op. A tag pointing elsewhere is still an error; assign a new version instead of moving or deleting the tag.
- Initial publication or missing history: a zero/missing `before` commit is not guessed. Use a reviewed explicit manual run for initial publication. The intent and publication checkouts use full history (`fetch-depth: 0`) so normal push ranges are available.
- `python scripts/publish_release.py --plan` performs no GitHub API calls or writes. In a main-branch Actions context it appends a literal `publish=true` or `publish=false` to `GITHUB_OUTPUT`. Invoking the publisher without `--plan` repeats the same intent check before any publication API calls.

The historical failed job in run 36261828606 correctly refused to move `v00.13.00`; the defect was requesting publication for a later unchanged-version push. Re-running that historical job executes its original commit, not this correction. Keep the old run as evidence and use the new checked workflow after this patch is merged.

The publisher uses only the scoped Actions token, reads tracked files through `git archive`, and never includes local settings, databases, or working-directory secrets. Only its job has `contents: write`; PR validation cannot publish. This publishes source, not a registry image or a running application.

## Badge semantics

The local source-version badge derives from `APP_VERSION` and links to the exact release tag. The live CI badge explicitly selects `branch=main&event=push`; it must not display another branch's result or a hardcoded passing image. Python badges describe tested runtimes, not every supported interpreter. The ChatGPT badge says **private GPT Actions**, not MCP, multi-user OAuth, or universal integration certification. The MIT badge links to the tracked license. No static vulnerability-free or coverage claim is used.

## Rollback

For a 00.13.03 subtask rollback, retain `v00.13.02` and revert the UI patch on a reviewed branch with a new forward-moving patch version. No schema migration was introduced; existing task relationships remain intact. For a 00.13.02 community/workflow rollback, retain `v00.13.00`; there are no new database, authentication, configuration, or dependency migrations in this patch. Revert the workflow correction on a reviewed branch with a new forward-moving patch version, rather than moving an existing tag. That revert restores the old release-trigger limitation. For upgrades crossing the 00.13.00 authentication change, retain `v00.12.03`, its release artifacts, the old running image, and a matching pre-upgrade database plus settings backup. A source revert does not roll back running containers or migrated password hashes. For a source rollback, revert the merge commit with `git revert -m 1 <merge-sha>` on a new reviewed branch, assign a new forward-moving release version, and do not move existing tags. For an application rollback, restore the old image and matching data together after preserving and reconciling any post-upgrade writes. Test this on staging before production.
