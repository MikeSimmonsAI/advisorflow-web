"""ENTERING A CUSTOMER WORKSPACE LANDS ON THAT CUSTOMER'S OWN SCREENS.

THE DEFECT, AS A USER SAW IT. A platform owner clicked "Enter organization" on
a commercial-cleaning customer in God Mode and landed on the PLATFORM
dashboard — generic "TOTAL LEADS" and "APPOINTMENTS" tiles, with the
AdvisorFlow → brand → customer strip over them — and stayed there until the
browser was refreshed. The navigation rail, the skin and the vocabulary beside
it were all that customer's own, which is what made it read as a rendering
glitch rather than a scoping one.

THE FIRST ACTUAL FAILURE, CAPTURED LIVE. Instrumenting the transition in
production produced this order:

    POST /god/platform/context/customer/<id>   200
    set    af_org_context
    remove af_branding
    GET  /branding/org                          200   →  industry: null   ←
    (navigate to "/")  main renders the platform dashboard
    ... 2.2s later, from inside the workspace ...
    GET  /branding/org                          200   →  industry: cleaning

Two reads of one endpoint, one session, two different answers. The difference
is the `X-Org-Override` header, and `request()` decided whether to send it from
`window.location.pathname` alone — which, during the transition, still said
`/god`. `classifyRoute` calls that PLATFORM, the header was stripped, and the
server answered as the neutral owner. `industry: null` was cached, and
`verticals/workspaceVertical.js` chooses the whole customer presentation from
`industry`.

TWO FIXES, BOTH GENERAL:

  1. `asCustomer` — a per-call, caller-supplied organization for the one
     request that cannot read its subject off the address bar. The whole
     decision now lives in `routeAuthority.orgOverrideFor`, exercised
     directly in tests/frontend/workspaceEntry.test.mjs.

  2. The routed page is keyed on the ACTIVE WORKSPACE as well as the path.
     `children` is an element Layout receives, so when Layout re-rendered from
     its own state React compared that element to itself and skipped the
     subtree — which is why the rail corrected itself and the page did not.
     A workspace change is now a remount, so no page can outlive the customer
     it was rendered for.

This file holds the server half (the header is what resolves the workspace),
the wiring, and the properties from commit 10604df that must survive.
"""
import json
import os
import subprocess
import uuid

import pytest

from app.models.models import Organization, Platform, User
from app.services.auth_service import create_access_token, hash_password

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "frontend", "src")

ORG_OVERRIDE_HEADER = "X-Org-Override"

CLIENT_FEATURES = ["leads", "reports", "users", "branding_settings"]


def _read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


def _org(db, name, platform, industry):
    org = Organization(name=name, slug="e-" + uuid.uuid4().hex[:8],
                       plan="standard", industry=industry, is_active=True,
                       platform_id=platform.id,
                       enabled_features=json.dumps(CLIENT_FEATURES))
    db.add(org)
    db.commit()
    return org


@pytest.fixture()
def estate(db_session):
    """Two customers in two different verticals, and the platform owner.

    Two, because "the workspace decides" is not demonstrated by one: the test
    that matters is that entering each one answers with ITS OWN industry.
    """
    platform = Platform(name="EvoSys Pro", slug="ev-" + uuid.uuid4().hex[:6])
    db_session.add(platform)
    db_session.commit()

    cleaning = _org(db_session, "Northbank Facility Care", platform, "cleaning")
    energy = _org(db_session, "Atlantis Light & Power", platform, "energy")

    owner = User(organization_id=None, email="owner@evosys.example",
                 password_hash=hash_password("TestPass123!"),
                 full_name="Platform Owner", role="god_admin", is_active=True,
                 must_change_password=False)
    db_session.add(owner)
    db_session.commit()

    return {"platform": platform, "cleaning": cleaning, "energy": energy,
            "owner": owner}


def _auth(db, user, *, inside=None):
    h = {"Authorization": "Bearer " + create_access_token(user, db)}
    if inside is not None:
        h[ORG_OVERRIDE_HEADER] = inside.id
    return h


# ── the server half: the header is what resolves the workspace ──────────────

