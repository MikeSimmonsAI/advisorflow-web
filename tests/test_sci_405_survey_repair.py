"""#405 - ONE survey path: after a COMPLETED visit, in the right time zone,
once, through the approved gateways, with an honest record. Synthetic only:
every provider call is replaced; nothing reaches Twilio or a mail server."""
import threading
from datetime import datetime, timedelta

import pytest

from app.models.models import (BookingFollowup, BookingLink, EmailMessage, Lead, LeadOutcome, Message,
                               Notification, SurveyResponse)
from app.services import survey_dispatch as SD
from tests.test_outreach_program import program  # noqa: F401

# Walter Johnson: Thursday Oct 22 2026, 2:00 PM CT (CDT, UTC-5) = 19:00 UTC.
WALTER_LOCAL = datetime(2026, 10, 22, 14, 0)
WALTER_SURVEY_ARRIVED = datetime(2026, 10, 22, 15, 27)   # 10:27 AM CT - what happened in production


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("SURVEY_SEND_ENABLED", "true")
    from app.services import contact_hours
    monkeypatch.setattr(contact_hours, "check", lambda lead, now=None: {"permitted": True})
    from app.services import public_identity
    monkeypatch.setattr(public_identity, "survey_url", lambda db, org, tok: "https://feedback.example.com/survey/" + tok)
    return monkeypatch


class FakeGateways:
    """Stands in for sms_service.send_sms / compose_body and the email gateway."""

    def __init__(self, mp, db, *, links=True, sms_error=None, email_error=None):
        self.texts, self.emails, self.db = [], [], db
        from app.services import outbound_email_gate, sms_service
        strip = (lambda b: b) if links else (lambda b: b.split(" https://")[0] + " Reply STOP to opt out.")

        def compose(template, lead, advisor, url):
            return strip(template)

        def send_sms(db, advisor, lead, template, include_booking_link=True, send_source=None, **k):
            if sms_error:
                raise ValueError(sms_error)
            m = Message(lead_id=lead.id, sender_id=advisor.id, body=strip(template), delivery_status="pending",
                        send_source=send_source)
            db.add(m)
            db.commit()
            self.texts.append(m.body)
            return m

        def send_email(db, lead, *, advisor, subject, body_html, send_source, actor_user_id=None):
            if email_error:
                raise RuntimeError(email_error)
            e = EmailMessage(lead_id=lead.id, sender_id=advisor.id, subject=subject, body_html=body_html,
                             status="sent", send_source=send_source)
            db.add(e)
            db.commit()
            self.emails.append(subject)
            return e

        mp.setattr(sms_service, "compose_body", compose)
        mp.setattr(sms_service, "send_sms", send_sms)
        mp.setattr(outbound_email_gate, "send_lead_email", send_email)


# Owner rule (Oct 26): one survey 24 hours after the visit ENDS, no staff action.
# Walter's visit ends 3:00 PM CT Oct 22 (20:00 UTC); due 3:00 PM CT Oct 23.
DUE = timedelta(hours=26)                  # from the visit's start: end + 24 h + 1 h


def _visit(db, org, advisor, *, local=WALTER_LOCAL, attendance=None, phone="12145550111",
           email="fam@example.com", minutes=60, status="booked", first="walter"):
    lead = Lead(organization_id=org.id, first_name=first, last_name="Johnson", phone=phone, email=email,
                status="booked", assigned_to_id=advisor.id)
    db.add(lead)
    db.flush()
    bl = BookingLink(lead_id=lead.id, user_id=advisor.id, status=status, booked_time=local, appt_duration=minutes)
    db.add(bl)
    db.flush()
    if attendance:
        db.add(LeadOutcome(lead_id=lead.id, recorded_by_id=advisor.id, booking_link_id=bl.id, attendance=attendance))
    db.commit()
    return lead, bl


def _utc(local_ct, offset_hours=5):
    return local_ct + timedelta(hours=offset_hours)


# ── time ────────────────────────────────────────────────────────────────────

