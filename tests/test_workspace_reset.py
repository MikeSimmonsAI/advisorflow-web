"""WORKSPACE DATA RESET + BATCH 1 PREPARATION (Issue #507). Synthetic data only;
nothing reaches a provider."""
import json
from datetime import datetime, timedelta

import pytest

from app.models.models import (BookingLink, CadenceState, CadenceTemplate, Lead, Message, PipelineConversation,
                               Reply, SuppressionEntry, User)
from app.models.program_models import CampaignFamily, ProgramSourceRecord
from app.models.reset_models import CampaignAudienceMember, LeadDisposition, WorkspaceResetItem
from app.services import sms_programs
from app.services.auth_service import create_access_token, hash_password
from app.services.programs import campaigns, single_location_cutover as C, workspace_reset as WR


def L(db, org, i, **kw):
    d = dict(organization_id=org.id, first_name="Fam%d" % i, last_name="Synth", phone="1214555%04d" % i,
             email="f%d@example.com" % i, status="new", tier="pre_need")
    d.update(kw)
    l = Lead(**d)
    db.add(l)
    db.flush()
    return l


@pytest.fixture()
def ws(db_session, sample_org, sample_advisor):
    db, org = db_session, sample_org
    leads = {f"ready{i}": L(db, org, i) for i in range(30)}
    leads["test"] = L(db, org, 100, is_test=True)
    leads["dnc"] = L(db, org, 101, status="dnc")
    leads["canon"] = L(db, org, 102)
    leads["dup"] = L(db, org, 103, phone=leads["canon"].phone, email=leads["canon"].email,
                     first_name=leads["canon"].first_name, status="dnc", is_duplicate=True,
                     duplicate_of_lead_id=leads["canon"].id)
    leads["review"] = L(db, org, 104, status="needs_tier_review", tier="partial")
    leads["bad_phone"] = L(db, org, 105, phone="999")
    leads["no_phone"] = L(db, org, 106, phone=None)
    leads["sold"] = L(db, org, 107, tier="contract_sold")
    leads["replied"] = L(db, org, 108)
    leads["booked"] = L(db, org, 109)
    leads["legacy"] = L(db, org, 110)
    leads["shared"] = L(db, org, 111, phone=leads["ready0"].phone, first_name="Spouse")
    leads["supp"] = L(db, org, 112)
    leads["future"] = L(db, org, 113)
    db.commit()
    db.add(Reply(lead_id=leads["replied"].id, body="old reply", source="sms", received_at=datetime(2026, 8, 1)))
    db.add(BookingLink(lead_id=leads["booked"].id, user_id=sample_advisor.id, status="booked",
                       booked_time=datetime(2026, 8, 12, 10)))
    db.add(BookingLink(lead_id=leads["future"].id, user_id=sample_advisor.id, status="booked",
                       booked_time=datetime.utcnow() + timedelta(days=4)))
    db.add(CadenceState(lead_id=leads["legacy"].id, status="ACTIVE", current_touch_number=0,
                        next_touch_due_at=datetime(2026, 6, 27)))
    db.add(CadenceState(lead_id=leads["ready29"].id, status="active", current_touch_number=5,
                        next_touch_due_at=datetime(2026, 11, 6)))
    db.add(SuppressionEntry(organization_id=org.id, phone="+1" + leads["supp"].phone[1:], reason="Permanent DNC",
                            source="MANUAL"))
    db.add(PipelineConversation(organization_id=org.id, lead_id=leads["ready28"].id, advisor_id=sample_advisor.id,
                                stage="outreach_sent", next_send_at=datetime(2026, 9, 21)))
    db.add(CadenceTemplate(organization_id=org.id, name="Funeral Home 9-Touch", industry="funeral",
                           is_default=True, is_active=True))
    db.commit()
    C.apply(db, org, hero_title="Restland")
    return {"db": db, "org": org, "L": leads}


