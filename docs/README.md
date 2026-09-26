# Project website

Static site published from `docs/`; the application API is served separately by FastAPI.

`python scripts/sync_docs.py` regenerates the package/route reference, machine-readable project metadata, and sitemap. CI runs `--check` plus internal-link/asset tests. Update the narrative HTML whenever behavior changes. Preserve release status: this maintenance branch is not a release or a live-site deployment.

Preview: `python -m http.server 8080 --directory docs`. The deployment uses the custom domain in `CNAME`, so absolute links assume that site root.
