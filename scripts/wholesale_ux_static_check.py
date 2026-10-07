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

# 4b. send refusal codes are worded, not shown raw -------------------------
shared = read(os.path.join(SRC, "pages/wholesale/wsShared.jsx"))
disp_py = read(os.path.join(ROOT, "app/services/wholesale_disposition.py"))
codes = set(re.findall(r'SendRefused\([^()]*?,\s*"([a-z_]+)"\s*\)', disp_py, re.S))
codes |= set(re.findall(r'"code":\s*"([a-z_]+)"', disp_py))
codes.discard("sent")  # success, not a refusal
check("found send refusal codes (got %s)" % sorted(codes), len(codes) >= 6)
for code in sorted(codes):
    check("refusal code %r has a plain-language label" % code,
          re.search(r"\b%s:\s*'" % code, shared) is not None)
check("disposition result renders refusalLabel(), not the bare code",
      "refusalLabel(r.code)" in deal_jsx and ": r.code}" not in deal_jsx)

# 4c. buyer-match factors are readable without colour or glyph --------------
check("match factor marks carry an accessible name",
      re.search(r"aria-label=\{f\.matched", shared) is not None)

# 4d. closing screen mirrors the server rules --------------------------------
closing = read(os.path.join(SRC, "pages/wholesale/wsClosing.jsx"))
check("closing does not offer Close on a deal in Dead (server answers 409)",
      "deal.stage === 'dead'" in closing)
check("close fee input blocks and explains a negative value",
      "feeInvalid" in closing and "ws-close-fee-err" in closing)
check("collected-amount input blocks and explains a negative value",
      "amountInvalid" in closing and "pay-amt-err" in closing)

# 4e. every labelled form control in closing has a matching input id ---------
for label_for in sorted(set(re.findall(r'htmlFor="([^"]+)"', closing))):
    check("closing label for=%s has an input" % label_for,
          re.search(r'id="%s"' % re.escape(label_for), closing) is not None)

# 4f. every Wholesale form label is programmatically tied to its control -----
# A bare <label>text</label> next to an input gives the control no accessible
# name. Allowed: htmlFor=, a label that wraps its control, or an sr-only span.
for sub in ("WholesaleBuyers.jsx", "WholesaleSettings.jsx", "wsBuyerBoard.jsx"):
    txt = read(os.path.join(SRC, "pages/wholesale", sub))
    bare = re.findall(r"<label>[^<]*(?:\{[^}]*\})?[^<]*</label>", txt)
    check("%s has no bare unassociated <label> (%s)" % (sub, bare[:2]), not bare)
    for f in sorted(set(re.findall(r'htmlFor="([^"]+)"', txt))):
        check("%s label for=%s has an input id" % (sub, f),
              re.search(r'id="%s"' % re.escape(f), txt) is not None)
buyers = read(os.path.join(SRC, "pages/wholesale/WholesaleBuyers.jsx"))
check("buyer channel select shows worded options, not sms/phone keys",
      "Text message" in buyers and ">{c}</option>" not in buyers)
check("buyer import file input has an id tied to its label",
      'htmlFor="bi-file"' in buyers and 'id="bi-file"' in buyers)

# 5. light-theme / mobile hooks ---------------------------------------------
css = read(os.path.join(SRC, "components/wholesale-shell.css"))
check("shell css has a mobile breakpoint", "@media" in css and "max-width" in css)

print("wholesale ux static: %d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
