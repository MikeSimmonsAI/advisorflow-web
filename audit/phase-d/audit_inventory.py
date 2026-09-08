# -*- coding: utf-8 -*-
"""HARD INVENTORY of the repository. Evidence, not impressions.

Writes _audit_inventory.txt. Read-only: opens files, writes one report.
"""
import io
import json
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = []


def w(s=""):
    OUT.append(s)


def read(p):
    try:
        return io.open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        return ""


def files(rel, exts):
    base = os.path.join(ROOT, rel)
    hits = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames
                       if d not in ("node_modules", "__pycache__", ".git", "dist")]
        for f in filenames:
            if any(f.endswith(e) for e in exts):
                hits.append(os.path.join(dirpath, f))
    return sorted(hits)


# ── BACKEND ROUTERS AND THEIR ROUTES ────────────────────────────────────────
w("=" * 78)
w("BACKEND ROUTERS")
w("=" * 78)
ROUTE_RE = re.compile(r'@router\.(get|post|put|patch|delete)\(\s*["\']([^"\']*)["\']')
PREFIX_RE = re.compile(r'APIRouter\([^)]*prefix\s*=\s*["\']([^"\']*)["\']', re.S)

router_routes = {}
total_routes = 0
for p in files("app/routers", (".py",)):
    name = os.path.basename(p)
    if name == "__init__.py":
        continue
    src = read(p)
    prefix = PREFIX_RE.search(src)
    prefix = prefix.group(1) if prefix else ""
    routes = [(m.group(1).upper(), prefix + m.group(2))
              for m in ROUTE_RE.finditer(src)]
    router_routes[name] = routes
    total_routes += len(routes)
    w("%-42s %3d routes  %s" % (name, len(routes),
                                ("prefix " + prefix) if prefix else ""))
w()
w("ROUTER FILES: %d   TOTAL ROUTES: %d" % (len(router_routes), total_routes))

# ── SERVICES ────────────────────────────────────────────────────────────────
w()
w("=" * 78)
w("BACKEND SERVICES  (name, lines, public defs)")
w("=" * 78)
svc = files("app/services", (".py",))
for p in svc:
    src = read(p)
    lines = src.count("\n") + 1
    defs = len(re.findall(r"^def [a-z]", src, re.M))
    rel = os.path.relpath(p, os.path.join(ROOT, "app", "services")).replace("\\", "/")
    w("%-46s %5d lines  %3d defs" % (rel, lines, defs))
w()
w("SERVICE FILES: %d" % len(svc))

# ── MODELS ──────────────────────────────────────────────────────────────────
w()
w("=" * 78)
w("MODELS  (file -> classes)")
w("=" * 78)
model_classes = {}
for p in files("app/models", (".py",)):
    name = os.path.basename(p)
    src = read(p)
    classes = re.findall(r"^class (\w+)\(Base\)", src, re.M)
    if classes:
        model_classes[name] = classes
        w("%-34s %s" % (name, ", ".join(classes)))
w()
w("MODEL CLASSES: %d across %d files"
  % (sum(len(v) for v in model_classes.values()), len(model_classes)))

# ── AUTO MIGRATIONS ─────────────────────────────────────────────────────────
w()
w("=" * 78)
w("AUTO MIGRATIONS")
w("=" * 78)
am = read(os.path.join(ROOT, "app", "auto_migrate.py"))
cols = re.findall(r'\(\s*["\'](\w+)["\']\s*,\s*["\'](\w+)["\']', am)
tables = sorted({t for t, _ in cols})
w("COLUMNS_TO_ADD entries: %d across %d tables" % (len(cols), len(tables)))
w("tables: %s" % ", ".join(tables))

# ── BACKGROUND JOBS / SCHEDULED WORK ────────────────────────────────────────
w()
w("=" * 78)
w("BACKGROUND / SCHEDULED WORK")
w("=" * 78)
mainsrc = read(os.path.join(ROOT, "app", "main.py"))
for m in re.finditer(r"^(async )?def (_?\w*(loop|cron|job|scheduler|worker)\w*)",
                     mainsrc, re.M | re.I):
    w("app/main.py  %s" % m.group(2))
for p in files("app", (".py",)):
    src = read(p)
    if "asyncio.create_task" in src or "BackgroundTasks" in src:
        n1 = src.count("asyncio.create_task")
        n2 = src.count("BackgroundTasks")
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        w("%-52s create_task=%d BackgroundTasks=%d" % (rel, n1, n2))

# ── FRONTEND ────────────────────────────────────────────────────────────────
w()
w("=" * 78)
w("FRONTEND ROUTES (App.jsx)")
w("=" * 78)
app = read(os.path.join(ROOT, "frontend", "src", "App.jsx"))
paths = re.findall(r'path="([^"]+)"', app)
w("registered routes: %d" % len(paths))
for grp, pref in (("GOD", "/god"), ("EXECUTIVE", "/executive"),
                  ("SALES", "/sales"), ("OTHER", None)):
    if pref:
        sel = sorted({p for p in paths if p.startswith(pref)})
    else:
        sel = sorted({p for p in paths
                      if not p.startswith(("/god", "/executive", "/sales"))})
    w()
    w("-- %s (%d)" % (grp, len(sel)))
    for p in sel:
        w("   %s" % p)

w()
w("=" * 78)
w("FRONTEND PAGES  (file, lines)  -- ORPHANS FLAGGED")
w("=" * 78)
pages = files("frontend/src/pages", (".jsx",))
allsrc = "\n".join(read(p) for p in files("frontend/src", (".jsx", ".js")))
orphans = []
for p in pages:
    stem = os.path.basename(p)[:-4]
    rel = os.path.relpath(p, os.path.join(ROOT, "frontend", "src")).replace("\\", "/")
    src = read(p)
    lines = src.count("\n") + 1
    # imported anywhere other than itself?
    imported = re.search(r"import\s+%s\s+from|from ['\"][^'\"]*%s['\"]"
                         % (re.escape(stem), re.escape(stem)), allsrc)
    flag = "" if imported else "   <-- NOT IMPORTED ANYWHERE"
    if not imported:
        orphans.append(rel)
    w("%-58s %5d%s" % (rel, lines, flag))
w()
w("PAGE FILES: %d   ORPHANS: %d" % (len(pages), len(orphans)))

w()
w("=" * 78)
w("FRONTEND COMPONENTS: %d files"
  % len(files("frontend/src/components", (".jsx", ".js"))))

# ── TESTS ───────────────────────────────────────────────────────────────────
w()
w("=" * 78)
w("TESTS")
w("=" * 78)
tests = files("tests", (".py",))
tot = 0
for p in tests:
    src = read(p)
    n = len(re.findall(r"^\s*def test_", src, re.M))
    tot += n
    w("%-56s %3d" % (os.path.basename(p), n))
w()
w("TEST FILES: %d   TEST FUNCTIONS: %d" % (len(tests), tot))

io.open(os.path.join(ROOT, "_audit_inventory.txt"), "w",
        encoding="utf-8", newline="\n").write("\n".join(OUT))
print("wrote _audit_inventory.txt  (%d lines)" % len(OUT))
print("routers=%d routes=%d services=%d models=%d pages=%d orphan_pages=%d tests=%d"
      % (len(router_routes), total_routes, len(svc),
         sum(len(v) for v in model_classes.values()), len(pages),
         len(orphans), tot))
