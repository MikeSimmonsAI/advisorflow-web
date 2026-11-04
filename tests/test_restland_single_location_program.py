"""RESTLAND ON THE PROGRAM SHELL - one location, existing families (Issue #507).

Synthetic data only. Nothing reaches Twilio, Resend or any provider: send
paths are asserted to REFUSE before a provider call.
"""
import json

import pytest

from app.models.models import Lead, Organization, User
from app.models.program_models import (
    CampaignFamily, LocationProfile, OutreachProgram, ProgramSourceRecord, WorkspaceSwitch,
)
from app.services import sms_programs
from app.services.auth_service import create_access_token, hash_password
from app.services.programs import program_profile, readiness_check, single_location_cutover as C


def _families(db, org, n=5, dnc=1):
    out = []
    for i in range(n):
        lead = Lead(organization_id=org.id, first_name="Fam%d" % i, last_name="Synthetic",
                    phone="+1214555%04d" % i, email="fam%d@example.com" % i,
                    status="dnc" if i < dnc else "new", tier="pre_need")
        db.add(lead)
        out.append(lead)
    db.commit()
    return out


def _admin(db, org, email="restland-admin@example.com"):
    u = User(organization_id=org.id, email=email, password_hash=hash_password("Pass12345!"),
             full_name="Restland Admin", role="org_admin", is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, user):
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


@pytest.fixture()
def cut(db_session, sample_org):
    db = db_session
    leads = _families(db, sample_org)
    out = C.apply(db, sample_org, hero_title="Restland", contact_name="Mike Simmons")
    return {"db": db, "org": sample_org, "leads": leads, "out": out}


# ── cutover: additive, complete, reversible ─────────────────────────────────

def test_preflight_writes_nothing_and_reports_baseline(db_session, sample_org):
    _families(db_session, sample_org, n=3)
    pre = C.preflight(db_session, sample_org)
    assert pre["problems"] == []
    assert pre["baseline"]["leads"] == 3
    assert db_session.query(OutreachProgram).count() == 0
    assert db_session.query(ProgramSourceRecord).count() == 0


def test_apply_links_every_family_once_with_everything_off(cut):
    db, org = cut["db"], cut["org"]
    rec = cut["out"]["reconciliation"]
    assert rec["ok"] and rec["leads"] == 5 and rec["linked_leads"] == 5
    assert rec["duplicate_links"] == 0 and rec["orphan_records"] == 0 and rec["cross_tenant_links"] == 0
    assert rec["records_with_campaign"] == 0 and rec["active_campaign_families"] == 0
    prog = db.query(OutreachProgram).filter_by(organization_id=org.id).one()
    assert prog.ai_setter_mode == "off" and prog.require_send_approval is True
    assert (prog.daily_batch_count or 0) == 0 and prog.staff_sms_alerts_enabled is False
    real = program_profile.real_locations(db, org.id)
    assert [p.official_name for p in real] == [org.name]
    assert db.query(LocationProfile).filter_by(organization_id=org.id, is_review_bucket=True).count() == 1
    # Leads themselves are untouched: same ids, same statuses.
    for lead in cut["leads"]:
        db.refresh(lead)
    assert sorted(l.status for l in cut["leads"]) == ["dnc", "new", "new", "new", "new"]
    assert all(r.source_lead_id == r.lead_id for r in db.query(ProgramSourceRecord).all())


def test_second_apply_is_refused_and_writes_nothing(cut):
    db, org = cut["db"], cut["org"]
    with pytest.raises(C.CutoverRefused):
        C.apply(db, org)
    assert db.query(ProgramSourceRecord).filter_by(organization_id=org.id).count() == 5


def test_sci_org_is_refused(db_session, sample_org):
    sample_org.name = sms_programs.SCI_ORG_NAME
    db_session.commit()
    pre = C.preflight(db_session, sample_org)
    assert any("SCI" in p for p in pre["problems"])


def test_rollback_removes_exactly_what_was_created(cut):
    db, org = cut["db"], cut["org"]
    out = C.rollback(db, org, cut["out"]["run_id"])
    assert out["deleted_source_records"] == 5
    after = out["after"]
    assert after["programs"] == 0 and after["source_records"] == 0 and after["location_profiles"] == 0
    assert after["campaign_families"] == 0 and after["leads"] == 5


def test_rollback_refused_after_a_family_was_worked(cut):
    db, org = cut["db"], cut["org"]
    r = db.query(ProgramSourceRecord).first()
    from datetime import datetime
    r.approved_to_send_at = datetime.utcnow()
    db.commit()
    with pytest.raises(C.CutoverRefused):
        C.rollback(db, org, cut["out"]["run_id"])
    assert db.query(ProgramSourceRecord).count() == 5


