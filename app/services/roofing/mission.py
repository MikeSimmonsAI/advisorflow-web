"""MISSION CONTROL - what the AI team is doing, what needs a person, what it produced.

Answers three questions from rows that exist, nothing estimated:

  1 What is the AI doing?   follow-up sequences running (CadenceState active),
                            touches that left today, touches due in the next 24h,
                            homeowners eligible but not yet started.
  2 What needs a person?    the inbox's top buckets (needs_human, ready_to_book,
                            new_reply), newest first, with one-click actions.
  3 What did it produce?    replies, engaged households (with denominator),
                            confirmed inspections, estimates/won only as entered.

WORKBOARD LANES (one homeowner in exactly one lane, first match wins):
  human_needed   the inbox says a person must answer
  requested      said yes / asked for times, nothing booked yet
  booked         inspection confirmed on the calendar (upcoming)
  replied        replied, read, conversation open
  working        follow-up sequence active, no reply yet
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import CadenceState, Lead
from app.services import lead_scope
from app.services.roofing import common as C
from app.services.roofing import inbox as I
from app.services.roofing import journey as J

LANES = [
    ("human_needed", "Human needed", "A person should step in"),
    ("requested", "Inspection requested", "Said yes - offer or confirm a time"),
    ("booked", "Booked", "Confirmed on the calendar"),
    ("replied", "Homeowner replied", "Conversation open"),
    ("working", "AI working", "Follow-up running, waiting on a reply"),
]


def _summary(lead_id: str, sig: J.Signals) -> Optional[str]:
    reps = sig.replies.get(lead_id, [])
    if reps:
        return "Homeowner: “%s”" % (reps[-1].body or "").strip()[:140]
    n = len(sig.outbound.get(lead_id, []))
    return ("Touch %d sent" % n) if n else None


def build(db: Session, ctx: C.Ctx, now: Optional[datetime] = None, lane_limit: int = 25) -> Dict:
    now = now or datetime.utcnow()
    leads = (lead_scope.authorized_lead_query(db, ctx.user, request=ctx.request)
             .filter(Lead.organization_id == ctx.org_id).all())
    ids = [l.id for l in leads]
    by_id = {l.id: l for l in leads}
    sig = J.Signals(db, ids)
    opps = J.sync(db, ctx.org_id, leads, now=now, sig=sig) if leads else {}
    cads: Dict[str, CadenceState] = {}
    for chunk in J._chunks(ids):
        for c in db.query(CadenceState).filter(CadenceState.lead_id.in_(chunk)).all():
            cads[c.lead_id] = c
    inbox = I.build(db, ctx, limit=10000)
    inbox_by_lead = {it["lead_id"]: it for it in inbox["items"]}
    names = C.user_names(db, ctx.org_id)

    day0 = datetime(now.year, now.month, now.day)
    sent_today = sum(1 for i in ids for t in sig.outbound.get(i, ()) if t >= day0)
    active = [c for c in cads.values() if c.status == "active"]
    due_24h = sum(1 for c in active if c.next_touch_due_at and c.next_touch_due_at.replace(tzinfo=None) <= now + timedelta(hours=24))
    contacted = [i for i in ids if sig.contacted(i)]
    engaged = [i for i in contacted if sig.engaged(i)]
    responded = [i for i in contacted if sig.replies.get(i)]
    upcoming = [(i, sig.booked(i)) for i in ids if sig.booked(i) is not None]
    upcoming = [(i, b) for i, b in upcoming if b.booked_time >= now - timedelta(hours=12)]
    confirmed_all = sum(1 for i in ids if sig.booked(i) is not None
                        or C.RANK.get(opps[i].stage, -1) >= C.RANK[C.BOOKED] and opps[i].stage != C.LOST) if ids else 0
    eligible_waiting = sum(1 for i in ids if opps[i].stage == C.QUALIFIED and i not in cads)
    replies_today = sum(1 for i in ids for r in sig.replies.get(i, ()) if r.received_at and r.received_at >= day0)
    booked_today = sum(1 for i in ids for b in sig.bookings.get(i, ())
                       if b.status in ("booked", "confirmed") and b.created_at and b.created_at >= day0)

    lanes = {k: [] for k, _, _ in LANES}
    for l in leads:
        o = opps[l.id]
        it = inbox_by_lead.get(l.id)
        b = sig.booked(l.id)
        c = cads.get(l.id)
        if o.stage in (C.WON, C.LOST) or l.status == "dnc" or (it and it["bucket"] == "opted_out"):
            continue
        if it and it["bucket"] == "needs_human":
            lane = "human_needed"
        elif it and it["bucket"] == "ready_to_book":
            lane = "requested"
        elif b is not None and b.booked_time >= now - timedelta(hours=12):
            lane = "booked"
        elif sig.replies.get(l.id) and C.RANK.get(o.stage, 0) < C.RANK[C.INSPECTED]:
            lane = "replied"
        elif c is not None and c.status in ("active", "paused"):
            lane = "working"
        else:
            continue
        f = C.lead_fields(l)
        last_reply = sig.replies.get(l.id)[-1] if sig.replies.get(l.id) else None
        lanes[lane].append({
            "lead_id": l.id, "name": C.lead_name(l), "address": C.address(l),
            "roof_issue": f.get("roof_issue"), "urgency": f.get("urgency"),
            "touch": len(sig.outbound.get(l.id, [])), "stage": o.stage, "stage_label": C.STAGE_LABEL[o.stage],
            "summary": _summary(l.id, sig),
            "last_activity_at": C.iso(max([t for t in [last_reply.received_at if last_reply else None,
                                                       (sig.outbound.get(l.id) or [None])[-1]] if t], default=None)),
            "next_touch_at": C.iso(c.next_touch_due_at) if c is not None and c.status == "active" else None,
            "inspection_at": b.booked_time.isoformat() if b else None,
            "unread": it["unread"] if it else 0,
            "unread_reply_ids": it["unread_reply_ids"] if it else [],
            "needs_human_reason": it.get("needs_human_reason") if it else None,
            "mode": (it.get("mode") if it else None) or ("human_active" if c is not None and c.status == "paused" else None),
            "follow_up": c.status if c is not None else None,
            "owner_name": names.get(l.assigned_to_id), "is_test": bool(l.is_test)})
    for k, v in lanes.items():
        if k == "booked":
            v.sort(key=lambda c: c["inspection_at"] or "")
        elif k == "working":
            v.sort(key=lambda c: c["next_touch_at"] or "9999")
        else:
            v.sort(key=lambda c: c["last_activity_at"] or "", reverse=True)

    attention = [it for it in inbox["items"] if it["bucket"] in ("needs_human", "ready_to_book", "new_reply")]
    est_open = sum(o.estimate_amount or 0 for o in opps.values() if o.stage == C.ESTIMATE_SENT)
    est_n = sum(1 for o in opps.values() if o.stage == C.ESTIMATE_SENT and o.estimate_amount)
    won_val = sum(o.won_amount or 0 for o in opps.values() if o.stage == C.WON)
    won_n = sum(1 for o in opps.values() if o.stage == C.WON)

    def pct(n, d):
        return round(100.0 * n / d, 1) if d else None

    sample = bool(getattr(ctx.org, "is_demo", False)) or (bool(leads) and all(l.is_test for l in leads))
    return {
        "as_of": C.iso(now),
        "sample_data": sample,
        "has_leads": bool(leads),
        "ai": {"sequences_running": len(active), "touches_sent_today": sent_today, "touches_due_24h": due_24h,
               "eligible_not_started": eligible_waiting, "replies_today": replies_today,
               "inspections_booked_today": booked_today},
        "outcomes": {
            "total_leads": len(ids),
            "contacted": len(contacted),
            "responded": len(responded), "response_pct": pct(len(responded), len(contacted)),
            "engaged": len(engaged), "engaged_pct": pct(len(engaged), len(contacted)),
            "inspections_confirmed": confirmed_all, "booking_pct_of_engaged": pct(confirmed_all, len(engaged)),
            "upcoming_inspections": len(upcoming),
            "estimates_open": est_n, "estimate_value_open": est_open,
            "won": won_n, "won_value": won_val,
            "denominators": {"response_pct": "of contacted homeowners", "engaged_pct": "of contacted homeowners",
                             "booking_pct_of_engaged": "of engaged homeowners"},
        },
        "lanes": [{"key": k, "label": lab, "hint": h, "count": len(lanes[k]), "cards": lanes[k][:lane_limit]}
                  for k, lab, h in LANES],
        "attention": attention[:12],
        "attention_total": len(attention),
        "inbox_buckets": inbox["buckets"],
    }


def playbook(db: Session, ctx: C.Ctx) -> Dict:
    """The roofing follow-up sequence as a running campaign, with its results."""
    from app.models.models import CadenceTemplate, CadenceTemplateTouch
    from app.services.cadence_service import org_sending_enabled
    t = (db.query(CadenceTemplate).filter(CadenceTemplate.organization_id == ctx.org_id,
                                          CadenceTemplate.name == C.SEQUENCE_NAME).first())
    touches = []
    if t is not None:
        touches = [{"touch": x.touch_number, "day": x.day_offset, "channel": x.channel,
                    "subject": x.subject_template, "text": x.message_template}
                   for x in db.query(CadenceTemplateTouch).filter(CadenceTemplateTouch.template_id == t.id)
                   .order_by(CadenceTemplateTouch.touch_number.asc()).all()]
    leads = (lead_scope.authorized_lead_query(db, ctx.user, request=ctx.request)
             .filter(Lead.organization_id == ctx.org_id).all())
    ids = [l.id for l in leads]
    enrolled = set()
    statuses = {}
    for chunk in J._chunks(ids):
        for c in db.query(CadenceState).filter(CadenceState.lead_id.in_(chunk)).all():
            enrolled.add(c.lead_id)
            statuses[c.status] = statuses.get(c.status, 0) + 1
    sig = J.Signals(db, list(enrolled))
    replied = sum(1 for i in enrolled if sig.engaged(i))
    opted = sum(1 for i in enrolled if sig.opted_out(i))
    booked = sum(1 for i in enrolled if sig.booked(i))
    on = org_sending_enabled(db, ctx.org_id)
    return {
        "active": {"name": C.SEQUENCE_NAME, "exists": t is not None, "is_default": bool(t and t.is_default),
                   "goal": "Turn existing leads into booked roof inspections",
                   "audience": "Homeowners the qualification engine calls ready to text",
                   "texts_on": on, "touches": touches,
                   "stop_rules": ["Stops when the homeowner replies", "Stops on STOP / opt-out",
                                  "Stops when an inspection is booked", "Never texts test records or DNC"],
                   "enrolled": len(enrolled), "by_status": statuses, "engaged": replied, "opted_out": opted,
                   "booked": booked},
        # Playbooks the handoff lists. They are NOT running: the cadence engine
        # runs one default sequence per company today, so these are shown as
        # available, never as active, and never with results.
        "available": [
            {"key": "old_estimate", "name": "Old estimate follow-up",
             "goal": "Reconnect with homeowners who got an estimate but never decided"},
            {"key": "stalled_inspection", "name": "Previous inspection interest",
             "goal": "Re-offer times to homeowners whose inspection request stalled"},
            {"key": "repair_vs_replace", "name": "Repair vs replacement inquiry",
             "goal": "Ask two routing questions, then offer the right inspection"},
            {"key": "storm_inquiries", "name": "Storm-related inquiries (your own list)",
             "goal": "Follow up with homeowners who already asked about storm damage"},
            {"key": "past_customer", "name": "Past customer check-in",
             "goal": "Check in and ask for referrals, through approved messaging only"},
        ],
        "available_note": "Available playbooks run one at a time today: the follow-up engine sends one sequence "
                          "per company. Selecting a different playbook per list is the remaining integration.",
    }
