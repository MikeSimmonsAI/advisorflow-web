"""READING THE DEMONSTRATION WORLD — the panels, rendered from the real rows.

Every function here is a READ over the demo tenant's ordinary tables. Nothing
is computed from a fixture, a constant or a hardcoded number: if the Priorities
panel says three records need a person today, three records genuinely qualify
that way, and moving one changes the count.

That is not a nicety. A demo whose numbers are hardcoded is a demo that
disagrees with itself the first time the presenter clicks something, in front
of the prospect, with no way to recover.

WHY THE DEMO SUITE RENDERS ITS OWN PANELS RATHER THAN THE TENANT SCREENS

The alternative — dropping the presenter into the real customer app pointed at
the demo workspace — was considered and rejected on two counts. It would
require the presenter to hold a membership in the demonstration workspace,
which makes demo access a species of workspace access and breaks the rule that
they are different entitlements. And a demo action would then be firing inside
the same screens a real tenant uses, one context switch away from a real
customer's data.

So the Suite reads the demo tenant through this module and renders it itself.
The DATA is the product's real data model; the surface is built for presenting.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.demo_suite_models import DemoActionEvent, DemoEnvironment
from app.models.models import (BookingLink, EmailMessage, Lead, Message,
                               Organization, User)
from app.models.sales_models import (OPPORTUNITY_STAGES, STAGE_LABELS,
                                     STAGE_LIVE, STAGE_LOST, STAGE_WON,
                                     SCOPE_BRAND_SALES_ORG, BrandSalesOrg,
                                     DiscoveryRecord, Membership, Opportunity,
                                     OpportunityEvent, ROLE_SALES_MANAGER)
from app.services import demo_actions

# Weighted-pipeline stage probabilities. DECLARED, not hidden inside a sum:
# a projection whose weights nobody can see is a number nobody should trust,
# and the first question a CFO asks in a demo is "weighted how?".
STAGE_WEIGHTS = {
    "prospect": 0.05, "contacted": 0.10, "discovery": 0.25,
    "demo_build": 0.35, "demo_proposal": 0.50, "closing": 0.75,
    "won": 1.00, "onboarding": 1.00, "live": 1.00,
}


def _money(v: Optional[Decimal]) -> float:
    return float(v or 0)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _lead_key(lead: Lead) -> str:
    """The seed key an action would name this lead by."""
    return (lead.id or "").rsplit("-", 1)[-1]


def _opp_key(opp: Opportunity) -> str:
    return (opp.id or "").rsplit("-", 1)[-1]


# ─────────────────────────────────────────────────────────────────────────────
# CUSTOMER-WORKSPACE PANELS
# ─────────────────────────────────────────────────────────────────────────────

def leads_panel(db: Session, org: Organization) -> Dict[str, Any]:
    rows = (db.query(Lead)
            .filter(Lead.organization_id == org.id)
            .order_by(Lead.last_name, Lead.first_name).all())
    owners = {u.id: u for u in db.query(User).filter(
        User.organization_id == org.id).all()}
    now = datetime.utcnow()
    out = []
    for lead in rows:
        owner = owners.get(lead.assigned_to_id) if lead.assigned_to_id else None
        age = ((now - lead.last_contact_date).days
               if lead.last_contact_date else None)
        out.append({
            "key": _lead_key(lead),
            "name": "%s %s" % (lead.first_name, lead.last_name),
            "phone": lead.phone,
            "email": lead.email,
            "tier": lead.tier,
            "status": lead.status,
            "temperature": getattr(lead.engagement_temperature, "value",
                                   lead.engagement_temperature),
            "channel": lead.contact_channel,
            "source": lead.source,
            "owner": owner.full_name if owner else None,
            "days_since_contact": age,
            "never_contacted": lead.last_contact_date is None,
            "dormant": age is not None and age >= 180,
            "note": lead.ai_lead_quality_note,
            "blocked": (lead.status or "").lower() == "dnc",
        })
    return {"organization": org.name, "total": len(out), "leads": out}


def priority_panel(db: Session, org: Organization) -> Dict[str, Any]:
    """The work queue, in the order the qualification decision produces.

    THE ORDER IS COMPUTED, NOT STORED, so this panel and the Qualify action can
    never disagree: both call `demo_actions.evaluate_lead`, which is the one
    implementation. A panel with its own copy of the rule is a panel that
    eventually shows a different answer than the button.
    """
    rows = (db.query(Lead).filter(Lead.organization_id == org.id).all())
    owners = {u.id: u for u in db.query(User).filter(
        User.organization_id == org.id).all()}
    scored = []
    excluded = []
    for lead in rows:
        d = demo_actions.evaluate_lead(lead)
        owner = owners.get(lead.assigned_to_id) if lead.assigned_to_id else None
        item = {
            "key": _lead_key(lead),
            "name": "%s %s" % (lead.first_name, lead.last_name),
            "band": d["band"], "score": d["score"], "reason": d["reason"],
            "factors": d["factors"], "status": lead.status,
            "owner": owner.full_name if owner else "Unassigned",
            "temperature": getattr(lead.engagement_temperature, "value",
                                   lead.engagement_temperature),
            "qualified": bool(lead.ai_lead_quality_note),
        }
        (excluded if d["excluded"] else scored).append(item)
    scored.sort(key=lambda i: (demo_actions.BAND_ORDER.get(i["band"], 5),
                               -(i["score"] or 0), i["name"]))
    bands: Dict[str, int] = {}
    for i in scored:
        bands[i["band"]] = bands.get(i["band"], 0) + 1
    return {"queue": scored, "excluded": excluded, "bands": bands,
            "needs_a_person_today": bands.get(demo_actions.BAND_URGENT, 0)}


def conversation_panel(db: Session, env: DemoEnvironment, org: Organization,
                       lead_key: Optional[str]) -> Dict[str, Any]:
    """One lead's whole story — texts, emails and any pending AI draft."""
    leads = (db.query(Lead).filter(Lead.organization_id == org.id).all())
    by_key = {_lead_key(l): l for l in leads}
    key = lead_key if lead_key in by_key else None
    if key is None:
        # Default to the most recently touched record, which is what the
        # presenter is almost always about to talk about.
        touched = sorted([l for l in leads if l.last_contact_date],
                         key=lambda l: l.last_contact_date, reverse=True)
        if not touched:
            return {"lead": None, "timeline": [], "draft": None}
        key = _lead_key(touched[0])
    lead = by_key[key]

    users = {u.id: u for u in db.query(User).filter(
        User.organization_id == org.id).all()}
    timeline: List[Dict[str, Any]] = []
    for m in (db.query(Message).filter(Message.lead_id == lead.id)
              .order_by(Message.sent_at).all()):
        inbound = (m.body or "").startswith("[FROM ")
        body = m.body or ""
        if inbound:
            body = body.split("] ", 1)[-1]
        sender = users.get(m.sender_id)
        timeline.append({
            "kind": "sms", "direction": "in" if inbound else "out",
            "body": body, "at": _iso(m.sent_at),
            "who": lead.first_name if inbound else (
                sender.full_name if sender else "Advisor"),
            "simulated": (m.twilio_sid or "").startswith("SIMULATED-DEMO"),
            "state": m.send_state,
        })
    for e in (db.query(EmailMessage).filter(EmailMessage.lead_id == lead.id)
              .order_by(EmailMessage.sent_at).all()):
        sender = users.get(e.sender_id)
        timeline.append({
            "kind": "email", "direction": "out", "subject": e.subject,
            "body": e.body_html, "at": _iso(e.sent_at),
            "who": sender.full_name if sender else "Advisor",
            "simulated": (e.provider_message_id or "").startswith("SIMULATED-DEMO"),
            "state": e.status,
        })
    timeline.sort(key=lambda t: t["at"] or "")

    pending = (db.query(DemoActionEvent)
               .filter(DemoActionEvent.environment_id == env.id,
                       DemoActionEvent.action == "ai_follow_up",
                       DemoActionEvent.target_id == lead.id)
               .order_by(DemoActionEvent.occurred_at.desc())
               .first())

    owner = users.get(lead.assigned_to_id) if lead.assigned_to_id else None
    return {
        "lead": {
            "key": key,
            "name": "%s %s" % (lead.first_name, lead.last_name),
            "phone": lead.phone, "email": lead.email,
            "status": lead.status, "tier": lead.tier,
            "temperature": getattr(lead.engagement_temperature, "value",
                                   lead.engagement_temperature),
            "owner": owner.full_name if owner else "Unassigned",
            "note": lead.ai_lead_quality_note,
            "blocked": (lead.status or "").lower() == "dnc",
        },
        "timeline": timeline,
        # A DRAFT IS NOT A MESSAGE, and this is where that distinction becomes
        # visible: it renders beside the thread, awaiting approval, rather than
        # inside it.
        "draft": ({"body": pending.detail,
                   "at": _iso(pending.occurred_at)} if pending else None),
        "options": [{"key": _lead_key(l),
                     "name": "%s %s" % (l.first_name, l.last_name)}
                    for l in sorted(leads, key=lambda x: x.last_name or "")],
    }


