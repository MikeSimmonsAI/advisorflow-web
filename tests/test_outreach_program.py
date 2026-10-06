"""LOCATION OUTREACH PROGRAMS (SCI) - configuration, staging, identity, replies.

All data here is synthetic. Nothing reaches Twilio or Resend: send paths are
asserted to REFUSE before a provider, or a provider function is patched.
"""
import io
import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.models.models import CadenceState, Lead, Notification, Organization, User
from app.models.program_models import (
    CampaignFamily, OutreachProgram, ProgramAlert, ProgramAsset, ProgramResponse,
    ProgramSourceRecord,
)
from app.services.auth_service import create_access_token, hash_password
from app.services.programs import identity, importer, responses, setup

ORG_NUMBER = "+19998887777"   # sample_org's own sending number (tests/conftest.py)

HEADER = ("Lead ID,Lead Owner: Manager,Lead Owner,First Name,Last Name,Email,Lead Status,"
          "Last Activity,Last Activity Date,Phone,Primary Campaign,Campaign Channel,Create Date,"
          "Location Friendly Name\n")


def _row(lid, first, last, email, phone, loc, status="Qualified",
         campaign="Direct Mail> Veteran> Veteran Planning Guide"):
    return "%s,Mgr,Owner,%s,%s,%s,%s,,1/5/2026,%s,%s,Direct Mail,12/27/2025,%s\n" % (
        lid, first, last, email, status, phone, campaign, loc)


CSV = (HEADER
       + _row("L001", "OLLIE", "TESTCASE", "ollie@example.com", "2145550101", "Eastern Gate Memorial Gardens")
       + _row("L002", "Ollie", "Testcase", "other@example.com", "2145550101", "Eastern Gate Memorial Gardens")  # same person+phone
       + _row("L003", "PAT", "SAMPLETON", "shared@example.com", "2145550103", "Striffler-Hamby Mortuary")
       + _row("L004", "DANA", "SAMPLETON", "shared@example.com", "2145550104", "Striffler-Hamby Mortuary")  # household email
       + _row("L005", "Chris", "Fakename- NOT INTERESTED DISQUALIFIED", "cb@example.com",
              "2145550105", "Alabama Heritage Funeral Home", status="Open",
              campaign="Direct Mail> Survey> General")
       + _row("L006", "LEE", "NOWHERE", "lm@example.com", "2145550106", "", status="Unworked")
       + _row("L007", "ROSA", "DIAZ", "rd@example.com", "2145550107", "Alabama Heritage Funeral Home",
              campaign="Direct Mail> Veterans> English with Spanish Callouts"))
LOCATIONS = ["Eastern Gate Memorial Gardens", "Striffler-Hamby Mortuary", "Alabama Heritage Funeral Home"]


def _admin(db, org, email="sci-admin@example.com"):
    u = User(organization_id=org.id, email=email, password_hash=hash_password("Pass12345!"),
             full_name="SCI Admin", role="org_admin", is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, user):
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


@pytest.fixture()
def program(db_session, sample_org):
    db = db_session
    admin = _admin(db, sample_org)
    prog = setup.ensure_program(db, sample_org, name="Service Corporation International",
                                primary_contact_name="Kerry Allan",
                                primary_contact_title="Head of Sales")
    profiles = setup.ensure_locations(db, sample_org, admin, LOCATIONS)
    setup.ensure_campaign_families(db, sample_org)
    db.commit()
    res = importer.stage(db, sample_org, CSV.encode(), filename="sci.csv", dry_run=False)
    return {"db": db, "org": sample_org, "admin": admin, "prog": prog, "profiles": profiles,
            "stage": res}


def _lead_for(db, org, advisor, source_lead_id, phone="2145550101", first="Ollie", last="Testcase",
              email="ollie@example.com"):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id, first_name=first,
                last_name=last, phone="1" + phone, email=email, status="sent")
    db.add(lead)
    db.flush()
    rec = db.query(ProgramSourceRecord).filter_by(organization_id=org.id,
                                                  source_lead_id=source_lead_id).one()
    rec.lead_id = lead.id
    db.add(CadenceState(lead_id=lead.id, status="active"))
    db.commit()
    return lead


# ── setup and staging ────────────────────────────────────────────────────────

def test_setup_is_idempotent_and_invents_nothing(program):
    db, org, admin = program["db"], program["org"], program["admin"]
    again = setup.ensure_locations(db, org, admin, LOCATIONS)
    setup.ensure_campaign_families(db, org)
    db.commit()
    from app.models.program_models import LocationProfile
    profs = db.query(LocationProfile).filter_by(organization_id=org.id).all()
    assert len(profs) == 4 and sum(p.is_review_bucket for p in profs) == 1   # 3 homes + review bucket
    assert all(p.website is None and p.manager_name is None for p in profs)   # nothing invented
    fams = db.query(CampaignFamily).filter_by(organization_id=org.id).all()
    assert len(fams) == len(setup.DEFAULT_FAMILIES) and not any(f.is_active for f in fams)
    for f in fams:   # families ask for replies, never calls
        assert "call" not in (f.sms_template + f.email_body_template).lower()
    assert len(again) == 4


def test_staging_keeps_every_id_and_applies_the_household_rule(program):
    db, org = program["db"], program["org"]
    s = program["stage"]["summary"]
    assert s["source_rows"] == 7 and s["distinct_lead_ids"] == 7 and s["staged"] == 7
    assert s["location_mapped"] == 6 and s["location_review"] == 1
    recs = {r.source_lead_id: r for r in db.query(ProgramSourceRecord).filter_by(organization_id=org.id)}
    assert len(recs) == 7
    # same normalised name + same phone -> linked under one master, both kept
    assert recs["L001"].link_status == "primary" and recs["L002"].link_status == "linked"
    assert recs["L002"].contact_master_key == "L001"
    # same email, DIFFERENT names (household) -> review, never merged
    assert recs["L003"].contact_master_key != recs["L004"].contact_master_key
    assert "same email" in (recs["L003"].duplicate_review_reason or "")
    # name-field notes are flagged; the status is NOT changed because of them
    assert json.loads(recs["L005"].data_note_flags) == ["NOT INTERESTED", "DISQUALIFIED"]
    assert recs["L005"].needs_data_review and recs["L005"].source_status == "Open"
    assert recs["L005"].last_name == "Fakename- NOT INTERESTED DISQUALIFIED"   # original untouched
    # blank location -> review bucket, never guessed
    assert recs["L006"].location_status == "location_review"
    assert recs["L007"].campaign_family == "veteran_spanish"
    assert json.loads(recs["L001"].raw_json)["Lead ID"] == "L001"


def test_restaging_updates_decisions_never_originals(program):
    db, org = program["db"], program["org"]
    edited = CSV.replace("OLLIE,TESTCASE", "CHANGED,NAME")
    importer.stage(db, org, edited.encode(), dry_run=False)
    rec = db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id="L001").one()
    assert rec.first_name == "OLLIE" and "OLLIE" in rec.raw_json
    assert db.query(ProgramSourceRecord).filter_by(organization_id=org.id).count() == 7


def test_dry_run_writes_no_staging_rows_and_bad_ids_are_refused(db_session, sample_org):
    admin = _admin(db_session, sample_org, "dry@example.com")
    setup.ensure_program(db_session, sample_org, name="P")
    setup.ensure_locations(db_session, sample_org, admin, LOCATIONS)
    db_session.commit()
    res = importer.stage(db_session, sample_org, CSV.encode(), dry_run=True)
    assert res["summary"]["staged"] == 0
    assert db_session.query(ProgramSourceRecord).count() == 0
    dup = CSV + _row("L001", "X", "Y", "x@example.com", "2145559999", "Striffler-Hamby Mortuary")
    res = importer.stage(db_session, sample_org, dup.encode(), dry_run=False)
    assert res["summary"]["errors"] and res["summary"]["staged"] == 0
    assert db_session.query(ProgramSourceRecord).count() == 0


# ── identity and the send gate ───────────────────────────────────────────────

def test_location_identity_and_sms_signoff(program, sample_advisor):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    ctx = identity.context_for(db, lead, "email")
    assert ctx["ok"] and ctx["display_name"] == "Kerry Allan | Eastern Gate Memorial Gardens"
    from app.services.sms_service import compose_body
    body = compose_body("Hi {first_name}, quick note.", lead, sample_advisor, "")
    assert "- Kerry Allan, Eastern Gate Memorial Gardens" in body
    assert body.rstrip().endswith("Reply STOP to opt out.")
    assert body.index("Kerry Allan") < body.index("Reply STOP")


def test_no_location_means_no_send(program, sample_advisor):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L006", phone="2145550106", first="Lee",
                     last="Nowhere", email="lm@example.com")
    from app.services import sms_service, email_service
    with patch("app.services.sms_service.Client") as tw:
        with pytest.raises(ValueError) as e:
            sms_service.send_sms(db, sample_advisor, lead, "Hello")
        assert "LOCATION REVIEW" in str(e.value)
        tw.assert_not_called()
    with patch("app.services.email_service.send_email_via_provider") as send:
        with pytest.raises(ValueError):
            email_service.send_email_to_lead(db, sample_advisor, lead, subject="Hi", body_html="<p>x</p>")
        send.assert_not_called()


