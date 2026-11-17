"""EvoSys Business Development campaigns (PR #663).

A campaign is a reach-out built around actual work: an event Mike went to,
a relationship, a project, an offer, an industry or a market. It has its own
audience, messages, approval and On/Off switch.

Rules this module owns (the send pass in outreach.py enforces them again
right before every email):

* A contact is in at most ONE campaign at a time. Their follow-up row
  (EvoSysOutreachState, one per lead) names the campaign. Adding someone who is
  already being emailed by another campaign is refused and reported, never
  doubled.
* New campaigns start Off. Approval is of an exact version (a hash of the
  messages, offer, booking link and the team's sender and mailing address).
  Approval alone sends nothing. A material change clears the approval and
  switches the campaign off until it is approved and switched on again.
* A message only uses facts that are saved on the lead. A warm first message
  that mentions an event, a referrer or a personal note is held for review
  for any contact who has no such fact - it is never filled with something
  invented.
* Opt-outs, Do Not Contact, replies, bookings and the stage rules always win.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.evosys_sales_models import (
    EvoSysCampaign, EvoSysOutreachMessage, EvoSysOutreachSettings, EvoSysOutreachState,
)
from app.models.sales_models import Opportunity, OpportunityEvent

KINDS = ("event", "relationship", "project", "offer", "industry", "market", "list")
KIND_LABELS = {"event": "Event", "relationship": "Relationship / referral", "project": "Project",
               "offer": "Offer", "industry": "Industry", "market": "Market", "list": "List"}

# Where a contact came from. `source_system` holds the key; older rows that
# only have free text are read through `source_key`.
SOURCES = [
    {"key": "networking", "label": "Networking cards"},
    {"key": "referral", "label": "Referrals"},
    {"key": "facebook", "label": "Facebook ads / groups"},
    {"key": "tiktok", "label": "TikTok"},
    {"key": "google", "label": "Google"},
    {"key": "instagram", "label": "Instagram / Meta"},
    {"key": "website", "label": "Website / demo requests"},
    {"key": "cold_email", "label": "Cold-email research"},
    {"key": "other", "label": "Other / existing lists"},
]
SOURCE_LABELS = {s["key"]: s["label"] for s in SOURCES}

WARM_STEPS = [
    {"day": 0, "channel": "email", "subject": "Good meeting you, {first_name}",
     "body": ("Hey {first_name}, Mike with EvoSys Pro. Good meeting you at {event}. "
              "I'd like to hear more about what you're working on at {company}.\n\n"
              "Open to a quick call next week? Pick any time that works: {booking_link}\n\n"
              "{sender_name}\nEvoSys Pro")},
    {"day": 4, "channel": "email", "subject": "Re: Good meeting you, {first_name}",
     "body": ("Hi {first_name},\n\nFollowing up in case this got buried. Still happy to grab "
              "15 minutes and hear what you're building: {booking_link}\n\n{sender_name}")},
    {"day": 10, "channel": "email", "subject": "Staying in touch",
     "body": ("Hi {first_name},\n\nI'll leave it here for now. If a call would help down the road, "
              "you can grab a time anytime: {booking_link}\n\n{sender_name}")},
]
REFERRAL_STEPS = [
    {"day": 0, "channel": "email", "subject": "{referrer} suggested we talk",
     "body": ("Hi {first_name}, Mike with EvoSys Pro. {referrer} suggested I reach out. "
              "I'd like to hear more about {company} and see if there's a way we can help.\n\n"
              "Open to a quick call? {booking_link}\n\n{sender_name}\nEvoSys Pro")},
    {"day": 4, "channel": "email", "subject": "Re: {referrer} suggested we talk",
     "body": ("Hi {first_name},\n\nFollowing up on my note. 15 minutes is plenty: {booking_link}\n\n"
              "{sender_name}")},
]
RESEARCHED_STEPS = [
    {"day": 0, "channel": "email", "subject": "More customers for {company}",
     "body": ("Hi {first_name},\n\nI came across {company}. EvoSys Pro finds businesses near you that "
              "need what you do, reaches out to them, and books the appointments onto your calendar.\n\n"
              "{offer}\n\nWould a short call to see how it would work for {company} be useful? "
              "Pick a time here: {booking_link}\n\n{sender_name}\nEvoSys Pro")},
    {"day": 3, "channel": "email", "subject": "Re: More customers for {company}",
     "body": ("Hi {first_name},\n\nFollowing up on my note. We do the prospecting and follow-up so you "
              "can focus on the work. 15 minutes is enough to show you: {booking_link}\n\n{sender_name}")},
    {"day": 7, "channel": "email", "subject": "Booked appointments for {company}",
     "body": ("Hi {first_name},\n\nWould a few more booked appointments a month help {company}? "
              "{offer}\n\nGrab a time: {booking_link}\n\n{sender_name}")},
    {"day": 14, "channel": "email", "subject": "Should I close your file?",
     "body": ("Hi {first_name},\n\nI haven't heard back, so I'll assume now isn't the right time. "
              "If you'd like more customers later, you can book a call anytime: {booking_link}\n\n"
              "{sender_name}")},
]

PLACEHOLDERS = ("first_name", "company", "event", "referrer", "personal_note", "industry", "market",
                "offer", "booking_link", "sender_name")
# Facts that come from the contact and are never filled in when missing.
STRICT = {"event": "the event you met at", "referrer": "who referred them",
          "personal_note": "a personal note"}
_PH = re.compile(r"\{(%s)\}" % "|".join(PLACEHOLDERS))

HELD_LABELS = {
    "no_email": "No email - call instead",
    "opted_out": "Opted out",
    "do_not_contact": "Do Not Contact",
    "replied": "Already replied - answer them personally",
    "booked": "Already booked a call",
    "stage": "Moved on in the pipeline",
    "in_other_campaign": "Being emailed by another campaign",
    "duplicate_email": "Same email as another contact in outreach",
    "market_unclear": "Market not known - review",
    "market_not_eligible": "Market not cleared for outreach",
}


def held_label(reason: Optional[str]) -> Optional[str]:
    if not reason:
        return None
    if reason.startswith("missing:"):
        return "Needs review: no saved %s" % STRICT.get(reason[8:], reason[8:])
    return HELD_LABELS.get(reason, reason)


# ── sources ──────────────────────────────────────────────────────────────────

def source_key(opp: Opportunity) -> str:
    sys_ = (opp.source_system or "").lower()
    txt = (opp.source or "").lower()
    for key, words in (("facebook", ("facebook", "fb-clean", "fb ad")), ("tiktok", ("tiktok",)),
                       ("instagram", ("instagram", "meta")), ("google", ("google",)),
                       ("networking", ("networking", "business card", "card")),
                       ("referral", ("referral", "referred")),
                       ("website", ("website", "request-demo", "discovery + demo", "inbound")),
                       ("cold_email", ("cold email", "cold_email", "research"))):
        if any(w in sys_ for w in words) or (sys_ == key):
            return key
    for key, words in (("facebook", ("facebook", "fb-clean")), ("networking", ("networking",)),
                       ("referral", ("referral",)), ("website", ("website", "inbound")),
                       ("tiktok", ("tiktok",)), ("instagram", ("instagram",)), ("google", ("google",))):
        if any(w in txt for w in words):
            return key
    return "other"


def batch_label(opp: Opportunity) -> str:
    """Which collection a lead arrived in, so batches can be counted and
    reconciled (the Nov 3 hand-entered FB-CLEAN prospects, an import file,
    networking cards...)."""
    if opp.import_batch_id:
        return "Import %s" % opp.import_batch_id[:8]
    txt = opp.source or ""
    if "FB-CLEAN" in txt.upper():
        return "FB-CLEAN (entered Nov 3)"
    sk = source_key(opp)
    return SOURCE_LABELS.get(sk, "Other")


# ── facts and rendering ──────────────────────────────────────────────────────

def first_name(opp: Opportunity) -> Optional[str]:
    n = (opp.contact_name or "").strip().split()
    return n[0].title() if n else None


def facts_for(opp: Opportunity, camp: Optional[EvoSysCampaign], s: EvoSysOutreachSettings) -> Dict[str, dict]:
    """Every value a message may use, with where it came from. `None` value =
    not known. Only `event`, `referrer` and `personal_note` are held when
    missing; the rest have neutral fallbacks that claim nothing."""
    src = source_key(opp)
    out = {
        "first_name": {"value": first_name(opp), "fallback": "there", "from": "contact name"},
        "company": {"value": (opp.company_name or "").strip() or None, "fallback": "your company",
                    "from": "company"},
        "event": {"value": (opp.source_detail or "").strip() or None if src == "networking" else None,
                  "from": "event saved on the contact"},
        "referrer": {"value": (opp.source_detail or "").strip() or None if src == "referral" else None,
                     "from": "referrer saved on the contact"},
        "personal_note": {"value": (opp.intro_note or "").strip() or None, "from": "personal note"},
        "industry": {"value": (opp.industry or (camp.industry if camp else None) or "").strip() or None,
                     "fallback": "your business", "from": "industry"},
        "market": {"value": (opp.market or (camp.market if camp else None) or "").strip() or None,
                   "fallback": "your area", "from": "market"},
        "offer": {"value": real_offer(camp.offer if camp else None) or real_offer(s.offer),
                  "from": "approved offer"},
        "booking_link": {"value": ((camp.booking_url if camp else None) or s.booking_url or "").strip() or None,
                         "from": "booking link"},
        "sender_name": {"value": (s.sender_name or "").strip() or None, "from": "sender"},
    }
    return out


def render(opp: Opportunity, camp: Optional[EvoSysCampaign], s: EvoSysOutreachSettings, step: dict) -> dict:
    """Fill one step for one contact. Returns the text plus the facts it used
    and any fact it needed but the contact does not have."""
    facts = facts_for(opp, camp, s)
    used, missing = [], []

    def sub(m):
        k = m.group(1)
        f = facts[k]
        v = f.get("value")
        if v:
            if k not in [u["key"] for u in used]:
                used.append({"key": k, "value": v, "from": f["from"]})
            return v
        if k in STRICT or k == "offer":
            if k not in missing:
                missing.append(k)
            return "[%s]" % k
        if "fallback" in f:
            return f["fallback"]
        if k not in missing:
            missing.append(k)
        return "[%s]" % k

    subject = _PH.sub(sub, step.get("subject") or "")
    text = _PH.sub(sub, step.get("body") or "")
    return {"subject": subject, "text": text, "facts_used": used, "missing": missing}


def strict_missing(opp: Opportunity, camp: EvoSysCampaign, s: EvoSysOutreachSettings) -> Optional[str]:
    """The first contact fact any step needs that this contact lacks."""
    facts = facts_for(opp, camp, s)
    for st in (camp.steps or []):
        for k in _PH.findall((st.get("subject") or "") + " " + (st.get("body") or "")):
            if k in STRICT and not facts[k].get("value"):
                return "missing:" + k
    return None


# ── approval ─────────────────────────────────────────────────────────────────

def content_hash(camp: EvoSysCampaign, s: EvoSysOutreachSettings) -> str:
    payload = {"steps": camp.steps or [], "offer": (camp.offer or "").strip(),
               "booking_url": (camp.booking_url or "").strip(), "tone": camp.tone,
               "sender_name": (s.sender_name or "").strip(), "from_email": (s.from_email or "").strip(),
               "reply_to_email": (s.reply_to_email or "").strip(),
               "mailing_address": (s.mailing_address or "").strip()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def is_approved(camp: EvoSysCampaign, s: EvoSysOutreachSettings) -> bool:
    return bool(camp.approved_at and camp.approved_hash and camp.approved_hash == content_hash(camp, s))


def problems(camp: EvoSysCampaign, s: EvoSysOutreachSettings) -> List[str]:
    """What blocks launch, in plain words. Planning and manual work never
    wait on these; only switching on does."""
    out = []
    steps = camp.steps or []
    if not steps:
        out.append("Add at least one message.")
    uses_offer = any("{offer}" in ((x.get("body") or "") + (x.get("subject") or "")) for x in steps)
    if uses_offer and not real_offer(camp.offer):
        out.append("The offer isn't written yet. The messages mention it.")
    if not (camp.booking_url or s.booking_url or "").strip().lower().startswith("http"):
        out.append("Add the booking link.")
    if not (s.mailing_address or "").strip():
        out.append("Add a mailing address (Outreach settings). The law requires one in every sales email.")
    if not re.match(r"[^@\s]+@evosyspro\.live$", (s.from_email or "").strip().lower()):
        out.append("The From address must be an @evosyspro.live address.")
    if not re.match(r"[^@+\s]+@evosyspro\.live$", (s.reply_to_email or "").strip().lower()):
        out.append("Replies must go to an @evosyspro.live address, so they can be read and matched.")
    if not (s.eligible_markets or []):
        out.append("Decide which markets outreach may reach (Outreach settings). Older territory "
                   "rules conflict, so none is assumed.")
    for i, st in enumerate(steps, 1):
        if st.get("channel") == "email" and not (st.get("subject") or "").strip():
            out.append("Message %d needs a subject." % i)
        if not (st.get("body") or "").strip():
            out.append("Message %d needs text." % i)
    return out


def status_of(camp: EvoSysCampaign, s: EvoSysOutreachSettings) -> dict:
    if camp.archived_at:
        return {"key": "archived", "label": "Archived"}
    if camp.sending_on and is_approved(camp, s):
        return {"key": "on", "label": "On"}
    if camp.approved_at and not is_approved(camp, s):
        return {"key": "needs_approval", "label": "Changed - approve again"}
    if is_approved(camp, s):
        return {"key": "approved", "label": "Approved - Off"}
    return {"key": "draft", "label": "Off - not approved"}


# ── creation and editing ─────────────────────────────────────────────────────

def default_steps(kind: str, tone: str) -> List[dict]:
    if tone == "warm" and kind == "relationship":
        return [dict(x) for x in REFERRAL_STEPS]
    if tone == "warm":
        return [dict(x) for x in WARM_STEPS]
    return [dict(x) for x in RESEARCHED_STEPS]


def real_offer(text: Optional[str]) -> Optional[str]:
    """An offer someone actually wrote. The old team-level placeholder
    ("[Your offer goes here: ...]") is not an offer and is never sent."""
    t = (text or "").strip()
    return None if (not t or t.startswith("[Your offer")) else t


def create(db: Session, bso: str, user_id: str, *, name: str, kind: str = "list",
           context: Optional[str] = None, industry: Optional[str] = None, market: Optional[str] = None,
           tone: Optional[str] = None, offer: Optional[str] = None) -> EvoSysCampaign:
    kind = kind if kind in KINDS else "list"
    tone = tone if tone in ("warm", "researched") else ("warm" if kind in ("event", "relationship") else "researched")
    from app.services.evosys_sales import outreach as O
    s = O.get_settings(db, bso)
    camp = EvoSysCampaign(brand_sales_org_id=bso, name=(name or "").strip()[:120] or "New campaign",
                          kind=kind, context=(context or "").strip()[:200] or None,
                          industry=(industry or "").strip()[:80] or None,
                          market=(market or "").strip()[:80] or None, tone=tone,
                          steps=default_steps(kind, tone),
                          offer=real_offer(offer if offer is not None else s.offer),
                          booking_url=s.booking_url, sending_on=False, version=1,
                          created_by=user_id, updated_by=user_id)
    db.add(camp)
    db.flush()
    return camp


MATERIAL = ("steps", "offer", "booking_url", "tone")


def update(db: Session, camp: EvoSysCampaign, data: dict, user_id: str) -> bool:
    """Returns True when the change was material (approval cleared)."""
    from app.services.evosys_sales import outreach as O
    changed = False
    for k in ("name", "kind", "context", "industry", "market") + MATERIAL:
        if k not in data or data[k] is None:
            continue
        v = data[k]
        if k == "steps":
            v = [O._clean_step(x) for x in (v or [])][:10]
        elif isinstance(v, str):
            v = v.strip()
            if k == "kind" and v not in KINDS:
                continue
            if k == "tone" and v not in ("warm", "researched"):
                continue
            v = v or None
        if getattr(camp, k) != v:
            setattr(camp, k, v)
            if k in MATERIAL:
                changed = True
    if changed:
        camp.version = (camp.version or 1) + 1
        camp.approved_at = camp.approved_by = camp.approved_hash = None
        camp.approved_version = None
        camp.sending_on = False
    camp.updated_at, camp.updated_by = datetime.utcnow(), user_id
    db.flush()
    return changed


def approve(db: Session, camp: EvoSysCampaign, s: EvoSysOutreachSettings, user_id: str) -> None:
    camp.approved_at, camp.approved_by = datetime.utcnow(), user_id
    camp.approved_hash = content_hash(camp, s)
    camp.approved_version = camp.version
    db.flush()


def switch(db: Session, camp: EvoSysCampaign, s: EvoSysOutreachSettings, on: bool, user_id: str,
           now: Optional[datetime] = None) -> int:
    """Turn a campaign on or off. On: every eligible member starts (or carries
    on from the step they were at). Off: queued and due emails wait; nothing
    is lost. Returns how many contacts were started."""
    now = now or datetime.utcnow()
    camp.sending_on = bool(on)
    camp.switched_at, camp.switched_by = now, user_id
    started = 0
    if on:
        started = start_members(db, camp, s, now)
    db.flush()
    return started


def start_members(db: Session, camp: EvoSysCampaign, s: EvoSysOutreachSettings, now: datetime) -> int:
    n = 0
    rows = db.query(EvoSysOutreachState).filter(EvoSysOutreachState.campaign_id == camp.id,
                                                EvoSysOutreachState.status == "queued").all()
    for st in rows:
        opp = db.get(Opportunity, st.opportunity_id)
        reason = eligibility(db, opp, camp, s, st) if opp else "stage"
        st.held_reason = reason
        if reason is None:
            st.status = "active"
            st.next_due_at = now
            n += 1
    return n


# ── audience ─────────────────────────────────────────────────────────────────

def _norm_email(e: Optional[str]) -> Optional[str]:
    e = (e or "").strip().lower()
    return e if re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+$", e) else None


def _market_ok(opp: Opportunity, camp: EvoSysCampaign, s: EvoSysOutreachSettings) -> Optional[str]:
    allowed = [m.strip().lower() for m in (s.eligible_markets or []) if (m or "").strip()]
    if not allowed:
        return None              # not decided: blocks switching on, not planning
    if "any" in allowed or "anywhere" in allowed:
        return None
    m = (opp.market or camp.market or "").strip().lower()
    if not m:
        return "market_unclear"
    return None if m in allowed else "market_not_eligible"


def _was_emailed(db: Session, opp_id: str) -> bool:
    return db.query(EvoSysOutreachMessage.id).filter(EvoSysOutreachMessage.opportunity_id == opp_id,
                                                     EvoSysOutreachMessage.direction == "out",
                                                     EvoSysOutreachMessage.status == "sent").first() is not None


def eligibility(db: Session, opp: Opportunity, camp: EvoSysCampaign, s: EvoSysOutreachSettings,
                st: Optional[EvoSysOutreachState] = None) -> Optional[str]:
    """Why this contact must not be emailed by this campaign right now, or
    None. Checked when added, when the campaign is switched on, and again by
    the send pass right before each email."""
    from app.services import sales_suppression as _dnc
    from app.services.evosys_sales import outreach as O
    if st is None:
        st = db.get(EvoSysOutreachState, opp.id)
    if st is not None and st.opted_out_at:
        return "opted_out"
    if _dnc.for_opportunity(db, opp):
        return "do_not_contact"
    if st is not None and st.replied_at:
        return "replied"
    if opp.stage not in O.WORKING_STAGES:
        return "stage"
    if O._booked(db, opp.id):
        return "booked"
    if st is not None and st.campaign_id and st.campaign_id != camp.id and st.status in ("active", "queued"):
        return "in_other_campaign"
    email = _norm_email(opp.email)
    if not email:
        return "no_email"
    # Another contact with the same address is already in outreach (or opted
    # out): one person must not get two sequences.
    from sqlalchemy import func
    twins = (db.query(Opportunity.id).filter(Opportunity.brand_sales_org_id == opp.brand_sales_org_id,
                                             Opportunity.id != opp.id,
                                             func.lower(func.trim(Opportunity.email)) == email).all())
    if twins:
        tid = [t[0] for t in twins]
        other = (db.query(EvoSysOutreachState).filter(EvoSysOutreachState.opportunity_id.in_(tid)).all())
        if any(o.opted_out_at for o in other):
            return "opted_out"
        # One of the twins goes ahead: the one already emailed, else the
        # older record. The others are held for review, never doubled.
        mine_sent = _was_emailed(db, opp.id)
        for o in other:
            if o.status not in ("active", "queued") or o.held_reason:
                continue
            if _was_emailed(db, o.opportunity_id) or (not mine_sent and o.opportunity_id < opp.id):
                return "duplicate_email"
    mk = _market_ok(opp, camp, s)
    if mk:
        return mk
    miss = strict_missing(opp, camp, s)
    if miss:
        return miss
    return None


def classify(db: Session, opp_ids: Iterable[str], camp: EvoSysCampaign, s: EvoSysOutreachSettings,
             bso: str) -> List[dict]:
    """Preview of an audience without writing anything."""
    out = []
    seen_emails = set()
    for oid in opp_ids:
        opp = db.get(Opportunity, oid)
        if opp is None or opp.brand_sales_org_id != bso:
            continue
        st = db.get(EvoSysOutreachState, opp.id)
        reason = eligibility(db, opp, camp, s, st)
        email = _norm_email(opp.email)
        if reason is None and email and email in seen_emails:
            reason = "duplicate_email"
        if email:
            seen_emails.add(email)
        already = bool(st and st.campaign_id == camp.id and st.status in ("queued", "active"))
        other_name = None
        if st and st.campaign_id and st.campaign_id != camp.id:
            oc = db.get(EvoSysCampaign, st.campaign_id)
            other_name = oc.name if oc else None
        out.append({"id": opp.id, "company": opp.company_name, "contact": opp.contact_name,
                    "email": opp.email, "market": opp.market, "source": SOURCE_LABELS.get(source_key(opp)),
                    "already_here": already, "held_reason": reason, "held_label": held_label(reason),
                    "other_campaign": other_name,
                    "will_email": reason is None})
    return out


def add_leads(db: Session, camp: EvoSysCampaign, s: EvoSysOutreachSettings, opp_ids: Iterable[str],
              bso: str, user_id: str, move: bool = False, now: Optional[datetime] = None) -> dict:
    """Put contacts in this campaign's audience. Someone being emailed by
    another campaign is never doubled: with move=True they move only if that
    other campaign is not sending to them right now."""
    now = now or datetime.utcnow()
    res = {"added": 0, "already_here": 0, "moved": 0, "held": [], "refused": []}
    seen = set()
    for oid in dict.fromkeys(opp_ids):
        opp = db.get(Opportunity, oid)
        if opp is None or opp.brand_sales_org_id != bso:
            continue
        st = db.get(EvoSysOutreachState, opp.id)
        if st and st.campaign_id == camp.id and st.status in ("queued", "active"):
            res["already_here"] += 1
            continue
        if st and st.opted_out_at:
            res["refused"].append({"id": opp.id, "company": opp.company_name, "reason": held_label("opted_out")})
            continue
        if st and st.replied_at:
            res["refused"].append({"id": opp.id, "company": opp.company_name, "reason": held_label("replied")})
            continue
        if st and st.campaign_id and st.campaign_id != camp.id and st.status in ("active", "queued"):
            other = db.get(EvoSysCampaign, st.campaign_id)
            sending_now = st.status == "active" and other is not None and other.sending_on
            if not move or sending_now:
                res["refused"].append({"id": opp.id, "company": opp.company_name,
                                       "reason": "Being emailed by \"%s\"" % (other.name if other else "another campaign")
                                       if sending_now else "In \"%s\" - choose Move to bring them here"
                                       % (other.name if other else "another campaign")})
                continue
            res["moved"] += 1
        if st is None:
            st = EvoSysOutreachState(opportunity_id=opp.id, brand_sales_org_id=bso, started_at=now)
            db.add(st)
        st.campaign_id, st.status, st.next_step, st.next_due_at = camp.id, "queued", 0, None
        st.stop_reason = st.stopped_at = None
        st.joined_at = now
        db.flush()
        reason = eligibility(db, opp, camp, s, st)
        email = _norm_email(opp.email)
        if reason is None and email and email in seen:
            reason = "duplicate_email"
        if email:
            seen.add(email)
        st.held_reason = reason
        if reason is None and camp.sending_on and is_approved(camp, s):
            st.status, st.next_due_at = "active", now
        if reason:
            res["held"].append({"id": opp.id, "company": opp.company_name, "reason": held_label(reason)})
        if not opp.bd_campaign_id:
            opp.bd_campaign_id = camp.id
        db.add(OpportunityEvent(opportunity_id=opp.id, event_type="bd_campaign_added",
                                summary="Added to campaign \"%s\"" % camp.name, actor_user_id=user_id,
                                occurred_at=now))
        res["added"] += 1
    db.flush()
    return res


def remove_lead(db: Session, camp: EvoSysCampaign, opp_id: str, user_id: str) -> bool:
    st = db.get(EvoSysOutreachState, opp_id)
    if st is None or st.campaign_id != camp.id:
        return False
    if st.status in ("queued", "active"):
        st.status, st.stop_reason, st.stopped_at, st.next_due_at = "stopped", "manual", datetime.utcnow(), None
    st.campaign_id = None
    st.held_reason = None
    db.add(OpportunityEvent(opportunity_id=opp_id, event_type="bd_campaign_removed",
                            summary="Removed from campaign \"%s\"" % camp.name, actor_user_id=user_id,
                            occurred_at=datetime.utcnow()))
    db.flush()
    return True


# ── grouping suggestions ─────────────────────────────────────────────────────

def _title(s: Optional[str]) -> str:
    return " ".join(w if w.isupper() else w.capitalize() for w in (s or "").split())


def suggest_groups(db: Session, opps: List[Opportunity]) -> List[dict]:
    """Proposed campaigns for a set of contacts: networking and referral
    contacts by the event or referrer saved on them, researched lists by
    industry and market. Rule-based so the reason is always visible."""
    groups: Dict[Tuple, dict] = {}
    for o in opps:
        sk = source_key(o)
        if sk == "networking":
            ev = (o.source_detail or "").strip()
            key = ("event", ev.lower())
            name = ("%s Follow-Up" % _title(ev)) if ev else "Networking Follow-Up"
            g = groups.setdefault(key, {"name": name, "kind": "event", "tone": "warm", "context": ev or None,
                                        "industry": None, "market": o.market, "why": "Met at the same event",
                                        "lead_ids": []})
        elif sk == "referral":
            key = ("relationship", "")
            g = groups.setdefault(key, {"name": "Partner Introductions", "kind": "relationship", "tone": "warm",
                                        "context": None, "industry": None, "market": None,
                                        "why": "Introduced by someone you know", "lead_ids": []})
        else:
            ind = _title(o.industry) or "Businesses"
            mk = (o.market or "").strip()
            key = ("industry", ind.lower(), mk.lower())
            name = "%s · %s" % (ind, mk) if mk else "%s · Market not set" % ind
            g = groups.setdefault(key, {"name": name, "kind": "industry", "tone": "researched", "context": None,
                                        "industry": ind if ind != "Businesses" else None, "market": mk or None,
                                        "why": "Same industry and market" if mk else
                                        "Same industry; their market isn't saved yet", "lead_ids": []})
        g["lead_ids"].append(o.id)
    out = sorted(groups.values(), key=lambda g: -len(g["lead_ids"]))
    for g in out:
        g["count"] = len(g["lead_ids"])
    return out


# ── the original team-wide follow-up ─────────────────────────────────────────

LEGACY_NAME = "Original follow-up"


def ensure_legacy(db: Session, bso: str) -> Optional[EvoSysCampaign]:
    """Follow-up rows created before campaigns existed (no campaign_id) keep
    their progress: they move into one campaign carrying the team's original
    messages, approval and switch. Idempotent; does nothing when there are
    none."""
    from app.services.evosys_sales import outreach as O
    orphans = (db.query(EvoSysOutreachState).filter(EvoSysOutreachState.brand_sales_org_id == bso,
                                                    EvoSysOutreachState.campaign_id.is_(None),
                                                    EvoSysOutreachState.status.in_(("active", "finished",
                                                                                    "stopped"))).all())
    orphans = [o for o in orphans if o.started_at is not None and (o.status != "stopped" or o.stop_reason != "manual"
                                                                   or o.next_step > 0)]
    if not orphans:
        return None
    s = O.get_settings(db, bso)
    camp = (db.query(EvoSysCampaign).filter(EvoSysCampaign.brand_sales_org_id == bso,
                                            EvoSysCampaign.name == LEGACY_NAME).first())
    if camp is None:
        camp = EvoSysCampaign(brand_sales_org_id=bso, name=LEGACY_NAME, kind="list", tone="researched",
                              steps=[dict(x) for x in (s.steps or [])], offer=real_offer(s.offer), booking_url=s.booking_url,
                              sending_on=bool(s.sending_on and s.approved_at), version=1,
                              created_at=s.updated_at or datetime.utcnow())
        if s.approved_at:
            camp.approved_at, camp.approved_by = s.approved_at, s.approved_by
            camp.approved_hash = content_hash(camp, s)
            camp.approved_version = 1
        db.add(camp)
        db.flush()
    for st in orphans:
        st.campaign_id = camp.id
        st.joined_at = st.joined_at or st.started_at
    db.flush()
    return camp


def migrate_messages(db: Session, bso: str) -> None:
    """Older messages without a campaign take the lead's campaign, so Results
    by campaign add up."""
    rows = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.brand_sales_org_id == bso,
                                                   EvoSysOutreachMessage.campaign_id.is_(None)).limit(2000).all())
    if not rows:
        return
    camp_of = {st.opportunity_id: st.campaign_id for st in
               db.query(EvoSysOutreachState).filter(EvoSysOutreachState.brand_sales_org_id == bso).all()}
    for m in rows:
        if camp_of.get(m.opportunity_id):
            m.campaign_id = camp_of[m.opportunity_id]
    db.flush()
