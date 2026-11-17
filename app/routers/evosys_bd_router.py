"""EvoSys Business Development (PR #663) - what the six screens read, plus
adding contacts and building a campaign from picked leads.

Same door as evosys_sales_router / evosys_sales_work_router (/sales/evosys/*,
the EvoSys Pro team through normal login and team membership; reps see their
own leads; managers change campaigns). Every path here is new, so the
existing API stays as it was; saving, approving, switching, AI drafts, reply
help, intro calls and signup go through the existing endpoints.

  Today          GET  /today
  Add contacts   POST /contacts, PATCH /contacts/{id}
  Leads          GET  /contacts, GET /contacts/{id}, POST /contacts/{id}/handled,
                 GET  /batches, GET /sources
  Campaigns      GET  /campaign-list, POST /campaigns/suggest,
                 POST /campaigns/audience/preview, POST /campaigns/picked,
                 GET  /campaigns/{id}/members, GET /campaigns/{id}/examples,
                 POST /campaigns/{id}/audience/preview,
                 POST|DELETE /campaigns/{id}/leads, POST /campaigns/{id}/archive
  Conversations  GET  /inbox
  Appointments   GET  /calls

A picked campaign (an event, a referral, a project...) emails only the leads
put in it; automatic industry + market routing never adds anyone to it. A
contact is in one follow-up at a time (one state row per lead), so two
sequences never overlap. New campaigns start Off and unapproved.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.evosys_sales_models import (EvoSysCampaign, EvoSysLead, EvoSysOutreachMessage,
                                            EvoSysOutreachState)
from app.models.sales_models import STAGE_LABELS, STAGE_PROSPECT, Opportunity, OpportunityEvent
from app.routers.evosys_sales_router import (STOP_LABELS, Ctx, ctx, _hours_text, _leads_q, _manager, _msg, _opp,
                                             iso)
from app.services.evosys_sales import campaigns as CA
from app.services.evosys_sales import facts as F
from app.services.evosys_sales import grouping as G
from app.services.evosys_sales import outreach as O

router = APIRouter(prefix="/sales/evosys", tags=["evosys-business-development"])

PRODUCT = "EvoSys Business Development"
KIND_LABELS = {"group": "Industry + market", "event": "Event", "relationship": "Relationship / referrals",
               "project": "Project", "offer": "Offer", "list": "List"}
PICKED_KINDS = ("event", "relationship", "project", "offer", "list")
SIGNED_UP = ("won", "onboarding", "live")
HELD_LABELS = {"no_email": "No email - call instead", "opted_out": "Opted out", "do_not_contact": "Do not contact",
               "replied": "Already replied", "booked": "Call booked", "stage": "Moved on in the pipeline",
               "in_other_campaign": "In another campaign", "sending_elsewhere": "Being emailed by another campaign",
               "duplicate": "Possible duplicate", "held": "Held for review", "excluded": "Excluded"}
MISSING_LABELS = {"event": "event", "referrer": "who referred them", "personal_note": "personal note",
                  "industry": "industry", "market": "market"}

# Steps for a new picked campaign. Warm wording uses only saved facts.
WARM_EVENT_STEPS = [
    {"day": 0, "channel": "email", "subject": "Good meeting you, {first_name}",
     "body": "Hi {first_name},\n\nGood meeting you at {event}. I'd like to hear more about what you're working "
             "on at {company}.\n\nOpen to a quick call next week? Pick any time that works: {booking_link}\n\n"
             "{sender_name}\nEvoSys Pro"},
    {"day": 4, "channel": "email", "subject": "Re: Good meeting you, {first_name}",
     "body": "Hi {first_name},\n\nFollowing up in case this got buried. Still happy to grab 15 minutes: "
             "{booking_link}\n\n{sender_name}"},
    {"day": 10, "channel": "email", "subject": "Staying in touch",
     "body": "Hi {first_name},\n\nI'll leave it here for now. If a call would help down the road, grab a time "
             "anytime: {booking_link}\n\n{sender_name}"},
]
WARM_REFERRAL_STEPS = [
    {"day": 0, "channel": "email", "subject": "{referrer} suggested we connect",
     "body": "Hi {first_name},\n\n{referrer} suggested I reach out. I'd like to learn a bit about {company} and "
             "see whether we can help.\n\nWould a short call work? {booking_link}\n\n{sender_name}\nEvoSys Pro"},
    {"day": 5, "channel": "email", "subject": "Re: {referrer} suggested we connect",
     "body": "Hi {first_name},\n\nJust bumping this up. Happy to work around your schedule: {booking_link}\n\n"
             "{sender_name}"},
]
RESEARCHED_STEPS = [
    {"day": 0, "channel": "email", "subject": "Quick question for {company}",
     "body": "Hi {first_name},\n\nI work with service businesses on getting more booked work from the leads they "
             "already have. Would a 15-minute call be worth it? {booking_link}\n\n{sender_name}\nEvoSys Pro"},
    {"day": 4, "channel": "email", "subject": "Re: Quick question for {company}",
     "body": "Hi {first_name},\n\nFollowing up once. If now isn't the time, no problem: {booking_link}\n\n"
             "{sender_name}"},
]


def default_steps(kind: str, tone: str) -> list:
    if tone == "warm":
        return [dict(x) for x in (WARM_REFERRAL_STEPS if kind == "relationship" else WARM_EVENT_STEPS)]
    return [dict(x) for x in RESEARCHED_STEPS]


# ── shared helpers ────────────────────────────────────────────────────────────

def _s(db: Session, c: Ctx):
    return O.get_settings(db, c.bso)


def _lead(db: Session, s, o: Opportunity) -> EvoSysLead:
    return db.get(EvoSysLead, o.id) or G.ensure(db, s, o)


def _states(db: Session, ids: Iterable[str]) -> Dict[str, EvoSysOutreachState]:
    ids = list(ids)
    if not ids:
        return {}
    return {x.opportunity_id: x for x in db.query(EvoSysOutreachState)
            .filter(EvoSysOutreachState.opportunity_id.in_(ids)).all()}


def _camps(db: Session, s) -> Dict[str, EvoSysCampaign]:
    return {k.id: k for k in CA.all_for(db, s)}


def _booked_ids(db: Session, bso: str) -> set:
    from app.models.scheduling_models import SalesAppointment
    return {r[0] for r in db.query(SalesAppointment.opportunity_id)
            .filter(SalesAppointment.brand_sales_org_id == bso, SalesAppointment.opportunity_id.isnot(None),
                    SalesAppointment.status.notin_(("cancelled", "canceled"))).all()}


def _held_label(reason: Optional[str]) -> str:
    r = reason or ""
    if r.startswith("missing:"):
        return "Missing " + ", ".join(MISSING_LABELS.get(x, x) for x in r[8:].split(",") if x)
    return HELD_LABELS.get(r, "Held")


def _status(st: Optional[EvoSysOutreachState], o: Opportunity, camp: Optional[EvoSysCampaign]) -> dict:
    if st is None:
        if o.stage not in O.WORKING_STAGES:
            return {"key": "not_in", "label": "Not in outreach"}
        if not O._has_email(o):
            return {"key": "no_email", "label": "No email - call instead"}
        return {"key": "none", "label": "Not in a campaign"}
    if st.status in ("queued", "active") and st.held_reason:
        return {"key": "held", "label": _held_label(st.held_reason), "reason": st.held_reason}
    if st.status == "queued":
        return {"key": "queued", "label": "In campaign - waiting for it to be on"}
    if st.status == "active":
        if camp is not None and not camp.sending_on:
            return {"key": "paused", "label": "Paused - message %d waiting" % (st.next_step + 1)}
        return {"key": "active", "label": "In follow-up - message %d next" % (st.next_step + 1),
                "next_due_at": iso(st.next_due_at)}
    if st.status == "finished":
        return {"key": "finished", "label": "All messages sent"}
    return {"key": st.stop_reason or "stopped", "label": STOP_LABELS.get(st.stop_reason, "Stopped")}


def _row(s, o: Opportunity, st, lead: Optional[EvoSysLead], camps: dict, booked: bool = False) -> dict:
    camp = camps.get(st.campaign_id) if st is not None and st.campaign_id else None
    ch = (lead.source_channel if lead is not None else None) or G.channel_of(o.source_system, o.source)
    return {"id": o.id, "company": o.company_name or o.contact_name, "contact": o.contact_name,
            "role": o.contact_role, "phone": o.phone, "email": o.email, "website": o.website,
            "industry": G.label_of(G.industries(s), lead.industry_key) if lead and lead.industry_key else o.industry,
            "market": G.label_of(G.markets(s), lead.market_key) if lead and lead.market_key else None,
            "review": lead.review_status if lead else "ok",
            "review_label": review_label(lead),
            "stage": o.stage, "stage_label": STAGE_LABELS.get(o.stage, o.stage),
            "source_key": ch, "source_label": G.SOURCE_LABEL.get(ch, "Other"), "source_detail": o.source_detail,
            "met_on": o.met_on.isoformat() if o.met_on else None, "preferred_contact": o.preferred_contact, "batch": batch_label(o, ch),
            "campaign_id": camp.id if camp else None, "campaign": camp.name if camp else None,
            "created_at": iso(o.created_at), "outreach": _status(st, o, camp), "booked": booked}


def review_label(lead: Optional[EvoSysLead]) -> Optional[str]:
    """Say which part of the group is missing: 'unclear_market' is also used
    when the market is known but the industry isn't one of the configured ones."""
    if lead is None:
        return None
    if lead.review_status == "unclear_market" and lead.market_key and not lead.industry_key:
        return "Industry not set"
    if lead.review_status == "unclear_market" and lead.industry_key and lead.market_key:
        return "Grouped"
    return G.REVIEW_LABEL.get(lead.review_status, lead.review_status)


