"""SS8 - FOUR OPERATIONAL QUEUES, DERIVED. NOT FOUR MORE TABLES.

WHY THIS IS A VIEW AND NOT A BUILD
----------------------------------
Six queue-ish screens exist today. Four of them read their own materialized
table - `auto_send_queue`, `pipeline_conversations`, `ai_work_items`, the
cadence counter - and every one of those tables drifts from the lead it
describes, because a row is written at one moment and the lead keeps moving
afterwards. That drift is not hypothetical: it is how one screen came to say a
lead needed a text while another said the same lead was booked.

So a fifth, sixth, seventh and eighth table is exactly the wrong answer. The
authoritative state for all four queues already exists:

    SMS        leads + suppression + replies + qualification(channel=sms)
    EMAIL      the same, channel=email - `GET /email/queue` is the naive
               version of this, filtering on Lead.status rather than on
               whether the lead is actually sendable
    VOICE      voice_calls.callback_at, call_number, outcome
    FOLLOW-UP  what `/workqueue/today` already derives, widened

Each queue here is a QUESTION ASKED OF STATE, computed fresh. Nothing is
stored, so nothing can drift.

HOW A QUEUE IS BUILT, IN ORDER
------------------------------
    1. AUTHORIZED SCOPE      lead_scope.authorized_lead_query - org, workspace,
                             and the advisor's own-records rule
    2. DERIVED STAGE         lead_stage - is there anything to do here at all
    3. QUALIFICATION         qualification.qualify_leads - may we, on THIS
                             channel, for this lead

Step 3 is the one that must never be skipped and must never be reimplemented.
`qualification` is where DNC, suppression, consent, test records, duplicates
and the per-org rules already live, and it is the module the send gate itself
uses. A queue that decided eligibility for itself would be the sixth
disagreeing answer to a question that already has one.

WHAT THIS DOES NOT DO
---------------------
It does not send. It does not enqueue. It does not write. It has no side
effect of any kind: it is a list, ordered, with a reason attached to every
row, and every action a rep takes from it goes through the existing endpoint
for that action. `EXCLUDED` leads are reported with their reasons rather than
hidden, for the same argument `qualification` makes about its own buckets - a
lead filtered out by a query can never be explained.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.models.models import Lead, User
from app.services import lead_scope, lead_stage, qualification

SMS = "sms"
EMAIL = "email"
VOICE = "voice"
FOLLOW_UP = "follow_up"
QUEUES = (SMS, EMAIL, VOICE, FOLLOW_UP)

QUEUE_LABEL = {SMS: "SMS", EMAIL: "Email", VOICE: "Voice",
               FOLLOW_UP: "Follow-up"}

DEFAULT_LIMIT = 100
MAX_LIMIT = 500

# The stages that put a lead in a per-channel outreach queue. A lead in
# IN_SEQUENCE is deliberately absent: automated work is already scheduled on
# it, and a queue that also asked a rep to touch it is how a lead gets two
# messages in an hour from two different places.
_OUTREACH_STAGES = (lead_stage.NEEDS_REPLY, lead_stage.NEW, lead_stage.STALLED)

# Sort key by urgency. A person waiting on an answer comes before a lead
# nobody has ever contacted.
_STAGE_RANK = {lead_stage.NEEDS_REPLY: 0, lead_stage.NEEDS_OUTCOME: 1,
               lead_stage.STALLED: 2, lead_stage.NEW: 3,
               lead_stage.IN_SEQUENCE: 4, lead_stage.BOOKED: 5,
               lead_stage.AWAITING_REPLY: 6, lead_stage.CLOSED: 7,
               lead_stage.UNWORKABLE: 8}

_PRIORITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, None: 3}


def _name(lead: Lead) -> str:
    n = ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip()
    return n or "Unnamed lead"


def _base(lead: Lead) -> Dict[str, Any]:
    return {"lead_id": lead.id, "name": _name(lead),
            "phone": lead.phone, "email": lead.email,
            "tier": getattr(lead.tier, "value", lead.tier),
            "status": getattr(lead.status, "value", lead.status)}


def _authorized_leads(db: Session, current_user: User, request=None,
                      limit: int = 2000) -> List[Lead]:
    """The population, and the ONLY place a lead enters any queue here."""
    return (lead_scope.authorized_lead_query(db, current_user, request=request)
            .filter(Lead.is_duplicate == False)  # noqa: E712
            .limit(limit).all())


def _qualified(db: Session, current_user: User, leads: Sequence[Lead],
               channel: str, request=None) -> Dict[str, Dict[str, Any]]:
    """Qualification decisions keyed by lead id. One call, not one per lead."""
    if not leads:
        return {}
    report = qualification.qualify_leads(
        db, current_user, channel=channel,
        lead_ids=[l.id for l in leads], request=request, include_leads=True)
    return {d["lead_id"]: d for d in report.get("leads", [])}


def _channel_queue(db: Session, current_user: User, channel: str, *,
                   request=None, limit: int = DEFAULT_LIMIT,
                   include_excluded: bool = False,
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))

    leads = _authorized_leads(db, current_user, request=request)
    stages = lead_stage.for_leads(db, leads, now=now)

    # Only leads there is actually something to do about get qualified. This
    # is the expensive step, and asking it about a closed lead is waste.
    candidates = [l for l in leads
                  if stages.get(l.id, {}).get("stage") in _OUTREACH_STAGES]
    decisions = _qualified(db, current_user, candidates, channel, request=request)

    ready, review, excluded = [], [], []
    for lead in candidates:
        st = stages[lead.id]
        d = decisions.get(lead.id)
        if d is None:
            continue
        row = dict(_base(lead))
        row.update({"stage": st["stage"], "stage_label": st["label"],
                    "reason": st["reason"], "next_action": st["next_action"],
                    "due_at": st["due_at"], "channel": channel,
                    "bucket": d["bucket"], "priority": d["priority"],
                    "score": d["score"], "blockers": d["reasons"]})
        if d["bucket"] == qualification.READY:
            ready.append(row)
        elif d["bucket"] == qualification.REVIEW:
            review.append(row)
        else:
            excluded.append(row)

    def order(rows):
        return sorted(rows, key=lambda r: (_STAGE_RANK.get(r["stage"], 9),
                                           _PRIORITY_RANK.get(r["priority"], 3),
                                           -(r["score"] or 0), r["name"]))

    ready, review, excluded = order(ready), order(review), order(excluded)
    out = {
        "queue": channel, "label": QUEUE_LABEL[channel],
        "generated_at": now, "derived": True,
        # THE HONEST COUNTS, before the page limit. A queue that reports the
        # length of its first page as its size teaches a rep to believe an
        # empty second page means empty.
        "counts": {"ready": len(ready), "review": len(review),
                   "excluded": len(excluded)},
        "items": ready[:limit],
        "review_items": review[:limit],
        "has_more": len(ready) > limit,
        # Qualification's own word on whether this channel's answer is
        # authoritative, carried through rather than restated.
        "authoritative": channel in qualification.AUTHORITATIVE_CHANNELS,
    }
    if include_excluded:
        out["excluded_items"] = excluded[:limit]
    return out


def sms_queue(db, current_user, **kw) -> Dict[str, Any]:
    return _channel_queue(db, current_user, SMS, **kw)


def email_queue(db, current_user, **kw) -> Dict[str, Any]:
    return _channel_queue(db, current_user, EMAIL, **kw)


def voice_queue(db: Session, current_user: User, *, request=None,
                limit: int = DEFAULT_LIMIT, include_excluded: bool = False,
                now: Optional[datetime] = None) -> Dict[str, Any]:
    """Calls a person owes somebody, plus leads ready for a first call.

    Two populations, and they are different questions:

      - A CALLBACK A CALLER ASKED FOR. `voice_calls.callback_at` is a promise
        made on a recorded line. It outranks everything else in this queue and
        it is not subject to the outreach-stage filter, because the promise was
        made regardless of what the lead's stage says now.
      - A LEAD WORTH CALLING. Qualified for voice, nothing scheduled, and
        under the three-attempt ceiling the dialer already enforces.
    """
    from app.models.models import VoiceCall
    now = now or datetime.utcnow()
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))

    leads = _authorized_leads(db, current_user, request=request)
    by_id = {l.id: l for l in leads}
    stages = lead_stage.for_leads(db, leads, now=now)

    # Chunked for the same reason lead_stage chunks: a book of two thousand
    # leads is one IN clause with two thousand bind parameters otherwise, and
    # SQLite has refused that at 999 for most of its life.
    ids = list(by_id)
    calls = []
    for i in range(0, len(ids), 500):
        calls.extend(db.query(VoiceCall)
                     .filter(VoiceCall.lead_id.in_(ids[i:i + 500])).all())

    attempts: Dict[str, int] = {}
    callbacks: Dict[str, datetime] = {}
    for c in calls:
        attempts[c.lead_id] = max(attempts.get(c.lead_id, 0), c.call_number or 1)
        if c.callback_at and c.outcome != "booked":
            prev = callbacks.get(c.lead_id)
            if prev is None or c.callback_at < prev:
                callbacks[c.lead_id] = c.callback_at

    due, fresh = [], []
    for lead_id, when in callbacks.items():
        lead = by_id.get(lead_id)
        if lead is None:
            continue
        st = stages.get(lead_id, {})
        row = dict(_base(lead))
        row.update({"stage": st.get("stage"), "channel": VOICE,
                    "due_at": when, "attempts": attempts.get(lead_id, 0),
                    "reason": "They asked to be called back.",
                    "next_action": "Call them back.",
                    "overdue": bool(when and when <= now)})
        due.append(row)

    candidates = [l for l in leads
                  if stages.get(l.id, {}).get("stage") in _OUTREACH_STAGES
                  and l.id not in callbacks
                  and attempts.get(l.id, 0) < 3]
    decisions = _qualified(db, current_user, candidates, qualification.CHANNEL_VOICE,
                           request=request)
    excluded = []
    for lead in candidates:
        d = decisions.get(lead.id)
        if d is None:
            continue
        st = stages[lead.id]
        row = dict(_base(lead))
        row.update({"stage": st["stage"], "channel": VOICE,
                    "due_at": st["due_at"], "attempts": attempts.get(lead.id, 0),
                    "reason": st["reason"], "next_action": st["next_action"],
                    "priority": d["priority"], "score": d["score"],
                    "bucket": d["bucket"], "blockers": d["reasons"]})
        if d["bucket"] == qualification.EXCLUDED:
            excluded.append(row)
        else:
            fresh.append(row)

    due.sort(key=lambda r: (r["due_at"] or now))
    fresh.sort(key=lambda r: (_STAGE_RANK.get(r["stage"], 9),
                              _PRIORITY_RANK.get(r["priority"], 3),
                              -(r["score"] or 0), r["name"]))
    out = {"queue": VOICE, "label": QUEUE_LABEL[VOICE], "generated_at": now,
           "derived": True,
           "counts": {"callbacks_due": len(due), "ready": len(fresh),
                      "excluded": len(excluded)},
           "callbacks": due[:limit], "items": fresh[:limit],
           "has_more": len(fresh) > limit,
           "authoritative": VOICE in qualification.AUTHORITATIVE_CHANNELS}
    if include_excluded:
        out["excluded_items"] = excluded[:limit]
    return out


def follow_up_queue(db: Session, current_user: User, *, request=None,
                    limit: int = DEFAULT_LIMIT,
                    now: Optional[datetime] = None) -> Dict[str, Any]:
    """The cross-channel one: work that is owed, whatever channel it came from.

    This is `/workqueue/today` widened and re-derived. Two differences matter.

    IT USES THE AUTHORIZED SCOPE. `/workqueue/today` filters on
    `current_user.organization_id` and `assigned_to_id == current_user.id`
    directly, so a MANAGER standing in a workspace sees only their own leads
    there and nothing their team owes. Everything here goes through
    `lead_scope.authorized_lead_query`, which answers that correctly for a rep,
    a manager and an observer alike.

    AND IT DOES NOT INVENT A DEFINITION. Every row's reason comes from
    `lead_stage`, so this queue and the lead's own detail page cannot say
    different things about the same lead.
    """
    now = now or datetime.utcnow()
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))

    leads = _authorized_leads(db, current_user, request=request)
    stages = lead_stage.for_leads(db, leads, now=now)

    owed = []
    for lead in leads:
        st = stages.get(lead.id)
        if not st:
            continue
        stage = st["stage"]
        # Work that is owed NOW: somebody waiting, an outcome missing, a
        # scheduled touch already past due.
        due_now = (stage in (lead_stage.NEEDS_REPLY, lead_stage.NEEDS_OUTCOME)
                   or (stage == lead_stage.IN_SEQUENCE and st["due_at"]
                       and st["due_at"] <= now))
        if not due_now:
            continue
        row = dict(_base(lead))
        row.update({"stage": stage, "stage_label": st["label"],
                    "reason": st["reason"], "next_action": st["next_action"],
                    "due_at": st["due_at"], "channel": st["channel"]})
        owed.append(row)

    owed.sort(key=lambda r: (_STAGE_RANK.get(r["stage"], 9),
                             r["due_at"] or now))
    buckets: Dict[str, int] = {}
    for r in owed:
        buckets[r["stage"]] = buckets.get(r["stage"], 0) + 1
    return {"queue": FOLLOW_UP, "label": QUEUE_LABEL[FOLLOW_UP],
            "generated_at": now, "derived": True,
            "counts": {"total": len(owed), "by_stage": buckets},
            "items": owed[:limit], "has_more": len(owed) > limit}


def summary(db: Session, current_user: User, *, request=None,
            now: Optional[datetime] = None) -> Dict[str, Any]:
    """Counts for all four, for a queue shell's tabs. One pass over the book."""
    now = now or datetime.utcnow()
    return {
        "generated_at": now,
        "queues": [
            {"queue": q, "label": QUEUE_LABEL[q], "counts": fn(db, current_user,
             request=request, limit=1)["counts"]}
            for q, fn in ((SMS, sms_queue), (EMAIL, email_queue),
                          (VOICE, voice_queue), (FOLLOW_UP, follow_up_queue))
        ],
    }


