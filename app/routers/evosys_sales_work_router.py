"""EvoSys Sales - the work behind the six screens (PR #663).

Same door as evosys_sales_router (/sales/evosys/*, the EvoSys Pro sales team
only; reps see their own leads; managers change campaigns and lead groups).

  Leads         group / review / AI group suggestion / regroup
  Outreach      campaigns: list, create, edit, approve, switch, AI draft
  Conversations AI summary + draft for a reply; send a reply; label a reply
  Appointments  intro-call times and booking (existing scheduler)
  Results       outcomes by campaign, industry, market and source
  Today         work queue + "needs your attention"
  Signed up     hand the business to the existing service-company onboarding
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.evosys_sales_models import (EvoSysAITask, EvoSysCampaign, EvoSysLead, EvoSysOutreachMessage,
                                            EvoSysOutreachState)
from app.models.sales_models import Opportunity, OpportunityEvent
from app.routers.evosys_sales_router import (Ctx, ctx, _leads_q, _manager, _msg, _opp, _hours_text, iso,
                                             claim_warnings, AD_SOURCES)
from app.services.evosys_sales import ai as AI
from app.services.evosys_sales import campaigns as CA
from app.services.evosys_sales import grouping as G
from app.services.evosys_sales import outreach as O

router = APIRouter(prefix="/sales/evosys", tags=["evosys-sales"])

INTRO_KEY = "discovery"                  # existing 30-minute "Discovery" meeting type
DEMO_KEYS = ("demo", "discovery_demo")   # existing demo meeting types


# ── helpers ───────────────────────────────────────────────────────────────────

def _settings(db: Session, c: Ctx):
    return O.get_settings(db, c.bso)


def _lead_row(db: Session, s, opp: Opportunity) -> EvoSysLead:
    return db.get(EvoSysLead, opp.id) or G.ensure(db, s, opp)


def _group_out(s, lead: Optional[EvoSysLead]) -> Dict[str, Any]:
    if lead is None:
        return {}
    return {"industry_key": lead.industry_key, "market_key": lead.market_key,
            "industry": G.label_of(G.industries(s), lead.industry_key),
            "market": G.label_of(G.markets(s), lead.market_key),
            "city": lead.city, "state": lead.state, "group_source": lead.group_source,
            "group_reason": lead.group_reason, "review_status": lead.review_status,
            "review_label": G.REVIEW_LABEL.get(lead.review_status, lead.review_status),
            "review_note": lead.review_note, "duplicate_of_id": lead.duplicate_of_id,
            "source_channel": lead.source_channel, "source_label": G.SOURCE_LABEL.get(lead.source_channel or "", "Other")}


def _camp(db: Session, c: Ctx, cid: str) -> EvoSysCampaign:
    camp = db.get(EvoSysCampaign, cid)
    if camp is None or camp.brand_sales_org_id != c.bso:
        raise HTTPException(404, "Campaign not found.")
    return camp


def _audience(db: Session, s, camp: EvoSysCampaign) -> Dict[str, int]:
    leads = db.query(EvoSysLead).filter(EvoSysLead.brand_sales_org_id == camp.brand_sales_org_id).all()
    camps = CA.all_for(db, s)                 # once, not twice per lead (saves took ~10 min)
    n = 0
    for l in leads:
        hit = CA.campaign_for(db, s, l, camps)
        n += 1 if hit is not None and hit.id == camp.id else 0
    sts = db.query(EvoSysOutreachState).filter(EvoSysOutreachState.campaign_id == camp.id).all()
    paused = not CA.can_send(camp, s)
    return {"matching_leads": n, "in_followup": sum(1 for x in sts if x.status == "active"),
            # Follow-ups held by this campaign while it is off or needs approval.
            "waiting": sum(1 for x in sts if x.status == "active") if paused else 0,
            "finished": sum(1 for x in sts if x.status == "finished"),
            "stopped": sum(1 for x in sts if x.status == "stopped")}


def _camp_out(db: Session, s, camp: EvoSysCampaign, detail: bool = False) -> Dict[str, Any]:
    approved = CA.is_approved(camp, s)
    out = {"id": camp.id, "name": camp.name, "is_default": camp.is_default,
           "industry_key": camp.industry_key, "market_key": camp.market_key,
           "group": G.group_label(s, camp.industry_key, camp.market_key) if not camp.is_default else "All leads not in another campaign",
           "sending_on": bool(camp.sending_on), "approved": approved, "version": camp.version,
           "approved_version": camp.approved_version if approved else None,
           "approved_at": iso(camp.approved_at) if approved else None,
           "can_send": CA.can_send(camp, s), "problems": CA.problems(camp, s),
           "hours": _hours_text(camp), "draft_origin": camp.draft_origin, "audience": _audience(db, s, camp)}
    if not approved and camp.approved_version:
        out["approval_note"] = "Version %d was approved; this is version %d. Approve it again to send." % (
            camp.approved_version, camp.version)
    if detail:
        out.update({"steps": camp.steps or [], "offer": camp.offer, "booking_url": camp.booking_url,
                    "daily_limit": camp.daily_limit, "send_days": CA.days(camp), "send_start_hour": camp.send_start_hour,
                    "send_end_hour": camp.send_end_hour, "timezone": camp.timezone,
                    "ai_draft": AI.payload(AI.latest(db, "draft_campaign", camp.id))})
    return out


# ── LEADS: groups and review ──────────────────────────────────────────────────

@router.get("/groups")
def groups(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Configured industries, markets and lead sources, with counts by group."""
    s = _settings(db, c)
    G.backfill(db, s, c.bso)
    ids = [o.id for o in _leads_q(db, c).filter(Opportunity.status == "open").all()]
    rows = db.query(EvoSysLead).filter(EvoSysLead.opportunity_id.in_(ids)).all() if ids else []
    counts: Dict[str, int] = {}
    for r in rows:
        k = "%s|%s" % (r.industry_key or "", r.market_key or "")
        counts[k] = counts.get(k, 0) + 1
    db.commit()
    return {"industries": G.industries(s), "markets": G.markets(s),
            "sources": [{"key": k, "label": l, "access": G.SOURCE_ACCESS.get(k, "import")} for k, l in G.SOURCE_CHANNELS],
            "groups": [{"industry_key": k.split("|")[0] or None, "market_key": k.split("|")[1] or None,
                        "label": G.group_label(s, k.split("|")[0] or None, k.split("|")[1] or None), "leads": n}
                       for k, n in sorted(counts.items(), key=lambda kv: -kv[1])],
            "review": {st: sum(1 for r in rows if r.review_status == st) for st in G.REVIEW_LABEL},
            "team_id": c.bso, "can_manage": c.manager}


