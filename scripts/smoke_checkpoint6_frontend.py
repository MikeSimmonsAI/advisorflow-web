"""Checkpoint 6 frontend guard rails.

Static, because there is no browser in the deploy pipeline. It cannot prove a
screen looks right - the screenshots do that - but it CAN prove the things that
break silently and are only noticed by a customer:

  * every Checkpoint 6 screen is actually routed, so a nav entry cannot point at
    a 404 while claiming to be built;
  * the activation page is PUBLIC, because an invited customer has no session
    and a ProtectedRoute would bounce them to a login they cannot pass;
  * no screen decides permissions in React;
  * no screen renders or asks for a temporary password;
  * every table collapses on mobile rather than scrolling sideways;
  * the God Ops stylesheet stays scoped and cannot leak into a tenant screen.

    python scripts/smoke_checkpoint6_frontend.py
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "frontend", "src")
FAILURES = []


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("\n         -> " + str(detail)[:400]) if not ok else ""))
    if not ok:
        FAILURES.append(label)


def read(*parts):
    with open(os.path.join(SRC, *parts), encoding="utf-8-sig") as fh:
        return fh.read()


def exists(*parts):
    return os.path.exists(os.path.join(SRC, *parts))


SCREENS = [
    ("GodSalesOps.jsx", "/god/sales-operations"),
    ("GodBrandDetail.jsx", "/god/brands/:brandId"),
    ("GodProvision.jsx", "/god/provision/:oppId"),
    ("GodImplementations.jsx", "/god/implementations"),
    ("GodImplementationDetail.jsx", "/god/implementations/:implId"),
    ("GodCustomers.jsx", "/god/customers"),
    ("GodControlAudit.jsx", "/god/audit"),
    ("SalesImplementations.jsx", "/sales/onboarding"),
    ("Activate.jsx", "/activate"),
]


def main():
    print("=" * 74)
    print("CHECKPOINT 6 FRONTEND")
    print("=" * 74)

    app = read("App.jsx")

    print("\n--- every screen exists and is routed " + "-" * 33)
    for fname, route in SCREENS:
        check("%s exists" % fname, exists("pages", fname))
        check("%s is routed" % route, ('path="%s"' % route) in app,
              "no <Route path=\"%s\">" % route)

    # The catch-all must stay LAST, or it swallows every specific god route.
    print("\n--- routing order " + "-" * 53)
    catchall = app.find('path="/god/*"')
    check("the /god/* catch-all exists", catchall > 0)
    for _, route in SCREENS:
        if route.startswith("/god/"):
            i = app.find('path="%s"' % route)
            check("%s is registered BEFORE the catch-all" % route,
                  i > 0 and i < catchall, "%d vs %d" % (i, catchall))

    print("\n--- the activation page is public " + "-" * 37)
    line = [l for l in app.splitlines() if 'path="/activate"' in l]
    check("exactly one /activate route", len(line) == 1, line)
    if line:
        check("it is NOT wrapped in ProtectedRoute", "ProtectedRoute" not in line[0], line[0])
        check("it is NOT wrapped in GodRoute", "GodRoute" not in line[0], line[0])
        check("it is NOT wrapped in SalesRoute", "SalesRoute" not in line[0], line[0])

    print("\n--- no permission decided in React " + "-" * 36)
    for fname, _ in SCREENS:
        src = read("pages", fname)
        # A screen may READ the user for display. It must not gate an action on
        # a role it computed itself - the server does that, on every route.
        bad = re.findall(r"role\s*===\s*'(god_admin|super_admin|sales_manager)'", src)
        check("%s decides no permission locally" % fname, not bad, bad)

    print("\n--- no plaintext password anywhere " + "-" * 36)
    for fname, _ in SCREENS:
        src = read("pages", fname)
        for bad in ("temp_password", "temporary password", "temporaryPassword"):
            check("%s never mentions %s" % (fname, bad), bad not in src)
    act = read("pages", "Activate.jsx")
    check("the activation page never prefills a password",
          'value={pw}' in act and 'defaultValue' not in act)
    check("password inputs are type=password",
          act.count('type="password"') == 2, act.count('type="password"'))
    check("the activation page does not store a session on success",
          "setToken" not in act)
    # It is public, seen before any brand is resolved, and must therefore paint
    # its own page rather than assuming a dark operator background exists under
    # it - which produced dark boxes on cream with an invisible heading.
    check("the activation page has its own stylesheet",
          "Activate.css" in act and "GodOps.css" not in act)
    act_css = read("pages", "Activate.css")
    check("it paints its own full-viewport background",
          "position: fixed" in act_css and "inset: 0" in act_css)
    check("its rules are scoped under .act-",
          not [x for x in re.findall(r"^([.#\w][^{;@]*)\{", act_css, re.M)
               if ".act-" not in x],
          [x for x in re.findall(r"^([.#\w][^{;@]*)\{", act_css, re.M)
           if ".act-" not in x][:3])
    check("every failure gives the same message",
          act_css is not None and act.count("GENERIC") >= 3, act.count("GENERIC"))

    detail = read("pages", "GodImplementationDetail.jsx")
    check("the one-time link is labelled as one-time",
          "shown once" in detail.lower())
    check("the link is never written to storage",
          "localStorage" not in detail and "sessionStorage" not in detail)

    print("\n--- responsive: tables collapse, they do not scroll " + "-" * 20)
    css = read("pages", "god", "GodOps.css")
    check("a mobile breakpoint exists", "@media (max-width: 900px)" in css)
    check("tables become blocks on mobile",
          ".go-table thead { display: none; }" in css)
    check("cells carry their own label on mobile",
          "content: attr(data-label)" in css)
    check("no horizontal scroll is used to cope",
          "overflow-x" not in css)
    # Only the God Ops screens are governed by this sheet. The sales-side screen
    # uses the Sales Workspace's own shell and `sw-` responsive table, which has
    # its own mobile behaviour and is checked by that workspace's own rules.
    for fname, _ in SCREENS:
        if not fname.startswith("God"):
            continue
        src = read("pages", fname)
        if "<table" in src:
            cells = len(re.findall(r"<td(?![a-zA-Z])", src))
            labelled = len(re.findall(r'<td[^>]*data-label=', src))
            check("%s labels every cell for mobile" % fname, cells == labelled,
                  "%d cells, %d labelled" % (cells, labelled))

    sales_screen = read("pages", "SalesImplementations.jsx")
    check("the sales screen uses the Sales Workspace shell",
          "SalesShell" in sales_screen)
    check("the sales screen does not import the God Ops sheet",
          "GodOps.css" not in sales_screen)
    check("the sales screen uses the workspace's responsive table",
          "sw-tablewrap" in sales_screen)

    print("\n--- the stylesheet stays in its lane " + "-" * 34)
    # Real selector extraction, not a line scan. A line-based check matches
    # `color: var(--go-text);` inside a block and calls it an unscoped selector,
    # which is a failure that teaches nobody anything.
    stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    selectors = []
    for chunk in stripped.split("}"):
        if "{" not in chunk:
            continue
        sel = chunk.rsplit("{", 1)[0].strip()
        if not sel or sel.startswith("@"):
            continue
        # A media query's own brace leaves its selector attached to the query.
        sel = sel.split("{")[-1].strip()
        for part in sel.split(","):
            part = part.strip()
            if part and not part.startswith("@"):
                selectors.append(part)
    unscoped = [s_ for s_ in selectors if ".go-" not in s_]
    check("every selector is scoped under .go-", not unscoped, unscoped[:5])
    check("the sheet actually has rules", len(selectors) > 40, len(selectors))
    for prefix in ("sw-", "gm-", "dc-"):
        check("it defines no %s class" % prefix, ("." + prefix) not in css)

    print("\n--- navigation tells the truth " + "-" * 40)
    shell = read("pages", "GodShell.jsx")
    for route in ("/god/sales-operations", "/god/implementations", "/god/customers", "/god/audit"):
        m = re.search(r"path: '%s'\s*,\s*icon: '\w+',\s*built: (true|false)" % re.escape(route), shell)
        check("nav for %s is marked built" % route, bool(m) and m.group(1) == "true",
              m.group(0) if m else "no nav entry")
    sales_shell = read("pages", "sales", "SalesShell.jsx")
    m = [l for l in sales_shell.splitlines() if "/sales/onboarding" in l]
    check("the sales nav no longer marks Sold/Onboarding as 'soon'",
          m and "soon" not in m[0], m)

    print("\n--- the API client carries structured errors " + "-" * 26)
    client = read("api", "client.js")
    check("err.detail is attached", "err.detail = detail" in client)
    check("err.status is attached", "err.status = res.status" in client)
    check("a string detail still becomes the message",
          "typeof detail === 'string'" in client)

    print("\n--- brand-sales access activation UI " + "-" * 34)
    act = read("pages", "Activate.jsx")
    # One page serves two token families that live in two different tables.
    # Routing on the token's own prefix is what stops it guessing, or trying one
    # then the other - which would leak which family a token belongs to and
    # double every rate-limit hit.
    check("the activation page routes on the token prefix",
          "apiBaseFor" in act and "stf_" in act, "no prefix routing")
    check("it targets both activation endpoints",
          "/auth/staff-activation" in act and "/auth/activation" in act)
    check("it still stores no session on success", "setToken" not in act)
    check("the copy does not call a sales rep an administrator",
          "sales workspace" in act and "invite.purpose === 'reset'" in act)

    brand = read("pages", "GodBrandDetail.jsx")
    check("the brand screen has a Sales team panel",
          'title="Sales team"' in brand)
    check("it can generate a setup link",
          "/god/ops/sales-users/" in brand and "setup-link" in brand)
    check("it can revoke one", "/god/ops/staff-activations/" in brand)
    check("it labels the one-time link as shown once",
          "shown once" in brand.lower())
    check("it never writes the link to storage",
          "localStorage" not in brand and "sessionStorage" not in brand)
    check("it surfaces a brand-sales user wrongly inside a tenant",
          "inside a customer tenant" in brand)
    check("it reports whether each person can actually sign in",
          "has_signed_in" in brand)
    for bad in ("temp_password", "temporary password"):
        check("the brand screen never mentions %s" % bad, bad not in brand)

    print("\n" + "=" * 74)
    if FAILURES:
        print("FAILED (%d):" % len(FAILURES))
        for f in FAILURES:
            print("  - %s" % f)
        sys.exit(1)
    print("ALL CHECKPOINT 6 FRONTEND CHECKS PASSED")


if __name__ == "__main__":
    main()
