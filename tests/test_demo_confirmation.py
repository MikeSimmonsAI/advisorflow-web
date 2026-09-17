"""
SS11 — the branded demo confirmation, and the rule that the graphic is not a link.

THE NON-NEGOTIABLE. Nothing a prospect must CLICK may live inside the image.
An image is not a link, corporate mail clients block images by default, and a
painted button that does nothing is worse than no button - the prospect clicks
it, nothing happens, and they conclude the meeting is not real.

That rule is enforced in code, not in a comment: `build` renders the CTA itself
from the appointment's meeting_url and refuses any graphic whose URL or alt
text carries a join phrase.

AND NO MEETING URL MEANS NO BUTTON. appointment_meetings already states the
house rule - "a provider failure produces an appointment with no video link and
a visible reason. It never produces a fake link" - so a confirmation for an
appointment without one shows the location or says the link will follow, and
renders no CTA at all.

NOTHING HERE SENDS. `build` and `preview` reach no provider; `send` goes
through the gate, which ships disabled.
"""

from datetime import datetime, timedelta

import pytest

from app.services import demo_confirmation as dc
from app.services import outbound_email_gate as gate


class _Appt:
    """A stand-in with exactly the fields the renderer reads."""
    def __init__(self, **kw):
        self.id = kw.get("id", "appt-1")
        self.title = kw.get("title", "EvoSys Pro demo")
        self.prospect_name = kw.get("prospect_name", "Sam")
        self.prospect_company = kw.get("prospect_company", "Countryside Land")
        self.prospect_email = kw.get("prospect_email", "sam@countryside.test")
        self.prospect_timezone = kw.get("prospect_timezone", "America/New_York")
        self.timezone = kw.get("timezone", "America/Chicago")
        self.starts_at = kw.get("starts_at", datetime(2026, 10, 1, 15, 0))
        self.ends_at = kw.get("ends_at", datetime(2026, 10, 1, 15, 45))
        self.meeting_url = kw.get("meeting_url", "https://zoom.us/j/123456")
        self.location = kw.get("location")
        self.opportunity_id = kw.get("opportunity_id")
        # `send` resolves the brand through appointment_invites.brand_identity,
        # which reads this. None is a real state - it resolves to the fallback
        # identity rather than raising.
        self.brand_sales_org_id = kw.get("brand_sales_org_id")
        self.demo_confirmation_count = 0
        self.demo_confirmation_sent_at = None
        self.demo_confirmation_error = None


IDENT = {"name": "EvoSys Pro", "accent": "#087cff",
         "website": "evosyspro.live", "logo_url": "https://cdn.test/logo.png",
         "support_phone": "469-555-0100", "from_email": "support@evosyspro.live"}


# ── the visual rule ─────────────────────────────────────────────────────────

def test_the_cta_is_real_html_using_the_appointments_meeting_url():
    out = dc.build(_Appt(), IDENT)
    assert out["has_cta"] is True
    assert '<a href="https://zoom.us/j/123456"' in out["html"]
    assert "Join your demo</a>" in out["html"]


def test_the_join_link_is_never_inside_the_image():
    out = dc.build(_Appt(), IDENT, graphic_url="https://cdn.test/hero.png",
                   graphic_alt="EvoSys Pro preview")
    html = out["html"]
    img = html[html.index("<img"):html.index(">", html.index("<img"))]
    assert "zoom.us" not in img
    assert "join" not in img.lower()
    # And the real link is still there, outside it.
    assert '<a href="https://zoom.us/j/123456"' in html


def test_a_graphic_advertising_a_join_action_is_refused():
    for alt in ("Join on Zoom", "JOIN YOUR LIVE DEMO", "Click here to join now"):
        with pytest.raises(dc.GraphicRefused):
            dc.build(_Appt(), IDENT, graphic_url="https://cdn.test/hero.png",
                     graphic_alt=alt)


def test_a_graphic_whose_alt_text_carries_a_url_is_refused():
    with pytest.raises(dc.GraphicRefused, match="alt text contains a URL"):
        dc.build(_Appt(), IDENT, graphic_url="https://cdn.test/hero.png",
                 graphic_alt="Open https://zoom.us/j/123456")


def test_a_graphic_hosted_at_a_meeting_provider_is_refused():
    with pytest.raises(dc.GraphicRefused):
        dc.build(_Appt(), IDENT, graphic_url="https://zoom.us/hero.png",
                 graphic_alt="preview")