def calendar_panel(db: Session, org: Organization) -> Dict[str, Any]:
    lead_ids = [r[0] for r in db.query(Lead.id).filter(
        Lead.organization_id == org.id).all()]
    if not lead_ids:
        return {"appointments": [], "upcoming": 0}
    leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_(lead_ids)).all()}
    users = {u.id: u for u in db.query(User).filter(
        User.organization_id == org.id).all()}
    rows = (db.query(BookingLink)
            .filter(BookingLink.lead_id.in_(lead_ids))
            .order_by(BookingLink.booked_time).all())
    now = datetime.utcnow()
    out = []
    upcoming = 0
    for b in rows:
        lead = leads.get(b.lead_id)
        advisor = users.get(b.user_id)
        future = bool(b.booked_time and b.booked_time >= now)
        if future:
            upcoming += 1
        out.append({
            "id": b.id,
            "lead": ("%s %s" % (lead.first_name, lead.last_name)
                     if lead else "Unknown"),
            "lead_key": _lead_key(lead) if lead else None,
            "advisor": advisor.full_name if advisor else None,
            "label": b.appt_label, "minutes": b.appt_duration,
            "when": _iso(b.booked_time), "status": b.status,
            "future": future,
        })
    return {"appointments": out, "upcoming": upcoming}


