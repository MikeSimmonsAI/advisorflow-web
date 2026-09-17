"""WHERE IS THIS LEAD, REALLY - one derived answer over nine vocabularies.

THE PROBLEM THIS SOLVES
-----------------------
A lead carries nine independent status vocabularies, written by different
features at different times:

    Lead.status                 new queued sent replied hot booked dnc dead
                                needs_tier_review cold not_interested
    Lead.tier                   8 values, plus per-org tier_definitions
    Lead.engagement_temperature hot warm cold unknown
    Lead.case_status            6 values, and NO writer anywhere
    PipelineConversation.stage  10 values
    CadenceState.status         an enum of 6, a column comment saying 4, and
                                "cancelled" written by cadence_router, in neither
    Reply.classification        7 values
    BookingLink.status          5 values
    CaseFile case_status/outcome_type   9 + 8 values

None of them is wrong. Each was the right answer for the feature that added
it. But no single column answers the question a rep actually has, which is not
"what is this lead's status" - it is "is there anything for me to do here, and
what". So every screen derived its own answer, and four of them disagreed.

WHAT THIS IS, AND WHAT IT IS EMPHATICALLY NOT
---------------------------------------------
This is a READ MODEL. It derives one stage from state that already exists. It

    writes nothing,
    stores nothing,
    adds no column,
    changes no writer,
    and replaces no vocabulary.

`Lead.status` remains exactly what it is and keeps every one of its ~40 write
sites. This sits BESIDE it. That is deliberate: a migration that rewrote
Lead.status would have to be right about forty call sites on the first try,
against production rows, with no way back. A derived view can be wrong and
corrected in an afternoon, and it can be checked against production without
touching production.

THE SCOPE SEAM
--------------
This module never queries `Lead`. It is handed leads the caller has already
authorized - through `lead_scope.authorized_lead_query`, like everything else -
and it can only ever describe them. There is no argument here that can widen a
result set, because there is no lead lookup here to widen. Same one-way street
`qualification` documents:

    AUTHORIZED SCOPE  ->  DERIVED STAGE     yes
    DERIVED STAGE     ->  AUTHORIZED SCOPE  never

PRECEDENCE, NOT A STATE MACHINE
-------------------------------
The stages are ordered by what a rep should do first, and the first one that
matches wins. A booked lead with an unreviewed "interested" reply is
NEEDS_REPLY, because a person is waiting on an answer right now and the
booking is not going anywhere. This is not a lifecycle graph and nothing
transitions between these; recompute it and you get today's answer.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy.orm import Session

# ── the derived vocabulary, in precedence order ─────────────────────────────

UNWORKABLE = "unworkable"      # nothing can be done: DNC, dead, duplicate
CLOSED = "closed"              # finished: outcome recorded, or declined
NEEDS_REPLY = "needs_reply"    # a person is waiting on an answer
NEEDS_OUTCOME = "needs_outcome"  # the appointment has passed, nothing recorded
BOOKED = "booked"              # an appointment is on the books, ahead of us
IN_SEQUENCE = "in_sequence"    # automated work is scheduled and will run
AWAITING_REPLY = "awaiting_reply"  # contacted recently, nothing scheduled
STALLED = "stalled"            # contacted, nothing scheduled, nobody came back
NEW = "new"                    # never contacted

STAGES = (UNWORKABLE, CLOSED, NEEDS_REPLY, NEEDS_OUTCOME, BOOKED,
          IN_SEQUENCE, AWAITING_REPLY, STALLED, NEW)

# The four that mean "a human has something to do here". SS8's queues are
# views over exactly this set plus a channel.
ACTIONABLE = (NEEDS_REPLY, NEEDS_OUTCOME, STALLED, NEW)

STAGE_LABEL = {
    UNWORKABLE: "Unworkable",
    CLOSED: "Closed",
    NEEDS_REPLY: "Needs reply",
    NEEDS_OUTCOME: "Needs outcome",
    BOOKED: "Booked",
    IN_SEQUENCE: "In sequence",
    AWAITING_REPLY: "Awaiting reply",
    STALLED: "Stalled",
    NEW: "New",
}

# A lead contacted longer ago than this, with nothing scheduled and no reply,
# is STALLED rather than AWAITING_REPLY. Three weeks is not a rule from
# anywhere - it is a starting number, stated once so it can be argued with and
# changed in one place rather than guessed at per screen.
STALE_AFTER_DAYS = 21

# Statuses that are terminal by themselves, with the reason a rep would give.
_UNWORKABLE_STATUS = {
    "dnc": "On the do-not-contact list.",
    "dead": "Marked dead.",
}
_CLOSED_STATUS = {
    "not_interested": "Said they are not interested. Not an opt-out.",
}

# PipelineConversation.stage values that mean the conversation is over. The
# other seven mean it is live.
_PIPELINE_FINISHED = ("stopped", "completed")

# CadenceState.status values that mean touches will still fire. "cancelled"
# is written by cadence_router and is in neither the enum nor the column
# comment; it is listed here as NOT live, which is what it plainly means.
_CADENCE_LIVE = ("active",)

_BOOKING_LIVE = ("booked", "confirmed")


def _val(v):
    return getattr(v, "value", v)


def _chunks(seq: Sequence[Any], size: int = 500):
    seq = list(seq)
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


class StageContext:
    """Everything the classifier needs, loaded once for a batch of leads.

    Built this way for one reason: asking each lead its own five questions is
    five queries per lead, and the leads list asks for a hundred at a time.
    """

    def __init__(self, db: Session, leads: Sequence[Any], now: Optional[datetime] = None):
        from app.models.models import (BookingLink, CadenceState, LeadOutcome,
                                       PipelineConversation, Reply)
        from app.services import reply_classification_service as rcs

        self.now = now or datetime.utcnow()
        ids = [l.id for l in leads if getattr(l, "id", None)]

        self.attention_reply_at: Dict[str, datetime] = {}
        self.cadence_due: Dict[str, Optional[datetime]] = {}
        self.pipeline_next: Dict[str, Optional[datetime]] = {}
        self.booking_at: Dict[str, Optional[datetime]] = {}
        self.has_outcome: set = set()

        if not ids:
            return

        for chunk in _chunks(ids):
            # Replies still waiting on a person - the ONE definition, from the
            # module that owns the reply vocabulary.
            for lead_id, at in (db.query(Reply.lead_id, Reply.received_at)
                                .filter(Reply.lead_id.in_(chunk),
                                        *rcs.attention_filters())
                                .all()):
                prev = self.attention_reply_at.get(lead_id)
                if prev is None or (at and at > prev):
                    self.attention_reply_at[lead_id] = at

            for lead_id, due in (db.query(CadenceState.lead_id,
                                          CadenceState.next_touch_due_at)
                                 .filter(CadenceState.lead_id.in_(chunk),
                                         CadenceState.status.in_(_CADENCE_LIVE))
                                 .all()):
                self.cadence_due[lead_id] = due

            for lead_id, nxt in (db.query(PipelineConversation.lead_id,
                                          PipelineConversation.next_send_at)
                                 .filter(PipelineConversation.lead_id.in_(chunk),
                                         PipelineConversation.paused == False,  # noqa: E712
                                         ~PipelineConversation.stage.in_(_PIPELINE_FINISHED))
                                 .all()):
                self.pipeline_next[lead_id] = nxt

            for lead_id, when in (db.query(BookingLink.lead_id, BookingLink.booked_time)
                                  .filter(BookingLink.lead_id.in_(chunk),
                                          BookingLink.status.in_(_BOOKING_LIVE))
                                  .all()):
                prev = self.booking_at.get(lead_id, "missing")
                if prev == "missing" or (when and (prev is None or when > prev)):
                    self.booking_at[lead_id] = when

            for (lead_id,) in (db.query(LeadOutcome.lead_id)
                               .filter(LeadOutcome.lead_id.in_(chunk))
                               .all()):
                self.has_outcome.add(lead_id)


def classify(lead, ctx: StageContext) -> Dict[str, Any]:
    """One lead's derived stage, why, and what is next.

    `reason` is written for a rep to read, not for a log. `due_at` is the
    moment this becomes work, where the state knows one - that is what lets a
    queue sort by urgency without inventing its own clock.
    """
    now = ctx.now
    lid = lead.id
    status = (_val(getattr(lead, "status", None)) or "").lower()

    def out(stage, reason, next_action, due_at=None, channel=None):
        return {"lead_id": lid, "stage": stage, "label": STAGE_LABEL[stage],
                "reason": reason, "next_action": next_action, "due_at": due_at,
                "channel": channel, "status": status,
                "actionable": stage in ACTIONABLE}

    # 1. NOTHING CAN BE DONE.
    if getattr(lead, "is_duplicate", False):
        return out(UNWORKABLE, "Marked as a duplicate of another record.",
                   "Merge or dismiss it.")
    if status in _UNWORKABLE_STATUS:
        return out(UNWORKABLE, _UNWORKABLE_STATUS[status], "Nothing. Leave it alone.")

    # 2. FINISHED.
    if lid in ctx.has_outcome:
        return out(CLOSED, "An outcome has been recorded.", "Nothing outstanding.")
    if status in _CLOSED_STATUS:
        return out(CLOSED, _CLOSED_STATUS[status], "Nothing, unless they come back.")

    # 3. SOMEBODY IS WAITING ON US. This outranks the booking on purpose.
    at = ctx.attention_reply_at.get(lid)
    if at is not None or lid in ctx.attention_reply_at:
        return out(NEEDS_REPLY, "They replied and nobody has worked it yet.",
                   "Read the reply and answer it.", due_at=at, channel="sms")

    # 4/5. THE APPOINTMENT.
    if lid in ctx.booking_at:
        when = ctx.booking_at[lid]
        if when is not None and when <= now:
            return out(NEEDS_OUTCOME, "The appointment time has passed and no "
                       "outcome is recorded.", "Record what happened.", due_at=when)
        return out(BOOKED, "An appointment is booked.",
                   "Keep it. Nothing to do until then.", due_at=when)
    if status == "booked":
        # Status says booked and no live booking row backs it. Real: bookings
        # made outside the booking-link flow land here.
        return out(NEEDS_OUTCOME, "Marked booked, with no booking on file and "
                   "no outcome recorded.", "Confirm it happened and record the outcome.")

    # 6. AUTOMATED WORK IS SCHEDULED.
    due = ctx.cadence_due.get(lid)
    if lid in ctx.cadence_due:
        return out(IN_SEQUENCE, "A cadence is running.",
                   "Touch is due." if (due and due <= now) else "Waiting on the next touch.",
                   due_at=due, channel="sms")
    nxt = ctx.pipeline_next.get(lid)
    if lid in ctx.pipeline_next:
        return out(IN_SEQUENCE, "An AI conversation is running.",
                   "Send is due." if (nxt and nxt <= now) else "Waiting on the next send.",
                   due_at=nxt, channel="email")

    # 7/8/9. CONTACTED, OR NOT.
    last = getattr(lead, "last_messaged_at", None) or getattr(lead, "last_contact_date", None)
    if status in ("new", "") and not last:
        return out(NEW, "Never contacted.", "Start the first touch.")
    if status == "cold":
        return out(STALLED, "The sequence ran out of touches and stopped.",
                   "Decide: re-engage, or close it.", due_at=last)
    if last is None:
        return out(NEW, "No outbound contact recorded.", "Start the first touch.")
    if last <= now - timedelta(days=STALE_AFTER_DAYS):
        return out(STALLED, "Last contacted %d days ago, with nothing scheduled."
                   % (now - last).days, "Decide: re-engage, or close it.", due_at=last)
    return out(AWAITING_REPLY, "Contacted, nothing scheduled, no reply yet.",
               "Wait, or follow up.", due_at=last)


def for_leads(db: Session, leads: Sequence[Any],
              now: Optional[datetime] = None) -> Dict[str, Dict[str, Any]]:
    """Derived stage for a batch of ALREADY AUTHORIZED leads, keyed by lead id."""
    ctx = StageContext(db, leads, now=now)
    return {l.id: classify(l, ctx) for l in leads if getattr(l, "id", None)}


def for_lead(db: Session, lead, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Derived stage for one already-authorized lead."""
    return classify(lead, StageContext(db, [lead], now=now))


