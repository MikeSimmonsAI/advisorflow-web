"""The roofing inbox: who needs what, first.

PRIORITY ORDER (first matching wins, then newest reply first):
  1 needs_human    the conversation engine flagged it for a person, or an unread
                   reply asks a question / asks for a call back
  2 ready_to_book  an unread or latest reply says yes and no inspection is booked
  3 new_reply      any other unread reply
  4 booked         an inspection is on the calendar (prepare / confirm)
  5 in_progress    read, still open, waiting on the homeowner or the AI
  6 opted_out      replied STOP or marked do-not-contact - shown, never worked

Built only from rows that exist (Reply, ConversationState, BookingLink,
RoofingOpportunity). Nothing is inferred into a reply that was not received.
"""
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.conversation_models import ConversationState
from app.models.models import Lead, Reply, ReplyClassification
from app.services import lead_scope
from app.services.roofing import common as C
from app.services.roofing import journey as J

BUCKETS = [
    ("needs_human", "Needs you", "A person should answer this one"),
    ("ready_to_book", "Ready to book", "Said yes - offer inspection times"),
    ("new_reply", "New replies", "Unread"),
    ("booked", "Inspection booked", "On the calendar"),
    ("in_progress", "In progress", "Waiting on the homeowner or the AI"),
    ("opted_out", "Opted out", "Do not contact"),
]
RANK = {k: i for i, (k, _, _) in enumerate(BUCKETS)}
HUMAN_PRIORITIES = ("respond_now", "needs_human")
HUMAN_MODES = ("human_active", "waiting_on_human")
ASKING = (ReplyClassification.QUESTION, ReplyClassification.CALLBACK)


def _cls(r: Reply) -> Optional[str]:
    c = r.classification
    return getattr(c, "value", c)


def bucket_for(lead: Lead, replies: List[Reply], state: Optional[ConversationState],
               opp_stage: str, booked: bool) -> str:
    unread = [r for r in replies if r.reviewed_at is None]
    latest = replies[-1] if replies else None
    if lead.status == "dnc" or any(r.classification == ReplyClassification.DNC for r in replies):
        return "opted_out"
    if opp_stage in (C.WON, C.LOST):
        return "in_progress"
    if state is not None and (state.priority in HUMAN_PRIORITIES or state.mode in HUMAN_MODES) and unread:
        return "needs_human"
    if any(r.classification in ASKING for r in unread):
        return "needs_human"
    if not booked and ((unread and any(r.classification == ReplyClassification.INTERESTED for r in unread))
                       or (latest is not None and latest.classification == ReplyClassification.INTERESTED
                           and C.RANK.get(opp_stage, 0) < C.RANK[C.OFFERED])):
        return "ready_to_book"
    if unread:
        return "new_reply"
    if booked:
        return "booked"
    return "in_progress"


def build(db: Session, ctx: C.Ctx, bucket: Optional[str] = None, q: Optional[str] = None,
          limit: int = 300) -> Dict:
    lead_ids_q = lead_scope.authorized_lead_query(db, ctx.user, Lead.id, request=ctx.request)
    replied_ids = [r[0] for r in db.query(Reply.lead_id).filter(Reply.lead_id.in_(lead_ids_q.subquery().select()))
                   .distinct().all()]
    flagged_ids = [r[0] for r in db.query(ConversationState.lead_id)
                   .filter(ConversationState.organization_id == ctx.org_id,
                           ConversationState.lead_id.in_(lead_ids_q.subquery().select()),
                           ConversationState.priority.in_(HUMAN_PRIORITIES)).all()]
    ids = list(dict.fromkeys(replied_ids + flagged_ids))
    leads = []
    for chunk in J._chunks(ids):
        leads.extend(lead_scope.authorized_lead_query(db, ctx.user, request=ctx.request)
                     .filter(Lead.id.in_(chunk)).all())
    sig = J.Signals(db, [l.id for l in leads])
    opps = J.sync(db, ctx.org_id, leads, sig=sig) if leads else {}
    states = {}
    for chunk in J._chunks([l.id for l in leads]):
        for s in (db.query(ConversationState).filter(ConversationState.organization_id == ctx.org_id,
                                                     ConversationState.lead_id.in_(chunk)).all()):
            states[s.lead_id] = s
    names = C.user_names(db, ctx.org_id)
    items = []
    counts = {k: 0 for k, _, _ in BUCKETS}
    needle = (q or "").strip().lower()
    for l in leads:
        reps = sig.replies.get(l.id, [])
        b = sig.booked(l.id)
        opp = opps[l.id]
        st = states.get(l.id)
        bk = bucket_for(l, reps, st, opp.stage, bool(b))
        counts[bk] += 1
        if bucket and bk != bucket:
            continue
        if needle and needle not in " ".join(filter(None, [C.lead_name(l), l.phone, l.email,
                                                            l.street_address, l.city])).lower():
            continue
        latest = reps[-1] if reps else None
        f = C.lead_fields(l)
        items.append({
            "lead_id": l.id, "name": C.lead_name(l), "phone": l.phone, "email": l.email,
            "address": C.address(l), "bucket": bk, "bucket_rank": RANK[bk],
            "unread": sum(1 for r in reps if r.reviewed_at is None),
            "unread_reply_ids": [r.id for r in reps if r.reviewed_at is None],
            "latest_reply": latest.body[:240] if latest else None,
            "latest_reply_at": C.iso(latest.received_at) if latest else None,
            "latest_classification": _cls(latest) if latest else None,
            "channel": (latest.source if latest else None),
            "stage": opp.stage, "stage_label": C.STAGE_LABEL[opp.stage],
            "mode": st.mode if st else None, "needs_human_reason": st.needs_human_reason if st else None,
            "owner_id": l.assigned_to_id, "owner_name": names.get(l.assigned_to_id),
            "inspection_at": b.booked_time.isoformat() if b else None,
            "roof_issue": f.get("roof_issue"), "urgency": f.get("urgency"),
            "is_test": bool(l.is_test),
        })
    items.sort(key=lambda it: (it["bucket_rank"], -(datetime.fromisoformat(it["latest_reply_at"][:-1]).timestamp()
                                                    if it["latest_reply_at"] else 0)))
    return {"buckets": [{"key": k, "label": lab, "hint": h, "count": counts[k]} for k, lab, h in BUCKETS],
            "items": items[:limit], "total": len(items)}
