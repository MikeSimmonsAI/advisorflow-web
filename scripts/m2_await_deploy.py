"""Wait until the deployed service reports the commit we just shipped.

    python scripts/m2_await_deploy.py <base_url> <sha> [minutes]

"Deployed" is not "pushed". Render builds, then swaps; asking /version until it
answers with the expected commit is the only honest way to know the code under
test is the code that shipped.
"""
import json
import sys
import time
import urllib.error
import urllib.request

base = (sys.argv[1] if len(sys.argv) > 1 else "").rstrip("/")
want = (sys.argv[2] if len(sys.argv) > 2 else "").strip().lower()
minutes = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0

if not (base and want):
    print("usage: m2_await_deploy.py <base_url> <sha> [minutes]")
    sys.exit(2)

deadline = time.time() + minutes * 60
last = None
while time.time() < deadline:
    try:
        with urllib.request.urlopen(base + "/version", timeout=30) as r:
            body = json.loads(r.read().decode() or "{}")
        got = str(body.get("commit") or body.get("sha") or "").lower()
        if got != last:
            print("  /version -> %s" % (got or "(none)"))
            last = got
        if got.startswith(want[:12]) or want.startswith(got[:12]) and got:
            print("DEPLOYED %s" % got)
            sys.exit(0)
    except urllib.error.HTTPError as e:
        print("  /version HTTP %s" % e.code)
    except Exception as e:                                   # noqa: BLE001
        print("  /version unreachable: %s" % e)
    time.sleep(20)

print("TIMED OUT waiting for %s at %s (last seen %s)" % (want[:12], base, last))
sys.exit(1)
