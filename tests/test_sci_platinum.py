"""SCI overnight hardening (2026-10-06): message brain, quality gate, five
distinct touches, identity lock + on-behalf audit, response timing, staging
cleanup and the booking_links migration fix. Synthetic data only."""
from datetime import datetime, timedelta
from unittest.mock import patch

from app.models.models import AuditLogEntry, EmailMessage, Lead
from app.models.program_models import ProgramEmailTouch
from app.services.programs import identity, message_brain as brain, responses
from tests.test_outreach_program import (  # noqa: F401 - fixtures
    _fake_provider, _god, _h, _lead_for, _touch_ready, program,
)


def _packet(**over):
    p = {"first_name": "Ollie", "kerry": "Kerry Allan", "location": "Eastern Gate Memorial Gardens",
         "campaign_family": "veteran_planning_guide", "campaign_active": True, "sender_healthy": True,
         "display_name": "Kerry Allan | Eastern Gate Memorial Gardens", "responses": [],
         "on_hold": False, "in_review": False, "suppressed": False, "appointment_booked": False,
         "postal": "Eastern Gate Memorial Gardens, 1 Test Way, Testville, FL 32500"}
    p.update(over)
    return p


# ── message brain ────────────────────────────────────────────────────────────

def test_every_touch_has_a_different_strategy_and_passes_quality():
    bodies = []
    for t in (1, 2, 3, 4, 5, brain.REACTIVATION):
        m = brain.compose(_packet(), t, "email", flyer_line="View your guide: https://x.example/g")
        q = brain.quality(_packet(), m, prior_bodies=bodies)
        assert q["ok"], (t, q)
        assert "Kerry Allan" in m["body"] and "Eastern Gate Memorial Gardens" in m["body"]
        assert "checking in" not in m["body"].lower()
        bodies.append(m["body"])
    first = brain.compose(_packet(), 1, "email")["body"]
    assert "Veteran Planning Guide" in first          # says WHY they are hearing from us


def test_sms_touches_pass_and_stay_short():
    for t in (1, 2, 3, 4, 5):
        m = brain.compose(_packet(), t, "sms")
        q = brain.quality(_packet(), m)
        assert q["ok"], (t, q) and len(m["body"]) <= 320


def test_quality_gate_holds_invented_facts_and_pressure():
    bad = {"touch": 1, "channel": "email", "subject": "Hi",
           "body": ("Hi Ollie, this is Kerry Allan with Eastern Gate Memorial Gardens. Thank you for your "
                    "service! As a veteran you are eligible for a free burial plot - act now, this is a "
                    "limited time offer for $0. Just reply. Kerry Allan Eastern Gate Memorial Gardens")}
    q = brain.quality(_packet(), bad)
    assert not q["ok"]
    joined = " ".join(q["failures"])
    for why in ("assumes veteran status", "asserts eligibility", "promises something free",
                "pressure language", "names a price"):
        assert why in joined


def test_quality_gate_holds_wrong_identity_location_and_robotic_copy():
    m = brain.compose(_packet(), 2, "email")
    q = brain.quality(_packet(kerry="Michael"), m)
    assert any("identify Michael" in f for f in q["failures"])
    m2 = dict(m, body=m["body"] + "\nWe also serve Bayview Memorial Park.")
    q2 = brain.quality(_packet(), m2, other_locations=["Bayview Memorial Park", "Eastern Gate Memorial Gardens"])
    assert any("different location" in f for f in q2["failures"])
    m3 = dict(m, body="Hi Ollie, just checking in. Please do not hesitate to reach out. Kerry Allan, "
                      "Eastern Gate Memorial Gardens. Reply here.")
    assert any("robotic" in f for f in brain.quality(_packet(), m3)["failures"])
    q4 = brain.quality(_packet(), brain.compose(_packet(), 1, "email"),
                       expected_display_name="Michael | Eastern Gate Memorial Gardens")
    assert any("display name" in f for f in q4["failures"])


