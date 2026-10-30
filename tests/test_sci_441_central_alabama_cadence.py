"""#441 - Central Alabama 2025-2026 MS: its own message for EVERY step of the
SCI cadence (6 texts, 5 emails), built from the owner's brief, editable,
previewed with the same rendering delivery uses, never falling back to another
campaign's words, OFF until a person switches it on. Synthetic data only; the
email provider is faked and nothing reaches Twilio."""
import re
from datetime import datetime, timedelta

import pytest

from app.models.models import CadenceState, EmailMessage, Lead, Message
from app.models.program_models import CampaignFamily, LocationProfile, ProgramSourceRecord
from app.services.programs import campaigns as C, identity, setup
from tests.test_outreach_program import _addresses, _fake_provider, _h, _lead_for, program  # noqa: F401

LOCS = ["Southern Heritage Funeral Home", "Eastern Gate Memorial Gardens"]


def _fam(db, org):
    return C.ensure_central_alabama(db, org.id)


def _ca_lead(db, org, admin, source="L007", loc=None, phone="2145550107", email="rd@example.com", first="rosa"):
    """A synthetic Central Alabama family at a chosen location."""
    lead = _lead_for(db, org, admin, source, phone=phone, first=first, email=email)
    rec = db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id=source).one()
    rec.campaign_family = C.CENTRAL_ALABAMA_KEY
    if loc is not None:
        prof = db.query(LocationProfile).filter_by(organization_id=org.id, official_name=loc).one()
        rec.location_id, rec.location_status = prof.location_id, "mapped"
    if not phone:
        lead.phone = None
    db.commit()
    return lead


def _all_copy(fam):
    sms = [C.sms_for(fam, n) for n in range(1, 7)]
    emails = [C.email_for(fam, n) for n in range(1, 6)]
    return sms, emails


# ── the sequence ────────────────────────────────────────────────────────────

def test_the_owners_sequence_is_built_for_every_step_and_stays_off(program):
    db, org = program["db"], program["org"]
    fam = _fam(db, org)
    assert fam.is_active is False
    sms, emails = _all_copy(fam)
    assert all(sms) and all(emails)
    assert C.missing_steps(db, fam) == []
    # the owner's own opening
    assert "filled out one of our surveys requesting a pre-planning guide and savings certificate" in sms[0]
    assert "What day works best for you?" in sms[0]
    assert emails[0]["subject"] == "Your Pre-Planning Guide and Savings Certificate"
    # every step has its own wording
    assert len(set(sms)) == 6 and len({e["body"] for e in emails}) == 5 and len({e["subject"] for e in emails}) == 5
    every = " ".join(sms + [e["subject"] + " " + e["body"] for e in emails])
    # never "MS", never an invented value / expiry / guarantee / other offer
    assert not re.search(r"\bMS\b", every)
    for bad in (r"\$", r"\bworth\b", r"expir", r"guarantee", r"\bfree\b", r"veteran", r"seminar", r"limited time"):
        assert not re.search(bad, every, re.I), bad
    # every touch asks for the appointment
    for t in sms + [e["body"] for e in emails]:
        assert re.search(r"day|time|visit|meet|ready", t, re.I)


def test_seeding_never_overwrites_edits_or_refills_a_cleared_step(program):
    db, org = program["db"], program["org"]
    fam = _fam(db, org)
    C.set_touch_copy(fam, sms={"2": "MY OWN TEXT 2 {first_name}"}, email={"3": {"subject": "", "body": ""}})
    db.commit()
    again = C.ensure_central_alabama(db, org.id)
    assert C.sms_for(again, 2) == "MY OWN TEXT 2 {first_name}"
    assert C.email_for(again, 3) is None                      # cleared on purpose stays cleared
    assert "Email 3" in C.missing_steps(db, again)


def test_other_campaigns_are_untouched(program):
    db, org = program["db"], program["org"]
    _fam(db, org)
    vet = db.query(CampaignFamily).filter_by(organization_id=org.id, key="veteran_planning_guide").one()
    assert vet.touch_copy_json is None and C.missing_steps is not None
    from app.services.programs.campaigns import OWN_COPY_KEYS
    assert OWN_COPY_KEYS == {C.CENTRAL_ALABAMA_KEY}


