"""Tests for the demo HTML preparer.

The two rules in demos/README.md are invisible until a prospect is looking at
the page: a broken in-page link takes the demo down inside its own frame, and a
concept with no label reads as a claim. Both are asserted here.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    path = os.path.join(ROOT, "scripts", "prep_demo_html.py")
    spec = importlib.util.spec_from_file_location("prep_demo_html", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["prep_demo_html"] = mod
    spec.loader.exec_module(mod)
    return mod


p = _load()

PAGE = """<!DOCTYPE html><html><head><title>T</title></head><body>
<a href="#marketplace">Shop</a><a href="#">Top</a>
<a href="tel:8558536374">855-853-6374</a>
<a href="mailto:info@atlantis-enterprises.com">info@atlantis-enterprises.com</a>
<p>Call (972) 354-9455 or 214-404-6173.</p>
</body></html>"""


def test_anchor_interceptor_is_injected_before_body_close():
    out, did = p.inject_anchor_fix(PAGE)
    assert did is True
    assert p.MARK_ANCHORS in out
    assert out.index(p.MARK_ANCHORS) < out.index("</body>")


def test_anchor_injection_is_idempotent():
    once, _ = p.inject_anchor_fix(PAGE)
    twice, did = p.inject_anchor_fix(once)
    assert did is False
    assert twice == once


def test_concept_bar_lands_immediately_after_body_open():
    out, did = p.inject_bar(PAGE, "DESIGN CONCEPT", "Illustrative only.")
    assert did is True
    assert "DESIGN CONCEPT" in out
    body_open = out.lower().index("<body")
    assert out.index(p.MARK_BAR) > body_open
    # Nothing of the page may precede the label.
    assert out.index(p.MARK_BAR) < out.index("Shop")


def test_concept_bar_is_idempotent():
    once, _ = p.inject_bar(PAGE, "A", "B")
    twice, did = p.inject_bar(once, "A", "B")
    assert did is False
    assert twice == once


def test_a_page_with_no_body_tag_still_gets_both():
    frag = '<div><a href="#x">x</a></div>'
    out, _ = p.inject_anchor_fix(frag)
    out, _ = p.inject_bar(out, "DESIGN CONCEPT", "note")
    assert p.MARK_ANCHORS in out and p.MARK_BAR in out


# ── contact safety ─────────────────────────────────────────────────────────

def test_phone_numbers_move_into_the_reserved_range():
    out, changes = p.safe_contacts(PAGE)
    assert "855-853-6374" not in out
    assert "(972) 354-9455" not in out
    assert "214-404-6173" not in out
    assert p.SAFE_PHONE_PREFIX in out
    assert any(k == "phone" for k, _, _ in changes)


def test_emails_move_to_the_reserved_domain():
    out, changes = p.safe_contacts(PAGE)
    assert "atlantis-enterprises.com" not in out
    assert "@example.com" in out
    assert any(k == "email" for k, _, _ in changes)


def test_tel_href_follows_the_visible_number():
    out, _ = p.safe_contacts(PAGE)
    assert "tel:8558536374" not in out
    assert "tel:8555550" in out.replace(" ", "")


def test_the_same_number_twice_becomes_the_same_replacement():
    page = "<body>(972) 354-9455 in the header, (972) 354-9455 in the footer</body>"
    out, changes = p.safe_contacts(page)
    phones = [c for c in changes if c[0] == "phone"]
    assert len(phones) == 1, "one original should map to one replacement"
    assert out.count(phones[0][2]) == 2


def test_an_already_safe_address_is_left_alone():
    page = "<body>hello@example.com</body>"
    out, changes = p.safe_contacts(page)
    assert out == page
    assert changes == []


def test_prices_and_zips_are_not_mistaken_for_phone_numbers():
    page = "<body>13.8 cents per kWh, ZIP 76063, est $138/mo, 1,000 kWh</body>"
    out, changes = p.safe_contacts(page)
    assert changes == []
    assert out == page


def test_the_script_names_no_customer():
    with open(os.path.join(ROOT, "scripts", "prep_demo_html.py"),
              encoding="utf-8") as fh:
        src = fh.read().lower()
    for word in ("atlantis", "restland", "jason", "evosys", "bookaboost"):
        assert word not in src, f"{word!r} does not belong in a reusable tool"
