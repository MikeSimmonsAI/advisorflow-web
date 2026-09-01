"""GATE 33 - A FAILED REQUEST IS NOT A REFUSAL, AND A REFUSAL IS NOT A FAILURE.

Jason McClellan, an advisor with an active Restland customer_org membership and
100 assigned leads that the backend could prove four independent ways, was shown
"You don't have access to this page" by his own browser. The backend never said
so. The frontend guard did, because it was written as:

    api.get('/auth/workspace/' + id).then(ok).catch(() => denied)

which reads 401, 404, 500, a cold-start timeout and a dropped connection as the
same answer as 403.

This gate holds three things:

 1. THE DECISION IS EXECUTED, not read. frontend/tests/workspaceGuard.test.mjs
    imports the real module the app imports and drives every lifecycle,
    including a sweep of every combination of phase and error, asserting that
    NO input produces a denial without an authenticated 403.

 2. THE TEST IS PROVEN BY REVERTS. Three separate ways of reintroducing the bug
    are applied to the real source and the suite must FAIL for each. A test that
    passes against the defect it was written for is decoration.

 3. THE TWO SIBLING DEFECTS STAY FIXED. Logout must not leave one person's
    workspace context for the next person, and a failed dashboard request must
    not render as the number 0.
"""
import io
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "frontend", "src")
GUARD = os.path.join(SRC, "auth", "workspaceGuard.js")
TEST = os.path.join(REPO, "frontend", "tests", "workspaceGuard.test.mjs")

FAIL, PASSED = [], []


def read_file(path):
    if not os.path.exists(path):
        return None
    return io.open(path, encoding="utf-8", errors="replace").read()


def read(*parts):
    return read_file(os.path.join(SRC, *parts))


def check(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "FAIL ", label,
                         ("\n          -> " + str(detail)[:240]) if detail else ""))
    (PASSED if ok else FAIL).append(label)


def strip_comments(s):
    """Remove // and /* */ so assertions test code, not commentary."""
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    return re.sub(r"^\s*//.*$", "", s, flags=re.M)


def node_binary():
    for cand in (os.environ.get("NODE_BIN"),
                 r"C:\Program Files\nodejs\node.exe",
                 "/usr/bin/node", "/usr/local/bin/node", "node"):
        if not cand:
            continue
        if cand == "node" or os.path.exists(cand):
            return cand
    return "node"


def run_suite():
    """Returns (exit_code, combined output). Never raises."""
    try:
        p = subprocess.run([node_binary(), TEST], cwd=REPO,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=180)
        return p.returncode, p.stdout.decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - a missing node is a gate failure
        return 99, repr(exc)


# ── the reverts ─────────────────────────────────────────────────────────────
#
# Each is a DIFFERENT way of putting the incident back. They are applied to the
# real file, the real suite is run against it, and the file is restored in a
# finally block whatever happens.
REVERTS = [
    (
        "a transport failure on the context list is read as a refusal",
        "      state: UNVERIFIED,\n      reason: 'contexts-unavailable',",
        "      state: DENIED,\n      reason: 'contexts-unavailable',",
    ),
    (
        "a transport failure on the confirmation is read as a refusal",
        "    state: UNVERIFIED,\n    reason: 'confirmation-unavailable',",
        "    state: DENIED,\n    reason: 'confirmation-unavailable',",
    ),
    (
        "a server error is added to the set of statuses that mean 'no'",
        "export const DENIAL_STATUSES = [403]",
        "export const DENIAL_STATUSES = [403, 500]",
    ),
    (
        "the guard concludes before the server has answered",
        "  if (contextsPhase === 'idle' || contextsPhase === 'loading') {\n"
        "    return { state: VERIFYING, reason: 'contexts-loading' }",
        "  if (contextsPhase === 'idle' || contextsPhase === 'loading') {\n"
        "    return { state: DENIED, reason: 'contexts-loading' }",
    ),
]


def prove_by_revert(original):
    for label, old, new in REVERTS:
        if old not in original:
            check("revert anchor present: %s" % label, False,
                  "the gate's own patch no longer matches the source")
            continue
        io.open(GUARD, "w", encoding="utf-8", newline="").write(
            original.replace(old, new, 1))
        code, out = run_suite()
        check("REVERT caught: %s" % label, code != 0,
              "the suite PASSED against the reintroduced defect" if code == 0
              else "suite exited %d" % code)


