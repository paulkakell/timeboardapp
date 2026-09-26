# Project website

Static site published from `docs/`; the application API is served separately by FastAPI.

`python scripts/sync_docs.py` regenerates the package/route reference, machine-readable project metadata, and sitemap. CI runs `--check` plus internal-link/asset tests. Update the narrative HTML whenever behavior changes. Run `python scripts/release_metadata.py` after updating the source version or release date. This generates version badges and release notices on every HTML page; CI rejects stale metadata. Source release, published GitHub Release, and a running application deployment are separate states. See `../VERSIONING.md` for the complete release procedure.

Preview: `python -m http.server 8080 --directory docs`. The deployment uses the custom domain in `CNAME`, so absolute links assume that site root.

The community guide is `docs/community.html`. It links the canonical root [Code of Conduct](../CODE_OF_CONDUCT.md) and [Contributing Guidelines](../CONTRIBUTING.md); do not maintain separate policy copies in the website. Issue forms and the pull request template are tested with the full suite and become available in GitHub after merging to the default branch.
