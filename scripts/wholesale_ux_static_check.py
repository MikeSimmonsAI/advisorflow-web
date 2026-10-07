"""Static Wholesale UX/route contract (STATIC SOURCE; no browser, no node needed).

  1. Every internal `/wholesale...` destination written in Wholesale screens
     resolves to a <Route path=...> in App.jsx (params and query strings handled).
  2. Every lazy page import in App.jsx for Wholesale points at an existing file.
  3. Screens do not render raw internal enum keys (snake_case) via obvious
     patterns, and do not contain garbled mojibake text.
  4. Send failures are surfaced to the person (the disposition and seller-send
     screens read the server's reason).

Run: python3 scripts/wholesale_ux_static_check.py [-v]
"""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SRC = os.path.join(ROOT, "frontend/src")
verbose = "-v" in sys.argv
passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        if verbose:
            print("ok:", name)
    else:
        failed += 1
        print("FAIL:", name)


def read(p):
    with open(p, encoding="utf-8-sig") as fh:
        return fh.read()


app = read(os.path.join(SRC, "App.jsx"))
routes = re.findall(r'<Route\s+path="([^"]+)"', app)
route_res = []
for r in routes:
    pat = re.escape(r).replace(r"\*", ".*")
    pat = re.sub(r":[A-Za-z_]+", "[^/]+", pat.replace(r"\:", ":"))
    route_res.append(re.compile("^" + pat + "$"))


def resolves(path):
    return any(rx.match(path) for rx in route_res)


files = []
for base in ("pages/wholesale", "components"):
    for dp, _, fs in os.walk(os.path.join(SRC, base)):
        for f in fs:
            if f.endswith(".jsx") and (base != "components" or "holesale" in f):
                files.append(os.path.join(dp, f))
check("found wholesale screens", len(files) >= 10)

# 1. internal destinations --------------------------------------------------
dest_re = re.compile(r"""(?:to=|href=|navigate\(|path:\s*)\s*\{?\s*[`'"](/wholesale[^`'"?#]*)""")
checked = 0
for p in sorted(files):
    text = read(p)
    for m in dest_re.finditer(text):
        raw = m.group(1)
        norm = re.sub(r"\$\{[^}]*\}", "x", raw)
        norm = norm.rstrip("/") or "/wholesale"
        # `'/wholesale/deals/' + id` style leaves a trailing slash we already stripped
        if raw.endswith("/") and not resolves(norm):
            norm = norm + "/x"
        checked += 1
        check("%s -> %s resolves to a Route" % (os.path.relpath(p, SRC), raw), resolves(norm))
check("checked at least 15 internal destinations (got %d)" % checked, checked >= 15)

# 2. lazy imports exist ------------------------------------------------------
imps = re.findall(r"import\('\./pages/wholesale/([^']+)'\)", app)
for i in sorted(set(imps)):
    ok = any(os.path.exists(os.path.join(SRC, "pages/wholesale", i + e)) for e in (".jsx", ".js", "/index.jsx"))
    check("lazy import ./pages/wholesale/%s exists" % i, ok)
check("wholesale lazy imports found", len(imps) >= 10)

# 3. enum / mojibake leakage -------------------------------------------------
mojibake = re.compile("Ã[\u0080-¿]|â\u0080[\u0090-¿]|�")
raw_enum = re.compile(r"(?<!=)\{\s*(?:deal|d|row|m|b|buyer|item|r|prop)\.(?:stage|status|channel|audience|deal_result|funding_status|payment_state)\s*\}")
for p in sorted(files):
    text = read(p)
    rel = os.path.relpath(p, SRC)
    check("no mojibake in " + rel, not mojibake.search(text))
    hits = raw_enum.findall(text)
    check("no raw enum rendered bare in %s %s" % (rel, hits[:3]), not hits)

# 4. visible failure reasons --------------------------------------------------
deal_jsx = read(os.path.join(SRC, "pages/wholesale/WholesaleDeal.jsx"))
check("disposition result shows the server's per-buyer reason",
      re.search(r"\.reason", deal_jsx) is not None)
check("disposition surfaces blocked/failed outcomes",
      re.search(r"blocked|Not sent|failed", deal_jsx) is not None)

# 5. light-theme / mobile hooks ---------------------------------------------
css = read(os.path.join(SRC, "components/wholesale-shell.css"))
check("shell css has a mobile breakpoint", "@media" in css and "max-width" in css)

print("wholesale ux static: %d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
