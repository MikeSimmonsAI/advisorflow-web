#!/usr/bin/env python3
"""
verify_dist_integrity.py - Artifact integrity gate.

Parses frontend/dist/index.html, extracts all /assets/ references,
then verifies:
  1. Each file exists on disk in frontend/dist/
  2. Each file is tracked in git (cached index OR HEAD tree)

Exits 0 if all assets are present and tracked.
Exits 1 if any asset is missing from disk or missing from git.

PURPOSE: Catches the commit 18369e5 failure pattern where a Vite build
updates index.html to reference a new content-hashed bundle but the
bundle file is never git-added, causing 503 errors on both Render deploys.

USAGE:
  python scripts/verify_dist_integrity.py
  python scripts/verify_dist_integrity.py --repo /path/to/repo
"""

import re
import subprocess
import sys
from pathlib import Path


ASSET_PATTERN = re.compile(
    r'(?:src|href)=["\'](?P<path>/assets/[^"\'?#]+)["\']',
    re.IGNORECASE,
)


def get_repo_root(hint: Path = None) -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
            cwd=str(hint or Path.cwd()),
        )
        return Path(result.stdout.strip())
    except subprocess.CalledProcessError:
        return hint or Path.cwd()


def git_tracked_files(repo_root: Path) -> set:
    """Return the set of repo-relative paths tracked in git index or HEAD."""
    tracked = set()
    for cmd in (
        ["git", "ls-files", "--cached"],
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
    ):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(repo_root))
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    tracked.add(line.strip())
        except Exception:
            pass
    return tracked


def verify(repo_root: Path) -> int:
    dist = repo_root / "frontend" / "dist"
    index_html = dist / "index.html"

    if not index_html.exists():
        print(f"ERROR: {index_html} does not exist")
        return 1

    html = index_html.read_text(encoding="utf-8", errors="replace")
    asset_paths = list(dict.fromkeys(m.group("path") for m in ASSET_PATTERN.finditer(html)))

    if not asset_paths:
        print("ERROR: No /assets/ references found in index.html â€” build may be stale")
        return 1

    print(f"Found {len(asset_paths)} asset reference(s) in index.html")

    tracked = git_tracked_files(repo_root)
    failures = []

    for asset_path in asset_paths:
        rel = asset_path.lstrip("/")          # assets/index-CRg1tUQ7.js
        disk_path = dist / Path(*rel.split("/")[1:])  # frontend/dist/assets/...
        disk_path2 = repo_root / rel          # frontend/dist/assets/... via repo root
        git_rel = f"frontend/dist/{Path(*rel.split('/')).name}"
        git_rel2 = f"frontend/dist/assets/{Path(asset_path).name}"

        on_disk = disk_path.exists() or disk_path2.exists() or (repo_root / "frontend" / "dist" / "assets" / Path(asset_path).name).exists()
        in_git = any(p in tracked for p in [
            rel, git_rel, git_rel2,
            f"frontend/dist/assets/{Path(asset_path).name}",
        ])

        status = []
        if not on_disk:
            status.append("MISSING FROM DISK")
        if not in_git:
            status.append("NOT IN GIT")

        if status:
            failures.append((asset_path, status))
            print(f"  FAIL  {asset_path}  [{', '.join(status)}]")
        else:
            print(f"  OK    {asset_path}")

    if failures:
        print(f"\nINTEGRITY GATE FAILED â€” {len(failures)} asset(s) missing")
        print("Run: git add -f frontend/dist/assets/<file> && git commit")
        return 1

    print(f"\nINTEGRITY GATE PASSED â€” all {len(asset_paths)} asset(s) present and tracked")
    return 0


def main() -> int:
    repo_hint = None
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--repo" and i + 2 < len(sys.argv):
            repo_hint = Path(sys.argv[i + 2])
    repo_root = get_repo_root(repo_hint)
    print(f"Repo root: {repo_root}")
    return verify(repo_root)


if __name__ == "__main__":
    sys.exit(main())
