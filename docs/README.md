# Project website

Static site published from `docs/`; the application API is served separately by FastAPI.

`python scripts/sync_docs.py` regenerates the package/route reference, machine-readable project metadata, and sitemap. CI runs `--check` plus internal-link/asset tests. Update the narrative HTML whenever behavior changes. Run `python scripts/release_metadata.py` after updating the source version or release date. This generates version badges and release notices on every HTML page; CI rejects stale metadata. Source release, published GitHub Release, and a running application deployment are separate states. See `../VERSIONING.md` for the complete release procedure.

Preview: `python -m http.server 8080 --directory docs`. The deployment uses the custom domain in `CNAME`, so absolute links assume that site root.

The community guide is `docs/community.html`. It links the canonical root [Code of Conduct](../CODE_OF_CONDUCT.md) and [Contributing Guidelines](../CONTRIBUTING.md); do not maintain separate policy copies in the website. Issue forms and the pull request template are tested with the full suite and become available in GitHub after merging to the default branch.

The 00.14.01 website documents RFC 9207 issuer identification, stable and callback-specific redirect handling, Windows discovery checks, and optional native MCP alongside the existing GPT Actions interface. Keep the installation, configuration, upgrade, security, architecture, API, and ChatGPT guides aligned when either integration changes. Native MCP is experimental and disabled by default; do not imply that publishing the website upgrades existing containers or proves live ChatGPT acceptance. The ChatGPT guide covers the current plugin connection flow, per-user OAuth, scoped tools, and profile disconnection. The repository's [MCP testing guide](../MCP_TESTING.md) remains the detailed isolated-test procedure.

For each release, update hand-authored version/date callouts and feature text as well as generated release badges. Preserve historical migration warnings with their applicable starting versions. Confirm that published release links point to the version whose source operators should rebuild, then verify the public site after the Pages workflow completes.
