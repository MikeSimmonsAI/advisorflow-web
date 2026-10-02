"""THE PUBLIC SITE'S BOOK A DEMO PATH - static checks for the 2026-10-02 defects.

1. On phones the header CSS hid the FIRST action button, which was "Get a
   Demo", and kept "SMS Opt-In": a phone visitor saw no demo button until the
   bottom of a ten-screen page, and there was no menu at all below 1050px.
2. /request-demo/ laid out 532px wide on a 375px phone (a single-column grid
   track of `1fr` grew to the date strip's min-content), so the phone zoomed
   the whole form out.
3. Legal pages linked their nav to `#see` etc., anchors that exist only on the
   home page; the 404 page (served for every missing URL) used relative links
   that resolve under the missing path.
4. A request without a time said "We have your information" - now it says the
   request was received and that nothing is booked yet.
"""
import re
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "public-site"
STATIC = sorted(p for p in SITE.glob("*.html"))


def _css_sources():
    yield SITE / "assets" / "site.css", (SITE / "assets" / "site.css").read_text(encoding="utf-8")
    for p in STATIC:
        yield p, p.read_text(encoding="utf-8")


def test_phones_keep_the_demo_button_and_hide_the_secondary_one():
    for path, src in _css_sources():
        assert ".nav .actions .btn:first-child{display:none}" not in src, path.name
        assert ".nav .actions .btn-ghost-gold{display:none}" in src, path.name


def test_every_page_has_a_menu_with_book_a_demo_below_desktop_width():
    for p in STATIC:
        src = p.read_text(encoding="utf-8")
        nav = src[src.index('<nav class="nav">'):src.index('</nav>')]
        assert 'class="mnav"' in nav, p.name
        assert re.search(r'class="mnav-cta" href="/request-demo/\?cta=menu">Book a Demo</a>', nav), p.name
        assert re.search(r'href="/request-demo/\?cta=header">Book a Demo</a>', nav), p.name
    php = (SITE / "private" / "site.php").read_text(encoding="utf-8")
    assert 'class="mnav"' in php and "request-demo/?cta=menu" in php and "request-demo/?cta=header" in php


def test_the_home_hero_offers_book_a_demo_first():
    src = (SITE / "index.html").read_text(encoding="utf-8")
    hero = src[src.index('<section class="hero">'):]
    actions = hero[hero.index('class="hero-actions"'):hero.index('</div>', hero.index('class="hero-actions"'))]
    assert actions.index("Book a Demo") < actions.index("Ask EvoSys Pro")
    assert 'href="/request-demo/?cta=hero"' in actions


def test_single_column_grids_cannot_grow_past_the_phone():
    for path, src in _css_sources():
        assert ".form-shell{grid-template-columns:1fr}" not in src, path.name
        if ".form-shell" in src and "max-width:1050px" in src:
            assert "grid-template-columns:minmax(0,1fr)}" in src, path.name


def test_no_dead_relative_or_cross_page_anchor_links_in_static_pages():
    for p in STATIC:
        src = p.read_text(encoding="utf-8")
        for href in re.findall(r'href="([^"]*)"', src):
            if re.match(r'^(https?:|mailto:|tel:|data:|/|javascript:)', href):
                continue
            assert href.startswith("#"), "%s has relative link %s" % (p.name, href)
        if p.name != "index.html":
            nav = src[src.index('<nav class="nav">'):src.index('</nav>')]
            assert not re.search(r'href="#', nav), "%s nav points at an anchor it does not have" % p.name


def test_every_internal_link_target_exists():
    for p in STATIC:
        src = p.read_text(encoding="utf-8")
        for href in set(re.findall(r'href="(/[^"#?]*)', src)):
            path = href.lstrip("/")
            if path in ("", "sell"):
                continue
            target = SITE / path
            assert target.exists() or (SITE / path / "index.php").exists() or \
                (SITE / path / "index.html").exists(), "%s -> %s" % (p.name, href)


def test_the_request_confirmation_never_claims_a_booking():
    src = (SITE / "request-demo" / "index.php").read_text(encoding="utf-8")
    assert "Your demo request has been received." in src
    assert "No meeting time has been booked yet." in src
    assert "We have your information." not in src


def test_the_demo_form_sends_interest_and_attribution():
    src = (SITE / "request-demo" / "index.php").read_text(encoding="utf-8")
    assert 'name="interest"' in src
    for key in ("cta", "utm_source", "utm_campaign", "landing_page"):
        assert key in src
    assert "function demo_context" in src
    assert "evo_attr" in (SITE / "assets" / "site.js").read_text(encoding="utf-8")


def test_sms_consent_box_is_still_optional_and_unchecked():
    src = (SITE / "request-demo" / "index.php").read_text(encoding="utf-8")
    box = re.search(r'<input type="checkbox" name="sms_consent"[^>]*>', src).group(0)
    assert "checked" not in box and "required" not in box