# ── no message goes out ─────────────────────────────────────────────────────

def test_texts_held_on_every_path_until_the_workspace_switch_is_on(cut):
    db, org = cut["db"], cut["org"]
    lead = next(l for l in cut["leads"] if l.status == "new")
    assert sms_programs.program_text_refusal(db, lead) == sms_programs.PROGRAM_TEXTS_OFF_REFUSAL
    from app.services import sms_service
    with pytest.raises(ValueError, match="PROGRAM TEXTS OFF"):
        sms_service._final_send_guards(db, lead, "hello")
    db.add(WorkspaceSwitch(organization_id=org.id, texts_on=True))
    db.commit()
    assert sms_programs.program_text_refusal(db, lead) is None


def test_automated_sends_refused_because_no_campaign_is_assigned(cut):
    from app.services.programs import identity
    db = cut["db"]
    lead = next(l for l in cut["leads"] if l.status == "new")
    assert identity.send_refusal(db, lead, "cadence") is not None


def test_org_without_a_program_is_unaffected(db_session, sample_org):
    lead = _families(db_session, sample_org, n=1, dnc=0)[0]
    assert sms_programs.is_program_org(db_session, sample_org.id) is False
    assert sms_programs.program_text_refusal(db_session, lead) is None


def test_inbound_reply_is_answered_only_by_the_program(cut, sample_advisor, ai_background_on):
    from app.services import pipeline_service
    db = cut["db"]
    lead = next(l for l in cut["leads"] if l.status == "new")
    assert sms_programs.is_program_org(db, lead.organization_id)
    out = pipeline_service.process_inbound_reply(db, lead, sample_advisor, None)
    assert out == {"action": "sci_program"}


# ── what the shell draws ────────────────────────────────────────────────────

def test_profile_names_the_business_and_one_location(cut):
    db, org = cut["db"], cut["org"]
    prog = db.query(OutreachProgram).filter_by(organization_id=org.id).one()
    p = program_profile.profile(db, prog)
    assert p["is_sci"] is False and p["short_name"] == "Restland"
    assert p["live_label"] == "Restland Live" and p["single_location"] is True
    assert p["location"]["name"] == org.name and p["sci_only_panels"] is False
    assert "SCI" not in p["footer"]


def test_sci_profile_keeps_its_wording(db_session, sample_org):
    from app.services.programs import setup
    sample_org.name = sms_programs.SCI_ORG_NAME
    db_session.commit()
    prog = setup.ensure_program(db_session, sample_org, name=sample_org.name)
    db_session.commit()
    p = program_profile.profile(db_session, prog)
    assert p["is_sci"] and p["short_name"] == "SCI" and p["live_label"] == "SCI Live"
    assert p["footer"] == program_profile.SCI_FOOTER and p["sci_only_panels"] is True


def test_status_endpoint_carries_the_profile(client, cut):
    db, org = cut["db"], cut["org"]
    admin = _admin(db, org)
    r = client.get("/program/status", headers=_h(db, admin))
    assert r.status_code == 200
    body = r.json()
    assert body["active"] is True and body["profile"]["short_name"] == "Restland"


def test_other_workspace_cannot_read_restland_program(client, cut):
    db = cut["db"]
    other = Organization(name="Other Synthetic Co", slug="other-synth", plan="standard", industry="generic")
    db.add(other)
    db.commit()
    stranger = _admin(db, other, email="stranger@example.com")
    r = client.get("/program/dashboard", headers=_h(db, stranger))
    assert r.status_code == 404      # its own workspace has no program; Restland's is never served


def test_launch_checklist_is_restlands_own(cut):
    db, org = cut["db"], cut["org"]
    gates = readiness_check.live_gates(db, org.id)
    keys = {g["key"]: g for g in gates}
    text = json.dumps(gates)
    assert "Kerry" not in text and "Oaklawn" not in text and "844" not in text
    assert keys["L1"]["group"] == "verified" and keys["L2"]["group"] == "verified"
    assert keys["S2"]["group"] == "approval"          # texts off until the owner says
    assert keys["S1"]["group"] in ("external", "approval")


def test_launcher_keeps_restland_identity(cut):
    from app.services import workspace_access
    db, org = cut["db"], cut["org"]
    admin = _admin(db, org)
    from app.models.sales_models import Membership
    db.add(Membership(user_id=admin.id, scope_type="customer_org", scope_id=org.id, role="org_admin", is_active=True))
    db.commit()
    ctx = workspace_access.authorized_contexts(db, admin)
    w = next(x for x in ctx["workspace_contexts"] if x["organization_id"] == org.id)
    assert w["is_program"] is True and w["program_is_sci"] is False