def test_quality_gate_blocks_on_hold_review_booked_and_replied():
    m = brain.compose(_packet(), 1, "email")
    for over, needle in ((dict(on_hold=True), "ON HOLD"), (dict(in_review=True), "review"),
                         (dict(appointment_booked=True), "appointment"), (dict(responses=[{"class": "hot"}]), "replied"),
                         (dict(location=None), "no resolved location"), (dict(suppressed=True), "suppressed"),
                         (dict(campaign_active=False), "not switched on")):
        q = brain.quality(_packet(**over), m)
        assert not q["ok"] and any(needle in f for f in q["failures"]), over


def test_a_location_named_inside_another_is_not_a_false_alarm():
    p = _packet(location="Pine Crest Cemetery West")
    m = brain.compose(p, 2, "email")
    q = brain.quality(p, m, other_locations=["Pine Crest Cemetery", "Pine Crest Cemetery West"])
    assert q["ok"], q


def test_near_duplicate_of_an_earlier_message_is_held():
    m = brain.compose(_packet(), 3, "email")
    q = brain.quality(_packet(), m, prior_bodies=[m["body"]])
    assert any("repeats an earlier message" in f for f in q["failures"])


def test_context_packet_is_built_from_verified_data(program, sample_advisor):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    p = brain.context_packet(db, lead)
    assert p["source_lead_id"] == "L001" and p["location"] == "Eastern Gate Memorial Gardens"
    assert p["kerry"] == "Kerry Allan" and p["campaign_family"] == "veteran_planning_guide"
    assert p["display_name"] == "Kerry Allan | Eastern Gate Memorial Gardens"
    assert p["on_hold"] is False and p["appointment_booked"] is False and p["responses"] == []


def test_five_touches_go_out_with_different_copy_then_stop(program, sample_advisor, monkeypatch):
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    sent = []
    now = datetime.utcnow()
    with _fake_provider(sent):
        for i in range(8):
            et.run(db, org.id, now=now + timedelta(days=5 * i), force_hours=True)
            # stamp each sent touch at the simulated time so spacing is honoured
            for t in db.query(ProgramEmailTouch).filter_by(lead_id=lead.id):
                if t.status == "sent" and t.attempted_at > now + timedelta(days=5 * i):
                    t.attempted_at = now + timedelta(days=5 * i)
            db.commit()
    rows = db.query(ProgramEmailTouch).filter_by(lead_id=lead.id).order_by(ProgramEmailTouch.touch_number).all()
    assert [t.touch_number for t in rows] == [1, 2, 3, 4, 5] and all(t.status == "sent" for t in rows)
    assert len(sent) == 5 and len({s["subject"] for s in sent}) == 5
    for a in range(5):
        for b in range(a + 1, 5):
            assert brain.similarity(sent[a]["body_html"], sent[b]["body_html"]) < 0.5


def test_an_edited_family_copy_that_breaks_the_rules_is_held_not_sent(program, sample_advisor, monkeypatch):
    from app.models.program_models import CampaignFamily
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    fam = db.query(CampaignFamily).filter_by(organization_id=org.id, key="veteran_planning_guide").one()
    fam.email_body_template = ("Hi {first_name}, thank you for your service. You qualify for a free burial "
                               "plot. Call us today! {primary_contact_name} {location_name}")
    db.commit()
    sent = []
    with _fake_provider(sent):
        r = et.run(db, org.id, force_hours=True)
    t = db.query(ProgramEmailTouch).filter_by(lead_id=lead.id).one()
    assert sent == [] and r["held_quality"] == 1
    assert t.status == "blocked" and t.reason.startswith("held by quality check")
    dry = et.run(db, org.id, dry_run=True, now=datetime.utcnow() + timedelta(days=2))
    assert dry["would_send"] and dry["would_send"][0]["quality_ok"] is False


# ── response intelligence ───────────────────────────────────────────────────

def test_hot_appointment_vs_active_information_vs_low_thanks(program, sample_advisor):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(False, "test")):
        r = responses.on_inbound(db, lead, "Yes, can we set up an appointment next week?", "email")
    assert r.response_class == "hot" and r.urgency == "HOT" and r.sla_due_at is not None
    assert "appointment_intent" in r.intents and r.cadence_paused
    lead2 = _lead_for(db, org, sample_advisor, "L002", phone="2145550102", email="b@example.com")
    r2 = responses.on_inbound(db, lead2, "Could you send me more information about cremation?", "email")
    assert r2.response_class == "active" and r2.urgency == "ACTIVE" and r2.sla_due_at is None
    assert "cremation_interest" in r2.intents and r2.suggested_reply


