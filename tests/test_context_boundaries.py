"""Platform operations are never silently scoped by a stale customer selection.

═══════════════════════════════════════════════════════════════════════════
THE DEFECT
═══════════════════════════════════════════════════════════════════════════

`X-Org-Override` was sent on EVERY request unless a call site opted out. On the
server, `deps.get_current_user` answers that header for a god_admin with:

    user.organization_id = org_override

and roughly 179 routers read that attribute at face value. So once the owner
entered Restland, every later request - including ones to platform tools -
arrived claiming to BE Restland, and `_god_all_orgs` (the flag meaning "no
customer selected, show the estate") was silently False.

THAT WAS NOT ONLY A MISLEADING BANNER. `lead_scraper_router._resolve_target_org`
resolves its import destination as:

    org_id = requested_org_id or current_user.organization_id

with a guard that only fires when `_god_all_orgs` is set. With a sticky
override the guard could not fire, so a back-office acquisition tool would
import into whichever customer the owner had last looked at.

═══════════════════════════════════════════════════════════════════════════
WHAT IS TESTED, AND WHERE
═══════════════════════════════════════════════════════════════════════════

The fix is in the API client: the override is sent ONLY on customer-space
routes, decided by `auth/routeAuthority.js`. That is frontend logic, so the
route-classification half is asserted by reading the source - the same
technique that caught the missing `/god/customer-app` route, and for the same
reason: the bug is about what is NOT sent.

The server half is asserted for real. `_resolve_target_org` REFUSES to guess
when no organization is selected, which is the behaviour the client fix now
actually reaches.
"""

import itertools
import os
import re

import pytest

from app.models.models import Organization, Platform, User
from app.services.auth_service import create_access_token, hash_password

FE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "frontend", "src")
ROUTE_AUTHORITY = os.path.join(FE, "auth", "routeAuthority.js")
API_CLIENT = os.path.join(FE, "api", "client.js")
GOD_SHELL = os.path.join(FE, "pages", "GodShell.jsx")
CONTEXT_BANNER = os.path.join(FE, "components", "ContextBanner.jsx")
APP_JSX = os.path.join(FE, "App.jsx")

_SEQ = itertools.count(70000)


def _read(p):
    return open(p, encoding="utf-8").read()


# ═══════════════════════════════════════════════════════════════════════════
# ROUTE CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════

def test_the_route_authority_module_exists():
    assert os.path.exists(ROUTE_AUTHORITY), (
        "there is no single place that decides which authority a route belongs "
        "to, so the decision will be re-implemented differently in each caller")


def test_platform_routes_are_classified_as_platform():
    src = _read(ROUTE_AUTHORITY)
    # /god/* is platform, with the customer app as the stated exception.
    assert "'/god/'" in src or '"/god/"' in src
    assert "CUSTOMER_INSIDE_GOD" in src
    assert "/god/customer-app" in src, (
        "the one /god path that IS customer space is not excepted, so entering "
        "a customer would render the tenant app with no scope at all")


def test_the_bare_path_platform_tools_are_listed():
    """These are the leaks: god-only tools living outside /god."""
    src = _read(ROUTE_AUTHORITY)
    for path in ("/scraper", "/god/lead-scraper", "/provision-client", "/orgs"):
        assert path in src, "%s is not classified as a platform surface" % path


def test_the_api_client_gates_the_override_on_route_authority():
    """THE fix. One line, and it is the whole defence."""
    src = _read(API_CLIENT)
    assert "shouldSendOrgOverride" in src, (
        "the API client still sends X-Org-Override from wherever the user "
        "happens to be standing")
    # The gate must actually be in the expression that decides orgCtx.
    m = re.search(r"const orgCtx = .*", src)
    assert m and "routeAllowsOrg" in m.group(0), (
        "the route gate is imported but not applied to the override: %s"
        % (m.group(0) if m else "orgCtx assignment not found"))


def test_the_customer_default_is_the_safe_direction():
    """An unclassified route must fall to CUSTOMER, not PLATFORM.

    A customer screen that lost its scope would read another tenant's data.
    A platform screen that kept one is merely wrong. Defaulting the other way
    would turn every future customer page into a cross-tenant leak until
    somebody remembered to list it.
    """
    src = _read(ROUTE_AUTHORITY)
    tail = src[src.index("export function classifyRoute"):]
    tail = tail[:tail.index("\n}")]
    assert tail.rstrip().endswith("return CUSTOMER"), (
        "classifyRoute does not default to CUSTOMER")


# ═══════════════════════════════════════════════════════════════════════════
# THE BANNER MUST NOT MAKE A FALSE STATEMENT
# ═══════════════════════════════════════════════════════════════════════════

