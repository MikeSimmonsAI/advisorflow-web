"""Is the Launch Engine actually in the DEPLOYED frontend bundle?

Greps the live static site's shipped JS for strings that exist only in the new
code. Answers "did it deploy" with evidence rather than inference from git.
"""

import re
import sys
import urllib.request

SITE = "https://advisorflow-frontend.onrender.com"

NEEDLES = [
    ("Launch nav entry (gated)",   "launchOnly", True),
    ("LaunchPad reads real steps", "/launch/me/steps/", True),
    ("submit call",                "/launch/me/submit", True),
    ("file upload call",           "/launch/me/files", True),
    ("God Launches page",          "Customer Launches", True),
    ("staff review action",        "/review", True),
    # These MUST be gone — they were the prototype's tells.
    ("prototype banner removed",   "Stage 1 prototype", False),
    ("mock customer removed",      "Atlantis", False),
    ("mock answers removed",       "MOCK_ANSWERS", False),
]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "launch-probe"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def main() -> int:
    html = fetch(SITE + "/").decode("utf-8", "replace")
    assets = re.findall(r'src="(/assets/[^"]+\.js)"', html)
    if not assets:
        print("FAIL: no JS asset in index.html")
        return 1
    print("deployed bundle:", assets)
    blob = b"".join(fetch(SITE + a) for a in assets).decode("utf-8", "replace")
    print("bytes:", f"{len(blob):,}")
    print()

    bad = 0
    for label, needle, want_present in NEEDLES:
        present = needle in blob
        ok = present == want_present
        if not ok:
            bad += 1
        state = "PRESENT" if present else "ABSENT "
        verdict = "OK  " if ok else "FAIL"
        print(f"{verdict} {state}  {label:30s} ({needle!r})")

    print()
    print("All expectations met." if not bad else f"{bad} expectation(s) failed.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
