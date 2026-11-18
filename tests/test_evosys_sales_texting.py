"""EvoSys Sales texting (owner request 2026-11-17): text steps go out from the
team's own number, phone-only leads are reached, every text carries the opt-out
line, and a STOP or a reply to that number stops the lead."""
from datetime import datetime

import pytest

from app.models.evosys_sales_models import EvoSysOutreachMessage, EvoSysOutreachState
from app.models.sales_models import Opportunity, OpportunityEvent
from app.services import sales_suppression as SDNC
from app.services.evosys_sales import campaigns as CA
from app.services.evosys_sales import outreach as O
from app.services.evosys_sales import sms_inbound as IN

from tests.test_sales_workspace import (  # noqa: F401  (fixtures)
    platform, brand, manager, manager_headers, _make_sales_user, _add_membership, _token_headers, _SEQ,
)

MON_10AM = datetime(2026, 11, 23, 16, 0)   # Monday 10:00 Central
TEAM_NO = "+18664870628"


@pytest.fixture(autouse=True)
def _brand_env(monkeypatch, brand):
    monkeypatch.setenv("EVOSYS_SALES_BRAND_ID", brand.id)


class Rec:
    def __init__(self):
        self.calls = []

    def __call__(self, *a):
        self.calls.append(a)
        return {"success": True, "provider_message_id": "SM%d" % len(self.calls)}


STEPS = [
    {"day": 0, "channel": "text", "subject": "",
     "body": "Hi {first_name}, this is {sender_name} with EvoSys Pro. Saw {company}'s cleaning ad. "
             "Can I send you the details?"},
    {"day": 2, "channel": "email", "subject": "Details for {company}", "body": "Hi {first_name}. {booking_link}"},
]


def _ready(db, brand, number=TEAM_NO):
    s = O.get_settings(db, brand.id)
    s.mailing_address, s.offer = "100 Main St, Dallas, TX 75201", "Free for 30 days."
    s.sms_from_number = number
    db.commit()
    c = CA.ensure_default(db, s)
    c.steps = STEPS
    c.approved_hash = CA.content_hash(c, s)
    c.sending_on = True
    db.commit()
    return s


def _lead(db, brand, owner, company, phone=None, email=None):
    o = Opportunity(brand_sales_org_id=brand.id, company_name=company, contact_name="Ana Ruiz",
                    phone=phone, email=email, stage="prospect", status="open", owner_user_id=owner.id)
    db.add(o); db.commit()
    return o


def test_phone_only_lead_gets_the_text_with_the_stop_line(db_session, brand, manager):
    _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "Shine Co", phone="(214) 555-0101")
    texts, emails = Rec(), Rec()
    out = O.run_pass(db_session, MON_10AM, emails, texts)
    assert out["sent"] == 1 and not emails.calls
    to, body, _s = texts.calls[0]
    assert to == "+12145550101"
    assert "EvoSys Pro" in body and "Shine Co" in body and body.endswith("Reply STOP to opt out.")
    m = db_session.query(EvoSysOutreachMessage).filter_by(opportunity_id=o.id, channel="text").one()
    assert m.status == "sent" and m.provider_message_id == "SM1"
    assert db_session.get(EvoSysOutreachState, o.id).next_step == 1


def test_email_only_lead_skips_the_text_step(db_session, brand, manager):
    _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "Mail Co", email="owner@mail.test")
    texts, emails = Rec(), Rec()
    O.run_pass(db_session, MON_10AM, emails, texts)
    assert not texts.calls
    st = db_session.get(EvoSysOutreachState, o.id)
    assert st.status == "active" and st.next_step == 1      # moved on to the email step
    assert not db_session.query(EvoSysOutreachMessage).filter_by(opportunity_id=o.id).count()


def test_without_a_number_texts_are_held_and_phone_only_leads_wait(db_session, brand, manager):
    _ready(db_session, brand, number=None)
    phone_only = _lead(db_session, brand, manager, "P Co", phone="2145550102")
    both = _lead(db_session, brand, manager, "B Co", phone="2145550103", email="b@b.test")
    texts = Rec()
    O.run_pass(db_session, MON_10AM, Rec(), texts)
    assert not texts.calls
    assert db_session.get(EvoSysOutreachState, phone_only.id) is None      # not reachable: not enrolled
    held = db_session.query(EvoSysOutreachMessage).filter_by(opportunity_id=both.id).one()
    assert held.status == "held" and held.channel == "text"


def test_do_not_contact_phone_is_never_texted(db_session, brand, manager):
    _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "D Co", phone="2145550104")
    SDNC.record(db_session, o, manager, "asked")
    db_session.commit()
    texts = Rec()
    O.run_pass(db_session, MON_10AM, Rec(), texts)
    assert not texts.calls


def test_stop_by_text_opts_out_and_records_do_not_contact(db_session, brand, manager):
    _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "S Co", phone="214-555-0105")
    O.run_pass(db_session, MON_10AM, Rec(), Rec())
    assert IN.handle(db_session, from_number="+12145550105", to_number=TEAM_NO, body="STOP", message_sid="SMin1")
    st = db_session.get(EvoSysOutreachState, o.id)
    assert st.status == "stopped" and st.stop_reason == "opted_out" and st.opted_out_at
    assert SDNC.find(db_session, brand.id, phone="2145550105") is not None
    # a Twilio retry of the same message is not recorded twice
    assert IN.handle(db_session, from_number="+12145550105", to_number=TEAM_NO, body="STOP", message_sid="SMin1")
    assert db_session.query(EvoSysOutreachMessage).filter_by(opportunity_id=o.id, direction="in").count() == 1


def test_a_reply_by_text_stops_follow_up_and_needs_a_person(db_session, brand, manager):
    _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "R Co", phone="2145550106")
    O.run_pass(db_session, MON_10AM, Rec(), Rec())
    assert IN.handle(db_session, from_number="+12145550106", to_number=TEAM_NO,
                     body="Yes send it over", message_sid="SMin2")
    st = db_session.get(EvoSysOutreachState, o.id)
    assert st.stop_reason == "replied"
    ev = db_session.query(OpportunityEvent).filter_by(opportunity_id=o.id, event_type="outreach_reply").one()
    assert ev.summary.startswith("NEEDS A PERSON")
    assert SDNC.find(db_session, brand.id, phone="2145550106") is None


def test_other_numbers_are_left_to_the_customer_pipeline(db_session, brand, manager):
    _ready(db_session, brand)
    assert IN.handle(db_session, from_number="+12145550107", to_number="+18553601164",
                     body="STOP", message_sid="SMin3") is False


def test_emails_must_go_from_support(db_session, brand):
    s = _ready(db_session, brand)
    s.from_email = "info@evosyspro.live"
    assert any("support@evosyspro.live" in p for p in O.settings_problems(s))
    s.from_email = "support@evosyspro.live"
    assert not any("go out from" in p for p in O.settings_problems(s))
    assert O._Sender(s).from_email == "support@evosyspro.live"