def test_email_from_name_is_contact_at_location(program, sample_advisor):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    seen = {}

    def fake_send(**kw):
        seen["from_name"] = getattr(kw.get("org"), "from_name", None)
        return {"success": True, "provider_message_id": "x"}
    with patch("app.services.email_service.send_email_via_provider", side_effect=fake_send):
        from app.services import email_service
        email_service.send_email_to_lead(db, sample_advisor, lead, subject="Hi", body_html="<p>x</p>",
                                         send_source="manual")
    assert seen["from_name"] == "Kerry Allan | Eastern Gate Memorial Gardens"


def test_organizations_without_a_program_are_untouched(db_session, sample_org, sample_advisor):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="A",
                last_name="B", phone="2145550199", status="new")
    db_session.add(lead)
    db_session.commit()
    assert identity.send_refusal(db_session, lead) is None
    from app.services.sms_service import compose_body, _compose_body_core
    assert compose_body("Hi there", lead, sample_advisor, "") == \
        _compose_body_core("Hi there", lead, sample_advisor, "")
    assert responses.on_inbound(db_session, lead, "yes please", "sms") is None


# ── inbound replies, alerts, SLA ─────────────────────────────────────────────

@pytest.mark.parametrize("text,cls", [
    ("Can I stop by Tuesday?", "hot"),                               # "stop" is not STOP
    ("Not interested in cremation but how much is a plot?", "hot"),  # a stated need wins
    ("My husband passed away, can we set an appointment?", "hot"),   # bereavement is never bad data
    ("My mother passed away last month", "hot"),
    ("STOP", "opt_out"),
    ("Please remove me from your list", "opt_out"),
    ("Yes I'd like an appointment next week", "hot"),
    ("How much does it cost?", "hot"),
    ("Please send me more information", "active"),   # ACTIVE + Information Request (2026-10-06)
    ("What hours are you open on Saturday?", "active"),
    ("ok thanks", "low"),
    ("Not interested, remove me", "opt_out"),
    ("Wrong number, I don't know who this is", "bad_data"),
])
def test_classification(text, cls):
    assert responses.classify(text)["class"] == cls


def test_hot_sms_reply_pauses_alerts_and_starts_the_sla(program, sample_advisor, twilio_webhook):
    db, org, prog = program["db"], program["org"], program["prog"]
    prog.alert_phone = "+12145550000"        # configured, but staff alerts are OFF
    prog.management_recipients = json.dumps([{"name": "Mgr", "phone": "+12145550001"}])
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001", phone="2145550101")
    with patch("app.services.sms_service.Client") as tw:
        r = twilio_webhook("/sms/webhook/inbound", data={
            "From": "+12145550101", "To": ORG_NUMBER, "Body": "Yes, can we set up a visit?",
            "MessageSid": "SMprog1"})
        assert r.status_code == 200, r.text[:300]
        tw.assert_not_called()                   # nothing sent to anyone
    resp = db.query(ProgramResponse).filter_by(lead_id=lead.id).one()
    assert resp.response_class == "hot" and resp.handling_status == "new"
    assert resp.source_lead_id == "L001" and resp.campaign_family == "veteran_planning_guide"
    assert resp.sla_due_at and 14 <= (resp.sla_due_at - resp.received_at).total_seconds() / 60 <= 15
    st = db.query(CadenceState).filter_by(lead_id=lead.id).one()
    assert str(getattr(st.status, "value", st.status)) != "active"
    alerts = db.query(ProgramAlert).filter_by(response_id=resp.id).all()
    in_app = [a for a in alerts if a.channel == "in_app"]
    external = [a for a in alerts if a.channel != "in_app"]
    assert in_app and all(a.delivered for a in in_app)
    assert {a.audience for a in external} == {"primary", "management"}
    assert not any(a.delivered for a in external)
    assert all("switched off" in a.reason or "no " in a.reason for a in external)
    assert db.query(Notification).filter(Notification.lead_id == lead.id).count() >= 1


def test_sla_realerts_once_per_window_and_stops_when_opened(program, sample_advisor):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    t0 = datetime.utcnow()
    resp = responses.on_inbound(db, lead, "I'm interested", "sms", now=t0)
    assert responses.sla_sweep(db, now=t0 + timedelta(minutes=10)) == []
    hit = responses.sla_sweep(db, now=t0 + timedelta(minutes=16))
    assert [r.id for r in hit] == [resp.id]
    breach = db.query(ProgramAlert).filter_by(response_id=resp.id, kind="sla_breach").all()
    assert breach and "NOT YET HANDLED" in breach[0].message
    assert responses.sla_sweep(db, now=t0 + timedelta(minutes=20)) == []   # same window
    responses.mark(db, resp, "opened", program["admin"])
    assert responses.sla_sweep(db, now=t0 + timedelta(minutes=60)) == []
    assert resp.opened_at and resp.handling_status == "opened"


def test_opt_out_and_bad_data(program, sample_advisor):
    db, org = program["db"], program["org"]
    a = _lead_for(db, org, sample_advisor, "L003", phone="2145550103", first="Pat",
                  last="Sampleton", email="shared@example.com")
    r = responses.on_inbound(db, a, "please stop, not interested", "email")
    assert r.response_class == "opt_out" and r.handling_status == "closed"
    b = _lead_for(db, org, sample_advisor, "L004", phone="2145550104", first="Dana",
                  last="Sampleton", email="shared@example.com")
    r = responses.on_inbound(db, b, "wrong person", "sms")
    rec = db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id="L004").one()
    assert r.response_class == "wrong_person" and rec.needs_data_review


# ── API: dashboard, permissions, assets, preview ─────────────────────────────

def test_dashboard_and_location_filter(client, program, auth_headers):
    db = program["db"]
    h = _h(db, program["admin"])
    d = client.get("/program/dashboard", headers=h).json()
    assert d["metrics"]["leads"] == 7 and d["metrics"]["contacts"] == 6
    assert d["metrics"]["locations"] == 3 and d["metrics"]["qualified"] == 5
    assert d["attention"]["location_review"] == 1 and d["attention"]["data_review"] == 1
    assert d["attention"]["duplicate_review"] == 2
    eg = next(l for l in d["locations"] if l["name"] == "Eastern Gate Memorial Gardens")
    d2 = client.get("/program/dashboard?location_id=%s" % eg["location_id"], headers=h).json()
    assert d2["metrics"]["leads"] == 2 and d2["selected_location"]["email_display_name"] == \
        "Kerry Allan | Eastern Gate Memorial Gardens"
    # an advisor in the same org sees none of it (every family's details) and
    # configures nothing; the nav entry is not offered to them
    assert client.get("/program/dashboard", headers=auth_headers).status_code == 403
    assert client.get("/program/records", headers=auth_headers).status_code == 403
    assert client.get("/program/responses", headers=auth_headers).status_code == 403
    assert client.get("/program/status", headers=auth_headers).json()["active"] is False
    assert client.get("/program/status", headers=h).json()["active"] is True
    assert client.patch("/program/settings", json={"hot_sla_minutes": 5},
                        headers=auth_headers).status_code == 403


def test_other_tenants_see_nothing(client, program, db_session):
    other = Organization(name="Other", slug="other-prog", plan="enterprise", industry="generic",
                         is_active=True)
    db_session.add(other)
    db_session.commit()
    u = _admin(db_session, other, "other-admin@example.com")
    h = _h(db_session, u)
    assert client.get("/program/dashboard", headers=h).status_code == 404
    assert client.get("/program/records", headers=h).status_code == 404
    rec = db_session.query(ProgramSourceRecord).first()
    assert client.get("/program/records/%s/verification" % rec.id, headers=h).status_code == 404


def test_settings_reject_call_language_and_store_recipients(client, program):
    h = _h(program["db"], program["admin"])
    r = client.patch("/program/settings", json={"reply_instructions_sms": "Call us at 555"}, headers=h)
    assert r.status_code == 422
    r = client.patch("/program/settings", json={
        "hot_sla_minutes": 20, "management_recipients": [{"name": "Ops", "email": "ops@example.com"}]},
        headers=h)
    assert r.status_code == 200 and r.json()["hot_sla_minutes"] == 20
    assert r.json()["management_recipients"][0]["email"] == "ops@example.com"
    assert r.json()["alert_phone"] is None            # never filled in for us


def test_assets_version_activate_and_host(client, program):
    h = _h(program["db"], program["admin"])
    pdf = b"%PDF-1.4 test flyer v1"

    def up(data, activate):
        return client.post("/program/assets", headers=h, data={
            "kind": "flyer", "title": "Veteran Planning Guide", "category": "veteran_planning_guide",
            "activate": "true" if activate else "false"},
            files={"file": ("guide.pdf", io.BytesIO(data), "application/pdf")})
    a1 = up(pdf, False).json()
    assert a1["version"] == 1 and not a1["is_active"] and a1["hosted_url"] is None
    a2 = up(pdf + b" v2", True).json()
    assert a2["version"] == 2 and a2["is_active"]
    tok = a2["hosted_url"].rsplit("/", 1)[1]
    assert client.get("/program-assets/%s" % tok).content == pdf + b" v2"     # public, active only
    client.post("/program/assets/%s/active" % a2["id"], json={"active": False}, headers=h)
    assert client.get("/program-assets/%s" % tok).status_code == 404
    bad = client.post("/program/assets", headers=h, data={"kind": "flyer", "title": "x"},
                      files={"file": ("x.exe", io.BytesIO(b"MZ"), "application/x-msdownload")})
    assert bad.status_code == 415