def test_the_god_shell_banner_is_authority_aware():
    src = _read(GOD_SHELL)
    assert "onPlatformSurface" in src, (
        "the God shell still shows one banner regardless of which authority "
        "the screen operates under")
    assert "classifyRoute" in src
    # The customer wording must be conditional, not unconditional.
    idx = src.index("VIEWING AS")
    assert "onPlatformSurface" in src[:idx], (
        "VIEWING AS is rendered before any authority check")


def test_the_context_banner_hides_on_platform_surfaces():
    src = _read(CONTEXT_BANNER)
    assert "classifyRoute" in src
    assert "onPlatformSurface" in src, (
        "the tenant context trail still renders over platform tools")


# ═══════════════════════════════════════════════════════════════════════════
# LEAD SCRAPER IS A BACK-OFFICE TOOL
# ═══════════════════════════════════════════════════════════════════════════

def test_the_scraper_renders_in_the_god_shell_not_the_tenant_layout():
    """It was god-only already, but rendered inside the customer Layout - which
    is why it presented itself as 'AdvisorFlow -> EvoSys Pro -> Restland'."""
    src = _read(APP_JSX)
    m = re.search(r'<Route path="/god/lead-scraper".{0,200}?/>', src, re.S)
    assert m, "Lead Scraper has no platform route"
    assert "GodModeLayout" in m.group(0), (
        "Lead Scraper does not render in the God shell")
    assert "<LeadScraper />" in m.group(0)


def test_the_old_scraper_path_redirects_rather_than_breaking():
    src = _read(APP_JSX)
    m = re.search(r'<Route path="/scraper".{0,160}?/>', src, re.S)
    assert m, "/scraper was removed outright, breaking existing links"
    assert "Navigate" in m.group(0) and "/god/lead-scraper" in m.group(0)


def test_the_scraper_is_not_in_the_customer_sidebar():
    src = _read(os.path.join(FE, "components", "Layout.jsx"))
    nav = src[:src.index("export default")] if "export default" in src else src
    assert "'/scraper'" not in nav and "'/god/lead-scraper'" not in nav, (
        "a platform acquisition tool is advertised in the customer's own nav")


def test_the_scraper_destination_has_no_stale_default(  # frontend contract
):
    """The selector must start EMPTY and the import must be blocked without it.

    A destination pre-filled from remembered context is precisely how a scrape
    lands in the wrong customer.
    """
    src = _read(os.path.join(FE, "pages", "LeadScraper.jsx"))
    assert re.search(r"useState\(''\)", src), "targetOrgId has a default value"
    assert "if (!targetOrgId)" in src, "import is not blocked without a destination"
    assert "target_org_id" in src, "the destination is not sent explicitly"


# ═══════════════════════════════════════════════════════════════════════════
# THE SERVER REFUSES TO GUESS — which the client fix now actually reaches
# ═══════════════════════════════════════════════════════════════════════════

def _god(db):
    u = User(organization_id=None, email="ctxgod%d@evosyspro.live" % next(_SEQ),
             password_hash=hash_password("GodPass123!"), full_name="God",
             role="god_admin", must_change_password=False, is_active=True)
    db.add(u); db.commit()
    return u


def _headers(db, user, org_override=None):
    h = {"Authorization": "Bearer " + create_access_token(user, db)}
    if org_override:
        h["X-Org-Override"] = org_override
    return h


@pytest.fixture()
def two_orgs(db_session):
    p = Platform(name="CtxBrand", slug="ctxbrand-%d" % next(_SEQ))
    db_session.add(p); db_session.commit()
    a = Organization(name="Restland", slug="restland-%d" % next(_SEQ),
                     platform_id=p.id, is_active=True, plan="trial")
    b = Organization(name="Other Co", slug="otherco-%d" % next(_SEQ),
                     platform_id=p.id, is_active=True, plan="trial")
    db_session.add_all([a, b]); db_session.commit()
    return p, a, b


def test_the_scraper_refuses_to_import_with_no_organization_selected(
        client, db_session, two_orgs):
    """THE accidental-import guard, reachable now that platform routes send no
    override. Without an explicit destination it must refuse, not guess."""
    _p, _a, _b = two_orgs
    r = client.post("/scraper/import",
                    json={"leads": [{"place_id": "pl_biz", "name": "Biz", "phone": "+12145550001"}]},
                    headers=_headers(db_session, _god(db_session)))
    assert r.status_code == 400, r.text
    assert "organization" in r.text.lower()


