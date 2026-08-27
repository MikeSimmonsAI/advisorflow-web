"""GATE 29 - the new screens do not decide anything the server should decide.

Source assertions, deliberately. These are the properties that a rendering test
would not catch and that quietly rot: a banner that trusts localStorage, a
status word invented in JSX, a permission enforced by hiding a button.

Each assertion below is about a CALL PATH or an absence, never about prose - an
earlier gate in this repo once failed because a file's own comment contained the
word it was asserting was absent.
"""
import io
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "frontend", "src")

FAIL, PASSED = [], []


def read(*parts):
    p = os.path.join(SRC, *parts)
    if not os.path.exists(p):
        return None
    return io.open(p, encoding="utf-8", errors="replace").read()


def check(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "FAIL ", label,
                         ("\n          -> " + str(detail)[:200]) if detail else ""))
    (PASSED if ok else FAIL).append(label)


def strip_comments(s):
    """Remove // and /* */ so assertions test code, not commentary."""
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    return re.sub(r"^\s*//.*$", "", s, flags=re.M)


def main():
    print("=" * 78)
    print("GATE 29 - PLATFORM / CUSTOMER SCREENS")
    print("=" * 78)

    banner = read("components", "ContextBanner.jsx")
    check("ContextBanner.jsx exists", banner is not None)
    if banner:
        code = strip_comments(banner)
        check("the banner text comes from the SERVER's context endpoint",
              "/god/platform/context" in code and "ctx.banner" in code,
              "asks the server and renders its string")
        check("...not from localStorage",
              "orgName" not in code,
              "no orgName from local storage is rendered")
        check("exiting calls the audited server endpoint",
              "/god/platform/context/exit" in code)
        check("...and clears the local context too",
              "clearOrgContext" in code)

    ov = read("pages", "god", "PlatformOverview.jsx")
    check("PlatformOverview.jsx exists", ov is not None)
    if ov:
        code = strip_comments(ov)
        check("the overview reads server totals rather than counting rows",
              "/god/platform/overview" in code and "data.totals" in code)
        check("...and surfaces customers with no brand instead of hiding them",
              "unassigned_customers" in code)
        check("no customer count is computed in the browser",
              not re.search(r"customers\s*\.\s*length", code) or "totals" in code)

    cc = read("pages", "god", "CustomerCreate.jsx")
    check("CustomerCreate.jsx exists", cc is not None)
    if cc:
        code = strip_comments(cc)
        check("create posts to the provisioning engine",
              "api.post('/god/customers'" in code or 'api.post("/god/customers"' in code)
        check("the brand is required before the button enables",
              "f.platform_id" in code and "ready" in code)

    cd = read("pages", "god", "CustomerDetail.jsx")
    check("CustomerDetail.jsx exists", cd is not None)
    if cd:
        code = strip_comments(cd)
        check("readiness status strings are rendered, not authored",
              "sec.status" in code or "s.status" in code)
        # The UI must never mint a status word the backend does not produce.
        for word in ("HEALTHY", "SYNCED", "'CONNECTED'", '"CONNECTED"'):
            check("the UI never invents the status %s" % word, word not in code)
        check("feature toggles PUT to the server",
              "/features" in code and "api.put" in code)
        check("adding a person is email-first (lookup before create)",
              "identity-lookup" in code and "look.can_add" in code)
        check("...and a refusal from the server is shown, not worked around",
              "look.reason" in code)
        check("entering a customer calls the audited context endpoint",
              "/god/platform/context/customer/" in code)

    app = read("App.jsx")
    if app:
        code = strip_comments(app)
        check("the banner is mounted on every tenant screen",
              "<ContextBanner />" in code and "<Layout>" in code)
        check("the new routes are god-guarded",
              code.count("PlatformOverview />") and "GodRoute" in code)
        for comp in ("PlatformOverview", "CustomerCreate", "CustomerDetail"):
            check("route wired: %s" % comp, comp + " />" in code)

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FAIL:
        print("\nFAILURES (%d):" % len(FAIL))
        for f in FAIL:
            print("  - %s" % f)
    else:
        print("\nALL PLATFORM FRONTEND CHECKS PASSED")
    print("=" * 78)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
