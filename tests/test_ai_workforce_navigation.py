"""
═══════════════════════════════════════════════════════════════════════════
T6 — THE SCREENS EXIST, AND THEY ARE REACHABLE
═══════════════════════════════════════════════════════════════════════════

THE FAILURE THIS DEFENDS AGAINST IS AN ABSENCE, and absences are invisible to
every test that exercises what is there. `tests/test_god_navigation.py` exists
because FOUR call sites navigated to `/god/customer-app` and that route was
never registered, so all four silently rendered the God Command Center. The
same shape of mistake here would be worse, not better: a God screen that
silently renders the Command Center during a dark launch is a screen where
somebody goes to check whether any AI employee can reach a person, sees a
different page, and concludes the engine is not installed.

So these assertions read the frontend as TEXT. That is deliberate. A Python
test can prove `/god/workforce/overview` answers; only reading App.jsx can
prove anything renders when a human clicks the link.

WHAT IS ASSERTED

  1. All three screens are registered routes: `/ai-team`, `/ai-team/:id` and
     `/god/workforce`.
  2. `/god/workforce` is registered BEFORE the `/god/*` catch-all. Order is
     the whole bug: after it, the catch-all wins.
  3. Every component the routes name is actually imported, and every page
     file exists on disk.
  4. Both nav surfaces point at registered routes — the God rail entry and
     the tenant "Your AI Team" entry.
  5. The customer route carries NO organization id. The workspace is resolved
     server-side on every request, and a customer id in a URL is a bookmark
     that can outlive the context it was created in.
  6. The God screen does not reference a CSS class GodStyles never defines.
     A class name that resolves to nothing renders as unstyled text, which
     looks exactly like a broken page and is invisible to every other test.
"""

import os
import re

FE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "frontend", "src")
APP_JSX = os.path.join(FE, "App.jsx")
GOD_SHELL = os.path.join(FE, "pages", "GodShell.jsx")
LAYOUT = os.path.join(FE, "components", "Layout.jsx")
GOD_STYLES = os.path.join(FE, "pages", "god", "GodStyles.jsx")
GOD_WORKFORCE = os.path.join(FE, "pages", "god", "GodWorkforce.jsx")

PAGES = {
    "AITeam": os.path.join(FE, "pages", "AITeam.jsx"),
    "AIEmployeeDetail": os.path.join(FE, "pages", "AIEmployeeDetail.jsx"),
    "GodWorkforce": GOD_WORKFORCE,
}


def _read(path):
    return open(path, encoding="utf-8").read()


def _registered_routes(src):
    return set(re.findall(r'<Route\s+path=["\']([^"\']+)["\']', src))


# ═══════════════════════════════════════════════════════════════════════════
# THE ROUTES
# ═══════════════════════════════════════════════════════════════════════════

def test_all_three_workforce_screens_are_registered_routes():
    routes = _registered_routes(_read(APP_JSX))
    for path in ("/ai-team", "/ai-team/:employeeId", "/god/workforce"):
        assert path in routes, (
            "%s is not a registered route. The page file exists and the "
            "endpoints answer, so nothing else in the suite would fail — the "
            "link would simply render something else." % path)


def test_the_god_workforce_route_is_registered_before_the_god_catch_all():
    src = _read(APP_JSX)
    specific = src.index('path="/god/workforce"')
    catch_all = src.index('path="/god/*"')
    assert specific < catch_all, (
        "/god/workforce is registered AFTER /god/*, so the catch-all wins and "
        "the God Command Center renders instead. During a dark launch that is "
        "a screen which answers 'can any AI employee reach a person right "
        "now' with somebody else's dashboard.")


def test_the_employee_detail_route_is_registered_after_the_team_list():
    """`/ai-team` must not be read as an employee id."""
    src = _read(APP_JSX)
    assert src.index('path="/ai-team"') < src.index('path="/ai-team/:employeeId"')


