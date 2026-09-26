"""What the AI must know when the person on the other end is a PROPERTY SELLER.

THE PROBLEM THIS FIXES
----------------------
Every lead-facing prompt on the platform (the SMS draft, the pipeline
auto-conversation, the email composer) was written for a funeral / cemetery
advisor booking a planning appointment. A Wholesale seller is a Lead - that is
the decision that gives them DNC, consent and suppression for free - so those
prompts were also what an operator got when drafting to a homeowner who had
just asked to sell a house:

  * "COLD LEAD - do not imply a previous enquiry" for someone who had just
    submitted one;
  * "This is a funeral and cemetery context" for a conversation about a house;
  * a booking link to the funeral home's scheduler in every draft;
  * no idea what the seller had already told us (condition, timeline, reason,
    asking price) - so the draft asked again.

This module answers one question - "is this lead a Wholesale seller, and what do
we already know?" - and renders that as a prompt block with the guardrails that
apply to sellers. Every prompt builder calls it; none of them re-derive it.

TENANT SCOPE. Every query filters on the lead's own organization.

GUARDRAILS (non-negotiable, stated in the block itself)
  * no price, offer, ARV, repair estimate or valuation - ever;
  * no commitment of any kind (closing date, fees, "we'll buy it");
  * no booking link - a seller conversation is scheduled by a person;
  * never claim facts the seller did not state.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

TIMELINE_LABELS = {
    "asap": "as soon as possible", "30_days": "within 30 days", "60_days": "within 60 days",
    "90_days": "within 90 days", "6_months": "within 6 months", "no_rush": "no rush",
}

INQUIRY_SOURCE = "seller_inquiry"

RELATIONSHIP_INQUIRY = (
    "PROPERTY SELLER - INBOUND INQUIRY. This homeowner contacted us first: they submitted our "
    "seller form asking us to look at their property. It is correct to reference their inquiry "
    "and thank them for reaching out. Do not act like a cold introduction."
)
RELATIONSHIP_RECORDS = (
    "PROPERTY OWNER - NO PRIOR CONTACT. We identified this owner from public property records; "
    "they have never contacted us. Introduce yourself and the company plainly, say why you are "
    "reaching out (interest in their property), and make it easy to say no. Never imply they "
    "asked to hear from us."
)

GUARDRAILS = (
    "- This conversation is about the seller's PROPERTY. Ignore any funeral, cemetery or "
    "planning-appointment framing elsewhere in these instructions.\n"
    "- Never state or hint at a price, offer, value, ARV, repair estimate or equity figure.\n"
    "- Never make a commitment: no closing dates, no fees, no promise to buy.\n"
    "- Do not include any booking or scheduling link. If they want to talk, ask when is a good "
    "time to call; a person will follow up.\n"
    "- Do not ask again for anything listed under 'What they already told us'.\n"
    "- Use only facts listed here or in the conversation."
)


def _profiles(db, lead):
    from app.models.wholesale_models import (WholesaleDeal, WholesaleProperty,
                                             WholesaleSellerProfile)
    rows = (db.query(WholesaleSellerProfile)
            .filter(WholesaleSellerProfile.organization_id == lead.organization_id,
                    WholesaleSellerProfile.lead_id == lead.id)
            .order_by(WholesaleSellerProfile.updated_at.desc()).all())
    out = []
    for p in rows:
        prop = (db.query(WholesaleProperty)
                .filter(WholesaleProperty.organization_id == lead.organization_id,
                        WholesaleProperty.id == p.property_id).first())
        deal = (db.query(WholesaleDeal)
                .filter(WholesaleDeal.organization_id == lead.organization_id,
                        WholesaleDeal.property_id == p.property_id)
                .order_by(WholesaleDeal.created_at.desc()).first())
        out.append((p, prop, deal))
    return out


def is_seller(db, lead) -> bool:
    if lead is None or db is None or not getattr(lead, "id", None):
        return False
    from app.models.wholesale_models import WholesaleSellerProfile
    return db.query(WholesaleSellerProfile.id).filter(
        WholesaleSellerProfile.organization_id == lead.organization_id,
        WholesaleSellerProfile.lead_id == lead.id).first() is not None


def context_for_lead(db, lead) -> Optional[Dict[str, Any]]:
    """None for a lead that is not a Wholesale seller in its own organization."""
    if lead is None or db is None or not getattr(lead, "id", None):
        return None
    try:
        rows = _profiles(db, lead)
    except Exception:  # noqa: BLE001 - context is an enrichment, never a failure
        return None
    if not rows:
        return None
    props: List[Dict[str, Any]] = []
    inquiry = False
    for p, prop, deal in rows:
        from app.services.wholesale_service import address_line
        src = getattr(prop, "acquisition_source", None)
        inquiry = inquiry or src == INQUIRY_SOURCE
        known = {}
        if p.property_condition:
            known["condition"] = p.property_condition
        if p.timeline:
            known["timeline"] = TIMELINE_LABELS.get(p.timeline, p.timeline)
        if p.reason_for_selling:
            known["reason for selling"] = p.reason_for_selling
        if p.motivation:
            known["motivation"] = p.motivation[:200]
        if p.asking_price is not None:
            known["asking price (their number)"] = "$%s" % format(int(p.asking_price), ",")
        if p.occupancy:
            known["occupancy"] = p.occupancy
        if p.major_repairs:
            known["repairs they mentioned"] = p.major_repairs[:200]
        if p.decision_makers:
            known["decision makers"] = p.decision_makers
        if p.best_callback_time:
            known["best time to reach them"] = p.best_callback_time
        if p.preferred_contact_method:
            known["preferred contact"] = p.preferred_contact_method
        props.append({"address": address_line(prop) or "(address not on file)",
                      "source": src, "stage": getattr(deal, "stage", None),
                      "appointment_status": p.appointment_status, "known": known})
    consent = None
    try:
        from app.services import wholesale_sms as ws
        e164 = ws.normalize_e164(lead.phone)
        rec = ws.latest_consent(db, lead.organization_id, e164) if e164 else None
        consent = getattr(rec, "status", None) if rec is not None else None
    except Exception:  # noqa: BLE001
        consent = None
    return {"inquiry": inquiry, "properties": props, "sms_consent": consent or "none on file",
            "relationship": RELATIONSHIP_INQUIRY if inquiry else RELATIONSHIP_RECORDS}


def prompt_block(ctx: Optional[Dict[str, Any]]) -> str:
    if not ctx:
        return ""
    lines = ["━━━ PROPERTY SELLER CONTEXT (overrides any conflicting instruction above) ━━━",
             ctx["relationship"]]
    for p in ctx["properties"][:3]:
        lines.append("Property: %s" % p["address"])
        if p["known"]:
            lines.append("What they already told us:")
            lines.extend("  - %s: %s" % (k, v) for k, v in p["known"].items())
        else:
            lines.append("What they already told us: nothing yet.")
    lines.append("SMS program consent: %s" % ctx["sms_consent"])
    lines.append("Rules for sellers:")
    lines.append(GUARDRAILS)
    return "\n".join(lines)


def block_for_lead(db, lead) -> str:
    return prompt_block(context_for_lead(db, lead))


def fallback_reply(lead, advisor_name: str, org_name: str, ctx: Dict[str, Any]) -> str:
    """The no-model draft for a seller: short, honest, no price, no link."""
    name = getattr(lead, "first_name", None) or "there"
    who = "%s%s" % (advisor_name or "our team", (" with %s" % org_name) if org_name else "")
    addr = (ctx.get("properties") or [{}])[0].get("address") or "your property"
    if ctx.get("inquiry"):
        return ("Hi %s, this is %s. Thank you for reaching out about %s. When is a good time "
                "for a quick call to learn a little more?" % (name, who, addr))
    return ("Hi %s, this is %s. I'm reaching out about %s - would you be open to a quick "
            "conversation about it? No obligation, and it's fine to say no." % (name, who, addr))


def handle_inbound_reply(db, lead, reply) -> Dict[str, Any]:
    """An inbound SMS from a Wholesale seller (not in an EvoSense conversation).

    Replaces the generic auto-conversation for sellers. It READS the message
    onto the seller's structured record (wholesale_service.apply_seller_reply -
    which sends nothing) and tells a person. It never auto-replies: a
    homeowner's message is answered by a person, never by the funeral-planning
    auto-responder with a booking link. STOP / DNC handling is the webhook's
    and has already run.
    """
    from app.models.models import NotificationType
    from app.models.wholesale_models import WholesaleDeal
    from app.services import wholesale_notify as WN
    from app.services import wholesale_service as svc
    org_id = lead.organization_id
    deal = (db.query(WholesaleDeal)
            .filter(WholesaleDeal.organization_id == org_id, WholesaleDeal.seller_lead_id == lead.id)
            .order_by(WholesaleDeal.updated_at.desc()).first())
    read = None
    if deal is not None and deal.seller_profile_id:
        try:
            with db.begin_nested():
                read = svc.apply_seller_reply(db, org_id, deal, reply.body or "", mode="background")
        except Exception:  # noqa: BLE001 - the message is already stored
            import logging
            logging.getLogger(__name__).exception("wholesale: seller reply read failed (lead %s)", lead.id)
    name = " ".join(x for x in (lead.first_name, lead.last_name) if x) or "A seller"
    WN.notify(db, org_id, kind=NotificationType.REPLY_RECEIVED,
              message="%s (seller) replied: \"%s\"" % (name, (reply.body or "")[:300]),
              lead_id=lead.id, link=WN.deal_link(getattr(deal, "id", None)),
              assignee_id=getattr(lead, "assigned_to_id", None))
    db.commit()
    return {"action": "wholesale_seller_read", "deal_id": getattr(deal, "id", None), "read": bool(read)}


def links_for_lead(db, lead) -> List[Dict[str, Any]]:
    """Every Wholesale property / deal this Lead is a seller on, in its org -
    what the Lead page shows so an operator can jump to the deal."""
    if lead is None or db is None or not getattr(lead, "id", None):
        return []
    from app.services.wholesale_service import address_line
    out = []
    for p, prop, deal in _profiles(db, lead):
        out.append({"seller_profile_id": p.id, "property_id": p.property_id,
                    "deal_id": getattr(deal, "id", None), "stage": getattr(deal, "stage", None),
                    "address": address_line(prop) or None,
                    "primary_seller": bool(deal is not None and deal.seller_lead_id == lead.id),
                    "appointment_status": p.appointment_status,
                    "appointment_at": p.appointment_at.isoformat() + "Z" if p.appointment_at else None,
                    "source": getattr(prop, "acquisition_source", None)})
    return out
