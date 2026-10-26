"""#385 - import a NORMAL export (no Lead IDs, "Nearest/Preferred Location"
with a location number) without repairs, keep every source record, link
people already in SCI instead of making second families, and give the list
its OWN campaign that stays off until approved copy exists. Synthetic only."""
import json

import pytest

from app.models.models import CadenceState, EmailMessage, Lead, Message
from app.models.program_models import CampaignFamily, ProgramSourceRecord
from app.services.programs import campaigns as C, holds, identity, importer, promote
from tests.test_outreach_program import _h, program  # noqa: F401

HEAD = ("Lead Owner: Manager,Lead Owner,First Name,Last Name,Email,Lead Status,Last Activity,"
        "Last Activity Date,Phone,Nearest/Preferred Location,Campaign Channel,Create Date\n")


def _r(first, last, email, phone, loc, status="Open"):
    return "Mgr,Owner,%s,%s,%s,%s,,10/21/2026,%s,%s,Direct Mail,6/11/2025\n" % (first, last, email, status, phone, loc)


ORIGINAL = (HEAD
            + _r("HANNAH", "MCCOY", "hm@example.com", "2055550001", "0142 - Eastern Gate Memorial Gardens")
            + _r("ANTHONY", "DEAN", "ad@example.com", "", "4394 - Striffler-Hamby Mortuary")       # email only
            + _r("JUNE", "KILGROE", "jk@example.com", "2055550003", "7008 - Kilgroe Funeral Home")     # not Kilgore
            + _r("OMAR", "MKT", "om@example.com", "2055550004", "5117 - OH MKT CENTRAL ALABAMA")
            + _r("RUTH", "SAME", "rs@example.com", "2055550005", "0142 - Eastern Gate Memorial Gardens")
            + _r("RUTH", "SAME", "rs@example.com", "2055550005", "0142 - Eastern Gate Memorial Gardens")  # same person twice
            + _r("PAUL", "HOUSE", "ph@example.com", "2055550006", "4394 - Striffler-Hamby Mortuary")
            + _r("PAULA", "HOUSE", "pa@example.com", "2055550006", "4394 - Striffler-Hamby Mortuary")  # shared household phone
            + _r("DORA", "DISQ", "dd@example.com", "2055550007", "4394 - Striffler-Hamby Mortuary", status="Disqualified")
            + _r("NED", "NOTINT", "nn@example.com", "2055550008", "4394 - Striffler-Hamby Mortuary", status="Not Interested")
            # Ollie Testcase is ALREADY an SCI family (veteran campaign, L001/L002)
            + _r("Ollie", "Testcase", "ollie@example.com", "2145550101", "0142 - Eastern Gate Memorial Gardens"))


def _ca(db, org):
    C.ensure_central_alabama(db, org.id)
    return db.query(CampaignFamily).filter_by(organization_id=org.id, key=C.CENTRAL_ALABAMA_KEY).one()


def _recs(db, org):
    return [r for r in db.query(ProgramSourceRecord).filter_by(organization_id=org.id)
            if r.campaign_family == C.CENTRAL_ALABAMA_KEY]


def test_the_original_export_stages_every_row_without_repairs(program):
    db, org = program["db"], program["org"]
    _ca(db, org)
    res = importer.stage(db, org, ORIGINAL.encode(), filename="Central Alabama.csv", dry_run=False,
                         campaign_family=C.CENTRAL_ALABAMA_KEY)
    s = res["summary"]
    assert s["source_rows"] == 11 and s["staged"] == 11 and not s["errors"]
    assert s["generated_lead_ids"] == 11
    recs = _recs(db, org)
    assert len(recs) == 11
    assert all(r.source_lead_id.startswith("GEN-") and r.source_id_generated for r in recs)
    # the location number is read through; Kilgroe is NOT Kilgore Green
    by = {(r.first_name, r.last_name): r for r in recs}
    assert by[("HANNAH", "MCCOY")].location_status == "mapped"
    assert by[("JUNE", "KILGROE")].location_status == "location_review"
    assert by[("OMAR", "MKT")].location_status == "location_review"
    assert s["location_review_names"] == {"Kilgroe Funeral Home": 1, "OH MKT CENTRAL ALABAMA": 1}
    # the original values are kept as supplied
    raw = json.loads(by[("HANNAH", "MCCOY")].raw_json)
    assert raw["Nearest/Preferred Location"] == "0142 - Eastern Gate Memorial Gardens" and "Lead ID" not in raw
    # email-only candidate counted
    assert s["email_only_candidates"] == 1 and s["no_phone_rows"] == 1