def test_campaign_preview_is_location_aware_and_uses_the_flyer(client, program):
    db = program["db"]
    h = _h(db, program["admin"])
    client.post("/program/assets", headers=h, data={
        "kind": "flyer", "title": "Veteran Planning Guide", "category": "veteran_planning_guide",
        "activate": "true"}, files={"file": ("g.pdf", io.BytesIO(b"%PDF-1.4 g"), "application/pdf")})
    fam = db.query(CampaignFamily).filter_by(organization_id=program["org"].id,
                                             key="veteran_planning_guide").one()
    rec = db.query(ProgramSourceRecord).filter_by(source_lead_id="L001").one()
    p = client.get("/program/campaigns/%s/preview?record_id=%s" % (fam.id, rec.id), headers=h).json()
    assert p["ok"] and p["from_display_name"] == "Kerry Allan | Eastern Gate Memorial Gardens"
    assert "Eastern Gate Memorial Gardens" in p["sms"] and "/program-assets/" in p["email_body"]
    assert "call" not in (p["sms"] + p["email_body"]).lower()
    p2 = client.get("/program/campaigns/%s/preview?record_id=%s&touch=followup" % (fam.id, rec.id),
                    headers=h).json()
    assert p2["email_mode"] == "attached" and p2["attachment"]
    review = db.query(ProgramSourceRecord).filter_by(source_lead_id="L006").one()
    p3 = client.get("/program/campaigns/%s/preview?record_id=%s" % (fam.id, review.id), headers=h).json()
    assert p3["ok"] is False and "LOCATION REVIEW" in p3["reason"]


def test_verification_is_stored_beside_the_original(client, program):
    db = program["db"]
    h = _h(db, program["admin"])
    rec = db.query(ProgramSourceRecord).filter_by(source_lead_id="L001").one()
    r = client.post("/program/records/%s/verification" % rec.id, headers=h,
                    json={"verified_phone": "2145559999", "phone_line_type": "mobile", "provider": "manual"})
    assert r.status_code == 200 and r.json()["outreach_eligible"] is False
    v = client.get("/program/records/%s/verification" % rec.id, headers=h).json()
    assert v["original"]["phone"] == "2145550101" and v["verified"]["verified_phone"] == "2145559999"
    db.refresh(rec)
    assert rec.phone == "2145550101"


def test_location_review_can_be_resolved_by_hand(client, program):
    db = program["db"]
    h = _h(db, program["admin"])
    rec = db.query(ProgramSourceRecord).filter_by(source_lead_id="L006").one()
    loc = client.get("/program/locations", headers=h).json()[0]
    r = client.post("/program/records/%s/location" % rec.id, json={"location_id": loc["location_id"]},
                    headers=h)
    assert r.status_code == 200 and r.json()["location_status"] == "mapped"
    db.refresh(rec)
    assert rec.source_location_name == ""          # the source value is kept as supplied


def test_god_setup_is_owner_only(client, program, auth_headers):
    r = client.post("/god/programs/setup", json={"organization_id": program["org"].id, "name": "x"},
                    headers=auth_headers)
    assert r.status_code in (401, 403)


# ── promotion (tomorrow's apply step) and the review holds ───────────────────

def test_promotion_one_lead_per_contact_master_and_no_consent(program):
    from app.models.intake_models import OrgContactSourceId
    from app.services.programs import promote
    db, org = program["db"], program["org"]
    dry = promote.promote(db, org, apply=False)
    assert dry["records"] == 7 and dry["masters"] == 6 and dry["to_create"] == 6 and not dry["applied"]
    assert db.query(Lead).filter(Lead.organization_id == org.id).count() == 0
    res = promote.promote(db, org, apply=True)
    assert res["created"] == 6
    recs = {r.source_lead_id: r for r in db.query(ProgramSourceRecord).filter_by(organization_id=org.id)}
    assert all(r.lead_id for r in recs.values())
    assert recs["L001"].lead_id == recs["L002"].lead_id            # linked, not deleted
    assert recs["L003"].lead_id != recs["L004"].lead_id            # household: two people
    ids = {s.source_record_id for s in db.query(OrgContactSourceId).filter_by(organization_id=org.id)}
    assert ids == set(recs)                                         # every source Lead ID traceable
    lead = db.query(Lead).filter(Lead.id == recs["L001"].lead_id).one()
    assert lead.sms_consent is False and lead.allow_sms is None and lead.status == "new"
    assert promote.promote(db, org, apply=True)["created"] == 0    # idempotent


def test_review_holds_block_sends_until_cleared(client, program, sample_advisor):
    from app.services.programs import promote
    db, org = program["db"], program["org"]
    promote.promote(db, org, apply=True)
    recs = {r.source_lead_id: r for r in db.query(ProgramSourceRecord).filter_by(organization_id=org.id)}
    lead_note = db.query(Lead).filter(Lead.id == recs["L005"].lead_id).one()
    lead_dup = db.query(Lead).filter(Lead.id == recs["L003"].lead_id).one()
    assert identity.send_refusal(db, lead_note) == identity.DATA_REVIEW_REFUSAL
    assert identity.send_refusal(db, lead_dup) == identity.DUPLICATE_REVIEW_REFUSAL
    h = _h(db, program["admin"])
    r = client.post("/program/records/%s/review" % recs["L005"].id, json={"review": "data_review"}, headers=h)
    assert r.status_code == 200 and r.json()["needs_data_review"] is False
    db.expire_all()
    assert identity.send_refusal(db, lead_note, "manual") is None
    assert json.loads(db.query(ProgramSourceRecord).get(recs["L005"].id).data_note_flags)  # flags kept
    r = client.post("/program/records/%s/review" % recs["L003"].id, json={"review": "duplicate_review"}, headers=h)
    assert r.status_code == 200
    db.expire_all()
    assert identity.send_refusal(db, lead_dup, "manual") is None


def test_readiness_flags_a_shared_reply_number(client, program, db_session):
    org = program["org"]
    h = _h(db_session, program["admin"])
    items = {i["key"]: i for i in client.get("/program/dashboard", headers=h).json()["readiness"]["items"]}
    assert {"email_sender", "sms_number", "sms_reply_routing", "email_reply_mailbox",
            "primary_contact_user", "alert_recipients", "outbound_brake"} <= set(items)
    assert items["alert_recipients"]["ok"] is False             # blank until supplied
    other = Organization(name="Shares Number", slug="shares-num", plan="enterprise",
                         industry="generic", is_active=True,
                         org_twilio_phone_number=org.org_twilio_phone_number)
    db_session.add(other)
    db_session.commit()
    items = {i["key"]: i for i in client.get("/program/dashboard", headers=h).json()["readiness"]["items"]}
    if org.org_twilio_phone_number:
        assert items["sms_reply_routing"]["ok"] is False and "other" in items["sms_reply_routing"]["detail"]


def test_program_email_carries_unsubscribe(program, sample_advisor):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    seen = {}

    def fake_send(**kw):
        seen.update(kw)
        return {"success": True, "provider_message_id": "x"}
    with patch("app.services.email_service.send_email_via_provider", side_effect=fake_send):
        from app.services import email_service
        email_service.send_email_to_lead(db, sample_advisor, lead, subject="Hi", body_html="<p>x</p>",
                                         send_source="manual")
    assert "/email/unsubscribe/" in seen["body_html"]
    assert seen["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_cadence_touches_use_the_location_aware_family(program, sample_advisor):
    from app.services.cadence_service import render_cadence_message
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    t1 = render_cadence_message(db, lead, sample_advisor, 1, "")
    assert "Eastern Gate Memorial Gardens" in t1 and "Veteran Planning Guide" in t1
    t2 = render_cadence_message(db, lead, sample_advisor, 2, "")
    assert "checking in" in t2 and "Eastern Gate Memorial Gardens" in t2
    assert "call" not in (t1 + t2).lower()



def test_automated_sends_need_the_campaign_switched_on(program, sample_advisor):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    assert identity.send_refusal(db, lead, "cadence") == identity.CAMPAIGN_OFF_REFUSAL
    assert identity.send_refusal(db, lead, None) == identity.CAMPAIGN_OFF_REFUSAL
    assert identity.send_refusal(db, lead, "manual") is None      # Kerry can always reply by hand
    fam = db.query(CampaignFamily).filter_by(organization_id=org.id, key="veteran_planning_guide").one()
    fam.is_active = True
    db.commit()
    assert identity.send_refusal(db, lead, "cadence") is None



def test_switching_a_campaign_on_needs_its_name(client, program):
    db = program["db"]
    h = _h(db, program["admin"])
    fam = db.query(CampaignFamily).filter_by(organization_id=program["org"].id, key="cremation").one()
    r = client.post("/program/campaigns/%s/activation" % fam.id, json={"active": True}, headers=h)
    assert r.status_code == 422
    r = client.post("/program/campaigns/%s/activation" % fam.id, json={"active": True, "confirm": "cremation"},
                    headers=h)
    assert r.status_code == 200 and r.json()["is_active"] is True
    r = client.post("/program/campaigns/%s/activation" % fam.id, json={"active": False}, headers=h)
    assert r.json()["is_active"] is False



def test_one_flagged_linked_row_holds_the_whole_lead(program):
    """Auto-linked rows share one lead; a flag on ANY of them holds it, whatever order
    the database returns them in."""
    from app.services.programs import promote
    db, org = program["db"], program["org"]
    promote.promote(db, org, apply=True)
    a = db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id="L001").one()
    b = db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id="L002").one()
    assert a.lead_id == b.lead_id
    lead = db.query(Lead).filter(Lead.id == a.lead_id).one()
    assert identity.send_refusal(db, lead, "manual") is None
    b.needs_data_review = True
    db.commit()
    assert identity.send_refusal(db, lead, "manual") == identity.DATA_REVIEW_REFUSAL
    b.needs_data_review = False
    b.location_id = db.query(ProgramSourceRecord).filter_by(source_lead_id="L003").one().location_id
    db.commit()
    assert identity.send_refusal(db, lead, "manual") == identity.LOCATION_REVIEW_REFUSAL   # rows disagree


