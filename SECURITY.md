# Security policy

Report suspected vulnerabilities privately to the repository owner. Use GitHub private vulnerability reporting when enabled; otherwise establish a private reporting channel before sharing exploit details. Never include live access tokens, passwords, or private task data in public issues.

Current source uses Argon2id migration, credential-bound sessions/JWTs, CSRF protection, owner-scoped integration tokens, restricted outbound notification transport, private atomic credential/backup files, non-root containers, and dependency locks. These controls do not certify a deployment as secure.

Version 00.14.00 includes experimental native MCP and per-user OAuth, disabled by default. Enable it only with an HTTPS application origin and exact OAuth callback allowlist. Access is scoped to the connected account's tasks, including for administrators; assignment transfers only owned, active, standalone tasks to eligible users. Never weaken callback matching, resource binding, scope checks, ownership checks, or browser CSRF protection to troubleshoot a connection. The existing private GPT Actions tokens and native MCP OAuth credentials are separate and cannot be substituted for each other.

Use **Profile → Connected applications** to revoke an MCP connection. Disabling MCP hides its endpoints but does not revoke stored connections; revoke first if later re-enablement should require fresh consent. Password changes and signing-key rotation invalidate relevant access. Protect full SQLite/settings backups because they preserve OAuth state and retry records. JSON exports omit that state and cannot preserve MCP retry protection. See [MCP_TESTING.md](MCP_TESTING.md#authentication-and-storage) for lifetime, replay, storage, and rollback details.

Operate behind HTTPS and a narrowly trusted proxy. Protect/encrypt the database and backups, restrict inbound/outbound network access, configure shared edge rate limits where needed, and run one application scheduler process unless job coordination is implemented. Do not enable public demo mode on real data.

Read [AUDIT_REPORT.md](AUDIT_REPORT.md) for residual risks and test limitations. The supported container/test matrix is documented in README; unreviewed dependency major upgrades and unsupported historical builds are not covered by the maintenance validation. Local MCP protocol and browser tests do not establish live ChatGPT compatibility or provider delivery. Public HTTPS and live ChatGPT login, refresh, isolation, and revocation remain operator acceptance checks.
