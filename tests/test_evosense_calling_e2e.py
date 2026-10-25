"""VA calling, end to end, on SYNTHETIC owners and 555-01xx numbers. Nothing
dials, nothing is sent: the queue, the one-click outcomes, the VA tasks, the
follow-ups, the hand-offs, the appointment and the promotion are exercised
through the existing services only."""
from datetime import datetime, timedelta

import pytest

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseFollowUp, EvoSenseHandoff,
                                        EvoSenseObservation, EvoSenseOwner, EvoSenseOwnership, EvoSenseProperty)
from app.models.wholesale_models import WholesaleWorkException
from app.services import wholesale_enrichment as WE
from app.services.evosense import calling as CL
from app.services.evosense import contacts as CT
from app.services.evosense import opportunity as OP
from app.services.evosense import providers as PV

OUTCOMES_EXPECTED = {
    # outcome: (still in the call queue?, workflow after, a hand-off open?)
    "no_answer": (True, "CONTACT_ATTEMPTED", False),
    "voicemail": (True, "CONTACT_ATTEMPTED", False),
    "wrong_number": (False, None, False),
    "do_not_call": (False, "DO_NOT_CONTACT", False),
    "not_interested": (False, "NOT_INTERESTED", False),
    "not_owner": (False, "NOT_INTERESTED", False),
    "interested": (True, "HUMAN_HANDOFF", True),
    "appointment": (True, "HUMAN_HANDOFF", True),
    "deceased": (True, "HUMAN_HANDOFF", True),
}


def _owner_with_phone(db, org, n, *, otype="individual"):
    p = EvoSenseProperty(organization_id=org, street_address="%d Synthetic Call St" % n, city="Dallas",
                         state="TX", zip_code="75216", county="Dallas", property_type="single_family",
                         is_test=False, status="new", opportunity_score=70, priority_score=90 - n)
    db.add(p)
    db.flush()
    db.add(EvoSenseObservation(organization_id=org, property_id=p.id, provider_key="dcad", connector_kind="real",
                               capability="OWNERSHIP", source_reference="synthetic:call:%d" % n))
    o = EvoSenseOwner(organization_id=org, display_name="SYNTH CALLER%d OWNER" % n, owner_type=otype,
                      mailing_street="%d Synthetic Call St" % n, mailing_zip="75216")
    db.add(o)
    db.flush()
    db.add(EvoSenseOwnership(organization_id=org, property_id=p.id, owner_id=o.id, is_current=True, source="dcad"))
    db.flush()
    CT.apply_result(db, o, WE.EnrichmentResult(status=WE.STATUS_SUCCEEDED, provider="manual",
                                               owner_name="Synth Caller%d Owner" % n,
                                               phones=[WE.EnrichmentPhone(number="21455501%02d" % n,
                                                                          phone_type="mobile")]),
                    PV.PROVIDERS["manual"])
    db.flush()
    OP.refresh(db, org, [p])
    return p


def _tasks(db, org, prop, open_only=True):
    q = db.query(WholesaleWorkException).filter(WholesaleWorkException.organization_id == org,
                                                WholesaleWorkException.kind == CL.VA_KIND,
                                                WholesaleWorkException.subject_id == prop.id)
    if open_only:
        q = q.filter(WholesaleWorkException.status.in_(("open", "assigned", "needs_more_info")))
    return q.all()


def _in_queue(db, org, prop):
    return prop.id in {r["property_id"] for r in CL.call_queue(db, org)["rows"]}


