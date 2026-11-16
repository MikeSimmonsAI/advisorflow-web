"""Nov 16 launch of the Historical Family Review: the file's own Need Type
becomes each family's tier, nobody is held, and the launch audience takes
every family of the file (pre-need and at-need) - owner, Nov 16 1:04 PM - while
the default File Review audience stays pre-need only. Synthetic data."""
import json
import uuid

import pytest

from app.models.models import Lead, Organization
from app.models.program_models import ProgramSourceRecord
from app.models.reset_models import WorkspaceResetItem
from app.services.programs import restland_launch as RL


def _family(db, org, need, prio, phone):
    lead = Lead(id=str(uuid.uuid4()), organization_id=org.id, first_name="Pat", last_name="Synthetic",
                phone=phone, status="new")
    db.add(lead)
    db.flush()
    rec = ProgramSourceRecord(organization_id=org.id, source_lead_id="GEN-" + uuid.uuid4().hex[:8], lead_id=lead.id,
                              raw_json=json.dumps({"Need Type": need, "Review Priority": prio}))
    db.add(rec)
    db.flush()
    return lead, rec


def test_need_type_becomes_the_tier_and_the_launch_takes_the_whole_file(db_session, sample_org, monkeypatch):
    monkeypatch.delenv("SMS_PROGRAM_ORG_SCI", raising=False)
    db = db_session
    pre1, _ = _family(db, sample_org, "Pre Need", "1 · Texas Pre Need, two channels", "2145550101")
    pre2, _ = _family(db, sample_org, "Pre Need", "2 · Texas Pre Need, one channel", "2145550102")
    conf, conf_rec = _family(db, sample_org, "Pre Need", "3 · Confirm location and fit", "2145550103")
    at, at_rec = _family(db, sample_org, "At Need", "4 · Review service history", "2145550104")
    odd, _ = _family(db, sample_org, "", "1", "2145550105")
    keep = Lead(organization_id=sample_org.id, first_name="Already", last_name="Set", phone="2145550106",
                status="new", tier="imminent")
    db.add(keep)
    db.commit()
    out = RL.classify(db, sample_org, "classify-1")
    s = out["summary"]
    assert s["tier_set"] == {"pre_need": 3, "at_need": 1}
    assert s["need_type_not_recognized"] == {"(blank)": 1}
    for l in (pre1, pre2, conf, at, odd, keep):
        db.refresh(l)
    assert (pre1.tier, pre2.tier, conf.tier, at.tier, odd.tier, keep.tier) == \
        ("pre_need", "pre_need", "pre_need", "at_need", None, "imminent")      # a set tier is never changed
    db.refresh(conf_rec)
    db.refresh(at_rec)
    assert conf_rec.on_hold is False and at_rec.on_hold is False              # nobody held
    items = db.query(WorkspaceResetItem).filter_by(run_id=out["run_id"]).all()
    assert len(items) == 4 and all(i.before_json for i in items)              # every change can be undone
    assert RL.classify(db, sample_org, "classify-1")["already"] is True
    from app.services.programs import workspace_reset as WR
    # Default File Review audience: pre-need only (unchanged).
    ready = {lid for lid, m in WR.plan(db, sample_org)["members"].items() if m["segment"] == "ready"}
    assert {pre1.id, pre2.id, conf.id} <= ready and at.id not in ready and odd.id not in ready
    # This launch (WORKSPACE_LAUNCH_TIERS=pre_need,at_need): the whole file.
    ready = {lid for lid, m in WR.plan(db, sample_org, tiers=("pre_need", "at_need"))["members"].items()
             if m["segment"] == "ready"}
    assert {pre1.id, pre2.id, conf.id, at.id} <= ready and odd.id not in ready and keep.id not in ready


def test_classify_refuses_sci(db_session, monkeypatch):
    sci = Organization(name="Service Corporation International", slug="sci-cls-" + uuid.uuid4().hex[:6])
    db_session.add(sci)
    db_session.commit()
    monkeypatch.setenv("SMS_PROGRAM_ORG_SCI", sci.id)
    with pytest.raises(RL.LaunchRefused):
        RL.classify(db_session, sci, "x")
