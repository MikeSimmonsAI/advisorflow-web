"""#441 (round 2) - Central Alabama campaign editor: the six owner themes, a
one-time upgrade of untouched first-draft wording, who saved each step and
when, draft preview through the delivery renderer, the full family roster
(STOP/DNC, last/next touch, held families) and tenant isolation. Synthetic
data only; nothing reaches Twilio or an email provider."""
import json
from datetime import datetime, timedelta

from app.models.models import (AuditLogEntry, CadenceState, EmailMessage, Message, Organization,
                               SuppressionEntry, SuppressionSource)
from app.models.program_models import CampaignFamily, LocationProfile, ProgramSourceRecord
from app.services.programs import campaigns as C, identity, setup
from tests.test_outreach_program import _admin, _h, program  # noqa: F401
from tests.test_sci_441_central_alabama_cadence import LOCS, _ca_lead, _fam


def _sends(db):
    return db.query(Message).count(), db.query(EmailMessage).count()


def _approve(db, lead):
    for r in db.query(ProgramSourceRecord).filter_by(lead_id=lead.id):
        r.approved_to_send_at = datetime.utcnow()
        r.on_hold = False
    db.commit()


# ── wording ─────────────────────────────────────────────────────────────────

def test_each_step_follows_its_theme_and_only_the_first_introduces(program):
    db, org = program["db"], program["org"]
    fam = _fam(db, org)
    t = [C.sms_for(fam, n) for n in range(1, 7)]
    assert t[0] == C.CA_SMS[1] and "What day works best for you?" in t[0]
    assert "earlier message" in t[1]                                   # 2 friendly follow-up
    assert "easy" in t[2] and "{appointment_options}" in t[2]         # 3 scheduling convenience
    assert "pre-planning guide and savings certificate" in t[3]       # 4 the materials, again
    assert len(t[4]) < len(t[0]) * 0.6                                # 5 a shorter check-in
    assert "Whenever" in t[5] or "whenever" in t[5]                   # 6 door left open
    intros = [x for x in t if "this is {primary_contact_name} with" in x]
    assert intros == [t[0]]                                           # no duplicate introductions
    e = [C.email_for(fam, n) for n in range(1, 6)]
    assert e[0]["subject"] == "Your Pre-Planning Guide and Savings Certificate"
    assert "I'm reaching out because you filled out one of our surveys" in e[0]["body"]
    assert len({x["subject"] for x in e}) == 5 and len({x["body"] for x in e}) == 5
    assert C.copy_problems(fam) == []
    labels = C.touch_copy(db, fam)
    assert [s["label"] for s in labels["sms"]] == ["First contact"] + ["Follow-up %d" % i for i in range(1, 6)]
    assert [s["theme"] for s in labels["sms"]][:2] == ["First contact", "Friendly follow-up"]


V1_TEXT_2 = ("Hi {first_name}, {primary_contact_name} again. I'd still like to get your pre-planning guide and "
             "savings certificate to you. Would a short visit later this week work for you?")


def test_untouched_first_draft_wording_is_upgraded_once_and_edits_are_kept(program):
    db, org = program["db"], program["org"]
    fam = _fam(db, org)
    assert C._fp(V1_TEXT_2) == C._V1_SMS_FP[2]
    d = json.loads(fam.touch_copy_json)
    d["sms"]["2"] = V1_TEXT_2                       # what production holds from the first build
    d["sms"]["3"] = "Mike's own edit {first_name}"   # someone edited this step
    d.pop("seed_version", None)
    fam.touch_copy_json = json.dumps(d)
    db.commit()
    before = _sends(db)
    C.ensure_central_alabama(db, org.id)
    db.refresh(fam)
    assert C.sms_for(fam, 2) == C.CA_SMS[2]
    assert C.sms_for(fam, 3) == "Mike's own edit {first_name}"
    assert fam.is_active is False and _sends(db) == before
    C.set_touch_copy(fam, sms={"2": V1_TEXT_2})      # a person puts it back on purpose
    db.commit()
    C.ensure_central_alabama(db, org.id)
    db.refresh(fam)
    assert C.sms_for(fam, 2) == V1_TEXT_2            # upgrade ran once only


# ── saving: who, when, which steps ──────────────────────────────────────────