def counts(db: Session, leads: Sequence[Any],
           now: Optional[datetime] = None) -> Dict[str, int]:
    """How many of these leads sit in each stage. Every stage is a key, so a
    caller never has to decide whether a missing key means zero."""
    derived = for_leads(db, leads, now=now)
    tally = {s: 0 for s in STAGES}
    for row in derived.values():
        tally[row["stage"]] += 1
    return tally


def disagreements(db: Session, leads: Sequence[Any],
                  now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Where `Lead.status` and the derived stage tell different stories.

    THIS IS THE POINT OF SHIPPING THE READ MODEL FIRST. Before anything is
    migrated, this can be pointed at a production book and asked how far apart
    the stored status and the real state have drifted - with no write, no
    migration and nothing to roll back. A large number here is the argument
    for the reconciliation; a small one is the argument against it.
    """
    expected = {
        "new": (NEW,),
        "queued": (NEW, IN_SEQUENCE, AWAITING_REPLY),
        "sent": (IN_SEQUENCE, AWAITING_REPLY, STALLED),
        "replied": (NEEDS_REPLY, AWAITING_REPLY, STALLED, CLOSED),
        "hot": (NEEDS_REPLY, AWAITING_REPLY, IN_SEQUENCE, STALLED),
        "booked": (BOOKED, NEEDS_OUTCOME, NEEDS_REPLY, CLOSED),
        "cold": (STALLED, CLOSED),
        "not_interested": (CLOSED, UNWORKABLE),
        "dnc": (UNWORKABLE,),
        "dead": (UNWORKABLE,),
    }
    rows = []
    for row in for_leads(db, leads, now=now).values():
        allowed = expected.get(row["status"])
        if allowed and row["stage"] not in allowed:
            rows.append({"lead_id": row["lead_id"], "status": row["status"],
                         "stage": row["stage"], "reason": row["reason"]})
    return rows
