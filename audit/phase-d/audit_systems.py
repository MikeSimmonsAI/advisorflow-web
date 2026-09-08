# -*- coding: utf-8 -*-
"""Phase D evidence gatherer.

For every backend router file emit, from the CURRENT source only:
  - route table (verb, path, gate dependency, handler)
  - which app.services modules it imports
  - which app.models classes it references
  - which frontend files call any of its literal path segments
  - which test files reference its module or its paths

No classification here. Classification is done by reading the code.
"""
import ast
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
ROUTERS = os.path.join(APP, "routers")
FE = os.path.join(ROOT, "frontend", "src")
TESTS = os.path.join(ROOT, "tests")

VERBS = ("get", "post", "put", "patch", "delete")
AUTH_RE = re.compile(
    r"Depends\(\s*(require_\w+|get_current\w*|verify_\w+|_require\w+|"
    r"resolve_\w*user\w*|current_\w+)"
)


def read(p):
    with io.open(p, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def walk(base, exts):
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in
                       ("node_modules", "dist", "__pycache__", ".git")]
        for fn in filenames:
            if fn.endswith(exts):
                out.append(os.path.join(dirpath, fn))
    return out


FE_FILES = [(p, read(p)) for p in walk(FE, (".js", ".jsx", ".ts", ".tsx"))]
TEST_FILES = [(p, read(p)) for p in walk(TESTS, (".py",))]


def route_decorators(tree, src):
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            fn = dec.func
            if not isinstance(fn, ast.Attribute) or fn.attr not in VERBS:
                continue
            if not dec.args or not isinstance(dec.args[0], ast.Constant):
                continue
            path = dec.args[0].value
            seg = ast.get_source_segment(src, node) or ""
            head = seg.split("):", 1)[0] if "):" in seg else seg[:1500]
            gates = sorted(set(AUTH_RE.findall(head)))
            rows.append((fn.attr.upper(), path, gates, node.name))
    return rows


def literal_segments(path):
    return [s for s in path.strip("/").split("/")
            if s and not s.startswith("{")]


def fe_callers(paths):
    hits = set()
    for path in paths:
        segs = literal_segments(path)
        if not segs:
            continue
        needle = "/" + "/".join(segs)
        for p, txt in FE_FILES:
            if needle in txt:
                hits.add(os.path.relpath(p, ROOT))
    return sorted(hits)


def test_refs(module, paths):
    hits = set()
    names = [module] + ["/" + "/".join(literal_segments(p)) for p in paths]
    names = [n for n in names if len(n) > 4]
    for p, txt in TEST_FILES:
        for n in names:
            if n in txt:
                hits.add(os.path.relpath(p, ROOT))
                break
    return sorted(hits)


def main():
    out = []
    files = sorted(f for f in os.listdir(ROUTERS) if f.endswith(".py")
                   and f != "__init__.py")
    for fn in files:
        p = os.path.join(ROUTERS, fn)
        src = read(p)
        module = fn[:-3]
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            out.append("### %s  PARSE ERROR %s" % (fn, e))
            continue
        rows = route_decorators(tree, src)
        svcs = sorted(set(re.findall(r"from app\.services import (\w+)", src)
                          + re.findall(r"from app\.services\.(\w+) import", src)))
        models = sorted(set(re.findall(r"from app\.models[\w.]* import ([^\n]+)", src)))
        models = sorted({m.strip() for line in models
                         for m in line.replace("(", "").replace(")", "").split(",")
                         if m.strip() and m.strip() != "*"})
        paths = [r[1] for r in rows]
        out.append("### %s  routes=%d  lines=%d" % (fn, len(rows), src.count("\n") + 1))
        out.append("  services: %s" % (", ".join(svcs) or "-"))
        out.append("  models:   %s" % (", ".join(models[:24]) or "-"))
        nogate = [r for r in rows if not r[2]]
        out.append("  routes_without_auth_dependency: %d" % len(nogate))
        for verb, path, gates, name in rows:
            out.append("    %-6s %-52s %-26s %s"
                       % (verb, path, ",".join(gates) or "NO-GATE", name))
        fc = fe_callers(paths)
        out.append("  frontend_callers: %s" % (", ".join(fc[:10]) or "NONE"))
        tr = test_refs(module, paths)
        out.append("  tests: %s" % (", ".join(tr[:10]) or "NONE"))
        out.append("")
    with io.open(os.path.join(ROOT, "_audit_systems.txt"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(out))
    print("routers:", len(files))
    print("wrote _audit_systems.txt")


main()
