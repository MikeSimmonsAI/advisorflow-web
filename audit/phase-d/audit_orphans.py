# -*- coding: utf-8 -*-
"""Orphaned-service detection, done properly.

The first pass used a line regex and reported billing_webhook as orphaned
because billing_router imports it inside a multi-line parenthesised import.
A line regex cannot see that. This pass parses every module with ast and
collects real import edges, so a service is only called dead when NOTHING
imports it.
"""
import ast
import io
import os

ROOT = os.path.dirname(os.path.abspath(__file__))


def read(p):
    with io.open(p, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def walk(base):
    out = []
    for dp, dn, fn in os.walk(base):
        dn[:] = [d for d in dn if d not in ("node_modules", "dist", "__pycache__", ".git")]
        for f in fn:
            if f.endswith(".py"):
                out.append(os.path.join(dp, f))
    return out


def imported_service_names(src):
    """Every app.services.X or `from app.services import X` name in one module."""
    names = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            m = node.module
            if m == "app.services":
                for a in node.names:
                    names.add(a.name)
            elif m.startswith("app.services."):
                names.add(m.split(".")[2])
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("app.services."):
                    names.add(a.name.split(".")[2])
    return names


SVC = os.path.join(ROOT, "app", "services")
services = {}
for entry in sorted(os.listdir(SVC)):
    p = os.path.join(SVC, entry)
    if entry == "__init__.py":
        continue
    if entry.endswith(".py"):
        services[entry[:-3]] = p
    elif os.path.isdir(p) and os.path.exists(os.path.join(p, "__init__.py")):
        services[entry] = p  # package-style service (comms, calendar_providers, ...)

scanned = walk(os.path.join(ROOT, "app")) + walk(os.path.join(ROOT, "tests"))
for extra in ("main.py", os.path.join("app", "main.py"), os.path.join("app", "deps.py")):
    p = os.path.join(ROOT, extra)
    if os.path.exists(p) and p not in scanned:
        scanned.append(p)

app_importers = {}   # service -> set of app/ modules importing it
test_importers = {}  # service -> set of tests importing it
for p in scanned:
    rel = os.path.relpath(p, ROOT)
    own = None
    for name, sp in services.items():
        if os.path.abspath(p).startswith(os.path.abspath(sp)):
            own = name
    for name in imported_service_names(read(p)):
        if name == own:
            continue
        bucket = test_importers if rel.startswith("tests") else app_importers
        bucket.setdefault(name, set()).add(rel)

out = ["== SERVICE IMPORT REACHABILITY (ast-based) ==",
       "   services (modules + packages): %d" % len(services), ""]
dead, test_only = [], []
for name in sorted(services):
    a = app_importers.get(name, set())
    t = test_importers.get(name, set())
    if not a and not t:
        dead.append(name)
    elif not a:
        test_only.append((name, sorted(t)))

out.append("-- IMPORTED BY NOTHING AT ALL (not even a test)")
for n in dead:
    lines = 0
    p = services[n]
    if p.endswith(".py"):
        lines = read(p).count("\n") + 1
    out.append("   %-44s %5d lines" % (n, lines))
out.append("")
out.append("-- IMPORTED ONLY BY TESTS (no production caller)")
for n, t in test_only:
    p = services[n]
    lines = read(p).count("\n") + 1 if p.endswith(".py") else 0
    out.append("   %-44s %5d lines   tests: %s" % (n, lines, ", ".join(t)))
out.append("")
out.append("-- SANITY: billing_webhook importers -> %s"
           % (sorted(app_importers.get("billing_webhook", set())) or "NONE"))

with io.open(os.path.join(ROOT, "_audit_orphans.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("\n".join(out))