def test_walters_2pm_visit_is_surveyed_once_exactly_24_hours_after_it_ends(db_session, sample_org, sample_advisor, on):
    gw = FakeGateways(on, db_session)
    _, bl = _visit(db_session, sample_org, sample_advisor)               # no staff action at all
    assert SD.run(db_session, WALTER_SURVEY_ARRIVED)["sent"] == 0        # 10:27 AM CT Oct 22 - never again
    assert SD.run(db_session, datetime(2026, 10, 22, 21, 1))["sent"] == 0   # 4:01 PM CT, 1 h after the end
    assert SD.run(db_session, datetime(2026, 10, 23, 19, 59))["sent"] == 0  # 2:59 PM CT Oct 23: 23 h 59 m
    assert gw.texts == [] and db_session.query(BookingFollowup).count() == 0
    assert SD.run(db_session, datetime(2026, 10, 23, 20, 0))["sent"] == 1   # 3:00 PM CT Oct 23: exactly 24 h
    assert SD.run(db_session, datetime(2026, 10, 23, 21, 0))["sent"] == 0   # and never again
    assert len(gw.texts) == 1


def test_24_hours_counts_across_the_daylight_saving_change(db_session, sample_org, sample_advisor, on):
    """A visit Sat Oct 31 2026 2:00-3:00 PM CDT ends 20:00 UTC; clocks fall
    back Nov 1. 24 real hours later is 20:00 UTC Nov 1 = 2:00 PM CST."""
    gw = FakeGateways(on, db_session)
    _visit(db_session, sample_org, sample_advisor, local=datetime(2026, 10, 31, 14, 0))
    assert SD.run(db_session, datetime(2026, 11, 1, 19, 59))["sent"] == 0
    assert SD.run(db_session, datetime(2026, 11, 1, 20, 0))["sent"] == 1 and len(gw.texts) == 1


def test_duration_sets_the_end(db_session, sample_org, sample_advisor, on):
    gw = FakeGateways(on, db_session)
    _visit(db_session, sample_org, sample_advisor, minutes=90)          # 2:00-3:30 PM CT
    assert SD.run(db_session, datetime(2026, 10, 23, 20, 29))["sent"] == 0
    assert SD.run(db_session, datetime(2026, 10, 23, 20, 30))["sent"] == 1


def test_end_time_is_converted_with_daylight_saving(db_session, sample_org, sample_advisor):
    _, summer = _visit(db_session, sample_org, sample_advisor, local=datetime(2026, 10, 22, 14, 0))
    _, winter = _visit(db_session, sample_org, sample_advisor, local=datetime(2026, 11, 2, 14, 0))
    assert SD.appointment_end_utc(db_session, summer) == datetime(2026, 10, 22, 20, 0)   # CDT UTC-5
    assert SD.appointment_end_utc(db_session, winter) == datetime(2026, 11, 2, 21, 0)    # CST UTC-6
    # The fall-back night itself: 1:30 AM exists twice; the first (CDT) is used, never earlier.
    _, fold = _visit(db_session, sample_org, sample_advisor, local=datetime(2026, 11, 1, 1, 30), minutes=30)
    assert SD.appointment_end_utc(db_session, fold) == datetime(2026, 11, 1, 7, 0)


def test_advisor_in_another_zone_uses_that_zone(db_session, sample_org, sample_advisor):
    sample_advisor.booking_timezone = "America/New_York"
    db_session.commit()
    _, bl = _visit(db_session, sample_org, sample_advisor)
    assert SD.appointment_end_utc(db_session, bl) == datetime(2026, 10, 22, 19, 0)


# ── which visits ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("att", [None, "completed", "follow_up_needed"])
def test_no_staff_action_needed_and_reporting_buttons_dont_block(db_session, sample_org, sample_advisor, on, att):
    gw = FakeGateways(on, db_session)
    _visit(db_session, sample_org, sample_advisor, attendance=att)
    assert SD.run(db_session, _utc(WALTER_LOCAL) + DUE)["sent"] == 1 and len(gw.texts) == 1


@pytest.mark.parametrize("att", ["no_show", "cancelled"])
def test_no_show_or_cancelled_is_never_surveyed(db_session, sample_org, sample_advisor, on, att):
    gw = FakeGateways(on, db_session)
    _, bl = _visit(db_session, sample_org, sample_advisor, attendance=att)
    later = _utc(WALTER_LOCAL) + DUE
    assert att.replace("_", "-") in SD.ineligible_reason(db_session, bl, later)
    assert SD.run(db_session, later)["sent"] == 0 and gw.texts == []
    assert db_session.query(BookingFollowup).count() == 0                      # nothing claimed either


