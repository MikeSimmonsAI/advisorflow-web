# -*- coding: utf-8 -*-
"""App.jsx is not the only place a page is mounted: the Sales, God and Executive
shells import their own panels. A page is only orphaned when NOTHING in
frontend/src imports it."""
import io
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
FE = os.path.join(ROOT, "frontend", "src")


def read(p):
    with io.open(p, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


srcs = {}
for dp, dn, fn in os.walk(FE):
    dn[:] = [d for d in dn if d not in ("node_modules", "dist")]
    for f in fn:
        if f.endswith((".js", ".jsx")):
            p = os.path.join(dp, f)
            srcs[os.path.relpath(p, FE).replace("\\", "/")] = read(p)

pages = sorted(k for k in srcs if k.startswith("pages/") and k.endswith(".jsx"))

out = ["== FRONTEND PAGES IMPORTED BY NOTHING =="]
orphans = []
for pg in pages:
    stem = os.path.basename(pg)[:-4]
    pat = re.compile(r"""import[^;\n]*from\s+["'][^"']*\b%s(\.jsx)?["']""" % re.escape(stem))
    lazy = re.compile(r"""import\(\s*["'][^"']*\b%s(\.jsx)?["']""" % re.escape(stem))
    importers = [k for k, t in srcs.items()
                 if k != pg and (pat.search(t) or lazy.search(t))]
    if not importers:
        orphans.append((pg, srcs[pg].count("\n") + 1))
for pg, n in orphans:
    out.append("   %-52s %5d lines" % (pg, n))
out.append("   total pages: %d   orphaned: %d" % (len(pages), len(orphans)))

with io.open(os.path.join(ROOT, "_audit_fe2.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("\n".join(out))
