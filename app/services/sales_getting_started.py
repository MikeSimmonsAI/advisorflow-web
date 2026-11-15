"""GETTING STARTED — a new salesperson's path from approval to their first
paid sale, read from their real records. Nothing is stored here: every step
is a fact about their own deals (or their own applicant onboarding), and each
step links to the record or queue where the next action happens.

Two different "onboardings" appear and are named apart:
  * Applicant onboarding  - the salesperson's own paperwork (Sales Onboarding)
  * Customer onboarding   - handing a won customer over to setup (Sold / Onboarding)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import (PROP_ACCEPTED, PROP_SENT, PROP_VIEWED, Proposal, User)
from app.models.sales_models import (STAGE_CLOSING, STAGE_CONTACTED, STAGE_DEMO_BUILD, STAGE_DISCOVERY,
                                     STAGE_LIVE, STAGE_ONBOARDING, STAGE_PROPOSAL, STAGE_PROSPECT,
                                     STAGE_WON, Opportunity)

PAST_CONTACT = (STAGE_CONTACTED, STAGE_DISCOVERY, STAGE_DEMO_BUILD, STAGE_PROPOSAL, STAGE_CLOSING,
                STAGE_WON, STAGE_ONBOARDING, STAGE_LIVE)
HANDED_OFF = (STAGE_ONBOARDING, STAGE_LIVE)


def _first(opps: List[Opportunity], pred) -> Optional[Opportunity]:
    return next((o for o in opps if pred(o)), None)


def steps_for(db: Session, user: User, brand_id: Optional[str] = None) -> Dict[str, Any]:
    from app.models.compensation_models import CompensationEntry
    from app.models.scheduling_models import AppointmentParticipant
    from app.models.sales_quote_models import SalesQuoteDraft
    from app.services import sales_onboarding as onb_svc
    from app.services import sales_team

    program = sales_team.program_brand(db)
    approved = True
    if program is not None and (brand_id in (None, program.id)):
        rec = onb_svc.record(db, user.id, program.id)
        approved = rec is None or rec.status == "approved"
    q = db.query(Opportunity).filter(Opportunity.owner_user_id == user.id)
    if brand_id:
        q = q.filter(Opportunity.brand_sales_org_id == brand_id)
    opps = q.order_by(Opportunity.created_at.desc()).all()
    ids = [o.id for o in opps] or ["-"]
    quotes = {r.opportunity_id for r in db.query(SalesQuoteDraft.opportunity_id)
              .filter(SalesQuoteDraft.opportunity_id.in_(ids)).all()}
    props = db.query(Proposal).filter(Proposal.opportunity_id.in_(ids)).all()
    prop_by_opp: Dict[str, List[Proposal]] = {}
    for p in props:
        prop_by_opp.setdefault(p.opportunity_id, []).append(p)
    sent = {p.opportunity_id for p in props if p.sales_status in (PROP_SENT, PROP_VIEWED, PROP_ACCEPTED)}
    accepted = {p.opportunity_id for p in props if p.sales_status == PROP_ACCEPTED}
    appts = db.query(AppointmentParticipant).filter(AppointmentParticipant.user_id == user.id).count()
    comm = db.query(CompensationEntry).filter(CompensationEntry.payee_user_id == user.id).count()

    def link(o, suffix=""):
        return ("/sales/opportunities/%s%s" % (o.id, suffix)) if o else None

    working = _first(opps, lambda o: o.status == "open")
    contacted = _first(opps, lambda o: o.stage in PAST_CONTACT)
    followed = _first(opps, lambda o: bool(o.next_action))
    demo = _first(opps, lambda o: o.demo_status not in (None, "not_requested"))
    quoted = _first(opps, lambda o: o.id in quotes)
    proposed = _first(opps, lambda o: o.id in prop_by_opp)
    sent_o = _first(opps, lambda o: o.id in sent)
    won = _first(opps, lambda o: o.id in accepted or o.status == "won" or o.stage in (STAGE_WON,) + HANDED_OFF)
    handed = _first(opps, lambda o: o.stage in HANDED_OFF)
    target = working or (opps[0] if opps else None)

    steps = [
        {"key": "approved", "label": "Applicant onboarding approved", "done": approved,
         "link": "/sales-onboarding", "action": "Finish Sales Onboarding"},
        {"key": "prospect", "label": "Add a prospect or open your assigned leads", "done": bool(opps),
         "link": "/sales/pipeline?add=1", "alt_link": "/sales/pipeline", "action": "Add Lead"},
        {"key": "contact", "label": "Contact them and do discovery", "done": bool(contacted),
         "link": link(target), "action": "Open the deal"},
        {"key": "follow_up", "label": "Set a follow-up or book an appointment", "done": bool(followed) or appts > 0,
         "link": link(target) or "/sales/appointments", "action": "Schedule"},
        {"key": "demo", "label": "Book an existing demo, or request a custom demo when needed", "done": bool(demo),
         "optional": True, "link": link(demo or target), "action": "Book Demo / Request Custom Demo"},
        {"key": "quote", "label": "Build Quote", "done": bool(quoted),
         "link": link(quoted or target, "/quote"), "action": "Build Quote"},
        {"key": "proposal", "label": "Save and review the customer proposal", "done": bool(proposed),
         "link": link(proposed or quoted or target), "action": "Open Quote & Proposal"},
        {"key": "send", "label": "Send it through the proposal flow", "done": bool(sent_o),
         "link": link(proposed or target), "action": "Send proposal"},
        {"key": "accepted", "label": "Track acceptance and payment", "done": bool(won),
         "link": link(sent_o or target), "action": "Track"},
        {"key": "handoff", "label": "Hand the won deal to customer onboarding", "done": bool(handed),
         "link": link(won) or "/sales/onboarding", "action": "Hand off"},
        {"key": "commission", "label": "See your own commission status", "done": comm > 0,
         "link": "/sales/my-compensation", "action": "My Compensation"},
    ]
    for s in steps:
        if not s.get("link"):
            s["link"] = "/sales/pipeline"
    nxt = next((s for s in steps if not s["done"] and not s.get("optional")), None)
    return {"steps": steps, "next": nxt, "approved": approved,
            "complete": all(s["done"] for s in steps if not s.get("optional")),
            "deal_count": len(opps)}