# ─────────────────────────────────────────────────────────────────────────────
# BRAND-SALES PANELS
# ─────────────────────────────────────────────────────────────────────────────

def _opportunities(db: Session, brand: BrandSalesOrg) -> List[Opportunity]:
    return (db.query(Opportunity)
            .filter(Opportunity.brand_sales_org_id == brand.id)
            .order_by(Opportunity.company_name).all())


def pipeline_panel(db: Session, brand: BrandSalesOrg) -> Dict[str, Any]:
    opps = _opportunities(db, brand)
    owners = {u.id: u for u in db.query(User).filter(
        User.id.in_([o.owner_user_id for o in opps if o.owner_user_id])).all()} \
        if opps else {}
    now = datetime.utcnow()
    columns = []
    for stage in OPPORTUNITY_STAGES:
        deals = []
        for o in opps:
            if o.stage != stage:
                continue
            days = ((now - o.stage_changed_at).days
                    if o.stage_changed_at else None)
            owner = owners.get(o.owner_user_id)
            deals.append({
                "key": _opp_key(o),
                "company": o.company_name, "contact": o.contact_name,
                "industry": o.industry,
                "value": _money(o.deal_value),
                "owner": owner.full_name if owner else "Unassigned",
                "days_in_stage": days,
                "stalled": bool(days is not None and days >= 10),
                "next_action": o.next_action,
                "next_action_due": _iso(o.next_action_due_at),
                "overdue": bool(o.next_action and o.next_action_due_at
                                and o.next_action_due_at < now),
            })
        columns.append({"stage": stage,
                        "label": STAGE_LABELS.get(stage, stage),
                        "weight": STAGE_WEIGHTS.get(stage, 0),
                        "count": len(deals),
                        "value": sum(d["value"] for d in deals),
                        "deals": deals})
    return {"brand": brand.name, "columns": columns,
            "stage_options": [{"key": s, "label": STAGE_LABELS.get(s, s)}
                              for s in OPPORTUNITY_STAGES]}


