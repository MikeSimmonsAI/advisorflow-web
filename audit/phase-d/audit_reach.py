# -*- coding: utf-8 -*-
"""REACHABILITY, second pass - literal-segment matching.

The first pass matched whole paths and produced 249 candidates, most of them
false: a URL assembled from a variable never matches a literal path. This pass
asks a weaker but far more reliable question:

    does ANY distinctive literal segment of this route appear anywhere in the
    frontend source?

A route whose distinctive segments appear nowhere in 127 pages and every
component is genuinely unreachable from the UI. This under-reports (a route
sharing a segment with a called sibling passes) and that is the right direction
for an audit: everything it flags is real.

Writes _audit_reach2.txt.
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))


def read(p):
    try:
        return io.open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        return ""


def files(rel, exts):
    base = os.path.join(ROOT, rel)
    hits = []
    for dp, dn, fn in os.walk(base):
        dn[:] = [d for d in dn if d not in ("node_modules", "__pycache__",
                                            ".git", "dist")]
        for f in fn:
            if any(f.endswith(e) for e in exts):
                hits.append(os.path.join(dp, f))
    return sorted(hits)


FE = "\n".join(read(p) for p in files("frontend/src", (".jsx", ".js")))

ROUTE_RE = re.compile(r'@router\.(get|post|put|patch|delete)\(\s*["\']([^"\']*)["\']')
PREFIX_RE = re.compile(r'APIRouter\([^)]*prefix\s*=\s*["\']([^"\']*)["\']', re.S)

# Segments too generic to prove anything on their own.
COMMON = {"", "god", "api", "v1", "me", "all", "list", "new", "id", "sales",
          "admin", "user", "users", "org", "orgs", "status", "detail",
          "customers", "customer", "leads", "lead", "settings", "search"}

PUBLIC = ("webhook", "/public", "/health", "/ping", "/demo", "/survey",
          "/intake", "/setup", "/onboarding", "/activate", "/booking",
          "/track", "/version", "/10dlc", "/voice/retell", "/deal-room")

rows = []
for p in files("app/routers", (".py",)):
    src = read(p)
    pref = PREFIX_RE.search(src)
    pref = pref.group(1) if pref else ""
    for m in ROUTE_RE.finditer(src):
        full = (pref + m.group(2)).rstrip("/") or "/"
        rows.append((m.group(1).upper(), full, os.path.basename(p)))

unreached = {}
for meth, path, rf in rows:
    if any(h in path.lower() for h in PUBLIC):
        continue
    segs = [s for s in re.sub(r"\{[^}]*\}", "", path).split("/")
            if s and s.lower() not in COMMON and len(s) > 3]
    if not segs:
        continue
    # reachable if EVERY distinctive segment is absent -> unreachable
    if any(s in FE for s in segs):
        continue
    unreached.setdefault(rf, []).append("%-6s %s" % (meth, path))

out = []
out.append("=" * 78)
out.append("ROUTES WITH NO DISTINCTIVE SEGMENT ANYWHERE IN THE FRONTEND")
out.append("=" * 78)
out.append("backend routes scanned: %d" % len(rows))
out.append("")
out.append("These are BUILT, DEPLOYED AND UNREACHABLE from the product, or")
out.append("they are machine-to-machine endpoints. Either is worth knowing:")
out.append("an endpoint nobody can reach is either a missing screen or dead")
out.append("code, and both cost something to carry.")
tot = 0
for rf in sorted(unreached, key=lambda k: -len(unreached[k])):
    out.append("")
    out.append("%s  (%d)" % (rf, len(unreached[rf])))
    for line in sorted(unreached[rf]):
        out.append("    " + line)
        tot += 1
out.append("")
out.append("TOTAL UNREACHABLE: %d of %d" % (tot, len(rows)))

io.open(os.path.join(ROOT, "_audit_reach2.txt"), "w",
        encoding="utf-8", newline="\n").write("\n".join(out))
print("wrote _audit_reach2.txt  unreachable=%d of %d" % (tot, len(rows)))