def test_only_kerry_opening_a_response_stops_the_sla_clock(client, program, sample_advisor):
    db, org, prog = program["db"], program["org"], program["prog"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(False, "test")):
        r = responses.on_inbound(db, lead, "Please call me to book a visit", "sms")
    admin = program["admin"]
    h = _h(db, admin)
    client.post("/program/responses/%s/viewed" % r.id, headers=h)
    db.refresh(r)
    assert r.handling_status == "new"             # a manager looking does not stop the clock
    prog.primary_contact_user_id = admin.id
    db.commit()
    client.post("/program/responses/%s/viewed" % r.id, headers=h)
    db.refresh(r)
    assert r.handling_status == "opened" and r.opened_by == admin.id
    items = client.get("/program/responses", headers=h).json()["items"]
    assert items[0]["urgency"] == "HOT" and items[0]["minutes_to_open"] is not None


# ── identity lock + on-behalf audit ─────────────────────────────────────────

def test_identity_is_locked_to_kerry(client, program):
    db, prog, admin = program["db"], program["prog"], program["admin"]
    h = _h(db, admin)
    r = client.patch("/program/settings", headers=h, json={"primary_contact_name": "Michael"})
    assert r.status_code == 409
    assert client.patch("/program/settings", headers=h, json={"customer_identity_locked": False}).status_code == 403
    from app.models.program_models import LocationProfile
    p = db.query(LocationProfile).filter_by(organization_id=prog.organization_id,
                                            official_name="Eastern Gate Memorial Gardens").one()
    bad = client.patch("/program/locations/%s" % p.id, headers=h, json={"email_display_name": "Michael | Eastern Gate"})
    assert bad.status_code == 409
    ok = client.patch("/program/locations/%s" % p.id, headers=h,
                      json={"email_display_name": "Kerry Allan | Eastern Gate Memorial Gardens"})
    assert ok.status_code == 200
    p.email_display_name = "Michael Smith"        # even if written behind the API's back
    db.commit()
    assert identity.display_name(prog, p) == "Kerry Allan | Eastern Gate Memorial Gardens"


def test_a_manager_send_is_audited_as_on_behalf_of_kerry(program, sample_advisor):
    db, org = program["db"], program["org"]
    org.from_email = "support@evosyspro.live"
    lead = _lead_for(db, org, sample_advisor, "L001")
    from app.services.email_service import send_email_to_lead
    sent = []
    with _fake_provider(sent):
        msg = send_email_to_lead(db, sample_advisor, lead, subject="Hello", body_html="<p>Hi</p>",
                                 send_source="manual", sent_by_user_id=sample_advisor.id)
    assert sent[0]["org"].from_name == "Kerry Allan | Eastern Gate Memorial Gardens"
    a = db.query(AuditLogEntry).filter_by(action="program.sent_on_behalf", target_id=lead.id).one()
    assert a.actor_user_id == sample_advisor.id
    assert "on behalf of Kerry Allan / Eastern Gate Memorial Gardens" in (a.note or "")
    assert isinstance(msg, EmailMessage)


# ── staging cleanup + migrations ────────────────────────────────────────────

def test_simulation_contacts_are_removed_and_nothing_else(client, db_session, monkeypatch):
    from app.models.models import Platform
    monkeypatch.setenv("STAGING_TEST_HARNESS", "on")
    if not db_session.query(Platform).filter_by(slug="evosyspro").first():
        db_session.add(Platform(id="plt-evosyspro-t2", name="EvoSys Pro", slug="evosyspro",
                                support_email="support@evosyspro.live"))
        db_session.commit()
    h = _h(db_session, _god(db_session))
    s = client.post("/god/staging/sci/seed", headers=h, json={"test_email": "tester@example.net",
                                                              "simulation_contacts": True}).json()
    assert s["contacts_in_workspace"] == 6
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(False, "sim")):
        pass
    out = client.post("/god/staging/sci/remove-simulation-contacts", headers=h).json()
    assert out["contacts"] == 5 and out["contacts_in_workspace"] == ["tester@example.net"]
    assert db_session.query(Lead).filter(Lead.email.like("sim.%@example.com")).count() == 0


