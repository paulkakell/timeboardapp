# Security policy

Report suspected vulnerabilities privately to the repository owner. Use GitHub private vulnerability reporting when enabled; otherwise establish a private reporting channel before sharing exploit details. Never include live access tokens, passwords, or private task data in public issues.

The maintenance source introduces Argon2id migration, credential-bound sessions/JWTs, CSRF protection, owner-scoped integration tokens, restricted outbound notification transport, private atomic credential/backup files, non-root containers, and dependency locks. These changes do not certify a deployment as secure.

Operate behind HTTPS and a narrowly trusted proxy. Protect/encrypt the database and backups, restrict inbound/outbound network access, configure shared edge rate limits where needed, and run one application scheduler process unless job coordination is implemented. Do not enable public demo mode on real data.

Read `AUDIT_REPORT.md` for residual risks and test limitations. The supported container/test matrix is documented in README; unreviewed dependency major upgrades and unsupported historical builds are not covered by the maintenance validation.
