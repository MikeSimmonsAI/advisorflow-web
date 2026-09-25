"""Re-score one property and put it in exactly one Discovery Inbox bucket.

Everything here is deterministic. The bucket is DERIVED from the records
(scores, decisions, engagements, handoffs) every time, never set by hand, so
it can never drift from the evidence that justifies it.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseEngagement,
                                        EvoSenseEnrichmentDecision, EvoSenseHandoff,
                                        EvoSenseProperty, EvoSenseSignal, EvoSenseStrategy)
from app.services.evosense import common as C
from app.services.evosense import contacts as CT
from app.services.evosense import scoring as SC
from app.services.evosense import signals as SIG


def strategy_for(db, prop) -> Optional[EvoSenseStrategy]:
    sid = prop.best_strategy_id or prop.first_strategy_id
    if not sid:
        return None
    return (db.query(EvoSenseStrategy)
            .filter(EvoSenseStrategy.id == sid,
                    EvoSenseStrategy.organization_id == prop.organization_id).first())


def stacked_signals(db, prop):
    rows = (db.query(EvoSenseSignal)
            .filter(EvoSenseSignal.organization_id == prop.organization_id,
                    EvoSenseSignal.property_id == prop.id).all())
    return SIG.stack(rows)


def rescore(db, prop: EvoSenseProperty, strategy=None) -> Dict[str, Any]:
    """Derive signals, compute Property Opportunity + Data Confidence + best
    Contact Confidence, store the history, cache the current values."""
    db.flush()          # the platform's sessions do not autoflush
    strategy = strategy or strategy_for(db, prop)
    owner = CT.primary_owner(db, prop)
    SIG.derive(db, prop, owner)
    db.flush()
    stacked = stacked_signals(db, prop)
    po = SC.property_opportunity(prop, stacked, strategy)
    SC.record(db, prop, "property_opportunity", po, strategy_id=getattr(strategy, "id", None))
    dc = SC.data_confidence(prop, stacked, owner_known=owner is not None)
    SC.record(db, prop, "data_confidence", dc)
    prop.opportunity_score = po["value"]
    prop.data_confidence = dc["label"]
    prop.signal_count = len([s for s in stacked if s["freshness"] != SIG.STALE])
    cp, cc = CT.best_contact(db, prop)
    prop.contact_confidence = cc["value"] if cc else None
    prop.last_evaluated_at = C.now()
    refresh_status(db, prop, strategy)
    return {"opportunity": po, "data_confidence": dc, "contact": cc, "stacked": stacked}


def _latest(db, model, prop, *order):
    return (db.query(model)
            .filter(model.organization_id == prop.organization_id,
                    model.property_id == prop.id)
            .order_by(*order).first())


def status_for(db, prop, strategy) -> Tuple[str, str, Optional[str], Optional[str]]:
    """(bucket, next action, detail, blocked reason) — first rule that applies wins."""
    if prop.promoted_deal_id:
        return C.S_PROMOTED, "Work it in Wholesale", "Promoted into the Wholesale deal pipeline.", None
    handoff = (db.query(EvoSenseHandoff)
               .filter(EvoSenseHandoff.organization_id == prop.organization_id,
                       EvoSenseHandoff.property_id == prop.id,
                       EvoSenseHandoff.status.in_(("open", "acknowledged"))).first())
    if handoff is not None:
        return C.S_NEEDS_YOU, handoff.next_action or "Review and decide", \
            "; ".join(r.get("label", "") for r in (C.jload(handoff.reasons, []) or [])), None
    if prop.identity_status == "review":
        return C.S_NEEDS_REVIEW, "Confirm which property this is", \
            "Two sources may describe the same house. Nothing is merged until you decide.", None

    eng = _latest(db, EvoSenseEngagement, prop, EvoSenseEngagement.updated_at.desc())
    if eng is not None:
        if eng.status == "nurture":
            when = eng.nurture_until.strftime("%b %d, %Y") if eng.nurture_until else "later"
            return C.S_NURTURE, "Follow up %s" % when, eng.nurture_reason, None
        if eng.status == "responded":
            return C.S_RESPONDED, "Read the reply", None, None
        if eng.status == "active":
            return C.S_OUTREACH, "Waiting for a reply", \
                "Delivery: %s" % ("SANDBOX — simulated, nothing sent" if eng.delivery_mode
                                  == "sandbox_simulated" else "platform cadence"), None
        if eng.status == "blocked" and eng.blocked_reason in ("OWNER ALREADY IN CONVERSATION",
                                                              "OWNER CONTACTED RECENTLY"):
            return C.S_CONTACT_FOUND, "No action — this owner is already being worked", \
                (C.jload(eng.eligibility, {}) or {}).get("summary"), eng.blocked_reason
        if eng.status == "stopped":
            if eng.last_outcome in (C.O_DNC,) or eng.blocked_reason == "opted_out":
                return C.S_SUPPRESSED, "Nothing — do not contact", \
                    "The owner asked not to be contacted. Honoured everywhere.", "DO NOT CONTACT"
            if eng.last_outcome == C.O_WRONG_PERSON:
                return C.S_CLOSED_OUT, "Only a person can find the right owner contact", \
                    "The number reached the wrong person. It is marked wrong-party and no strategy will use it again.", \
                    "WRONG PARTY"
            return C.S_CLOSED_OUT, "Nothing", (eng.last_outcome or "stopped").replace("_", " ").lower(), None

    score = prop.opportunity_score
    threshold = getattr(strategy, "min_opportunity_score", 60) if strategy else 60
    if score is None:
        return C.S_NEW, "Waiting for more evidence", "Insufficient evidence to score.", None
    if score < threshold:
        return C.S_LOW, "Nothing", "Scored %s; this strategy acts at %s." % (score, threshold), None

    cps = (db.query(EvoSenseContactPoint)
           .filter(EvoSenseContactPoint.organization_id == prop.organization_id,
                   EvoSenseContactPoint.owner_id.in_([o.id for o in CT.current_owners(db, prop)] or ["-"]))
           .all())
    active = [c for c in cps if c.status == "active"]
    if cps and not active:
        worst = sorted(cps, key=lambda c: c.status != "suppressed" and c.status != "opted_out")[0]
        if worst.status in ("suppressed", "opted_out"):
            return C.S_SUPPRESSED, "Nothing — do not contact", \
                worst.status_reason or "Contact is on the suppression list.", "SUPPRESSED"

    dec = _latest(db, EvoSenseEnrichmentDecision, prop, EvoSenseEnrichmentDecision.created_at.desc())
    if active:
        min_cc = getattr(strategy, "min_contact_confidence", 60) if strategy else 60
        best = prop.contact_confidence or 0
        if best >= min_cc:
            return C.S_READY, "Start outreach", "Contact confidence %s (strategy needs %s)." % (best, min_cc), None
        return C.S_CONTACT_FOUND, "Verify the contact", \
            "Contact confidence %s is below the strategy's %s." % (best, min_cc), None
    if dec is not None:
        reasons = C.jload(dec.reasons, []) or []
        why = reasons[0] if reasons else None
        if dec.decision == C.D_BUDGET:
            return C.S_BUDGET_BLOCKED, "Raise the budget or wait for tomorrow", why, "BUDGET"
        if dec.decision == C.D_APPROVAL:
            return C.S_NEEDS_ENRICHMENT, "Approve a %s lookup" % C.money(dec.estimated_cost_cents), why, None
        if dec.decision == C.D_SUPPRESSED:
            return C.S_SUPPRESSED, "Nothing — do not contact", why, "SUPPRESSED"
        if dec.outcome in ("no_match", "provider_failed") or dec.decision == C.D_RETRY:
            return C.S_WAITING_DATA, "Add a contact manually, or wait for a provider", why, None
    return C.S_HIGH, "Look up the owner's contact", "Passes the strategy; no contact yet.", None


def refresh_status(db, prop, strategy=None) -> str:
    db.flush()
    strategy = strategy or strategy_for(db, prop)
    bucket, action, detail, blocked = status_for(db, prop, strategy)
    prop.status = bucket
    prop.next_action = action
    prop.next_action_detail = (detail or None) and str(detail)[:250]
    prop.blocked_reason = blocked
    return bucket
