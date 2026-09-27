"""THE MORNING COMMAND CENTER, NEEDS YOU, and the one seller lifecycle.

This module OWNS NO STATE. Everything here is read from the records that
already hold it - EvoSense properties/engagements/handoffs, Wholesale deals,
seller profiles and facts, approvals, the cost ledger, lead capacity - and
presented as the few questions a wholesaler has at 8am:

    What needs ME?            NEEDS YOU - only work a person must do, each with
                              WHY YOU ARE SEEING THIS and the evidence
    What got better?          new qualified opportunities, seller replies,
                              appointments
    What is stuck?            awaiting contact data, provider problems
    What comes back today?    nurture due
    What did it cost?         spend
    What moved?               pipeline movement

Every count carries a link to the list behind it. Nothing here is a vanity
number: if a count cannot lead somewhere actionable it is not shown.

SELLER LIFECYCLE is a VIEW, not a fourth status system. It reads EvoSense's
property status + engagement, the Wholesale deal stage and the seller's
qualification outcome, and names the one place the seller is:

    DISCOVERED -> OWNER_IDENTIFIED -> CONTACTABILITY_PENDING -> CONTACTABLE
    -> OUTREACH_ELIGIBLE -> ENGAGED -> QUALIFYING -> QUALIFIED -> OPPORTUNITY
    or NURTURE / NOT_INTERESTED / DO_NOT_CONTACT / UNREACHABLE / REVIEW_REQUIRED
with the underlying states it was derived from, so nothing is hidden.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# ── lifecycle ───────────────────────────────────────────────────────────────

DISCOVERED = "DISCOVERED"
OWNER_IDENTIFIED = "OWNER_IDENTIFIED"
CONTACTABILITY_PENDING = "CONTACTABILITY_PENDING"
CONTACTABLE = "CONTACTABLE"
OUTREACH_ELIGIBLE = "OUTREACH_ELIGIBLE"
ENGAGED = "ENGAGED"
QUALIFYING = "QUALIFYING"
QUALIFIED = "QUALIFIED"
OPPORTUNITY = "OPPORTUNITY"
NURTURE = "NURTURE"
NOT_INTERESTED = "NOT_INTERESTED"
DO_NOT_CONTACT = "DO_NOT_CONTACT"
UNREACHABLE = "UNREACHABLE"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
LIFECYCLE = (DISCOVERED, OWNER_IDENTIFIED, CONTACTABILITY_PENDING, CONTACTABLE, OUTREACH_ELIGIBLE,
             ENGAGED, QUALIFYING, QUALIFIED, OPPORTUNITY, NURTURE, NOT_INTERESTED, DO_NOT_CONTACT,
             UNREACHABLE, REVIEW_REQUIRED)
LIFECYCLE_LABELS = {k: k.replace("_", " ").title() for k in LIFECYCLE}

OPPORTUNITY_STAGES = ("analysis", "offer_review", "offer_sent", "negotiating", "under_contract",
                      "disposition", "buyer_identified", "assignment_pending", "title_closing",
                      "closed")
ENGAGED_STAGES = ("seller_engaged", "qualifying")


def _life(stage: str, why: str, **sources) -> Dict[str, Any]:
    return {"stage": stage, "label": LIFECYCLE_LABELS[stage], "why": why,
            "derived_from": {k: v for k, v in sources.items() if v is not None}}


def seller_lifecycle(*, lead=None, profile=None, deal=None, evosense_prop=None,
                     engagement_status: Optional[str] = None,
                     contactability: Optional[str] = None) -> Dict[str, Any]:
    """The one place this seller is, from the states that already exist."""
    lead_status = getattr(lead, "status", None)
    q = getattr(profile, "qualification_status", None)
    stage = getattr(deal, "stage", None)
    es = getattr(evosense_prop, "status", None)
    src = dict(lead_status=lead_status, qualification=q, deal_stage=stage, evosense_status=es,
               engagement=engagement_status, contactability=contactability)
    if lead_status == "dnc" or contactability == "DO_NOT_CONTACT" or es == "suppressed":
        return _life(DO_NOT_CONTACT, "Asked not to be contacted / suppressed", **src)
    if stage == "dead":
        return _life(NOT_INTERESTED, "Deal closed out: %s" % (getattr(deal, "lost_reason", None)
                                                            or "dead"), **src)
    if stage in OPPORTUNITY_STAGES:
        return _life(OPPORTUNITY, "In deal analysis and beyond (%s)" % stage.replace("_", " "), **src)
    if q == "DISQUALIFIED":
        return _life(NOT_INTERESTED if lead_status != "dnc" else DO_NOT_CONTACT,
                     "Disqualified by what they said", **src)
    if q == "NOT_INTERESTED":
        return _life(NOT_INTERESTED, "Said no", **src)
    if q == "NURTURE" or engagement_status == "nurture" or es == "nurture":
        return _life(NURTURE, "Not now - coming back later", **src)
    if q == "HUMAN_REVIEW" or contactability == "REVIEW_REQUIRED" or es == "needs_review":
        return _life(REVIEW_REQUIRED, "A person has to decide something first", **src)
    if q == "QUALIFIED" or stage == "qualified":
        return _life(QUALIFIED, "Meets the workspace's qualification criteria", **src)
    if q == "NEEDS_MORE_INFORMATION" or stage in ENGAGED_STAGES or engagement_status in (
            "responded", "handed_off"):
        return _life(QUALIFYING, "In conversation; still learning what they want", **src)
    if engagement_status == "active" or stage == "outreach_active":
        return _life(ENGAGED, "Outreach is under way", **src)
    if engagement_status == "stopped":
        return _life(UNREACHABLE, "Outreach stopped without a seller", **src)
    if contactability in ("CONTACTABLE_SMS", "CONTACTABLE_EMAIL", "CONTACTABLE_OTHER"):
        if es == "ready_for_outreach" or stage == "ready_for_outreach":
            return _life(OUTREACH_ELIGIBLE, "Contactable and cleared for outreach", **src)
        return _life(CONTACTABLE, "May be contacted (%s)" % contactability.split("_", 1)[1].lower(),
                     **src)
    if contactability == "CONTACT_DATA_FOUND" or es == "contact_found":
        return _life(CONTACTABILITY_PENDING, "Contact data found; permission not established", **src)
    if es == "waiting_for_data":
        return _life(UNREACHABLE, "No provider could find a contact", **src)
    if contactability in ("ENRICHMENT_NEEDED",) or es in ("needs_enrichment", "budget_blocked") \
            or lead is not None:
        return _life(OWNER_IDENTIFIED, "Owner known; no usable contact yet", **src)
    return _life(DISCOVERED, "Property found; owner not established", **src)


# ── NEEDS YOU ───────────────────────────────────────────────────────────────

def _item(kind, priority, title, why, evidence, link, **ids) -> Dict[str, Any]:
    return {"kind": kind, "priority": priority, "title": title, "why": why,
            "evidence": [e for e in evidence if e], "link": link, **ids}


_SELLER_FACT_REASONS = {
    "offer_request": (92, "Seller requests an offer",
                      "The seller asked for an offer. An offer commits the company - a person decides."),
    "appointment_request": (90, "Seller wants to talk",
                            "The seller asked for a call or a walkthrough."),
    "asking_price": (85, "Seller proposed a price",
                     "The seller named a price. Negotiating is a person's job."),
    "callback_request": (80, "Seller asked for a call back",
                         "The seller asked to be called back."),
    "ownership_complication": (70, "Ownership needs confirming",
                               "Who can sell is unclear (estate, family, title). Confirm before going further."),
}
_ESCALATE_STAGES_EXCLUDED = ("dead", "closed")


def _deal_link(deal_id):
    return "/wholesale/deals/%s" % deal_id


def needs_you(db, org_id: str, *, include_test: bool = False, limit: int = 50) -> List[Dict[str, Any]]:
    """Only work a person must do. Each item says WHY and shows the evidence.
    Routine machine work (lookups within policy, scoring, nurture timers,
    blocked sends) never appears here."""
    from app.models.models import Lead
    from app.models.wholesale_models import (WholesaleApproval, WholesaleDeal, WholesaleProperty,
                                             WholesaleSellerFact, WholesaleSellerProfile)
    items: List[Dict[str, Any]] = []

    # 1. Sellers: what they asked for, and reviews only a person can do.
    deals = (db.query(WholesaleDeal)
             .filter(WholesaleDeal.organization_id == org_id,
                     WholesaleDeal.stage.notin_(_ESCALATE_STAGES_EXCLUDED)))
    if not include_test:
        deals = deals.filter(WholesaleDeal.is_test.isnot(True))
    deals = {d.seller_profile_id: d for d in deals.all() if d.seller_profile_id}
    if deals:
        profiles = (db.query(WholesaleSellerProfile)
                    .filter(WholesaleSellerProfile.organization_id == org_id,
                            WholesaleSellerProfile.id.in_(list(deals))).all())
        facts: Dict[str, List[WholesaleSellerFact]] = {}
        for f in (db.query(WholesaleSellerFact)
                  .filter(WholesaleSellerFact.organization_id == org_id,
                          WholesaleSellerFact.profile_id.in_([p.id for p in profiles]),
                          WholesaleSellerFact.superseded.is_(False),
                          WholesaleSellerFact.verified_at.is_(None),
                          WholesaleSellerFact.fact_type.in_(list(_SELLER_FACT_REASONS))).all()):
            facts.setdefault(f.profile_id, []).append(f)
        leads = {l.id: l for l in db.query(Lead).filter(
            Lead.organization_id == org_id, Lead.id.in_([p.lead_id for p in profiles])).all()}
        props = {p.id: p for p in db.query(WholesaleProperty).filter(
            WholesaleProperty.organization_id == org_id,
            WholesaleProperty.id.in_([p.property_id for p in profiles])).all()}
        for p in profiles:
            deal, lead, prop = deals[p.id], leads.get(p.lead_id), props.get(p.property_id)
            if lead is not None and lead.status == "dnc":
                continue
            who = " ".join(x for x in (getattr(lead, "first_name", None),
                                       getattr(lead, "last_name", None)) if x) or "The seller"
            where = getattr(prop, "street_address", None) or "the property"
            common = dict(deal_id=deal.id, profile_id=p.id, property=where, seller=who,
                          seller_intent=p.seller_intent, qualification=p.qualification_status)
            flist = sorted(facts.get(p.id, []),
                           key=lambda f: -_SELLER_FACT_REASONS[f.fact_type][0])
            if flist:
                top = flist[0]
                pr, title, why = _SELLER_FACT_REASONS[top.fact_type]
                evidence = ["%s said: “%s”" % (who, (f.quote or f.value or "")[:200])
                            for f in flist[:3]]
                evidence.append("Seller Intent %s; qualification: %s" % (
                    p.seller_intent if p.seller_intent is not None else "insufficient",
                    (p.qualification_status or "not assessed").replace("_", " ").lower()))
                items.append(_item("seller_request", pr, "%s — %s" % (title, where), why, evidence,
                                   _deal_link(deal.id), fact_ids=[f.id for f in flist], **common))
            elif p.qualification_status == "HUMAN_REVIEW":
                detail = json.loads(p.qualification_detail or "{}")
                items.append(_item("human_review", 60, "Review this seller — %s" % where,
                                   "; ".join(detail.get("reasons") or ["A person has to decide"]),
                                   ["Reader confidence was too low" if p.needs_human else None],
                                   _deal_link(deal.id), **common))
            elif p.appointment_status == "requested":
                items.append(_item("appointment", 88, "Appointment requested — %s" % where,
                                   "The seller asked for an appointment that is not scheduled yet.",
                                   [], _deal_link(deal.id), **common))

    # 2. Approvals: the human gates on consequential commitments.
    ap = (db.query(WholesaleApproval, WholesaleDeal, WholesaleProperty)
          .join(WholesaleDeal, WholesaleDeal.id == WholesaleApproval.deal_id)
          .join(WholesaleProperty, WholesaleProperty.id == WholesaleDeal.property_id)
          .filter(WholesaleApproval.organization_id == org_id,
                  WholesaleDeal.organization_id == org_id,
                  WholesaleApproval.status == "pending"))
    if not include_test:
        ap = ap.filter(WholesaleDeal.is_test.isnot(True))
    for a, d, wp in ap.all():
        items.append(_item("approval", 86, "Approve %s — %s" % (a.kind, wp.street_address or "deal"),
                           "A %s commits the company; it needs your approval." % a.kind,
                           [a.recommendation[:200] if a.recommendation else None,
                            "Amount: $%s" % format(float(a.amount), ",.0f") if a.amount is not None
                            else None],
                           _deal_link(d.id), deal_id=d.id, approval_id=a.id))

    # 3. Capacity: inquiries waiting because the workspace is full.
    try:
        from app.services import lead_capacity
        held = lead_capacity.held_query(db, org_id).count()
        if held:
            items.append(_item("capacity", 75, "%s seller inquir%s waiting — lead limit reached"
                               % (held, "y" if held == 1 else "ies"),
                               "Inquiries are kept but nobody can work them until capacity exists. "
                               "Upgrade or free up leads.", [], "/leads?capacity=held"))
    except Exception:  # noqa: BLE001
        pass

    # 4. EvoSense: its own NEEDS YOU (handoffs), ownership conflicts, and
    #    paid lookups that are outside policy.
    try:
        from app.models.evosense_models import (EvoSenseEnrichmentDecision, EvoSenseHandoff,
                                                EvoSenseIdentityReview, EvoSenseProperty)
        from app.services.evosense import common as C
        hq = (db.query(EvoSenseHandoff, EvoSenseProperty)
              .join(EvoSenseProperty, EvoSenseProperty.id == EvoSenseHandoff.property_id)
              .filter(EvoSenseHandoff.organization_id == org_id,
                      EvoSenseHandoff.status.in_(("open", "acknowledged"))))
        if not include_test:
            hq = hq.filter(EvoSenseHandoff.is_test.isnot(True))
        for h, prop in hq.all():
            reasons = C.jload(h.reasons, []) or []
            items.append(_item("evosense_handoff", h.priority or 50,
                               "%s — %s" % (reasons[0]["label"] if reasons else "Owner needs you",
                                            prop.street_address or "property"),
                               "; ".join(r.get("label", "") for r in reasons) or "EvoSense handed this off",
                               [h.next_action],
                               "/wholesale/evosense/properties/%s" % prop.id,
                               evosense_property_id=prop.id, handoff_id=h.id))
        iq = db.query(EvoSenseIdentityReview).filter(EvoSenseIdentityReview.organization_id == org_id,
                                                     EvoSenseIdentityReview.status == "open")
        n = iq.count()
        if n:
            items.append(_item("identity_review", 50, "%s property identit%s to confirm"
                               % (n, "y" if n == 1 else "ies"),
                               "Two sources may describe the same house; nothing is merged until you decide.",
                               [], "/wholesale/evosense?tab=identity"))
        # latest decision per property: still waiting for approval?
        latest = {}
        for d in (db.query(EvoSenseEnrichmentDecision)
                  .filter(EvoSenseEnrichmentDecision.organization_id == org_id)
                  .order_by(EvoSenseEnrichmentDecision.created_at.desc()).limit(500).all()):
            latest.setdefault(d.property_id, d)
        waiting = [d for d in latest.values() if d.decision == C.D_APPROVAL]
        if waiting and not include_test:
            real = {p.id for p in db.query(EvoSenseProperty.id).filter(
                EvoSenseProperty.id.in_([d.property_id for d in waiting]),
                EvoSenseProperty.is_test.isnot(True)).all()}
            waiting = [d for d in waiting if d.property_id in real]
        if waiting:
            cost = sum(d.estimated_cost_cents or 0 for d in waiting)
            items.append(_item("enrichment_approval", 40,
                               "%s paid lookup%s waiting for approval" % (len(waiting),
                                                                         "" if len(waiting) == 1 else "s"),
                               "These lookups are outside the strategy's automatic policy.",
                               ["Estimated total %s" % C.money(cost)],
                               "/wholesale/evosense?bucket=needs_enrichment"))
    except Exception:  # noqa: BLE001 - EvoSense not in use for this workspace
        pass

    # 5. Comp review: a deal heading to an offer on a LOW-confidence ARV.
    cr = (db.query(WholesaleDeal, WholesaleProperty)
          .join(WholesaleProperty, WholesaleProperty.id == WholesaleDeal.property_id)
          .filter(WholesaleDeal.organization_id == org_id,
                  WholesaleDeal.stage.in_(("analysis", "offer_review")),
                  WholesaleDeal.arv_confidence == "low"))
    if not include_test:
        cr = cr.filter(WholesaleDeal.is_test.isnot(True))
    for d, wp in cr.all():
        items.append(_item("comp_review", 55, "Review the comps — %s" % (wp.street_address or "deal"),
                           "The ARV rests on low-confidence comparable sales; a person should check "
                           "them before any offer.", [], _deal_link(d.id), deal_id=d.id))

    items.sort(key=lambda i: -i["priority"])
    return items[:limit]


def _data_depth(db, org_id, deals_q, profiles_q, active) -> Dict[str, Any]:
    """Where the evidence is thin, and what it is costing - each count with
    the list behind it."""
    from app.models.wholesale_models import WholesaleDeal
    analysis_stages = ("analysis", "offer_review", "offer_sent", "negotiating")
    in_analysis = deals_q().filter(WholesaleDeal.stage.in_(analysis_stages)).all()
    enough = [d for d in in_analysis if d.arv is not None and d.arv_confidence in ("high", "medium")]
    insufficient = [d for d in in_analysis if d.arv is None or d.arv_confidence in ("insufficient", None)]
    mao_blocked = [d for d in in_analysis if d.mao_status == "NOT_CALCULATED"]
    awaiting_analysis = [p for p in profiles_q().filter(
        WholesaleSellerProfile_status_qualified()).all()
        if p.id in active and active[p.id].stage in ("qualified", "qualifying", "seller_engaged")]
    out = {
        "comps_enough_evidence": {"count": len(enough), "link": "/wholesale?view=analysis"},
        "comps_insufficient": {"count": len(insufficient),
                               "items": [{"deal_id": d.id, "link": _deal_link(d.id)} for d in insufficient[:20]]},
        "mao_not_calculated": {"count": len(mao_blocked),
                               "items": [{"deal_id": d.id, "link": _deal_link(d.id),
                                          "why": (json.loads(d.mao_detail or "{}").get("reasons") or [])[:2]}
                                         for d in mao_blocked[:20]]},
        "qualified_awaiting_analysis": {"count": len(awaiting_analysis),
                                        "items": [{"profile_id": p.id, "link": _deal_link(active[p.id].id)}
                                                  for p in awaiting_analysis[:20]]},
        "blocked_by_provider_config": [], "cost": None,
    }
    try:
        from app.models.evosense_models import EvoSenseProperty
        from app.services.evosense import capabilities as CAPS
        from app.services.evosense import economics as EC
        waiting = db.query(EvoSenseProperty).filter(
            EvoSenseProperty.organization_id == org_id, EvoSenseProperty.is_test.isnot(True),
            EvoSenseProperty.contactability.in_(("ENRICHMENT_NEEDED",))).count()
        for cap in ("PHONE", "SOLD_COMPS"):
            if not CAPS.is_operational(db, org_id, cap):
                out["blocked_by_provider_config"].append({
                    "capability": cap, "label": CAPS.LABELS[cap],
                    "waiting": waiting if cap == "PHONE" else len(insufficient),
                    "why": "No real provider is operational for %s" % CAPS.LABELS[cap].lower(),
                    "link": "/wholesale/evosense?tab=providers"})
        promoted = (db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org_id,
                                                      EvoSenseProperty.promoted_deal_id.isnot(None),
                                                      EvoSenseProperty.is_test.isnot(True))
                    .limit(50).all())
        costs = [EC.acquisition_cost(db, p) for p in promoted]
        if costs:
            def avg(key):
                vals = [c[key] for c in costs if c.get(key) is not None]
                return round(sum(vals) / len(vals)) if vals else None
            out["cost"] = {"deals": len(costs), "avg_cost_to_find_cents": avg("cost_to_find_cents"),
                           "avg_cost_to_contactability_cents": avg("cost_to_contactability_cents"),
                           "avg_cost_to_qualification_cents": avg("cost_to_qualification_cents"),
                           "avg_total_cents": avg("total_cents")}
    except Exception:  # noqa: BLE001 - EvoSense not in use
        pass
    return out


def WholesaleSellerProfile_status_qualified():
    from app.models.wholesale_models import WholesaleSellerProfile
    return WholesaleSellerProfile.qualification_status == "QUALIFIED"


# ── Morning Command Center ──────────────────────────────────────────────────

def command_center(db, org_id: str, *, include_test: bool = False, days: int = 7) -> Dict[str, Any]:
    from sqlalchemy import func
    from app.models.models import Lead, Reply
    from app.models.wholesale_models import (WholesaleDeal, WholesaleEvent, WholesaleSellerProfile)
    now = datetime.utcnow()
    since = now - timedelta(days=days)

    def deals_q():
        q = db.query(WholesaleDeal).filter(WholesaleDeal.organization_id == org_id)
        return q if include_test else q.filter(WholesaleDeal.is_test.isnot(True))

    def profiles_q():
        q = db.query(WholesaleSellerProfile).filter(WholesaleSellerProfile.organization_id == org_id)
        return q if include_test else q.filter(WholesaleSellerProfile.is_test.isnot(True))

    ny = needs_you(db, org_id, include_test=include_test)

    active = {d.seller_profile_id: d for d in deals_q().filter(
        WholesaleDeal.stage.notin_(("dead", "closed"))).all() if d.seller_profile_id}
    qualified = [p for p in profiles_q().filter(
        WholesaleSellerProfile.qualification_status == "QUALIFIED",
        WholesaleSellerProfile.updated_at >= since).all() if p.id in active]

    seller_leads = [p.lead_id for p in profiles_q().all()]
    replies = []
    if seller_leads:
        rows = (db.query(Reply, Lead).join(Lead, Lead.id == Reply.lead_id)
                .filter(Lead.organization_id == org_id, Reply.lead_id.in_(seller_leads),
                        Reply.received_at >= now - timedelta(hours=48))
                .order_by(Reply.received_at.desc()).limit(20).all()) \
            if hasattr(Reply, "received_at") else []
        by_lead = {p.lead_id: p for p in profiles_q().all()}
        for r, l in rows:
            p = by_lead.get(l.id)
            d = active.get(p.id) if p else None
            replies.append({"lead_id": l.id, "seller": " ".join(x for x in (l.first_name, l.last_name) if x),
                            "body": (r.body or "")[:160],
                            "at": r.received_at.isoformat() + "Z" if r.received_at else None,
                            "link": _deal_link(d.id) if d else "/leads/%s" % l.id})

    appts = profiles_q().filter(WholesaleSellerProfile.appointment_status.in_(("requested",
                                                                              "scheduled"))).all()
    appointments = [{"profile_id": p.id, "status": p.appointment_status,
                     "at": p.appointment_at.isoformat() + "Z" if p.appointment_at else None,
                     "link": _deal_link(active[p.id].id) if p.id in active else None}
                    for p in appts if p.id in active]
    appointments.sort(key=lambda a: (a["status"] != "requested", a["at"] or "9999"))

    from app.services import wholesale_seller_intel as SI
    due = [p for p in SI.nurture_due(db, org_id, now=now) if include_test or not p.is_test]
    upcoming = profiles_q().filter(WholesaleSellerProfile.nurture_until > now,
                                   WholesaleSellerProfile.nurture_until <= now + timedelta(days=14)).count()

    awaiting_deals = deals_q().filter(WholesaleDeal.stage == "enrichment_needed").count()
    awaiting = {"wholesale_deals": awaiting_deals, "evosense_properties": 0,
                "link": "/wholesale?stage=enrichment_needed"}
    provider_problems: List[Dict[str, Any]] = []
    spend = None
    try:
        from app.models.evosense_models import EvoSenseProperty
        from app.services.evosense import common as C
        from app.services.evosense import providers as PV
        from app.services.evosense import views as EVV
        eq = db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org_id,
                                               EvoSenseProperty.contactability.in_(
                                                   ("ENRICHMENT_NEEDED", "CONTACT_DATA_FOUND")))
        if not include_test:
            eq = eq.filter(EvoSenseProperty.is_test.isnot(True))
        awaiting["evosense_properties"] = eq.count()
        awaiting["evosense_link"] = "/wholesale/evosense?bucket=needs_enrichment"
        from app.models.evosense_models import EvoSenseProviderConfig
        for cfg in (db.query(EvoSenseProviderConfig)
                    .filter(EvoSenseProviderConfig.organization_id == org_id,
                            EvoSenseProviderConfig.enabled.is_(True)).all()):
            prov = PV.PROVIDERS.get(cfg.provider_key)
            if prov is None:
                continue
            key = cfg.provider_key
            state = PV.health_state(prov, cfg)
            if state in (C.H_DEGRADED, C.H_RATE_LIMITED, C.H_BLOCKED):
                provider_problems.append({"provider": key, "label": prov.label, "state": state,
                                          "reason": getattr(cfg, "last_failure_reason", None),
                                          "link": "/wholesale/evosense?tab=providers"})
        budget_blocked = db.query(EvoSenseProperty).filter(
            EvoSenseProperty.organization_id == org_id,
            EvoSenseProperty.status == "budget_blocked").count()
        if budget_blocked:
            provider_problems.append({"provider": None, "label": "Budget", "state": "BUDGET BLOCKED",
                                      "reason": "%s properties are waiting for budget" % budget_blocked,
                                      "link": "/wholesale/evosense?bucket=budget_blocked"})
        spend = EVV.spend(db, org_id)
    except Exception:  # noqa: BLE001 - EvoSense not in use
        pass

    moves = (db.query(WholesaleEvent.after_state)
             .filter(WholesaleEvent.organization_id == org_id,
                     WholesaleEvent.action == "deal.stage_changed",
                     WholesaleEvent.created_at >= since).all())
    movement: Dict[str, int] = {}
    for (after,) in moves:
        try:
            st = (json.loads(after) or {}).get("stage")
        except (ValueError, TypeError):
            st = None
        if st:
            movement[st] = movement.get(st, 0) + 1

    data_depth = _data_depth(db, org_id, deals_q, profiles_q, active)

    return {
        "generated_at": now.isoformat() + "Z",
        "data_depth": data_depth,
        "include_test": include_test,
        "needs_you": {"count": len(ny), "items": ny},
        "new_qualified": {"count": len(qualified), "days": days,
                          "items": [{"profile_id": p.id, "deal_id": active[p.id].id,
                                     "seller_intent": p.seller_intent,
                                     "link": _deal_link(active[p.id].id)} for p in qualified[:20]]},
        "seller_replies": {"count": len(replies), "hours": 48, "items": replies},
        "appointments": {"count": len(appointments), "items": appointments[:20]},
        "awaiting_contact_data": awaiting,
        "nurture": {"due": len(due), "upcoming_14_days": upcoming,
                    "items": [{"profile_id": p.id, "until": p.nurture_until.isoformat() + "Z",
                               "reason": p.nurture_reason,
                               "link": _deal_link(active[p.id].id) if p.id in active else None}
                              for p in due[:20]]},
        "provider_problems": {"count": len(provider_problems), "items": provider_problems},
        "spend": spend,
        "pipeline_movement": {"days": days, "by_stage": movement,
                              "total": sum(movement.values())},
    }