def test_restaging_keeps_what_people_decided(client, program, sample_advisor):
    db, org = program["db"], program["org"]
    h = _h(db, program["admin"])
    review = db.query(ProgramSourceRecord).filter_by(source_lead_id="L006").one()
    loc = [l for l in client.get("/program/locations", headers=h).json() if not l["is_review_bucket"]][0]
    client.post("/program/records/%s/location" % review.id, json={"location_id": loc["location_id"]}, headers=h)
    lead = _lead_for(db, org, sample_advisor, "L003", phone="2145550103", first="Pat",
                     last="Sampleton", email="shared@example.com")
    responses.on_inbound(db, lead, "wrong person", "sms")
    importer.stage(db, org, CSV.encode(), dry_run=False)
    db.expire_all()
    review = db.query(ProgramSourceRecord).filter_by(source_lead_id="L006").one()
    flagged = db.query(ProgramSourceRecord).filter_by(source_lead_id="L003").one()
    assert review.location_status == "mapped" and review.location_id == loc["location_id"]
    assert flagged.needs_data_review and "WRONG PERSON (reply)" in flagged.data_note_flags


def test_email_opt_out_is_recorded_on_the_lead(program, sample_advisor):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    responses.on_inbound(db, lead, "Please remove me from your list", "email")
    db.refresh(lead)
    assert lead.allow_email is False


# ── campaign email touches ───────────────────────────────────────────────────

def _touch_ready(program, sample_advisor, monkeypatch, source="L001", **lead_kw):
    from app.services.programs import email_touches
    db, org = program["db"], program["org"]
    monkeypatch.setenv(email_touches.SENDING_ENV, "on")
    fam = db.query(CampaignFamily).filter_by(organization_id=org.id, key="veteran_planning_guide").one()
    fam.is_active = True
    # The quality gate refuses automated mail with no verified sending identity.
    if not getattr(org, "from_email", None):
        org.from_email = "support@evosyspro.live"
    setup.store_asset(db, org, kind="flyer", title="Veteran Planning Guide", data=b"%PDF-1.4 guide",
                      content_type="application/pdf", filename="guide.pdf",
                      category="veteran_planning_guide", activate=True)
    db.commit()
    lead = _lead_for(db, org, sample_advisor, source, **lead_kw)
    return db, org, lead, email_touches


def _fake_provider(sent):
    def fake(**kw):
        sent.append(kw)
        return {"success": True, "provider_message_id": "pm-%d" % len(sent)}
    return patch("app.services.email_service.send_email_via_provider", side_effect=fake)


def test_email_touches_are_off_by_default(program, sample_advisor, monkeypatch):
    from app.models.program_models import ProgramEmailTouch
    from app.services.programs import email_touches
    db, org = program["db"], program["org"]
    monkeypatch.delenv(email_touches.SENDING_ENV, raising=False)
    _lead_for(db, org, sample_advisor, "L001")
    sent = []
    with _fake_provider(sent):
        r = email_touches.run(db, org.id, force_hours=True)
    assert r["sent"] == 0 and "off" in r["reason"] and sent == []
    assert db.query(ProgramEmailTouch).count() == 0


def test_email_touches_need_the_campaign_switched_on(program, sample_advisor, monkeypatch):
    from app.services.programs import email_touches
    db, org = program["db"], program["org"]
    monkeypatch.setenv(email_touches.SENDING_ENV, "on")
    _lead_for(db, org, sample_advisor, "L001")
    sent = []
    with _fake_provider(sent):
        r = email_touches.run(db, org.id, force_hours=True)
    assert r["due"] == 0 and sent == []


def test_first_touch_hosted_then_followup_attached_never_twice(program, sample_advisor, monkeypatch):
    from app.models.program_models import ProgramEmailTouch
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    monkeypatch.setenv("PROGRAM_EMAIL_MAX_TOUCHES", "2")   # this test is about touches 1 and 2
    sent = []
    with _fake_provider(sent):
        r1 = et.run(db, org.id, force_hours=True)
        r2 = et.run(db, org.id, force_hours=True)          # same day: nothing more
    assert r1["sent"] == 1 and r2["sent"] == 0 and len(sent) == 1
    first = sent[0]
    assert first["org"].from_name == "Kerry Allan | Eastern Gate Memorial Gardens"
    assert "/program-assets/" in first["body_html"] and '<a href=' in first["body_html"]
    assert "attachments" not in first and "/email/unsubscribe/" in first["body_html"]
    later = datetime.utcnow() + timedelta(days=et.followup_days() + 1)
    with _fake_provider(sent):
        r3 = et.run(db, org.id, now=later, force_hours=True)
        r4 = et.run(db, org.id, now=later, force_hours=True)
    assert r3["sent"] == 1 and r4["sent"] == 0 and len(sent) == 2
    assert sent[1]["attachments"][0]["filename"] == "guide.pdf"
    rows = db.query(ProgramEmailTouch).filter_by(lead_id=lead.id).order_by(ProgramEmailTouch.touch_number).all()
    assert [(t.touch_number, t.status, t.email_mode) for t in rows] == [(1, "sent", "hosted"), (2, "sent", "attached")]
    from app.models.models import EmailMessage
    assert {m.send_source for m in db.query(EmailMessage).filter_by(lead_id=lead.id)} == {"cadence"}


def test_a_reply_ends_the_email_sequence(program, sample_advisor, monkeypatch):
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    responses.on_inbound(db, lead, "Yes please send the guide", "sms")
    db.commit()
    sent = []
    with _fake_provider(sent):
        r = et.run(db, org.id, force_hours=True)
    assert sent == [] and r["skipped"] == 1


def test_email_touches_respect_reviews_and_opt_out(program, sample_advisor, monkeypatch):
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    other = _lead_for(db, org, sample_advisor, "L003", phone="2145550103", first="Pat",
                      last="Sampleton", email="shared@example.com")       # open duplicate review
    lead.allow_email = False
    db.commit()
    sent = []
    with _fake_provider(sent):
        r = et.run(db, org.id, force_hours=True)
    assert sent == [] and r["skipped"] == 2 and other is not None


def test_a_compliance_refusal_is_recorded_as_blocked(program, sample_advisor, monkeypatch):
    from app.models.program_models import ProgramEmailTouch
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    lead.manual_flag = "bad_email"
    db.commit()
    sent = []
    with _fake_provider(sent):
        r = et.run(db, org.id, force_hours=True)
    assert sent == [] and r["blocked"] == 1
    row = db.query(ProgramEmailTouch).filter_by(lead_id=lead.id).one()
    assert row.status == "blocked" and row.reason


def test_email_touches_only_in_sending_hours(program, sample_advisor, monkeypatch):
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    night = datetime(2026, 10, 5, 8, 0)      # 03:00 Central
    day = datetime(2026, 10, 5, 16, 0)       # 11:00 Central
    assert not et.in_sending_hours(db, org.id, night) and et.in_sending_hours(db, org.id, day)
    sent = []
    with _fake_provider(sent):
        assert et.run(db, org.id, now=night)["sent"] == 0
    assert sent == []


def test_email_touch_plan_endpoint_sends_nothing(client, program, sample_advisor, monkeypatch):
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    sent = []
    with _fake_provider(sent):
        res = client.get("/program/email-touches", headers=_h(db, program["admin"])).json()
    assert sent == [] and res["enabled"] is True and res["would_send_total"] == 1
    w = res["would_send"][0]
    assert w["location"] == "Eastern Gate Memorial Gardens" and w["touch"] == 1 and w["email_mode"] == "hosted"


def test_a_flyer_campaign_without_an_approved_flyer_is_held(program, sample_advisor, monkeypatch):
    from app.services.programs import email_touches as et
    db, org = program["db"], program["org"]
    monkeypatch.setenv(et.SENDING_ENV, "on")
    fam = db.query(CampaignFamily).filter_by(organization_id=org.id, key="veteran_planning_guide").one()
    fam.is_active = True          # first touch mode is "hosted" and no flyer is uploaded
    org.from_email = "support@evosyspro.live"
    db.commit()
    _lead_for(db, org, sample_advisor, "L001")
    sent = []
    with _fake_provider(sent):
        r = et.run(db, org.id, force_hours=True)
    assert sent == [] and r["held_no_flyer"] == 1
    fam.first_touch_email_mode = "none"
    db.commit()
    with _fake_provider(sent):
        assert et.run(db, org.id, force_hours=True)["sent"] == 1
    assert "/program-assets/" not in sent[0]["body_html"]


def test_emergency_stop_claims_no_touches(program, sample_advisor, monkeypatch):
    from app.models.program_models import ProgramEmailTouch
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    monkeypatch.setenv("OUTBOUND_EMERGENCY_STOP", "1")
    sent = []
    with _fake_provider(sent):
        r = et.run(db, org.id, force_hours=True)
    assert sent == [] and "emergency" in r["reason"]
    assert db.query(ProgramEmailTouch).count() == 0