def test_saving_records_editor_time_and_revision_and_sends_nothing(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    fam = _fam(db, org)
    before = _sends(db) + (db.query(CadenceState).count(),)
    body = "Hi {first_name}, {primary_contact_name} here at {location_name}. Would Tuesday suit you?"
    r = client.patch("/program/campaigns/%s" % fam.id, headers=_h(db, admin), json={"steps": {"sms": {"5": body}}})
    assert r.status_code == 200, r.text
    out = r.json()
    s5 = out["steps"]["sms"][4]
    assert s5["body"] == body and s5["updated_by"] == "SCI Admin" and s5["revision"] >= 2 and s5["updated_at"]
    assert out["last_saved_by"] == "SCI Admin" and out["ready"] is True and out["is_active"] is False
    rev = s5["revision"]
    # saving the same words again bumps nothing
    r2 = client.patch("/program/campaigns/%s" % fam.id, headers=_h(db, admin), json={"steps": {"sms": {"5": body}}})
    assert r2.json()["steps"]["sms"][4]["revision"] == rev
    assert r2.json()["steps"]["sms"][3]["updated_by"] == "Owner's campaign brief"   # untouched step
    logs = db.query(AuditLogEntry).filter(AuditLogEntry.action == "program.campaign_copy_saved").all()
    assert any("sms:5" in (x.details or "") for x in logs)
    assert _sends(db) + (db.query(CadenceState).count(),) == before


def test_a_blank_required_step_blocks_switching_on(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    fam = _fam(db, org)
    r = client.patch("/program/campaigns/%s" % fam.id, headers=_h(db, admin),
                     json={"steps": {"email": {"3": {"subject": "", "body": ""}}}})
    assert r.status_code == 200 and r.json()["ready"] is False and "Email 3" in r.json()["missing_steps"]
    on = client.post("/program/campaigns/%s/activation" % fam.id, headers=_h(db, admin),
                     json={"active": True, "confirm": fam.name})
    assert on.status_code == 422 and "Email 3" in on.json()["detail"]


# ── previews ────────────────────────────────────────────────────────────────

def test_draft_preview_renders_unsaved_words_saves_and_sends_nothing(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    setup.ensure_locations(db, org, admin, LOCS)
    C.ensure_kilgroe_for_sci(db, org.id)
    fam = _fam(db, org)
    saved = C.sms_for(fam, 2)
    before = _sends(db)
    for loc in LOCS + ["Kilgroe Funeral Home"]:
        prof = db.query(LocationProfile).filter_by(organization_id=org.id, official_name=loc).one()
        r = client.post("/program/campaigns/%s/preview-draft" % fam.id, headers=_h(db, admin),
                        json={"location_id": prof.location_id, "touch": "sms:2", "first_name": "Maria",
                              "sms": "Hi {first_name}, {primary_contact_name} at {location_name} - Friday?"})
        p = r.json()
        assert r.status_code == 200 and p["ok"] and p["draft"] and p["sends_nothing"]
        assert p["sms"].startswith("Hi Maria, Kerry Allan at %s - Friday?" % loc)
        assert p["sender"] == "Kerry Allan" and p["location"] == loc
        for other in set(LOCS + ["Kilgroe Funeral Home"]) - {loc}:
            assert other not in p["sms"]
        e = client.post("/program/campaigns/%s/preview-draft" % fam.id, headers=_h(db, admin),
                        json={"location_id": prof.location_id, "touch": "email:4", "subject": "Hi {first_name}",
                              "body": "Worth $500! {primary_contact_name} {location_name}"}).json()
        assert e["email_subject"] == "Hi Pat" and e["problems"]          # the $ is flagged before saving
    db.refresh(fam)
    assert C.sms_for(fam, 2) == saved and _sends(db) == before


def test_saved_preview_uses_a_sample_name_and_matches_draft_preview(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    fam = _fam(db, org)
    prof = db.query(LocationProfile).filter_by(organization_id=org.id, official_name="Alabama Heritage Funeral Home").one()
    g = client.get("/program/campaigns/%s/preview?location_id=%s&touch=sms:3&first_name=Jo<b>" % (fam.id, prof.location_id),
                   headers=_h(db, admin)).json()
    assert g["sample_name"] == "Job" and g["sms"].startswith("Job, setting up a time is easy.")
    d = client.post("/program/campaigns/%s/preview-draft" % fam.id, headers=_h(db, admin),
                    json={"location_id": prof.location_id, "touch": "sms:3", "first_name": "Job",
                          "sms": C.sms_for(fam, 3)}).json()
    assert d["sms"] == g["sms"]                                            # same renderer, same words


def test_other_tenants_cannot_preview_edit_or_see_the_roster(client, program):
    db, org = program["db"], program["org"]
    fam = _fam(db, org)
    other = Organization(name="Other", slug="other-441", plan="enterprise", industry="generic", is_active=True)
    db.add(other)
    db.commit()
    h = _h(db, _admin(db, other, "other-441@example.com"))
    prof = db.query(LocationProfile).filter_by(organization_id=org.id).first()
    assert client.patch("/program/campaigns/%s" % fam.id, headers=h, json={"steps": {"sms": {"2": "x"}}}).status_code == 404
    assert client.post("/program/campaigns/%s/preview-draft" % fam.id, headers=h,
                       json={"location_id": prof.location_id, "sms": "x"}).status_code == 404
    assert client.get("/program/central-alabama/roster", headers=h).status_code == 404
    db.refresh(fam)
    assert C.sms_for(fam, 2) == C.CA_SMS[2]


def test_other_campaigns_keep_their_copy(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    snap = lambda: {f.key: (f.sms_template, f.email_subject_template, f.email_body_template, f.is_active,
                            f.touch_copy_json)
                    for f in db.query(CampaignFamily).filter(CampaignFamily.organization_id == org.id,
                                                             CampaignFamily.key != C.CENTRAL_ALABAMA_KEY)}
    _fam(db, org)
    before = snap()
    fam = _fam(db, org)
    client.patch("/program/campaigns/%s" % fam.id, headers=_h(db, admin),
                 json={"steps": {"sms": {"6": "Hi {first_name}, reply any time. {primary_contact_name}"}}})
    C.ensure_central_alabama(db, org.id)
    db.expire_all()
    assert snap() == before


# ── roster ──────────────────────────────────────────────────────────────────

def test_roster_reports_real_state_per_family(client, program, sample_advisor):
    from app.services.programs import responses
    db, org, admin = program["db"], program["org"], program["admin"]
    _fam(db, org)
    quiet = _ca_lead(db, org, admin, source="L007")
    texted = _ca_lead(db, org, admin, source="L003", phone="2145550103", email="p@example.com", first="pat")
    stopped = _ca_lead(db, org, admin, source="L004", phone="2145550104", email="d@example.com", first="dana")
    email_only = _ca_lead(db, org, admin, source="L001", phone="", email="ollie@example.com", first="ollie")
    for l in (quiet, texted, stopped, email_only):
        _approve(db, l)
    # one real (synthetic) delivered text to "texted", then a reply
    db.add(Message(lead_id=texted.id, sender_id=sample_advisor.id, body="hi", delivery_status="delivered"))
    st = db.query(CadenceState).filter_by(lead_id=texted.id).one()
    st.current_touch_number, st.last_touch_sent_at = 1, datetime.utcnow() - timedelta(hours=2)
    db.add(SuppressionEntry(organization_id=org.id, phone="12145550104", reason="STOP",
                            source=SuppressionSource.REPLY_STOP))
    db.commit()
    d = client.get("/program/central-alabama/roster", headers=_h(db, admin)).json()
    rows = {r["lead_id"]: r for r in d["rows"]}
    assert d["people"] == 4 and d["with_phone"] == 3 and d["email_only"] == 1 and d["no_phone_on_file"] == 1
    assert rows[quiet.id]["status"].startswith("Approved") and not rows[quiet.id]["contacted"]
    assert rows[quiet.id]["last_touch"] is None and rows[quiet.id]["next_touch"] is None   # off: nothing scheduled
    assert rows[texted.id]["contacted"] and rows[texted.id]["last_touch"] == "Text 1"
    assert rows[texted.id]["status"] == "Contacted"
    assert rows[stopped.id]["stop_dnc"] == "STOP / do not contact" and rows[stopped.id]["status"] == "Do not contact"
    assert rows[email_only.id]["path"] == "Email only (no phone on file)" and not rows[email_only.id]["has_phone"]
    assert d["contacted"] == 1 and d["texts_sent"] == 1 and d["stop_dnc"] == 1
    responses.on_inbound(db, texted, "Thursday works", "sms")
    db.commit()
    d2 = client.get("/program/central-alabama/roster", headers=_h(db, admin)).json()
    r2 = {r["lead_id"]: r for r in d2["rows"]}[texted.id]
    assert r2["replied"] and r2["status"].startswith("Replied")
    assert db.query(CadenceState).filter_by(lead_id=texted.id).one().status != "active"   # sequence stops


def test_roster_lists_held_families_and_never_counts_them(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    _fam(db, org)
    approved = _ca_lead(db, org, admin, source="L007")
    _approve(db, approved)
    review = db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id="L006").one()
    review.campaign_family, review.location_status, review.on_hold = C.CENTRAL_ALABAMA_KEY, "location_review", True
    review.first_name, review.last_name = "BARBARA", "SAMPLE"
    excluded = _ca_lead(db, org, admin, source="L005", phone="2145550105", email="cb@example.com", first="chris")
    for r in db.query(ProgramSourceRecord).filter_by(lead_id=excluded.id):
        r.on_hold, r.hold_reason, r.approved_to_send_at = True, "EXCLUDED FROM CENTRAL ALABAMA: already in SCI", None
    db.commit()
    d = client.get("/program/central-alabama/roster", headers=_h(db, admin)).json()
    assert d["people"] == 1 and [r["lead_id"] for r in d["rows"]] == [approved.id]
    assert [h["name"] for h in d["held"]["location_review"]] == ["Barbara Sample"]
    assert d["held"]["excluded_people"] == 1


def test_the_first_text_of_each_location_says_kerry_and_its_own_home(program, sample_advisor):
    from app.services import cadence_service
    db, org, admin = program["db"], program["org"], program["admin"]
    setup.ensure_locations(db, org, admin, LOCS)
    _fam(db, org)
    for i, loc in enumerate(LOCS):
        lead = _ca_lead(db, org, admin, source=["L001", "L003"][i], loc=loc, first="sam",
                        phone="21455501%02d" % (i + 1), email="s%d@example.com" % i)
        body = identity.apply_sms_signoff(db, lead, cadence_service.render_cadence_message(db, lead, sample_advisor, 1, ""))
        assert body.startswith("Hi Sam, this is Kerry Allan with %s." % loc)
        assert body.count("Reply STOP") == 1