@pytest.mark.parametrize("status", ["cancelled", "expired", "rescheduled", "pending"])
def test_a_booking_that_is_no_longer_live_is_never_surveyed(db_session, sample_org, sample_advisor, on, status):
    gw = FakeGateways(on, db_session)
    _visit(db_session, sample_org, sample_advisor, status=status)
    assert SD.run(db_session, _utc(WALTER_LOCAL) + DUE)["sent"] == 0 and gw.texts == []


def test_rescheduled_visit_surveys_only_the_new_time(db_session, sample_org, sample_advisor, on):
    gw = FakeGateways(on, db_session)
    lead, old = _visit(db_session, sample_org, sample_advisor)
    old.created_at = datetime(2026, 10, 15, 12, 0)
    new = BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked",
                      booked_time=datetime(2026, 10, 26, 10, 0), appt_duration=60,
                      created_at=datetime(2026, 10, 21, 12, 0))                 # rebooked before the 22nd
    db_session.add(new)
    db_session.commit()
    assert SD.rescheduled(db_session, old) and not SD.rescheduled(db_session, new)
    assert SD.run(db_session, _utc(WALTER_LOCAL) + DUE)["sent"] == 0          # the old slot: nothing
    assert SD.run(db_session, datetime(2026, 10, 27, 16, 0))["sent"] == 1     # 24 h after the new visit
    assert len(gw.texts) == 1


def test_moving_a_bookings_own_time_moves_the_survey(db_session, sample_org, sample_advisor, on):
    gw = FakeGateways(on, db_session)
    _, bl = _visit(db_session, sample_org, sample_advisor)
    bl.booked_time = datetime(2026, 10, 24, 9, 0)                            # moved to Sat 9 AM
    db_session.commit()
    assert SD.run(db_session, _utc(WALTER_LOCAL) + DUE)["sent"] == 0
    assert SD.run(db_session, datetime(2026, 10, 25, 15, 0))["sent"] == 1


def test_status_changed_after_listing_is_rechecked(db_session, sample_org, sample_advisor, on):
    gw = FakeGateways(on, db_session)
    _, bl = _visit(db_session, sample_org, sample_advisor)
    now = _utc(WALTER_LOCAL) + DUE
    listed = SD.candidates(db_session, now)
    assert [b.id for b in listed] == [bl.id]
    bl.status = "cancelled"
    db_session.commit()
    assert SD.claim(db_session, listed[0], now) is None and gw.texts == []


def test_cancelled_booking_and_old_visits_are_never_surveyed(db_session, sample_org, sample_advisor, on):
    FakeGateways(on, db_session)
    _, cancelled = _visit(db_session, sample_org, sample_advisor, status="cancelled")
    _, old = _visit(db_session, sample_org, sample_advisor, local=WALTER_LOCAL - timedelta(days=30))
    now = _utc(WALTER_LOCAL) + DUE
    assert "cancelled" in SD.ineligible_reason(db_session, cancelled, now)
    assert "more than 3 days" in SD.ineligible_reason(db_session, old, now)
    assert SD.run(db_session, now)["sent"] == 0


def test_switched_off_sends_and_claims_nothing(db_session, sample_org, sample_advisor, monkeypatch):
    monkeypatch.delenv("SURVEY_SEND_ENABLED", raising=False)
    gw = FakeGateways(monkeypatch, db_session)
    _visit(db_session, sample_org, sample_advisor)
    out = SD.run(db_session, _utc(WALTER_LOCAL) + DUE)
    assert out["enabled"] is False and out["sent"] == 0 and gw.texts == []
    assert db_session.query(BookingFollowup).count() == 0


# ── one survey ──────────────────────────────────────────────────────────────

def test_an_existing_survey_is_never_resent(db_session, sample_org, sample_advisor, on):
    gw = FakeGateways(on, db_session)
    lead, bl = _visit(db_session, sample_org, sample_advisor)
    # Walter's real history: surveyed (early) and answered.
    bl.review_request_sent_at = WALTER_SURVEY_ARRIVED
    fu = BookingFollowup(booking_link_id=bl.id, lead_id=lead.id, advisor_id=sample_advisor.id,
                         survey_link_sent=True, channel="sms")
    db_session.add(fu)
    db_session.flush()
    db_session.add(SurveyResponse(booking_followup_id=fu.id, lead_id=lead.id, advisor_id=sample_advisor.id, rating=5))
    db_session.commit()
    token = fu.survey_token
    assert SD.run(db_session, _utc(WALTER_LOCAL) + DUE)["sent"] == 0 and gw.texts == []
    db_session.refresh(fu)
    assert fu.survey_token == token and fu.state is None                        # untouched
    assert db_session.query(SurveyResponse).count() == 1