def get(name: str, db: Session, current_user: User, **kw) -> Dict[str, Any]:
    """Dispatch by queue name. Unknown names raise rather than returning
    something plausible - a typo'd queue that rendered an empty list would read
    as "nothing to do"."""
    fns = {SMS: sms_queue, EMAIL: email_queue, VOICE: voice_queue,
           FOLLOW_UP: follow_up_queue}
    if name not in fns:
        raise ValueError("Unknown queue: %s" % name)
    return fns[name](db, current_user, **kw)


# ── WHERE EACH QUEUE COMES FROM ─────────────────────────────────────────────
#
# STATED IN DATA, NOT IN A COMMENT, so it can be checked.
#
# The claim SS8 rests on is that these four queues need no tables of their own
# because the authoritative state already exists. That claim is only worth
# something if it is true of the code rather than of the design document, so
# this names the actual sources and a test asserts the module really does read
# them and really does not read anything else it has not declared.

PROVENANCE = {
    SMS: {
        "authoritative_state": [
            ("leads", "the population, through lead_scope.authorized_lead_query"),
            ("replies", "an unreviewed interested/callback reply is the top item"),
            ("cadence_states", "a live sequence REMOVES a lead from this queue"),
            ("suppression_entries", "via qualification, never read directly"),
            ("messages", "last contact, and the manual-takeover signal"),
        ],
        "services": ["lead_scope", "lead_stage", "qualification"],
        "materialized_table": None,
        "replaces": "nothing yet - there was no SMS queue at all",
    },
    EMAIL: {
        "authoritative_state": [
            ("leads", "the population, through lead_scope.authorized_lead_query"),
            ("email_messages", "last contact and delivery state"),
            ("replies", "an unreviewed reply outranks new outreach"),
            ("pipeline_conversations", "a live AI conversation REMOVES a lead"),
        ],
        "services": ["lead_scope", "lead_stage", "qualification"],
        "materialized_table": None,
        "replaces": ("GET /email/queue, which filters on Lead.status rather "
                     "than on whether the lead is actually sendable"),
    },
    VOICE: {
        "authoritative_state": [
            ("leads", "the population, through lead_scope.authorized_lead_query"),
            ("voice_calls.callback_at", "a promise made on a recorded line"),
            ("voice_calls.call_number", "the existing three-attempt ceiling"),
            ("voice_calls.outcome", "a booked call closes its own callback"),
        ],
        "services": ["lead_scope", "lead_stage", "qualification"],
        "materialized_table": None,
        "replaces": ("the flat list on GET /voice/calls, which is a log rather "
                     "than a queue"),
    },
    FOLLOW_UP: {
        "authoritative_state": [
            ("leads", "the population, through lead_scope.authorized_lead_query"),
            ("replies", "somebody is waiting on an answer"),
            ("booking_links", "an appointment whose time has passed"),
            ("lead_outcomes", "whether anybody recorded what happened"),
            ("cadence_states", "a touch that is already overdue"),
            ("pipeline_conversations", "a send that is already overdue"),
        ],
        "services": ["lead_scope", "lead_stage"],
        "materialized_table": None,
        "replaces": ("GET /workqueue/today, which is correct but scopes on "
                     "assigned_to_id == me, so a manager saw nothing their "
                     "team owed"),
    },
}

# The materialized tables SS8 exists to stop reading as queues. Each one is
# written at a moment and then drifts from the lead it describes, which is how
# one screen came to say a lead needed a text while another said it was booked.
SUPERSEDED_MATERIALIZED_TABLES = (
    "auto_send_queue",
    "ai_work_items",
)


def provenance(queue: Optional[str] = None) -> Dict[str, Any]:
    """What authoritative state produces each queue. Read-only, no database."""
    if queue is not None:
        if queue not in PROVENANCE:
            raise ValueError("Unknown queue: %s" % queue)
        return dict(PROVENANCE[queue])
    return {"queues": {k: dict(v) for k, v in PROVENANCE.items()},
            "superseded_materialized_tables": list(SUPERSEDED_MATERIALIZED_TABLES),
            "derived": True}