def batch_label(o: Opportunity, ch: Optional[str] = None) -> str:
    """Which list a lead came in with, so older batches can be counted."""
    if o.import_batch_id:
        return "Import %s" % o.import_batch_id[:8]
    if (o.source or "").upper().startswith("FB-CLEAN") or (o.source_system or "") == "facebook_ad":
        return "FB-CLEAN (entered %s)" % (o.created_at.strftime("%b %-d") if o.created_at else "earlier")
    ch = ch or G.channel_of(o.source_system, o.source)
    if ch == "networking":
        return "Networking cards"
    if ch == "referral":
        return "Referrals"
    if ch == "website":
        return "Website requests"
    return G.SOURCE_LABEL.get(ch, "Other") + " (added by hand)"


# ── audience rules ────────────────────────────────────────────────────────────

def eligibility(db: Session, s, o: Opportunity, st, lead, camp: EvoSysCampaign, booked: set,
                camps: dict) -> Optional[str]:
    """Why this contact will not be emailed by this campaign (None = will be)."""
    from app.services import sales_suppression as SDNC
    if st is not None and st.opted_out_at:
        return "opted_out"
    if SDNC.for_opportunity(db, o) or SDNC.find(db, o.brand_sales_org_id, o.phone, o.email):
        return "do_not_contact"
    if st is not None and st.replied_at:
        return "replied"
    if o.id in booked:
        return "booked"
    if o.stage not in O.WORKING_STAGES or o.status != "open":
        return "stage"
    if st is not None and st.campaign_id and st.campaign_id != camp.id and st.status in ("queued", "active"):
        other = camps.get(st.campaign_id)
        return "sending_elsewhere" if (other is not None and other.sending_on and st.status == "active") \
            else "in_other_campaign"
    if not O._has_email(o):
        return "no_email"
    if lead is not None and lead.review_status == "possible_duplicate":
        return "duplicate"
    if lead is not None and lead.review_status in ("held", "excluded"):
        return lead.review_status
    need = {k for x in (camp.steps or []) for k in F.placeholders_in((x.get("subject") or "") + (x.get("body") or ""))}
    facts = F.facts_for(o, s, camp, lead)
    miss = [k for k in F.STRICT if k in need and k not in ("offer", "booking_link") and not facts[k]["value"]]
    if miss:
        return "missing:" + ",".join(miss)
    return None


def _pick(db: Session, c: Ctx, s, pick: dict) -> List[Opportunity]:
    """Leads chosen by id, or by the same filter the Leads screen uses."""
    if pick.get("lead_ids"):
        ids = [str(x) for x in pick["lead_ids"]][:2000]
        rows = _leads_q(db, c).filter(Opportunity.id.in_(ids)).all()
        if len(rows) != len(set(ids)):
            raise HTTPException(404, "Some of those contacts were not found.")
        return rows
    return _filtered(db, c, s, view=pick.get("view") or "all", q=pick.get("q") or "",
                     source=pick.get("source") or "", campaign=pick.get("campaign") or "",
                     batch=pick.get("batch") or "")[0]


def _audience(db: Session, c: Ctx, s, camp: EvoSysCampaign, opps: List[Opportunity]) -> dict:
    camps = _camps(db, s)
    camps.setdefault(camp.id, camp)
    states = _states(db, [o.id for o in opps])
    booked = _booked_ids(db, c.bso)
    rows, will, held, here, other = [], 0, 0, 0, 0
    for o in opps:
        st, lead = states.get(o.id), _lead(db, s, o)
        if st is not None and st.campaign_id == camp.id and st.status in ("queued", "active"):
            here += 1
            rows.append({"id": o.id, "company": o.company_name or o.contact_name, "contact": o.contact_name,
                         "email": o.email, "already_here": True, "will_email": not st.held_reason,
                         "held_label": _held_label(st.held_reason) if st.held_reason else None})
            continue
        why = eligibility(db, s, o, st, lead, camp, booked, camps)
        oc = camps.get(st.campaign_id) if st is not None and st.campaign_id and why in (
            "in_other_campaign", "sending_elsewhere") else None
        if oc is not None:
            other += 1
        if why is None:
            will += 1
        else:
            held += 1
        rows.append({"id": o.id, "company": o.company_name or o.contact_name, "contact": o.contact_name,
                     "email": o.email, "market": G.label_of(G.markets(s), lead.market_key) if lead.market_key else None,
                     "will_email": why is None, "reason": why, "held_label": _held_label(why) if why else None,
                     "other_campaign": oc.name if oc is not None else None})
    return {"total": len(opps), "will_email": will, "held": held, "already_here": here,
            "in_other_campaigns": other, "rows": rows[:500]}