def test_booking_links_indexes_only_name_real_columns():
    from app.auto_migrate import INDEXES_TO_CREATE
    from app.models.models import BookingLink
    cols = set(BookingLink.__table__.columns.keys())
    import re
    for stmt in INDEXES_TO_CREATE:
        stmt = stmt if isinstance(stmt, str) else stmt[0]
        m = re.search(r"ON\s+booking_links\s*\(([^)]*)\)", stmt)
        if m:
            for c in m.group(1).split(","):
                assert c.strip().split()[0] in cols, stmt


# ── location numbers: inbound SMS ────────────────────────────────────────────

def _location_number(db, org, name="Eastern Gate Memorial Gardens", e164="+12055550199"):
    from app.models.program_models import LocationProfile
    from app.models.telephony_models import PhoneNumber
    prof = db.query(LocationProfile).filter_by(organization_id=org.id, official_name=name).one()
    db.add(PhoneNumber(e164=e164, organization_id=org.id, workspace_id=prof.location_id, cap_sms=True,
                       cap_voice_inbound=True, label=name))
    db.commit()
    return prof


def test_a_reply_to_a_location_number_reaches_the_contact_at_that_location(program, sample_advisor,
                                                                             twilio_webhook):
    from app.models.program_models import ProgramResponse
    db, org = program["db"], program["org"]
    prof = _location_number(db, org)
    lead = _lead_for(db, org, sample_advisor, "L001", phone="2145550101")
    with patch("app.services.sms_service.Client") as tw, \
            patch("app.services.programs.responses._deliver_staff_alert", return_value=(False, "test")):
        r = twilio_webhook("/sms/webhook/inbound", data={
            "From": "+12145550101", "To": "+12055550199", "Body": "Can I come by Tuesday to visit?",
            "MessageSid": "SMloc1"})
        assert r.status_code == 200
        tw.assert_not_called()
    resp = db.query(ProgramResponse).filter_by(lead_id=lead.id).one()
    assert resp.location_id == prof.location_id and resp.reply_to_alias == "+12055550199"
    assert resp.response_class == "hot" and resp.cadence_paused


def test_an_unknown_texter_to_a_location_number_is_kept_for_review_not_attached(program, twilio_webhook):
    from app.models.program_models import ProgramResponse, ProgramUnmatchedReply
    db, org = program["db"], program["org"]
    prof = _location_number(db, org)
    r = twilio_webhook("/sms/webhook/inbound", data={
        "From": "+13345550000", "To": "+12055550199", "Body": "Who is this? I got your letter",
        "MessageSid": "SMloc2"})
    assert r.status_code == 200
    u = db.query(ProgramUnmatchedReply).filter_by(organization_id=org.id).one()
    assert u.location_id == prof.location_id and u.from_address == "+13345550000"
    assert db.query(ProgramResponse).count() == 0
    # Twilio retries the same message: kept once
    twilio_webhook("/sms/webhook/inbound", data={
        "From": "+13345550000", "To": "+12055550199", "Body": "Who is this? I got your letter",
        "MessageSid": "SMloc2"})
    assert db.query(ProgramUnmatchedReply).filter_by(organization_id=org.id).count() == 1


# ── workspace "manager" role (Michael's SCI access on his existing login) ────

def test_a_manager_works_the_program_but_not_the_workspace_admin(client, program, db_session):
    from app.models.models import Organization, User
    from app.services.workspace_access import grant_workspace_membership, WORKSPACE_ROLES
    from tests.test_outreach_program import hash_password
    db, org = program["db"], program["org"]
    assert "manager" in WORKSPACE_ROLES
    home = Organization(name="Michael Home Co", slug="michael-home-co")
    db.add(home)
    db.commit()
    mgr = User(organization_id=home.id, email="michael.mgr@example.com", password_hash=hash_password("Pass12345!"),
               full_name="Michael Manager", role="advisor", is_active=True, must_change_password=False)
    db.add(mgr)
    db.commit()
    grant_workspace_membership(db, mgr.id, org.id, role="manager", check_capacity=False)
    users_before = db.query(User).count()
    h = dict(_h(db, mgr), **{"X-Workspace-Id": org.id})
    assert client.get("/program/responses", headers=h).status_code == 200      # works the program
    assert client.get("/program/health", headers=h).status_code == 200
    assert client.get("/admin/users", headers=h).status_code == 403             # not user admin
    assert client.get("/org-settings/twilio", headers=h).status_code == 403      # not credentials
    assert client.patch("/program/settings", headers=h, json={"customer_identity_locked": False}).status_code == 403
    assert db.query(User).count() == users_before                               # no account created