def test_people_are_linked_not_doubled(program):
    db, org = program["db"], program["org"]
    _ca(db, org)
    s = importer.stage(db, org, ORIGINAL.encode(), dry_run=False, campaign_family=C.CENTRAL_ALABAMA_KEY)["summary"]
    recs = {(r.first_name, r.last_name, r.row_number): r for r in _recs(db, org)}
    ruth = [r for k, r in recs.items() if k[0] == "RUTH"]
    assert len(ruth) == 2 and ruth[0].contact_master_key == ruth[1].contact_master_key   # one person, two rows kept
    paul = [r for k, r in recs.items() if k[0] in ("PAUL", "PAULA")]
    assert paul[0].contact_master_key != paul[1].contact_master_key                   # household: never merged
    assert all("different name" in (r.duplicate_review_reason or "") for r in paul)
    # Ollie is already an SCI family: linked to that family, not a new one
    ollie = [r for k, r in recs.items() if k[0] == "Ollie"][0]
    l001 = db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id="L001").one()
    assert ollie.contact_master_key == (l001.contact_master_key or "L001")
    assert s["linked_to_existing_families"] == 1


def test_reimport_is_idempotent_and_matches_earlier_hand_repaired_records(program):
    db, org = program["db"], program["org"]
    _ca(db, org)
    # an earlier load of a hand-repaired copy (IDs typed in, no campaign)
    repaired = ("Lead ID," + HEAD.replace("Nearest/Preferred Location", "Location Friendly Name")
                + "SCI-CA-0001,Mgr,Owner,HANNAH,MCCOY,hm@example.com,Open,,10/21/2026,2055550001,"
                  "Eastern Gate Memorial Gardens,Direct Mail,6/11/2025\n")
    importer.stage(db, org, repaired.encode(), dry_run=False)
    first = importer.stage(db, org, ORIGINAL.encode(), dry_run=False, campaign_family=C.CENTRAL_ALABAMA_KEY)
    assert first["summary"]["reused_earlier_records"] == 1 and first["summary"]["new_records"] == 10
    hannah = db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id="SCI-CA-0001").one()
    assert hannah.campaign_family == C.CENTRAL_ALABAMA_KEY                       # the earlier record now has its campaign
    n = db.query(ProgramSourceRecord).filter_by(organization_id=org.id).count()
    again = importer.stage(db, org, ORIGINAL.encode(), dry_run=False, campaign_family=C.CENTRAL_ALABAMA_KEY)
    assert again["summary"]["new_records"] == 0 and again["summary"]["staged"] == 11
    assert db.query(ProgramSourceRecord).filter_by(organization_id=org.id).count() == n


def test_a_second_lead_made_earlier_for_an_existing_family_is_superseded(program, sample_advisor):
    db, org = program["db"], program["org"]
    _ca(db, org)
    old = Lead(organization_id=org.id, first_name="Ollie", last_name="Testcase", phone="12145550101",
               email="ollie@example.com", status="sent")
    db.add(old)
    db.flush()
    l001 = db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id="L001").one()
    l001.lead_id = old.id
    # the earlier repaired-copy load made a SECOND lead for Ollie (never contacted)
    repaired = ("Lead ID," + HEAD.replace("Nearest/Preferred Location", "Location Friendly Name")
                + "SCI-CA-0099,Mgr,Owner,Ollie,Testcase,ollie@example.com,Open,,10/21/2026,2145550101,"
                  "Eastern Gate Memorial Gardens,Direct Mail,6/11/2025\n")
    importer.stage(db, org, repaired.encode(), dry_run=False)
    dup = Lead(organization_id=org.id, first_name="Ollie", last_name="Testcase", phone="12145550101", status="new")
    db.add(dup)
    db.flush()
    db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id="SCI-CA-0099").one().lead_id = dup.id
    db.commit()
    s = importer.stage(db, org, ORIGINAL.encode(), dry_run=False, campaign_family=C.CENTRAL_ALABAMA_KEY)["summary"]
    assert s["superseded_duplicate_leads"] == 1
    db.refresh(dup)
    rec = db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id="SCI-CA-0099").one()
    assert rec.lead_id == old.id and dup.duplicate_of_lead_id == old.id and dup.status == "duplicate"


def test_historic_statuses_do_not_hold_but_real_dnc_does(program):
    db, org = program["db"], program["org"]
    _ca(db, org)
    importer.stage(db, org, ORIGINAL.encode(), dry_run=False, campaign_family=C.CENTRAL_ALABAMA_KEY)
    by = {r.last_name: r for r in _recs(db, org)}
    assert holds.review_reasons(by["DISQ"]) == [] and holds.review_reasons(by["NOTINT"]) == []
    assert by["DISQ"].source_status == "Disqualified"                            # kept as supplied
    by["DISQ"].source_status = "Do Not Contact"
    assert any("SOURCE STATUS" in x for x in holds.review_reasons(by["DISQ"]))