def test_the_scraper_imports_into_the_organization_it_was_given(
        client, db_session, two_orgs):
    from app.models.models import Lead
    _p, a, b = two_orgs
    r = client.post("/scraper/import", json={
        "leads": [{"place_id": "pl_target_biz", "name": "Target Biz", "phone": "+12145550002"}],
        "target_org_id": b.id,
    }, headers=_headers(db_session, _god(db_session)))
    assert r.status_code in (200, 201), r.text

    assert db_session.query(Lead).filter(Lead.organization_id == b.id).count() == 1
    assert db_session.query(Lead).filter(Lead.organization_id == a.id).count() == 0, (
        "leads landed in an organization that was never chosen")


def test_an_explicit_destination_beats_any_override(client, db_session, two_orgs):
    """Even if an override somehow arrives, the STATED destination wins.

    Belt and braces: the client no longer sends one from a platform route, and
    the server would not defer to it here anyway.
    """
    from app.models.models import Lead
    _p, a, b = two_orgs
    r = client.post("/scraper/import", json={
        "leads": [{"place_id": "pl_explicit_biz", "name": "Explicit Biz", "phone": "+12145550003"}],
        "target_org_id": b.id,
    }, headers=_headers(db_session, _god(db_session), org_override=a.id))
    assert r.status_code in (200, 201), r.text
    assert db_session.query(Lead).filter(Lead.organization_id == b.id).count() == 1
    assert db_session.query(Lead).filter(Lead.organization_id == a.id).count() == 0


def test_an_unknown_destination_organization_is_refused(client, db_session, two_orgs):
    r = client.post("/scraper/import", json={
        "leads": [{"place_id": "pl_nowhere", "name": "Nowhere", "phone": "+12145550004"}],
        "target_org_id": "org-does-not-exist",
    }, headers=_headers(db_session, _god(db_session)))
    assert r.status_code in (400, 404), r.text


def test_the_platform_pseudo_org_can_never_be_a_destination(
        client, db_session, two_orgs):
    """Leads landing in the platform's own org look like they vanished."""
    r = client.post("/scraper/import", json={
        "leads": [{"place_id": "pl_platform", "name": "Platform", "phone": "+12145550005"}],
        "target_org_id": "org-god-platform",
    }, headers=_headers(db_session, _god(db_session)))
    assert r.status_code in (400, 404), r.text


def test_a_non_god_cannot_use_the_scraper_at_all(client, db_session, two_orgs):
    _p, a, _b = two_orgs
    admin = User(organization_id=a.id,
                 email="ctxadmin%d@evosyspro.live" % next(_SEQ),
                 password_hash=hash_password("AdminPass123!"),
                 full_name="Org Admin", role="org_admin",
                 must_change_password=False, is_active=True)
    db_session.add(admin); db_session.commit()

    r = client.post("/scraper/import", json={
        "leads": [{"place_id": "pl_nope", "name": "Nope", "phone": "+12145550006"}],
        "target_org_id": a.id,
    }, headers=_headers(db_session, admin))
    assert r.status_code in (401, 403, 404)


def test_scraped_imports_respect_the_capacity_hold(client, db_session, two_orgs):
    """The held-lead policy must survive the relocation untouched."""
    from app.models.billing_models import BrandBillingPlan
    from app.models.models import Lead
    from app.services import lead_capacity

    p, _a, b = two_orgs
    db_session.add(BrandBillingPlan(
        platform_id=p.id, key="tiny", name="Tiny", monthly_cents=1000,
        currency="usd", is_purchasable=True, is_active=True, sort_order=10,
        max_leads=1))
    b.billing_plan_key = "tiny"
    b.plan = "tiny"
    db_session.commit()

    r = client.post("/scraper/import", json={
        "leads": [{"place_id": "pl_one", "name": "One", "phone": "+12145550011"},
                  {"place_id": "pl_two", "name": "Two", "phone": "+12145550012"}],
        "target_org_id": b.id,
    }, headers=_headers(db_session, _god(db_session)))
    assert r.status_code in (200, 201), r.text

    leads = db_session.query(Lead).filter(Lead.organization_id == b.id).all()
    assert len(leads) == 2, "a prospect was DROPPED rather than held"
    held = [l for l in leads if lead_capacity.is_held(l)]
    assert len(held) == 1, (
        "the capacity hold did not apply to a scraped import: %d held of %d"
        % (len(held), len(leads)))


# ═══════════════════════════════════════════════════════════════════════════
# APPEARANCE
# ═══════════════════════════════════════════════════════════════════════════

APPEARANCE_JS = os.path.join(FE, "appearance.js")
APPEARANCE_CSS = os.path.join(FE, "styles", "appearance.css")


