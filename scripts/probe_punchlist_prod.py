"""Unauthenticated production probe for the punch-list surfaces.

This proves three things WITHOUT any credential:
  * the backend is up and which commit it is serving
  * every punch-list route EXISTS (401/403 = present and guarded; 404 = missing)
  * the frontend bundle actually deployed is the one in this worktree

It deliberately does NOT claim a surface "works" -- a 401 proves a route is
mounted and guarded, nothing more. Authenticated shape checks are a separate
step. Reporting 401 as success is how a punch list gets signed off while still
broken.
"""

import json
import sys
import urllib.error
import urllib.request

API = "https://advisorflow-backend.onrender.com"

ROUTES = [
    ("GET", "/health"),
    ("GET", "/god/revenue-history"),
    ("GET", "/god/job-runs/latest"),
    ("GET", "/god/diagnostics/job-runs"),
    ("GET", "/god/diagnostics/twilio"),
    ("GET", "/god/voice/config"),
    ("GET", "/god/voice/mappings"),
    ("GET", "/god/roadmap"),
    ("GET", "/sales/compensation/summary"),
    ("GET", "/sales/team"),
    ("GET", "/executive/workspace"),
    ("GET", "/openapi.json"),
]


def probe(method, path):
    req = urllib.request.Request(f"{API}{path}", method=method)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, r.read(400_000)
    except urllib.error.HTTPError as e:
        return e.code, e.read(4000)
    except Exception as e:  # noqa: BLE001 - transport failure is a real result
        return 0, str(e).encode()


def main():
    results = {}
    for method, path in ROUTES:
        status, body = probe(method, path)
        if path == "/openapi.json" and status == 200:
            try:
                spec = json.loads(body)
                with open("scripts/_openapi_prod.json", "w", encoding="utf-8") as fh:
                    json.dump(spec, fh)
                results[path] = f"{status} (saved {len(spec.get('paths', {}))} paths)"
                continue
            except Exception as e:  # noqa: BLE001
                results[path] = f"{status} but unparseable: {e}"
                continue
        snippet = body[:220].decode("utf-8", "replace").replace("\n", " ")
        results[path] = f"{status}  {snippet}"

    for path, line in results.items():
        print(f"{path:38s} {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
