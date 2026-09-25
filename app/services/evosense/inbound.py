"""Real inbound SMS → EvoSense (Phase 7.1).

The platform's inbound webhook (`sms_router.inbound_webhook` →
`process_inbound_sms`) stays the ONE inbound path. It authenticates the
message (signature + the receiving number's owner), resolves the
ORGANIZATION from the number the message arrived on, finds the Lead within
that organization, applies the platform's hard-stop / DNC / suppression rules,
and persists the `Reply`. Only then — after that commit — does it hand the
persisted reply to `route_reply` below.

HOW EVOSENSE KNOWS A CONVERSATION IS ITS OWN. Not by phone number alone. The
chain is: receiving number → organization (webhook) → sender phone → Lead in
that organization (webhook) → the EvoSense ENGAGEMENT whose `lead_id` is that
Lead (the Lead EvoSense created when it started working the owner). Only
engagements in that one organization are ever considered.

    no engagement for this sender in this org        → not EvoSense's; untouched
    exactly one, on the same Lead the platform chose  → routed: saved, then read
    anything else (two properties, a different Lead
      with the same number also in an EvoSense
      conversation)                                   → ROUTING REVIEW, never a guess

`owns_lead` is asked BEFORE the platform's own AI pipeline runs, so an
EvoSense seller never receives an automated platform AI reply: EvoSense owns
that conversation, and EvoSense sends no AI replies.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.models.evosense_models import EvoSenseEngagement, EvoSenseEvent, EvoSenseProperty
from app.models.models import Lead
from app.services.evosense import common as C

# Conversations EvoSense still owns. `promoted` belongs to the Wholesale deal.
OWNED = ("pending", "active", "responded", "handed_off", "nurture", "stopped", "blocked")


def _phone_forms(phone: str) -> List[str]:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) == 10:
        digits = "1" + digits
    forms = {digits, "+" + digits}
    if len(digits) == 11:
        forms.add(digits[1:])
    return [f for f in forms if f and f != "+"]


def owns_lead(db, org_id: str, lead) -> bool:
    if lead is None or not org_id:
        return False
    return (db.query(EvoSenseEngagement.id)
            .filter(EvoSenseEngagement.organization_id == org_id,
                    EvoSenseEngagement.lead_id == lead.id,
                    EvoSenseEngagement.lead_id.isnot(None),
                    EvoSenseEngagement.status.in_(OWNED)).first() is not None)


def candidates(db, org_id: str, phone: str) -> List[EvoSenseEngagement]:
    lead_ids = [lid for (lid,) in db.query(Lead.id)
                .filter(Lead.organization_id == org_id, Lead.phone.in_(_phone_forms(phone))).all()]
    if not lead_ids:
        return []
    return (db.query(EvoSenseEngagement)
            .filter(EvoSenseEngagement.organization_id == org_id,
                    EvoSenseEngagement.lead_id.in_(lead_ids),
                    EvoSenseEngagement.status.in_(OWNED)).all())


def route_reply(db, org_id: str, lead, reply) -> Dict[str, Any]:
    """Hand a PERSISTED platform reply to EvoSense. Idempotent per reply."""
    from app.services.evosense import conversation as CV
    if lead is None or reply is None or not org_id:
        return {"route": "not_evosense"}
    cands = candidates(db, org_id, lead.phone)
    if not cands:
        return {"route": "not_evosense"}
    if len(cands) == 1 and cands[0].lead_id == lead.id:
        eng = cands[0]
        msg, created = CV.record_inbound(db, org_id, eng, reply.body, delivery="received", reply=reply)
        if created:
            C.log_event(db, org_id, "inbound.routed", property_id=eng.property_id,
                        is_test=eng.is_test, actor_type=C.ACTOR_AUTOMATION,
                        summary="Inbound SMS matched to this EvoSense conversation",
                        details={"reply": reply.id, "engagement": eng.id,
                                 "match": "organization + sender Lead + engagement"})
        db.commit()                              # saved before any reading happens
        if not created and msg.outcome:
            return {"route": "duplicate", "message_id": msg.id, "outcome": msg.outcome}
        result = CV.evaluate(db, org_id, msg)
        return {"route": "routed", **result}
    return open_routing_review(db, org_id, lead, reply, cands)


def open_routing_review(db, org_id, lead, reply, cands) -> Dict[str, Any]:
    existing = [e for e in (db.query(EvoSenseEvent)
                            .filter(EvoSenseEvent.organization_id == org_id,
                                    EvoSenseEvent.action == "inbound.routing_review").all())
                if (C.jload(e.details, {}) or {}).get("reply") == reply.id]
    if existing:
        return {"route": "routing_review", "review_id": existing[0].id, "duplicate": True}
    props = {p.id: p for p in db.query(EvoSenseProperty).filter(
        EvoSenseProperty.organization_id == org_id,
        EvoSenseProperty.id.in_([c.property_id for c in cands] or ["-"])).all()}
    ev = C.log_event(db, org_id, "inbound.routing_review", actor_type=C.ACTOR_AUTOMATION,
                     is_test=all(c.is_test for c in cands),
                     summary="ROUTING REVIEW REQUIRED — an inbound SMS could belong to %s EvoSense "
                             "conversations; it was saved and attached to none." % len(cands),
                     details={"reply": reply.id, "lead": lead.id, "body": (reply.body or "")[:500],
                              "candidates": [{"engagement": c.id, "property": c.property_id,
                                              "address": getattr(props.get(c.property_id),
                                                                 "street_address", None),
                                              "same_lead": c.lead_id == lead.id} for c in cands],
                              "resolved": False})
    db.commit()
    return {"route": "routing_review", "review_id": ev.id}


def open_routing_reviews(db, org_id: str) -> List[Dict[str, Any]]:
    out = []
    for e in (db.query(EvoSenseEvent)
              .filter(EvoSenseEvent.organization_id == org_id,
                      EvoSenseEvent.action == "inbound.routing_review")
              .order_by(EvoSenseEvent.created_at.desc()).limit(50).all()):
        d = C.jload(e.details, {}) or {}
        if not d.get("resolved"):
            out.append({"id": e.id, "at": e.created_at.isoformat() + "Z" if e.created_at else None,
                        "body": d.get("body"), "candidates": d.get("candidates") or [],
                        "is_test": bool(e.is_test)})
    return out


def resolve_routing_review(db, org_id: str, review_id: str, engagement_id, user) -> Dict[str, Any]:
    """A person says which conversation the message belongs to (or none)."""
    from app.models.models import Reply
    from app.services.evosense import conversation as CV
    ev = (db.query(EvoSenseEvent).filter(EvoSenseEvent.id == review_id,
                                         EvoSenseEvent.organization_id == org_id,
                                         EvoSenseEvent.action == "inbound.routing_review").first())
    if ev is None:
        raise LookupError("Not found")
    d = C.jload(ev.details, {}) or {}
    if d.get("resolved"):
        raise ValueError("Already resolved")
    allowed = {c["engagement"] for c in d.get("candidates") or []}
    result: Dict[str, Any] = {"route": "dismissed"}
    if engagement_id:
        if engagement_id not in allowed:
            raise ValueError("Choose one of the conversations this message could belong to.")
        eng = db.query(EvoSenseEngagement).filter(EvoSenseEngagement.id == engagement_id,
                                                  EvoSenseEngagement.organization_id == org_id).first()
        reply = (db.query(Reply).join(Lead, Lead.id == Reply.lead_id)
                 .filter(Reply.id == d.get("reply"), Lead.organization_id == org_id).first())
        if eng is None or reply is None:
            raise LookupError("Not found")
        msg, _ = CV.record_inbound(db, org_id, eng, reply.body, delivery="received", reply=reply,
                                   user=user)
        db.commit()
        result = {"route": "routed", **CV.evaluate(db, org_id, msg, user=user)}
    d["resolved"] = True
    d["resolved_by"] = getattr(user, "id", None)
    d["resolved_to"] = engagement_id
    ev.details = C.jdump(d)
    C.log_event(db, org_id, "inbound.routing_resolved", user=user, actor_type=C.ACTOR_USER,
                is_test=ev.is_test, property_id=result.get("property_id"),
                summary="Routing review %s" % ("resolved to a conversation" if engagement_id
                                               else "dismissed: belongs to no EvoSense conversation"))
    db.commit()
    return result
