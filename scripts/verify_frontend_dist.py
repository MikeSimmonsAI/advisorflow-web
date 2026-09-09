#!/usr/bin/env python3
"""
verify_frontend_dist.py — Pre-deploy integrity gate for the frontend build.

Reads frontend/dist/index.html, extracts every local asset reference
(src="..." and href="..."), and verifies each file exists on disk.

Exit 0 = all assets present, safe to deploy.
Exit 1 = one or more assets referenced in index.html are missing from dist/.
         Deployment must NOT proceed — the build is incomplete.

Usage (from repo root):
    python scripts/verify_frontend_dist.py

Run this automatically as part of any CI or pre-push hook that deploys
the frontend.  It is NOT needed when Render builds from source (Render
produces and immediately serves the dist it just built), but is useful
as a local sanity check before pushing render.yaml changes or when
testing the build locally.
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "frontend" / "dist"
INDEX_HTML = DIST_DIR / "index.html"

# Match src="..." and href="..." that reference a local path (starts with /)
# but not external URLs (http/https).
ASSET_RE = re.compile(r'(?:src|href)="(/[^"]+)"')


def main() -> int:
    if not INDEX_HTML.exists():
        print(f"ERROR: {INDEX_HTML} not found — did you run `npm run build`?")
        return 1

    html = INDEX_HTML.read_text(encoding="utf-8")
    refs = ASSET_RE.findall(html)

    if not refs:
        print("WARNING: no local asset references found in index.html")
        return 0

    missing = []
    for ref in refs:
        # Strip leading slash; resolve relative to dist dir
        candidate = DIST_DIR / ref.lstrip("/")
        if not candidate.exists():
            missing.append(ref)

    if missing:
        print("INTEGRITY FAILURE — the following assets are referenced in")
        print(f"  {INDEX_HTML}")
        print("  but do NOT exist in the dist directory:")
        for m in missing:
            print(f"    MISSING: {m}")
        print()
        print("Do NOT deploy. Re-run `cd frontend && npm run build` and retry.")
        return 1

    print(f"OK — all {len(refs)} asset reference(s) from index.html are present in dist/")
    for ref in refs:
        print(f"    {ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
