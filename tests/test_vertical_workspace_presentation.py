"""A VERTICAL'S WORKSPACE PRESENTATION IS CONFIGURATION, AND HAS TO STAY SO.

The retail-energy workspace renames the rail, regroups it and repaints the
surface. Every one of those is a presentation decision, and each carries a way
to go wrong that no amount of looking at the screen would catch:

  * a renamed label pointing at a route that does not exist — the rail says
    "Sales Pipeline" and the click 404s,
  * a `view:` key no workspace has configured — a permanent dead entry,
  * a customer's name compiled into the shell, which is the layering mistake
    tests/test_workspace_views.py already guards on the server side,
  * a skin rule that is not scoped to the attribute, which would repaint
    EVERY customer's workspace — the one outcome the brief for this work
    ruled out explicitly.

These are text assertions against the frontend sources because this repo has
no JavaScript test runner. They are cheap and they are exact: each one names
the file it reads and fails with the offending line.
"""
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERTICAL = ROOT / "frontend" / "src" / "verticals" / "workspaceVertical.js"
LAYOUT = ROOT / "frontend" / "src" / "components" / "Layout.jsx"
APP = ROOT / "frontend" / "src" / "App.jsx"
VIEW_CONFIG_DIR = ROOT / "config" / "workspace-views"
_VERTICAL_PAGES = ROOT / "frontend" / "src" / "pages" / "vertical"
_STYLES = ROOT / "frontend" / "src" / "styles"

# EVERY CONFIGURED VERTICAL, NOT THE FIRST ONE SOMEBODY WROTE.
#
# These guards were written against the energy workspace and every one of them
# named its files directly. The second vertical then arrived and inherited
# exactly none of them — which is the failure mode a guard is supposed to
# prevent rather than demonstrate. Each entry below is
# (attribute value, the JS constant that declares it, its skin, its dashboard)
# and the parameterised tests run over all of them.
VERTICALS = {
    "energy": {
        "declaration": "const ENERGY =",
        "skin": _STYLES / "vertical-energy.css",
        "overview": _VERTICAL_PAGES / "EnergyOverview.jsx",
        "overview_css": _VERTICAL_PAGES / "EnergyOverview.css",
    },
    "cleaning": {
        "declaration": "const CLEANING =",
        "skin": _STYLES / "vertical-cleaning.css",
        "overview": _VERTICAL_PAGES / "CleaningOverview.jsx",
        "overview_css": _VERTICAL_PAGES / "CleaningOverview.css",
    },
}

SKINS = [v["skin"] for v in VERTICALS.values()]

PRESENTATION_FILES = tuple(
    [VERTICAL]
    + [v["skin"] for v in VERTICALS.values()]
    + [v["overview"] for v in VERTICALS.values()]
    + [v["overview_css"] for v in VERTICALS.values()]
)


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _block(key: str) -> str:
    """The source of one vertical's declaration, from its const to the next."""
    body = _text(VERTICAL)
    start = body.index(VERTICALS[key]["declaration"])
    rest = body[start + 1:]
    ends = [rest.index(m) for m in ("\nconst ", "\nexport ") if m in rest]
    return rest[:min(ends)] if ends else rest


# ── the shell is code; the customer is a row ────────────────────────────────

@pytest.mark.parametrize("path", PRESENTATION_FILES, ids=lambda p: p.name)
def test_no_presentation_file_names_a_customer(path):
    """The same rule test_workspace_views.py applies to the server modules.

    The rail's wordmark is built from the branding row the server returns, so
    a customer name appearing in any of these files means somebody stopped
    reading the row and started hard-coding the customer.
    """
    forbidden = ("atlantis", "commercial cleaning blueprint", "brightpath",
                 "almaguer")
    body = _text(path).lower()
    for name in forbidden:
        assert name not in body, "%s names %r" % (path.name, name)


# ── every renamed door opens onto a route that exists ───────────────────────

def _routes(key):
    """The `to:` targets of one vertical's rail, read out of the module."""
    return re.findall(r"\{\s*to:\s*'([^']+)'", _block(key))


