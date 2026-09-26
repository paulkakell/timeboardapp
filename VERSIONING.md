# Versioning and release procedure

## Version identity

`app/version.py:APP_VERSION` is the authoritative application version. Display it as `xx.xx.xx` (release.feature.fix) and tag it as `vxx.xx.xx`. Source release `00.13.00` advances the feature component from `00.12.03` and resets the fix component because PR #30 adds the private GPT Actions API and release infrastructure. Breaking authentication, pagination, and container changes are explicitly documented.

While the release component is `00`, feature updates may include documented breaking changes; a patch must not introduce new compatibility breaks. Advancing the release component is an explicitly planned release-line change. This padded project format is not strict SemVer syntax. npm requires the unpadded equivalent: `00.13.00` maps to `0.13.0`. Both npm manifest roots must agree; vendored dependency versions remain unchanged by an application version bump.

## Prepare a release

1. Update `APP_VERSION`, `release.json` (date, previous version, classification and PR), both npm manifest roots, the Docker OCI version label, and the Compose image tag.
2. Add a classified changelog entry, `RELEASE_NOTES_<version>.md`, validation guidance, rollback requirements, and copyable commit notes. Preserve historical reports and tags.
3. Update current README/website source-version notices. Run `python scripts/sync_docs.py`, then `python scripts/release_metadata.py`. These generate route/dependency metadata, the sitemap, an accessible local version badge, and consistent badge blocks in the README and every website HTML page.
4. Run `python -m pytest -q`, `python scripts/sync_docs.py --check`, and `python scripts/release_metadata.py --check`. CI repeats the full suite on Python 3.12/3.13 plus audits, browser checks, and a fresh container build.
5. Merge only the checked PR head. On a successful push to `main`, the gated release job publishes `v<APP_VERSION>` at that exact checked commit, source archives, notes, and a checksum manifest. A conflicting existing tag or a moved main branch aborts publication. Existing releases are never overwritten. Manual reruns on the same main commit are safe; changing the source requires a new version.

The publisher uses only the scoped Actions token, reads tracked files through `git archive`, and never includes local settings, databases, or working-directory secrets. Only its job has `contents: write`; PR validation cannot publish. This publishes source, not a registry image or a running application.

## Badge semantics

The local source-version badge derives from `APP_VERSION` and links to the exact release tag. The live CI badge explicitly selects `branch=main&event=push`; it must not display another branch's result or a hardcoded passing image. Python badges describe tested runtimes, not every supported interpreter. The ChatGPT badge says **private GPT Actions**, not MCP, multi-user OAuth, or universal integration certification. The MIT badge links to the tracked license. No static vulnerability-free or coverage claim is used.

## Rollback

Retain `v00.12.03`, its release artifacts, the old running image, and a matching pre-upgrade database plus settings backup. A source revert does not roll back running containers or migrated password hashes. For a source rollback, revert the merge commit with `git revert -m 1 <merge-sha>` on a new reviewed branch, assign a new forward-moving release version, and do not move existing tags. For an application rollback, restore the old image and matching data together after preserving and reconciling any post-upgrade writes. Test this on staging before production.
