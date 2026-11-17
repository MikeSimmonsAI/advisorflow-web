"""EvoSys Business Development API (PR #663; first built as "EvoSys Sales").

How EvoSys reaches out to the people and businesses it wants to work with:
networking cards, referrals, inbound requests, researched lists. Contacts are
the existing `opportunities` rows of the EvoSys Pro sales team; campaigns,
follow-up state and the message log are evosys_* tables.

/sales/evosys/*        the EvoSys Pro sales team only (normal login and team
                       membership; no God Mode needed). Managers see every
                       lead, reps their own. Campaign messages, approval and
                       switches are managers only.
/evosys-sales-public/* the opt-out link in every email. No login; the token
                       names one lead and cannot be forged.

The routes keep their original /sales/evosys paths so saved links and the API
contract stay compatible.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.evosys_sales_models import EvoSysCampaign, EvoSysOutreachMessage, EvoSysOutreachState
from app.models.models import User
from app.models.sales_models import STAGE_LABELS, STAGE_PROSPECT, Opportunity, OpportunityEvent
from app.services import sales_access
from app.services.evosys_sales import campaigns as C
from app.services.evosys_sales import outreach as O

router = APIRouter(prefix="/sales/evosys", tags=["evosys-business-development"])
public_router = APIRouter(prefix="/evosys-sales-public", tags=["evosys-sales-public"])

PRODUCT = "EvoSys Business Development"
STOP_LABELS = {"replied": "Replied", "booked": "Booked a call", "opted_out": "Opted out",
               "no_email": "No email address", "stage": "Moved on in the pipeline",
               "manual": "Stopped by you", "email_failed": "Email bounced"}
SIGNED_UP = ("won", "onboarding", "live")
DEMO_STAGES = ("demo_build", "demo_proposal", "closing")


class Ctx:
    def __init__(self, user, bso, manager):
        self.user, self.bso, self.manager = user, bso, manager


def ctx(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Ctx:
    bso = O.brand_id(db)
    if not bso or not sales_access.is_sales_member(user, db, bso):
        raise HTTPException(403, "%s is for the EvoSys Pro platform team." % PRODUCT)
    return Ctx(user, bso, sales_access.is_sales_manager(user, db, bso))


def _manager(c: Ctx):
    if not c.manager:
        raise HTTPException(403, "Only an EvoSys manager can change campaigns and outreach.")


def iso(d):
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.isoformat() + "Z"
    return d.isoformat()


def _leads_q(db: Session, c: Ctx):
    q = db.query(Opportunity).filter(Opportunity.brand_sales_org_id == c.bso)
    if not c.manager:
        q = q.filter(Opportunity.owner_user_id == c.user.id)
    return q


def _opp(db: Session, c: Ctx, opp_id: str) -> Opportunity:
    o = _leads_q(db, c).filter(Opportunity.id == opp_id).first()
    if o is None:
        raise HTTPException(404, "Lead not found.")
    return o


def _camp(db: Session, c: Ctx, camp_id: str) -> EvoSysCampaign:
    k = db.get(EvoSysCampaign, camp_id)
    if k is None or k.brand_sales_org_id != c.bso:
        raise HTTPException(404, "Campaign not found.")
    return k


def _status(st: Optional[EvoSysOutreachState], opp: Opportunity) -> dict:
    if st is None or (st.campaign_id is None and st.status in ("queued", "active")):
        if opp.stage not in O.WORKING_STAGES:
            return {"key": "not_in", "label": "Not in outreach"}
        if not O._has_email(opp):
            return {"key": "no_email", "label": "No email - call"}
        return {"key": "none", "label": "Not in a campaign"}
    if st.status == "queued":
        if st.held_reason:
            return {"key": "held", "label": C.held_label(st.held_reason), "reason": st.held_reason}
        return {"key": "queued", "label": "In campaign - waiting for it to be on"}
    if st.status == "active":
        return {"key": "active", "label": "In follow-up - message %d next" % (st.next_step + 1),
                "next_due_at": iso(st.next_due_at)}
    if st.status == "finished":
        return {"key": "finished", "label": "All messages sent"}
    return {"key": st.stop_reason or "stopped", "label": STOP_LABELS.get(st.stop_reason, "Stopped")}


def _camp_names(db: Session, bso: str) -> dict:
    return {k.id: k.name for k in db.query(EvoSysCampaign.id, EvoSysCampaign.name)
            .filter(EvoSysCampaign.brand_sales_org_id == bso).all()}


def _row(o: Opportunity, st, last=None, booked=False, names=None) -> dict:
    sk = C.source_key(o)
    camp_id = st.campaign_id if st is not None else None
    return {"id": o.id, "company": o.company_name, "contact": o.contact_name, "role": o.contact_role,
            "phone": o.phone, "email": o.email, "website": o.website, "industry": o.industry,
            "market": o.market, "stage": o.stage, "stage_label": STAGE_LABELS.get(o.stage, o.stage),
            "source": o.source, "source_key": sk, "source_label": C.SOURCE_LABELS.get(sk, "Other"),
            "source_detail": o.source_detail, "met_on": iso(o.met_on),
            "preferred_contact": o.preferred_contact, "batch": C.batch_label(o),
            "campaign_id": camp_id, "campaign": (names or {}).get(camp_id) if camp_id else None,
            "created_at": iso(o.created_at), "outreach": _status(st, o), "booked": booked,
            "last_message": last}


def _msg(m: EvoSysOutreachMessage, names=None) -> dict:
    return {"id": m.id, "direction": m.direction, "channel": m.channel, "step": m.step_no,
            "to": m.to_address, "subject": m.subject, "body": m.body, "status": m.status,
            "error": m.error, "how": m.match_method, "at": iso(m.created_at),
            "campaign_id": m.campaign_id, "campaign": (names or {}).get(m.campaign_id)}


def _booked_ids(db: Session, bso: str) -> set:
    from app.models.scheduling_models import SalesAppointment
    return {r[0] for r in db.query(SalesAppointment.opportunity_id)
            .filter(SalesAppointment.brand_sales_org_id == bso,
                    SalesAppointment.opportunity_id.isnot(None)).all()}


def _states(db: Session, ids) -> dict:
    if not ids:
        return {}
    return {x.opportunity_id: x for x in db.query(EvoSysOutreachState)
            .filter(EvoSysOutreachState.opportunity_id.in_(list(ids))).all()}


def _prep(db: Session, c: Ctx):
    """Bring older follow-up rows into campaigns before anything is shown."""
    C.ensure_legacy(db, c.bso)
    C.migrate_messages(db, c.bso)


# ── reply intent (no AI needed to sort the queue) ─────────────────────────────

_PRICE = re.compile(r"\b(price|pricing|cost|costs|how much|per month|monthly|fee|charge|\$\d)", re.I)
_CALL = re.compile(r"\b(call me|give me a call|phone|schedule|set up a time|available|thursday|friday|monday|"
                    r"tuesday|wednesday|tomorrow|next week|let'?s talk|meet)\b", re.I)
_NO = re.compile(r"\b(not interested|no thanks|no thank you|remove me|unsubscribe|stop emailing|don'?t contact)\b", re.I)


def intent(text: Optional[str]) -> dict:
    t = text or ""
    if _NO.search(t):
        return {"key": "not_interested", "label": "Not interested"}
    if _PRICE.search(t):
        return {"key": "pricing", "label": "Pricing question"}
    if _CALL.search(t):
        return {"key": "wants_call", "label": "Wants a call"}
    return {"key": "other", "label": "Question or other reply"}


def _offer_ready(camp: Optional[EvoSysCampaign], s) -> bool:
    """An offer counts as approved only when it is real text on a campaign
    whose current version is approved."""
    return bool(camp is not None and C.real_offer(camp.offer) and C.is_approved(camp, s))


def _waiting_replies(db: Session, ids: list, names: dict) -> List[EvoSysOutreachMessage]:
    """Inbound replies not yet answered: no later outbound email, call or
    'handled' note for that lead."""
    if not ids:
        return []
    ins = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id.in_(ids),
                                                  EvoSysOutreachMessage.direction == "in")
           .order_by(EvoSysOutreachMessage.created_at.desc()).limit(300).all())
    if not ins:
        return []
    last_out = dict(db.query(EvoSysOutreachMessage.opportunity_id, func.max(EvoSysOutreachMessage.created_at))
                    .filter(EvoSysOutreachMessage.opportunity_id.in_([m.opportunity_id for m in ins]),
                            EvoSysOutreachMessage.direction == "out",
                            EvoSysOutreachMessage.status.in_(("sent", "logged")))
                    .group_by(EvoSysOutreachMessage.opportunity_id).all())
    seen, out = set(), []
    for m in ins:
        if m.opportunity_id in seen:
            continue
        seen.add(m.opportunity_id)
        lo = last_out.get(m.opportunity_id)
        if lo is None or lo < m.created_at or (m.match_method == "manual" and lo <= m.created_at):
            out.append(m)
    return out


# ── Today ─────────────────────────────────────────────────────────────────────

@router.get("/overview")
def overview(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _prep(db, c)
    s = O.get_settings(db, c.bso)
    db.commit()
    leads = _leads_q(db, c).all()
    ids = [o.id for o in leads]
    by_id = {o.id: o for o in leads}
    states = _states(db, ids)
    names = _camp_names(db, c.bso)
    camps = {k.id: k for k in db.query(EvoSysCampaign).filter(EvoSysCampaign.brand_sales_org_id == c.bso).all()}
    booked = _booked_ids(db, c.bso)
    now = datetime.utcnow()
    waiting = _waiting_replies(db, ids, names)
    replies = []
    for m in waiting[:25]:
        st = states.get(m.opportunity_id)
        it = intent(m.body)
        camp = camps.get(m.campaign_id or (st.campaign_id if st else None))
        needs_review = it["key"] == "pricing" and not _offer_ready(camp, s)
        replies.append(dict(_msg(m, names), intent=it, needs_review=needs_review,
                            lead=_row(by_id[m.opportunity_id], st, booked=m.opportunity_id in booked, names=names)))
    working = [o for o in leads if o.stage in O.WORKING_STAGES]
    to_call = [o for o in working if (not O._has_email(o) or (o.preferred_contact or "") in ("phone", "call"))
               and o.id not in booked and not (states.get(o.id) and (states[o.id].opted_out_at
                                                                    or states[o.id].status == "stopped"))]
    review = _review_items(db, c, leads, states, names)
    from app.models.scheduling_models import SalesAppointment
    upcoming = (db.query(SalesAppointment).filter(SalesAppointment.brand_sales_org_id == c.bso,
                                                  SalesAppointment.starts_at >= now - timedelta(minutes=30),
                                                  SalesAppointment.status.notin_(("cancelled", "canceled")))
                .order_by(SalesAppointment.starts_at).limit(10).all())
    if not c.manager:
        upcoming = [a for a in upcoming if a.opportunity_id in by_id]
    on = [k for k in camps.values() if k.sending_on and not k.archived_at]
    in_followup = sum(1 for x in states.values() if x.status == "active")
    suggestion = _suggestion(replies, review, to_call, camps, s)
    return {
        "counts": {"replies": len(waiting), "to_call": len(to_call), "in_followup": in_followup,
                   "booked": sum(1 for o in leads if o.id in booked), "leads": len(leads),
                   "needs_review": len(review), "sent_today": O.sent_today(db, c.bso, now)},
        "replies": replies,
        "to_call": [_row(o, states.get(o.id), names=names) for o in to_call[:25]],
        "needs_review": review[:25],
        "appointments": [_appt(a, by_id) for a in upcoming],
        "next_call": _appt(upcoming[0], by_id) if upcoming else None,
        "suggestion": suggestion,
        "outreach": {"campaigns_on": len(on), "any_on": bool(on), "in_window": O.in_send_window(now),
                     "team_problems": O.settings_problems(s)},
        "can_manage": c.manager,
    }


def _suggestion(replies, review, to_call, camps, s) -> Optional[dict]:
    """One useful next step, worked out from the data (and saying so)."""
    for r in replies:
        if r["needs_review"]:
            return {"kind": "pricing", "title": "Pricing question",
                    "text": "%s asked about price and there's no approved offer on file. Answer it yourself."
                            % (r["lead"]["company"] or "A contact"),
                    "action": "Open conversation", "lead_id": r["lead"]["id"]}
    for r in replies:
        if r["intent"]["key"] == "wants_call":
            return {"kind": "call", "title": "Book the call",
                    "text": "%s wants to talk. Book the intro call while it's fresh." % (r["lead"]["company"] or "A contact"),
                    "action": "Open conversation", "lead_id": r["lead"]["id"]}
    dup = [x for x in review if x["kind"] == "duplicate"]
    if dup:
        return {"kind": "review", "title": "Possible duplicates",
                "text": "%d contacts may be the same person or business. Review them before outreach." % len(dup),
                "action": "Review leads", "to": "leads?view=review"}
    draft = [k for k in camps.values() if not k.sending_on and not k.archived_at and not k.approved_at]
    if draft:
        return {"kind": "campaign", "title": "Finish a campaign",
                "text": "\"%s\" is built but not approved yet." % draft[0].name,
                "action": "Open campaign", "campaign_id": draft[0].id}
    if to_call:
        return {"kind": "calls", "title": "Make some calls",
                "text": "%d contacts have no email. A call is the only way to reach them." % len(to_call),
                "action": "Start calling", "to": "leads?view=no_email"}
    return None


def _appt(a, by_id) -> dict:
    o = by_id.get(a.opportunity_id)
    return {"id": a.id, "title": a.title, "status": a.status,
            "starts_at": iso(a.starts_at), "ends_at": iso(a.ends_at),
            "timezone": a.timezone, "lead_id": a.opportunity_id,
            "company": o.company_name if o else None, "contact": o.contact_name if o else None,
            "confirmation_status": getattr(a, "confirmation_status", None)}


# ── review: duplicates, held contacts, unclear markets ───────────────────────

def _norm_phone(p):
    d = re.sub(r"\D", "", p or "")
    return d[-10:] if len(d) >= 10 else None


def _review_items(db: Session, c: Ctx, leads, states, names) -> List[dict]:
    out = []
    by_email, by_phone = {}, {}
    for o in leads:
        if o.stage not in O.WORKING_STAGES:
            continue
        e = (o.email or "").strip().lower()
        if e:
            by_email.setdefault(e, []).append(o)
        p = _norm_phone(o.phone)
        if p:
            by_phone.setdefault(p, []).append(o)
    seen = set()
    for key, group in list(by_email.items()) + list(by_phone.items()):
        if len(group) < 2:
            continue
        sig = tuple(sorted(o.id for o in group))
        if sig in seen:
            continue
        seen.add(sig)
        out.append({"kind": "duplicate", "label": "Possible duplicate",
                    "text": " / ".join(sorted({o.company_name or "?" for o in group})),
                    "detail": "Same %s: %s" % ("email" if "@" in key else "phone", key),
                    "lead_ids": [o.id for o in group]})
    for oid, st in states.items():
        if st.status == "queued" and st.held_reason and st.held_reason != "no_email":
            o = next((x for x in leads if x.id == oid), None)
            if o is None:
                continue
            out.append({"kind": "held", "label": C.held_label(st.held_reason),
                        "text": o.company_name, "detail": "Campaign: %s" % names.get(st.campaign_id, "-"),
                        "lead_ids": [oid], "campaign_id": st.campaign_id})
    return out


@router.get("/review")
def review(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _prep(db, c)
    db.commit()
    leads = _leads_q(db, c).all()
    states = _states(db, [o.id for o in leads])
    names = _camp_names(db, c.bso)
    by_id = {o.id: o for o in leads}
    items = _review_items(db, c, leads, states, names)
    for it in items:
        it["leads"] = [_row(by_id[i], states.get(i), names=names) for i in it["lead_ids"] if i in by_id]
    return {"items": items}


# ── contacts ──────────────────────────────────────────────────────────────────

class ContactIn(BaseModel):
    contact_name: Optional[str] = None
    company_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    industry: Optional[str] = None
    market: Optional[str] = None
    source: Optional[str] = "networking"
    source_detail: Optional[str] = None      # the event or the referrer
    met_on: Optional[date] = None
    notes: Optional[str] = None
    intro_note: Optional[str] = None
    preferred_contact: Optional[str] = None
    website: Optional[str] = None
    force: bool = False


def _clean(v, n=200):
    v = (v or "").strip()
    return v[:n] or None


def _find_dupes(db: Session, bso: str, email: Optional[str], phone: Optional[str], company: Optional[str],
                contact: Optional[str], exclude: Optional[str] = None) -> List[Opportunity]:
    q = db.query(Opportunity).filter(Opportunity.brand_sales_org_id == bso)
    if exclude:
        q = q.filter(Opportunity.id != exclude)
    hits = []
    e = (email or "").strip().lower()
    if e:
        hits += q.filter(func.lower(func.trim(Opportunity.email)) == e).limit(5).all()
    p = _norm_phone(phone)
    if p:
        for o in q.filter(Opportunity.phone.ilike("%" + p[-4:] + "%")).limit(200).all():
            if _norm_phone(o.phone) == p:
                hits.append(o)
    if company and contact:
        hits += (q.filter(func.lower(Opportunity.company_name) == company.strip().lower(),
                          func.lower(Opportunity.contact_name) == contact.strip().lower()).limit(5).all())
    uniq = {}
    for h in hits:
        uniq[h.id] = h
    return list(uniq.values())


@router.post("/contacts", status_code=201)
def add_contact(body: ContactIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Quick add, e.g. from a business card after a networking event. Saves on
    the existing sales record so the contact shows up everywhere at once."""
    person = _clean(body.contact_name, 120)
    company = _clean(body.company_name, 160)
    if not person and not company:
        raise HTTPException(400, "Enter a name or a company.")
    email = _clean(body.email, 200)
    if email and not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(400, "That email address doesn't look right.")
    src = body.source if body.source in C.SOURCE_LABELS else "other"
    dupes = _find_dupes(db, c.bso, email, body.phone, company, person)
    if dupes and not body.force:
        names = _camp_names(db, c.bso)
        states = _states(db, [d.id for d in dupes])
        return JSONResponse(status_code=409, content={"detail": {
            "message": "This looks like a contact you already have.",
            "duplicates": [_row(d, states.get(d.id), names=names) for d in dupes]}})
    now = datetime.utcnow()
    detail = _clean(body.source_detail, 160)
    o = Opportunity(brand_sales_org_id=c.bso, owner_user_id=c.user.id, company_name=company or person,
                    contact_name=person, email=email, phone=_clean(body.phone, 40), website=_clean(body.website),
                    industry=_clean(body.industry, 80), market=_clean(body.market, 80),
                    contact_role=_clean(body.role, 80), source_system=src, source_detail=detail,
                    source="%s%s" % (C.SOURCE_LABELS[src], (" - " + detail) if detail else ""),
                    met_on=body.met_on, intro_note=_clean(body.intro_note, 1000),
                    preferred_contact=body.preferred_contact if body.preferred_contact in ("email", "phone", "text")
                    else None, stage=STAGE_PROSPECT, status="open", stage_changed_at=now, created_at=now)
    db.add(o)
    db.flush()
    db.add(OpportunityEvent(opportunity_id=o.id, event_type="created",
                            summary="Contact added (%s)" % C.SOURCE_LABELS[src],
                            detail=_clean(body.notes, 2000), actor_user_id=c.user.id, occurred_at=now))
    db.commit()
    return {"lead": _row(o, None)}