def test_phone_and_email_normalization_never_rewrites_the_lead(ws):
    assert WR.e164("12145551234") == "+12145551234" and WR.e164("(214) 555-1234") == "+12145551234"
    assert WR.e164("999") is None and WR.e164("11145551234") is None
    assert WR.email_issue("a@b") == "malformed" and WR.email_issue("") == "missing"
    WR.apply(ws["db"], ws["org"], "k1")
    ws["db"].refresh(ws["L"]["ready1"])
    assert ws["L"]["ready1"].phone == "12145550001"                  # original kept
    d = ws["db"].query(LeadDisposition).filter_by(lead_id=ws["L"]["ready1"].id).one()
    assert d.phone_e164 == "+12145550001"


def test_plan_is_read_only_and_classifies(ws):
    db = ws["db"]
    p = WR.plan(db, ws["org"])
    assert db.query(LeadDisposition).count() == 0 and db.query(CampaignAudienceMember).count() == 0
    D = p["dispositions"]
    assert D[ws["L"]["test"].id]["disposition"] == "test"
    assert D[ws["L"]["dup"].id]["disposition"] == "merged_duplicate"
    assert D[ws["L"]["canon"].id]["disposition"] == "retain"        # the duplicate's dnc never blocks the canonical
    assert D[ws["L"]["dnc"].id]["disposition"] == "suppressed"
    assert D[ws["L"]["supp"].id]["disposition"] == "suppressed"
    assert D[ws["L"]["review"].id]["review_queue"] == "needs_classification"
    assert D[ws["L"]["bad_phone"].id]["review_queue"] is None or D[ws["L"]["bad_phone"].id]["phone_issue"] == "invalid"
    assert D[ws["L"]["no_phone"].id]["disposition"] == "email_only"


def test_audience_exclusions_and_one_text_per_number(ws):
    p = WR.plan(ws["db"], ws["org"])
    M = p["members"]
    seg = lambda k: M[ws["L"][k].id]["segment"]
    assert seg("replied") == "prior_conversation" and seg("booked") == "past_appointment"
    assert seg("legacy") == "earlier_sequence" and seg("ready29") == "earlier_sequence"
    assert seg("ready28") == "earlier_sequence"                          # old AI pipeline
    assert seg("supp") == "suppressed" and seg("future") == "upcoming_appointment"
    assert sorted([seg("shared"), seg("ready0")]) == ["ready", "shared_number"]   # one text per number
    for k in ("test", "dup", "dnc", "review", "sold", "no_phone", "bad_phone"):
        assert ws["L"][k].id not in M, k                                # never in the pre-need pool
    dests = [m["destination"] for m in M.values() if m["segment"] == "ready"]
    assert len(dests) == len(set(dests))
    assert len(p["batch"]) == 25


def test_apply_pauses_legacy_prepares_draft_and_is_idempotent(ws):
    db, org = ws["db"], ws["org"]
    r1 = WR.apply(db, org, "k1")
    assert r1["status"] == "applied" and not r1["already"]
    states = {s.lead_id: s.status for s in db.query(CadenceState).all()}
    assert set(states.values()) == {"paused"}
    pc = db.query(PipelineConversation).one()
    assert pc.paused is True and pc.next_send_at is None
    fam = db.query(CampaignFamily).filter_by(organization_id=org.id, key=WR.CAMPAIGN_KEY).one()
    assert fam.is_active is False and campaigns.activation_blocker(fam, db) is None   # every step drafted
    assert [n for n, _d, _h in campaigns.sms_steps(db, org.id)] == [1, 2, 3]
    staged = db.query(CampaignAudienceMember).filter_by(is_staged=True).count()
    assert staged == 25
    assert db.query(ProgramSourceRecord).filter(ProgramSourceRecord.approved_to_send_at.isnot(None)).count() == 0
    assert db.query(ProgramSourceRecord).filter(ProgramSourceRecord.campaign_family.isnot(None)).count() == 0
    n_items = db.query(WorkspaceResetItem).count()
    r2 = WR.apply(db, org, "k1")
    assert r2["already"] and db.query(WorkspaceResetItem).count() == n_items
    assert db.query(CampaignFamily).filter_by(key=WR.CAMPAIGN_KEY).count() == 1


