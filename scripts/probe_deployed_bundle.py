"""Prove what the DEPLOYED frontend actually contains — no login required.

The static site is a Vite build, so every page's code is in one hashed JS
bundle. Fetching that bundle and searching it for strings that only exist in a
given source revision is real evidence that a change shipped, as opposed to
"it's committed, so it must be live".

Reports the deployed asset hash so it can be compared with the local build.
"""

import re
import sys
import urllib.request

SITE = "https://advisorflow-frontend.onrender.com"

# (label, needle) — needles are strings that appear ONLY in the feature's source.
NEEDLES = [
    ("roadmap board (GOD-04)",        "Roadmap data unavailable"),
    ("roadmap schema field",          "schema_version"),
    ("job-runs screen",               "Background Jobs"),
    ("job ledger diagnostic (NEW)",   "Job ledger unavailable"),
    ("twilio diagnostics screen",     "twilio-diagnostics"),
    ("voice config screen",           "voice/agents"),
    ("compensation command",          "compensation/payables"),
    ("team availability",             "availability/team"),
    ("executive workspace",           "executive/workspace"),
]


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "punchlist-probe"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main() -> int:
    html = fetch(SITE + "/").decode("utf-8", "replace")
    assets = re.findall(r'src="(/assets/[^"]+\.js)"', html)
    if not assets:
        print("FAIL: no JS asset found in index.html")
        return 1
    print(f"deployed index.html -> {assets}")

    blob = b""
    for a in assets:
        blob += fetch(SITE + a)
    text = blob.decode("utf-8", "replace")
    print(f"bundle bytes: {len(blob):,}\n")

    worst = 0
    for label, needle in NEEDLES:
        present = needle in text
        print(f"{'PRESENT' if present else 'ABSENT ':8s} {label:32s} ({needle!r})")
        if not present:
            worst = 1
    return worst


if __name__ == "__main__":
    sys.exit(main())