# ── deliverability: postal footer, placement checks, SLA cap ────────────────

def test_no_postal_address_holds_automated_email(program, sample_advisor, monkeypatch):
    from app.models.location_models import Location
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    for loc in db.query(Location).filter_by(organization_id=org.id):
        loc.address_line1 = None
    db.commit()
    sent = []
    with _fake_provider(sent):
        r = et.run(db, org.id, force_hours=True)
    t = db.query(ProgramEmailTouch).filter_by(lead_id=lead.id).one()
    assert sent == [] and r["held_quality"] == 1 and "postal address" in t.reason


def test_program_email_footer_carries_the_location_postal_address(program, sample_advisor, monkeypatch):
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    sent = []
    with _fake_provider(sent):
        et.run(db, org.id, force_hours=True)
    assert "1 Test Way" in sent[0]["body_html"] and "/email/unsubscribe/" in sent[0]["body_html"]


def test_placement_checks_send_the_real_first_touch_to_seeds_and_gate_readiness(client, program, sample_advisor,
                                                                              monkeypatch):
    from app.services.programs import placement
    db, org, prog, admin = program["db"], program["org"], program["prog"], program["admin"]
    org.from_email = "support@evosyspro.live"
    from tests.test_outreach_program import _addresses
    _addresses(db, org)
    db.commit()
    h = _h(db, admin)
    assert client.patch("/program/settings", headers=h, json={"placement_seed_addresses": {
        "gmail": "seed.g@example.com", "outlook": "seed.o@example.com",
        "yahoo": "seed.y@example.com", "icloud": "seed.i@example.com"}}).status_code == 200
    sent = []
    with _fake_provider(sent):
        r = client.post("/program/placement-checks/send", headers=h, json={})
    assert r.status_code == 200 and len(sent) == 4
    assert {s["to_email"] for s in sent} == {"seed.g@example.com", "seed.o@example.com",
                                             "seed.y@example.com", "seed.i@example.com"}
    assert all("Kerry Allan" in s["body_html"] and "List-Unsubscribe" in s["headers"] for s in sent)
    assert sent[0]["org"].from_name.startswith("Kerry Allan | ")
    checks = r.json()["checks"]
    assert placement.readiness_item(db, prog)["ok"] is False                  # nobody has looked yet
    for c in checks:
        folder = "promotions" if c["provider"] == "gmail" else "inbox"
        assert client.post("/program/placement-checks/%s/result" % c["id"], headers=h,
                           json={"folder": folder}).status_code == 200
    item = placement.readiness_item(db, prog)
    assert item["ok"] is False and "gmail: promotions" in item["detail"]
    gm = next(c for c in checks if c["provider"] == "gmail")
    client.post("/program/placement-checks/%s/result" % gm["id"], headers=h, json={"folder": "primary"})
    assert placement.readiness_item(db, prog)["ok"] is True
    assert client.post("/program/placement-checks/%s/result" % gm["id"], headers=h,
                       json={"folder": "somewhere"}).status_code == 422


def test_hot_sla_re_alerts_are_capped(program, sample_advisor, monkeypatch):
    db, org = program["db"], program["org"]
    monkeypatch.setenv("PROGRAM_SLA_MAX_REALERTS", "2")
    lead = _lead_for(db, org, sample_advisor, "L001")
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(False, "test")):
        r = responses.on_inbound(db, lead, "Can I book a visit this week?", "sms")
        t = r.received_at
        for i in range(6):
            responses.sla_sweep(db, now=t + timedelta(minutes=16 * (i + 1)))
    db.refresh(r)
    assert r.sla_alert_count == 2