def test_promotion_respects_the_plan_lead_limit(program, monkeypatch):
    from fastapi import HTTPException
    from app.models.models import Lead as _Lead
    from app.services import plan_limits
    from app.services.programs import promote
    db, org = program["db"], program["org"]
    monkeypatch.setattr(plan_limits, "limit_for", lambda *a, **k: 1)
    with pytest.raises(HTTPException) as ei:
        promote.promote(db, org, apply=True)
    assert ei.value.status_code == 402
    assert db.query(_Lead).filter(_Lead.organization_id == org.id).count() == 0   # nothing half-done


# ── email runner: review regressions ─────────────────────────────────────────

@pytest.mark.parametrize("status", ["booked", "hot", "not_interested", "dead"])
def test_a_staff_set_status_holds_the_sequence_and_is_not_overwritten(program, sample_advisor, monkeypatch, status):
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    lead.status = status
    db.commit()
    sent = []
    with _fake_provider(sent):
        r = et.run(db, org.id, force_hours=True)
    db.refresh(lead)
    assert sent == [] and r["skipped"] == 1 and lead.status == status


def test_a_blocked_touch_is_retried_after_the_hold_clears(program, sample_advisor, monkeypatch):
    from app.models.program_models import ProgramEmailTouch
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    lead.manual_flag = "bad_email"
    db.commit()
    sent = []
    with _fake_provider(sent):
        assert et.run(db, org.id, force_hours=True)["blocked"] == 1
    lead.manual_flag = None
    db.commit()
    with _fake_provider(sent):
        assert et.run(db, org.id, force_hours=True)["sent"] == 0          # not before 24 h
        later = datetime.utcnow() + timedelta(hours=25)
        assert et.run(db, org.id, now=later, force_hours=True)["sent"] == 1
    rows = db.query(ProgramEmailTouch).filter_by(lead_id=lead.id).all()
    assert len(rows) == 1 and rows[0].status == "sent" and len(sent) == 1


def test_an_error_after_the_provider_is_unknown_not_failed(program, sample_advisor, monkeypatch):
    from app.models.program_models import ProgramEmailTouch
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    calls = []

    def accepted_then_boom(**kw):
        calls.append(kw)
        raise ConnectionError("socket closed after send")
    with patch("app.services.email_service.send_email_via_provider", side_effect=accepted_then_boom):
        r = et.run(db, org.id, force_hours=True)
    row = db.query(ProgramEmailTouch).filter_by(lead_id=lead.id).one()
    assert r["unknown"] == 1 and row.status == "unknown" and "check the provider" in row.reason
    with _fake_provider(calls):
        assert et.run(db, org.id, force_hours=True)["sent"] == 0           # never auto-resent


def test_a_demo_refusal_is_blocked(program, sample_advisor, monkeypatch):
    from app.services.demo_guard import DemoBoundaryViolation
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)

    def demo(*a, **k):
        raise DemoBoundaryViolation("demo lead")
    monkeypatch.setattr("app.services.sms_service._demo_send_guard", demo)
    sent = []
    with _fake_provider(sent):
        assert et.run(db, org.id, force_hours=True)["blocked"] == 1
    assert sent == []


def test_a_stale_claim_surfaces_as_unknown(program, sample_advisor, monkeypatch):
    from app.models.program_models import ProgramEmailTouch
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    db.add(ProgramEmailTouch(organization_id=org.id, lead_id=lead.id, touch_number=1, status="claimed",
                             attempted_at=datetime.utcnow() - timedelta(hours=2)))
    db.commit()
    sent = []
    with _fake_provider(sent):
        et.run(db, org.id, force_hours=True)
    row = db.query(ProgramEmailTouch).filter_by(lead_id=lead.id).one()
    assert sent == [] and row.status == "unknown"


def test_template_fields_are_never_expanded_twice():
    assert identity.render("Hi {first_name} - {location_name}",
                           {"first_name": "{location_name}", "location_name": "Home"}) == "Hi {location_name} - Home"


def test_the_dry_run_plan_writes_nothing(program, sample_advisor, monkeypatch):
    from app.models.program_models import ProgramEmailTouch
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    db.add(ProgramEmailTouch(organization_id=org.id, lead_id=lead.id, touch_number=1, status="claimed",
                             attempted_at=datetime.utcnow() - timedelta(hours=2)))
    db.commit()
    lead.notes = "unsaved edit"                     # caller's pending change survives
    et.run(db, org.id, dry_run=True)
    assert lead.notes == "unsaved edit"
    db.rollback()
    assert db.query(ProgramEmailTouch).filter_by(lead_id=lead.id).one().status == "claimed"


def test_the_daily_cap_trickles_sends(program, sample_advisor, monkeypatch):
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    _lead_for(db, org, sample_advisor, "L007", phone="2145550107", first="Rosa", last="Diaz",
              email="rd@example.com")
    fam = db.query(CampaignFamily).filter_by(organization_id=org.id, key="veteran_spanish").one()
    fam.is_active, fam.first_touch_email_mode = True, "none"
    db.commit()
    monkeypatch.setenv("PROGRAM_EMAIL_TOUCH_DAILY_CAP", "1")
    sent = []
    with _fake_provider(sent):
        r1 = et.run(db, org.id, force_hours=True)
        r2 = et.run(db, org.id, force_hours=True)
    assert r1["sent"] == 1 and r2["sent"] == 0 and "daily cap" in r2["reason"] and len(sent) == 1
    with _fake_provider(sent):
        assert et.run(db, org.id, now=datetime.utcnow() + timedelta(days=1), force_hours=True)["sent"] == 1


# ── location email aliases, central inbox routing, holds, management alerts ──

def test_alias_slugs_are_unique_and_twins_keep_their_type():
    from app.services.programs import aliases
    names = ["Eastern Gate Memorial Funeral Home", "Eastern Gate Memorial Gardens", "Striffler-Hamby Mortuary",
             "Alabama Heritage Cemetery", "Alabama Heritage Funeral Home", "Radney Funeral Home",
             "Radney Funeral Home-Mobile", "Rockco Funeral Home (Montevallo)", "Ridout's Valley Chapel"]
    s = aliases.slugs_for(names)
    assert s["Eastern Gate Memorial Funeral Home"] == "easterngatefuneralhome"
    assert s["Eastern Gate Memorial Gardens"] == "easterngategardens"
    assert s["Striffler-Hamby Mortuary"] == "strifflerhamby"
    assert s["Alabama Heritage Cemetery"] == "alabamaheritagecemetery"
    assert s["Radney Funeral Home-Mobile"] == "radneymobile" and s["Radney Funeral Home"] == "radney"
    assert s["Rockco Funeral Home (Montevallo)"] == "rockcomontevallo"
    assert s["Ridout's Valley Chapel"] == "ridoutsvalley"
    assert len(set(s.values())) == len(names)
    assert aliases.validate_local("support") and aliases.validate_local("Bad Name")
    assert aliases.validate_local("easterngategardens") is None


def _alias_program(program, from_email="support@evosyspro.live"):
    from app.services.programs import aliases
    db, org, prog = program["db"], program["org"], program["prog"]
    org.from_email = from_email
    db.commit()
    table = aliases.assign(db, prog)
    db.commit()
    return db, org, prog, table


def test_aliases_are_assigned_per_location_never_the_review_bucket(program):
    from app.models.program_models import LocationProfile
    db, org, prog, table = _alias_program(program)
    assert table == {"Alabama Heritage Funeral Home": "alabamaheritage@evosyspro.live",
                     "Eastern Gate Memorial Gardens": "easterngate@evosyspro.live",
                     "Striffler-Hamby Mortuary": "strifflerhamby@evosyspro.live"}
    review = db.query(LocationProfile).filter_by(organization_id=org.id, is_review_bucket=True).one()
    assert review.email_alias is None
    from app.services.programs import aliases
    again = aliases.assign(db, prog)          # existing addresses are kept
    assert again == table


def _sent_identity(db, lead, sample_advisor):
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return {"success": True, "provider_message_id": "pm"}
    with patch("app.services.email_service.send_email_via_provider", side_effect=fake):
        from app.services import email_service
        email_service.send_email_to_lead(db, sample_advisor, lead, subject="Hi", body_html="<p>x</p>",
                                         send_source="manual")
    return seen["org"]


def test_an_alias_is_not_used_until_it_receives_mail(program, sample_advisor):
    db, org, prog, _ = _alias_program(program)
    lead = _lead_for(db, org, sample_advisor, "L001")
    ident = _sent_identity(db, lead, sample_advisor)
    # Not yet known to receive: exactly as before - verified From, location display name.
    assert ident.from_email == "support@evosyspro.live"
    assert getattr(ident, "reply_to_email", None) in (None, "")
    assert ident.from_name == "Kerry Allan | Eastern Gate Memorial Gardens"


def test_a_receiving_alias_on_the_verified_domain_is_the_from(program, sample_advisor):
    from app.models.program_models import LocationProfile
    db, org, prog, _ = _alias_program(program)
    prof = db.query(LocationProfile).filter_by(organization_id=org.id,
                                               official_name="Eastern Gate Memorial Gardens").one()
    prof.alias_verified_at = datetime.utcnow()
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001")
    ident = _sent_identity(db, lead, sample_advisor)
    assert ident.from_email == "easterngate@evosyspro.live"
    assert ident.reply_to_email == "easterngate@evosyspro.live"
    assert ident.from_name == "Kerry Allan | Eastern Gate Memorial Gardens"


