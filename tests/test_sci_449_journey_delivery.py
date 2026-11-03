"""#449 - Oct 31 Central Alabama incident: switching the campaign on released
emails to approved families with no text journey, and several delivery
failure notices were never recorded. Covers: no send before a journey is
started, Start / Resume Journey (preview, one start per family, spacing,
resume after pause), the pause seen by a running batch, failure-notice
classification and address flagging (email only - texts unaffected), the
delivery dashboard and address recovery. Synthetic data; providers faked."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import update

from app.models.models import (BookingLink, CadenceState, EmailMessage, Lead, Message, Organization,
                               SuppressionEntry, SuppressionSource)
from app.models.program_models import CampaignFamily, ProgramEmailTouch, ProgramSourceRecord
from app.services import inbound_mailbox_service as inbox
from app.services.programs import bounces as B, campaigns as C, identity, journey
from tests.test_outreach_program import _addresses, _admin, _fake_provider, _h, program  # noqa: F401
from tests.test_sci_441_central_alabama_cadence import _ca_lead, _fam

@pytest.fixture(autouse=True)
def _release_any_time(request, monkeypatch):
    """Most tests here are about WHO starts, not WHEN: let the release window
    cover the whole day so they don't depend on the wall clock. The window
    tests below use the real window."""
    if "window" not in request.node.name and "release_slots" not in request.node.name:
        monkeypatch.setattr(journey, "RELEASE_START", (0, 0))
        monkeypatch.setattr(journey, "RELEASE_END", (23, 59))
        monkeypatch.setattr(journey, "RELEASE_WEEKDAYS_ONLY", False)


# Notice bodies in the shapes Microsoft sent on Oct 31 (addresses synthetic).
NDR_GMAIL = ("Your message to ghost@gmail.com couldn't be delivered. ghost wasn't found at gmail.com. "
             "Status code: 550 5.1.1 Remote server returned '550-5.1.1 The email account that you tried to "
             "reach does not exist.'")
NDR_COMCAST = ("<p><b>resimta.comcast.net rejected your message to the following email addresses:</b></p>"
               "<p>nobody@comcast.net</p>Your message was rejected by the recipient email server. DSN code 5.1.0 "
               "<p><b>resimta.comcast.net gave this error:<br />Not our Customer <br /></b></p>"
               "<p>Remote server returned '550 5.1.0 Not our Customer'</p><p>Original message headers:</p>"
               "<pre>spf=pass dkim=pass dmarc=pass</pre>")
NDR_GODADDY = ("secureserver.net rejected your message to the following email addresses: x@atl.net "
               "Remote server returned '550 5.1.0 <x@atl.net> Recipient not found.'")
NDR_RELAY = ("Your message to typo@atot.com couldn't be delivered. support is not authorized to relay messages "
             "through the server that reported this error. Sender not authorized for relay. "
             "Status code: 550 5.7.367 Error: 550 5.7.367 Remote server returned not permitted to relay -> "
             "554 5.7.1 <typo@atot.com>: Relay access denied")
NDR_EXPIRED = ("Delivery has failed to these recipients or groups: w@act.net Your message wasn't delivered. "
               "Despite repeated attempts to deliver your message, the recipient's email system refused to "
               "accept a connection. Remote server returned '550 5.4.316 Message expired, connection refused'")
NDR_DELAY = "Delivery has been delayed. Remote server returned '451 4.7.500 Server busy. Please try again later'"
NDR_AUTH = "Remote server returned '550 5.7.26 Unauthenticated email is not accepted due to the DMARC policy'"
NDR_SLOW = ("Delivery has been delayed to these recipients or groups: slow@example.com. Remote Server returned "
            "'400 4.4.7 Message delayed'")
NDR_SPAM = "Remote server returned '554 5.7.1 Service unavailable; Client host blocked using zen.spamhaus.org'"


@pytest.mark.parametrize("body,kind,code", [
    (NDR_GMAIL, B.PERMANENT, "5.1.1"), (NDR_COMCAST, B.PERMANENT, "5.1.0"),
    (NDR_GODADDY, B.PERMANENT, "5.1.0"), (NDR_RELAY, B.REJECTED, "5.7.367"),
    (NDR_EXPIRED, B.EXPIRED, "5.4.316"), (NDR_DELAY, B.TEMPORARY, "4.7.500"),
    (NDR_AUTH, B.SENDER_CONFIG, "5.7.26"), (NDR_SLOW, B.TEMPORARY, "4.4.7"), (NDR_SPAM, B.BLOCKED, "5.7.1")])
def test_failure_codes_are_classified_from_the_servers_own_words(body, kind, code):
    v = B.classify(body, "Undeliverable: Your Pre-Planning Guide and Savings Certificate")
    assert v["kind"] == kind and v["code"] == code
    assert v["reason"]


def test_the_shapes_that_were_missed_are_now_recognised():
    for body, addr in ((NDR_COMCAST, "nobody@comcast.net"), (NDR_GODADDY, "x@atl.net")):
        m = {"from": {"emailAddress": {"address": "MicrosoftExchange329e@tenant.onmicrosoft.com",
                                       "name": "Microsoft Outlook"}},
             "subject": "Undeliverable: Your Pre-Planning Guide and Savings Certificate",
             "body": {"content": body}, "toRecipients": [{"emailAddress": {"address": addr}}]}
        assert addr in (inbox.bounce_addresses(m, own={"support@evosyspro.live"}) or [])


# ── helpers ─────────────────────────────────────────────────────────────────

def _ca(db, org, admin, source="L007", phone="2145550107", email="rd@example.com", first="rosa", approve=True):
    lead = _ca_lead(db, org, admin, source=source, phone=phone, email=email, first=first)
    db.query(CadenceState).filter_by(lead_id=lead.id).delete()        # production had no journeys
    if approve:
        for r in db.query(ProgramSourceRecord).filter_by(lead_id=lead.id):
            r.approved_to_send_at, r.on_hold = datetime.utcnow(), False
            # the synthetic file's household-email review is not under test here
            r.duplicate_review_reason, r.needs_data_review = None, False
    db.commit()
    return lead


def _on(db, org):
    fam = _fam(db, org)
    fam.is_active = True
    db.commit()
    return fam


def _email_on(db, org, monkeypatch):
    from app.services.programs import email_touches
    monkeypatch.setenv(email_touches.SENDING_ENV, "on")
    org.from_email = org.from_email or "support@evosyspro.live"
    _addresses(db, org)
    db.commit()
    return email_touches


def _sent_email(db, lead, subject="Your Pre-Planning Guide and Savings Certificate", sender=None, touch=1):
    m = EmailMessage(lead_id=lead.id, sender_id=sender or lead.assigned_to_id, subject=subject,
                     body_html="<p>x</p>", status="sent", sent_at=datetime.utcnow() - timedelta(minutes=2),
                     to_email=lead.email)
    db.add(m)
    db.flush()
    db.add(ProgramEmailTouch(organization_id=lead.organization_id, lead_id=lead.id, campaign_family=C.CENTRAL_ALABAMA_KEY,
                             touch_number=touch, status="sent", attempted_at=m.sent_at, email_message_id=m.id))
    db.commit()
    return m


# ── the Oct 31 cause ────────────────────────────────────────────────────────

def test_switching_on_alone_sends_nothing_on_any_channel(program, sample_advisor, monkeypatch):
    """Approved but not started: the campaign switched on sends no email and no text."""
    from app.services import cadence_service
    db, org, admin = program["db"], program["org"], program["admin"]
    et = _email_on(db, org, monkeypatch)
    _on(db, org)
    lead = _ca(db, org, admin)
    assert identity.send_refusal(db, lead, "cadence") == identity.JOURNEY_NOT_STARTED_REFUSAL
    sent = []
    with _fake_provider(sent):
        r = et.run(db, org.id, force_hours=True)
    assert sent == [] and r["sent"] == 0
    assert db.query(ProgramEmailTouch).filter_by(lead_id=lead.id).count() == 0     # nothing claimed either
    assert db.query(CadenceState).filter_by(lead_id=lead.id).count() == 0
    assert identity.send_refusal(db, lead, "manual") is None                       # a person can still reply


def test_other_campaigns_need_no_journey(program, sample_advisor):
    from tests.test_outreach_program import _lead_for
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    fam = db.query(CampaignFamily).filter_by(organization_id=org.id, key="veteran_planning_guide").one()
    fam.is_active = True
    db.commit()
    assert identity.send_refusal(db, lead, "cadence") is None


def test_a_pause_is_seen_by_the_very_next_send_of_a_running_batch(program):
    db, org, admin = program["db"], program["org"], program["admin"]
    fam = _on(db, org)
    lead = _ca(db, org, admin)
    for r in db.query(ProgramSourceRecord).filter_by(lead_id=lead.id):
        r.journey_started_at = datetime.utcnow()
    db.commit()
    assert identity.send_refusal(db, lead, "cadence") is None
    # The owner pauses from another request while this session still holds the "on" object.
    db.execute(update(CampaignFamily).where(CampaignFamily.id == fam.id).values(is_active=False)
               .execution_options(synchronize_session=False))
    assert fam.is_active is True                                       # stale copy in this session
    assert identity.send_refusal(db, lead, "cadence") == identity.CAMPAIGN_OFF_REFUSAL


# ── Start / Resume Journey ──────────────────────────────────────────────────

def test_preview_is_read_only_and_explains_every_family(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    fam = _on(db, org)
    a = _ca(db, org, admin, source="L007")
    b = _ca(db, org, admin, source="L003", phone="2145550103", email="p@example.com", first="pat")
    c = _ca(db, org, admin, source="L004", phone="2145550104", email="d@example.com", first="dana")
    d = _ca(db, org, admin, source="L001", phone="", email="ollie@example.com", first="ollie")
    db.add(SuppressionEntry(organization_id=org.id, phone="12145550104", reason="STOP", source=SuppressionSource.REPLY_STOP))
    c.status = "dnc"
    _sent_email(db, a)
    before = (db.query(CadenceState).count(), db.query(ProgramEmailTouch).count(),
              [r.journey_started_at for r in db.query(ProgramSourceRecord).all()])
    r = client.get("/program/campaigns/%s/journey" % fam.id, headers=_h(db, admin))
    assert r.status_code == 200, r.text
    imp = r.json()
    rows = {x["lead_id"]: x for x in imp["families"]}
    assert rows[a.id]["action"] == "start" and "Email 1 already went" in rows[a.id]["note"]
    assert rows[c.id]["action"] == "hold" and rows[c.id]["reason"] == "Do not contact"
    assert rows[d.id]["action"] == "start" and rows[d.id]["reason"] == "Email only"
    assert imp["sends_now"] == 0 and imp["email_1_already_sent"] == 1
    assert imp["confirm_phrase"] == "START %d" % imp["will_start"]
    assert (db.query(CadenceState).count(), db.query(ProgramEmailTouch).count(),
            [r.journey_started_at for r in db.query(ProgramSourceRecord).all()]) == before


def test_start_needs_the_campaign_on_and_the_typed_phrase(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    fam = _fam(db, org)
    _ca(db, org, admin)
    r = client.post("/program/campaigns/%s/journey/start" % fam.id, headers=_h(db, admin), json={"confirm": "START 1"})
    assert r.status_code == 422 and "Switch the campaign on first" in r.json()["detail"]
    _on(db, org)
    r = client.post("/program/campaigns/%s/journey/start" % fam.id, headers=_h(db, admin), json={"confirm": "start"})
    assert r.status_code == 422 and "START 1" in r.json()["detail"]
    assert db.query(CadenceState).count() == 0


def test_start_schedules_spaced_firsts_once_and_keeps_places(client, program, sample_advisor):
    db, org, admin = program["db"], program["org"], program["admin"]
    fam = _on(db, org)
    a = _ca(db, org, admin, source="L007")                                           # text + email
    b = _ca(db, org, admin, source="L003", phone="2145550103", email="p@example.com", first="pat")
    e = _ca(db, org, admin, source="L001", phone="", email="ollie@example.com", first="ollie")   # email only
    first = _sent_email(db, a)                                                        # a got email 1 on Oct 31
    sends = (db.query(Message).count(), db.query(EmailMessage).count())
    h = _h(db, admin)
    r = client.post("/program/campaigns/%s/journey/start" % fam.id, headers=h,
                    json={"confirm": "START 2", "count": 2, "spacing_minutes": 10})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["started"] == 2 and out["sent_now"] == 0
    started = {x.lead_id for x in db.query(ProgramSourceRecord).filter(ProgramSourceRecord.journey_started_at.isnot(None))}
    assert len(started) == 2
    states = {s.lead_id: s for s in db.query(CadenceState).all()}
    for lid in started:
        lead = db.get(Lead, lid)
        if lead.phone:
            assert states[lid].current_touch_number == 0 and states[lid].status == "active"
    due = sorted(s.next_touch_due_at for s in states.values())
    if len(due) == 2:
        assert due[1] - due[0] == timedelta(minutes=10)                               # spaced, not at once
    # a second click / a retry after a timeout starts nobody twice
    r2 = client.post("/program/campaigns/%s/journey/start" % fam.id, headers=h, json={"confirm": "START 1"})
    assert r2.status_code == 200 and r2.json()["started"] == 1, r2.text
    r3 = client.post("/program/campaigns/%s/journey/start" % fam.id, headers=h, json={"confirm": "START 1"})
    assert r3.status_code == 422
    assert db.query(CadenceState).count() == len([x for x in (a, b, e) if x.phone])
    # nothing was sent by starting, and email 1 is never repeated
    assert (db.query(Message).count(), db.query(EmailMessage).count()) == sends
    assert db.query(ProgramEmailTouch).filter_by(lead_id=a.id, touch_number=1).count() == 1
    assert db.get(ProgramEmailTouch, db.query(ProgramEmailTouch).filter_by(lead_id=a.id).one().id).email_message_id == first.id


def test_started_family_gets_its_first_text_before_any_email(program, sample_advisor, monkeypatch):
    db, org, admin = program["db"], program["org"], program["admin"]
    et = _email_on(db, org, monkeypatch)
    fam = _on(db, org)
    lead = _ca(db, org, admin)
    journey.start(db, program["prog"], fam, admin, spacing_minutes=0)
    sent = []
    with _fake_provider(sent):
        et.run(db, org.id, force_hours=True)
    assert sent == []                                                  # email waits for text 1
    st = db.query(CadenceState).filter_by(lead_id=lead.id).one()
    st.current_touch_number, st.last_touch_sent_at = 1, datetime.utcnow() - timedelta(hours=5)
    db.commit()
    with _fake_provider(sent):
        et.run(db, org.id, force_hours=True)
    assert len(sent) == 1 and sent[0]["subject"] == "Your Pre-Planning Guide and Savings Certificate"


def test_resume_after_a_pause_respaces_overdue_families(program):
    db, org, admin = program["db"], program["org"], program["admin"]
    fam = _on(db, org)
    a = _ca(db, org, admin, source="L007")
    b = _ca(db, org, admin, source="L003", phone="2145550103", email="p@example.com", first="pat")
    journey.start(db, program["prog"], fam, admin, spacing_minutes=0)
    for s in db.query(CadenceState).all():                             # they reached Text 3, then a long pause
        s.current_touch_number, s.next_touch_due_at = 3, datetime.utcnow() - timedelta(days=2)
    db.commit()
    assert journey.impact(db, program["prog"], fam)["would_resume"] == 2
    out = journey.start(db, program["prog"], fam, admin, spacing_minutes=15)
    assert out["started"] == 0 and out["resumed"] == 2
    st = sorted(db.query(CadenceState).all(), key=lambda s: s.next_touch_due_at)
    assert [s.current_touch_number for s in st] == [3, 3]                # same place
    assert st[1].next_touch_due_at - st[0].next_touch_due_at == timedelta(minutes=15)
    assert st[0].next_touch_due_at >= datetime.utcnow() - timedelta(minutes=1)


def test_held_excluded_and_review_families_are_never_started(program):
    db, org, admin = program["db"], program["org"], program["admin"]
    fam = _on(db, org)
    ok = _ca(db, org, admin, source="L007")
    held = _ca(db, org, admin, source="L003", phone="2145550103", email="p@example.com", first="pat")
    for r in db.query(ProgramSourceRecord).filter_by(lead_id=held.id):
        r.on_hold = True
    excluded = _ca(db, org, admin, source="L004", phone="2145550104", email="d@example.com", first="dana",
                   approve=False)                                       # the 85: never approved here
    review = db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id="L006").one()
    review.campaign_family, review.location_status, review.on_hold = C.CENTRAL_ALABAMA_KEY, "location_review", True
    db.commit()
    journey.start(db, program["prog"], fam, admin, spacing_minutes=0)
    started = {x.lead_id for x in db.query(ProgramSourceRecord).filter(ProgramSourceRecord.journey_started_at.isnot(None))}
    assert started == {ok.id}
    assert db.query(CadenceState).filter(CadenceState.lead_id.in_([held.id, excluded.id])).count() == 0
    assert review.journey_started_at is None


def test_journey_endpoints_are_manager_only_and_tenant_scoped(client, program):
    db, org = program["db"], program["org"]
    fam = _on(db, org)
    other = Organization(name="Other449", slug="other-449", plan="enterprise", industry="generic", is_active=True)
    db.add(other)
    db.commit()
    h = _h(db, _admin(db, other, "other-449@example.com"))
    assert client.get("/program/campaigns/%s/journey" % fam.id, headers=h).status_code == 404
    assert client.post("/program/campaigns/%s/journey/start" % fam.id, headers=h, json={"confirm": "START 1"}).status_code == 404
    assert client.get("/program/central-alabama/delivery", headers=h).status_code == 404


# ── failure notices ─────────────────────────────────────────────────────────

def _notice(db, org, lead, body, subject="Undeliverable: Your Pre-Planning Guide and Savings Certificate"):
    return inbox.record_bounce(db, [org.id], [lead.email.lower()], datetime.utcnow(), body=body, subject=subject)


def test_permanent_bounce_flags_the_address_only_and_keeps_the_reason(program, sample_advisor):
    from app.services.compliance_service import check_compliance_preflight
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _ca(db, org, admin, email="nobody@comcast.net")
    m = _sent_email(db, lead, sender=sample_advisor.id)
    out = _notice(db, org, lead, NDR_COMCAST)
    db.refresh(m)
    db.refresh(lead)
    assert m.status == "bounced" and m.bounce_type == B.PERMANENT and "Not our Customer" in m.error
    assert lead.manual_flag == "bad_email" and "Not our Customer" in lead.manual_flag_reason
    assert "texts unaffected" in out
    assert lead.status != "dnc" and lead.allow_sms is not False
    check_compliance_preflight(db, lead, channel="sms")                # texting still allowed
    with pytest.raises(ValueError):
        check_compliance_preflight(db, lead, channel="email")          # that address is not mailed again
    # the same notice twice changes nothing
    assert "already recorded" in _notice(db, org, lead, NDR_COMCAST)


def test_relay_refusal_is_the_address_not_our_setup(program, sample_advisor):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _ca(db, org, admin, email="typo@atot.com")
    m = _sent_email(db, lead, sender=sample_advisor.id)
    _notice(db, org, lead, NDR_RELAY)
    db.refresh(m)
    db.refresh(lead)
    assert m.bounce_type == B.REJECTED and lead.manual_flag == "bad_email"


def test_sender_setup_and_delays_never_flag_the_family(program, sample_advisor):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _ca(db, org, admin, email="fine@example.com")
    m = _sent_email(db, lead, sender=sample_advisor.id)
    _notice(db, org, lead, NDR_DELAY)
    db.refresh(m)
    db.refresh(lead)
    assert m.status == "soft_bounced" and m.bounce_type == B.TEMPORARY and lead.manual_flag is None
    _notice(db, org, lead, NDR_AUTH)
    db.refresh(m)
    db.refresh(lead)
    assert m.bounce_type == B.SENDER_CONFIG and lead.manual_flag is None
    _notice(db, org, lead, NDR_DELAY)                                  # a late delay never overwrites a final notice
    db.refresh(m)
    assert m.bounce_type == B.SENDER_CONFIG


def test_a_second_expiry_flags_the_address(program, sample_advisor):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _ca(db, org, admin, email="w@act.net")
    _sent_email(db, lead, subject="A time to sit down?", sender=sample_advisor.id)
    _notice(db, org, lead, NDR_EXPIRED, subject="Undeliverable: A time to sit down?")
    db.refresh(lead)
    assert lead.manual_flag is None
    _sent_email(db, lead, subject="Morning or afternoon?", sender=sample_advisor.id, touch=2)
    _notice(db, org, lead, NDR_EXPIRED, subject="Undeliverable: Morning or afternoon?")
    db.refresh(lead)
    assert lead.manual_flag == "bad_email"


def test_a_flagged_address_gets_no_further_email(program, sample_advisor, monkeypatch):
    db, org, admin = program["db"], program["org"], program["admin"]
    et = _email_on(db, org, monkeypatch)
    fam = _on(db, org)
    lead = _ca(db, org, admin, phone="", email="ghost@gmail.com")
    journey.start(db, program["prog"], fam, admin, spacing_minutes=0)
    lead.manual_flag = "bad_email"
    db.commit()
    sent = []
    with _fake_provider(sent):
        et.run(db, org.id, force_hours=True)
    assert sent == []


# ── dashboard, history, recovery ────────────────────────────────────────────

def test_delivery_dashboard_reports_truth_not_approvals(client, program, sample_advisor):
    db, org, admin = program["db"], program["org"], program["admin"]
    _fam(db, org)
    a = _ca(db, org, admin, source="L007", email="nobody@comcast.net")
    b = _ca(db, org, admin, source="L003", phone="2145550103", email="p@example.com", first="pat")
    _ca(db, org, admin, source="L004", phone="2145550104", email="d@example.com", first="dana")
    _sent_email(db, a, sender=sample_advisor.id)
    _sent_email(db, b, sender=sample_advisor.id)
    _notice(db, org, a, NDR_COMCAST)
    d = client.get("/program/central-alabama/delivery", headers=_h(db, admin)).json()
    e = d["email"]
    assert e["provider_accepted"] == 2 and e["permanent_bounce"] == 1 and e["accepted_no_failure_reported"] == 1
    assert e["delivered_confirmed"] is None and "does not report delivery" in e["delivered_note"]
    assert e["not_attempted"] == 1 and e["unknown"] == 0
    assert d["journey"]["approved"] == 3 and d["journey"]["awaiting_start"] == 3 and d["journey"]["started"] == 0
    assert d["sms"]["attempted"] == 0 and d["sms"]["scheduled"] == 0
    roster = client.get("/program/central-alabama/roster", headers=_h(db, admin)).json()
    rows = {r["lead_id"]: r for r in roster["rows"]}
    assert rows[a.id]["status"] == "Email bounced - not reached" and rows[a.id]["email_flagged"]
    assert rows[b.id]["status"] == "Contacted" and roster["contacted"] == 1 and roster["emails_bounced"] == 1
    assert all(r["journey"] == "Not started" for r in roster["rows"])


def test_family_history_and_controlled_recovery(client, program, sample_advisor):
    db, org, admin = program["db"], program["org"], program["admin"]
    _fam(db, org)
    lead = _ca(db, org, admin, email="ghost@gmail.com")
    _sent_email(db, lead, sender=sample_advisor.id)
    _notice(db, org, lead, NDR_GMAIL)
    h = _h(db, admin)
    ev = client.get("/program/central-alabama/family/%s/events" % lead.id, headers=h).json()
    whats = [e["what"] for e in ev["events"]]
    assert "Approved to send" in whats and "Email 1 accepted by Microsoft" in whats
    assert any("Address does not exist" in w for w in whats) and ev["email_flagged"]
    sends = db.query(EmailMessage).count()
    same = client.post("/program/people/%s/email-recovery" % lead.id, headers=h, json={"email": "ghost@gmail.com"})
    assert same.status_code == 422
    ok = client.post("/program/people/%s/email-recovery" % lead.id, headers=h, json={"email": "real@gmail.com"})
    assert ok.status_code == 200 and ok.json()["resent"] is False
    db.refresh(lead)
    assert lead.email == "real@gmail.com" and lead.manual_flag is None
    assert db.query(EmailMessage).count() == sends


def test_spam_blocks_and_delays_never_flag_and_never_count_as_unreached(program, sample_advisor):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _ca(db, org, admin, email="slow@example.com")
    _sent_email(db, lead, sender=sample_advisor.id)
    _notice(db, org, lead, NDR_SLOW)
    _sent_email(db, lead, subject="Second", sender=sample_advisor.id, touch=2)
    _notice(db, org, lead, NDR_SLOW, subject="Undeliverable: Second")
    db.refresh(lead)
    assert lead.manual_flag is None                                    # two delays are not two failures
    other = _ca(db, org, admin, source="L001", phone="2145550101", email="spamfiltered@example.com", first="ollie")
    m = _sent_email(db, other, sender=sample_advisor.id)
    _notice(db, org, other, NDR_SPAM)
    db.refresh(other)
    db.refresh(m)
    assert m.bounce_type == B.BLOCKED and other.manual_flag is None


def test_replies_to_a_family_who_wrote_back_are_not_held_by_the_journey_gate(program):
    db, org, admin = program["db"], program["org"], program["admin"]
    _on(db, org)
    lead = _ca(db, org, admin)
    assert identity.send_refusal(db, lead, "cadence") == identity.JOURNEY_NOT_STARTED_REFUSAL
    for src in ("sci_appointment_setter", "appointment_followup", "manual"):
        assert identity.send_refusal(db, lead, src) is None


def test_the_email_waits_for_the_first_text_at_most_a_day(program, sample_advisor, monkeypatch):
    db, org, admin = program["db"], program["org"], program["admin"]
    et = _email_on(db, org, monkeypatch)
    fam = _on(db, org)
    lead = _ca(db, org, admin)
    journey.start(db, program["prog"], fam, admin, spacing_minutes=0)
    for r in db.query(ProgramSourceRecord).filter_by(lead_id=lead.id):   # the text never could go (quiet hours, ...)
        r.scheduled_start_at = datetime.utcnow() - timedelta(hours=25)
    db.commit()
    sent = []
    with _fake_provider(sent):
        et.run(db, org.id, force_hours=True)
    assert len(sent) == 1


def test_a_quick_off_on_does_not_move_anyone_and_refused_texts_resume_first(client, program):
    """Nov 1, 11:56 PM: off for 16 seconds, on, then START 270 moved all 50
    started families behind the 270. Now only texts actually held back by the
    pause are re-spaced - and first."""
    from app.models.models import CadenceTouchLog
    db, org, admin = program["db"], program["org"], program["admin"]
    fam = _on(db, org)
    a = _ca(db, org, admin, source="L007")
    _ca(db, org, admin, source="L001", phone="", email="ollie@example.com", first="ollie")
    h = _h(db, admin)
    later = (datetime.utcnow() + timedelta(days=1)).isoformat()
    assert client.post("/program/campaigns/%s/journey/start" % fam.id, headers=h,
                       json={"confirm": "START 2", "start_at": later}).status_code == 200
    st = db.query(CadenceState).filter_by(lead_id=a.id).one()
    due_before = st.next_touch_due_at
    client.post("/program/campaigns/%s/activation" % fam.id, headers=h, json={"active": False})
    client.post("/program/campaigns/%s/activation" % fam.id, headers=h, json={"active": True, "confirm": fam.name})
    db.expire_all()
    assert journey.impact(db, program["prog"], fam)["would_resume"] == 0          # nothing was held back
    db.refresh(st)
    assert st.next_touch_due_at == due_before
    # a text the sender refused while the campaign was off IS resumed
    db.add(CadenceTouchLog(cadence_state_id=st.id, lead_id=a.id, organization_id=org.id, touch_number=1,
                           attempt_seq=1, outcome="skipped", reason="Lead x: CAMPAIGN OFF: switched off"))
    st.next_touch_due_at = datetime.utcnow() + timedelta(hours=6)
    db.commit()
    imp = client.get("/program/campaigns/%s/journey" % fam.id, headers=h).json()
    assert imp["would_resume"] == 1 and imp["confirm_phrase"] == "RESUME"
    r = client.post("/program/campaigns/%s/journey/start" % fam.id, headers=h, json={"confirm": "RESUME", "spacing_minutes": 7})
    assert r.status_code == 200 and r.json()["resumed"] == 1 and r.json()["started"] == 0
    db.refresh(st)
    assert st.next_touch_due_at <= datetime.utcnow() + timedelta(minutes=1)        # first in line, not pushed


def test_repair_puts_pushed_first_texts_back_in_front_once(program):
    """The Nov 1 damage, rebuilt: Batch A started (first texts never went,
    moved past a later batch). The repair puts A first, re-spaces the later
    batch behind it, restores a texted family's own next-text time, and only
    ever runs once. Nothing is sent."""
    db, org, admin = program["db"], program["org"], program["admin"]
    fam = _on(db, org)
    now = datetime.utcnow()
    early = _ca(db, org, admin, source="L007")                                   # Batch A, never texted
    texted = _ca(db, org, admin, source="L001", phone="2145550101", email="ollie@example.com", first="ollie")
    later = _ca(db, org, admin, source="L003", phone="2145550103", email="p@example.com", first="pat")
    t0 = now - timedelta(days=2)
    for lead, began, start, due in ((early, t0, now - timedelta(days=1), now + timedelta(days=2)),
                                    (texted, t0 - timedelta(days=1), now - timedelta(days=6), now + timedelta(days=3)),
                                    (later, now - timedelta(days=1), now + timedelta(hours=3), now + timedelta(hours=3))):
        for r in db.query(ProgramSourceRecord).filter_by(lead_id=lead.id):
            r.journey_started_at, r.scheduled_start_at = began, due
        db.add(CadenceState(lead_id=lead.id, status="active", current_touch_number=0,
                            cadence_started_at=start, next_touch_due_at=due))
    db.commit()
    st_t = db.query(CadenceState).filter_by(lead_id=texted.id).one()
    st_t.current_touch_number, st_t.last_touch_sent_at = 2, now - timedelta(days=5)
    db.add(Message(lead_id=texted.id, sender_id=admin.id, body="hi", delivery_status="delivered"))
    db.commit()
    sends = (db.query(Message).count(), db.query(EmailMessage).count())
    out = journey.repair_pushed_starts(db, program["prog"], fam, now=now)
    assert out["pushed"] == 1 and out["respaced"] == 2 and out["followups"] == 1
    se = db.query(CadenceState).filter_by(lead_id=early.id).one()
    sl = db.query(CadenceState).filter_by(lead_id=later.id).one()
    assert se.next_touch_due_at < sl.next_touch_due_at                          # Batch A first
    assert sl.next_touch_due_at - se.next_touch_due_at == timedelta(minutes=5)
    assert se.next_touch_due_at <= now + timedelta(minutes=11)
    db.refresh(st_t)
    assert st_t.next_touch_due_at <= now + timedelta(days=1, minutes=1)        # its own next-text time back (day 7)
    assert (db.query(Message).count(), db.query(EmailMessage).count()) == sends
    assert journey.repair_pushed_starts(db, program["prog"], fam, now=now) == {"skipped": "already done"}


def test_never_started_check_tells_scheduled_from_late(program):
    from app.services.programs import self_check
    db, org, admin = program["db"], program["org"], program["admin"]
    now = datetime.utcnow()
    future = _ca(db, org, admin, source="L007")                                 # starts tomorrow: fine
    moved = _ca(db, org, admin, source="L001", phone="2145550101", email="o@example.com", first="ollie")
    overdue = _ca(db, org, admin, source="L003", phone="2145550103", email="p@example.com", first="pat")
    for lead, start, due in ((future, now + timedelta(days=1), now + timedelta(days=1)),
                             (moved, now - timedelta(days=1), now + timedelta(days=2)),
                             (overdue, now - timedelta(hours=5), now - timedelta(hours=3))):
        db.add(CadenceState(lead_id=lead.id, status="active", current_touch_number=0,
                            cadence_started_at=start, next_touch_due_at=due))
    db.commit()
    out = self_check.texts_never_started(db, org.id, now)
    names = " | ".join(out["people"])
    assert out["ok"] is False and len(out["people"]) == 2
    assert "Ollie Testcase (first text moved" in names and "Pat Testcase (first text due since" in names
    assert "Rosa" not in names


# ── Oct 31, 4:32 PM: "2 people are past their start time" ───────────────────

def test_email_only_family_behind_a_provider_hold_is_held_not_started(program):
    db, org, admin = program["db"], program["org"], program["admin"]
    fam = _on(db, org)
    program["prog"].email_hold_providers = '["outlook", "yahoo"]'
    aol = _ca(db, org, admin, source="L007", phone="", email="someone@aol.com")
    for r in db.query(ProgramSourceRecord).filter_by(lead_id=aol.id):
        r.send_mode = "email_only"
    ok = _ca(db, org, admin, source="L001", phone="", email="someone@gmail.com", first="ollie")
    db.commit()
    rows = {r["lead_id"]: r for r in journey.impact(db, program["prog"], fam)["families"]}
    assert rows[aol.id]["action"] == "hold" and "provider on hold" in rows[aol.id]["reason"]
    assert rows[ok.id]["action"] == "start" and rows[ok.id]["reason"] == "Email only"
    journey.start(db, program["prog"], fam, admin, spacing_minutes=0)
    assert all(r.journey_started_at is None for r in db.query(ProgramSourceRecord).filter_by(lead_id=aol.id))


def test_first_messages_check_does_not_page_for_a_family_held_on_purpose(program, monkeypatch):
    from app.services import contact_hours
    from app.services.programs import self_check
    db, org, admin = program["db"], program["org"], program["admin"]
    program["prog"].email_hold_providers = '["outlook", "yahoo"]'
    held = _ca(db, org, admin, source="L007", phone="", email="someone@msn.com", first="james")
    late = _ca(db, org, admin, source="L001", phone="", email="someone@gmail.com", first="ollie")
    past = datetime.utcnow() - timedelta(minutes=40)
    for lead in (held, late):
        for r in db.query(ProgramSourceRecord).filter_by(lead_id=lead.id):
            r.journey_started_at, r.scheduled_start_at, r.send_mode = past, past, "email_only"
    db.commit()
    monkeypatch.setattr(contact_hours, "is_permitted", lambda lead, now=None: True)
    out = self_check.first_messages_late(db, org.id, datetime.utcnow())
    assert out["people"] == ["Ollie Testcase"]                       # a real miss is still reported


# ── Nov 1: the full campaign releases itself inside a daily window ──────────

def test_release_slots_stay_inside_weekday_window_and_roll_over():
    from zoneinfo import ZoneInfo
    z, u = ZoneInfo("America/Chicago"), ZoneInfo("UTC")
    first = datetime(2026, 11, 2, 17, 5)                      # Mon 11:05 AM CST
    slots = journey.release_slots(first, 245, timedelta(minutes=5))
    local = [t.replace(tzinfo=u).astimezone(z) for t in slots]
    assert len(set(slots)) == 245
    assert local[0].hour == 11 and local[0].minute == 5
    assert all(x.weekday() < 5 and (9, 0) <= (x.hour, x.minute) < (18, 0) for x in local)
    gaps = [(b - a) for a, b in zip(slots, slots[1:])]
    assert all(g == timedelta(minutes=5) or g >= timedelta(hours=14) for g in gaps)   # 5 min, or overnight
    fri = journey.release_slots(datetime(2026, 11, 6, 23, 55), 2, timedelta(minutes=5))   # Fri 5:55 PM
    nxt = fri[1].replace(tzinfo=u).astimezone(z)
    assert (nxt.weekday(), nxt.hour, nxt.minute) == (0, 9, 0)                         # Monday 9:00
    night = journey.release_slots(datetime(2026, 11, 3, 3, 0), 1, timedelta(minutes=5))  # Mon 9 PM
    n0 = night[0].replace(tzinfo=u).astimezone(z)
    assert (n0.day, n0.hour) == (3, 9)                                                # Tue 9:00


def test_starting_everyone_never_schedules_outside_the_window(program):
    from zoneinfo import ZoneInfo
    db, org, admin = program["db"], program["org"], program["admin"]
    fam = _on(db, org)
    _ca(db, org, admin, source="L007")
    _ca(db, org, admin, source="L001", phone="", email="ollie@example.com", first="ollie")
    late = datetime(2026, 11, 3, 23, 58)                          # Tue 5:58 PM CST
    out = journey.start(db, program["prog"], fam, admin, spacing_minutes=5, start_at=late,
                        now=datetime(2026, 11, 3, 23, 0))
    assert out["started"] == 2 and sum(out["by_day"].values()) == 2
    z, u = ZoneInfo("America/Chicago"), ZoneInfo("UTC")
    starts = sorted({r.scheduled_start_at for r in db.query(ProgramSourceRecord)
                     .filter(ProgramSourceRecord.journey_started_at.isnot(None))})
    loc = [t.replace(tzinfo=u).astimezone(z) for t in starts]
    assert (loc[0].hour, loc[0].minute) == (17, 58) and (loc[1].day, loc[1].hour, loc[1].minute) == (4, 9, 0)
