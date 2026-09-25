"""The SANDBOX review scenario, as code — used by the tests and by
`scripts/seed_evosense_review.py`. Everything it creates is is_test and
labelled SANDBOX / TEST. It never touches a real organization's data: the
caller passes the organization it created for the purpose.
"""
from __future__ import annotations

from typing import Any, Dict

from app.models.evosense_models import EvoSenseEngagement, EvoSenseProperty, EvoSenseStrategy
from app.services.evosense import common as C
from app.services.evosense import conversation as CV
from app.services.evosense import hunt as HU
from app.services.evosense import providers as PV
from app.services.evosense import strategy as ST

SUPPRESSED_PHONE = "+13185550163"      # Harold D. Benning — already on the org's suppression list

FLAGSHIP_REPLY = ("Yes, I inherited it last year. It's vacant and needs work. "
                  "I'd probably sell around 150 if we can close quickly.")
WRONG_PERSON_REPLY = "Wrong person. Stop texting me."
NOT_NOW_REPLY = "Not interested right now. Maybe after the holidays."

DFW = dict(
    name="DFW Distressed SFR (TEST)",
    description="SANDBOX strategy for the Phase 7 review. Synthetic data only.",
    states=["TX"], counties=["Dallas", "Tarrant"], property_types=["single_family"],
    min_value=100000, max_value=400000, min_equity_pct=35, min_ownership_years=10,
    occupancy_preferences=["vacant", "non_owner_occupied"], owner_geography="absentee",
    preferred_signals=["VACANT", "TAX_DELINQUENT", "ABSENTEE_OWNER"],
    excluded_signals=["PROBATE"],
    min_opportunity_score=60, min_contact_confidence=60, handoff_intent_threshold=70,
    target_fee=10000, daily_budget_cents=500, monthly_budget_cents=5000,
    max_cost_per_property_cents=100,
    outreach_policy={"auto_outreach": True, "channels": ["sms"], "campaign": "platform_cadence",
                     "cold_outreach_compliance_confirmed": False},
    nurture_policy={"allow_nurture": True, "default_days": 60},
)
PROBATE = dict(
    name="Tarrant Probate — $0.20/day (TEST)",
    description="SANDBOX strategy with a deliberately tiny budget, to show BUDGET BLOCKED.",
    states=["TX"], counties=["Tarrant"], property_types=["single_family"],
    required_signals=["PROBATE"], preferred_signals=["VACANT"],
    min_opportunity_score=50, min_contact_confidence=50, handoff_intent_threshold=70,
    daily_budget_cents=20, monthly_budget_cents=200,
    outreach_policy={"auto_outreach": False},
)


def enable_sandbox(db, org_id: str) -> None:
    for key in PV.SANDBOX_KEYS:
        PV.config(db, org_id, key).enabled = True
    db.flush()


def create_strategy(db, org_id: str, user, spec: Dict[str, Any], *, activate=True) -> EvoSenseStrategy:
    clean, problems = ST.validate(spec)
    assert not problems, problems
    s = EvoSenseStrategy(organization_id=org_id, created_by_id=getattr(user, "id", None), is_test=True)
    ST.apply(s, clean)
    db.add(s)
    db.flush()
    if activate:
        ST.transition(s, "activate")
    C.log_event(db, org_id, "strategy.created", strategy_id=s.id, user=user,
                actor_type=C.ACTOR_USER, is_test=True, summary="Strategy created: %s" % s.name)
    return s


def prop_at(db, org_id, street) -> EvoSenseProperty:
    return (db.query(EvoSenseProperty)
            .filter(EvoSenseProperty.organization_id == org_id,
                    EvoSenseProperty.street_address == street).first())


def engagement_for(db, prop) -> EvoSenseEngagement:
    return (db.query(EvoSenseEngagement)
            .filter(EvoSenseEngagement.organization_id == prop.organization_id,
                    EvoSenseEngagement.property_id == prop.id)
            .order_by(EvoSenseEngagement.created_at.desc()).first())


def seed_review(db, org_id: str, user, *, replies: bool = True) -> Dict[str, Any]:
    """Build the whole review scenario. Idempotent enough to re-run: hunts are
    idempotent, strategies are reused by name, replies are only sent once."""
    from app.models.models import SuppressionSource
    from app.services import compliance_service
    enable_sandbox(db, org_id)
    compliance_service.add_suppression_entry(
        db, org_id, SUPPRESSED_PHONE, "SANDBOX: pre-existing opt-out for the review",
        source=SuppressionSource.MANUAL)
    out = {}
    for spec in (DFW, PROBATE):
        s = (db.query(EvoSenseStrategy)
             .filter(EvoSenseStrategy.organization_id == org_id,
                     EvoSenseStrategy.name == spec["name"]).first())
        if s is None:
            s = create_strategy(db, org_id, user, spec)
        db.commit()
        out[spec["name"]] = s
    dfw, prob = out[DFW["name"]], out[PROBATE["name"]]
    # Phase 7.1: nobody presses Run hunt. The scheduler pass — the same
    # function the platform loop runs every 15 minutes — finds both
    # strategies due (new strategies are due immediately) and hunts them.
    from app.services.evosense import scheduler as SCH
    from app.models.evosense_models import EvoSenseRun
    report = SCH.run_due(db, org_id=org_id)
    runs = (db.query(EvoSenseRun).filter(EvoSenseRun.organization_id == org_id,
                                         EvoSenseRun.trigger == "schedule")
            .order_by(EvoSenseRun.started_at.asc()).all())
    if replies:
        for street, text in (("1418 Cedar Springs Rd", FLAGSHIP_REPLY),
                             ("4915 Live Oak St", WRONG_PERSON_REPLY),
                             ("7302 Ferguson Rd", NOT_NOW_REPLY)):
            p = prop_at(db, org_id, street)
            eng = engagement_for(db, p) if p else None
            if eng is None or eng.status != "active":
                continue
            deliver_sms(db, org_id, eng, text, sid="SBX-seed-%s" % p.id[:12])
    return {"strategies": {"dfw": dfw.id, "probate": prob.id}, "scheduler": report,
            "runs": [C.jload(r.counts, {}) for r in runs]}


def deliver_sms(db, org_id, engagement, text, *, sid):
    """A SANDBOX seller reply through the platform's REAL inbound SMS
    processing (the Twilio webhook minus the signature and number lookup)."""
    from app.models.models import Lead
    from app.routers.sms_router import process_inbound_sms
    lead = db.query(Lead).filter(Lead.id == engagement.lead_id).first()
    return process_inbound_sms(db, org_id=org_id, advisor=None, From="+" + lead.phone.lstrip("+"),
                               Body=text, MessageSid=sid)