@pytest.mark.parametrize("outcome", sorted(OUTCOMES_EXPECTED))
def test_every_call_outcome_updates_the_existing_records(db_session, sample_org, sample_advisor, outcome):
    db, org = db_session, sample_org.id
    p = _owner_with_phone(db, org, 1)
    db.commit()
    assert _in_queue(db, org, p)
    assert CL.sweep(db, org) == 1 and CL.sweep(db, org) == 1           # re-raising returns the SAME task
    db.commit()
    assert len(_tasks(db, org, p)) == 1                               # one VA task, never two
    out = CL.record_outcome(db, org, p, sample_advisor, outcome=outcome, note="synthetic test call")
    db.commit()
    db.refresh(p)
    queued, wf, handoff = OUTCOMES_EXPECTED[outcome]
    assert _in_queue(db, org, p) is queued, out
    if wf:
        assert (OP.compact(p) or {}).get("workflow_state", {}).get("key") == wf
    has = db.query(EvoSenseHandoff).filter(EvoSenseHandoff.property_id == p.id,
                                           EvoSenseHandoff.status == "open").count() > 0
    assert has is handoff
    open_tasks = _tasks(db, org, p)
    if outcome in ("no_answer", "voicemail"):
        assert len(open_tasks) == 1 and "Call attempted" in open_tasks[0].outcome_note
        assert db.query(EvoSenseFollowUp).filter(EvoSenseFollowUp.property_id == p.id).count() == 1
    else:
        assert open_tasks == []                                       # the call resolved the VA task
    CL.sweep(db, org)
    db.commit()
    assert len(_tasks(db, org, p)) == (1 if outcome in ("no_answer", "voicemail") else 0)   # no duplicate


def test_a_callback_is_never_lost_and_never_raised_early(db_session, sample_org, sample_advisor):
    db, org = db_session, sample_org.id
    p = _owner_with_phone(db, org, 2)
    CL.sweep(db, org)
    when = (datetime.utcnow() + timedelta(days=2)).replace(microsecond=0).isoformat()
    CL.record_outcome(db, org, p, sample_advisor, outcome="callback", callback_at=when, note="after work")
    db.commit()
    assert _tasks(db, org, p) == []
    CL.sweep(db, org)
    assert _tasks(db, org, p) == []                                   # not before the promised time
    fu = db.query(EvoSenseFollowUp).filter(EvoSenseFollowUp.property_id == p.id).one()
    fu.due_at = datetime.utcnow() - timedelta(minutes=5)              # the promised time arrives
    db.commit()
    CL.sweep(db, org)
    db.commit()
    t = _tasks(db, org, p)
    assert len(t) == 1 and t[0].title.startswith("Call back the owner")


def test_appointment_hand_off_booking_and_promotion(db_session, sample_org, sample_advisor):
    from app.services import wholesale_appointments as WA
    from app.services.evosense import promotion as PR
    db, org = db_session, sample_org.id
    p = _owner_with_phone(db, org, 3)
    CL.record_outcome(db, org, p, sample_advisor, outcome="appointment", note="Thursday afternoon")
    db.commit()
    h = db.query(EvoSenseHandoff).filter(EvoSenseHandoff.property_id == p.id).one()
    assert "APPOINTMENT" in h.reasons and "Book the appointment" in h.next_action
    out = PR.promote(db, org, p, sample_advisor)
    db.commit()
    assert out["deal_id"] and not out["already"]
    when = (datetime.utcnow() + timedelta(days=4)).replace(microsecond=0).isoformat()
    a = WA.book(db, org, sample_advisor, {"deal_id": out["deal_id"], "evosense_property_id": p.id, "starts_at": when})
    again = WA.book(db, org, sample_advisor, {"deal_id": out["deal_id"], "evosense_property_id": p.id,
                                              "starts_at": when})
    assert again.id == a.id                                           # one visit, never two
    db.refresh(p)
    OP.refresh(db, org, [p])
    assert (OP.compact(p) or {}).get("workflow_state", {}).get("key") in ("APPOINTMENT", "ANALYSIS")
    assert not _in_queue(db, org, p)                                  # a deal is worked in Wholesale


def test_a_number_already_suppressed_is_never_queued(db_session, sample_org):
    from app.models.models import SuppressionEntry
    db, org = db_session, sample_org.id
    p = _owner_with_phone(db, org, 4)
    cp = db.query(EvoSenseContactPoint).filter(EvoSenseContactPoint.value == "+12145550104").one()
    from app.services.compliance_service import usable_us_phone
    db.add(SuppressionEntry(organization_id=org, phone=usable_us_phone(cp.value), reason="synthetic"))
    db.commit()
    rows = [r for r in CL.call_queue(db, org)["rows"] if r["property_id"] == p.id]
    assert rows == []                                                 # no callable number left
