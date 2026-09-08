"""God Mode navigation and customer-workspace entry.

═══════════════════════════════════════════════════════════════════════════
THE BUG THIS FILE EXISTS FOR
═══════════════════════════════════════════════════════════════════════════

Clicking Enter on a customer row established the organization context
correctly - the server call ran, X-Org-Override and X-Brand-Override were both
set - and then bounced the owner straight back to the God Command Center.

The cause was not in the context system at all. FOUR separate call sites
navigated to `/god/customer-app` and THAT ROUTE WAS NEVER REGISTERED, so every
one of them fell through to the `/god/*` catch-all, which renders the Command
Center. Entry worked; the destination did not exist.

A route that does not exist cannot be caught by a Python test suite, so the
frontend assertions below read App.jsx as text. That is deliberate and it is
the only kind of test that would have caught this: the bug was an ABSENCE, and
absences are invisible to every test that exercises what is there.

═══════════════════════════════════════════════════════════════════════════
WHAT ELSE IS DEFENDED
═══════════════════════════════════════════════════════════════════════════

The two row actions must not lead to the same place - that was the second
complaint, and "360" beside "Enter" gave no clue which was which.

Every path in the rail must be a registered route. A rail item pointing at
nothing renders the Command Center and looks like the app ignoring the click.

Workspaces must appear exactly once. It was in both the primary nav and Jump
To, which reads as two different screens.

Brand destinations must come from the platform records. The rail hard-coded a
single site - whichever brand the domain happened to be - so on a white-label
platform every brand but one had no destination at all.
"""

import itertools
import os
import re

import pytest

from app.models.models import Organization, Platform, User
from app.services.auth_service import create_access_token, hash_password

FE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "frontend", "src")
APP_JSX = os.path.join(FE, "App.jsx")
GOD_SHELL = os.path.join(FE, "pages", "GodShell.jsx")
GOD_CUSTOMERS = os.path.join(FE, "pages", "GodCustomers.jsx")

_SEQ = itertools.count(60000)


def _read(p):
    return open(p, encoding="utf-8").read()


def _registered_routes(src):
    """Every path App.jsx registers, catch-alls included."""
    return set(re.findall(r'<Route\s+path=["\']([^"\']+)["\']', src))


# ═══════════════════════════════════════════════════════════════════════════
# THE ROUTE THAT DID NOT EXIST
# ═══════════════════════════════════════════════════════════════════════════

def test_the_god_customer_app_route_is_registered():
    """THE regression. Its absence was the whole bug."""
    routes = _registered_routes(_read(APP_JSX))
    assert "/god/customer-app" in routes, (
        "/god/customer-app is navigated to from GodCustomers, CustomerDetail, "
        "Layout's org picker and GodShell's Customer App jump. Without a "
        "registered route every one of them falls through to the /god/* "
        "catch-all and renders the Command Center - which is exactly the "
        "'Enter does not enter' symptom.")


def test_the_customer_app_route_is_registered_before_the_god_catch_all():
    """Order decides the outcome; after the catch-all it would never match."""
    src = _read(APP_JSX)
    specific = src.index('path="/god/customer-app"')
    catch_all = src.index('path="/god/*"')
    assert specific < catch_all, (
        "/god/customer-app is registered AFTER /god/*, so the catch-all wins "
        "and the Command Center renders instead of the tenant application.")


def test_every_place_that_navigates_to_the_customer_app_uses_the_same_path():
    """One destination, spelled one way, or the next one drifts again."""
    offenders = []
    for dirpath, dirnames, filenames in os.walk(FE):
        dirnames[:] = [d for d in dirnames if d != "node_modules"]
        for fn in filenames:
            if not fn.endswith((".jsx", ".js")):
                continue
            src = _read(os.path.join(dirpath, fn))
            for m in re.finditer(r"['\"](/god/customer[-_]app[^'\"]*)['\"]", src):
                if m.group(1) != "/god/customer-app":
                    offenders.append("%s -> %s" % (fn, m.group(1)))
    assert not offenders, "inconsistent customer-app paths: %s" % offenders