def test_the_branding_read_answers_for_the_customer_it_names(estate, db_session, client):
    """THE TWO ANSWERS FROM THE PRODUCTION TIMELINE, REPRODUCED.

    Same endpoint, same owner, same session — one carrying the header and one
    not. The one without it is the answer the app cached and then built a
    workspace out of.
    """
    owner = estate["owner"]

    entered = client.get("/branding/org",
                         headers=_auth(db_session, owner,
                                       inside=estate["cleaning"])).json()
    assert entered["industry"] == "cleaning", \
        "entering a cleaning customer did not resolve that customer"
    assert entered["organization_id"] == estate["cleaning"].id

    neutral = client.get("/branding/org", headers=_auth(db_session, owner)).json()
    assert neutral["industry"] != "cleaning", (
        "the neutral owner's read now claims a customer's vertical — if this "
        "ever equals the entered answer, this test proves nothing")


def test_each_workspace_answers_with_its_own_vertical(estate, db_session, client):
    """THE REUSABILITY CLAIM, STATED AS A TEST.

    The destination is not a redirect somebody wrote for one customer. It is
    whatever `industry` the entered organization carries, which is what the
    dashboard, the rail and the vocabulary are all chosen from. Two customers,
    two verticals, one mechanism.
    """
    owner = estate["owner"]
    for key, expected in (("cleaning", "cleaning"), ("energy", "energy")):
        body = client.get("/branding/org",
                          headers=_auth(db_session, owner,
                                        inside=estate[key])).json()
        assert body["industry"] == expected, \
            "entering %s resolved %r" % (key, body["industry"])
        assert body["organization_id"] == estate[key].id


def test_switching_customers_switches_the_answer(estate, db_session, client):
    """CCB → Atlantis → CCB, one header apart, with nothing stale in between.

    The browser-side symptom was a page that outlived the customer it was
    rendered for. This is the same property one layer down: the read that
    decides the presentation follows the customer being entered, every time,
    with no memory of the last one.
    """
    owner = estate["owner"]

    def industry(org):
        return client.get("/branding/org",
                          headers=_auth(db_session, owner, inside=org)).json()["industry"]

    assert industry(estate["cleaning"]) == "cleaning"
    assert industry(estate["energy"]) == "energy"
    assert industry(estate["cleaning"]) == "cleaning"


def test_the_industry_is_canonical_not_whatever_was_typed(estate, db_session, client):
    """`organizations.industry` is stored raw, and every consumer compares it
    to a canonical key. A customer created as "Commercial Cleaning" must
    resolve the same vertical as one created as "cleaning", or the fix works
    for whichever spelling happened to be used."""
    raw = _org(db_session, "Summit Building Services", estate["platform"],
               "Commercial Cleaning")
    body = client.get("/branding/org",
                      headers=_auth(db_session, estate["owner"], inside=raw)).json()
    assert body["industry"] == "cleaning", \
        "a raw industry spelling no longer resolves to its canonical key"


# ── the browser half, run for real ──────────────────────────────────────────

def test_the_entry_decision_itself(): # noqa: D103
    """THE DECISION, EXECUTED — not matched in the source.

    `routeAuthority.js` imports nothing, so the real function runs under plain
    node with no bundler and no DOM. The cases are in
    tests/frontend/workspaceEntry.test.mjs: entering from `/god` names the
    customer, entering without naming one still sends nothing, and every
    customer-space route keeps the behaviour it had.
    """
    script = os.path.join(ROOT, "tests", "frontend", "workspaceEntry.test.mjs")
    try:
        proc = subprocess.run(["node", script], capture_output=True, text=True,
                              cwd=ROOT, timeout=120)
    except (FileNotFoundError, OSError):
        pytest.skip("node is not available on this machine")
    assert proc.returncode == 0, (
        "the workspace-entry decision regressed:\n"
        + (proc.stdout or "") + (proc.stderr or ""))


# ── the wiring, which the node test cannot see ──────────────────────────────

def test_entering_a_customer_names_that_customer_on_the_branding_read():
    """The one call site the whole defect turned on."""
    body = _read(FE, "pages", "god", "enterCustomer.js")
    assert "fetchAndStoreBranding({ applyTheme: false, asCustomer: orgId })" in body, (
        "enterCustomer no longer names the organization it is entering, so the "
        "branding read is decided by the address bar again — which still says "
        "/god at that moment")