REQUIRED_ROUTES = {
    # The energy rail is the whole back office, so most of it is required.
    "energy": ("/", "/leads", "/pipeline", "/replies", "/workqueue",
               "/reports", "/users", "/launch"),
    # The cleaning rail is six entries by design. Three of them are configured
    # screens, which are asserted separately; these are the routes.
    "cleaning": ("/", "/activity", "/reports"),
}


@pytest.mark.parametrize("key", sorted(VERTICALS))
def test_the_vertical_rail_names_at_least_the_approved_screens(key):
    """A guard against a rail being quietly emptied, not a design review."""
    routes = _routes(key)
    assert routes, "the %s rail declares no routes at all" % key
    for required in REQUIRED_ROUTES[key]:
        assert required in routes, "the %s rail no longer opens %s" % (key, required)


@pytest.mark.parametrize("key", sorted(VERTICALS))
def test_every_vertical_route_is_a_route_the_app_actually_declares(key):
    """THE FAILURE THIS CATCHES. Renaming "Leads" to "Leads & Customers" is
    presentation; pointing it somewhere that does not exist is a 404 with a
    friendly label on it. Every target below is matched against App.jsx's own
    <Route path="…"> declarations."""
    declared = set(re.findall(r'<Route\s+path="([^"]+)"', _text(APP)))
    assert declared, "App.jsx declared no routes — this test is reading the wrong file"
    for route in _routes(key):
        if route == "/":
            continue  # the index route is declared as path="/" or index
        assert route in declared, (
            "the %s rail opens %s, which App.jsx does not declare" % (key, route))


# ── every configured screen it names is one a workspace can have ────────────

def _view_keys(key):
    return re.findall(r"\{\s*view:\s*'([^']+)'", _block(key))


def _shipped_view_keys():
    """Every screen key a workspace can actually end up with.

    TWO SOURCES, BECAUSE THERE ARE TWO LAYERS. A customer's own
    `workspace_views` column (the JSON files here) and its industry's default
    (`industry_templates`) are both real ways a screen arrives, and reading
    only the first is how a rail whose screens come from the template — which
    is the preferred layer — would look like a rail naming screens nobody has.
    """
    from app.services import industry_templates

    shipped = set()
    for path in VIEW_CONFIG_DIR.glob("*.json"):
        for view in json.loads(_text(path)):
            shipped.add(view["key"])
    for template in industry_templates.TEMPLATES.values():
        for view in (template.get("workspace_views") or []):
            shipped.add(view["key"])
    return shipped


@pytest.mark.parametrize("key", sorted(VERTICALS))
def test_the_view_keys_the_rail_names_exist_in_a_shipped_configuration(key):
    """A `view:` the rail names and no configuration provides is an entry that
    can never render. It is dropped at runtime rather than drawn dead, which
    is right — and silent, which is why this says so at build time instead."""
    keys = set(_view_keys(key))
    assert keys, "the %s rail names no configured screens at all" % key
    missing = keys - _shipped_view_keys()
    assert not missing, (
        "the %s rail names %s, which no shipped configuration provides"
        % (key, sorted(missing)))


def test_a_verticals_own_industry_template_supplies_the_screens_it_names():
    """The cleaning rail's three screens come from the `cleaning` template.

    Stated separately from the test above because "some configuration
    somewhere provides this key" is a weaker claim than the one that matters:
    a cleaning company with NO configuration of its own still gets all three,
    which is what makes the workspace work on the day it is created.
    """
    from app.services import industry_templates

    supplied = {v["key"] for v in industry_templates.workspace_views("cleaning")}
    assert set(_view_keys("cleaning")) <= supplied, (
        "the cleaning rail names screens its own industry template does not "
        "supply, so a new cleaning customer would open a workspace missing them")


# ── the skin repaints ONE workspace, never the platform ─────────────────────