def test_the_customer_app_route_requires_god_and_a_selected_organization():
    """Rendering the tenant app with no org selected shows empty lists that
    look like a broken customer rather than a missing selection."""
    src = _read(APP_JSX)
    body = src[src.index("function GodCustomerAppRoute"):]
    body = body[:body.index("\nfunction ")]

    assert "god_admin" in body, "the route does not check god_admin"
    assert "getOrgContext" in body, (
        "the route does not read the organization context, so it cannot know "
        "which customer it is rendering")
    assert "/god/customers" in body, (
        "with no organization selected the route must send the owner back to "
        "pick one rather than render an unscoped tenant app")


def test_the_customer_app_route_reads_context_at_render_not_from_state():
    """Browser back after an exit must not resurrect the customer."""
    src = _read(APP_JSX)
    body = src[src.index("function GodCustomerAppRoute"):]
    body = body[:body.index("\nfunction ")]
    assert "useState" not in body, (
        "the route caches the organization context in state. Back/forward "
        "after an exit would then re-render a customer the owner has left.")


# ═══════════════════════════════════════════════════════════════════════════
# THE TWO ROW ACTIONS GO TO TWO DIFFERENT PLACES
# ═══════════════════════════════════════════════════════════════════════════

def _stripped(path):
    """Source with comments removed.

    A label assertion must read the RENDERED text, not prose. Checking the raw
    file made this test fail on the comment that explains why the old label was
    replaced - which would have taught the next person to delete the
    explanation to get green.
    """
    src = _read(path)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)     # block and JSX comments
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)     # line comments
    return src


def test_the_row_actions_are_named_360_and_workspace():
    src = _stripped(GOD_CUSTOMERS)
    assert "360" in src
    assert "'Workspace'" in src, "the entry action is not labelled Workspace"
    assert "'Enter'" not in src and '"Enter"' not in src, (
        "the ambiguous 'Enter' label is still rendered - it said nothing about "
        "what was being entered, the record or the product")


def test_the_360_action_and_the_workspace_action_have_different_targets():
    """They sat side by side looking interchangeable. They must not be."""
    src = _read(GOD_CUSTOMERS)
    assert "/360'" in src, "the 360 action does not open Customer 360"
    assert "handleEnter" in src, "the workspace action does not enter the org"
    enter = src[src.index("async function handleEnter"):]
    enter = enter[:enter.index("\n  const ")]
    assert "enterCustomer" in enter, (
        "the workspace action does not establish the organization context")
    assert "/god/customer-app" in enter, (
        "the workspace action does not route into the tenant application")
    assert "/360" not in enter, (
        "the workspace action navigates to Customer 360 - the two buttons go "
        "to the same place, which is the exact confusion being fixed")


def test_the_workspace_action_passes_the_exact_organization_id():
    """No fuzzy lookup: selecting org A must never open org B."""
    src = _read(GOD_CUSTOMERS)
    assert "handleEnter(e, o.organization_id, o.name)" in src, (
        "the workspace action does not pass the row's durable organization id")


# ═══════════════════════════════════════════════════════════════════════════
# THE RAIL
# ═══════════════════════════════════════════════════════════════════════════

def _nav_paths():
    src = _read(GOD_SHELL)
    block = src[src.index("const NAV = ["):]
    block = block[:block.index("\n]")]
    return re.findall(r"path:\s*'([^']+)'", block)


def test_every_rail_path_is_a_registered_route():
    """A rail item pointing at nothing renders the Command Center, which looks
    exactly like the app ignoring the click."""
    routes = _registered_routes(_read(APP_JSX))
    prefixes = {r.rstrip("/*") for r in routes}
    dead = []
    for p in _nav_paths():
        base = p.split("#")[0]          # hash entries jump within a page
        if not base or base in routes:
            continue
        if any(base == pref or base.startswith(pref + "/") for pref in prefixes if pref):
            continue
        dead.append(p)
    assert not dead, "rail entries with no registered route: %s" % dead


def test_workspaces_appears_exactly_once_in_the_rail():
    """It was in the primary nav AND under Jump To - the same screen listed
    twice reads as two different things."""
    src = _read(GOD_SHELL)
    nav = src[src.index("const NAV = ["):]
    nav = nav[:nav.index("\n]")]
    jump = src[src.index("const JUMP = ["):]
    jump = jump[:jump.index("\n]")]

    assert nav.count("'/god/workspaces'") == 1, "Workspaces duplicated in NAV"
    assert "/god/workspaces" not in jump, (
        "Workspaces is still in Jump To as well as the primary navigation")