def test_running_twice_sends_once(db_session, sample_org, sample_advisor, on):
    gw = FakeGateways(on, db_session)
    _visit(db_session, sample_org, sample_advisor)
    now = _utc(WALTER_LOCAL) + DUE
    assert SD.run(db_session, now)["sent"] == 1
    assert SD.run(db_session, now)["sent"] == 0
    assert len(gw.texts) == 1 and db_session.query(BookingFollowup).count() == 1


def test_simultaneous_workers_claim_once(tmp_path):
    """Two runners with their OWN connections (a file database, real pooling)
    race for one completed visit: exactly one claim wins."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.models.models import Base, Organization, User
    engine = create_engine("sqlite:///%s" % (tmp_path / "race.db"), connect_args={"timeout": 30})
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        org = Organization(name="Race Org", slug="race", plan="standard", industry="funeral")
        s.add(org)
        s.flush()
        u = User(organization_id=org.id, email="r@race.example", password_hash="x", full_name="R",
                 role="advisor", must_change_password=False)
        s.add(u)
        s.flush()
        lead, bl = _visit(s, org, u)
        bid = bl.id
    for _ in range(5):                                    # several races, same answer every time
        with Session(engine) as s:
            s.execute(__import__("sqlalchemy").text("DELETE FROM booking_followups"))
            s.execute(__import__("sqlalchemy").text("UPDATE booking_links SET review_request_sent_at = NULL"))
            s.commit()
        wins, errors = [], []
        barrier = threading.Barrier(2)

        def runner():
            try:
                with Session(engine) as s:
                    b = s.query(BookingLink).filter_by(id=bid).one()
                    barrier.wait()
                    if SD.claim(s, b, _utc(WALTER_LOCAL) + DUE) is not None:
                        wins.append(1)
            except Exception as exc:                      # a lock timeout is a lost claim, never a second win
                errors.append(type(exc).__name__)

        ts = [threading.Thread(target=runner) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert len(wins) == 1, (wins, errors)
        with Session(engine) as s:
            assert s.query(BookingFollowup).filter_by(booking_link_id=bid).count() == 1


def test_the_retired_senders_send_nothing(db_session, sample_org, sample_advisor, on):
    from app.services import post_appointment_service as pas
    _visit(db_session, sample_org, sample_advisor)
    assert pas.check_and_send_followups(db_session) == 0
    with pytest.raises(RuntimeError):
        pas._send_sms(sample_advisor, None, "x")
    assert db_session.query(BookingFollowup).count() == 0


# ── links ───────────────────────────────────────────────────────────────────

def test_short_tokens_are_random_and_collision_checked(db_session, sample_org, sample_advisor, monkeypatch):
    toks = {SD.new_token(db_session) for _ in range(200)}
    assert len(toks) == 200 and all(len(t) == 12 and t.isalnum() for t in toks)
    lead, bl = _visit(db_session, sample_org, sample_advisor)
    db_session.add(BookingFollowup(booking_link_id=bl.id, lead_id=lead.id, advisor_id=sample_advisor.id,
                                   survey_token="AAAAAAAAAAAA"))
    db_session.commit()
    seq = iter("A" * 12 + "B" * 12)
    monkeypatch.setattr(SD.secrets, "choice", lambda alphabet: next(seq))
    assert SD.new_token(db_session) == "B" * 12                                 # the taken one was skipped


def test_legacy_uuid_links_still_open_and_submit(client, db_session, sample_org, sample_advisor):
    lead, bl = _visit(db_session, sample_org, sample_advisor)
    fu = BookingFollowup(booking_link_id=bl.id, lead_id=lead.id, advisor_id=sample_advisor.id)
    db_session.add(fu)
    db_session.commit()
    assert len(fu.survey_token) == 36
    assert client.get("/survey/%s/context" % fu.survey_token).status_code == 200
    r = client.post("/survey/%s" % fu.survey_token, json={"rating": 5})
    assert r.status_code == 200 and r.json()["success"] is True


def test_no_text_when_the_sender_may_not_carry_links_email_instead(db_session, sample_org, sample_advisor, on):
    gw = FakeGateways(on, db_session, links=False)
    _visit(db_session, sample_org, sample_advisor)
    assert SD.run(db_session, _utc(WALTER_LOCAL) + DUE)["sent"] == 1
    fu = db_session.query(BookingFollowup).one()
    assert gw.texts == [] and len(gw.emails) == 1                                # never both, never a link-less "survey"
    assert fu.channel == "email" and fu.state == "sent" and fu.email_message_id


def test_no_link_and_no_email_is_held_with_a_reason(db_session, sample_org, sample_advisor, on):
    gw = FakeGateways(on, db_session, links=False)
    _visit(db_session, sample_org, sample_advisor, email=None)
    out = SD.run(db_session, _utc(WALTER_LOCAL) + DUE)
    fu = db_session.query(BookingFollowup).one()
    assert out["held"] == 1 and gw.texts == [] and gw.emails == []
    assert fu.state == "held" and "not approved to carry links" in fu.state_reason
    assert fu.survey_link_sent is False


def test_email_only_family(db_session, sample_org, sample_advisor, on):
    gw = FakeGateways(on, db_session)
    _visit(db_session, sample_org, sample_advisor, phone=None)
    assert SD.run(db_session, _utc(WALTER_LOCAL) + DUE)["sent"] == 1
    assert gw.texts == [] and len(gw.emails) == 1


# ── gates and failures ──────────────────────────────────────────────────────

def test_stop_dnc_refusal_is_recorded_not_claimed_as_sent(db_session, sample_org, sample_advisor, on):
    gw = FakeGateways(on, db_session, sms_error="Lead x's phone number is on the suppression list - blocked",
                      email_error="This lead opted out of email")
    _visit(db_session, sample_org, sample_advisor)
    out = SD.run(db_session, _utc(WALTER_LOCAL) + DUE)
    fu = db_session.query(BookingFollowup).one()
    assert out["failed"] == 1 and fu.state == "failed" and fu.survey_link_sent is False
    assert "suppression list" in fu.state_reason and "opted out of email" in fu.state_reason


def test_dnc_family_is_not_even_claimed(db_session, sample_org, sample_advisor, on):
    FakeGateways(on, db_session)
    lead, bl = _visit(db_session, sample_org, sample_advisor)
    lead.status = "dnc"
    db_session.commit()
    assert SD.run(db_session, _utc(WALTER_LOCAL) + DUE)["sent"] == 0
    assert db_session.query(BookingFollowup).count() == 0


def test_failed_send_is_surfaced_and_recovered_once(db_session, sample_org, sample_advisor, on):
    gw = FakeGateways(on, db_session, sms_error="Twilio 30007 carrier filtered", email_error="mail down")
    _visit(db_session, sample_org, sample_advisor)
    now = _utc(WALTER_LOCAL) + DUE
    SD.run(db_session, now)
    fu = db_session.query(BookingFollowup).one()
    items = SD.attention(db_session, sample_org.id)
    assert [i["followup_id"] for i in items] == [fu.id] and "30007" in items[0]["reason"]
    # the provider recovers; a person retries
    gw2 = FakeGateways(on, db_session)
    out = SD.retry(db_session, fu.id, sample_org.id, now)
    assert out["ok"] is True and len(gw2.texts) == 1
    db_session.refresh(fu)
    assert fu.state == "sent" and fu.attempts == 2
    again = SD.retry(db_session, fu.id, sample_org.id, now)
    assert again["ok"] is False and len(gw2.texts) == 1                         # never twice


def test_retry_refused_when_something_may_have_reached_them(db_session, sample_org, sample_advisor, on):
    FakeGateways(on, db_session)
    lead, bl = _visit(db_session, sample_org, sample_advisor)
    now = _utc(WALTER_LOCAL) + DUE
    fu = SD.claim(db_session, bl, now)                                        # a crash after the claim
    m = Message(lead_id=lead.id, sender_id=sample_advisor.id, body="x", delivery_status="delivered")
    db_session.add(m)
    db_session.flush()
    fu.message_id = m.id
    fu.created_at = now
    db_session.commit()
    out = SD.retry(db_session, fu.id, sample_org.id, now + timedelta(hours=2))      # stuck an hour+
    assert out["ok"] is False and "may already have reached" in out["reason"]


def test_a_text_accepted_without_its_link_is_not_called_a_survey(db_session, sample_org, sample_advisor, on):
    gw = FakeGateways(on, db_session)
    from app.services import sms_service

    def sends_without_link(db, advisor, lead, template, **k):
        m = Message(lead_id=lead.id, sender_id=advisor.id, body="Hi Walter. Reply STOP to opt out.")
        db.add(m)
        db.commit()
        return m
    on.setattr(sms_service, "send_sms", sends_without_link)
    _visit(db_session, sample_org, sample_advisor)
    SD.run(db_session, _utc(WALTER_LOCAL) + DUE)
    fu = db_session.query(BookingFollowup).one()
    assert fu.state == "failed" and fu.survey_link_sent is False and "WITHOUT the survey link" in fu.state_reason


def test_undelivered_text_is_surfaced(db_session, sample_org, sample_advisor, on):
    FakeGateways(on, db_session)
    _visit(db_session, sample_org, sample_advisor)
    SD.run(db_session, _utc(WALTER_LOCAL) + DUE)
    fu = db_session.query(BookingFollowup).one()
    m = db_session.query(Message).filter_by(id=fu.message_id).one()
    assert fu.state == "sent"                                                   # accepted ...
    m.delivery_status, m.error_code = "undelivered", "30007"                    # ... the carrier said no
    db_session.commit()
    items = SD.attention(db_session, sample_org.id)
    assert items and "did not deliver" in items[0]["reason"]


def test_logs_carry_no_token_or_phone(db_session, sample_org, sample_advisor, on, caplog):
    FakeGateways(on, db_session)
    lead, _ = _visit(db_session, sample_org, sample_advisor)
    with caplog.at_level("INFO"):
        SD.run(db_session, _utc(WALTER_LOCAL) + DUE)
    fu = db_session.query(BookingFollowup).one()
    assert fu.survey_token not in caplog.text and lead.phone not in caplog.text


# ── content and identity ────────────────────────────────────────────────────

def test_message_is_short_and_names_the_business(db_session, sample_org, sample_advisor, on):
    gw = FakeGateways(on, db_session)
    _visit(db_session, sample_org, sample_advisor)
    SD.run(db_session, _utc(WALTER_LOCAL) + DUE)
    t = gw.texts[0]
    assert t.startswith("Hi Walter, thank you for visiting ") and "How did we do?" in t
    assert len(t) <= 160 + 20                                                  # about one segment


def test_program_contact_survey_names_its_location(program, monkeypatch):
    from tests.test_outreach_program import _lead_for
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="rosa", email="rd@example.com")
    name, held = SD.place_for(db, lead)
    assert name == "Alabama Heritage Funeral Home" and held is None
    unresolved = _lead_for(db, org, admin, "L006", phone="2145550106", first="Lee", email="lm@example.com")
    name, held = SD.place_for(db, unresolved)
    assert name is None and "no resolved location" in held


def test_program_survey_page_shows_only_the_locations_own_review_link(client, program):
    from app.models.program_models import LocationProfile
    from tests.test_outreach_program import _lead_for
    db, org, admin = program["db"], program["org"], program["admin"]
    org.google_review_url = "https://g.page/corporate"
    prof = db.query(LocationProfile).filter_by(organization_id=org.id,
                                               official_name="Alabama Heritage Funeral Home").one()
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="rosa", email="rd@example.com")
    bl = BookingLink(lead_id=lead.id, user_id=admin.id)
    db.add(bl)
    db.flush()
    fu = BookingFollowup(booking_link_id=bl.id, lead_id=lead.id, advisor_id=admin.id)
    db.add(fu)
    db.commit()
    body = client.get("/survey/%s/context" % fu.survey_token).json()
    assert body["review_url"] is None and "corporate" not in str(body)
    prof.review_url = "https://g.page/alabama-heritage"
    db.commit()
    assert client.get("/survey/%s/context" % fu.survey_token).json()["review_url"] == "https://g.page/alabama-heritage"


# ── answers ─────────────────────────────────────────────────────────────────

def _token(db, org, advisor):
    lead, bl = _visit(db, org, advisor)
    fu = BookingFollowup(booking_link_id=bl.id, lead_id=lead.id, advisor_id=advisor.id, survey_token="Tok12345abcd")
    db.add(fu)
    db.commit()
    return fu.survey_token


def test_rating_validation_and_single_answer(client, db_session, sample_org, sample_advisor):
    tok = _token(db_session, sample_org, sample_advisor)
    assert client.post("/survey/%s" % tok, json={"rating": 9}).status_code == 422
    assert client.post("/survey/%s" % tok, json={"rating": 4, "feedback": "kind"}).json()["success"] is True
    assert client.post("/survey/%s" % tok, json={"rating": 1}).json()["note"] == "already_submitted"
    assert db_session.query(SurveyResponse).one().rating == 4
    assert client.get("/survey/nope-not-a-token/context").status_code == 404


def test_low_rating_notifies_the_advisor(client, db_session, sample_org, sample_advisor):
    tok = _token(db_session, sample_org, sample_advisor)
    r = client.post("/survey/%s" % tok, json={"rating": 2, "feedback": "waited 40 minutes"})
    assert r.json()["team_notified"] is True
    n = db_session.query(Notification).filter_by(user_id=sample_advisor.id).one()
    assert "2 of 5" in n.message and "waited 40 minutes" in n.message


def test_high_rating_creates_no_alert(client, db_session, sample_org, sample_advisor):
    tok = _token(db_session, sample_org, sample_advisor)
    assert client.post("/survey/%s" % tok, json={"rating": 5}).json()["team_notified"] is False
    assert db_session.query(Notification).count() == 0


def test_review_links_are_the_same_for_every_rating(client, db_session, sample_org, sample_advisor):
    sample_org.google_review_url = "https://g.page/restland"
    db_session.commit()
    tok = _token(db_session, sample_org, sample_advisor)
    ctx = client.get("/survey/%s/context" % tok).json()
    assert ctx["review_url"] == "https://g.page/restland"                       # not tied to a rating
    from app.routers import survey_router
    html = survey_router._review_links_html({"review_url": "https://g.page/restland"})
    assert "If you would like" in html and "Help others find us" not in html


def test_legacy_page_never_shows_thanks_for_an_unsaved_answer():
    import inspect
    from app.routers import survey_router
    src = inspect.getsource(survey_router.get_survey_page)
    assert "if (!res.ok) throw" in src and "catch(e) {{}}" not in src
    assert "will follow up with you shortly" not in src
    assert "success-high" not in src                                            # no rating-gated review page


# ── no production sends from tests ──────────────────────────────────────────

def test_the_real_gateways_are_not_reached(db_session, sample_org, sample_advisor, on, monkeypatch):
    """With the fakes removed, sending still never reaches a real provider in
    tests: the real send_sms path for a sandbox org refuses or raises - and the
    survey is recorded as not sent."""
    import twilio.rest

    def boom(*a, **k):
        raise AssertionError("a real Twilio client was built")
    monkeypatch.setattr(twilio.rest, "Client", boom)
    _visit(db_session, sample_org, sample_advisor, email=None)
    SD.run(db_session, _utc(WALTER_LOCAL) + DUE)
    fu = db_session.query(BookingFollowup).one()
    assert fu.state in ("held", "failed") and fu.survey_link_sent is False


def test_program_low_rating_pages_the_program_team_once(program, monkeypatch):
    from app.services.programs import alert_ledger
    from tests.test_outreach_program import _lead_for
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="rosa", email="rd@example.com")
    calls = []
    monkeypatch.setattr(alert_ledger, "page", lambda db, prog, **k: calls.append(k) or [type("R", (), {"send_state": "sent"})()])
    SimpleResp = SurveyResponse(id="resp-1", booking_followup_id="f", lead_id=lead.id, advisor_id=admin.id,
                                    rating=1, feedback="nobody called back")
    assert SD.notify_low_rating(db, SimpleResp) is True
    assert calls[0]["key"] == "survey:resp-1" and calls[0]["kind"] == "survey_low"
    assert "Alabama Heritage Funeral Home" in calls[0]["sms_text"] and "nobody called back" in calls[0]["sms_text"]
    assert db.query(Notification).count() == 0                                  # the program's own alert, not a second one


# ── review follow-ups ───────────────────────────────────────────────────────

def test_two_retries_at_once_send_once(db_session, sample_org, sample_advisor, on):
    FakeGateways(on, db_session, sms_error="carrier down", email_error="mail down")
    _visit(db_session, sample_org, sample_advisor)
    now = _utc(WALTER_LOCAL) + DUE
    SD.run(db_session, now)
    fu = db_session.query(BookingFollowup).one()
    gw = FakeGateways(on, db_session)
    from sqlalchemy import text as _t
    # A second manager already moved it (same state + attempts they both saw).
    db_session.execute(_t("UPDATE booking_followups SET state='retrying' WHERE id=:i"), {"i": fu.id})
    db_session.commit()
    db_session.refresh(fu)
    out = SD.retry(db_session, fu.id, sample_org.id, now)
    assert out["ok"] is False and gw.texts == []


def test_bookkeeping_failure_after_a_text_never_adds_an_email(db_session, sample_org, sample_advisor, on):
    gw = FakeGateways(on, db_session)
    real = SD._record
    calls = {"n": 0}

    def flaky(db, fu, **k):
        if k.get("state") == "sent" and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("database blip")
        return real(db, fu, **k)
    on.setattr(SD, "_record", flaky)
    _visit(db_session, sample_org, sample_advisor)
    now = _utc(WALTER_LOCAL) + DUE
    SD.run(db_session, now)
    assert len(gw.texts) == 1 and gw.emails == []                                 # never both channels
    fu = db_session.query(BookingFollowup).one()
    assert SD.retry(db_session, fu.id, sample_org.id, now + timedelta(hours=2))["ok"] is False
    assert SD.run(db_session, now)["sent"] == 0 and len(gw.texts) == 1           # no resend


def test_one_bad_booking_does_not_stop_the_others(db_session, sample_org, sample_advisor, on):
    gw = FakeGateways(on, db_session)
    _visit(db_session, sample_org, sample_advisor, first="alpha")
    _visit(db_session, sample_org, sample_advisor, first="beta")
    real = SD.deliver
    seen = {"n": 0}

    def first_breaks(db, fu, booking, lead, advisor, **k):
        seen["n"] += 1
        if seen["n"] == 1:
            raise RuntimeError("boom")
        return real(db, fu, booking, lead, advisor, **k)
    on.setattr(SD, "deliver", first_breaks)
    out = SD.run(db_session, _utc(WALTER_LOCAL) + DUE)
    assert out["sent"] == 1 and len(gw.texts) == 1


def test_unknown_time_zone_goes_to_email_not_skipped_forever(db_session, sample_org, sample_advisor, on):
    from app.services import contact_hours
    on.setattr(contact_hours, "check", lambda lead, now=None: {"permitted": False, "code": contact_hours.ZONE_UNKNOWN})
    gw = FakeGateways(on, db_session)
    _visit(db_session, sample_org, sample_advisor)
    assert SD.run(db_session, _utc(WALTER_LOCAL) + DUE)["sent"] == 1
    assert gw.texts == [] and len(gw.emails) == 1


def test_outside_texting_hours_waits_and_retry_refuses(db_session, sample_org, sample_advisor, on):
    from app.services import contact_hours
    on.setattr(contact_hours, "check", lambda lead, now=None: {"permitted": False, "code": "outside_window"})
    gw = FakeGateways(on, db_session)
    _visit(db_session, sample_org, sample_advisor)
    out = SD.run(db_session, _utc(WALTER_LOCAL) + DUE)
    assert out["skipped_hours"] == 1 and gw.texts == [] and db_session.query(BookingFollowup).count() == 0


def test_survey_does_not_move_the_family_back_in_the_pipeline(db_session, sample_org, sample_advisor, on):
    FakeGateways(on, db_session)
    lead, _ = _visit(db_session, sample_org, sample_advisor)
    from app.services import sms_service
    real = sms_service.send_sms

    def marks_sent(db, advisor, lead, template, **k):
        lead.status = "sent"
        return real(db, advisor, lead, template, **k)
    on.setattr(sms_service, "send_sms", marks_sent)
    SD.run(db_session, _utc(WALTER_LOCAL) + DUE)
    db_session.refresh(lead)
    assert lead.status == "booked"