def _selectors(path):
    # Strip comments so prose describing a selector cannot fail the test.
    body = re.sub(r"/\*.*?\*/", "", _text(path), flags=re.S)
    out = []
    for block in re.finditer(r"([^{}]+)\{", body):
        chunk = block.group(1).strip()
        if not chunk or chunk.startswith("@"):
            continue
        out.extend(s.strip() for s in chunk.split(",") if s.strip())
    return out


@pytest.mark.parametrize("path", SKINS, ids=lambda p: p.name)
def test_every_skin_rule_is_scoped_to_the_vertical_attribute(path):
    """THE OUTCOME THIS WORK WAS TOLD NOT TO PRODUCE.

    One unscoped selector in a skin — `:root { --bg-base: … }`, `.sidebar
    { … }` — repaints every customer's workspace on the platform, and it
    would look correct in the one workspace anybody was testing. So each rule
    has to carry the attribute that limits it.
    """
    selectors = _selectors(path)
    assert selectors, "no selectors found — this test is reading the wrong file"
    for selector in selectors:
        assert '[data-workspace-vertical=' in selector, (
            "%s: %r is not scoped to the vertical attribute, so it would "
            "repaint every workspace on the platform" % (path.name, selector))


@pytest.mark.parametrize("path", SKINS, ids=lambda p: p.name)
def test_the_skin_outranks_the_appearance_layer(path):
    """MEASURED, BECAUSE GUESSING IT COST A DEPLOY.

    `:root[data-appearance="dark"]` in styles/appearance.css redefines the
    same neutral tokens a skin does, at exactly (0,2,0) — which is what the
    first skin was written at. Equal specificity falls to source order, the
    appearance layer happened to come last, and production rendered the
    platform's dark palette with the vertical's rail on top of it.

    The leading element selector makes every skin rule (0,2,1). This asserts
    it stays there, because the symptom of losing it is a workspace that
    looks almost right.
    """
    for selector in _selectors(path):
        assert selector.startswith('html:root[data-workspace-vertical='), (
            "%s: %r drops the element prefix, so it ties with "
            ':root[data-appearance="dark"] and the cascade decides by bundle '
            "order" % (path.name, selector))

    appearance = _text(ROOT / "frontend" / "src" / "styles" / "appearance.css")
    assert ':root[data-appearance="dark"] {' in appearance, (
        "appearance.css no longer defines the block this specificity was "
        "measured against — re-measure before trusting the prefix")


@pytest.mark.parametrize("key", sorted(VERTICALS))
def test_a_skin_paints_only_its_own_vertical(key):
    """One skin, one attribute value.

    Two skins are loaded into the same bundle for every user on the platform,
    so a selector in one that names the other's value — a copy-paste when the
    second was written from the first — would let an energy workspace pick up
    a cleaning rule, or the reverse.
    """
    others = [k for k in VERTICALS if k != key]
    for selector in _selectors(VERTICALS[key]["skin"]):
        assert ('[data-workspace-vertical="%s"]' % key) in selector, (
            "%r does not name its own vertical" % selector)
        for other in others:
            assert ('[data-workspace-vertical="%s"]' % other) not in selector, (
                "the %s skin has a selector scoped to %s: %r" % (key, other, selector))


@pytest.mark.parametrize("key", sorted(VERTICALS))
def test_every_skin_is_actually_loaded_by_the_shell(key):
    """A skin nobody imports is a file, not a presentation. The attribute
    would be set on <html> and nothing would answer it."""
    body = _text(LAYOUT)
    assert "styles/vertical-%s.css" % key in body, (
        "Layout.jsx does not import the %s skin, so setting the attribute "
        "paints nothing" % key)