def test_no_fallback_campaign_and_nothing_sent_on_stage(program):
    db, org = program["db"], program["org"]
    before = (db.query(Message).count(), db.query(EmailMessage).count(), db.query(CadenceState).count(),
              db.query(Lead).count())
    importer.stage(db, org, ORIGINAL.encode(), dry_run=False)                   # no campaign chosen, none in the file
    recs = [r for r in db.query(ProgramSourceRecord).filter_by(organization_id=org.id) if r.source_id_generated]
    assert len(recs) == 11 and all(r.campaign_family is None for r in recs)      # never Veteran / Survey / Re-engagement
    assert (db.query(Message).count(), db.query(EmailMessage).count(), db.query(CadenceState).count(),
            db.query(Lead).count()) == before


def test_a_record_with_no_campaign_is_never_worked(program, sample_advisor):
    db, org = program["db"], program["org"]
    re_eng = db.query(CampaignFamily).filter_by(organization_id=org.id, key="re_engagement").one()
    re_eng.is_active = True                                                      # even if re-engagement were on
    importer.stage(db, org, ORIGINAL.encode(), dry_run=False)
    rec = [r for r in db.query(ProgramSourceRecord).filter_by(organization_id=org.id)
           if r.first_name == "HANNAH"][0]
    lead = Lead(organization_id=org.id, first_name="HANNAH", last_name="MCCOY", phone="12055550001", status="new",
                assigned_to_id=sample_advisor.id)
    db.add(lead)
    db.flush()
    rec.lead_id = lead.id
    db.commit()
    assert identity.send_refusal(db, lead, "cadence") == identity.CAMPAIGN_OFF_REFUSAL


def test_the_campaign_is_its_own_off_and_cannot_be_switched_on_without_copy(program, client):
    db, org = program["db"], program["org"]
    fam = _ca(db, org)
    assert fam.is_active is False and fam.sms_template is None and fam.email_body_template is None
    assert fam.name == "Central Alabama 2025–2026 MS"
    pats = json.loads(fam.source_campaign_patterns)
    assert "Central Alabama 2025-2026 MS" in pats
    assert importer._family_for("Direct Mail> Veteran> Veteran Planning Guide", [fam]) is None
    r = client.post("/program/campaigns/%s/activation" % fam.id, headers=_h(db, program["admin"]),
                    json={"active": True, "confirm": fam.name})
    assert r.status_code == 422 and "no approved text and email copy" in r.json()["detail"]
    db.refresh(fam)
    assert fam.is_active is False


def test_the_ai_follows_the_campaigns_own_rules():
    notes = C.ai_notes_for(C.CENTRAL_ALABAMA_KEY)
    assert "NOT verified" in notes["rules"] and "veteran" in notes["rules"].lower()
    assert "MS" in notes["rules"]                                                # never expanded
    assert C.ai_notes_for("veteran_planning_guide") is None


def test_location_number_is_stripped_but_names_are_never_guessed():
    assert importer.strip_location_number("4394 - Southern Heritage Funeral Home") == "Southern Heritage Funeral Home"
    assert importer.strip_location_number("7008 - Kilgroe Funeral Home") == "Kilgroe Funeral Home"
    assert importer.strip_location_number("Rockco Funeral Home (Montevallo)") == "Rockco Funeral Home (Montevallo)"


@pytest.mark.parametrize("header", ["Nearest/Preferred Location", "Location Friendly Name", "Preferred Location"])
def test_every_location_column_name_is_read(header):
    rows = importer.parse_csv(("First Name,%s\nA,0142 - X\n" % header).encode())
    assert importer._val(rows[0], "location") == "0142 - X"


# ---- independent review follow-ups -----------------------------------------

def _lead_for(db, org, rec, user):
    lead = Lead(organization_id=org.id, first_name=rec.first_name, last_name=rec.last_name,
                phone="1" + (rec.phone or "")[-10:] if rec.phone else None, email=rec.email, status="new",
                assigned_to_id=user.id)
    db.add(lead)
    db.flush()
    rec.lead_id = lead.id
    db.commit()
    return lead


def test_approving_a_family_never_switches_on_a_campaign_without_copy(program, sample_advisor):
    from app.models.program_models import OutreachProgram
    from app.services.programs import send_approval
    db, org = program["db"], program["org"]
    fam = _ca(db, org)
    importer.stage(db, org, ORIGINAL.encode(), dry_run=False, campaign_family=C.CENTRAL_ALABAMA_KEY)
    rec = [r for r in _recs(db, org) if r.first_name == "HANNAH"][0]
    lead = _lead_for(db, org, rec, sample_advisor)
    prog = db.query(OutreachProgram).filter_by(organization_id=org.id).one()
    out = send_approval.approve(db, prog, program["admin"], [lead.id])
    db.refresh(fam)
    assert fam.is_active is False
    assert db.query(CadenceState).filter_by(lead_id=lead.id).count() == 0
    assert any(s["lead_id"] == lead.id for s in out["skipped"])


