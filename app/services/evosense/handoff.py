"""NEEDS YOU — where automation stops and a person decides.

One open handoff per property. New reasons are merged into it rather than
stacking duplicates. EvoSense pauses its own outreach for a handed-off owner:
the next message is a person's.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from app.models.evosense_models import EvoSenseHandoff
from app.services.evosense import common as C

REASONS = {
    "INTENT_THRESHOLD": "Seller intent reached the strategy's handoff threshold",
    "PRICE_STATED": "Seller stated a price",
    "WANTS_OFFER": "Seller asked for an offer",
    "APPOINTMENT": "Seller asked to meet or show the property",
    "CALLBACK": "Seller asked for a call",
    "ESTATE": "Estate / inherited property — decision makers to confirm",
    "TENANT_ISSUE": "Tenant situation needs judgment",
    "UNCLEAR": "EvoSense could not read the reply with confidence",
    "CONFLICT": "Sources conflict on a material fact",
    "LEGAL_SENSITIVE": "Sensitive or legal topic raised",
    "PROMOTION_READY": "Ready to promote into Wholesale",
    "AI_REVIEW_PENDING": "AI review pending — the seller's reply is saved but could not be read automatically",
    "ROUTING_REVIEW": "An inbound SMS may belong to this property — confirm before it is used",
}


def open_handoff(db, prop, reasons: List[str], *, engagement=None, priority: int = 50,
                 next_action: Optional[str] = None, detail: Optional[Dict] = None) -> EvoSenseHandoff:
    h = (db.query(EvoSenseHandoff)
         .filter(EvoSenseHandoff.organization_id == prop.organization_id,
                 EvoSenseHandoff.property_id == prop.id,
                 EvoSenseHandoff.status.in_(("open", "acknowledged"))).first())
    new = [{"code": r, "label": REASONS.get(r, r.replace("_", " ").title())} for r in reasons]
    if h is None:
        h = EvoSenseHandoff(organization_id=prop.organization_id, property_id=prop.id,
                            engagement_id=getattr(engagement, "id", None), reasons=C.jdump(new),
                            priority=priority, next_action=next_action, is_test=bool(prop.is_test))
        db.add(h)
        C.log_event(db, prop.organization_id, "handoff.opened", property_id=prop.id,
                    is_test=prop.is_test, summary="NEEDS YOU: " + "; ".join(n["label"] for n in new),
                    details={"reasons": reasons})
    else:
        have = C.jload(h.reasons, []) or []
        codes = {r["code"] for r in have}
        h.reasons = C.jdump(have + [n for n in new if n["code"] not in codes])
        h.priority = max(h.priority or 0, priority)
        h.next_action = next_action or h.next_action
    if engagement is not None:
        engagement.status = "handed_off"
    db.flush()
    return h


def resolve(db, handoff: EvoSenseHandoff, status: str, user, note: Optional[str] = None):
    if status not in ("acknowledged", "dismissed", "promoted"):
        raise ValueError("status must be acknowledged, dismissed or promoted")
    handoff.status = status
    handoff.resolved_by_id = getattr(user, "id", None)
    handoff.resolved_at = C.now()
    handoff.resolution_note = (note or "")[:250] or None
    C.log_event(db, handoff.organization_id, "handoff." + status, property_id=handoff.property_id,
                user=user, actor_type=C.ACTOR_USER, is_test=handoff.is_test,
                summary="Handoff %s%s" % (status, (": " + note) if note else ""))