def test_a_workspaces_vocabulary_cache_is_keyed_on_the_organization():
    """THE BUG THIS CAUGHT, FOUND ON A LIVE CLIENT DASHBOARD.

    `af_terminology` was keyed on `getWorkspaceContext()` — the
    `X-Workspace-Id` header a member sets when switching between their own
    workspaces. That header is NULL for an operator who enters a customer
    through God Mode, because that context lives on the server. So every
    customer entered that way shared one cache key, "default", and the second
    one inherited the first one's vocabulary: a newly created cleaning
    company's dashboard opened headed with another customer's company name.

    The key has to be the organization the server actually resolved, whichever
    way the reader got there.
    """
    body = _text(ROOT / "frontend" / "src" / "terminology.js")
    assert "getBranding" in body, \
        "terminology.js no longer reads the resolved organization"
    assert "organization_id" in body, \
        "the cache key does not mention the organization it is keyed on"


def test_the_vocabulary_cache_is_dropped_wherever_the_branding_cache_is():
    """`clearTerminology` existed and was called from NOWHERE.

    The two caches answer the same question — which customer is this — so one
    surviving a switch the other did not is a screen showing two customers at
    once. Every site that drops the branding cache has to drop this one too.
    """
    for relative in ("frontend/src/components/Layout.jsx",
                     "frontend/src/pages/god/enterCustomer.js"):
        body = _text(ROOT / relative)
        branding = body.count("clearBranding()")
        vocabulary = body.count("clearTerminology()")
        assert branding > 0, "%s no longer clears branding at all" % relative
        assert vocabulary >= branding, (
            "%s drops the branding cache %d time(s) and the vocabulary cache "
            "%d — the two describe the same workspace"
            % (relative, branding, vocabulary))


def test_a_vertical_dashboard_does_not_call_a_loading_screen_misconfigured():
    """A card whose payload has not arrived is not a card whose screen is
    missing. Saying so tells a client their account is broken for as long as
    the request takes, every time they open the page."""
    for key in VERTICALS:
        body = _text(VERTICALS[key]["overview"])
        assert "const missing = (payload, sentence) =>" in body, (
            "%s asserts 'not configured' without distinguishing 'not loaded "
            "yet'" % VERTICALS[key]["overview"].name)


def test_the_skin_is_applied_and_removed_by_the_shell():
    """Set on entry and removed on exit. A skin left behind after switching
    workspace paints one customer's rail in another's colours, which is the
    same class of defect as a stale capability list."""
    body = _text(LAYOUT)
    assert "setAttribute('data-workspace-vertical'" in body
    assert "removeAttribute('data-workspace-vertical')" in body


# ── a workspace without a vertical is untouched ─────────────────────────────

def test_the_platform_rail_is_still_what_everyone_else_gets():
    """`navGroupsFor` returns null for an industry with no presentation, and
    the shell falls through to NAV_GROUPS. If that fallback ever goes, every
    other customer loses their navigation."""
    layout = _text(LAYOUT)
    assert "const verticalGroups = navGroupsFor(vertical, configuredViews)" in layout
    assert "const groups = NAV_GROUPS.map(" in layout

    vertical = _text(VERTICAL)
    assert "if (!industry) return null" in vertical
    assert "if (!vertical) return null" in vertical


def test_the_platform_overview_is_still_rendered_for_everyone_else():
    """The dashboard switch is one line per vertical and falls through by
    default — the fallthrough being the line that matters, because losing it
    is how every other customer on the platform loses their dashboard."""
    body = _text(ROOT / "frontend" / "src" / "pages" / "Overview.jsx")
    assert "return <PlatformOverview />" in body
    assert "verticalFor(branding)" in body


@pytest.mark.parametrize("key", sorted(VERTICALS))
def test_every_vertical_dashboard_is_reachable_from_the_overview_switch(key):
    """A dashboard nobody routes to is a file. Each vertical's component has
    to be imported AND named in the switch, or its workspace silently gets the
    platform screen the whole exercise was to replace."""
    body = _text(ROOT / "frontend" / "src" / "pages" / "Overview.jsx")
    component = VERTICALS[key]["overview"].stem
    assert "import %s from './vertical/%s'" % (component, component) in body, \
        "Overview.jsx does not import %s" % component
    assert "<%s />" % component in body, \
        "Overview.jsx never renders %s" % component


# ── the primary rail is the approved set, and only that ─────────────────────