def test_jump_to_carries_only_genuinely_different_contexts():
    """Anything that merely duplicates primary navigation belongs there once."""
    src = _read(GOD_SHELL)
    jump = src[src.index("const JUMP = ["):]
    jump = jump[:jump.index("\n]")]
    nav_paths = set(_nav_paths())
    for p in re.findall(r"path:\s*'([^']+)'", jump):
        assert p not in nav_paths, (
            "Jump To duplicates the primary navigation entry %s" % p)


@pytest.mark.parametrize("group", [
    "COMMAND", "CUSTOMERS", "SALES & REVENUE",
    "LEADS & AUTOMATION", "SECURITY & PLATFORM",
])
def test_the_rail_is_grouped_by_business_function(group):
    src = _read(GOD_SHELL)
    assert "group: '%s'" % group in src, "missing rail group %r" % group


def test_organizations_customers_and_workspaces_each_explain_themselves():
    """Three nouns the backend keeps separate. An owner who cannot tell them
    apart picks one at random and concludes the product is confused."""
    src = _read(GOD_SHELL)
    nav = src[src.index("const NAV = ["):]
    nav = nav[:nav.index("\n]")]
    for path in ("/god/organizations", "/god/customers", "/god/workspaces"):
        entry = nav[nav.index("'%s'" % path):]
        entry = entry[:entry.index("},") + 2]
        assert "hint:" in entry, "%s has no description in the rail" % path


def test_the_rail_does_not_hard_code_a_brand_website():
    """A single hard-coded site is the one arrangement guaranteed to be wrong
    on a white-label platform."""
    src = _read(GOD_SHELL)
    for literal in ("evosyspro.live", "bookaboost.live", "harmonyhustle.com"):
        assert literal not in src, (
            "GodShell hard-codes %s. Brand destinations must come from the "
            "platform records so a new brand needs configuration, not a "
            "deploy." % literal)


def test_brand_links_are_derived_from_the_platform_records():
    src = _read(GOD_SHELL)
    assert "/god/platform/overview" in src, (
        "the rail does not read the brand list from the platform records")
    assert "website_url" in src, "the rail does not use the configured site URL"
    assert "p.is_active && p.website_url" in src, (
        "the rail does not filter to active brands with a configured site - a "
        "brand with no URL would render a dead link")


# ═══════════════════════════════════════════════════════════════════════════
# THE BACKEND THE RAIL NOW DEPENDS ON
# ═══════════════════════════════════════════════════════════════════════════

def _god(db):
    u = User(organization_id=None, email="godnav%d@evosyspro.live" % next(_SEQ),
             password_hash=hash_password("GodPass123!"), full_name="God",
             role="god_admin", must_change_password=False, is_active=True)
    db.add(u); db.commit()
    return u


def _headers(db, user):
    return {"Authorization": "Bearer " + create_access_token(user, db)}


def test_platform_overview_returns_each_brands_configured_destinations(
        client, db_session):
    p = Platform(name="BrandNav", slug="brandnav-%d" % next(_SEQ),
                 website_url="https://example-brand.test",
                 app_base_url="https://app.example-brand.test")
    db_session.add(p); db_session.commit()

    body = client.get("/god/platform/overview",
                      headers=_headers(db_session, _god(db_session))).json()
    row = next(x for x in body["platforms"] if x["id"] == p.id)
    assert row["website_url"] == "https://example-brand.test"
    assert row["app_base_url"] == "https://app.example-brand.test"


def test_a_brand_with_no_configured_site_reports_null_not_a_guess(
        client, db_session):
    """Guessing a URL from a slug produces a dead link that looks like a bug."""
    p = Platform(name="NoSite", slug="nosite-%d" % next(_SEQ))
    db_session.add(p); db_session.commit()

    body = client.get("/god/platform/overview",
                      headers=_headers(db_session, _god(db_session))).json()
    row = next(x for x in body["platforms"] if x["id"] == p.id)
    assert row["website_url"] is None
    assert row["app_base_url"] is None