def add_leads(db: Session, c: Ctx, s, camp: EvoSysCampaign, opps: List[Opportunity], move: bool) -> dict:
    """Put picked contacts in this campaign. Opted-out, Do Not Contact,
    replied, booked and closed contacts are refused; a contact being emailed
    by another live campaign is never moved (no overlapping sequences)."""
    if camp.archived_at is not None:
        raise HTTPException(409, "This campaign is archived.")
    camps = _camps(db, s)
    states = _states(db, [o.id for o in opps])
    booked = _booked_ids(db, c.bso)
    now = datetime.utcnow()
    added, moved, already, held, refused = 0, 0, 0, [], []
    for o in opps:
        st, lead = states.get(o.id), _lead(db, s, o)
        if st is not None and st.campaign_id == camp.id and st.status in ("queued", "active"):
            already += 1
            continue
        why = eligibility(db, s, o, st, lead, camp, booked, camps)
        if why in ("opted_out", "do_not_contact", "replied", "booked", "stage", "sending_elsewhere", "excluded") or \
                (why == "in_other_campaign" and not move):
            refused.append({"id": o.id, "company": o.company_name or o.contact_name, "reason": _held_label(why)})
            continue
        hold = why if why and (why.startswith("missing:") or why in ("no_email", "duplicate", "held")) else None
        if st is None:
            st = EvoSysOutreachState(opportunity_id=o.id, brand_sales_org_id=c.bso, started_at=now)
            db.add(st)
        elif st.campaign_id and st.campaign_id != camp.id:
            moved += 1
        st.campaign_id, st.held_reason, st.stop_reason, st.stopped_at = camp.id, hold, None, None
        if camp.sending_on and CA.is_approved(camp, s):
            st.status, st.next_step, st.next_due_at = "active", 0, now
        else:
            st.status, st.next_step, st.next_due_at = "queued", 0, None
        if hold:
            held.append({"id": o.id, "company": o.company_name or o.contact_name, "reason": _held_label(hold)})
        db.add(OpportunityEvent(opportunity_id=o.id, event_type="note", actor_user_id=c.user.id,
                                summary="Added to campaign: %s" % camp.name, occurred_at=now))
        added += 1
    db.flush()
    return {"added": added, "moved": moved, "already_here": already, "held": held, "refused": refused}


def suggest_groups(db: Session, s, opps: List[Opportunity]) -> List[dict]:
    """Groupings worth a campaign of their own, from what is saved: the event
    they were met at, who referred them, or industry + market."""
    groups: Dict[tuple, dict] = {}
    for o in opps:
        lead = _lead(db, s, o)
        ch = lead.source_channel or G.channel_of(o.source_system, o.source)
        if ch == "networking" and o.source_detail:
            key = ("event", o.source_detail.strip().lower())
            g = groups.setdefault(key, {"name": "%s Follow-Up" % o.source_detail.strip(), "kind": "event",
                                        "tone": "warm", "context": o.source_detail.strip(),
                                        "why": "Met at the same event", "lead_ids": []})
        elif ch == "referral":
            key = ("relationship",)
            g = groups.setdefault(key, {"name": "Partner Introductions", "kind": "relationship", "tone": "warm",
                                        "context": None, "why": "Introduced by someone you know", "lead_ids": []})
        else:
            ind = G.label_of(G.industries(s), lead.industry_key) if lead.industry_key else None
            mk = G.label_of(G.markets(s), lead.market_key) if lead.market_key else None
            key = ("list", ind, mk)
            name = " · ".join(x for x in (ind, mk) if x) or "Researched leads"
            g = groups.setdefault(key, {"name": name, "kind": "list", "tone": "researched", "context": None,
                                        "why": "Same industry and market" if ind and mk else
                                        "No relationship yet", "lead_ids": []})
        g["lead_ids"].append(o.id)
    out = sorted(groups.values(), key=lambda g: -len(g["lead_ids"]))
    for g in out:
        g["count"] = len(g["lead_ids"])
    return out


# ── Today ─────────────────────────────────────────────────────────────────────

_PRICE = re.compile(r"\b(price|pricing|cost|costs|how much|per month|monthly|fee|charge)\b|\$\d", re.I)
_CALL = re.compile(r"\b(call me|give me a call|schedule|set up a time|let'?s talk|meet|next week|tomorrow)\b", re.I)
_NO = re.compile(r"\b(not interested|no thanks|no thank you|remove me|unsubscribe|stop emailing)\b", re.I)


def intent(text: Optional[str], label: Optional[str] = None) -> dict:
    if label in ("not_interested", "unsubscribe"):
        return {"key": "not_interested", "label": "Not interested"}
    if label == "pricing":
        return {"key": "pricing", "label": "Pricing question"}
    if label == "interested":
        return {"key": "wants_call", "label": "Interested"}
    t = text or ""
    if _NO.search(t):
        return {"key": "not_interested", "label": "Not interested"}
    if _PRICE.search(t):
        return {"key": "pricing", "label": "Pricing question"}
    if _CALL.search(t):
        return {"key": "wants_call", "label": "Wants a call"}
    return {"key": "other", "label": "Question or other reply"}


def offer_ready(camp: Optional[EvoSysCampaign], s) -> bool:
    """An offer counts only when it is real text on a campaign whose current
    version is approved. Otherwise a pricing question goes to a person."""
    return bool(camp is not None and CA.real_offer(camp.offer) and CA.is_approved(camp, s))


def waiting_replies(db: Session, ids: list) -> List[EvoSysOutreachMessage]:
    """The latest reply from each lead not yet answered (no later email sent,
    call logged or 'answered' note)."""
    if not ids:
        return []
    ins = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id.in_(ids),
                                                  EvoSysOutreachMessage.direction == "in")
           .order_by(EvoSysOutreachMessage.created_at.desc()).limit(400).all())
    if not ins:
        return []
    last_out = dict(db.query(EvoSysOutreachMessage.opportunity_id, func.max(EvoSysOutreachMessage.created_at))
                    .filter(EvoSysOutreachMessage.opportunity_id.in_({m.opportunity_id for m in ins}),
                            EvoSysOutreachMessage.direction == "out",
                            EvoSysOutreachMessage.status.in_(("sent", "logged")))
                    .group_by(EvoSysOutreachMessage.opportunity_id).all())
    seen, out = set(), []
    for m in ins:
        if m.opportunity_id in seen:
            continue
        seen.add(m.opportunity_id)
        lo = last_out.get(m.opportunity_id)
        if lo is None or lo < m.created_at:
            out.append(m)
    return out