def test_an_alias_off_the_verified_domain_is_only_the_reply_to(program, sample_advisor):
    db, org, prog, _ = _alias_program(program)
    prog.alias_domain = "other-domain.test"
    from app.models.program_models import LocationProfile
    for p in db.query(LocationProfile).filter_by(organization_id=org.id):
        p.email_alias = None
    db.commit()
    from app.services.programs import aliases
    aliases.assign(db, prog)
    prog.aliases_receiving_confirmed_at = datetime.utcnow()
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001")
    ident = _sent_identity(db, lead, sample_advisor)
    assert ident.from_email == "support@evosyspro.live"           # never weakens DMARC alignment
    assert ident.reply_to_email == "easterngate@other-domain.test"


def test_confirming_aliases_needs_the_phrase(client, program):
    db, org, prog, _ = _alias_program(program)
    h = _h(db, program["admin"])
    assert client.post("/program/aliases/confirm-receiving", headers=h, json={"confirm": "yes"}).status_code == 422
    r = client.post("/program/aliases/confirm-receiving", headers=h, json={"confirm": "aliases receive mail"})
    assert r.status_code == 200 and r.json()["confirmed_all"] is True and r.json()["effective"] == {"from": 3}
    csv_text = client.get("/program/aliases.csv", headers=h).text
    assert "easterngate@evosyspro.live,Eastern Gate Memorial Gardens" in csv_text


def _central_box(db):
    from app.models.inbound_mailbox_models import InboundMailbox
    box = InboundMailbox(address="support@evosyspro.live", is_active=True)
    db.add(box)
    db.commit()
    return box


def _graph_msg(gid, sender, to, body, subject="Re: Your veteran benefits information"):
    t = (datetime.utcnow() - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": gid, "internetMessageId": "<%s@mail.test>" % gid, "subject": subject,
            "from": {"emailAddress": {"address": sender}},
            "toRecipients": [{"emailAddress": {"address": to}}], "ccRecipients": [],
            "receivedDateTime": t, "body": {"content": body}, "bodyPreview": body[:50]}


def test_alias_to_central_inbox_to_the_right_contact_and_location(program, sample_advisor):
    from app.models.models import Reply
    from app.models.program_models import LocationProfile
    from app.services import inbound_mailbox_service as S
    db, org, prog, _ = _alias_program(program)
    lead = _lead_for(db, org, sample_advisor, "L001")
    box = _central_box(db)
    msgs = [_graph_msg("a1", "Ollie@Example.com", "EasternGate@evosyspro.live",
                       "Yes please, I'd like to set up a time to go over the veteran guide.")]
    res = S.poll_mailbox(db, box, fetch=lambda since: msgs)
    assert res["matched"] == 1
    reply = db.query(Reply).filter_by(lead_id=lead.id).one()
    assert "veteran guide" in reply.body
    resp = db.query(ProgramResponse).filter_by(lead_id=lead.id).one()
    prof = db.query(LocationProfile).filter_by(organization_id=org.id,
                                               official_name="Eastern Gate Memorial Gardens").one()
    assert resp.reply_to_alias == "easterngate@evosyspro.live" and resp.location_id == prof.location_id
    assert resp.response_class == "hot" and resp.sla_due_at is not None
    assert prof.alias_verified_at is not None              # seen arriving -> alias now in use
    # the very next email to this family goes out From the alias
    ident = _sent_identity(db, lead, sample_advisor)
    assert ident.from_email == "easterngate@evosyspro.live"


def test_plus_addressed_alias_also_routes(program, sample_advisor):
    from app.services import inbound_mailbox_service as S
    db, org, prog, _ = _alias_program(program)
    lead = _lead_for(db, org, sample_advisor, "L001")
    box = _central_box(db)
    S.poll_mailbox(db, box, fetch=lambda since: [
        _graph_msg("p1", "ollie@example.com", "support+easterngate@evosyspro.live", "ok thanks")])
    assert db.query(ProgramResponse).filter_by(lead_id=lead.id).one().reply_to_alias == "easterngate@evosyspro.live"


def test_writing_to_another_locations_alias_stays_on_the_contacts_own_conversation(program, sample_advisor):
    from app.models.program_models import LocationProfile
    from app.services import inbound_mailbox_service as S
    db, org, prog, _ = _alias_program(program)
    lead = _lead_for(db, org, sample_advisor, "L001")
    box = _central_box(db)
    S.poll_mailbox(db, box, fetch=lambda since: [
        _graph_msg("m1", "ollie@example.com", "strifflerhamby@evosyspro.live", "Is the guide free?")])
    resp = db.query(ProgramResponse).filter_by(lead_id=lead.id).one()
    home = db.query(LocationProfile).filter_by(organization_id=org.id,
                                               official_name="Eastern Gate Memorial Gardens").one()
    assert resp.location_id == home.location_id
    assert "Striffler-Hamby Mortuary address" in resp.summary


def test_a_reply_to_an_alias_from_an_unknown_sender_is_kept_and_alerted(client, program, sample_advisor):
    from app.models.inbound_mailbox_models import InboundMailboxMessage
    from app.models.program_models import ProgramUnmatchedReply
    from app.services import inbound_mailbox_service as S
    db, org, prog, _ = _alias_program(program)
    box = _central_box(db)
    S.poll_mailbox(db, box, fetch=lambda since: [
        _graph_msg("u1", "daughter@example.org", "strifflerhamby@evosyspro.live",
                   "My father got your letter - can you send the guide to me instead?")])
    u = db.query(ProgramUnmatchedReply).one()
    assert u.alias == "strifflerhamby@evosyspro.live" and u.status == "open" and u.reason == "no_lead"
    row = db.query(InboundMailboxMessage).one()
    assert row.organization_id == org.id and "Striffler-Hamby" in row.detail
    assert db.query(ProgramResponse).count() == 0
    assert db.query(Notification).filter(Notification.message.contains("matches no contact")).count() >= 1
    h = _h(db, program["admin"])
    listed = client.get("/program/unmatched-replies", headers=h).json()
    assert listed[0]["location"] == "Striffler-Hamby Mortuary" and listed[0]["from"] == "daughter@example.org"
    assert client.post("/program/unmatched-replies/%s/handled" % u.id, headers=h).json()["status"] == "handled"
    # re-reading the same message does not duplicate it
    S.poll_mailbox(db, box, fetch=lambda since: [
        _graph_msg("u1", "daughter@example.org", "strifflerhamby@evosyspro.live", "same")])
    assert db.query(ProgramUnmatchedReply).count() == 1


def test_an_alias_routes_only_into_its_own_workspace(program, sample_advisor, db_session):
    import uuid
    from app.models.models import Reply
    from app.services import inbound_mailbox_service as S
    db, org, prog, _ = _alias_program(program)
    other = Organization(name="Other Co", slug="oc-%s" % uuid.uuid4().hex[:6], is_active=True,
                         from_email="support@evosyspro.live")
    db.add(other)
    db.commit()
    stranger = Lead(organization_id=other.id, first_name="Ollie", email="ollie@example.com", status="sent")
    db.add(stranger)
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001")
    box = _central_box(db)
    S.poll_mailbox(db, box, fetch=lambda since: [
        _graph_msg("w1", "ollie@example.com", "easterngate@evosyspro.live", "Yes please")])
    assert db.query(Reply).filter_by(lead_id=lead.id).count() == 1
    assert db.query(Reply).filter_by(lead_id=stranger.id).count() == 0


def test_holding_open_reviews_excludes_them_and_does_not_block_the_rest(client, program, sample_advisor):
    from app.services.programs import holds, promote
    db, org = program["db"], program["org"]
    h = _h(db, program["admin"])
    res = client.post("/program/records/hold-open-reviews", headers=h).json()
    recs = {r.source_lead_id: r for r in db.query(ProgramSourceRecord).filter_by(organization_id=org.id)}
    assert res["held"] == 4 and sorted(res["source_lead_ids"]) == ["L003", "L004", "L005", "L006"]
    assert recs["L006"].hold_reason == "LOCATION REVIEW" and "DUPLICATE REVIEW" in recs["L003"].hold_reason
    assert recs["L005"].last_name == "Fakename- NOT INTERESTED DISQUALIFIED"       # nothing auto-fixed
    assert recs["L005"].needs_data_review is True
    assert db.query(ProgramSourceRecord).filter_by(organization_id=org.id).count() == 7   # nothing deleted
    p = promote.promote(db, org, apply=True)
    assert p["held_not_promoted"] == 4 and p["created"] == 2    # L001+L002 (one contact) and L007
    assert all(recs[k].lead_id is None for k in ("L003", "L004", "L005", "L006"))
    q = client.get("/program/records?queue=on_hold", headers=h).json()
    assert q["total"] == 4 and all(i["on_hold"] for i in q["items"])
    dash = client.get("/program/dashboard", headers=h).json()
    assert dash["attention"]["on_hold"] == 4
    item = [i for i in dash["readiness"]["items"] if i["key"] == "held_records"][0]
    assert item["ok"] is True and "4 on hold" in item["detail"]
    # a person can release one; nothing else changes
    r = client.post("/program/records/%s/hold" % recs["L006"].id, headers=h, json={"on_hold": False}).json()
    assert r["on_hold"] is False


def test_a_held_contact_is_refused_on_every_path(program, sample_advisor):
    from app.services.programs import holds
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    rec = db.query(ProgramSourceRecord).filter_by(source_lead_id="L001").one()
    holds.set_hold(db, rec, True, None, "held for test")
    db.commit()
    assert identity.send_refusal(db, lead, "manual") == identity.HOLD_REFUSAL
    from app.services import email_service
    with patch("app.services.email_service.send_email_via_provider") as prov:
        with pytest.raises(ValueError, match="ON HOLD"):
            email_service.send_email_to_lead(db, sample_advisor, lead, subject="Hi", body_html="<p>x</p>",
                                             send_source="manual")
    assert not prov.called


