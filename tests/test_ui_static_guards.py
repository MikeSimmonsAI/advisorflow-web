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


def test_crm_reads_every_page_not_the_first_fifty():
    """/crm-native/contacts is paginated (50 by default). The CRM screen read
    page 1 only: a workspace with more contacts saw 50, a header saying "50
    contacts", and a search that could not find anybody else."""
    src = _read("pages", "CRM.jsx")
    assert "page_size=200" in src and "CRM_LOAD_CAP" in src
    assert "&search=" in src            # past the cap, search goes to the server


def test_availability_calendar_uses_the_viewers_date():
    """toISOString() is UTC: from 7pm Central the availability calendar
    marked tomorrow as today, and evening appointments showed on the next
    day's square."""
    src = _read("pages", "Availability.jsx")
    assert "today.toISOString().slice(0, 10)" not in src
    assert "e.booked_time.slice(0, 10))" not in src
    assert "const ymd = " in src


def test_capped_lists_do_not_report_the_cap_as_the_count():
    """Admin All leads stored the {items,total} envelope as the list and threw
    on .slice(); Overview/Pipeline showed a 200-row cap as an exact count."""
    admin = open("frontend/src/pages/Admin.jsx", encoding="utf-8").read()
    assert "api.get('/admin/leads').then(setAllLeads)" not in admin
    assert "loadAllLeads" in admin and "leadsTotal" in admin
    ov = open("frontend/src/pages/Overview.jsx", encoding="utf-8").read()
    assert "REPLIES_CAP" in ov and "See all {replies.length}" not in ov
    pl = open("frontend/src/pages/Pipeline.jsx", encoding="utf-8").read()
    assert "flaggedLabel(" in pl