@router.get("/today")
def today(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    from app.models.scheduling_models import SalesAppointment
    s = _s(db, c)
    G.backfill(db, s, c.bso)
    camps = _camps(db, s)
    leads = _leads_q(db, c).all()
    by_id = {o.id: o for o in leads}
    ids = list(by_id)
    states = _states(db, ids)
    lrows = {l.opportunity_id: l for l in db.query(EvoSysLead).filter(EvoSysLead.opportunity_id.in_(ids)).all()} \
        if ids else {}
    booked = _booked_ids(db, c.bso)
    now = datetime.utcnow()
    replies = []
    for m in waiting_replies(db, ids)[:25]:
        st = states.get(m.opportunity_id)
        it = intent(m.body, getattr(m, "label", None))
        camp = camps.get(m.campaign_id or (st.campaign_id if st else None))
        replies.append(dict(_msg(m), intent=it, needs_review=it["key"] == "pricing" and not offer_ready(camp, s),
                            lead=_row(s, by_id[m.opportunity_id], st, lrows.get(m.opportunity_id), camps,
                                      m.opportunity_id in booked)))
    to_call = [o for o in leads if o.stage in O.WORKING_STAGES and o.status == "open" and o.id not in booked
               and (not O._has_email(o) or (o.preferred_contact or "") in ("phone", "call"))
               and not (states.get(o.id) and (states[o.id].opted_out_at or states[o.id].status == "stopped"))]
    review = []
    for o in leads:
        l, st = lrows.get(o.id), states.get(o.id)
        if l is not None and l.review_status in ("possible_duplicate", "unclear_market", "held"):
            review.append({"kind": {"possible_duplicate": "duplicate", "unclear_market": "market",
                                    "held": "held"}[l.review_status],
                           "lead_id": o.id, "text": o.company_name or o.contact_name,
                           "label": review_label(l), "detail": l.review_note or l.group_reason or ""})
        elif st is not None and st.held_reason and st.status in ("queued", "active"):
            review.append({"kind": "held", "lead_id": o.id, "text": o.company_name or o.contact_name,
                           "label": "Held in a campaign", "detail": _held_label(st.held_reason)})
    upcoming = (db.query(SalesAppointment).filter(SalesAppointment.brand_sales_org_id == c.bso,
                                                  SalesAppointment.starts_at >= now - timedelta(minutes=30),
                                                  SalesAppointment.status.notin_(("cancelled", "canceled")))
                .order_by(SalesAppointment.starts_at).limit(10).all())
    if not c.manager:
        upcoming = [a for a in upcoming if a.opportunity_id in by_id]
    on = [k for k in camps.values() if k.sending_on and k.archived_at is None]
    suggestion = None
    for r in replies:
        if r["needs_review"]:
            suggestion = {"kind": "pricing", "title": "Pricing question",
                          "text": "%s asked about price and there's no approved offer on file. Answer it yourself."
                                  % (r["lead"]["company"] or "A contact"), "action": "Open conversation",
                          "lead_id": r["lead"]["id"]}
            break
    if suggestion is None:
        for r in replies:
            if r["intent"]["key"] == "wants_call":
                suggestion = {"kind": "call", "title": "Book the call", "action": "Open conversation",
                              "text": "%s wants to talk. Book the intro call while it's fresh."
                                      % (r["lead"]["company"] or "A contact"), "lead_id": r["lead"]["id"]}
                break
    if suggestion is None and review:
        suggestion = {"kind": "review", "title": "Leads need a decision", "action": "Review leads",
                      "text": "%d leads need a decision (duplicates, unclear market or held) before outreach."
                              % len(review), "to": "leads?view=review"}
    if suggestion is None:
        loose = sum(1 for o in leads if o.stage in O.WORKING_STAGES and o.id not in states and O._has_email(o)
                    and (o.contact_role or o.source_detail))
        if loose:
            suggestion = {"kind": "campaign", "title": "Follow up your new contacts", "action": "Pick contacts",
                          "text": "%d contacts you met or were introduced to aren't in a campaign yet." % loose,
                          "to": "leads?view=unassigned"}
    p_by_id = {o.id: o for o in leads}
    out = {"counts": {"replies": len(replies), "to_call": len(to_call),
                      "in_followup": sum(1 for x in states.values() if x.status == "active"),
                      "booked": sum(1 for o in leads if o.id in booked), "leads": len(leads),
                      "needs_review": len(review)},
           "replies": replies,
           "to_call": [_row(s, o, states.get(o.id), lrows.get(o.id), camps) for o in to_call[:25]],
           "needs_review": review[:25],
           "next_call": _appt(upcoming[0], p_by_id) if upcoming else None,
           "suggestion": suggestion,
           "outreach": {"campaigns_on": len(on), "any_on": bool(on)},
           "can_manage": c.manager}
    db.commit()
    return out


def _appt(a, by_id) -> dict:
    o = by_id.get(a.opportunity_id)
    return {"id": a.id, "title": a.title, "status": a.status, "starts_at": iso(a.starts_at),
            "ends_at": iso(a.ends_at), "timezone": a.timezone, "lead_id": a.opportunity_id,
            "company": (o.company_name or o.contact_name) if o else None, "contact": o.contact_name if o else None}


# ── Add contacts ─────────────────────────────────────────────────────────────

class ContactIn(BaseModel):
    contact_name: Optional[str] = None
    company_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    industry: Optional[str] = None
    market: Optional[str] = None
    city: Optional[str] = None
    source: Optional[str] = "networking"
    source_detail: Optional[str] = None
    met_on: Optional[date] = None
    notes: Optional[str] = None
    intro_note: Optional[str] = None
    preferred_contact: Optional[str] = None
    website: Optional[str] = None
    force: bool = False


SOURCE_SYSTEM = {"networking": "networking", "referral": "referral", "website": "website",
                 "facebook": "facebook_ads", "tiktok": "tiktok", "instagram": "instagram", "google": "google",
                 "cold_email": "cold_email_research", "other": "manual"}


def _digits(p: Optional[str]) -> str:
    d = re.sub(r"\D", "", p or "")
    return d[-10:] if len(d) >= 10 else d


def _dupes(db: Session, c: Ctx, email: Optional[str], phone: Optional[str], company: Optional[str]) -> list:
    e, d = (email or "").strip().lower(), _digits(phone)
    core = G.core_name(company) if company else ""
    out = []
    for o in db.query(Opportunity).filter(Opportunity.brand_sales_org_id == c.bso).all():
        if (e and (o.email or "").strip().lower() == e) or (d and len(d) >= 10 and _digits(o.phone) == d) or \
                (core and len(core) >= 4 and G.core_name(o.company_name) == core):
            ch = G.channel_of(o.source_system, o.source)
            out.append({"id": o.id, "company": o.company_name or o.contact_name, "contact": o.contact_name,
                        "email": o.email, "phone": o.phone, "source_label": G.SOURCE_LABEL.get(ch, "Other")})
    return out[:5]


@router.post("/contacts", status_code=201)
def add_contact(body: ContactIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """One contact typed in (a business card, a referral...). Only a name or a
    company is needed. A likely duplicate is shown instead of created unless
    the person says it is a different contact."""
    name, company = (body.contact_name or "").strip(), (body.company_name or "").strip()
    if not name and not company:
        raise HTTPException(400, "Enter a name or a company.")
    email = (body.email or "").strip() or None
    if email and not O._has_email(type("X", (), {"email": email})()):
        raise HTTPException(400, "That email address doesn't look right.")
    if not body.force:
        d = _dupes(db, c, email, body.phone, company)
        if d:
            raise HTTPException(409, {"message": "This looks like a contact you already have.", "duplicates": d})
    src = body.source if body.source in SOURCE_SYSTEM else "other"
    now = datetime.utcnow()
    o = Opportunity(brand_sales_org_id=c.bso, owner_user_id=c.user.id, company_name=company or name,
                    contact_name=name or None, email=email, phone=(body.phone or "").strip() or None,
                    website=(body.website or "").strip() or None, industry=(body.industry or "").strip() or None,
                    source=src.replace("_", " ").title() if src != "cold_email" else "Researched",
                    source_system=SOURCE_SYSTEM[src], stage=STAGE_PROSPECT, status="open",
                    contact_role=(body.role or "").strip()[:80] or None,
                    source_detail=(body.source_detail or "").strip()[:160] or None, met_on=body.met_on,
                    preferred_contact=body.preferred_contact if body.preferred_contact in ("email", "phone", "text")
                    else None, intro_note=(body.intro_note or "").strip()[:1000] or None, created_at=now)
    db.add(o)
    db.flush()
    s = _s(db, c)
    city = (body.city or "").strip() or None
    lead = G.ensure(db, s, o, city=city, state=None,
                    extra_text=" ".join(x for x in (body.market, body.industry) if x))
    lead.source_channel = G.channel_of(o.source_system, o.source)
    db.add(OpportunityEvent(opportunity_id=o.id, event_type="note", actor_user_id=c.user.id,
                            summary="Contact added (%s%s)" % (G.SOURCE_LABEL.get(lead.source_channel, "Other"),
                                                             ": " + o.source_detail if o.source_detail else ""),
                            detail=(body.notes or "").strip()[:4000] or None, occurred_at=now))
    out = _row(s, o, None, lead, _camps(db, s))
    db.commit()
    return {"lead": out}


class ContactPatch(BaseModel):
    contact_name: Optional[str] = None
    company_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    source_detail: Optional[str] = None
    met_on: Optional[date] = None
    intro_note: Optional[str] = None
    preferred_contact: Optional[str] = None


@router.patch("/contacts/{opp_id}")
def edit_contact(opp_id: str, body: ContactPatch, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Fix or add facts. A contact held for a missing fact is checked again,
    so saving the event (say) lets their email go out."""
    o = _opp(db, c, opp_id)
    data = body.model_dump(exclude_unset=True)
    m = {"contact_name": "contact_name", "company_name": "company_name", "email": "email", "phone": "phone",
         "role": "contact_role", "source_detail": "source_detail", "met_on": "met_on", "intro_note": "intro_note",
         "preferred_contact": "preferred_contact"}
    for k, col in m.items():
        if k in data:
            v = data[k]
            v = v.strip() if isinstance(v, str) else v
            if col == "company_name" and not v:
                continue          # a contact always keeps a name to show
            setattr(o, col, v or None)
    if o.email and not O._has_email(o):
        raise HTTPException(400, "That email address doesn't look right.")
    s = _s(db, c)
    st = db.get(EvoSysOutreachState, o.id)
    if st is not None and st.held_reason and st.campaign_id:
        camp = db.get(EvoSysCampaign, st.campaign_id)
        if camp is not None:
            camps = _camps(db, s)
            why = eligibility(db, s, o, None if st.campaign_id == camp.id else st, _lead(db, s, o), camp,
                              _booked_ids(db, c.bso), camps)
            st.held_reason = why if why and (why.startswith("missing:") or why in ("no_email", "duplicate", "held")) \
                else None
    o.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


# ── Leads ─────────────────────────────────────────────────────────────────────

VIEWS = ("all", "unassigned", "followup", "no_email", "replied", "booked", "review", "stopped")


def _filtered(db: Session, c: Ctx, s, *, view: str, q: str, source: str, campaign: str, batch: str):
    camps = _camps(db, s)
    rows = _leads_q(db, c).order_by(Opportunity.created_at.desc()).limit(3000).all()
    ids = [o.id for o in rows]
    states = _states(db, ids)
    lrows = {l.opportunity_id: l for l in db.query(EvoSysLead).filter(EvoSysLead.opportunity_id.in_(ids)).all()} \
        if ids else {}
    booked = _booked_ids(db, c.bso)
    t = (q or "").strip().lower()
    out = []
    for o in rows:
        st, l = states.get(o.id), lrows.get(o.id)
        ch = (l.source_channel if l else None) or G.channel_of(o.source_system, o.source)
        if t and t not in " ".join(x or "" for x in (o.company_name, o.contact_name, o.email, o.phone, o.source_detail,
                                                     l.city if l else None)).lower():
            continue
        if source and ch != source:
            continue
        if campaign == "none" and st is not None and st.campaign_id and st.status in ("queued", "active"):
            continue
        if campaign and campaign != "none" and not (st is not None and st.campaign_id == campaign):
            continue
        if batch and batch_label(o, ch) != batch:
            continue
        inq = st is not None and st.status in ("queued", "active") and st.campaign_id
        if view == "unassigned" and (inq or o.stage not in O.WORKING_STAGES or o.status != "open"):
            continue
        if view == "followup" and not (st is not None and st.status == "active"):
            continue
        if view == "no_email" and O._has_email(o):
            continue
        if view == "replied" and not (st is not None and st.replied_at):
            continue
        if view == "booked" and o.id not in booked:
            continue
        if view == "review" and not ((l is not None and l.review_status in ("possible_duplicate", "unclear_market",
                                                                             "held"))
                                     or (st is not None and st.held_reason)):
            continue
        if view == "stopped" and not (st is not None and st.status == "stopped"):
            continue
        out.append(o)
    return out, states, lrows, camps, booked


@router.get("/contacts")
def list_contacts(view: str = "all", q: str = "", source: str = "", campaign: str = "", batch: str = "",
                  c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    s = _s(db, c)
    G.backfill(db, s, c.bso)
    view = view if view in VIEWS else "all"
    rows, states, lrows, camps, booked = _filtered(db, c, s, view=view, q=q, source=source, campaign=campaign,
                                                   batch=batch)
    total = _leads_q(db, c).count()
    srcs: Dict[str, int] = {}
    for l in db.query(EvoSysLead).filter(EvoSysLead.brand_sales_org_id == c.bso).all():
        srcs[l.source_channel or "other"] = srcs.get(l.source_channel or "other", 0) + 1
    out = {"leads": [_row(s, o, states.get(o.id), lrows.get(o.id), camps, o.id in booked) for o in rows[:500]],
           "shown": len(rows), "total": total, "can_manage": c.manager,
           "sources": [{"key": k, "label": G.SOURCE_LABEL.get(k, "Other"), "count": n}
                       for k, n in sorted(srcs.items(), key=lambda kv: -kv[1])],
           "campaigns": [{"id": k.id, "name": k.name, "kind": k.kind or "group"} for k in camps.values()
                         if k.archived_at is None]}
    db.commit()
    return out


@router.get("/contacts/{opp_id}")
def contact(opp_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    from app.models.scheduling_models import SalesAppointment
    o = _opp(db, c, opp_id)
    s = _s(db, c)
    camps = _camps(db, s)
    lead = _lead(db, s, o)
    st = db.get(EvoSysOutreachState, o.id)
    camp = camps.get(st.campaign_id) if st is not None and st.campaign_id else None
    msgs = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id == o.id)
            .order_by(EvoSysOutreachMessage.created_at).all())
    events = (db.query(OpportunityEvent).filter(OpportunityEvent.opportunity_id == o.id)
              .order_by(OpportunityEvent.occurred_at.desc()).limit(60).all())
    appts = (db.query(SalesAppointment).filter(SalesAppointment.opportunity_id == o.id)
             .order_by(SalesAppointment.starts_at.desc()).limit(10).all())
    nxt = None
    if st is not None and camp is not None and st.status in ("queued", "active") and st.next_step < len(camp.steps or []):
        step = (camp.steps or [])[st.next_step]
        r = O.render_email(o, s, step, camp)
        nxt = {"step": st.next_step + 1, "due_at": iso(st.next_due_at), "campaign_on": bool(camp.sending_on),
               "subject": r["subject"], "body": r["text"], "facts_used": r["facts_used"], "missing": r["missing"]}
    waiting = waiting_replies(db, [o.id])
    names = {k.id: k.name for k in camps.values()}
    out = {"lead": _row(s, o, st, lead, camps, bool(appts)), "intro_note": o.intro_note,
           "group": {"industry": G.label_of(G.industries(s), lead.industry_key) if lead.industry_key else None,
                     "market": G.label_of(G.markets(s), lead.market_key) if lead.market_key else None,
                     "city": lead.city, "reason": lead.group_reason, "review": lead.review_status,
                     "review_label": review_label(lead), "duplicate_of_id": lead.duplicate_of_id},
           "messages": [dict(_msg(m), campaign=names.get(m.campaign_id)) for m in msgs],
           "next": nxt, "reply_waiting": bool(waiting),
           "reply_message_id": waiting[0].id if waiting else None,
           "intent": intent(waiting[0].body, getattr(waiting[0], "label", None)) if waiting else None,
           "appointments": [{"id": a.id, "starts_at": iso(a.starts_at), "status": a.status, "title": a.title}
                            for a in appts],
           "timeline": [{"at": iso(e.occurred_at), "summary": e.summary, "detail": e.detail} for e in events],
           "signed_up": o.stage in SIGNED_UP or bool(o.customer_organization_id),
           "can_manage": c.manager}
    db.commit()
    return out


class NoteIn(BaseModel):
    note: Optional[str] = None


@router.post("/contacts/{opp_id}/handled")
def handled(opp_id: str, body: NoteIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """The reply was answered outside the system (a call, a personal email)."""
    o = _opp(db, c, opp_id)
    st = db.get(EvoSysOutreachState, o.id)
    db.add(EvoSysOutreachMessage(brand_sales_org_id=c.bso, opportunity_id=o.id, direction="out", channel="email",
                                 status="logged", body=(body.note or "Answered outside the system")[:5000],
                                 actor_user_id=c.user.id, campaign_id=st.campaign_id if st else None,
                                 created_at=datetime.utcnow()))
    db.add(OpportunityEvent(opportunity_id=o.id, event_type="note", actor_user_id=c.user.id,
                            summary="Reply answered", detail=body.note, occurred_at=datetime.utcnow()))
    db.commit()
    return {"ok": True}


@router.get("/batches")
def batches(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Every list already in the system, counted, so older batches (the
    Facebook imports, FB-CLEAN, networking cards...) can be reconciled."""
    s = _s(db, c)
    G.backfill(db, s, c.bso)
    rows = _leads_q(db, c).all()
    lrows = {l.opportunity_id: l for l in db.query(EvoSysLead).filter(EvoSysLead.brand_sales_org_id == c.bso).all()}
    states = _states(db, [o.id for o in rows])
    out: Dict[str, dict] = {}
    for o in rows:
        l = lrows.get(o.id)
        ch = (l.source_channel if l else None) or G.channel_of(o.source_system, o.source)
        b = out.setdefault(batch_label(o, ch), {"batch": batch_label(o, ch), "source": G.SOURCE_LABEL.get(ch, "Other"),
                                                "total": 0, "working": 0, "with_email": 0, "in_campaign": 0,
                                                "closed": 0, "first": None, "last": None})
        b["total"] += 1
        if o.stage in O.WORKING_STAGES and o.status == "open":
            b["working"] += 1
        else:
            b["closed"] += 1
        if O._has_email(o):
            b["with_email"] += 1
        st = states.get(o.id)
        if st is not None and st.campaign_id and st.status in ("queued", "active"):
            b["in_campaign"] += 1
        if o.created_at:
            b["first"] = min(b["first"] or o.created_at, o.created_at)
            b["last"] = max(b["last"] or o.created_at, o.created_at)
    res = sorted(out.values(), key=lambda b: -b["total"])
    for b in res:
        b["first"], b["last"] = iso(b["first"]), iso(b["last"])
    db.commit()
    return {"batches": res, "total": len(rows)}


@router.get("/sources")
def sources(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    counts: Dict[str, int] = {}
    for l in db.query(EvoSysLead).filter(EvoSysLead.brand_sales_org_id == c.bso).all():
        counts[l.source_channel or "other"] = counts.get(l.source_channel or "other", 0) + 1
    label = {"connected": "Connected", "import": "Import only", "setup": "Needs setup"}
    how = {"networking": "Add contacts by hand, from a card", "referral": "Add contacts by hand, with who referred them",
           "website": "The evosyspro.live demo-request form adds them by itself"}
    return {"sources": [{"key": k, "label": l, "status": G.SOURCE_ACCESS.get(k, "import"),
                         "status_label": label[G.SOURCE_ACCESS.get(k, "import")],
                         "how": how.get(k, "Export a list and import it"), "count": counts.get(k, 0)}
                        for k, l in G.SOURCE_CHANNELS if k != "other"],
            "import_path": "/imports/sales?target=%s" % c.bso,
            "card_scanning": "Scanning a photo of a business card isn't built yet. Type the card in with Add contacts."}


# ── Campaigns ─────────────────────────────────────────────────────────────────

def _counts(db: Session, camp: EvoSysCampaign) -> dict:
    sts = db.query(EvoSysOutreachState).filter(EvoSysOutreachState.campaign_id == camp.id).all()
    sent = (db.query(func.count(EvoSysOutreachMessage.id))
            .filter(EvoSysOutreachMessage.campaign_id == camp.id, EvoSysOutreachMessage.direction == "out",
                    EvoSysOutreachMessage.status == "sent").scalar() or 0)
    return {"audience": len(sts), "in_followup": sum(1 for x in sts if x.status == "active" and not x.held_reason),
            "waiting": sum(1 for x in sts if x.status == "queued" and not x.held_reason),
            "held": sum(1 for x in sts if x.status in ("queued", "active") and x.held_reason),
            "replied": sum(1 for x in sts if x.replied_at), "finished": sum(1 for x in sts if x.status == "finished"),
            "sent": sent}


def _cstatus(camp: EvoSysCampaign, s) -> dict:
    if camp.archived_at is not None:
        return {"key": "archived", "label": "Archived"}
    ok = CA.is_approved(camp, s)
    if camp.sending_on and ok:
        return {"key": "on", "label": "On"}
    if ok:
        return {"key": "approved", "label": "Approved - off"}
    if camp.approved_version:
        return {"key": "needs_approval", "label": "Changed - needs approval"}
    return {"key": "draft", "label": "Off - not approved"}


def _camp_card(db: Session, s, camp: EvoSysCampaign) -> dict:
    kind = camp.kind or "group"
    nxt = (db.query(func.min(EvoSysOutreachState.next_due_at))
           .filter(EvoSysOutreachState.campaign_id == camp.id, EvoSysOutreachState.status == "active").scalar())
    return {"id": camp.id, "name": camp.name, "kind": kind, "kind_label": KIND_LABELS.get(kind, kind),
            "picked": kind in PICKED_KINDS, "is_default": bool(camp.is_default), "context": camp.context,
            "tone": camp.tone, "group": G.group_label(s, camp.industry_key, camp.market_key)
            if kind == "group" and not camp.is_default else ("All leads not in another campaign"
                                                             if camp.is_default else None),
            "status": _cstatus(camp, s), "sending_on": bool(camp.sending_on), "approved": CA.is_approved(camp, s),
            "version": camp.version, "approved_version": camp.approved_version, "problems": CA.problems(camp, s),
            "counts": _counts(db, camp), "next_send_at": iso(nxt), "hours": _hours_text(camp),
            "archived": camp.archived_at is not None}


@router.get("/campaign-list")
def campaign_list(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    s = _s(db, c)
    camps = list(_camps(db, s).values())
    out = {"campaigns": [_camp_card(db, s, k) for k in camps if k.archived_at is None],
           "archived": [_camp_card(db, s, k) for k in camps if k.archived_at is not None],
           "team_problems": O.settings_problems(s) if hasattr(O, "settings_problems") else [],
           "can_manage": c.manager}
    out["team_problems"] = [p for p in out["team_problems"]
                            if not p.startswith(("Write the offer", "Add the booking link", "Add at least one step",
                                                 "Step "))]
    db.commit()
    return out


class PickIn(BaseModel):
    lead_ids: Optional[List[str]] = None
    view: Optional[str] = None
    q: Optional[str] = None
    source: Optional[str] = None
    campaign: Optional[str] = None
    batch: Optional[str] = None


@router.post("/campaigns/suggest")
def suggest(body: PickIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    s = _s(db, c)
    opps = _pick(db, c, s, body.model_dump())
    out = {"groups": suggest_groups(db, s, opps), "total": len(opps)}
    db.commit()
    return out


class PreviewNewIn(BaseModel):
    kind: str = "list"
    tone: Optional[str] = None
    pick: PickIn


def _draft(c: Ctx, s, kind: str, tone: Optional[str], name: str = "Draft") -> EvoSysCampaign:
    kind = kind if kind in PICKED_KINDS else "list"
    tone = tone if tone in ("warm", "researched") else ("warm" if kind in ("event", "relationship") else "researched")
    return EvoSysCampaign(id="draft", brand_sales_org_id=c.bso, name=name, kind=kind, tone=tone,
                          steps=default_steps(kind, tone), sending_on=False, version=1)


@router.post("/campaigns/audience/preview")
def preview_new(body: PreviewNewIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Who a new campaign would email, before it exists. Writes nothing."""
    s = _s(db, c)
    opps = _pick(db, c, s, body.pick.model_dump())
    out = _audience(db, c, s, _draft(c, s, body.kind, body.tone), opps)
    db.commit()
    return out


class PickedIn(BaseModel):
    name: str
    kind: str = "list"
    context: Optional[str] = None
    tone: Optional[str] = None
    pick: Optional[PickIn] = None
    move: bool = False


@router.post("/campaigns/picked", status_code=201)
def create_picked(body: PickedIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """A campaign built around the actual work (an event, a referral, a
    project...) from picked leads. It starts Off and unapproved, with the
    default campaign's schedule and limit and warm or researched messages."""
    _manager(c)
    s = _s(db, c)
    if not (body.name or "").strip():
        raise HTTPException(422, "Name the campaign.")
    base = CA.ensure_default(db, s)
    d = _draft(c, s, body.kind, body.tone, body.name.strip()[:200])
    camp = EvoSysCampaign(brand_sales_org_id=c.bso, name=d.name, kind=d.kind, tone=d.tone,
                          context=(body.context or "").strip()[:200] or None, is_default=False,
                          industry_key=None, market_key=None, steps=d.steps, offer=None,
                          booking_url=base.booking_url or s.booking_url, daily_limit=base.daily_limit,
                          send_days=base.send_days, send_start_hour=base.send_start_hour,
                          send_end_hour=base.send_end_hour, timezone=base.timezone, sending_on=False,
                          draft_origin="manual", version=1, updated_by=c.user.id)
    db.add(camp)
    db.flush()
    added = None
    if body.pick is not None:
        added = add_leads(db, c, s, camp, _pick(db, c, s, body.pick.model_dump()), body.move)
    out = {"campaign": _camp_card(db, s, camp), "added": added}
    db.commit()
    return out


@router.get("/campaigns/{cid}/members")
def members(cid: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    s = _s(db, c)
    camp = db.get(EvoSysCampaign, cid)
    if camp is None or camp.brand_sales_org_id != c.bso:
        raise HTTPException(404, "Campaign not found.")
    camps = _camps(db, s)
    sts = (db.query(EvoSysOutreachState).filter(EvoSysOutreachState.campaign_id == camp.id)
           .order_by(EvoSysOutreachState.started_at.desc()).limit(500).all())
    opps = {o.id: o for o in _leads_q(db, c).filter(Opportunity.id.in_([x.opportunity_id for x in sts])).all()} \
        if sts else {}
    out = dict(_camp_card(db, s, camp),
               audience=[dict(_row(s, opps[x.opportunity_id], x, db.get(EvoSysLead, x.opportunity_id), camps))
                         for x in sts if x.opportunity_id in opps],
               placeholders=list(F.PLACEHOLDERS), can_manage=c.manager, my_email=c.user.email)
    db.commit()
    return out


@router.get("/campaigns/{cid}/examples")
def examples(cid: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """The messages filled in for up to three real contacts in this campaign,
    with the facts each one used and anything missing."""
    s = _s(db, c)
    camp = db.get(EvoSysCampaign, cid)
    if camp is None or camp.brand_sales_org_id != c.bso:
        raise HTTPException(404, "Campaign not found.")
    ids = [x.opportunity_id for x in db.query(EvoSysOutreachState).filter(EvoSysOutreachState.campaign_id == camp.id)
           .limit(50).all()]
    if not ids and (camp.kind or "group") == "group":
        for l in db.query(EvoSysLead).filter(EvoSysLead.brand_sales_org_id == c.bso).all():
            k = CA.campaign_for(db, s, l)
            if k is not None and k.id == camp.id:
                ids.append(l.opportunity_id)
            if len(ids) >= 20:
                break
    opps = _leads_q(db, c).filter(Opportunity.id.in_(ids)).all() if ids else []
    opps.sort(key=lambda o: (not O._has_email(o), o.created_at or datetime.min))
    exs = []
    for o in opps[:3]:
        steps = []
        for st in camp.steps or []:
            r = O.render_email(o, s, st, camp)
            steps.append({"day": st.get("day"), "channel": st.get("channel"), "subject": r["subject"], "text": r["text"],
                          "facts_used": r["facts_used"], "missing": r["missing"]})
        exs.append({"lead": {"id": o.id, "company": o.company_name or o.contact_name, "email": o.email},
                    "steps": steps})
    db.commit()
    return {"examples": exs}


@router.post("/campaigns/{cid}/audience/preview")
def preview_existing(cid: str, body: PickIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    s = _s(db, c)
    camp = db.get(EvoSysCampaign, cid)
    if camp is None or camp.brand_sales_org_id != c.bso:
        raise HTTPException(404, "Campaign not found.")
    out = _audience(db, c, s, camp, _pick(db, c, s, body.model_dump()))
    db.commit()
    return out


class AddIn(PickIn):
    move: bool = False


@router.post("/campaigns/{cid}/leads")
def add_to_campaign(cid: str, body: AddIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    s = _s(db, c)
    camp = db.get(EvoSysCampaign, cid)
    if camp is None or camp.brand_sales_org_id != c.bso:
        raise HTTPException(404, "Campaign not found.")
    if (camp.kind or "group") == "group":
        raise HTTPException(409, "This campaign reaches its industry + market group by itself. "
                                 "Pick leads into an event, referral, project or list campaign instead.")
    out = add_leads(db, c, s, camp, _pick(db, c, s, body.model_dump(exclude={"move"})), body.move)
    db.commit()
    return out


@router.delete("/campaigns/{cid}/leads/{opp_id}")
def remove_from_campaign(cid: str, opp_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    o = _opp(db, c, opp_id)
    st = db.get(EvoSysOutreachState, o.id)
    if st is None or st.campaign_id != cid:
        raise HTTPException(404, "That contact isn't in this campaign.")
    if st.status in ("queued", "active"):
        st.status, st.stop_reason, st.stopped_at, st.next_due_at = "stopped", "manual", datetime.utcnow(), None
    st.held_reason = None
    db.add(OpportunityEvent(opportunity_id=o.id, event_type="note", actor_user_id=c.user.id,
                            summary="Removed from a campaign", occurred_at=datetime.utcnow()))
    db.commit()
    return {"ok": True}


@router.post("/campaigns/{cid}/archive")
def archive(cid: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Turn the campaign off for good. Its contacts stop getting its emails;
    their history stays."""
    _manager(c)
    camp = db.get(EvoSysCampaign, cid)
    if camp is None or camp.brand_sales_org_id != c.bso:
        raise HTTPException(404, "Campaign not found.")
    if camp.is_default:
        raise HTTPException(409, "The default campaign can be turned off, not archived.")
    camp.sending_on, camp.archived_at, camp.updated_by = False, datetime.utcnow(), c.user.id
    for st in db.query(EvoSysOutreachState).filter(EvoSysOutreachState.campaign_id == camp.id,
                                                   EvoSysOutreachState.status.in_(("queued", "active"))).all():
        st.status, st.stop_reason, st.stopped_at, st.next_due_at = "stopped", "manual", datetime.utcnow(), None
    db.commit()
    return {"ok": True}


# ── Conversations and appointments ───────────────────────────────────────────

@router.get("/inbox")
def inbox(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    s = _s(db, c)
    camps = _camps(db, s)
    leads = {o.id: o for o in _leads_q(db, c).all()}
    ids = list(leads)
    if not ids:
        return {"conversations": []}
    last = dict(db.query(EvoSysOutreachMessage.opportunity_id, func.max(EvoSysOutreachMessage.created_at))
                .filter(EvoSysOutreachMessage.opportunity_id.in_(ids)).group_by(EvoSysOutreachMessage.opportunity_id)
                .all())
    counts = dict(db.query(EvoSysOutreachMessage.opportunity_id, func.count(EvoSysOutreachMessage.id))
                  .filter(EvoSysOutreachMessage.opportunity_id.in_(list(last))).group_by(
                      EvoSysOutreachMessage.opportunity_id).all()) if last else {}
    waiting = {m.opportunity_id: m for m in waiting_replies(db, list(last))}
    states = _states(db, list(last))
    out = []
    for oid, at in last.items():
        o, m = leads[oid], waiting.get(oid)
        st = states.get(oid)
        camp = camps.get(m.campaign_id or (st.campaign_id if st else None)) if m else None
        it = intent(m.body, getattr(m, "label", None)) if m else None
        lm = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id == oid)
              .order_by(EvoSysOutreachMessage.created_at.desc()).first())
        out.append({"id": oid, "company": o.company_name or o.contact_name, "contact": o.contact_name,
                    "phone": o.phone, "waiting": m is not None, "reply": _msg(m) if m else None,
                    "reply_message_id": m.id if m else None, "intent": it,
                    "needs_review": bool(it and it["key"] == "pricing" and not offer_ready(camp, s)),
                    "following_up": bool(st is not None and st.status == "active"),
                    "last_message": _msg(lm) if lm else None, "message_count": counts.get(oid, 0),
                    "last_at": iso(at)})
    out.sort(key=lambda r: (not r["waiting"], r["last_at"] or ""), reverse=False)
    out = [r for r in out if r["waiting"]] + sorted([r for r in out if not r["waiting"]],
                                                    key=lambda r: r["last_at"] or "", reverse=True)
    db.commit()
    return {"conversations": out[:200]}


@router.get("/calls")
def calls(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    from app.models.scheduling_models import SalesAppointment
    leads = {o.id: o for o in _leads_q(db, c).all()}
    now = datetime.utcnow()
    q = db.query(SalesAppointment).filter(SalesAppointment.brand_sales_org_id == c.bso)
    rows = q.filter(SalesAppointment.starts_at >= now - timedelta(days=30)).order_by(SalesAppointment.starts_at).all()
    if not c.manager:
        rows = [a for a in rows if a.opportunity_id in leads]
    up = [a for a in rows if a.starts_at >= now - timedelta(minutes=30) and a.status not in ("cancelled", "canceled")]
    past = [a for a in rows if a.starts_at < now - timedelta(minutes=30)]
    problems = []
    for a in past:
        if a.status == "scheduled":
            problems.append(dict(_appt(a, leads), problem="Past and not marked done or no-show"))
    for a in up:
        if a.opportunity_id and a.opportunity_id not in leads and c.manager:
            problems.append(dict(_appt(a, leads), problem="Not linked to a lead"))
    return {"upcoming": [_appt(a, leads) for a in up], "recent": [_appt(a, leads) for a in reversed(past[-15:])],
            "problems": problems}


# ── Markets outreach may reach (configurable) ────────────────────────────────

class MarketIn(BaseModel):
    label: str
    cities: Optional[str] = None
    aliases: Optional[str] = None
    state: Optional[str] = None


class MarketsIn(BaseModel):
    markets: List[MarketIn]


@router.get("/markets")
def get_markets(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    s = _s(db, c)
    db.commit()
    return {"markets": [{"key": m["key"], "label": m["label"], "state": m.get("state"),
                         "cities": ", ".join(x.title() for x in m.get("cities") or []),
                         "aliases": ", ".join(m.get("aliases") or [])} for m in G.markets(s)],
            "custom": bool(getattr(s, "markets", None)), "can_manage": c.manager}


@router.put("/markets")
def put_markets(body: MarketsIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Which markets leads are grouped into. A lead whose city or text names
    none of them (or more than one) is 'unclear market' and waits for a
    person; market campaigns only reach their own market. Saved markets
    replace the defaults; people's own group choices are kept."""
    _manager(c)
    s = _s(db, c)
    out, seen = [], set()
    for m in body.markets[:30]:
        label = (m.label or "").strip()[:60]
        if not label:
            continue
        key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40]
        if key in seen:
            raise HTTPException(422, "Two markets are both called %s." % label)
        seen.add(key)
        out.append({"key": key, "label": label, "state": (m.state or "").strip().upper()[:2] or None,
                    "cities": [x.strip().lower() for x in (m.cities or "").split(",") if x.strip()][:200],
                    "aliases": [x.strip().lower() for x in (m.aliases or "").split(",") if x.strip()][:20]})
    if not out:
        raise HTTPException(422, "Keep at least one market.")
    s.markets = out
    s.updated_at, s.updated_by = datetime.utcnow(), c.user.id
    # Re-group by rule (manual and AI-accepted choices are kept by ensure()).
    for o in db.query(Opportunity).filter(Opportunity.brand_sales_org_id == c.bso, Opportunity.status == "open").all():
        G.ensure(db, s, o)
    db.commit()
    return get_markets(c, db)