def test_linked_rows_at_two_locations_are_held_as_a_location_conflict(program):
    from app.models.program_models import LocationProfile
    from app.services.programs import holds
    db, org = program["db"], program["org"]
    recs = {r.source_lead_id: r for r in db.query(ProgramSourceRecord).filter_by(organization_id=org.id)}
    other = db.query(LocationProfile).filter_by(organization_id=org.id,
                                                official_name="Striffler-Hamby Mortuary").one()
    recs["L002"].location_id = other.location_id     # same person, second row at another home
    db.commit()
    res = holds.hold_open_reviews(db, org.id, None)
    assert res["by_reason"]["LOCATION CONFLICT"] == 2
    assert recs["L001"].on_hold and "LOCATION CONFLICT" in recs["L001"].hold_reason


def test_management_recipients_are_normalised_and_validated(client, program):
    db = program["db"]
    h = _h(db, program["admin"])
    r = client.patch("/program/settings", headers=h, json={
        "management_recipients": [{"name": "Manager", "phone": "540-555-0123", "email": "Manager@Example.com"}],
        "staff_alerts_enabled": True})
    assert r.status_code == 200
    m = r.json()["management_recipients"][0]
    assert m["phone"] == "+15405550123" and m["email"] == "manager@example.com"
    assert client.patch("/program/settings", headers=h, json={
        "management_recipients": [{"phone": "12"}]}).status_code == 422
    assert client.patch("/program/settings", headers=h, json={
        "management_recipients": [{"email": "not-an-email"}]}).status_code == 422
    item = [i for i in client.get("/program/dashboard", headers=h).json()["readiness"]["items"]
            if i["key"] == "alert_recipients"][0]
    assert item["ok"] is True and "sms+email" in item["detail"]


def test_a_hot_reply_alerts_management_by_sms_and_email_immediately(program, sample_advisor):
    from app.models.program_models import ProgramAlert
    db, org, prog = program["db"], program["org"], program["prog"]
    prog.management_recipients = json.dumps([{"name": "Manager", "phone": "+15405550123",
                                              "email": "manager@example.com"}])
    prog.staff_sms_alerts_enabled = True
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001")
    calls = []

    def fake_deliver(db_, prog_, channel, to, message, **kw):
        calls.append((channel, to, kw.get("kind")))
        return True, None
    with patch("app.services.programs.responses._deliver_staff_alert", side_effect=fake_deliver):
        responses.on_inbound(db, lead, "Yes I'd like to set up a time to visit", "sms")
    assert ("sms", "+15405550123", "hot") in calls and ("email", "manager@example.com", "hot") in calls
    alerts = db.query(ProgramAlert).filter_by(organization_id=org.id, audience="management").all()
    assert {a.channel for a in alerts} >= {"in_app", "sms", "email"}


def test_management_hot_email_has_a_clear_subject(program, monkeypatch):
    from app.services.programs import responses as R
    db, prog = program["db"], program["prog"]
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    seen = {}

    def fake_send(to, subject, body, org=None, **kw):
        seen.update(to=to, subject=subject, body=body)
        return {"success": True}
    with patch("app.services.email_service.send_email_via_provider", side_effect=fake_send):
        ok, _ = R._deliver_staff_alert(db, prog, "email", "manager@example.com", "Ollie <b>replied</b>",
                                       kind="hot", response_id="r1")
    assert ok and seen["subject"].startswith("HOT RESPONSE")
    assert "&lt;b&gt;" in seen["body"] and "https://app.example.com/program?tab=responses&amp;id=r1" in seen["body"]


@pytest.mark.parametrize("text,cls,found", [
    ("Yes, I would like more information and would like to schedule a time.", "hot",
     ["appointment_intent", "information_request"]),
    ("How much does a cemetery plot cost at your location?", "hot", ["pricing_question", "cemetery_interest"]),
    ("What does the veteran guide include?", "active", ["information_request", "veteran_planning_question"]),
    ("This is not him, you have the wrong person", "wrong_person", ["wrong_person"]),
    ("Wrong number", "bad_data", ["bad_data"]),
    ("Am I eligible for burial benefits as a veteran?", "active",
     ["benefits_question", "veteran_planning_question", "cemetery_interest"]),
    ("We're thinking about cremation, what are the options?", "active", ["information_request", "cremation_interest"]),
    ("I'd like to start pre-planning my arrangements", "active", ["general_planning"]),
    ("Not right now, maybe later", "active", ["objection"]),
    ("Not interested, remove me", "opt_out", ["not_interested", "opt_out"]),
    ("STOP", "opt_out", ["opt_out"]),
    ("thank you", "low", ["simple_acknowledgment"]),
])
def test_classes_and_intents(text, cls, found):
    c = responses.classify(text)["class"]
    assert c == cls
    assert responses.intents(text, c) == found


@pytest.mark.parametrize("cls,urg", [("hot", "HOT"), ("active", "ACTIVE"), ("low", "LOW"),
                                     ("opt_out", "LOW"), ("wrong_person", "LOW"), ("bad_data", "LOW")])
def test_urgency_is_separate_from_intent(cls, urg):
    assert responses.urgency(cls) == urg


def test_a_hot_reply_gets_intents_and_a_draft_that_is_never_sent(program, sample_advisor):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    with patch("app.services.email_service.send_email_via_provider") as prov, \
            patch("app.services.sms_service.Client") as tw:
        r = responses.on_inbound(db, lead, "Yes, I would like more information and would like to schedule a time.",
                                 "email")
    assert not prov.called and not tw.called            # a draft, never sent
    assert json.loads(r.intents) == ["appointment_intent", "information_request"]
    assert r.suggested_reply.startswith("Hi Ollie, I'd be glad to set a time")
    assert "Eastern Gate Memorial Gardens" in r.suggested_reply and "call" not in r.suggested_reply.lower()
    assert responses.suggested_reply("opt_out", [], first_name="A", contact="K", location="L", channel="sms") is None


# ── hardening: Outlook filing, mailbox reconnect, retries, booking stop, health ──

class _FakeMover:
    can_write = True

    def __init__(self, results=None):
        self.calls, self.results = [], list(results or [])

    def __call__(self, gid, path):
        self.calls.append((gid, path))
        return self.results.pop(0) if self.results else (True, None)


def test_processed_alias_reply_is_filed_in_its_location_folder_after_processing(program, sample_advisor):
    from app.models.program_models import ProgramMailFiling
    from app.services import inbound_mailbox_service as S
    db, org, prog, _ = _alias_program(program)
    prog.mailbox_folder_path = "Inbox/Customers Folder/SCI"
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001")
    box = _central_box(db)
    mover = _FakeMover()
    S.poll_mailbox(db, box, fetch=lambda since: [
        _graph_msg("f1", "ollie@example.com", "easterngate@evosyspro.live", "Yes please, schedule a time")],
        mover=mover)
    assert mover.calls == [("f1", "Inbox/Customers Folder/SCI/Eastern Gate Memorial Gardens")]
    f = db.query(ProgramMailFiling).one()
    assert f.status == "filed" and f.filed_at is not None
    # processing happened before filing: the response exists
    assert db.query(ProgramResponse).filter_by(lead_id=lead.id).count() == 1
    # re-reading the moved message (same immutable id) neither re-processes nor re-files
    S.poll_mailbox(db, box, fetch=lambda since: [
        _graph_msg("f1", "ollie@example.com", "easterngate@evosyspro.live", "Yes please, schedule a time")],
        mover=mover)
    assert len(mover.calls) == 1 and db.query(ProgramResponse).filter_by(lead_id=lead.id).count() == 1


def test_unmatched_mail_is_never_filed(program, sample_advisor):
    from app.models.program_models import ProgramMailFiling
    from app.services import inbound_mailbox_service as S
    db, org, prog, _ = _alias_program(program)
    prog.mailbox_folder_path = "Inbox/Customers Folder/SCI"
    db.commit()
    box = _central_box(db)
    mover = _FakeMover()
    S.poll_mailbox(db, box, fetch=lambda since: [
        _graph_msg("n1", "stranger@example.org", "easterngate@evosyspro.live", "hello?")], mover=mover)
    assert mover.calls == [] and db.query(ProgramMailFiling).count() == 0


def test_a_failed_move_is_retried_then_files(program, sample_advisor):
    from app.models.program_models import ProgramMailFiling
    from app.services import inbound_mailbox_service as S
    db, org, prog, _ = _alias_program(program)
    prog.mailbox_folder_path = "Inbox/Customers Folder/SCI"
    db.commit()
    _lead_for(db, org, sample_advisor, "L001")
    box = _central_box(db)
    mover = _FakeMover([(False, "Graph move failed 503: busy"), (True, None)])
    msgs = [_graph_msg("r1", "ollie@example.com", "easterngate@evosyspro.live", "Is the guide free?")]
    S.poll_mailbox(db, box, fetch=lambda since: msgs, mover=mover)
    f = db.query(ProgramMailFiling).one()
    assert f.status in ("pending", "filed")        # first attempt failed, end-of-poll retry may file it
    S.poll_mailbox(db, box, fetch=lambda since: [], mover=mover)
    db.refresh(f)
    assert f.status == "filed" and f.attempts == 2


