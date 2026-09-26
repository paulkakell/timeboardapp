# Validation record for 00.13.00

The release workflow is the authoritative final execution record. It preserves the exact tested commit, source snapshot, JUnit XML, coverage XML, installed package inventory, vulnerability audits, Bandit output, browser smoke output, and container diagnostics. This file describes scope rather than predicting a test result.

## Required gates

Python 3.12 and 3.13 execute the entire suite, including new version/badge and publisher guards, plus existing unit, API/integration, migration, security, concurrency, and regression tests. The 3.13 job also checks npm/vendored-file reproducibility and Chromium flows. A separate fresh container job validates Compose, OCI version identity, non-root/read-only startup, health/version reporting, and private initial credentials. Source compilation, JavaScript syntax, OpenAPI validation, generated website reference, internal links/fragments, and all release badges are checked.

The release job depends on both validation jobs, accepts only the intended repository and main branch, verifies that the checked commit is still main, and refuses conflicting tags. It publishes tracked-source archives and a checksummed manifest linking to the main workflow. It does not change runtime application code, database schema, or dependencies beyond changes already assessed in PR #30.

## Review for this versioning change

No new authentication, authorization, logging, database, network-service, or application configuration behavior is introduced by the version/badge edits. `app/version.py` propagates to OpenAPI, health reporting, and the existing application footer. The npm root version changes without dependency re-resolution. Container version labels and the local Compose image tag identify the built source; no registry image is claimed published.

The publisher is read-only outside its explicitly gated release job. Its Actions token is not placed in command arguments, source files, or archived files. API/subprocess arguments are passed as lists without shell evaluation. There are no tag moves, force pushes, overwritten releases, or production data changes. The badge generator escapes HTML and reads only fixed repository paths.

Database migration, concurrency, and performance regressions already covered by the suite are repeated; the release metadata itself does not change core queries or I/O paths. Production load/restore testing, third-party notification delivery, live GPT acceptance, OS-image scanning, full historical secret scanning, and universal static typing remain separate limitations of the audit. CI passing does not remove those limitations or the recorded low-severity findings.

## Historical comparison

The baseline run 36252534894 reported 49 passed and one startup failure. The reviewed implementation run 36256060593 reported 110 passed on each runtime before these metadata tests. Do not reuse those counts for a later source commit. The release manifest records the final main-branch run for this version.

## Rollback and artifacts

Preserve previous tag `v00.12.03` and the pre-upgrade database, settings, and container image. Revert source through a reviewed, forward-versioned commit; never move a published tag. Runtime rollback requires the matching data snapshot because Argon2id migration is not backwards compatible with the old password library. Release notes and `VERSIONING.md` provide the procedure and its data-loss precautions.

### Release tooling quality scope

The Python 3.13 CI job runs Ruff 0.16.9 on the two new release scripts and their regression tests, and Pyright 1.1.414 on the two release scripts. Both tool distributions are SHA-256 pinned; the application and npm dependency locks are unchanged by this metadata update. This scoped check does not assert that the entire historical application has complete type coverage.
