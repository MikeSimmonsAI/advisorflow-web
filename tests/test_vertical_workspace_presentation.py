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
SKIN = ROOT / "frontend" / "src" / "styles" / "vertical-energy.css"
OVERVIEW = ROOT / "frontend" / "src" / "pages" / "vertical" / "EnergyOverview.jsx"
LAYOUT = ROOT / "frontend" / "src" / "components" / "Layout.jsx"
APP = ROOT / "frontend" / "src" / "App.jsx"
VIEW_CONFIG_DIR = ROOT / "config" / "workspace-views"

PRESENTATION_FILES = (VERTICAL, SKIN, OVERVIEW,
                      ROOT / "frontend" / "src" / "pages" / "vertical" / "EnergyOverview.css")


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


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

def _energy_routes():
    """The `to:` targets of the energy rail, read out of the module itself."""
    body = _text(VERTICAL)
    start = body.index("const ENERGY =")
    end = body.index("const BY_INDUSTRY")
    return re.findall(r"\{\s*to:\s*'([^']+)'", body[start:end])


def test_the_vertical_rail_names_at_least_the_approved_screens():
    """A guard against the rail being quietly emptied, not a design review."""
    routes = _energy_routes()
    assert len(routes) >= 8, routes
    for required in ("/", "/leads", "/pipeline", "/replies", "/workqueue",
                     "/reports", "/users", "/launch"):
        assert required in routes, "the energy rail no longer opens %s" % required


def test_every_vertical_route_is_a_route_the_app_actually_declares():
    """THE FAILURE THIS CATCHES. Renaming "Leads" to "Leads & Customers" is
    presentation; pointing it somewhere that does not exist is a 404 with a
    friendly label on it. Every target below is matched against App.jsx's own
    <Route path="…"> declarations."""
    declared = set(re.findall(r'<Route\s+path="([^"]+)"', _text(APP)))
    assert declared, "App.jsx declared no routes — this test is reading the wrong file"
    for route in _energy_routes():
        if route == "/":
            continue  # the index route is declared as path="/" or index
        assert route in declared, "the energy rail opens %s, which App.jsx does not declare" % route


# ── every configured screen it names is one a workspace can have ────────────

def _energy_view_keys():
    body = _text(VERTICAL)
    start = body.index("const ENERGY =")
    end = body.index("const BY_INDUSTRY")
    return re.findall(r"\{\s*view:\s*'([^']+)'", body[start:end])


def test_the_view_keys_the_rail_names_exist_in_a_shipped_configuration():
    """A `view:` the rail names and no configuration provides is an entry that
    can never render. It is dropped at runtime rather than drawn dead, which
    is right — and silent, which is why this says so at build time instead."""
    keys = set(_energy_view_keys())
    assert keys, "the energy rail names no configured screens at all"
    shipped = set()
    for path in VIEW_CONFIG_DIR.glob("*.json"):
        for view in json.loads(_text(path)):
            shipped.add(view["key"])
    missing = keys - shipped
    assert not missing, "the rail names %s, which no shipped configuration provides" % sorted(missing)


# ── the skin repaints ONE workspace, never the platform ─────────────────────

def test_every_skin_rule_is_scoped_to_the_vertical_attribute():
    """THE OUTCOME THIS WORK WAS TOLD NOT TO PRODUCE.

    One unscoped selector in this file — `:root { --bg-base: … }`, `.sidebar
    { … }` — repaints every customer's workspace on the platform, and it
    would look correct in the one workspace anybody was testing. So each rule
    has to carry the attribute that limits it.
    """
    body = _text(SKIN)
    # Strip comments so prose describing a selector cannot fail the test.
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    selectors = []
    for block in re.finditer(r"([^{}]+)\{", body):
        chunk = block.group(1).strip()
        if not chunk or chunk.startswith("@"):
            continue
        selectors.extend(s.strip() for s in chunk.split(",") if s.strip())
    assert selectors, "no selectors found — this test is reading the wrong file"
    for selector in selectors:
        assert '[data-workspace-vertical=' in selector, (
            "%r is not scoped to the vertical attribute, so it would repaint "
            "every workspace on the platform" % selector)


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
    """The dashboard switch is one line and falls through by default."""
    body = _text(ROOT / "frontend" / "src" / "pages" / "Overview.jsx")
    assert "return <PlatformOverview />" in body
    assert "verticalFor(branding)" in body