# ── the missing-URL rule ────────────────────────────────────────────────────

def test_no_meeting_url_means_no_button_at_all():
    out = dc.build(_Appt(meeting_url=None), IDENT)
    assert out["has_cta"] is False
    assert "Join your demo" not in out["html"]
    assert "will follow" in out["html"]


def test_no_meeting_url_but_a_location_shows_the_location(): 
    out = dc.build(_Appt(meeting_url=None, location="1600 Restland Rd"), IDENT)
    assert out["has_cta"] is False
    assert "1600 Restland Rd" in out["html"]
    assert "Join your demo" not in out["html"]


def test_a_dead_cta_is_never_rendered_under_any_input():
    """The single rule this module exists for, asserted over every shape."""
    for appt in (_Appt(meeting_url=None), _Appt(meeting_url=""),
                 _Appt(meeting_url=None, location="Somewhere")):
        html = dc.build(appt, IDENT)["html"]
        assert 'href=""' not in html
        assert 'href="None"' not in html
        assert "Join your demo" not in html


# ── industry profiles ───────────────────────────────────────────────────────

def test_a_known_industry_changes_the_language():
    funeral = dc.build(_Appt(), IDENT, industry="funeral")["html"]
    assert "families" in funeral


def test_an_unknown_industry_falls_back_to_generic_rather_than_guessing():
    out = dc.build(_Appt(), IDENT, industry="underwater-basket-weaving")
    assert out["industry"] == dc.GENERIC
    assert "pipeline" in out["html"]


def test_no_industry_at_all_is_generic():
    assert dc.build(_Appt(), IDENT, industry=None)["industry"] == dc.GENERIC
    assert dc.profile_for(None) is dc.INDUSTRY_PROFILES[dc.GENERIC]


# ── personalisation and timezone ────────────────────────────────────────────

def test_the_prospect_and_company_are_personalised():
    html = dc.build(_Appt(), IDENT)["html"]
    assert "Hi Sam," in html
    assert "Countryside Land" in html


def test_the_time_is_shown_in_the_prospects_timezone():
    """Showing a prospect in New York a Chicago time without saying so is how
    people arrive an hour late."""
    html = dc.build(_Appt(), IDENT)["html"]
    assert "New York" in html


def test_the_salesperson_is_named_when_there_is_one():
    class Rep:
        full_name = "Mike Simmons"
        email = "mike@example.test"
    html = dc.build(_Appt(), IDENT, salesperson=Rep())["html"]
    assert "Mike Simmons" in html


def test_the_brand_is_never_hardcoded():
    other = dict(IDENT, name="BookaBoost", website="bookaboost.live")
    # The title is neutral on purpose: the assertion below is about the
    # TEMPLATE carrying no brand of its own, and a stub whose own meeting title
    # said "EvoSys Pro demo" would fail it on the prospect's own words.
    html = dc.build(_Appt(title="Platform demo"), other)["html"]
    assert "BookaBoost" in html
    assert "EvoSys" not in html


def test_prospect_supplied_text_is_escaped():
    html = dc.build(_Appt(prospect_name='<script>alert(1)</script>'), IDENT)["html"]
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ── sending stays disabled ──────────────────────────────────────────────────

def test_the_renderer_reaches_no_provider():
    import ast, inspect
    tree = ast.parse(inspect.getsource(dc.build))
    names = {getattr(n.func, "id", None) or getattr(n.func, "attr", None)
             for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "send_email_via_provider" not in names
    assert "send_email" not in names


def test_sending_is_refused_in_this_build(db_session, monkeypatch):
    from unittest.mock import patch
    for s in gate.GATED_SOURCES:
        monkeypatch.delenv(gate._ENV_BY_SOURCE[s], raising=False)
    with patch("app.services.email_service.send_email_via_provider") as provider:
        with pytest.raises(Exception) as caught:
            dc.send(db_session, _Appt())
    provider.assert_not_called()
    assert "OUTBOUND_EMAIL_STAFF_ESCALATION" in str(caught.value)


def test_an_appointment_with_no_prospect_email_is_refused_before_anything(db_session):
    with pytest.raises(ValueError, match="no prospect email"):
        dc.send(db_session, _Appt(prospect_email=None))
