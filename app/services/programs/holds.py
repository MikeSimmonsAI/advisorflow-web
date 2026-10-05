"""HOLD - park source records a person decided not to work yet.

A held record is excluded from every send path (identity.send_refusal checks
it first), from promotion to a live lead, and from the email runner. It is
never deleted and never auto-fixed: its original values, flags and review
reasons stay exactly as they were. Only a person releases it.

hold_open_reviews() holds, in one audited step, every record that is still
in a review state:
    LOCATION REVIEW   no location could be resolved
    DUPLICATE REVIEW  phone/email shared with a different name
    DATA REVIEW       an operational note in a name field, or a reply said
                      "wrong person"
    LOCATION CONFLICT rows auto-linked as one person name two different
                      locations, so no single facility can speak for them
"""
import json
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.program_models import ProgramSourceRecord


def review_reasons(rec: ProgramSourceRecord, conflict: bool = False) -> List[str]:
    out = []
    if rec.location_status != "mapped" or not rec.location_id:
        out.append("LOCATION REVIEW")
    if rec.duplicate_review_reason and not rec.duplicate_review_cleared_at:
        out.append("DUPLICATE REVIEW")
    if rec.needs_data_review:
        out.append("DATA REVIEW")
    if conflict:
        out.append("LOCATION CONFLICT")
    return out


def _conflicted_masters(recs: List[ProgramSourceRecord]) -> set:
    locs = defaultdict(set)
    for r in recs:
        if r.location_status == "mapped" and r.location_id:
            locs[r.contact_master_key or r.source_lead_id].add(r.location_id)
    return {k for k, v in locs.items() if len(v) > 1}


def hold_open_reviews(db: Session, org_id: str, actor_id: Optional[str] = None,
                      *, apply: bool = True, now: Optional[datetime] = None) -> Dict:
    now = now or datetime.utcnow()
    recs = db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == org_id).all()
    conflict = _conflicted_masters(recs)
    held, already, by_reason = [], 0, defaultdict(int)
    for r in recs:
        reasons = review_reasons(r, (r.contact_master_key or r.source_lead_id) in conflict)
        if not reasons:
            continue
        for x in reasons:
            by_reason[x] += 1
        if r.on_hold:
            already += 1
            continue
        held.append(r.source_lead_id)
        if apply:
            r.on_hold, r.held_at, r.held_by = True, now, actor_id
            r.hold_reason = "; ".join(reasons)
    if apply and held:
        if actor_id:
            from app.routers.audit_log_router import log_action
            log_action(db, org_id, actor_id, action="program.records_held",
                       target_type="organization", target_id=org_id,
                       details={"held": len(held), "source_lead_ids": held[:100],
                                "by_reason": dict(by_reason)})
        db.flush()
    return {"held": len(held), "already_held": already, "by_reason": dict(by_reason),
            "source_lead_ids": held, "applied": apply}


def set_hold(db: Session, rec: ProgramSourceRecord, on_hold: bool, actor_id: Optional[str],
             reason: Optional[str] = None, now: Optional[datetime] = None) -> ProgramSourceRecord:
    now = now or datetime.utcnow()
    if on_hold:
        rec.on_hold, rec.held_at, rec.held_by = True, now, actor_id
        rec.hold_reason = (reason or rec.hold_reason or "held by a person").strip()[:500]
    else:
        rec.on_hold = False
        rec.hold_reason = "released %s" % now.strftime("%Y-%m-%d %H:%M UTC")
    return rec