def test_paused_legacy_state_is_not_picked_up(ws):
    from app.services import cadence_service as cs
    db = ws["db"]
    WR.apply(db, ws["org"], "k1")
    before = db.query(Message).count()
    cs.run_due_cadences(db, now=datetime(2026, 11, 7, 16))
    assert db.query(Message).count() == before


def test_rollback_restores_exactly(ws):
    db, org = ws["db"], ws["org"]
    r = WR.apply(db, org, "k1")
    out = WR.rollback(db, org, r["run_id"])
    assert out["status"] == "rolled_back"
    st = {s.lead_id: s.status for s in db.query(CadenceState).all()}
    assert st[ws["L"]["legacy"].id] == "ACTIVE" and st[ws["L"]["ready29"].id] == "active"
    pc = db.query(PipelineConversation).one()
    assert pc.paused is False and pc.next_send_at == datetime(2026, 9, 21)
    assert db.query(CadenceTemplate).filter_by(name="Funeral Home 9-Touch").one().is_default is True
    assert db.query(CadenceTemplate).filter_by(name=WR.CADENCE_NAME).count() == 0
    assert db.query(CampaignFamily).filter_by(key=WR.CAMPAIGN_KEY).count() == 0
    assert db.query(LeadDisposition).count() == 0 and db.query(CampaignAudienceMember).count() == 0


def test_rollback_refused_once_the_campaign_is_on(ws):
    db, org = ws["db"], ws["org"]
    r = WR.apply(db, org, "k1")
    fam = db.query(CampaignFamily).filter_by(key=WR.CAMPAIGN_KEY).one()
    fam.is_active = True
    db.commit()
    with pytest.raises(WR.ResetRefused):
        WR.rollback(db, org, r["run_id"])


def test_sci_org_refused(ws):
    ws["org"].name = sms_programs.SCI_ORG_NAME
    ws["db"].commit()
    with pytest.raises(WR.ResetRefused):
        WR.apply(ws["db"], ws["org"], "k1")


def test_campaign_key_is_own_copy_and_drafts_name_mike_and_restland():
    assert WR.CAMPAIGN_KEY == campaigns.RESTLAND_FFR_KEY and WR.CAMPAIGN_KEY in campaigns.OWN_COPY_KEYS
    for n, t in WR.SMS_COPY.items():
        assert "Mike Simmons" in t and "Restland" in t and t.rstrip().endswith("Reply STOP to opt out.")
        for word in ("must", "required", "problem", "urgent"):
            assert word not in t.lower()


def test_review_endpoints_scoped_and_manager_only(client, ws):
    db, org = ws["db"], ws["org"]
    WR.apply(db, org, "k1")
    admin = User(organization_id=org.id, email="rl-admin@example.com", password_hash=hash_password("Pass12345!"),
                 full_name="Admin", role="org_admin", is_active=True, must_change_password=False)
    adv = User(organization_id=org.id, email="rl-adv@example.com", password_hash=hash_password("Pass12345!"),
               full_name="Adv", role="advisor", is_active=True, must_change_password=False)
    db.add_all([admin, adv])
    db.commit()
    h = {"Authorization": "Bearer %s" % create_access_token(admin, db)}
    s = client.get("/program/command/data-review/summary", headers=h).json()
    assert s["batch_1"] == 25 and s["campaign"]["is_active"] is False and s["campaign"]["sender"] == "+18553601164"
    lst = client.get("/program/command/data-review/list?bucket=batch_1&limit=100", headers=h).json()
    assert lst["total"] == 25 and len(lst["items"]) == 25
    assert client.get("/program/command/data-review/list?bucket=nope", headers=h).status_code == 400
    ha = {"Authorization": "Bearer %s" % create_access_token(adv, db)}
    assert client.get("/program/command/data-review/summary", headers=ha).status_code == 403
