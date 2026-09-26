# Project website

Static site published from `docs/`; the application API is served separately by FastAPI.

`python scripts/sync_docs.py` regenerates the package/route reference, machine-readable project metadata, and sitemap. CI runs `--check` plus internal-link/asset tests. Update the narrative HTML whenever behavior changes. Run `python scripts/release_metadata.py` after updating the source version or release date. This generates version badges and release notices on every HTML page; CI rejects stale metadata. Source release, published GitHub Release, and a running application deployment are separate states. See `../VERSIONING.md` for the complete release procedure.

Preview: `python -m http.server 8080 --directory docs`. The deployment uses the custom domain in `CNAME`, so absolute links assume that site root.