def test_the_request_layer_has_one_place_that_decides_the_override():
    """Two copies of this rule is how one of them ends up wrong."""
    client_js = _read(FE, "api", "client.js")
    assert "orgOverrideFor(" in client_js, \
        "client.js no longer asks routeAuthority which customer to send"
    assert "asCustomer" in client_js
    assert "'asCustomer'" in client_js, \
        "asCustomer is not in CLIENT_ONLY_OPTIONS, so it is spread into fetch() " \
        "as a no-op property and the header is silently never sent"


def test_the_explicit_customer_is_part_of_the_dedupe_key():
    """Otherwise the entry read can be merged with a platform read of the same
    path already in flight, and the wrong answer arrives by another route."""
    client_js = _read(FE, "api", "client.js")
    block = client_js[client_js.index("function _getDedupeKey"):]
    block = block[:block.index("export function resetInFlightGets")]
    assert "asCustomer" in block, \
        "the dedupe key ignores asCustomer, so two differently-scoped reads of " \
        "one path can be coalesced into one answer"


def test_the_routed_page_is_keyed_on_the_active_workspace():
    """THE SECOND HALF, AND THE ONE THAT MAKES A STALE PAGE IMPOSSIBLE.

    `children` is an element Layout RECEIVES. When Layout re-renders from its
    own state — which is what happens when a branding answer lands — React
    compares that same element object to itself and skips the subtree. The
    rail, the skin and the vocabulary updated; the page between them did not.
    A key that names the workspace turns a customer switch into a remount.
    """
    body = _read(FE, "components", "Layout.jsx")
    assert "const workspaceKey = [branding?.organization_id || ''," in body, \
        "Layout no longer derives a workspace key from the resolved organization"
    assert "<PageBoundary key={location.pathname + '\\u0000' + workspaceKey}>" in body, \
        "the routed page is keyed on the path alone again — entering a customer " \
        "and switching between two customers both land on the same path, so the " \
        "path cannot tell them apart"


def test_leaving_a_customer_still_drops_every_cache_that_names_one():
    """Returning to God Mode must not leave a customer's entitlements,
    vocabulary or context behind for the platform screens to render from."""
    body = _read(FE, "pages", "god", "enterCustomer.js")
    exit_block = body[body.index("export async function exitCustomer"):]
    for call in ("clearOrgContext()", "clearBranding()", "clearTerminology()"):
        assert call in exit_block, "exitCustomer no longer calls %s" % call


def test_a_normal_sign_in_is_untouched():
    """The client path was never affected and must stay that way: Login reads
    the branding for the workspace it is about to open, and does not name a
    customer, because a customer signing in IS the customer."""
    body = _read(FE, "pages", "Login.jsx")
    assert "fetchAndStoreBranding" in body
    assert "asCustomer" not in body, \
        "Login now names a customer on its branding read — sign-in resolves the " \
        "workspace from the account, and nothing in that flow should override it"


# ── what must survive from the Reports/scope correction (10604df) ───────────

def test_the_reporting_scope_predicate_is_still_the_one_that_is_asked():
    """The pass before this one replaced `role == "god_admin"` as a scope test.
    Entering a workspace is exactly the transition that defect lived in, so
    these are asserted together rather than in two places."""
    from app.services import lead_scope
    assert hasattr(lead_scope, "god_sees_all_orgs")
    for module in ("reports_router", "pipeline_router", "admin_router"):
        body = _read(ROOT, "app", "routers", module + ".py")
        assert "god_sees_all_orgs" in body, \
            "%s no longer asks the scope predicate" % module
        assert 'is_god = current_user.role == "god_admin"' not in body, \
            "%s is back to reading the role as if it were a scope" % module


def test_the_vertical_reports_screen_is_still_reachable():
    """The delegation added in 10604df, which this change routes through."""
    body = _read(FE, "pages", "Reports.jsx")
    assert "import CleaningReports from './vertical/CleaningReports'" in body
    assert "<CleaningReports />" in body
    assert "return <PlatformReports />" in body


def test_the_page_boundary_still_contains_rather_than_reloads():
    """Keying it on the workspace must not have turned it into a refresh."""
    body = _read(FE, "components", "PageBoundary.jsx")
    for forbidden in ("window.location.reload", "window.location.href",
                      "window.location.assign", "setTimeout"):
        assert forbidden not in body, \
            "PageBoundary papers over the failure with %s" % forbidden