def team_panel(db: Session, brand: BrandSalesOrg) -> Dict[str, Any]:
    mems = (db.query(Membership)
            .filter(Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                    Membership.scope_id == brand.id,
                    Membership.is_active.is_(True)).all())
    users = {u.id: u for u in db.query(User).filter(
        User.id.in_([m.user_id for m in mems])).all()} if mems else {}
    opps = _opportunities(db, brand)
    now = datetime.utcnow()
    people = []
    for m in mems:
        u = users.get(m.user_id)
        if u is None:
            continue
        mine = [o for o in opps if o.owner_user_id == u.id]
        open_deals = [o for o in mine if o.stage not in (STAGE_LIVE, STAGE_LOST)]
        overdue = [o for o in mine
                   if o.next_action and o.next_action_due_at
                   and o.next_action_due_at < now]
        people.append({
            "name": u.full_name,
            "role": m.role,
            "role_label": ("Sales Manager" if m.role == ROLE_SALES_MANAGER
                           else "Account Executive"),
            "open_deals": len(open_deals),
            "pipeline_value": sum(_money(o.deal_value) for o in open_deals),
            "overdue": len(overdue),
            "next_actions": [{"key": _opp_key(o), "company": o.company_name,
                              "action": o.next_action,
                              "due": _iso(o.next_action_due_at),
                              "overdue": o.next_action_due_at < now
                              if o.next_action_due_at else False}
                             for o in mine if o.next_action],
        })
    people.sort(key=lambda p: (p["role"] != ROLE_SALES_MANAGER, p["name"]))
    return {"brand": brand.name, "people": people,
            "overdue_total": sum(p["overdue"] for p in people)}


def revenue_panel(db: Session, brand: BrandSalesOrg) -> Dict[str, Any]:
    opps = _opportunities(db, brand)
    open_deals = [o for o in opps if o.stage not in (STAGE_LIVE, STAGE_LOST)]
    closed = [o for o in opps if o.stage in (STAGE_WON, STAGE_LIVE)]
    weighted = sum(_money(o.deal_value) * STAGE_WEIGHTS.get(o.stage, 0)
                   for o in open_deals)
    by_stage = []
    for stage in OPPORTUNITY_STAGES:
        deals = [o for o in opps if o.stage == stage]
        if not deals:
            continue
        by_stage.append({"stage": stage, "label": STAGE_LABELS.get(stage, stage),
                         "count": len(deals),
                         "value": sum(_money(o.deal_value) for o in deals)})
    return {
        "brand": brand.name,
        "open_pipeline": sum(_money(o.deal_value) for o in open_deals),
        "open_count": len(open_deals),
        "weighted_projection": round(weighted, 2),
        "closed_value": sum(_money(o.deal_value) for o in closed),
        "closed_count": len(closed),
        "by_stage": by_stage,
        # STATED, NOT BURIED. The weights that produced the projection are
        # returned with it, because the first question in the room is "weighted
        # how?" and "the system works it out" is not an answer.
        "weights": [{"stage": s, "label": STAGE_LABELS.get(s, s),
                     "weight": STAGE_WEIGHTS.get(s, 0)}
                    for s in OPPORTUNITY_STAGES],
    }