def test_platform_overview_requires_god(client, db_session):
    p = Platform(name="Guarded", slug="guarded-%d" % next(_SEQ))
    db_session.add(p); db_session.commit()
    org = Organization(name="Tenant", slug="tenant-%d" % next(_SEQ),
                       platform_id=p.id, is_active=True, plan="trial")
    db_session.add(org); db_session.commit()
    admin = User(organization_id=org.id,
                 email="tenantnav%d@evosyspro.live" % next(_SEQ),
                 password_hash=hash_password("TenantPass123!"),
                 full_name="Tenant Admin", role="org_admin",
                 must_change_password=False, is_active=True)
    db_session.add(admin); db_session.commit()

    r = client.get("/god/platform/overview", headers=_headers(db_session, admin))
    assert r.status_code in (401, 403, 404), (
        "a customer org admin read the platform-wide brand list")


def test_entering_a_customer_never_grants_a_membership(client, db_session):
    """The load-bearing rule of the whole entry design, asserted server-side.

    enterCustomer refuses to continue if memberships_before != memberships_after,
    so this proves the endpoint reports the pair the client checks.
    """
    p = Platform(name="EnterBrand", slug="enterbrand-%d" % next(_SEQ))
    db_session.add(p); db_session.commit()
    org = Organization(name="Enterable", slug="enterable-%d" % next(_SEQ),
                       platform_id=p.id, is_active=True, plan="trial")
    db_session.add(org); db_session.commit()

    r = client.post("/god/platform/context/customer/" + org.id, json={},
                    headers=_headers(db_session, _god(db_session)))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "memberships_before" in body and "memberships_after" in body, (
        "the enter endpoint no longer reports the membership pair the client "
        "asserts on - entry would silently stop being proven safe")
    assert body["memberships_before"] == body["memberships_after"], (
        "entering an organization changed a membership count")


def test_entering_resolves_the_exact_organization_requested(client, db_session):
    """Selecting org A must never open org B. No fuzzy lookup anywhere."""
    p = Platform(name="TwoOrgs", slug="twoorgs-%d" % next(_SEQ))
    db_session.add(p); db_session.commit()
    a = Organization(name="Alpha Co", slug="alpha-%d" % next(_SEQ),
                     platform_id=p.id, is_active=True, plan="trial")
    b = Organization(name="Alpha Corporation", slug="alphacorp-%d" % next(_SEQ),
                     platform_id=p.id, is_active=True, plan="trial")
    db_session.add_all([a, b]); db_session.commit()

    god = _god(db_session)
    for target in (a, b):
        body = client.post("/god/platform/context/customer/" + target.id, json={},
                           headers=_headers(db_session, god)).json()
        got = body["context"]["customer"]
        assert got["id"] == target.id, (
            "requested %s and the server resolved %s" % (target.id, got["id"]))


def test_an_unknown_organization_id_is_refused(client, db_session):
    r = client.post("/god/platform/context/customer/org-does-not-exist", json={},
                    headers=_headers(db_session, _god(db_session)))
    assert r.status_code >= 400, "an unknown organization id was accepted"


def test_exiting_the_organization_context_is_available(client, db_session):
    r = client.post("/god/platform/context/exit", json={},
                    headers=_headers(db_session, _god(db_session)))
    assert r.status_code == 200, r.text


def test_a_tenant_admin_cannot_enter_another_organization(client, db_session):
    """God authority is what permits entry. Nothing below it does."""
    p = Platform(name="IsoBrand", slug="isobrand-%d" % next(_SEQ))
    db_session.add(p); db_session.commit()
    mine = Organization(name="Mine", slug="mine-%d" % next(_SEQ),
                        platform_id=p.id, is_active=True, plan="trial")
    theirs = Organization(name="Theirs", slug="theirs-%d" % next(_SEQ),
                          platform_id=p.id, is_active=True, plan="trial")
    db_session.add_all([mine, theirs]); db_session.commit()
    admin = User(organization_id=mine.id,
                 email="isoadmin%d@evosyspro.live" % next(_SEQ),
                 password_hash=hash_password("IsoPass123!"),
                 full_name="Iso Admin", role="org_admin",
                 must_change_password=False, is_active=True)
    db_session.add(admin); db_session.commit()

    r = client.post("/god/platform/context/customer/" + theirs.id, json={},
                    headers=_headers(db_session, admin))
    assert r.status_code in (401, 403, 404), (
        "a customer org admin entered another organization's context")