APPROVED_RAIL = {
    "energy": {
        "Operate": ["Overview", "Leads & Customers", "Rate Requests",
                    "Sales Pipeline", "Move Concierge"],
        "Work": ["Communications", "Tasks & Follow-Up", "Renewals", "Reports"],
        "System": ["Integrations", "Team & Access", "Launch Center"],
    },
    # SIX ENTRIES, ONE GROUP. This is the approved client-portal navigation
    # exactly: the workspace has a lead importer, a reply inbox, a work queue,
    # a pipeline board and a connector page all switched on, and none of them
    # is in the rail, because none of them was in the design.
    "cleaning": {
        "Your Account": ["Dashboard", "Prospects", "VA Activity", "Follow-Up",
                         "Walkthroughs", "Reports"],
    },
}


def _rail(key):
    """Group -> labels, in declaration order, read out of the module."""
    groups, current = {}, None
    for line in _block(key).splitlines():
        group = re.search(r"label:\s*'([^']+)',\s*$", line)
        item = re.search(r"\{\s*(?:to|view):\s*'[^']+',\s*label:\s*'([^']+)'", line)
        if item and current:
            groups[current].append(item.group(1))
        elif group:
            current = group.group(1)
            groups.setdefault(current, [])
    return groups


@pytest.mark.parametrize("key", sorted(VERTICALS))
def test_the_primary_rail_is_exactly_the_approved_navigation(key):
    """THE FAILURE THIS CATCHES, WHICH ALREADY HAPPENED ONCE.

    The first version appended every configured screen the design did not
    name, so switching a capability on grew a top-level nav entry and the
    agreed rail quietly stopped being the agreed rail — `Consultations`
    appeared under Operate without anybody deciding it should.

    A configured screen that is not in the design is still reachable at its
    own /view/<key> route. It simply does not claim a place in the customer's
    main navigation by existing.
    """
    assert _rail(key) == APPROVED_RAIL[key]


def test_nothing_appends_unnamed_views_to_the_rail():
    """The mechanism, not just today's output: no overflow bucket."""
    body = _text(VERTICAL)
    assert "overflowGroup" not in body
    assert "overflow.items.push" not in body


# ── one admin control, and none of the five strips it replaced ──────────────

def test_a_vertical_workspace_renders_one_admin_control_not_five():
    """Inside a configured vertical, the platform's chrome collapses into
    WorkspaceAdminMenu: the amber god strip, the Back Office button, the
    Command Center rail item and the org picker are all conditioned off."""
    body = _text(LAYOUT)
    assert "{!vertical && <GodReturnBar" in body, \
        "the god strip still renders inside a customer's vertical workspace"
    assert "vertical ? <WorkspaceAdminMenu /> : <ContextSwitcher" in body, \
        "the admin control and the Back Office button are not exclusive"
    assert "{isGodAdmin && !vertical && (" in body, \
        "Command Center and the org picker still sit in the customer's rail"


def test_the_admin_control_is_invisible_to_a_customers_own_staff():
    """It is an operator affordance. A customer's user must not see a door
    that refuses them — and must not learn there is anything above their own
    company at all."""
    body = _text(ROOT / "frontend" / "src" / "components" / "WorkspaceAdminMenu.jsx")
    assert "if (!isGod && !isSuper && !hasBackOffice) return null" in body
    assert "if (items.length === 0) return null" in body
    # Every action it offers is one the platform already guarded elsewhere.
    for action in ("/god", "/god/workspaces", "/god/platform/context/exit", "/sales"):
        assert action in body, "the admin control lost %s" % action


def test_the_hierarchy_strip_is_not_printed_over_a_customers_own_product():
    """"AdvisorFlow -> EvoSys Pro -> <customer>" is the white-label chain.
    True, internal, and not part of what the customer bought."""
    body = _text(ROOT / "frontend" / "src" / "components" / "ContextBanner.jsx")
    assert "if (inVerticalWorkspace) return null" in body
    assert "verticalFor(getBranding())" in body
