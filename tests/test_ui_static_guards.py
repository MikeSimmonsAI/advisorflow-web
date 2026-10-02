"""STATIC GUARDS FOR UI DEFECTS FOUND IN THE 2026-10-02 SWEEPS.

Each test pins one defect that shipped to production and was found by an
axe / browser sweep, so it cannot quietly come back. They read source files;
no browser is needed.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "frontend", "src")


def _read(*parts):
    with open(os.path.join(SRC, *parts), encoding="utf-8") as f:
        return f.read()


def _jsx_files():
    for base, _dirs, files in os.walk(SRC):
        for name in files:
            if name.endswith(".jsx"):
                yield os.path.join(base, name)


_PAIR = re.compile(r"background:\s*([^,\n]+?),\s*\n?\s*color:\s*([^,\n]+?),")
_COLOUR = re.compile(r"'var\(--[\w-]+\)'|'#[0-9a-fA-F]{3,8}'")


def test_no_button_text_the_same_colour_as_its_background():
    """Owner-console buttons rendered blue text on a blue button and teal on
    teal (Billing ops Apply/Resync, Stripe Sync, Brand catalogue Create).
    A style whose background and text start with the same colour is invisible
    text."""
    bad = []
    for path in _jsx_files():
        src = open(path, encoding="utf-8").read()
        for m in _PAIR.finditer(src):
            if "+ '" in m.group(1) or "+'" in m.group(1) or "`" in m.group(1):
                continue          # colour + alpha suffix: a tint, not the same colour
            b = _COLOUR.findall(m.group(1))
            c = _COLOUR.findall(m.group(2))
            if b and c and b[0] == c[0]:
                bad.append("%s:%d" % (os.path.relpath(path, ROOT), src[:m.start()].count("\n") + 1))
    assert not bad, "text the same colour as its background: %s" % bad


def test_billing_support_address_comes_from_the_workspace_brand():
    """On the shared app host the hostname named the wrong brand, and every
    customer's Billing page showed the platform owner's personal address."""
    src = _read("pages", "Billing.jsx")
    assert "detectTheme()" not in src.replace("detectTheme() read", "")
    assert "resolveBrand(shellTheme(getBranding()))" in src


def test_change_password_paints_its_own_backdrop():
    """Login.css is written for a dark backdrop that only the Login page
    paints; /change-password borrowed the classes without it (1.04:1)."""
    src = _read("pages", "ChangePassword.jsx")
    assert re.search(r'className="login-page" style=\{\{ background: \'#0b1220\'', src)
    assert "AdvisorFlow" not in src.replace("(\"AdvisorFlow\")", "")


def test_agency_skin_gives_shared_screens_a_light_sheet():
    """The agency skin paints the body black; shared light-theme screens put
    their titles on it at 1.1:1."""
    css = _read("pages", "agency", "agency.css")
    assert 'html:root[data-workspace-vertical="agency"] .main-content:not(:has(.ag))' in css


def test_new_customer_form_uses_the_industry_registry_key():
    """/org-settings/industries returns {key, label}; reading `value` left
    every option without a value or key."""
    src = _read("pages", "god", "CustomerCreate.jsx")
    assert "value={i.key ?? i.value}" in src