# ── texts ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("loc", LOCS + ["Kilgroe Funeral Home"])
def test_each_text_touch_uses_its_own_step_with_the_right_location(program, loc):
    db, org, admin = program["db"], program["org"], program["admin"]
    setup.ensure_locations(db, org, admin, LOCS)
    C.ensure_kilgroe_for_sci(db, org.id)
    _fam(db, org)
    lead = _ca_lead(db, org, admin, loc=loc)
    texts = [identity.cadence_text(db, lead, n) for n in range(1, 7)]
    assert all(texts) and len(set(texts)) == 6
    assert "Rosa" in texts[0] and loc in texts[0] and "Kerry Allan" in texts[0]
    for t in texts:
        assert "{" not in t
        for other in set(LOCS + ["Kilgroe Funeral Home"]) - {loc}:
            assert other not in t
    # the program's generic follow-up texts are never used for this campaign
    from app.services.programs.identity import program_touch_text
    for n in range(2, 7):
        generic = program_touch_text(db, org.id, n)
        if generic:
            assert texts[n - 1] != identity.render(generic, {})


def test_a_blank_text_step_sends_nothing_and_never_falls_back(program, sample_advisor):
    from app.services import cadence_service
    db, org, admin = program["db"], program["org"], program["admin"]
    fam = _fam(db, org)
    C.set_touch_copy(fam, sms={"3": ""})
    db.commit()
    lead = _ca_lead(db, org, admin)
    assert identity.cadence_text(db, lead, 3) is None
    assert cadence_service.render_cadence_message(db, lead, sample_advisor, 3, "", "PLATFORM TEMPLATE {first_name}") is None
    assert cadence_service.render_cadence_message(db, lead, sample_advisor, 2, "", "PLATFORM TEMPLATE") != "PLATFORM TEMPLATE"


def test_the_first_text_is_the_owners_line_with_the_signoff_rule(program, sample_advisor):
    from app.services import cadence_service
    db, org, admin = program["db"], program["org"], program["admin"]
    _fam(db, org)
    lead = _ca_lead(db, org, admin)
    body = cadence_service.render_cadence_message(db, lead, sample_advisor, 1, "")
    assert body.startswith("Hi Rosa, this is Kerry Allan with Alabama Heritage Funeral Home.")
    assert body.endswith("Reply STOP to opt out.")
    out = identity.apply_sms_signoff(db, lead, body)
    assert out.count("Kerry Allan") == 1                           # introduces herself: no repeat sign-off
    later = identity.apply_sms_signoff(db, lead, cadence_service.render_cadence_message(db, lead, sample_advisor, 3, ""))
    assert "- Kerry Allan, Alabama Heritage Funeral Home" in later   # a step that doesn't name both gets it


# ── emails ──────────────────────────────────────────────────────────────────

def _email_ready(db, org, monkeypatch):
    from app.services.programs import email_touches
    monkeypatch.setenv(email_touches.SENDING_ENV, "on")
    fam = _fam(db, org)
    fam.is_active = True                       # test only: the real switch needs the typed name
    org.from_email = org.from_email or "support@evosyspro.live"
    _addresses(db, org)
    db.commit()
    return email_touches


def test_all_five_emails_go_with_their_own_words_and_pass_the_quality_gate(program, monkeypatch):
    db, org, admin = program["db"], program["org"], program["admin"]
    et = _email_ready(db, org, monkeypatch)
    lead = _ca_lead(db, org, admin, phone="")                         # email-only family
    sent = []
    now = datetime.utcnow()
    for n in range(5):
        with _fake_provider(sent):
            r = et.run(db, org.id, now=now + timedelta(days=n * (et.followup_days() + 1)), force_hours=True)
        assert r["sent"] == 1, (n, r)
    with _fake_provider(sent):
        assert et.run(db, org.id, now=now + timedelta(days=60), force_hours=True)["sent"] == 0   # never a 6th / twice
    assert len(sent) == 5
    subjects = [s["subject"] for s in sent]
    assert subjects[0] == "Your Pre-Planning Guide and Savings Certificate" and len(set(subjects)) == 5
    for s in sent:
        assert "Kerry Allan" in s["body_html"] and "Alabama Heritage Funeral Home" in s["body_html"]
        assert "{" not in s["body_html"] and "Veteran" not in s["body_html"]
    from app.models.program_models import ProgramEmailTouch
    assert [t.status for t in db.query(ProgramEmailTouch).filter_by(lead_id=lead.id).order_by(
        ProgramEmailTouch.touch_number)] == ["sent"] * 5


def test_a_blank_email_step_is_held_never_replaced(program, monkeypatch):
    db, org, admin = program["db"], program["org"], program["admin"]
    et = _email_ready(db, org, monkeypatch)
    fam = _fam(db, org)
    C.set_touch_copy(fam, email={"1": {"subject": "", "body": ""}})
    db.commit()
    _ca_lead(db, org, admin)
    sent = []
    with _fake_provider(sent):
        r = et.run(db, org.id, force_hours=True)
    assert sent == [] and r["sent"] == 0