def main():
    print("=" * 78)
    print("GATE 33 - WORKSPACE ROUTE GUARD: NO DENIAL WITHOUT A 403")
    print("=" * 78)

    guard_src = read_file(GUARD)
    check("frontend/src/auth/workspaceGuard.js exists", guard_src is not None)
    check("frontend/tests/workspaceGuard.test.mjs exists",
          read_file(TEST) is not None)
    if guard_src is None or read_file(TEST) is None:
        finish()
        return

    print("\n-- the decision module, executed --")
    code, out = run_suite()
    check("the guard suite passes", code == 0, out[-600:] if code else "")
    m = re.search(r"PASSED (\d+) checks \((\d+) lifecycles", out or "")
    if m:
        print("       %s checks, %s lifecycles swept" % (m.group(1), m.group(2)))
    check("the sweep is not vacuous (>10000 lifecycles)",
          bool(m) and int(m.group(2)) > 10000, m.group(2) if m else "no sweep line")

    print("\n-- proven by reverts --")
    try:
        prove_by_revert(guard_src)
    finally:
        io.open(GUARD, "w", encoding="utf-8", newline="").write(guard_src)
    code, _ = run_suite()
    check("the source was restored and the suite is green again", code == 0)

    # ── the route that consumes the decision ────────────────────────────────
    print("\n-- WorkspaceRoute --")
    app = read("App.jsx")
    check("App.jsx exists", app is not None)
    if app:
        code_app = strip_comments(app)
        route = code_app.split("function WorkspaceRoute()", 1)
        check("WorkspaceRoute is present", len(route) == 2)
        body = route[1].split("\nfunction ", 1)[0] if len(route) == 2 else ""

        check("the route asks the shared decision module rather than deciding",
              "decideWorkspaceAccess(" in body)
        check("...and renders the refusal ONLY for the DENIED state",
              re.search(r"decision\.state === DENIED[\s\S]{0,400}?<Unauthorized", body)
              is not None)
        check("...and has a state for a check that could not be completed",
              "UNVERIFIED" in body and "VerificationUnavailable" in body)
        check("...which does not render the workspace either (no fail-open)",
              re.search(r"decision\.state === UNVERIFIED[\s\S]{0,400}?<Overview", body)
              is None)
        check("VERIFYING renders neither the workspace nor a refusal",
              body.rstrip().endswith("return null\n}") or
              re.search(r"return null\s*\}\s*$", body) is not None)

        # THE DEFECT ITSELF: no catch handler anywhere in the route may set a
        # denial. The error object is stored; the decision is made elsewhere.
        catch_bodies = re.findall(r"\.catch\(([\s\S]{0,200}?)\)\s*\n", body)
        offending = [c for c in catch_bodies
                     if "denied" in c.lower() or "DENIED" in c]
        check("no catch handler in WorkspaceRoute concludes a denial",
              not offending, offending[:2] or "none")

        check("the error OBJECT is kept, not flattened to a message",
              "confirmError" in body and "err?.message" not in body)
        check("the workspace selection is set only on the authorized path",
              re.search(r"decision\.state === AUTHORIZED[\s\S]{0,400}?setWorkspaceContext",
                        body) is not None)
        check("...and cleared on a verified denial",
              re.search(r"decision\.state === DENIED[\s\S]{0,300}?clearWorkspaceContext",
                        body) is not None)

        # The login redirect must not invent a second authority: HomeRedirect
        # navigates from the server's own default_context and falls back to the
        # legacy home on failure rather than to a refusal.
        home = code_app.split("function HomeRedirect()", 1)
        if len(home) == 2:
            hbody = home[1].split("\nfunction ", 1)[0]
            check("the login redirect navigates from the SERVER's default context",
                  "default_context" in hbody)
            check("...and never renders a refusal of its own",
                  "Unauthorized" not in hbody)

    # ── sibling defect 1: logout leaves the previous person's context ───────
    print("\n-- logout does not hand the next person a context --")
    client = read("api", "client.js")
    check("client.js exists", client is not None)
    if client:
        c = strip_comments(client)
        logout = c.split("export async function logout()", 1)
        check("logout() is present", len(logout) == 2)
        if len(logout) == 2:
            lbody = logout[1].split("\n}", 1)[0]
            check("logout clears the org, brand and workspace context",
                  "clearAllContext()" in lbody)
            check("logout clears the token", "clearToken()" in lbody)
            check("logout drops the cached context list", "resetMyContexts()" in lbody)
        check("clearAllContext clears all three",
              re.search(r"function clearAllContext\(\)[\s\S]{0,240}?clearOrgContext\(\)"
                        r"[\s\S]{0,240}?clearBrandContext\(\)"
                        r"[\s\S]{0,240}?clearWorkspaceContext\(\)", c) is not None)
        login = c.split("export async function login(", 1)
        if len(login) == 2:
            lbody = login[1].split("\n}", 1)[0]
            check("a new login cannot inherit the previous list of workspaces",
                  "resetMyContexts()" in lbody)
        check("a failed context fetch is never cached as an answer",
              re.search(r"_contextsPromise = api\.get\('/auth/my-contexts'\)"
                        r"[\s\S]{0,200}?_contextsPromise = null", c) is not None)

    # ── sibling defect 2: a failed request rendered as the number 0 ─────────
    print("\n-- a refused request is not an empty pipeline --")
    ov = read("pages", "Overview.jsx")
    check("Overview.jsx exists", ov is not None)
    if ov:
        o = strip_comments(ov)
        swallowers = re.findall(r"\.catch\(\(\)\s*=>\s*(?:null|\[\]|0|\{\})\)", o)
        check("no dashboard call swallows its failure into an empty value",
              not swallowers, swallowers[:3] or "none")
        check("every failure is recorded with the call that produced it",
              "const attempt = (" in o and "failures.push(" in o)
        check("...and surfaced to the person reading the numbers",
              "setLoadError(" in o and "loadError" in o)
        check("the failure notice is announced, not just coloured",
              'role="alert"' in o)
        # The advisor/manager asymmetry: [isManager] alone never changes for an
        # advisor, so a first burst that failed stayed failed until a reload.
        deps = re.search(r"\}, \[isManager([^\]]*)\]\)", o)
        check("the dashboard refetches on identity and workspace, not only role",
              bool(deps) and "identityKey" in (deps.group(1) or "")
              and "workspaceKey" in (deps.group(1) or ""),
              deps.group(0) if deps else "dependency array not found")
        check("a real zero still renders as zero",
              "?? 0" in o or "?? null" in o,
              "null means unknown and renders an em-dash; 0 means zero")

    # ── deployment compatibility ────────────────────────────────────────────
    #
    # The gate that would have caught this class earliest: the shipped bundle
    # must actually contain the guard being asserted above. A green source
    # assertion over an unbuilt tree proves nothing about what a browser runs.
    print("\n-- the built bundle carries the fix --")
    dist = os.path.join(REPO, "frontend", "dist", "assets")
    bundles = []
    if os.path.isdir(dist):
        bundles = [os.path.join(dist, f) for f in os.listdir(dist)
                   if f.endswith(".js")]
    check("a built bundle exists", bool(bundles),
          "run the frontend build before deploying")
    if bundles:
        newest = max(bundles, key=os.path.getmtime)
        blob = read_file(newest) or ""
        check("the bundle contains the four-state guard",
              "confirmation-unavailable" in blob and "listed-by-server" in blob,
              os.path.basename(newest))
        check("the bundle contains the 'could not check' screen",
              "check your access just now" in blob)
        check("the bundle no longer ships the old two-state guard",
              "status: 'checking'" not in blob and '"checking"' not in blob)


def finish():
    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FAIL:
        print("\nFAILURES (%d):" % len(FAIL))
        for f in FAIL:
            print("  - %s" % f)
        print("\nWORKSPACE GUARD BROKE")
    else:
        print("\nNO DENIAL WITHOUT A 403 - ALL WORKSPACE GUARD CHECKS PASSED")
    print("=" * 78)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
    finish()