def test_no_workforce_route_carries_an_organization_id():
    """The workspace is resolved server-side, never from the URL.

    workforce_router.py takes no organization id on any route; it reads the
    active workspace from the caller's own context. A customer id in the path
    would be a bookmark that outlives the context that created it, and two
    sources of truth for whose team is on screen.
    """
    for path in _registered_routes(_read(APP_JSX)):
        if path.startswith("/ai-team"):
            assert "orgId" not in path and "organization" not in path, path


# ═══════════════════════════════════════════════════════════════════════════
# THE COMPONENTS BEHIND THEM
# ═══════════════════════════════════════════════════════════════════════════

def test_every_routed_workforce_component_is_imported_and_exists():
    src = _read(APP_JSX)
    for name, path in PAGES.items():
        assert re.search(r"^import\s+%s\s+from\s+" % name, src, re.M), (
            "%s is used in a route but never imported into App.jsx." % name)
        assert os.path.exists(path), "%s is imported but %s is missing." % (
            name, path)
        assert "export default" in _read(path), (
            "%s has no default export, so the import resolves to undefined "
            "and the route renders nothing." % name)


# ═══════════════════════════════════════════════════════════════════════════
# THE TWO NAV SURFACES
# ═══════════════════════════════════════════════════════════════════════════

def test_the_god_rail_links_to_a_registered_workforce_route():
    rail = _read(GOD_SHELL)
    assert "'/god/workforce'" in rail, (
        "Nothing in the God rail reaches AI Workforce. A built, routed, "
        "deployed screen nobody can find is the same as a missing one.")
    assert "/god/workforce" in _registered_routes(_read(APP_JSX))


def test_the_god_rail_icon_key_exists_in_the_icon_table():
    """ICONS[icon] has no fallback — a missing key draws nothing."""
    rail = _read(GOD_SHELL)
    entry = re.search(r"path:\s*'/god/workforce',\s*icon:\s*'([a-zA-Z]+)'", rail)
    assert entry, "The AI Workforce rail entry has no icon."
    icons = set(re.findall(r"^\s{2}([a-zA-Z]+):\s*'M", rail, re.M))
    assert entry.group(1) in icons, (
        "The rail asks for icon '%s', which ICONS does not define. The call "
        "sites index ICONS directly with no fallback, so the row renders with "
        "an empty glyph." % entry.group(1))


def test_the_tenant_nav_links_to_the_ai_team_and_does_not_hide_it_behind_a_flag():
    """A hidden link says the product does not exist, not that it is off.

    `/workforce/team` is require_tenant_user and answers honestly for a
    customer who has hired nobody. Whether an AI employee may actually ACT is
    decided at execution time by activation plus entitlement on the server —
    never by whether a nav row was drawn.
    """
    nav = _read(LAYOUT)
    row = re.search(r"\{[^{}]*to:\s*'/ai-team'[^{}]*\}", nav)
    assert row, "Layout.jsx has no /ai-team nav entry."
    assert "featureKey" not in row.group(0), (
        "The AI Team nav row asks isFeatureEnabled() for a key. No workforce "
        "template declares a feature key, and asking for a key the server has "
        "never heard of is the mistake documented in entitlements.py.")
    assert "/ai-team" in _registered_routes(_read(APP_JSX))


# ═══════════════════════════════════════════════════════════════════════════
# THE STYLING THAT SILENTLY DOES NOTHING
# ═══════════════════════════════════════════════════════════════════════════

def test_the_god_screen_uses_no_class_god_styles_does_not_define():
    """A class name that resolves to nothing looks exactly like a broken page.

    GodWorkforce.jsx originally reached for `gm-sec`, which GodStyles has
    never defined, in thirteen places. Nothing failed; the section labels just
    rendered as unstyled body text.
    """
    styles = _read(GOD_STYLES)
    defined = set(re.findall(r"\.(gm-[a-z0-9-]+)", styles))
    used = set(re.findall(r'className="([^"]*gm-[^"]*)"', _read(GOD_WORKFORCE)))
    asked = {c for group in used for c in group.split() if c.startswith("gm-")}
    missing = sorted(asked - defined)
    assert not missing, (
        "GodWorkforce.jsx uses %s, which GodStyles.jsx does not define."
        % ", ".join(missing))