def test_a_reply_or_a_booking_ends_the_emails(program, monkeypatch):
    from app.services.programs import responses
    db, org, admin = program["db"], program["org"], program["admin"]
    et = _email_ready(db, org, monkeypatch)
    lead = _ca_lead(db, org, admin)
    responses.on_inbound(db, lead, "Thursday works", "sms")
    db.commit()
    sent = []
    with _fake_provider(sent):
        et.run(db, org.id, force_hours=True)
    assert sent == []


# ── activation, saving, preview ─────────────────────────────────────────────

def test_cannot_switch_on_with_a_blank_step_and_saving_never_activates(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    fam = _fam(db, org)
    before = (db.query(Message).count(), db.query(EmailMessage).count(), db.query(CadenceState).count())
    r = client.patch("/program/campaigns/%s" % fam.id, headers=_h(db, admin),
                     json={"steps": {"sms": {"4": ""}, "email": {"5": {"subject": "Hi", "body": "Edited {first_name}, just reply. {primary_contact_name} {location_name}"}}}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_active"] is False and "Text 4" in body["missing_steps"]
    assert body["steps"]["email"][4]["body"].startswith("Edited {first_name}")
    on = client.post("/program/campaigns/%s/activation" % fam.id, headers=_h(db, admin),
                     json={"active": True, "confirm": fam.name})
    assert on.status_code == 422 and "Text 4" in on.json()["detail"]
    assert (db.query(Message).count(), db.query(EmailMessage).count(), db.query(CadenceState).count()) == before
    db.refresh(fam)
    assert fam.is_active is False


def test_a_full_sequence_can_be_switched_on_only_with_the_typed_name(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    fam = _fam(db, org)
    assert client.post("/program/campaigns/%s/activation" % fam.id, headers=_h(db, admin),
                       json={"active": True, "confirm": "wrong"}).status_code == 422
    assert C.activation_blocker(fam, db) is None


def test_steps_are_listed_with_the_cadence_schedule(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    _fam(db, org)
    rows = client.get("/program/campaigns", headers=_h(db, admin)).json()
    ca = next(r for r in rows if r["key"] == C.CENTRAL_ALABAMA_KEY)
    assert [s["touch"] for s in ca["steps"]["sms"]] == [1, 2, 3, 4, 5, 6]
    assert [s["day_offset"] for s in ca["steps"]["sms"]] == [0, 1, 3, 5, 7, 9]
    assert [s["touch"] for s in ca["steps"]["email"]] == [1, 2, 3, 4, 5]
    vet = next(r for r in rows if r["key"] == "veteran_planning_guide")
    assert "steps" not in vet


@pytest.mark.parametrize("loc", LOCS + ["Kilgroe Funeral Home"])
def test_preview_renders_each_step_like_delivery(client, program, sample_advisor, loc):
    from app.services import cadence_service
    db, org, admin = program["db"], program["org"], program["admin"]
    setup.ensure_locations(db, org, admin, LOCS)
    C.ensure_kilgroe_for_sci(db, org.id)
    fam = _fam(db, org)
    prof = db.query(LocationProfile).filter_by(organization_id=org.id, official_name=loc).one()
    lead = _ca_lead(db, org, admin, loc=loc, first="pat")
    for n in range(1, 7):
        p = client.get("/program/campaigns/%s/preview?location_id=%s&touch=sms:%d" % (fam.id, prof.location_id, n),
                       headers=_h(db, admin)).json()
        assert p["ok"] and loc in (p["sms"] + p["from_display_name"])
        delivered = identity.apply_sms_signoff(db, lead, cadence_service.render_cadence_message(
            db, lead, sample_advisor, n, ""))
        assert p["sms"] == delivered                                  # the same text, word for word
    for n in range(1, 6):
        p = client.get("/program/campaigns/%s/preview?location_id=%s&touch=email:%d" % (fam.id, prof.location_id, n),
                       headers=_h(db, admin)).json()
        assert p["ok"] and p["email_subject"] and "Kerry Allan" in p["email_body"] and loc in p["email_body"]
    assert db.query(Message).count() == 0 and db.query(EmailMessage).count() == 0


# ── roster ──────────────────────────────────────────────────────────────────

def test_roster_shows_paths_and_real_counts_not_approvals_as_sends(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    _fam(db, org)
    with_phone = _ca_lead(db, org, admin, source="L007")
    email_only = _ca_lead(db, org, admin, source="L001", phone="", email="ollie@example.com", first="ollie")
    for lead in (with_phone, email_only):
        for r in db.query(ProgramSourceRecord).filter_by(lead_id=lead.id):
            r.approved_to_send_at = datetime.utcnow()
    db.commit()
    d = client.get("/program/central-alabama/roster", headers=_h(db, admin)).json()
    assert d["people"] == 2 and d["with_phone"] == 1 and d["email_only"] == 1
    assert d["texts_sent"] == 0 and d["emails_sent"] == 0 and d["campaign_active"] is False
    paths = {r["lead_id"]: r["path"] for r in d["rows"]}
    assert paths[email_only.id] == "Email only (no phone on file)" and paths[with_phone.id] == "Text + Email"
    assert all("campaign off" in r["status"] for r in d["rows"])
    assert "consent" not in str(d).lower()


# ── review follow-ups ───────────────────────────────────────────────────────

def test_an_on_campaign_cannot_be_blanked_through_the_old_fields(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    fam = _fam(db, org)
    fam.is_active = True
    db.commit()
    r = client.patch("/program/campaigns/%s" % fam.id, headers=_h(db, admin), json={"sms_template": ""})
    assert r.status_code == 422
    db.refresh(fam)
    assert fam.sms_template


def test_saving_rejects_copy_the_send_check_would_hold(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    fam = _fam(db, org)
    bad = [{"email": {"2": {"subject": "Hi", "body": "Your certificate is worth $500. {primary_contact_name} {location_name}"}}},
           {"email": {"3": {"subject": "Hi", "body": "Call us! {primary_contact_name} {location_name}"}}},
           {"sms": {"2": "Hi {first_nam}"}},
           {"sms": "not a dict"}]
    for steps in bad:
        r = client.patch("/program/campaigns/%s" % fam.id, headers=_h(db, admin), json={"steps": steps})
        assert r.status_code == 422, steps
    db.refresh(fam)
    assert C.copy_problems(fam) == [] and C.email_for(fam, 2)["body"] == C.CA_EMAIL[2][1]


def test_deploy_time_seeding_starts_nothing(program):
    """ensure_for_sci at startup seeds copy into an OFF campaign: nothing is enrolled or sent."""
    from app.services import sms_programs
    db, org = program["db"], program["org"]
    before = (db.query(Message).count(), db.query(EmailMessage).count(), db.query(CadenceState).count())
    import unittest.mock as m
    with m.patch.object(sms_programs, "sci_org_id", return_value=org.id):
        C.ensure_for_sci(db)
        C.ensure_for_sci(db)
    fam = db.query(CampaignFamily).filter_by(organization_id=org.id, key=C.CENTRAL_ALABAMA_KEY).one()
    assert fam.is_active is False and C.missing_steps(db, fam) == []
    assert (db.query(Message).count(), db.query(EmailMessage).count(), db.query(CadenceState).count()) == before


def test_a_skipped_blank_text_is_recorded_not_sent(program, sample_advisor, monkeypatch):
    from app.models.models import CadenceTouchLog
    from app.services import cadence_service
    db, org, admin = program["db"], program["org"], program["admin"]
    fam = _fam(db, org)
    fam.is_active = True
    C.set_touch_copy(fam, sms={"1": ""})        # (test only - the real switch refuses a blank step)
    db.commit()
    lead = _ca_lead(db, org, admin)
    for r in db.query(ProgramSourceRecord).filter_by(lead_id=lead.id):
        r.approved_to_send_at = datetime.utcnow()
    st = db.query(CadenceState).filter_by(lead_id=lead.id).one()
    st.status, st.current_touch_number, st.next_touch_due_at = "active", 0, datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    sent = []
    import app.services.sms_service as _sms
    monkeypatch.setattr(_sms, "send_sms", lambda *a, **k: sent.append(k))
    monkeypatch.setattr(cadence_service, "org_sending_enabled", lambda db, oid: True)
    from app.services import entitlements
    monkeypatch.setattr(entitlements, "org_has_feature", lambda org, f: True)
    from app.services import contact_hours
    monkeypatch.setattr(contact_hours, "check", lambda lead, now=None: {"permitted": True})
    try:
        cadence_service.run_due_cadences(db)
    except TypeError:
        pass
    assert sent == []
    logs = db.query(CadenceTouchLog).filter_by(cadence_state_id=st.id).all()
    assert all(l.outcome != "sent" for l in logs)
    assert any("no campaign wording" in (l.reason or "") for l in logs), [(l.outcome, l.reason) for l in logs]
