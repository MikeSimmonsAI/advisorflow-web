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
    ("Please send me more information", "hot"),
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
    assert r.response_class == "bad_data" and rec.needs_data_review


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
