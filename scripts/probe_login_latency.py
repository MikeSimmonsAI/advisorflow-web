"""
LOGIN / WORKSPACE PATH LATENCY - measured, unauthenticated, read-only.

Measures what can be measured WITHOUT credentials, and says plainly what it
cannot reach. It sends no login, holds no token, and mutates nothing.

  1. backend reachability and cold start   GET  /health
  2. the CORS preflight the browser must   OPTIONS /auth/workspace/{id}
     clear before any workspace call          and /auth/my-contexts
  3. the frontend document and its bundle  GET  https://app.evosyspro.live/
  4. an unauthenticated POST /auth/login   (deliberately wrong credentials, so
     round trip                               it measures the path and cannot
                                              sign anything in)

What it CANNOT measure: authenticated workspace resolution, membership
lookup, lead_scope and dashboard timings. Those need a session, and the god
diagnostic already reports them per scenario.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API = "https://advisorflow-backend.onrender.com"
APP = "https://app.evosyspro.live"
ORIGIN = APP
SAMPLES = 5


def timed(req: urllib.request.Request) -> tuple[float, int, int]:
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            return (time.perf_counter() - t0) * 1000, r.status, len(body)
    except urllib.error.HTTPError as e:
        body = e.read()
        return (time.perf_counter() - t0) * 1000, e.code, len(body)
    except Exception as e:
        print("   transport error:", type(e).__name__, str(e)[:120])
        return (time.perf_counter() - t0) * 1000, 0, 0


def run(label: str, make_req, samples: int = SAMPLES) -> None:
    times, status, size = [], None, None
    for _ in range(samples):
        ms, st, sz = timed(make_req())
        times.append(ms)
        status, size = st, sz
    times.sort()
    print(f"  {label:<44} status={status:<4} bytes={size:<8} "
          f"first={times[-1] if len(times) == 1 else times[0]:7.0f}ms "
          f"median={statistics.median(times):7.0f}ms "
          f"max={max(times):7.0f}ms")


def get(url: str, headers: dict | None = None):
    return lambda: urllib.request.Request(url, headers=headers or {}, method="GET")


def options(url: str, method: str, headers: str):
    def make():
        return urllib.request.Request(url, method="OPTIONS", headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        })
    return make


def post_login():
    def make():
        payload = json.dumps({"email": "no-such-user@example.invalid",
                              "password": "x"}).encode()
        return urllib.request.Request(f"{API}/auth/login", data=payload,
                                      method="POST",
                                      headers={"Content-Type": "application/json",
                                               "Origin": ORIGIN})
    return make


def main() -> int:
    print("LOGIN / WORKSPACE LATENCY - unauthenticated, read-only")
    print(f"api {API}")
    print(f"app {APP}\n")

    print("COLD START (first call wakes the instance)")
    ms, st, _ = timed(urllib.request.Request(f"{API}/health"))
    print(f"  first /health                                status={st} {ms:.0f}ms")

    print("\nWARM BACKEND")
    run("GET /health", get(f"{API}/health"))

    print("\nCORS PREFLIGHT (must clear before any workspace call)")
    hdrs = "authorization,content-type,x-workspace-id,x-org-override,x-brand-override"
    run("OPTIONS /auth/my-contexts", options(f"{API}/auth/my-contexts", "GET", hdrs))
    run("OPTIONS /auth/workspace/{id}",
        options(f"{API}/auth/workspace/00000000-0000-0000-0000-000000000000",
                "GET", hdrs))
    run("OPTIONS /leads", options(f"{API}/leads", "GET", hdrs))

    print("\nAUTH ROUND TRIP (invalid credentials on purpose - signs nothing in)")
    run("POST /auth/login (expect 4xx)", post_login(), samples=3)

    print("\nFRONTEND")
    run("GET / (document)", get(APP))
    try:
        with urllib.request.urlopen(APP, timeout=60) as r:
            html = r.read().decode("utf-8", "replace")
        import re
        m = re.search(r'src="(/assets/[^"]+\.js)"', html)
        if m:
            run(f"GET {m.group(1)[:34]}", get(APP + m.group(1)))
        else:
            print("  (no bundle reference found in the document)")
    except Exception as e:
        print("  bundle probe failed:", type(e).__name__, str(e)[:100])

    print("\nNOT MEASURED HERE (needs an authenticated session):")
    print("  membership lookup, workspace resolution, lead_scope, /leads,")
    print("  dashboard. The god diagnostic reports those per scenario.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