def test_the_picked_campaign_never_takes_another_campaigns_record(program):
    db, org = program["db"], program["org"]
    _ca(db, org)
    from tests.test_outreach_program import CSV as STANDARD
    before = {r.source_lead_id: r.campaign_family for r in db.query(ProgramSourceRecord).filter_by(organization_id=org.id)}
    s = importer.stage(db, org, STANDARD.encode(), dry_run=False, campaign_family=C.CENTRAL_ALABAMA_KEY)["summary"]
    after = {r.source_lead_id: r.campaign_family for r in db.query(ProgramSourceRecord).filter_by(organization_id=org.id)}
    for k, v in before.items():
        if v:
            assert after[k] == v
    assert s["kept_in_other_campaign"] >= 1


def test_the_same_row_on_two_campaigns_files_is_two_records(program):
    db, org = program["db"], program["org"]
    _ca(db, org)
    importer.stage(db, org, ORIGINAL.encode(), dry_run=False, campaign_family=C.CENTRAL_ALABAMA_KEY)
    importer.stage(db, org, ORIGINAL.encode(), dry_run=False, campaign_family="cemetery_x_sell")
    ca = [r for r in _recs(db, org) if r.first_name == "HANNAH"]
    other = [r for r in db.query(ProgramSourceRecord).filter_by(organization_id=org.id)
             if r.first_name == "HANNAH" and r.campaign_family == "cemetery_x_sell"]
    assert len(ca) == 1 and len(other) == 1


def test_a_lead_with_a_reply_or_visit_is_never_marked_duplicate(program, sample_advisor):
    from app.models.models import Reply
    db, org = program["db"], program["org"]
    _ca(db, org)
    old = Lead(organization_id=org.id, first_name="Ollie", last_name="Testcase", phone="12145550101", status="sent")
    db.add(old)
    db.flush()
    db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id="L001").one().lead_id = old.id
    repaired = ("Lead ID," + HEAD.replace("Nearest/Preferred Location", "Location Friendly Name")
                + "SCI-CA-0099,Mgr,Owner,Ollie,Testcase,ollie@example.com,Open,,10/21/2026,2145550101,"
                  "Eastern Gate Memorial Gardens,Direct Mail,6/11/2025\n")
    importer.stage(db, org, repaired.encode(), dry_run=False)
    dup = Lead(organization_id=org.id, first_name="Ollie", last_name="Testcase", phone="12145550101", status="new")
    db.add(dup)
    db.flush()
    db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id="SCI-CA-0099").one().lead_id = dup.id
    db.add(Reply(lead_id=dup.id, body="yes call me"))
    db.commit()
    s = importer.stage(db, org, ORIGINAL.encode(), dry_run=False, campaign_family=C.CENTRAL_ALABAMA_KEY)["summary"]
    db.refresh(dup)
    assert s["superseded_duplicate_leads"] == 0 and dup.status == "new" and dup.duplicate_of_lead_id is None
    rec = db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id="SCI-CA-0099").one()
    assert "a person decides" in rec.duplicate_review_reason


def test_a_superseded_lead_is_refused_by_the_send_gate(program, sample_advisor):
    db, org = program["db"], program["org"]
    lead = Lead(organization_id=org.id, first_name="X", last_name="Y", phone="12145559999", status="duplicate",
                assigned_to_id=sample_advisor.id)
    db.add(lead)
    db.commit()
    assert identity.send_refusal(db, lead, "cadence") == identity.DUPLICATE_REVIEW_REFUSAL


def test_stop_status_holds():
    rec = ProgramSourceRecord(location_status="mapped", location_id="x", source_status="STOP")
    assert any("SOURCE STATUS" in x for x in holds.review_reasons(rec))
    rec.source_status = "Unworked"
    assert holds.review_reasons(rec) == []


def test_follow_ups_use_only_the_campaigns_own_words(program, sample_advisor):
    db, org = program["db"], program["org"]
    fam = _ca(db, org)
    fam.sms_template = "APPROVED-CA-COPY {location_name}"
    db.commit()
    importer.stage(db, org, ORIGINAL.encode(), dry_run=False, campaign_family=C.CENTRAL_ALABAMA_KEY)
    rec = [r for r in _recs(db, org) if r.first_name == "HANNAH"][0]
    lead = _lead_for(db, org, rec, sample_advisor)
    for touch in (1, 3):
        txt = identity.cadence_text(db, lead, touch)
        assert txt is None or txt.startswith("APPROVED-CA-COPY")