@router.post("/leads/regroup")
def regroup(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Group every open lead by rule. People's choices are kept."""
    _manager(c)
    s = _settings(db, c)
    n = G.backfill(db, s, c.bso)
    for row in db.query(EvoSysLead).filter(EvoSysLead.brand_sales_org_id == c.bso).all():
        opp = db.get(Opportunity, row.opportunity_id)
        if opp is not None and row.group_source == "rule":
            G.ensure(db, s, opp)
    db.commit()
    return {"grouped": n}


@router.get("/leads/{opp_id}/group")
def lead_group(opp_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    s = _settings(db, c)
    o = _opp(db, c, opp_id)
    lead = _lead_row(db, s, o)
    dup = db.get(Opportunity, lead.duplicate_of_id) if lead.duplicate_of_id else None
    out = _group_out(s, lead)
    out["duplicate_of"] = ({"id": dup.id, "company": dup.company_name, "email": dup.email, "phone": dup.phone}
                           if dup else None)
    out["ai"] = AI.payload(AI.latest(db, "group_lead", o.id))
    camp = CA.campaign_for(db, s, lead)
    out["campaign"] = {"id": camp.id, "name": camp.name} if camp else None
    db.commit()
    return out


class GroupIn(BaseModel):
    industry_key: Optional[str] = None
    market_key: Optional[str] = None
    note: Optional[str] = None
    from_ai_task_id: Optional[str] = None


@router.put("/leads/{opp_id}/group")
def set_lead_group(opp_id: str, body: GroupIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    s = _settings(db, c)
    o = _opp(db, c, opp_id)
    if body.industry_key and body.industry_key not in {i["key"] for i in G.industries(s)}:
        raise HTTPException(422, "Unknown industry.")
    if body.market_key and body.market_key not in {m["key"] for m in G.markets(s)}:
        raise HTTPException(422, "Unknown market.")
    source = "manual"
    if body.from_ai_task_id:
        t = db.get(EvoSysAITask, body.from_ai_task_id)
        if t is None or t.target_id != o.id:
            raise HTTPException(404, "Suggestion not found.")
        same = (t.output or {}).get("industry_key") == body.industry_key and \
            (t.output or {}).get("market_key") == body.market_key
        AI.resolve(db, t, "accepted" if same else "edited")
        source = "ai_accepted" if same else "manual"
    G.set_group(db, o, c.user.id, industry_key=body.industry_key, market_key=body.market_key, source=source,
                note=body.note)
    db.commit()
    return lead_group(opp_id, c, db)


class ReviewIn(BaseModel):
    action: str            # hold | release | exclude | not_duplicate | same_business
    note: Optional[str] = None


@router.post("/leads/{opp_id}/review")
def review_lead(opp_id: str, body: ReviewIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    s = _settings(db, c)
    o = _opp(db, c, opp_id)
    lead = _lead_row(db, s, o)
    a = body.action
    if a == "hold":
        G.set_review(db, o, c.user.id, "held", body.note or "Held by a person")
    elif a == "release":
        G.set_review(db, o, c.user.id, "ok" if lead.market_key else "unclear_market", body.note or "Released from hold")
    elif a == "exclude":
        G.set_review(db, o, c.user.id, "excluded", body.note or "Excluded by a person")
        O.stop(db, o.id, "excluded")
    elif a == "not_duplicate":
        G.set_review(db, o, c.user.id, "ok" if lead.market_key else "unclear_market",
                     body.note or "Checked: not the same business", duplicate_of="")
    elif a == "same_business":
        if not lead.duplicate_of_id:
            raise HTTPException(409, "This lead is not marked as a possible duplicate.")
        G.set_review(db, o, c.user.id, "excluded",
                     body.note or "Same business as another lead - kept the other one")
        O.stop(db, o.id, "excluded")
    else:
        raise HTTPException(422, "Unknown action.")
    db.commit()
    return lead_group(opp_id, c, db)


@router.post("/leads/{opp_id}/ai-group")
def ai_group(opp_id: str, again: bool = False, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    s = _settings(db, c)
    o = _opp(db, c, opp_id)
    lead = _lead_row(db, s, o)
    notes = [e.detail or "" for e in db.query(OpportunityEvent).filter(
        OpportunityEvent.opportunity_id == o.id, OpportunityEvent.event_type.in_(("created", "note"))).limit(8).all()]
    record = "\n".join(x for x in [o.company_name or "", o.industry or "", o.source or "", o.website or ""] + notes if x)
    t = AI.suggest_group(db, s, o, lead, c.user.id, record[:4000], force=again)
    db.commit()
    return AI.payload(t)


# ── OUTREACH: campaigns ───────────────────────────────────────────────────────

@router.get("/campaigns")
def list_campaigns(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    s = _settings(db, c)
    G.backfill(db, s, c.bso)
    out = [_camp_out(db, s, x) for x in CA.all_for(db, s)]
    db.commit()
    return {"campaigns": out, "can_manage": c.manager}


class CampaignIn(BaseModel):
    name: Optional[str] = None
    industry_key: Optional[str] = None
    market_key: Optional[str] = None
    steps: Optional[list] = None
    offer: Optional[str] = None
    booking_url: Optional[str] = None
    daily_limit: Optional[int] = None
    send_days: Optional[Any] = None
    send_start_hour: Optional[int] = None
    send_end_hour: Optional[int] = None
    timezone: Optional[str] = None


@router.post("/campaigns", status_code=201)
def create_campaign(body: CampaignIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """A new campaign for one industry + market group, starting from the
    default campaign's copy and schedule, OFF and not approved."""
    _manager(c)
    s = _settings(db, c)
    if not body.industry_key and not body.market_key:
        raise HTTPException(422, "Pick an industry, a market or both.")
    for x in CA.all_for(db, s):
        if not x.is_default and x.industry_key == body.industry_key and x.market_key == body.market_key:
            raise HTTPException(409, "There is already a campaign for this group: %s." % x.name)
    base = CA.ensure_default(db, s)
    camp = EvoSysCampaign(brand_sales_org_id=c.bso, name=(body.name or G.group_label(s, body.industry_key, body.market_key))[:200],
                          industry_key=body.industry_key, market_key=body.market_key, is_default=False,
                          steps=[dict(x) for x in (base.steps or [])], offer=base.offer, booking_url=base.booking_url,
                          daily_limit=base.daily_limit, send_days=base.send_days, send_start_hour=base.send_start_hour,
                          send_end_hour=base.send_end_hour, timezone=base.timezone, sending_on=False,
                          draft_origin="manual", version=1, updated_by=c.user.id)
    db.add(camp)
    db.flush()
    CA.save(db, camp, body.model_dump(exclude_none=True, exclude={"industry_key", "market_key"}), c.user.id)
    out = _camp_out(db, s, camp, detail=True)
    db.commit()
    return out


@router.get("/campaigns/{cid}")
def get_campaign(cid: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    s = _settings(db, c)
    out = _camp_out(db, s, _camp(db, c, cid), detail=True)
    db.commit()
    return out


@router.put("/campaigns/{cid}")
def put_campaign(cid: str, body: CampaignIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    s = _settings(db, c)
    camp = _camp(db, c, cid)
    data = body.model_dump(exclude_unset=True)
    if "timezone" in data and data["timezone"]:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(data["timezone"])
        except Exception:  # noqa: BLE001
            raise HTTPException(422, "Unknown time zone.")
    CA.save(db, camp, data, c.user.id)
    out = _camp_out(db, s, camp, detail=True)
    db.commit()
    return out


@router.post("/campaigns/{cid}/approve")
def approve_campaign(cid: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    s = _settings(db, c)
    camp = _camp(db, c, cid)
    try:
        CA.approve(db, camp, s, c.user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    out = _camp_out(db, s, camp, detail=True)
    db.commit()
    return out


class SwitchIn(BaseModel):
    on: bool


@router.post("/campaigns/{cid}/switch")
def switch_campaign(cid: str, body: SwitchIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    s = _settings(db, c)
    camp = _camp(db, c, cid)
    try:
        CA.switch(db, camp, s, body.on, c.user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    out = _camp_out(db, s, camp, detail=True)
    db.commit()
    return out


@router.post("/campaigns/{cid}/ai-draft")
def ai_draft(cid: str, again: bool = False, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """AI drafts the emails. Nothing is changed until a person applies it."""
    _manager(c)
    s = _settings(db, c)
    camp = _camp(db, c, cid)
    chans = {l.source_channel for l in db.query(EvoSysLead).filter(EvoSysLead.brand_sales_org_id == c.bso).all()
             if CA.campaign_for(db, s, l) is not None and CA.campaign_for(db, s, l).id == camp.id}
    seen = bool(chans) and chans <= set(AD_SOURCES)
    t = AI.draft_campaign(db, s, camp, c.user.id, source_seen=seen, force=again)
    db.commit()
    return AI.payload(t)


@router.post("/campaigns/{cid}/ai-draft/{task_id}/apply")
def apply_ai_draft(cid: str, task_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Copy the AI draft into the campaign as a draft. It still has to be
    approved and turned on by a person."""
    _manager(c)
    s = _settings(db, c)
    camp = _camp(db, c, cid)
    t = db.get(EvoSysAITask, task_id)
    if t is None or t.target_id != camp.id or t.kind != "draft_campaign" or t.status == "failed":
        raise HTTPException(404, "Draft not found.")
    steps = (t.output or {}).get("steps") or []
    if not steps:
        raise HTTPException(409, "This draft has no steps.")
    CA.save(db, camp, {"steps": steps}, c.user.id, origin="ai")
    AI.resolve(db, t, "accepted")
    out = _camp_out(db, s, camp, detail=True)
    db.commit()
    return out


# ── CONVERSATIONS: AI help with a reply, sending a reply ─────────────────────

def _reply_msg(db: Session, c: Ctx, mid: str) -> EvoSysOutreachMessage:
    m = db.get(EvoSysOutreachMessage, mid)
    if m is None or m.direction != "in":
        raise HTTPException(404, "Reply not found.")
    _opp(db, c, m.opportunity_id)        # access check
    return m


@router.post("/messages/{mid}/assist")
def assist(mid: str, again: bool = False, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Summarise a reply and draft an answer. Only for a reply that already
    stopped the follow-up (deterministic rules); nothing is sent."""
    s = _settings(db, c)
    m = _reply_msg(db, c, mid)
    opp = db.get(Opportunity, m.opportunity_id)
    st = db.get(EvoSysOutreachState, opp.id)
    if st is None or st.status == "active":
        raise HTTPException(409, "This lead's follow-up is still running; AI help is for replies that stopped it.")
    camp = db.get(EvoSysCampaign, st.campaign_id) if st.campaign_id else None
    if m.channel == "text":
        from app.services.evosys_sales import sms_inbound as SI
        t = AI.text_reply(db, s, camp, opp, m, SI.conversation(db, opp.id), c.user.id, force=again)
        if t.status != "failed" and (t.output or {}).get("category"):
            m.label = m.label or (t.output or {}).get("category")
        db.commit()
        return AI.payload(t)
    last_out = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id == opp.id,
                                                       EvoSysOutreachMessage.direction == "out",
                                                       EvoSysOutreachMessage.channel == "email",
                                                       EvoSysOutreachMessage.status == "sent")
                .order_by(EvoSysOutreachMessage.created_at.desc()).first())
    t = AI.reply_assist(db, s, camp, opp, m, last_out.body if last_out else "", c.user.id, force=again)
    if t.status != "failed" and (t.output or {}).get("category") in ("interested", "pricing", "question",
                                                                       "not_interested", "wrong_person", "unsubscribe"):
        m.label = m.label or (t.output or {}).get("category")
    db.commit()
    return AI.payload(t)


class LabelIn(BaseModel):
    label: str


@router.post("/messages/{mid}/label")
def label(mid: str, body: LabelIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    if body.label not in AI.REPLY_CATEGORIES:
        raise HTTPException(422, "Unknown label.")
    m = _reply_msg(db, c, mid)
    m.label = body.label
    db.commit()
    return {"ok": True, "label": m.label}


class ReplyIn(BaseModel):
    subject: str = ""
    body: str
    channel: str = "email"
    ai_task_id: Optional[str] = None
    reply_to_message_id: Optional[str] = None


@router.post("/leads/{opp_id}/send-reply")
def send_reply(opp_id: str, body: ReplyIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Send one email answer through the existing sender, with this lead's
    reply tag so their next reply is matched. Refused for Do Not Contact and
    opted-out leads. Never restarts the follow-up."""
    from app.services import sales_suppression as SDNC
    from app.services.evosys_sales import replies as R
    s = _settings(db, c)
    o = _opp(db, c, opp_id)
    st = db.get(EvoSysOutreachState, o.id)
    if (st is not None and st.opted_out_at) or SDNC.for_opportunity(db, o) or SDNC.find(db, c.bso, o.phone, o.email):
        raise HTTPException(409, "This lead asked not to be contacted.")
    if body.channel == "text":
        return _send_text_reply(db, c, s, o, st, body)
    if not O._has_email(o):
        raise HTTPException(409, "This lead has no email address.")
    if not (body.body or "").strip() or not (body.subject or "").strip():
        raise HTTPException(422, "Write a subject and a message.")
    if st is None:
        st = EvoSysOutreachState(opportunity_id=o.id, brand_sales_org_id=c.bso, status="stopped",
                                 stop_reason="manual", started_at=datetime.utcnow())
        db.add(st)
        db.flush()
    import html as _h
    html = "".join("<p>%s</p>" % _h.escape(p).replace("\n", "<br>") for p in body.body.split("\n\n") if p.strip())
    html += ('<p style="font-size:12px;color:#6b7f8c;margin-top:24px">%s<br>Not interested? '
             '<a href="%s">Stop these emails</a>.</p>' % (_h.escape(s.mailing_address or ""), O.opt_out_url(o.id)))
    msg = {"subject": body.subject.strip()[:300], "html": html, "text": body.body,
           "reply_to": R.tagged_address(s.reply_to_email or O.DEFAULT_FROM, R.ensure_code(db, st))}
    try:
        r = O._send(o.email.strip(), msg, s, o.id)
    except Exception as e:  # noqa: BLE001
        r = {"success": False, "error": str(e)}
    ok = bool(r.get("success"))
    m = EvoSysOutreachMessage(brand_sales_org_id=c.bso, opportunity_id=o.id, direction="out", channel="email",
                              to_address=o.email.strip(), subject=msg["subject"], body=body.body[:5000],
                              status="sent" if ok else "failed", error=None if ok else str(r.get("error") or "")[:1000],
                              provider_message_id=r.get("provider_message_id"), actor_user_id=c.user.id,
                              campaign_id=st.campaign_id, created_at=datetime.utcnow())
    db.add(m)
    if body.ai_task_id:
        t = db.get(EvoSysAITask, body.ai_task_id)
        if t is not None and t.kind == "reply_assist":
            same = ((t.output or {}).get("draft") or "").strip() == body.body.strip()
            AI.resolve(db, t, "accepted" if same else "edited")
    if ok:
        db.add(OpportunityEvent(opportunity_id=o.id, event_type="outreach_reply_sent", actor_user_id=c.user.id,
                                summary="Answered their reply by email", detail=msg["subject"],
                                occurred_at=datetime.utcnow()))
    db.commit()
    if not ok:
        raise HTTPException(502, "The email did not send: %s. Nothing else changed; try again." % (r.get("error") or "unknown error"))
    return {"ok": True, "message": _msg(m)}


def _send_text_reply(db: Session, c: Ctx, s, o, st, body: ReplyIn):
    """A person answers a text from the team's texting number, so the whole
    conversation stays on one number and on this lead."""
    to, text = O.e164(o.phone), (body.body or "").strip()
    if not to:
        raise HTTPException(409, "This lead has no phone number we can text.")
    if not O.e164(s.sms_from_number):
        raise HTTPException(409, "EvoSys Sales has no texting number.")
    if not text:
        raise HTTPException(422, "Write a message.")
    if len(text) > 640:
        raise HTTPException(422, "That is too long for a text (keep it under 640 characters).")
    from app.services.evosys_sales import inbox as IB
    dup = IB.duplicate_of(db, o.id, text)
    if dup is not None:
        raise HTTPException(409, "They already got this exact text (%s). Nothing was sent - change the wording "
                                 "if you want to send something new." % IB.ct(dup.created_at))
    try:
        r = O._send_text(to, text, s)
    except Exception as e:  # noqa: BLE001
        r = {"success": False, "error": str(e)}
    ok = bool(r.get("success"))
    m = EvoSysOutreachMessage(brand_sales_org_id=c.bso, opportunity_id=o.id, direction="out", channel="text",
                              to_address=to, body=text[:5000], status="sent" if ok else "failed",
                              error=None if ok else str(r.get("error") or "")[:1000],
                              provider_message_id=r.get("provider_message_id"), actor_user_id=c.user.id,
                              campaign_id=st.campaign_id if st else None, created_at=datetime.utcnow())
    db.add(m)
    if body.ai_task_id:
        t = db.get(EvoSysAITask, body.ai_task_id)
        if t is not None and t.kind == "text_reply":
            same = ((t.output or {}).get("draft") or "").strip() == text
            AI.resolve(db, t, "accepted" if same else "edited")
    if ok:
        db.add(OpportunityEvent(opportunity_id=o.id, event_type="outreach_reply_sent", actor_user_id=c.user.id,
                                summary="Answered their text", detail=text[:500], occurred_at=datetime.utcnow()))
    db.commit()
    if not ok:
        raise HTTPException(502, "The text did not send: %s. Nothing else changed; try again." % (r.get("error") or "unknown error"))
    return {"ok": True, "message": _msg(m)}


# ── APPOINTMENTS: intro calls through the existing scheduler ──────────────────

def _meeting_type(db: Session, bso: str, key: str):
    from app.models.scheduling_models import MeetingType
    return (db.query(MeetingType).filter(MeetingType.brand_sales_org_id == bso, MeetingType.key == key,
                                         MeetingType.is_active.is_(True)).first())


@router.get("/leads/{opp_id}/intro-slots")
def intro_slots(opp_id: str, date_from: Optional[date] = None, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Times the signed-in person is free for a 30-minute intro call (the
    existing Discovery meeting type), from the existing availability engine."""
    from app.routers.sales_scheduling_router import FindTimeIn, find_team_time
    o = _opp(db, c, opp_id)
    mt = _meeting_type(db, c.bso, INTRO_KEY)
    start = date_from or (datetime.utcnow().date() + timedelta(days=1))
    try:
        r = find_team_time(FindTimeIn(meeting_type_id=mt.id if mt else None,
                                      duration_minutes=None if mt else 30,
                                      required_user_ids=[c.user.id], date_from=start,
                                      date_to=start + timedelta(days=6), opportunity_id=o.id,
                                      brand_sales_org_id=c.bso), user=c.user, db=db)
    except HTTPException as e:
        return {"slots": [], "problem": e.detail, "timezone": None}
    slots = (r or {}).get("slots") or []
    return {"slots": slots[:24], "timezone": (r or {}).get("timezone"), "meeting": mt.name if mt else "Intro call",
            "duration_minutes": mt.duration_minutes if mt else 30}


class IntroIn(BaseModel):
    starts_at: datetime
    timezone: Optional[str] = None
    prospect_timezone: Optional[str] = None


@router.post("/leads/{opp_id}/intro-call")
def book_intro(opp_id: str, body: IntroIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Book the intro call. Safe to press twice: the same lead at the same time
    returns the booking already made. A taken slot or a calendar failure
    leaves the lead as it was and says what to do."""
    from app.models.scheduling_models import SalesAppointment
    from app.routers.sales_scheduling_router import BookIn, create_appointment
    o = _opp(db, c, opp_id)
    when = body.starts_at.replace(tzinfo=None) if body.starts_at.tzinfo is None else \
        body.starts_at.astimezone(__import__("datetime").timezone.utc).replace(tzinfo=None)
    prior = (db.query(SalesAppointment).filter(SalesAppointment.opportunity_id == o.id,
                                               SalesAppointment.starts_at == when,
                                               SalesAppointment.status == "scheduled").first())
    if prior is not None:
        return {"status": "booked", "already_booked": True, "appointment_id": prior.id,
                "starts_at": iso(prior.starts_at)}
    mt = _meeting_type(db, c.bso, INTRO_KEY)
    try:
        appt = create_appointment(BookIn(starts_at=body.starts_at, meeting_type_id=mt.id if mt else None,
                                         duration_minutes=None if mt else 30, opportunity_id=o.id,
                                         brand_sales_org_id=c.bso, title="Intro call - %s" % (o.company_name or ""),
                                         timezone=body.timezone, required_user_ids=[c.user.id],
                                         prospect_name=o.contact_name, prospect_email=o.email,
                                         prospect_phone=o.phone, prospect_timezone=body.prospect_timezone),
                                  user=c.user, db=db)
    except HTTPException as e:
        db.rollback()
        db.add(OpportunityEvent(opportunity_id=o.id, event_type="intro_call_failed", actor_user_id=c.user.id,
                                summary="Intro call not booked", detail=str(e.detail)[:500],
                                occurred_at=datetime.utcnow()))
        db.commit()
        if e.status_code == 409:
            return {"status": "conflict", "message": "That time was just taken or clashes with your calendar. "
                    "Pick another time - nothing was booked.", "detail": e.detail}
        return {"status": "error", "message": "The calendar didn't confirm the booking. Nothing was booked; try again.",
                "detail": e.detail}
    except Exception as e:  # noqa: BLE001
        db.rollback()
        db.add(OpportunityEvent(opportunity_id=o.id, event_type="intro_call_failed", actor_user_id=c.user.id,
                                summary="Intro call not booked", detail=type(e).__name__, occurred_at=datetime.utcnow()))
        db.commit()
        return {"status": "error", "message": "The calendar didn't confirm the booking. Nothing was booked; try again."}
    appt_id = appt.get("id") if isinstance(appt, dict) else getattr(appt, "id", None)
    O.stop(db, o.id, "booked")
    db.commit()
    a = db.get(SalesAppointment, appt_id) if appt_id else None
    return {"status": "booked", "already_booked": False, "appointment_id": appt_id,
            "starts_at": iso(a.starts_at) if a else None}


# ── SIGNED UP: hand to the existing service-company onboarding ─────────────────

@router.post("/leads/{opp_id}/signed-up")
def signed_up(opp_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """The business signed up: the existing conversion (acquisition
    onboarding.onboard with this Sales opportunity) makes or finds its
    service-company workspace. Source and campaign go with it."""
    from app.services.acquisition import onboarding as ONB
    _manager(c)
    s = _settings(db, c)
    o = _opp(db, c, opp_id)
    lead = _lead_row(db, s, o)
    st = db.get(EvoSysOutreachState, o.id)
    camp = db.get(EvoSysCampaign, st.campaign_id) if st and st.campaign_id else None
    attribution = "EvoSys Sales - source: %s; campaign: %s; group: %s" % (
        G.SOURCE_LABEL.get(lead.source_channel or "", "Other"), camp.name if camp else "none",
        G.group_label(s, lead.industry_key, lead.market_key))
    data = {"sales_opportunity_id": o.id, "name": o.company_name, "contact_phone": o.phone}
    if lead.industry_key == "commercial_cleaning":
        data["service_category"] = "cleaning"
    res = ONB.onboard(db, c.user, data)
    if isinstance(res, dict) and res.get("status") == "duplicate":
        db.commit()
        return res
    O.stop(db, o.id, "stage")
    db.add(OpportunityEvent(opportunity_id=o.id, event_type="evosys_signed_up", actor_user_id=c.user.id,
                            summary="Signed up - handed to service-company onboarding", detail=attribution,
                            occurred_at=datetime.utcnow()))
    db.commit()
    return {"status": "handed_off", "attribution": attribution,
            "organization_id": (res or {}).get("organization_id") if isinstance(res, dict) else None,
            "result": res}


# ── RESULTS by campaign / industry / market / source ──────────────────────────

@router.get("/results/breakdown")
def breakdown(by: str = "campaign", days: int = 90, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    from app.models.scheduling_models import MeetingType, SalesAppointment
    if by not in ("campaign", "industry", "market", "source"):
        raise HTTPException(422, "Group by campaign, industry, market or source.")
    s = _settings(db, c)
    since = datetime.utcnow() - timedelta(days=max(1, min(days, 365)))
    opps = _leads_q(db, c).all()
    ids = [o.id for o in opps]
    leads = {l.opportunity_id: l for l in db.query(EvoSysLead).filter(EvoSysLead.opportunity_id.in_(ids)).all()} if ids else {}
    states = {x.opportunity_id: x for x in db.query(EvoSysOutreachState).filter(EvoSysOutreachState.opportunity_id.in_(ids)).all()} if ids else {}
    camps = {x.id: x.name for x in CA.all_for(db, s)}
    msgs = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id.in_(ids),
                                                   EvoSysOutreachMessage.created_at >= since).all()) if ids else []
    mts = {m.id: m.key for m in db.query(MeetingType).filter(MeetingType.brand_sales_org_id == c.bso).all()}
    appts = (db.query(SalesAppointment).filter(SalesAppointment.brand_sales_org_id == c.bso,
                                               SalesAppointment.opportunity_id.in_(ids)).all()) if ids else []

    def key(oid):
        l = leads.get(oid)
        if by == "campaign":
            st = states.get(oid)
            return camps.get(st.campaign_id) if st and st.campaign_id else "Not in a campaign"
        if by == "industry":
            return G.label_of(G.industries(s), l.industry_key) if l and l.industry_key else "Unclear"
        if by == "market":
            return G.label_of(G.markets(s), l.market_key) if l and l.market_key else "Unclear"
        return G.SOURCE_LABEL.get(l.source_channel, "Other") if l else "Other"

    rows: Dict[str, Dict[str, int]] = {}

    def bump(k, f, n=1):
        r = rows.setdefault(k, {"leads": 0, "contacted": 0, "replied": 0, "interested": 0, "intro_booked": 0,
                                "intro_completed": 0, "demos": 0, "signed_up": 0})
        r[f] += n
    for o in opps:
        bump(key(o.id), "leads")
        if o.customer_organization_id or o.stage == "won":
            bump(key(o.id), "signed_up")
    seen = set()
    for m in msgs:
        if m.direction == "out" and m.status == "sent" and ("c", m.opportunity_id) not in seen:
            seen.add(("c", m.opportunity_id)); bump(key(m.opportunity_id), "contacted")
        if m.direction == "in" and ("r", m.opportunity_id) not in seen:
            seen.add(("r", m.opportunity_id)); bump(key(m.opportunity_id), "replied")
        if m.direction == "in" and m.label == "interested" and ("i", m.opportunity_id) not in seen:
            seen.add(("i", m.opportunity_id)); bump(key(m.opportunity_id), "interested")
    for a in appts:
        k = mts.get(a.meeting_type_id)
        if k == INTRO_KEY:
            bump(key(a.opportunity_id), "intro_booked")
            if a.status == "completed":
                bump(key(a.opportunity_id), "intro_completed")
        elif k in DEMO_KEYS:
            bump(key(a.opportunity_id), "demos")
    db.commit()
    return {"by": by, "days": days, "rows": [dict(v, name=k) for k, v in sorted(rows.items(), key=lambda kv: -kv[1]["leads"])]}


# ── TODAY: work queue and what needs a person ─────────────────────────────────

@router.get("/attention")
def attention(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Only what needs a person, each with where to go and what to do."""
    from app.models.inbound_mailbox_models import InboundMailboxMessage
    s = _settings(db, c)
    G.backfill(db, s, c.bso)
    items: List[Dict[str, Any]] = []
    opps = {o.id: o for o in _leads_q(db, c).filter(Opportunity.status == "open").all()}
    leads = [l for l in db.query(EvoSysLead).filter(EvoSysLead.opportunity_id.in_(list(opps))).all()] if opps else []

    def add(kind, title, text, action, lead_id=None, campaign_id=None, count=None):
        items.append({"kind": kind, "title": title, "text": text, "action": action, "lead_id": lead_id,
                      "campaign_id": campaign_id, "count": count})
    dups = [l for l in leads if l.review_status == "possible_duplicate"]
    if dups:
        add("possible_duplicate", "%d possible duplicate%s" % (len(dups), "" if len(dups) == 1 else "s"),
            "Probably the same business as another lead. Keep both or keep one.", "Review leads", count=len(dups))
    unclear = [l for l in leads if l.review_status == "unclear_market"]
    if unclear:
        two = sum(1 for l in unclear if "more than one market" in (l.group_reason or ""))
        add("unclear_market", "%d lead%s with an unclear market" % (len(unclear), "" if len(unclear) == 1 else "s"),
            (("%d serve%s more than one market. " % (two, "s" if two == 1 else "")) if two else "")
            + "Set the market so they can join the right campaign.", "Set markets", count=len(unclear))
    nocontact = [o for o in opps.values() if not O._has_email(o) and not (o.phone or "").strip()]
    if nocontact:
        add("no_contact", "%d lead%s with no way to reach them" % (len(nocontact), "" if len(nocontact) == 1 else "s"),
            "No email and no phone on file.", "Open leads", count=len(nocontact))
    for t in db.query(EvoSysAITask).filter(EvoSysAITask.brand_sales_org_id == c.bso,
                                           EvoSysAITask.status.in_(("failed", "needs_person")),
                                           EvoSysAITask.outcome.is_(None)).order_by(EvoSysAITask.created_at.desc()).limit(10):
        if t.status == "failed":
            add("ai_failed", "AI couldn't finish", t.error or "Try again or do it by hand.", "Open",
                lead_id=t.target_id if t.kind == "group_lead" else None,
                campaign_id=t.target_id if t.kind == "draft_campaign" else None)
        elif t.kind == "reply_assist":
            m = db.get(EvoSysOutreachMessage, t.target_id)
            if m is not None and m.opportunity_id in opps:
                why = "; ".join((t.output or {}).get("needs_person") or []) or "Needs a person."
                cat = {"pricing": "Pricing question", "wrong_person": "Wrong person", "unclear": "Unclear reply",
                       "unsubscribe": "Wants no more emails", "not_interested": "Not interested"}.get(
                    (t.output or {}).get("category"), "Reply needs you")
                add("reply_needs_person", "%s: %s" % (opps[m.opportunity_id].company_name, cat),
                    why, "Open conversation", lead_id=m.opportunity_id)
    for camp in CA.all_for(db, s):
        a = _audience(db, s, camp)
        if camp.sending_on and not CA.is_approved(camp, s):
            add("campaign_unapproved", "%s needs approving" % camp.name,
                "Something changed after it was approved, so nothing is sending.", "Open campaign", campaign_id=camp.id)
        elif camp.sending_on and CA.problems(camp, s):
            add("campaign_blocked", "%s can't send" % camp.name, "; ".join(CA.problems(camp, s)), "Open campaign",
                campaign_id=camp.id)
        if a["waiting"] and not camp.sending_on:
            add("campaign_paused", "%s is paused with emails waiting" % camp.name,
                "%d email%s will go out when it is turned on." % (a["waiting"], "" if a["waiting"] == 1 else "s"),
                "Open campaign", campaign_id=camp.id, count=a["waiting"])
    from app.routers.evosys_sales_router import _reply_tracking
    rt = _reply_tracking(db, s)
    if not rt.get("automatic"):
        add("sender_disconnected", "Replies aren't being read", rt.get("text"), "Open Outreach")
    since = datetime.utcnow() - timedelta(days=14)
    for st in db.query(EvoSysOutreachState).filter(EvoSysOutreachState.brand_sales_org_id == c.bso,
                                                   EvoSysOutreachState.stop_reason == "email_failed",
                                                   EvoSysOutreachState.stopped_at >= since).limit(10):
        if st.opportunity_id in opps:
            add("bounce", "%s: email bounced" % opps[st.opportunity_id].company_name,
                "Their email didn't deliver twice, so the follow-up stopped. Find a better address or call.",
                "Open lead", lead_id=st.opportunity_id)
    fails = (db.query(OpportunityEvent).filter(OpportunityEvent.event_type == "intro_call_failed",
                                               OpportunityEvent.occurred_at >= since,
                                               OpportunityEvent.opportunity_id.in_(list(opps))).all()) if opps else []
    from app.models.scheduling_models import SalesAppointment
    for e in fails:
        later = db.query(SalesAppointment.id).filter(SalesAppointment.opportunity_id == e.opportunity_id,
                                                     SalesAppointment.created_at >= e.occurred_at).first()
        if not later:
            add("booking_issue", "%s: intro call not booked" % opps[e.opportunity_id].company_name,
                "The time was taken or the calendar didn't confirm. Pick another time.", "Book intro call",
                lead_id=e.opportunity_id)
    amb = (db.query(InboundMailboxMessage).filter(InboundMailboxMessage.outcome.in_(("evs_ambiguous", "evs_unknown_tag")),
                                                  InboundMailboxMessage.received_at >= since).all())
    if amb:
        add("unmatched_reply", "%d repl%s we couldn't match to one lead" % (len(amb), "y" if len(amb) == 1 else "ies"),
            "Not attached to anyone. Read them in the support@evosyspro.live inbox and log them on the right lead.",
            "See details", count=len(amb))
    db.commit()
    return {"items": items, "count": len(items)}