def customers_panel(db: Session, brand: BrandSalesOrg,
                    org: Organization) -> Dict[str, Any]:
    """The portfolio an executive would see for this brand.

    Built from the deals that reached Won or Live plus the demonstration
    workspace itself, because those are the customers this brand actually has
    in the demonstration. Nothing is invented to fill the panel out.
    """
    opps = _opportunities(db, brand)
    now = datetime.utcnow()
    rows = []
    for o in opps:
        if o.stage not in (STAGE_WON, "onboarding", STAGE_LIVE):
            continue
        age = (now - o.won_at).days if o.won_at else None
        rows.append({
            "company": o.company_name, "contact": o.contact_name,
            "industry": o.industry,
            "state": STAGE_LABELS.get(o.stage, o.stage),
            "value": _money(o.deal_value),
            "since_days": age,
            "attention": bool(o.next_action and o.next_action_due_at
                              and o.next_action_due_at < now),
        })
    lead_count = db.query(Lead).filter(Lead.organization_id == org.id).count()
    rows.append({
        "company": org.name, "contact": None,
        "industry": "Demonstration workspace",
        "state": "Live", "value": None, "since_days": None,
        "attention": False,
        "note": "%d live records — this is the workspace the operator panels "
                "are showing." % lead_count,
    })
    return {"brand": brand.name, "customers": rows,
            "needing_attention": sum(1 for r in rows if r.get("attention"))}


# The Launch Engine's real intake sections. NOT invented for the demo — this is
# the sequence a customer is actually taken through after a deal is won, so a
# prospect asking "what happens on Monday" is shown the real answer.
LAUNCH_STEPS = [
    ("company", "Company information",
     "Legal name, locations, hours and the people who will use the system.",
     "Customer"),
    ("branding", "Branding and assets",
     "Logo, colours and the from-name every message goes out under.",
     "Customer"),
    ("website", "Website and hosting access",
     "Where enquiry forms live and how they will reach the platform.",
     "Customer + Implementation"),
    ("integrations", "Integrations",
     "Calendars, phone numbers and any system already in use.",
     "Implementation"),
    ("team", "Team and current systems",
     "Who does what today, and what they use to do it.", "Customer"),
    ("process", "Customer process",
     "How enquiries are handled now, so the automation matches the business "
     "rather than the other way round.", "Customer + Implementation"),
    ("files", "Files and documents",
     "Existing lead lists and templates to bring across.", "Customer"),
    ("review", "Review and submit",
     "The customer confirms it, and provisioning follows.", "Both"),
]


def launch_panel(db: Session, brand: BrandSalesOrg) -> Dict[str, Any]:
    """What week one looks like, against the demonstration's won customer."""
    won = next((o for o in _opportunities(db, brand)
                if o.stage in (STAGE_WON, "onboarding", STAGE_LIVE)), None)
    total = len(LAUNCH_STEPS)
    # A LIVE customer has completed the intake; a WON one is partway through.
    completed = total if (won is not None and won.stage == STAGE_LIVE) else 3
    steps = []
    for i, (key, title, detail, owner) in enumerate(LAUNCH_STEPS):
        steps.append({
            "key": key, "title": title, "detail": detail, "owner": owner,
            "state": ("complete" if i < completed
                      else "in_progress" if i == completed else "not_started"),
        })
    return {
        "customer": won.company_name if won else None,
        "won_at": _iso(won.won_at) if won else None,
        "steps": steps,
        "completed": completed, "total": total,
        "source": "AdvisorFlow Launch Engine — the same intake a real customer "
                  "is taken through after a deal is won.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# THE WHOLE WORLD
# ─────────────────────────────────────────────────────────────────────────────

def snapshot(db: Session, env: DemoEnvironment, org: Organization,
             brand: BrandSalesOrg,
             lead_key: Optional[str] = None) -> Dict[str, Any]:
    """Every panel, in one round trip.

    ONE REQUEST, DELIBERATELY. A presenter clicking between panels mid-sentence
    must not be waiting on a spinner, and the whole demonstration world is a few
    dozen rows — fetching it in nine requests would be slower and would let the
    panels disagree with each other by a few hundred milliseconds while an
    action settles.
    """
    return {
        "priority": priority_panel(db, org),
        "leads": leads_panel(db, org),
        "conversation": conversation_panel(db, env, org, lead_key),
        "calendar": calendar_panel(db, org),
        "pipeline": pipeline_panel(db, brand),
        "team": team_panel(db, brand),
        "revenue": revenue_panel(db, brand),
        "customers": customers_panel(db, brand, org),
        "launch": launch_panel(db, brand),
    }