def test_the_appearance_module_offers_light_dark_and_system():
    src = _read(APPEARANCE_JS)
    for token in ("export const LIGHT", "export const DARK", "export const SYSTEM"):
        assert token in src, "missing %s" % token
    assert "matchMedia" in src and "prefers-color-scheme" in src, (
        "System does not consult the operating system")


def test_system_is_resolved_to_a_concrete_appearance_in_the_dom():
    """One place decides; the DOM records what was decided."""
    src = _read(APPEARANCE_JS)
    apply_fn = src[src.index("export function applyAppearance"):]
    apply_fn = apply_fn[:apply_fn.index("\n}")]
    assert "data-appearance" in apply_fn
    assert "SYSTEM" not in apply_fn, (
        "'system' is written to the DOM as a third state, so a rendered page "
        "cannot say which appearance it actually used")


def test_the_preference_persists_and_defaults_to_dark():
    src = _read(APPEARANCE_JS)
    assert "localStorage" in src, "the choice is not remembered"
    assert "'af_appearance'" in src, (
        "the preference key does not follow the af_ convention every other "
        "client preference in this codebase uses")
    pref = src[src.index("export function getAppearancePreference"):]
    pref = pref[:pref.index("\n}")]
    assert "return DARK" in pref, (
        "the default is not DARK - a product that has shipped dark must not "
        "silently flip on every machine set to light")


def test_appearance_is_an_orthogonal_attribute_not_a_second_brand_theme():
    """data-theme is the BRAND. Reusing it would force brands x appearances."""
    css = _read(APPEARANCE_CSS)
    assert "data-appearance" in css
    assert 'data-theme="light"' not in css and 'data-theme="dark"' not in css, (
        "appearance was implemented on the brand attribute, which collapses "
        "white-labelling into the light/dark axis")


def test_the_light_appearance_defines_the_neutral_tokens():
    css = _read(APPEARANCE_CSS)
    block = css[css.index('[data-appearance="light"]'):]
    block = block[:block.index("\n}")]
    for token in ("--bg-base", "--bg-card", "--border-subtle",
                  "--text-primary", "--text-secondary"):
        assert token in block, "light appearance does not define %s" % token


def _css_no_comments(text):
    """Comments are prose, not declarations.

    Checking the raw block made this fail on the comment that EXPLAINS why the
    neon was replaced, which would have taught the next person to delete the
    explanation to get green.
    """
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def test_the_light_appearance_darkens_the_signal_colours():
    """The dark theme's neons are unreadable on white - #1ef0a8 on #fff is
    about 1.6:1. Status colour must survive the appearance change."""
    css = _read(APPEARANCE_CSS)
    block = css[css.index('[data-appearance="light"]'):]
    block = _css_no_comments(block[:block.index("\n}")])
    assert "--signal-green" in block, "light appearance does not restate the signals"
    for neon in ("#1ef0a8", "#2fb6ff", "#ff4d7e", "#ffb238"):
        assert neon not in block, (
            "the light appearance keeps the dark theme's neon %s, which is "
            "illegible on a light ground" % neon)


def test_both_appearances_set_color_scheme_for_native_controls():
    css = _read(APPEARANCE_CSS)
    assert "color-scheme: light" in css and "color-scheme: dark" in css, (
        "native form controls and scrollbars will keep rendering in the wrong "
        "appearance, which is the most obvious tell of a half-done theme")


def test_focus_is_visible_in_both_appearances():
    css = _read(APPEARANCE_CSS)
    assert "focus-visible" in css and "outline" in css


def test_appearance_is_initialised_before_react_renders():
    src = _read(os.path.join(FE, "main.jsx"))
    assert "initAppearance()" in src, "the saved appearance is never applied"
    assert src.index("import './styles/appearance.css'") > src.index("import './index.css'"), (
        "appearance.css is imported BEFORE index.css, so index.css's defaults "
        "would win and the light theme would never apply")


def test_the_appearance_control_is_reachable_from_both_shells():
    """Mike must be able to switch it without developer tools."""
    assert "AppearanceToggle" in _read(GOD_SHELL), (
        "no appearance control in the God shell")
    assert "AppearanceToggle" in _read(os.path.join(FE, "pages", "Settings.jsx")), (
        "no appearance control in customer Settings")


def test_the_control_offers_all_three_choices_as_radios():
    src = _read(os.path.join(FE, "components", "AppearanceToggle.jsx"))
    assert 'role="radiogroup"' in src and 'role="radio"' in src, (
        "the control is not exposed as a choice to assistive technology")
    assert "APPEARANCES" in src, "the control does not render all three options"