def test_a_read_only_mailbox_reads_but_reports_reconnect_for_filing(program, sample_advisor):
    from app.models.program_models import ProgramMailFiling
    from app.services.programs.mailfiling import GraphMover
    from app.services import inbound_mailbox_service as S
    db, org, prog, _ = _alias_program(program)
    prog.mailbox_folder_path = "Inbox/Customers Folder/SCI"
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001")
    box = _central_box(db)
    S.poll_mailbox(db, box, fetch=lambda since: [
        _graph_msg("ro1", "ollie@example.com", "easterngate@evosyspro.live", "Yes please")],
        mover=GraphMover("tok", can_write=False))
    assert db.query(ProgramResponse).filter_by(lead_id=lead.id).count() == 1          # still read + routed
    f = db.query(ProgramMailFiling).one()
    assert f.status == "skipped" and "Mail.ReadWrite" in f.last_error


def test_mailbox_token_falls_back_to_read_only_instead_of_breaking(monkeypatch, db_session):
    from types import SimpleNamespace
    from app.services import inbound_mailbox_service as S
    calls = []

    def fake_refresh(box, scope):
        calls.append(scope)
        if "ReadWrite" in scope:
            return SimpleNamespace(status_code=400, text="AADSTS65001 consent required", json=lambda: {},
                                   raise_for_status=lambda: None)
        return SimpleNamespace(status_code=200, text="", raise_for_status=lambda: None,
                               json=lambda: {"access_token": "AT", "scope": "Mail.Read User.Read"})
    monkeypatch.setattr(S, "_refresh", fake_refresh)
    box = SimpleNamespace(refresh_token_encrypted="x")
    tok, can_write = S._access_token_rw(box)
    assert tok == "AT" and can_write is False and len(calls) == 2


def test_mailbox_disconnect_alerts_once_per_outage(program, monkeypatch):
    from app.models.program_models import ProgramAlert
    from app.services import inbound_mailbox_service as S
    db = program["db"]
    box = _central_box(db)
    box.refresh_token_encrypted = "x"
    db.commit()

    def refused(b):
        raise S.MailboxAuthError("Microsoft refused the stored sign-in (400)")
    monkeypatch.setattr(S, "_access_token_rw", refused)
    S.poll_mailbox(db, box)
    S.poll_mailbox(db, box)
    alerts = db.query(ProgramAlert).filter_by(kind="mailbox_reconnect").all()
    assert alerts and len({a.recipient for a in alerts}) == len(alerts)     # one per admin, not per poll
    assert box.last_status == "auth_error"


@pytest.mark.parametrize("text,kind", [
    ("429 Too Many Requests", "transient"), ("503 Service Unavailable", "transient"),
    ("ConnectError: connection refused", "transient"), ("Read timed out", "ambiguous"),
    ("422 The from address domain is not verified", "permanent"),
])
def test_provider_errors_are_classified(text, kind):
    from app.services.programs import email_touches as et
    assert et.classify_provider_error(text) == kind


def test_a_temporary_provider_failure_retries_with_backoff_then_sends(program, sample_advisor, monkeypatch):
    from app.models.program_models import ProgramEmailTouch
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    outcomes = [{"success": False, "provider_message_id": None, "error": "429 Too Many Requests"},
                {"success": True, "provider_message_id": "pm-ok"}]
    with patch("app.services.email_service.send_email_via_provider", side_effect=lambda **kw: outcomes.pop(0)):
        r1 = et.run(db, org.id, force_hours=True)
        r2 = et.run(db, org.id, force_hours=True)                       # not yet - backoff
        later = datetime.utcnow() + timedelta(minutes=15)
        r3 = et.run(db, org.id, now=later, force_hours=True)
    t = db.query(ProgramEmailTouch).filter_by(lead_id=lead.id).one()
    assert r1["retry"] == 1 and r2["sent"] == 0 and r3["sent"] == 1
    assert t.status == "sent" and t.attempts == 2


def test_a_permanent_provider_failure_is_not_retried(program, sample_advisor, monkeypatch):
    from app.models.program_models import ProgramEmailTouch
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    with patch("app.services.email_service.send_email_via_provider",
               side_effect=lambda **kw: {"success": False, "provider_message_id": None,
                                         "error": "422 domain is not verified"}) as prov:
        et.run(db, org.id, force_hours=True)
        et.run(db, org.id, now=datetime.utcnow() + timedelta(days=2), force_hours=True)
    t = db.query(ProgramEmailTouch).filter_by(lead_id=lead.id).one()
    assert t.status == "failed" and prov.call_count == 1


def test_a_booked_appointment_stops_the_email_sequence(program, sample_advisor, monkeypatch):
    from app.models.models import BookingLink
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    db.add(BookingLink(lead_id=lead.id, user_id=sample_advisor.id, token="tok-booked-1", status="booked"))
    db.commit()
    sent = []
    with _fake_provider(sent):
        r = et.run(db, org.id, force_hours=True)
    assert sent == [] and r["skipped"] == 1


def test_health_snapshot_reports_every_block(client, program, sample_advisor, monkeypatch):
    from app.services.programs import health
    db, org, prog, _ = _alias_program(program)
    monkeypatch.setattr(health, "_txt", lambda name: ["v=spf1 include:x ~all"] if name.startswith("send.")
                        else (["p=MIGf"] if "domainkey" in name else ["v=DMARC1; p=quarantine"]))
    health._DNS_CACHE.clear()
    _central_box(db)
    h = client.get("/program/health", headers=_h(db, program["admin"])).json()
    for k in ("email_system", "sender_domain", "webhook", "mailbox", "aliases", "sms", "campaigns",
              "held_contacts", "failed_sends", "bounces", "complaints", "unmatched_replies", "hot_responses",
              "unhandled_hot", "response_time", "automation_errors", "outlook_filing", "last_successful", "decisions"):
        assert k in h, k
    assert h["sender_domain"]["status"] == "ok" and h["sms"]["status"] in ("warn", "fail")
    assert h["overall"] in ("ok", "warn", "fail")


def test_an_opt_out_ends_the_cadence_not_just_pauses_it(program, sample_advisor):
    from app.models.models import CadenceState
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    responses.on_inbound(db, lead, "Please remove me, not interested.", "email")
    st = db.query(CadenceState).filter_by(lead_id=lead.id).one()
    assert str(getattr(st.status, "value", st.status)) == "stopped_dnc" and lead.allow_email is False


# ── staging harness (owner-only, OFF unless STAGING_TEST_HARNESS=on) ─────────

def _god(db):
    u = User(email="owner-%s@example.com" % datetime.utcnow().strftime("%f"), password_hash=hash_password("Pass12345!"),
             full_name="Owner", role="god_admin", is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def test_staging_harness_is_invisible_unless_switched_on(client, db_session, monkeypatch):
    monkeypatch.delenv("STAGING_TEST_HARNESS", raising=False)
    h = _h(db_session, _god(db_session))
    assert client.get("/god/staging/sci/status", headers=h).status_code == 404
    assert client.post("/god/staging/sci/send-test", headers=h, json={}).status_code == 404


def test_staging_harness_seeds_and_refuses_a_second_send(client, db_session, monkeypatch):
    from app.models.models import Platform
    monkeypatch.setenv("STAGING_TEST_HARNESS", "on")
    if not db_session.query(Platform).filter_by(slug="evosyspro").first():
        db_session.add(Platform(id="plt-evosyspro-t", name="EvoSys Pro", slug="evosyspro",
                                support_email="support@evosyspro.live"))
        db_session.commit()
    god = _god(db_session)
    h = _h(db_session, god)
    # no owner sign-in -> refused
    assert client.post("/god/staging/sci/seed", json={"test_email": "t@example.com"}).status_code in (401, 403)
    s = client.post("/god/staging/sci/seed", headers=h, json={"test_email": "tester@example.com",
                                                              "mgmt_sms": "5405550123",
                                                              "mgmt_email": "mgr@example.com"}).json()
    assert s["locations"] == 39 and s["aliases_assigned"] == 39 and s["alias_mismatches"] == {}
    # the live-loop seed creates the approved test contact ONLY
    assert s["simulation_contacts"] == [] and s["contacts_in_workspace"] == 1
    sent = []

    def fake(**kw):
        sent.append(kw)
        return {"success": True, "provider_message_id": "pm-1"}
    with patch("app.services.email_service.send_email_via_provider", side_effect=fake):
        first = client.post("/god/staging/sci/send-test", headers=h, json={})
        second = client.post("/god/staging/sci/send-test", headers=h, json={})
    assert first.status_code == 200 and second.status_code == 409 and len(sent) == 1
    body = first.json()
    assert body["to"] == "tester@example.com"
    assert body["from_email"] == "easterngategardens@evosyspro.live" == body["reply_to"]
    assert body["from_name"] == "Kerry Allan | Eastern Gate Memorial Gardens"
    assert sent[0]["to_email"] == "tester@example.com"
    st = client.get("/god/staging/sci/status", headers=h).json()
    assert st["outbound"][0]["status"] == "sent" and st["campaign_touches"] == 0 and st["active_campaigns"] == []
    assert st["contacts_in_workspace"] == ["tester@example.com"]
    assert client.post("/god/staging/sci/simulate/active", headers=h).status_code == 409
    again = client.post("/god/staging/sci/seed", headers=h, json={"test_email": "tester@example.com",
                                                                  "simulation_contacts": True}).json()
    assert len(again["simulation_contacts"]) == 5 and again["contacts_in_workspace"] == 6