class ContactPatch(BaseModel):
    contact_name: Optional[str] = None
    company_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    industry: Optional[str] = None
    market: Optional[str] = None
    source_detail: Optional[str] = None
    met_on: Optional[date] = None
    intro_note: Optional[str] = None
    preferred_contact: Optional[str] = None


@router.patch("/leads/{opp_id}")
def edit_contact(opp_id: str, body: ContactPatch, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    o = _opp(db, c, opp_id)
    m = {"contact_name": ("contact_name", 120), "company_name": ("company_name", 160), "email": ("email", 200),
         "phone": ("phone", 40), "role": ("contact_role", 80), "industry": ("industry", 80),
         "market": ("market", 80), "source_detail": ("source_detail", 160), "intro_note": ("intro_note", 1000)}
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        if k in m:
            col, n = m[k]
            val = _clean(v, n)
            if col == "company_name" and not val:
                continue
            if col == "email" and val and not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+$", val):
                raise HTTPException(400, "That email address doesn't look right.")
            setattr(o, col, val)
        elif k == "met_on":
            o.met_on = v
        elif k == "preferred_contact":
            o.preferred_contact = v if v in ("email", "phone", "text") else None
    # A fact that was missing may now be there: let held contacts be re-checked.
    st = db.get(EvoSysOutreachState, o.id)
    if st and st.status == "queued" and st.held_reason and st.campaign_id:
        camp = db.get(EvoSysCampaign, st.campaign_id)
        s = O.get_settings(db, c.bso)
        st.held_reason = C.eligibility(db, o, camp, s, st)
        if st.held_reason is None and camp.sending_on and C.is_approved(camp, s):
            st.status, st.next_due_at = "active", datetime.utcnow()
    db.commit()
    return {"lead": _row(o, st, names=_camp_names(db, c.bso))}


# ── leads ─────────────────────────────────────────────────────────────────────

def _filtered(db: Session, c: Ctx, view="all", q="", source="", campaign="", batch=""):
    rows = _leads_q(db, c).order_by(Opportunity.created_at.desc()).all()
    states = _states(db, [o.id for o in rows])
    booked = _booked_ids(db, c.bso)
    names = _camp_names(db, c.bso)
    needle = q.strip().lower()
    review_ids = set()
    if view == "review":
        for it in _review_items(db, c, rows, states, names):
            review_ids.update(it["lead_ids"])
    out = []
    for o in rows:
        r = _row(o, states.get(o.id), booked=o.id in booked, names=names)
        k = r["outreach"]["key"]
        if view == "followup" and k != "active":
            continue
        if view == "no_email" and k != "no_email" and not (r["outreach"].get("reason") == "no_email"):
            continue
        if view == "replied" and k != "replied":
            continue
        if view == "booked" and not r["booked"]:
            continue
        if view == "stopped" and k not in STOP_LABELS and k != "finished":
            continue
        if view == "review" and o.id not in review_ids:
            continue
        if view == "unassigned" and r["campaign_id"]:
            continue
        if source and r["source_key"] != source:
            continue
        if campaign == "none" and r["campaign_id"]:
            continue
        if campaign and campaign != "none" and r["campaign_id"] != campaign:
            continue
        if batch and r["batch"] != batch:
            continue
        if needle and needle not in " ".join(str(x or "") for x in (o.company_name, o.contact_name, o.phone, o.email,
                                                                     o.market, o.industry, o.source_detail)).lower():
            continue
        out.append(r)
    return rows, out


@router.get("/leads")
def leads(view: str = "all", q: str = "", source: str = "", campaign: str = "", batch: str = "",
          c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _prep(db, c)
    db.commit()
    rows, out = _filtered(db, c, view, q, source, campaign, batch)
    sources = {}
    for o in rows:
        k = C.source_key(o)
        sources[k] = sources.get(k, 0) + 1
    camps = (db.query(EvoSysCampaign).filter(EvoSysCampaign.brand_sales_org_id == c.bso,
                                             EvoSysCampaign.archived_at.is_(None))
             .order_by(EvoSysCampaign.created_at.desc()).all())
    return {"leads": out, "total": len(rows),
            "sources": [{"key": k, "label": C.SOURCE_LABELS.get(k, k), "count": n} for k, n in sorted(sources.items())],
            "campaigns": [{"id": k.id, "name": k.name} for k in camps], "can_manage": c.manager}


@router.get("/leads/batches")
def batches(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Every collection of contacts that exists, counted, so old batches can be
    reconciled: how many, how many still workable, how many can be emailed."""
    rows = _leads_q(db, c).all()
    out = {}
    for o in rows:
        b = out.setdefault(C.batch_label(o), {"batch": C.batch_label(o), "source": C.SOURCE_LABELS.get(C.source_key(o)),
                                             "total": 0, "working": 0, "with_email": 0, "closed": 0,
                                             "first": None, "last": None})
        b["total"] += 1
        if o.stage in O.WORKING_STAGES:
            b["working"] += 1
            if O._has_email(o):
                b["with_email"] += 1
        else:
            b["closed"] += 1
        d = o.created_at
        if d:
            b["first"] = min(b["first"], d) if b["first"] else d
            b["last"] = max(b["last"], d) if b["last"] else d
    res = sorted(out.values(), key=lambda b: -(b["total"]))
    for b in res:
        b["first"], b["last"] = iso(b["first"]), iso(b["last"])
    return {"batches": res, "total": len(rows)}


@router.get("/leads/{opp_id}")
def lead(opp_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    o = _opp(db, c, opp_id)
    st = db.get(EvoSysOutreachState, o.id)
    names = _camp_names(db, c.bso)
    msgs = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id == o.id)
            .order_by(EvoSysOutreachMessage.created_at).all())
    events = (db.query(OpportunityEvent).filter(OpportunityEvent.opportunity_id == o.id)
              .order_by(OpportunityEvent.occurred_at.desc()).limit(40).all())
    s = O.get_settings(db, c.bso)
    camp = db.get(EvoSysCampaign, st.campaign_id) if st and st.campaign_id else None
    nxt = None
    if st and st.status == "active" and camp and st.next_step < len(camp.steps or []):
        step = (camp.steps or [])[st.next_step]
        r = C.render(o, camp, s, step)
        nxt = {"step": st.next_step + 1, "channel": step.get("channel"), "subject": r["subject"],
               "body": r["text"], "facts_used": r["facts_used"], "due_at": iso(st.next_due_at),
               "campaign": camp.name, "campaign_on": bool(camp.sending_on)}
    from app.models.scheduling_models import SalesAppointment
    appts = (db.query(SalesAppointment).filter(SalesAppointment.opportunity_id == o.id)
             .order_by(SalesAppointment.starts_at.desc()).limit(10).all())
    db.commit()
    waiting = _waiting_replies(db, [o.id], names)
    return {"lead": _row(o, st, booked=bool(appts), names=names),
            "intro_note": o.intro_note,
            "messages": [_msg(m, names) for m in msgs], "next": nxt,
            "reply_waiting": bool(waiting), "intent": intent(waiting[0].body) if waiting else None,
            "appointments": [_appt(a, {o.id: o}) for a in appts],
            "signed_up": o.stage in SIGNED_UP,
            # Booking a call from this page puts the operator on it; the browser
            # does not keep the signed-in user's id.
            "me": c.user.id,
            "timeline": [{"summary": e.summary, "detail": e.detail, "type": e.event_type,
                          "at": iso(e.occurred_at)} for e in events]}


class Note(BaseModel):
    note: str = ""
    channel: str = "email"


@router.post("/leads/{opp_id}/reply")
def log_reply(opp_id: str, body: Note, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    o = _opp(db, c, opp_id)
    O.log_reply(db, o, c.user.id, body.note, body.channel)
    db.commit()
    return {"ok": True}


@router.post("/leads/{opp_id}/call")
def log_call(opp_id: str, body: Note, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    o = _opp(db, c, opp_id)
    now = datetime.utcnow()
    st = db.get(EvoSysOutreachState, o.id)
    db.add(EvoSysOutreachMessage(brand_sales_org_id=o.brand_sales_org_id, opportunity_id=o.id,
                                 campaign_id=st.campaign_id if st else None, direction="out",
                                 channel="call", to_address=o.phone, body=(body.note or "Called").strip()[:5000],
                                 status="logged", actor_user_id=c.user.id, created_at=now))
    db.add(OpportunityEvent(opportunity_id=o.id, event_type="call_logged", summary="Call logged",
                            detail=(body.note or "")[:2000], actor_user_id=c.user.id, occurred_at=now))
    if o.stage == "prospect":
        o.stage, o.stage_changed_at = "contacted", now
    o.contacted_at = o.contacted_at or now
    db.commit()
    return {"ok": True}


@router.post("/leads/{opp_id}/handled")
def mark_handled(opp_id: str, body: Note, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """The operator answered a reply outside the system (their own email,
    a call). Takes it off the work queue; automatic follow-up stays stopped."""
    o = _opp(db, c, opp_id)
    now = datetime.utcnow()
    st = db.get(EvoSysOutreachState, o.id)
    db.add(EvoSysOutreachMessage(brand_sales_org_id=o.brand_sales_org_id, opportunity_id=o.id,
                                 campaign_id=st.campaign_id if st else None, direction="out", channel="email",
                                 body=(body.note or "Answered").strip()[:5000], status="logged",
                                 actor_user_id=c.user.id, created_at=now))
    db.add(OpportunityEvent(opportunity_id=o.id, event_type="reply_handled", summary="Reply answered",
                            detail=(body.note or "")[:2000], actor_user_id=c.user.id, occurred_at=now))
    db.commit()
    return {"ok": True}


@router.post("/leads/{opp_id}/stop")
def stop_lead(opp_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    o = _opp(db, c, opp_id)
    O.stop(db, o.id, "manual")
    db.commit()
    return {"ok": True}


@router.post("/leads/{opp_id}/restart")
def restart_lead(opp_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    o = _opp(db, c, opp_id)
    if O.restart(db, o.id) is None:
        raise HTTPException(409, "This contact opted out or isn't in a campaign.")
    db.commit()
    return {"ok": True}


@router.post("/leads/{opp_id}/signup")
def signup_handoff(opp_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Hand a contact who wants to sign up to the existing deal and onboarding
    flow. Nothing is provisioned here: the deal page does that, and the source
    and campaign stay on the record for Results."""
    o = _opp(db, c, opp_id)
    names = _camp_names(db, c.bso)
    st = db.get(EvoSysOutreachState, o.id)
    camp = names.get(o.bd_campaign_id or (st.campaign_id if st else None))
    db.add(OpportunityEvent(opportunity_id=o.id, event_type="bd_signup_handoff",
                            summary="Signup started from %s" % PRODUCT,
                            detail="Source: %s%s" % (C.SOURCE_LABELS.get(C.source_key(o)),
                                                     ("; campaign: " + camp) if camp else ""),
                            actor_user_id=c.user.id, occurred_at=datetime.utcnow()))
    if st and st.status in ("active", "queued"):
        O.stop(db, o.id, "stage")
    db.commit()
    from app.services.acquisition.admins import require_master
    try:
        require_master(db, c.user)
        acq = True
    except HTTPException:
        acq = False
    return {"path": "/sales/opportunities/%s" % o.id, "campaign": camp,
            "source": C.SOURCE_LABELS.get(C.source_key(o)),
            # A service business joining the Acquisition Control Center is added
            # from this same record there (it keeps the link to this lead).
            "acquisition_path": "/acquisition-ops/clients" if acq else None,
            "acquisition_note": None if acq else ("Adding them as a service company in the Acquisition Control "
                                                  "Center needs Acquisition admin access.")}


# ── sources ───────────────────────────────────────────────────────────────────

@router.get("/sources")
def sources(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """What each source can do today, from what is actually built."""
    counts = {}
    for o in _leads_q(db, c).all():
        k = C.source_key(o)
        counts[k] = counts.get(k, 0) + 1
    status = {"networking": ("connected", "Add contacts by hand, from a card"),
              "referral": ("connected", "Add contacts by hand, with who referred them"),
              "website": ("connected", "Demo requests from evosyspro.live arrive by themselves"),
              "facebook": ("import", "Bring a list in with the importer"),
              "tiktok": ("import", "Bring a list in with the importer"),
              "instagram": ("import", "Bring a list in with the importer"),
              "google": ("import", "Bring a list in with the importer; no live Google search for this team yet"),
              "cold_email": ("import", "Bring a researched list in with the importer"),
              "other": ("import", "Any spreadsheet, with the importer")}
    label = {"connected": "Connected", "import": "Import only", "setup": "Needs setup"}
    return {"sources": [{"key": s["key"], "label": s["label"], "status": status[s["key"]][0],
                         "status_label": label[status[s["key"]][0]], "how": status[s["key"]][1],
                         "count": counts.get(s["key"], 0)} for s in C.SOURCES],
            "import_path": "/imports/sales?target=%s" % c.bso,
            "card_scanning": "Not built yet: enter the card by hand."}


# ── campaigns ─────────────────────────────────────────────────────────────────

def _camp_out(db: Session, k: EvoSysCampaign, s, c: Ctx, full=False) -> dict:
    mem = db.query(EvoSysOutreachState).filter(EvoSysOutreachState.campaign_id == k.id).all()
    counts = {"audience": sum(1 for m in mem if m.status in ("queued", "active", "finished", "stopped")),
              "in_followup": sum(1 for m in mem if m.status == "active"),
              "waiting": sum(1 for m in mem if m.status == "queued" and not m.held_reason),
              "held": sum(1 for m in mem if m.status == "queued" and m.held_reason),
              "replied": sum(1 for m in mem if m.replied_at),
              "finished": sum(1 for m in mem if m.status == "finished")}
    sent = (db.query(func.count(EvoSysOutreachMessage.id))
            .filter(EvoSysOutreachMessage.campaign_id == k.id, EvoSysOutreachMessage.direction == "out",
                    EvoSysOutreachMessage.status == "sent").scalar() or 0)
    nxt = (db.query(func.min(EvoSysOutreachState.next_due_at))
           .filter(EvoSysOutreachState.campaign_id == k.id, EvoSysOutreachState.status == "active").scalar())
    out = {"id": k.id, "name": k.name, "kind": k.kind, "kind_label": C.KIND_LABELS.get(k.kind, k.kind),
           "context": k.context, "industry": k.industry, "market": k.market, "tone": k.tone,
           "status": C.status_of(k, s), "sending_on": bool(k.sending_on), "approved": C.is_approved(k, s),
           "approved_at": iso(k.approved_at), "version": k.version, "approved_version": k.approved_version,
           "switched_at": iso(k.switched_at), "created_at": iso(k.created_at), "updated_at": iso(k.updated_at),
           "problems": C.problems(k, s), "counts": dict(counts, sent=sent), "next_send_at": iso(nxt),
           "archived": k.archived_at is not None}
    if full:
        out.update({"steps": k.steps or [], "offer": k.offer, "booking_url": k.booking_url,
                    "can_manage": c.manager, "placeholders": list(C.PLACEHOLDERS),
                    "sending_hours": "Weekdays 8:00 AM - 6:00 PM Central",
                    "my_email": c.user.email})
    return out


@router.get("/campaigns")
def list_campaigns(archived: bool = False, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _prep(db, c)
    s = O.get_settings(db, c.bso)
    db.commit()
    q = db.query(EvoSysCampaign).filter(EvoSysCampaign.brand_sales_org_id == c.bso)
    if not archived:
        q = q.filter(EvoSysCampaign.archived_at.is_(None))
    camps = q.order_by(EvoSysCampaign.created_at.desc()).all()
    return {"campaigns": [_camp_out(db, k, s, c) for k in camps], "team_problems": O.settings_problems(s),
            "can_manage": c.manager}


class LeadPick(BaseModel):
    lead_ids: List[str] = []
    view: Optional[str] = None
    q: Optional[str] = None
    source: Optional[str] = None
    campaign: Optional[str] = None
    batch: Optional[str] = None


def _pick(db: Session, c: Ctx, p: LeadPick) -> List[str]:
    if p.lead_ids:
        allowed = {o.id for o in _leads_q(db, c).filter(Opportunity.id.in_(p.lead_ids)).all()}
        return [i for i in p.lead_ids if i in allowed]
    if any((p.view, p.q, p.source, p.campaign, p.batch)):
        _, rows = _filtered(db, c, p.view or "all", p.q or "", p.source or "", p.campaign or "", p.batch or "")
        return [r["id"] for r in rows]
    return []


@router.post("/campaigns/suggest")
def suggest(p: LeadPick, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    ids = _pick(db, c, p)
    opps = _leads_q(db, c).filter(Opportunity.id.in_(ids)).all() if ids else []
    return {"groups": C.suggest_groups(db, opps), "count": len(opps)}


class DraftAudience(BaseModel):
    kind: str = "list"
    tone: Optional[str] = None
    market: Optional[str] = None
    industry: Optional[str] = None
    pick: LeadPick


@router.post("/campaigns/audience/preview")
def draft_audience_preview(body: DraftAudience, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Who a campaign would email before it exists: duplicates, held contacts,
    eligibility and anyone already in another campaign. Writes nothing."""
    s = O.get_settings(db, c.bso)
    kind = body.kind if body.kind in C.KINDS else "list"
    tone = body.tone if body.tone in ("warm", "researched") else ("warm" if kind in ("event", "relationship")
                                                                  else "researched")
    tmp = EvoSysCampaign(id="__draft__", brand_sales_org_id=c.bso, name="draft", kind=kind, tone=tone,
                         market=body.market, industry=body.industry, steps=C.default_steps(kind, tone))
    rows = C.classify(db, _pick(db, c, body.pick), tmp, s, c.bso)
    return {"rows": rows, "will_email": sum(1 for r in rows if r["will_email"]),
            "held": sum(1 for r in rows if r["held_reason"]),
            "in_other_campaigns": sum(1 for r in rows if r["other_campaign"])}


class CampaignIn(BaseModel):
    name: str
    kind: str = "list"
    context: Optional[str] = None
    industry: Optional[str] = None
    market: Optional[str] = None
    tone: Optional[str] = None
    offer: Optional[str] = None
    pick: Optional[LeadPick] = None
    move: bool = False


@router.post("/campaigns", status_code=201)
def create_campaign(body: CampaignIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    k = C.create(db, c.bso, c.user.id, name=body.name, kind=body.kind, context=body.context,
                 industry=body.industry, market=body.market, tone=body.tone, offer=body.offer)
    s = O.get_settings(db, c.bso)
    added = None
    if body.pick:
        added = C.add_leads(db, k, s, _pick(db, c, body.pick), c.bso, c.user.id, move=body.move)
    db.commit()
    return {"campaign": _camp_out(db, k, s, c, full=True), "added": added}


@router.get("/campaigns/{camp_id}")
def get_campaign(camp_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    k = _camp(db, c, camp_id)
    s = O.get_settings(db, c.bso)
    db.commit()
    mem = db.query(EvoSysOutreachState).filter(EvoSysOutreachState.campaign_id == k.id).all()
    ids = [m.opportunity_id for m in mem]
    opps = {o.id: o for o in _leads_q(db, c).filter(Opportunity.id.in_(ids)).all()} if ids else {}
    names = _camp_names(db, c.bso)
    audience = [_row(opps[m.opportunity_id], m, names=names) for m in mem if m.opportunity_id in opps]
    return dict(_camp_out(db, k, s, c, full=True), audience=audience)


class CampaignPatch(BaseModel):
    name: Optional[str] = None
    kind: Optional[str] = None
    context: Optional[str] = None
    industry: Optional[str] = None
    market: Optional[str] = None
    tone: Optional[str] = None
    steps: Optional[list] = None
    offer: Optional[str] = None
    booking_url: Optional[str] = None


@router.put("/campaigns/{camp_id}")
def edit_campaign(camp_id: str, body: CampaignPatch, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    k = _camp(db, c, camp_id)
    s = O.get_settings(db, c.bso)
    material = C.update(db, k, body.model_dump(exclude_none=True), c.user.id)
    db.commit()
    return dict(_camp_out(db, k, s, c, full=True), approval_cleared=material)


@router.post("/campaigns/{camp_id}/audience/preview")
def audience_preview(camp_id: str, p: LeadPick, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    k = _camp(db, c, camp_id)
    s = O.get_settings(db, c.bso)
    rows = C.classify(db, _pick(db, c, p), k, s, c.bso)
    return {"rows": rows, "will_email": sum(1 for r in rows if r["will_email"] and not r["already_here"]),
            "held": sum(1 for r in rows if r["held_reason"]),
            "already_here": sum(1 for r in rows if r["already_here"]),
            "in_other_campaigns": sum(1 for r in rows if r["other_campaign"] and not r["already_here"])}


class AddIn(LeadPick):
    move: bool = False


@router.post("/campaigns/{camp_id}/leads")
def add_to_campaign(camp_id: str, body: AddIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    k = _camp(db, c, camp_id)
    s = O.get_settings(db, c.bso)
    res = C.add_leads(db, k, s, _pick(db, c, body), c.bso, c.user.id, move=body.move)
    db.commit()
    return dict(res, campaign=_camp_out(db, k, s, c))


@router.delete("/campaigns/{camp_id}/leads/{opp_id}")
def remove_from_campaign(camp_id: str, opp_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    k = _camp(db, c, camp_id)
    _opp(db, c, opp_id)
    if not C.remove_lead(db, k, opp_id, c.user.id):
        raise HTTPException(404, "That contact isn't in this campaign.")
    db.commit()
    return {"ok": True}


@router.post("/campaigns/{camp_id}/approve")
def approve_campaign(camp_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    k = _camp(db, c, camp_id)
    s = O.get_settings(db, c.bso)
    p = C.problems(k, s)
    if p:
        raise HTTPException(400, " ".join(p))
    C.approve(db, k, s, c.user.id)
    db.commit()
    return _camp_out(db, k, s, c, full=True)


class Switch(BaseModel):
    on: bool


@router.post("/campaigns/{camp_id}/switch")
def switch_campaign(camp_id: str, body: Switch, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    k = _camp(db, c, camp_id)
    s = O.get_settings(db, c.bso)
    if body.on:
        if not C.is_approved(k, s):
            raise HTTPException(400, "Approve this version of the messages before turning the campaign on.")
        p = C.problems(k, s)
        if p:
            raise HTTPException(400, " ".join(p))
    started = C.switch(db, k, s, body.on, c.user.id)
    db.commit()
    return dict(_camp_out(db, k, s, c, full=True), started=started)


@router.post("/campaigns/{camp_id}/archive")
def archive_campaign(camp_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    k = _camp(db, c, camp_id)
    k.sending_on = False
    k.archived_at = datetime.utcnow()
    for st in db.query(EvoSysOutreachState).filter(EvoSysOutreachState.campaign_id == k.id,
                                                   EvoSysOutreachState.status.in_(("queued", "active"))).all():
        st.status, st.stop_reason, st.stopped_at, st.next_due_at = "stopped", "manual", datetime.utcnow(), None
    db.commit()
    return {"ok": True}


@router.get("/campaigns/{camp_id}/preview")
def campaign_preview(camp_id: str, lead_id: Optional[str] = None, c: Ctx = Depends(ctx),
                     db: Session = Depends(get_db)):
    """Real rendered examples: the messages filled in for actual contacts in
    the audience, with the facts each one used and anything missing."""
    k = _camp(db, c, camp_id)
    s = O.get_settings(db, c.bso)
    if lead_id:
        picks = [_opp(db, c, lead_id)]
    else:
        ids = [m.opportunity_id for m in db.query(EvoSysOutreachState)
               .filter(EvoSysOutreachState.campaign_id == k.id).limit(50).all()]
        picks = _leads_q(db, c).filter(Opportunity.id.in_(ids)).limit(3).all() if ids else []
    return {"examples": [{"lead": {"id": o.id, "company": o.company_name, "contact": o.contact_name,
                                   "email": o.email},
                          "steps": [dict(C.render(o, k, s, st), day=st.get("day"), channel=st.get("channel"))
                                    for st in (k.steps or [])]} for o in picks]}


class TestSend(BaseModel):
    to: str
    step: int = 1
    lead_id: Optional[str] = None


@router.post("/campaigns/{camp_id}/test")
def campaign_test(camp_id: str, body: TestSend, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """One message, filled in with a real contact's details, to an address
    the manager types (their own). Never to the contact."""
    _manager(c)
    k = _camp(db, c, camp_id)
    s = O.get_settings(db, c.bso)
    steps = k.steps or []
    if not (1 <= body.step <= len(steps)) or steps[body.step - 1].get("channel") != "email":
        raise HTTPException(400, "Pick an email message.")
    if "@" not in (body.to or ""):
        raise HTTPException(400, "Enter your email address.")
    o = _opp(db, c, body.lead_id) if body.lead_id else None
    if o is None:
        ids = [m.opportunity_id for m in db.query(EvoSysOutreachState)
               .filter(EvoSysOutreachState.campaign_id == k.id).all()]
        o = _leads_q(db, c).filter(Opportunity.id.in_(ids)).first() if ids else None
    if o is None:
        raise HTTPException(400, "Add a contact to the campaign first, so the test can be filled in.")
    msg = O.render_email(o, s, steps[body.step - 1], k)
    msg = dict(msg, subject="[TEST] " + msg["subject"])
    r = O._send(body.to.strip(), msg, s, o.id)
    if not r.get("success"):
        raise HTTPException(502, "The test email did not send: %s" % (r.get("error") or "unknown error"))
    return {"ok": True, "sent_to": body.to.strip(), "filled_with": o.company_name}


@router.post("/campaigns/{camp_id}/ai-draft")
def ai_draft(camp_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """AI proposes the campaign's messages. It only gets the campaign's own
    description and may only use the placeholders; nothing is saved until the
    manager saves it, and saving still needs approval."""
    _manager(c)
    k = _camp(db, c, camp_id)
    from app.services.evosys_sales import ai_assist
    try:
        steps = ai_assist.draft_steps(k, c.user.id, c.bso)
    except ai_assist.AIUnavailable as e:
        raise HTTPException(503, "AI couldn't write a draft (%s). Edit the messages yourself." % e)
    return {"steps": steps, "note": "Proposed by AI from the campaign description. Review, edit, then save."}


# ── conversations ─────────────────────────────────────────────────────────────

@router.get("/conversations")
def conversations(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    leads_ = {o.id: o for o in _leads_q(db, c).all()}
    if not leads_:
        return {"conversations": []}
    names = _camp_names(db, c.bso)
    s = O.get_settings(db, c.bso)
    last = (db.query(EvoSysOutreachMessage.opportunity_id, func.max(EvoSysOutreachMessage.created_at),
                     func.count(EvoSysOutreachMessage.id))
            .filter(EvoSysOutreachMessage.opportunity_id.in_(list(leads_)))
            .group_by(EvoSysOutreachMessage.opportunity_id).all())
    states = _states(db, list(leads_))
    waiting = {m.opportunity_id: m for m in _waiting_replies(db, list(leads_), names)}
    camps = {k.id: k for k in db.query(EvoSysCampaign).filter(EvoSysCampaign.brand_sales_org_id == c.bso).all()}
    out = []
    for opp_id, at, n in sorted(last, key=lambda r: r[1], reverse=True):
        m = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id == opp_id,
                                                    EvoSysOutreachMessage.created_at == at).first())
        r = _row(leads_[opp_id], states.get(opp_id), last=_msg(m, names) if m else None, names=names)
        r["message_count"] = n
        w = waiting.get(opp_id)
        r["waiting"] = w is not None
        if w is not None:
            it = intent(w.body)
            st = states.get(opp_id)
            r["intent"] = it
            r["needs_review"] = it["key"] == "pricing" and not _offer_ready(
                camps.get(w.campaign_id or (st.campaign_id if st else None)), s)
            r["reply"] = _msg(w, names)
        out.append(r)
    out.sort(key=lambda r: (not r.get("waiting"),))
    return {"conversations": out}


@router.post("/conversations/{opp_id}/suggest")
def suggest_reply(opp_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """AI summary of the reply and a draft answer for the operator to edit and
    send themselves. Never sent by the system, never quotes a price that is not
    in the approved offer, never confirms a booking."""
    o = _opp(db, c, opp_id)
    names = _camp_names(db, c.bso)
    waiting = _waiting_replies(db, [o.id], names)
    last_in = waiting[0] if waiting else (db.query(EvoSysOutreachMessage)
                                          .filter(EvoSysOutreachMessage.opportunity_id == o.id,
                                                  EvoSysOutreachMessage.direction == "in")
                                          .order_by(EvoSysOutreachMessage.created_at.desc()).first())
    if last_in is None:
        raise HTTPException(400, "There's no reply from this contact to answer.")
    st = db.get(EvoSysOutreachState, o.id)
    camp = db.get(EvoSysCampaign, last_in.campaign_id or (st.campaign_id if st else None) or "") \
        if (last_in.campaign_id or (st and st.campaign_id)) else None
    s = O.get_settings(db, c.bso)
    it = intent(last_in.body)
    review = it["key"] == "pricing" and not _offer_ready(camp, s)
    from app.services.evosys_sales import ai_assist
    try:
        d = ai_assist.draft_reply(o, camp, s, last_in.body or "", c.user.id, c.bso, offer_ready=not review)
    except ai_assist.AIUnavailable as e:
        return {"ok": False, "intent": it, "needs_review": review, "original": last_in.body,
                "error": "AI couldn't draft a reply (%s). Write it yourself." % e}
    return dict(d, ok=True, intent=it, needs_review=review, original=last_in.body)


# ── appointments and results ──────────────────────────────────────────────────

@router.get("/appointments")
def appointments(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    from app.models.scheduling_models import SalesAppointment
    by_id = {o.id: o for o in _leads_q(db, c).all()}
    now = datetime.utcnow()
    rows = (db.query(SalesAppointment).filter(SalesAppointment.brand_sales_org_id == c.bso,
                                              SalesAppointment.starts_at >= now - timedelta(days=30))
            .order_by(SalesAppointment.starts_at).all())
    if not c.manager:
        rows = [a for a in rows if a.opportunity_id in by_id]
    up = [_appt(a, by_id) for a in rows if a.starts_at >= now - timedelta(hours=2)
          and a.status not in ("cancelled", "canceled")]
    past = [_appt(a, by_id) for a in rows if a.starts_at < now - timedelta(hours=2)][::-1]
    problems = []
    for a in rows:
        if a.status in ("cancelled", "canceled") and a.starts_at >= now:
            problems.append(dict(_appt(a, by_id), problem="Cancelled - offer a new time"))
        elif a.starts_at < now - timedelta(hours=2) and a.status in ("scheduled", "confirmed"):
            problems.append(dict(_appt(a, by_id), problem="Call time passed - record what happened"))
        elif getattr(a, "confirmation_status", None) in ("declined", "reschedule_requested"):
            problems.append(dict(_appt(a, by_id), problem="Asked for a different time"))
    return {"upcoming": up, "recent": past, "problems": problems}


@router.get("/results")
def results(days: int = 30, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Replies, booked calls, completed calls, demos and signups, kept apart and
    broken down by source and by campaign."""
    from app.models.scheduling_models import SalesAppointment
    days = max(1, min(days, 365))
    since = datetime.utcnow() - timedelta(days=days)
    leads = _leads_q(db, c).all()
    ids = [o.id for o in leads]
    names = _camp_names(db, c.bso)
    states = _states(db, ids)
    msgs = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id.in_(ids),
                                                   EvoSysOutreachMessage.created_at >= since).all()) if ids else []
    appts = (db.query(SalesAppointment).filter(SalesAppointment.brand_sales_org_id == c.bso,
                                               SalesAppointment.opportunity_id.in_(ids)).all()) if ids else []
    appt_by = {}
    for a in appts:
        appt_by.setdefault(a.opportunity_id, []).append(a)

    def blank(label):
        return {"label": label, "contacts": 0, "emailed": 0, "replied": 0, "booked": 0, "completed": 0,
                "demos": 0, "signups": 0}
    by_source, by_camp = {}, {}
    emailed = {m.opportunity_id for m in msgs if m.direction == "out" and m.status == "sent"}
    replied = {m.opportunity_id for m in msgs if m.direction == "in"}
    for o in leads:
        sk = C.source_key(o)
        st = states.get(o.id)
        ck = o.bd_campaign_id or (st.campaign_id if st else None) or "none"
        rows = [by_source.setdefault(sk, blank(C.SOURCE_LABELS.get(sk, sk))),
                by_camp.setdefault(ck, blank(names.get(ck, "No campaign")))]
        ap = appt_by.get(o.id, [])
        for r in rows:
            r["contacts"] += 1
            r["emailed"] += o.id in emailed
            r["replied"] += o.id in replied
            r["booked"] += bool(ap)
            r["completed"] += any(a.status in ("completed", "held", "attended") for a in ap)
            r["demos"] += bool(o.demo_requested_at) or o.stage in DEMO_STAGES or o.stage in SIGNED_UP
            r["signups"] += o.stage in SIGNED_UP or bool(o.won_at)
    totals = blank("All")
    for r in by_source.values():
        for k in totals:
            if k != "label":
                totals[k] += r[k]
    return {"days": days, "totals": totals,
            "emails_sent": sum(1 for m in msgs if m.direction == "out" and m.status == "sent"),
            "opted_out": sum(1 for s in states.values() if s.opted_out_at),
            "bounced": sum(1 for s in states.values() if s.stop_reason == "email_failed"),
            "by_source": sorted(by_source.values(), key=lambda r: -r["contacts"]),
            "by_campaign": sorted(by_camp.values(), key=lambda r: -r["contacts"])}


# ── team outreach settings ────────────────────────────────────────────────────

class SettingsIn(BaseModel):
    offer: Optional[str] = None
    booking_url: Optional[str] = None
    sender_name: Optional[str] = None
    from_email: Optional[str] = None
    reply_to_email: Optional[str] = None
    mailing_address: Optional[str] = None
    daily_limit: Optional[int] = None
    eligible_markets: Optional[List[str]] = None


def _settings_out(s, c: Ctx, db: Session) -> dict:
    return {"offer": s.offer, "booking_url": s.booking_url, "sender_name": s.sender_name,
            "from_email": s.from_email, "reply_to_email": s.reply_to_email,
            "mailing_address": s.mailing_address, "daily_limit": s.daily_limit,
            "eligible_markets": s.eligible_markets or [],
            "problems": O.settings_problems(s), "can_manage": c.manager, "texting_ready": False,
            "reply_tracking": _reply_tracking(db, s), "sending_hours": "Weekdays 8:00 AM - 6:00 PM Central",
            "sent_today": O.sent_today(db, c.bso, datetime.utcnow())}


def _reply_tracking(db: Session, s) -> dict:
    """Where replies are read, in plain words. A reply address on the same
    domain as a connected mailbox is delivered into it (info@ and plus-tagged
    addresses arrive in support@evosyspro.live: checked 2026-11-16 with real
    mail), and the mailbox poller reads it every 5 minutes."""
    from app.models.inbound_mailbox_models import InboundMailbox
    addr = (s.reply_to_email or "").strip().lower()
    domain = addr.split("@", 1)[1] if "@" in addr else ""
    boxes = [b for b in db.query(InboundMailbox).filter(InboundMailbox.is_active.is_(True),
                                                        InboundMailbox.purpose.is_(None)).all()]
    box = next((b for b in boxes if b.address.lower() == addr), None) or \
        next((b for b in boxes if domain and b.address.lower().endswith("@" + domain)), None)
    last = (db.query(func.max(EvoSysOutreachMessage.created_at))
            .filter(EvoSysOutreachMessage.direction == "in",
                    EvoSysOutreachMessage.match_method.in_(("reply_address", "sender"))).scalar())
    if box is None:
        return {"automatic": False, "mailbox": None, "last_auto_reply_at": None,
                "text": ("Replies to %s are not read automatically: no connected mailbox receives "
                         "that domain. Use \"They replied\" until one is connected." % (addr or "-"))}
    ok = box.last_status == "ok"
    return {"automatic": ok, "mailbox": box.address, "last_polled_at": iso(box.last_polled_at),
            "last_auto_reply_at": iso(last),
            "text": ("Replies to %s arrive in %s, which is read every 5 minutes. A reply stops that "
                     "contact's emails by itself; out-of-office replies are ignored." % (addr, box.address))
                    if ok else ("The %s mailbox needs attention (%s), so replies are not being read right now."
                                % (box.address, box.last_status or "unknown"))}


@router.get("/outreach")
def get_outreach(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    s = O.get_settings(db, c.bso)
    db.commit()
    return _settings_out(s, c, db)


@router.put("/outreach")
def put_outreach(body: SettingsIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    s = O.get_settings(db, c.bso)
    O.save_settings(db, s, body.model_dump(exclude_none=True), c.user.id)
    db.commit()
    return _settings_out(s, c, db)


@router.post("/outreach/approve")
def approve_team(c: Ctx = Depends(ctx)):
    raise HTTPException(410, "Outreach is approved per campaign now. Open the campaign and approve it there.")


@router.post("/outreach/switch")
def switch_team(body: Switch, c: Ctx = Depends(ctx)):
    _manager(c)
    raise HTTPException(410, "Outreach is switched on per campaign now. Open the campaign and turn it on there.")


# ── public opt-out ────────────────────────────────────────────────────────────

_PAGE = ('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">'
         '<title>EvoSys Pro</title><body style="font-family:system-ui,sans-serif;max-width:520px;'
         'margin:15vh auto;padding:0 20px;color:#16233a"><h2>%s</h2><p>%s</p></body>')


def _opt_out(token: str, db: Session) -> bool:
    opp_id = O.read_opt_out(token)
    if not opp_id or db.get(Opportunity, opp_id) is None:
        return False
    O.stop(db, opp_id, "opted_out")
    db.add(OpportunityEvent(opportunity_id=opp_id, event_type="outreach_opt_out",
                            summary="Opted out of EvoSys emails", occurred_at=datetime.utcnow()))
    db.commit()
    return True


@public_router.get("/stop/{token}", response_class=HTMLResponse)
def opt_out_page(token: str, db: Session = Depends(get_db)):
    if not _opt_out(token, db):
        return HTMLResponse(_PAGE % ("Link not recognized", "This link is not valid."), status_code=404)
    return HTMLResponse(_PAGE % ("You're unsubscribed", "You won't get any more emails from EvoSys Pro."))


@public_router.post("/stop/{token}")
def opt_out_one_click(token: str, db: Session = Depends(get_db)):
    if not _opt_out(token, db):
        raise HTTPException(404, "Link not recognized.")
    return {"ok": True}
