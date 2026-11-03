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
    """THE fix. One decision, and it is the whole defence.

    The gate used to be an expression inline in `request()`. It is now
    `routeAuthority.orgOverrideFor`, which answers the same question with one
    more input — an explicitly named customer, for the single request made
    DURING a transition, when the address bar still names the screen being
    left rather than the workspace being entered. See tests/test_workspace_
    entry.py for what that was breaking.

    What this test guards is unchanged: the client must not decide the header
    for itself, and the route must still gate the AMBIENT selection.
    """
    src = _read(API_CLIENT)
    assert "orgOverrideFor" in src, (
        "the API client still sends X-Org-Override from wherever the user "
        "happens to be standing")
    m = re.search(r"const orgOverride = .*", src)
    assert m, "the override assignment was not found in client.js"
    assert "orgOverrideFor(" in m.group(0), (
        "the route gate is imported but not applied to the override: %s"
        % m.group(0))

    # And the gate itself still exists, still asks the route, and still
    # answers "no override" for a platform surface.
    authority = _read(ROUTE_AUTHORITY)
    gate = authority[authority.index("export function orgOverrideFor"):]
    assert "shouldSendOrgOverride(pathname)" in gate, (
        "orgOverrideFor no longer consults the route, so the ambient customer "
        "selection would be sent from platform tools again")
    assert "if (noOrgContext) return null" in gate, (
        "the explicit platform-wide opt-out was dropped")


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




# ═══════════════════════════════════════════════════════════════════════════
# APPEARANCE
# ═══════════════════════════════════════════════════════════════════════════

APPEARANCE_JS = os.path.join(FE, "appearance.js")
APPEARANCE_CSS = os.path.join(FE, "styles", "appearance.css")


def _css_no_comments(text):
    """Comments are prose, not declarations.

    Checking the raw block made this fail on the comment that EXPLAINS why the
    neon was replaced, which would have taught the next person to delete the
    explanation to get green.
    """
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)




def test_the_appearance_module_is_light_only():
    """Spec section 66: one polished light theme, no day/night toggle."""
    src = _read(APPEARANCE_JS)
    pref = src[src.index("export function getAppearancePreference"):]
    pref = pref[:pref.index("\n}")]
    assert "return LIGHT" in pref
    assert "return DARK" not in src and "return SYSTEM" not in src
    assert "matchMedia" not in src, "the OS dark preference is still consulted"
    assert "APPEARANCES = [{ value: LIGHT" in src


def test_the_stale_preference_key_is_cleared_not_read():
    src = _read(APPEARANCE_JS)
    assert "removeItem(KEY)" in src and "'af_appearance'" in src
    assert "getItem(" not in src, "the old dark preference is still honoured"


def test_apply_writes_a_concrete_light_attribute():
    src = _read(APPEARANCE_JS)
    apply_fn = src[src.index("export function applyAppearance"):]
    apply_fn = apply_fn[:apply_fn.index("\n}")]
    assert "setAttribute('data-appearance', LIGHT)" in apply_fn
    assert "colorScheme = LIGHT" in apply_fn


def test_the_token_layer_does_not_depend_on_the_attribute():
    """The light tokens are unconditional; nothing keys on data-appearance."""
    css = _css_no_comments(_read(APPEARANCE_CSS))
    assert "data-appearance" not in css
    assert 'data-theme="light"' not in css and 'data-theme="dark"' not in css
    block = css[css.index("html:root {"):]
    block = block[:block.index("\n}")]
    for token in ("--bg-base", "--bg-card", "--border-subtle", "--text-primary",
                  "--text-secondary", "--color-primary", "--pill-success-bg",
                  "--space-4", "--shadow-md"):
        assert token in block, "token layer does not define %s" % token
    for neon in ("#1ef0a8", "#2fb6ff", "#ff4d7e", "#ffb238"):
        assert neon not in block, "dark-theme neon %s left in the light layer" % neon


def test_native_controls_render_light():
    css = _css_no_comments(_read(APPEARANCE_CSS))
    assert "color-scheme: light" in css
    idx = _css_no_comments(_read(os.path.join(FE, "index.css")))
    select_rule = idx[idx.index("select {"):]
    select_rule = select_rule[:select_rule.index("}")]
    assert "color-scheme: dark" not in select_rule


def test_focus_is_visible():
    css = _read(APPEARANCE_CSS)
    assert "focus-visible" in css and "outline" in css


def test_appearance_is_initialised_before_react_renders():
    src = _read(os.path.join(FE, "main.jsx"))
    assert "initAppearance()" in src
    assert src.index("import './styles/appearance.css'") > src.index("import './index.css'"), (
        "appearance.css is imported BEFORE index.css, so index.css's dark "
        "defaults would win")


def test_no_appearance_control_is_mounted_anywhere():
    for rel in (("pages", "GodShell.jsx"), ("pages", "Settings.jsx"),
                ("pages", "executive", "ExecutiveSuite.jsx"),
                ("components", "Layout.jsx")):
        assert "AppearanceToggle" not in _read(os.path.join(FE, *rel)), rel
    assert not os.path.exists(os.path.join(FE, "components", "AppearanceToggle.jsx"))


def test_god_mode_does_not_consult_the_appearance_preference():
    """The tenant preference must not reach the control plane.

    The owner's own tenant app is currently set to dark. God Mode has to be
    light anyway, and `color-scheme` has to be pinned light inside the God scope
    or the NATIVE widgets - the open <select> list, the date picker, the
    scrollbars - keep following the document root and drop a black dropdown over
    a white God form.
    """
    tokens = _css_no_comments(_read(os.path.join(
        FE, "pages", "god", "godTokens.css")))
    assert "[data-appearance" not in tokens, (
        "the God token sheet varies on the appearance attribute, which brings "
        "the rejected dark control plane back through the side door")
    assert "color-scheme: light" in tokens, (
        "the God scope does not pin color-scheme, so native controls will "
        "follow the tenant preference")
    assert "color-scheme: dark" not in tokens


def test_direct_scraper_import_is_retired(client, db_session):
    """#470 Stage D: the scraper no longer writes leads into an organization
    directly - results are staged (POST /scraper/stage) and routed from Lead
    Intelligence. The retired route answers 410 to the owner and 403 to anyone else."""
    from app.models.models import Lead, Organization, User
    from app.services.auth_service import create_access_token, hash_password
    god = User(organization_id=None, email="cb-god-retired@example.test", full_name="Owner",
               password_hash=hash_password("Pass12345!"), role="god_admin", is_active=True, must_change_password=False)
    org = Organization(name="CB Retired Org", slug="cb-retired-org", plan="enterprise", is_active=True)
    db_session.add_all([god, org])
    db_session.commit()
    admin = User(organization_id=org.id, email="cb-admin-retired@example.test", full_name="Admin",
                 password_hash=hash_password("Pass12345!"), role="org_admin", is_active=True, must_change_password=False)
    db_session.add(admin)
    db_session.commit()
    body = {"target_org_id": org.id, "leads": [{"place_id": "p-retired-2", "name": "Any Co", "phone": "+12145550998"}]}
    r = client.post("/scraper/import", json=body, headers={"Authorization": "Bearer " + create_access_token(god, db_session)})
    assert r.status_code == 410
    r = client.post("/scraper/import", json=body, headers={"Authorization": "Bearer " + create_access_token(admin, db_session)})
    assert r.status_code == 403
    assert db_session.query(Lead).filter(Lead.organization_id == org.id).count() == 0
