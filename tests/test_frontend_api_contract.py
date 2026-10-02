"""EVERY API CALL THE FRONTEND MAKES HAS A ROUTE ON THE BACKEND.

Static cross-check: each `api.get/post/put/patch/delete('...')` path in
frontend/src (template params and string concatenation treated as path
segments, a `${query}` glued to the end treated as a query string) must match
a backend route with that method. A renamed or removed endpoint otherwise
ships as a screen that 404s or 405s only in production.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..", "frontend", "src")
CALL = re.compile(r"""\bapi\.(get|post|put|patch|delete)\(\s*(`[^`]*`|'[^']*'|"[^"]*")(\s*\+)?""")


def _frontend_calls():
    for root, _, files in os.walk(ROOT):
        for f in files:
            if not f.endswith((".js", ".jsx")):
                continue
            fp = os.path.join(root, f)
            s = open(fp, encoding="utf-8", errors="ignore").read()
            for m in CALL.finditer(s):
                yield (m.group(1).upper(), m.group(2)[1:-1], bool(m.group(3)),
                       "%s:%d" % (os.path.relpath(fp, ROOT), s[:m.start()].count("\n") + 1))


def _seg_rx(path, prefix=False):
    body = "/".join("[^/]+" if seg == "X" else re.escape(seg) for seg in path.split("/"))
    return re.compile("^" + body + ("" if prefix else "/?$"))


def test_every_frontend_api_call_has_a_backend_route():
    from app.main import app
    routes = [(re.sub(r"\{[^}]+\}", "v", r.path), set(r.methods))
              for r in app.routes if getattr(r, "methods", None)]
    missing = []
    for meth, lit, concat, where in _frontend_calls():
        glued_tail = bool(re.search(r"[^/]\$\{[^}]*\}$", lit))
        if "${" in lit and lit.rfind("${") > lit.rfind("}"):
            lit_prefix = lit[:lit.rfind("${")]               # a nested template literal cut the match short
            glued_tail = True
        else:
            lit_prefix = lit
        # A ${...} glued to the end is a query string (${orgQuery}) or a path
        # tail (${path}) - either way only the part before it is checked, as a prefix.
        p = re.sub(r"(?<=[^/])\$\{[^}]*\}$", "", lit_prefix)
        p = re.sub(r"\$\{[^}]*\}", "X", p).split("?")[0]
        concat = concat or glued_tail
        if not p.startswith("/") or p == "/X":
            continue
        cands = [rp for rp, ms in routes if meth in ms]
        last = p.rstrip("/").split("/")[-1]
        if concat or ("X" in last and last != "X"):
            rx = _seg_rx(re.sub(r"X[^/]*$", "", p), prefix=True)
        else:
            rx = _seg_rx(p.rstrip("/") or "/")
        if not any(rx.match(rp) for rp in cands):
            missing.append("%s %s  (%s)" % (meth, lit, where))
    assert not missing, "frontend calls with no backend route:\n  " + "\n  ".join(missing)
