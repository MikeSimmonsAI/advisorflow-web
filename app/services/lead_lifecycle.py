"""SS7 — SIX QUESTIONS, ANSWERED SEPARATELY, BECAUSE THEY ARE SIX QUESTIONS.

WHAT THIS IS
------------
`lead_stage` derives ONE stage - the single "is there anything to do here"
answer a work queue needs. This derives the six INDEPENDENT dimensions a lead
actually has, because collapsing them is what produced nine competing status
vocabularies in the first place.

    SYSTEM STATE          is this record usable by the platform at all
    SALES STAGE           where the relationship has got to
    COMMUNICATION STATE   what the machine is doing about it right now
    QUALIFICATION STATE   may we contact them, per channel
    APPOINTMENT STATE     is there a meeting, and did it happen
    OUTCOME               how it ended, if it has

THE ARGUMENT FOR SEPARATING THEM
--------------------------------
`Lead.status` currently holds eleven values drawn from four of those six
dimensions at once:

    new, queued, sent           communication state
    replied, hot                sales stage
    cold, not_interested        outcome
    booked                      appointment state
    dnc, dead                   system state
    needs_tier_review           a workflow flag that is none of the above

A single column cannot hold two of those simultaneously, so every writer has
to destroy information to write to it. A lead who booked an appointment and
then went on the do-not-contact list can be `booked` or `dnc`, not both - and
whichever is written second erases the other. That is not a naming problem
that a better vocabulary fixes. It is a shape problem, and the shape is six
fields, not one.

77 write sites across 31 files and 124 read sites across 44 files depend on
that column. So this module does not touch it. It derives beside it, exactly
as `lead_stage` does, and carries a COMPATIBILITY MAPPING in both directions
so a screen can move one at a time.

READ-ONLY. Writes nothing, stores nothing, adds no column. AST-asserted.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.services import lead_stage

# ── 1. SYSTEM STATE ─────────────────────────────────────────────────────────
# Is this record something the platform may act on? Nothing here is a sales
# judgement; every value is a fact about the record.
SYS_ACTIVE = "active"
SYS_DUPLICATE = "duplicate"
SYS_SUPPRESSED = "suppressed"        # the person asked us to stop
SYS_HELD = "held"                    # over plan capacity
SYS_TEST = "test"                    # a test record, not a person
SYSTEM_STATES = (SYS_ACTIVE, SYS_DUPLICATE, SYS_SUPPRESSED, SYS_HELD, SYS_TEST)

# ── 2. SALES STAGE ──────────────────────────────────────────────────────────
# The North Star, in order. A lead only moves forward through these; the other
# five dimensions carry everything that is not forward motion.
SALE_NEW = "new"                     # LEAD
SALE_CONTACTED = "contacted"         # CONTACT
SALE_ENGAGED = "engaged"             # CONVERSATION
SALE_QUALIFIED = "qualified"         # QUALIFICATION
SALE_FOLLOW_UP = "follow_up"         # FOLLOW-UP
SALE_APPOINTMENT = "appointment"     # APPOINTMENT
SALE_HANDOFF = "handoff"             # HUMAN HANDOFF
SALE_CLOSED = "closed"               # OUTCOME
SALES_STAGES = (SALE_NEW, SALE_CONTACTED, SALE_ENGAGED, SALE_QUALIFIED,
                SALE_FOLLOW_UP, SALE_APPOINTMENT, SALE_HANDOFF, SALE_CLOSED)

# ── 3. COMMUNICATION STATE ──────────────────────────────────────────────────
# What automation is doing, right now. Orthogonal to the sales stage: a lead
# can be `appointment` and `idle`, or `new` and `scheduled`.
COMM_IDLE = "idle"
COMM_SCHEDULED = "scheduled"         # a touch is queued for a future moment
COMM_DUE = "due"                     # that moment has passed
COMM_AWAITING_REPLY = "awaiting_reply"
COMM_STOPPED = "stopped"             # sequence ended, for any reason
COMMUNICATION_STATES = (COMM_IDLE, COMM_SCHEDULED, COMM_DUE,
                        COMM_AWAITING_REPLY, COMM_STOPPED)

# ── 4. QUALIFICATION STATE ──────────────────────────────────────────────────
# NOT redefined here. `qualification` owns this and is the module the send gate
# itself asks; this dimension is a pointer to that answer, per channel.
QUAL_UNKNOWN = "unknown"             # not asked (it costs a query per channel)

# ── 5. APPOINTMENT STATE ────────────────────────────────────────────────────
APPT_NONE = "none"
APPT_BOOKED = "booked"
APPT_CONFIRMED = "confirmed"
APPT_PASSED = "passed"               # the time went by; nobody has said what happened
APPT_KEPT = "kept"
APPT_RECORDED = "recorded"           # an outcome exists
APPOINTMENT_STATES = (APPT_NONE, APPT_BOOKED, APPT_CONFIRMED, APPT_PASSED,
                      APPT_KEPT, APPT_RECORDED)

# ── 6. OUTCOME ──────────────────────────────────────────────────────────────
OUT_OPEN = "open"                    # not finished. NOT the same as "no sale".
OUT_SALE = "sale"
OUT_NO_SALE = "no_sale"
OUT_NOT_INTERESTED = "not_interested"
OUT_UNREACHABLE = "unreachable"      # the sequence ran out; nobody ever answered
OUT_OPTED_OUT = "opted_out"
OUTCOMES = (OUT_OPEN, OUT_SALE, OUT_NO_SALE, OUT_NOT_INTERESTED,
            OUT_UNREACHABLE, OUT_OPTED_OUT)

DIMENSIONS = ("system_state", "sales_stage", "communication_state",
              "qualification", "appointment_state", "outcome")


# ── COMPATIBILITY, BOTH WAYS ────────────────────────────────────────────────
#
# THE TABLE THAT MAKES A MIGRATION POSSIBLE WITHOUT ONE.
#
# Each legacy value contributes to SOME dimensions and says nothing about the
# others. `None` means "this value carries no information about that
# dimension" - which is the honest reading and the whole point: `booked` was
# never a statement about whether the family had opted out.
LEGACY_TO_DIMENSIONS: Dict[str, Dict[str, Optional[str]]] = {
    "new":               {"sales_stage": SALE_NEW,
                          "communication_state": COMM_IDLE},
    "queued":            {"sales_stage": SALE_NEW,
                          "communication_state": COMM_SCHEDULED},
    "sent":              {"sales_stage": SALE_CONTACTED,
                          "communication_state": COMM_AWAITING_REPLY},
    "replied":           {"sales_stage": SALE_ENGAGED},
    "hot":               {"sales_stage": SALE_ENGAGED},
    "booked":            {"sales_stage": SALE_APPOINTMENT,
                          "appointment_state": APPT_BOOKED},
    "cold":              {"communication_state": COMM_STOPPED,
                          "outcome": OUT_UNREACHABLE},
    "not_interested":    {"communication_state": COMM_STOPPED,
                          "outcome": OUT_NOT_INTERESTED},
    "dnc":               {"system_state": SYS_SUPPRESSED,
                          "communication_state": COMM_STOPPED,
                          "outcome": OUT_OPTED_OUT},
    "dead":              {"communication_state": COMM_STOPPED,
                          "outcome": OUT_NO_SALE},
    "needs_tier_review": {},          # a workflow flag, and none of the six
}

# The reverse, for as long as `Lead.status` remains the stored column: given
# the derived dimensions, which single legacy value best represents them.
#
# ORDERED BY WHAT A WRITER WOULD DESTROY LEAST. System state first, because a
# suppression that is overwritten by a sales stage is the one error here with a
# person on the other end of it.
_LEGACY_PRECEDENCE = (
    ("system_state", SYS_SUPPRESSED, "dnc"),
    ("outcome", OUT_OPTED_OUT, "dnc"),
    ("outcome", OUT_NOT_INTERESTED, "not_interested"),
    ("outcome", OUT_NO_SALE, "dead"),
    ("outcome", OUT_UNREACHABLE, "cold"),
    ("appointment_state", APPT_RECORDED, "booked"),
    ("appointment_state", APPT_KEPT, "booked"),
    ("appointment_state", APPT_CONFIRMED, "booked"),
    ("appointment_state", APPT_BOOKED, "booked"),
    ("sales_stage", SALE_ENGAGED, "replied"),
    ("sales_stage", SALE_CONTACTED, "sent"),
    ("communication_state", COMM_SCHEDULED, "queued"),
)


def dimensions_for_legacy(status: Optional[str]) -> Dict[str, Optional[str]]:
    """What one legacy `Lead.status` value actually tells us."""
    return dict(LEGACY_TO_DIMENSIONS.get((status or "").strip().lower(), {}))


def legacy_for_dimensions(dims: Dict[str, Any]) -> str:
    """The single legacy value that loses the least, for a writer that must
    still write one. Falls back to `new`, which is what an untouched row says."""
    for key, value, legacy in _LEGACY_PRECEDENCE:
        if dims.get(key) == value:
            return legacy
    return "new"


def information_lost(dims: Dict[str, Any]) -> List[str]:
    """Which dimensions the single legacy column CANNOT carry for this lead.

    This is the argument for the migration, made per row rather than in the
    abstract: point it at a production book and it names, lead by lead, what
    the status column is currently unable to say.
    """
    legacy = legacy_for_dimensions(dims)
    carried = dimensions_for_legacy(legacy)
    lost = []
    for key in ("system_state", "sales_stage", "communication_state",
                "appointment_state", "outcome"):
        here = dims.get(key)
        if here in (None, SYS_ACTIVE, OUT_OPEN, APPT_NONE, COMM_IDLE):
            continue
        if carried.get(key) != here:
            lost.append("%s=%s" % (key, here))
    return lost


# ── DERIVATION ──────────────────────────────────────────────────────────────

class LifecycleContext(lead_stage.StageContext):
    """`lead_stage`'s loader, plus the three things the extra dimensions need.

    Subclassed rather than rewritten on purpose: the reply, cadence, pipeline,
    booking and outcome lookups are identical, and a second copy of them is how
    two modules come to disagree about the same lead.
    """

    def __init__(self, db: Session, leads: Sequence[Any], now=None):
        super().__init__(db, leads, now=now)
        from app.models.models import BookingLink, PipelineConversation
        self.confirmed: set = set()
        self.kept: set = set()
        self.handoff: set = set()
        self.sale: Dict[str, bool] = {}

        ids = [l.id for l in leads if getattr(l, "id", None)]
        if not ids:
            return

        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for (lead_id,) in (db.query(BookingLink.lead_id)
                               .filter(BookingLink.lead_id.in_(chunk),
                                       BookingLink.status == "confirmed").all()):
                self.confirmed.add(lead_id)
            for lead_id, kept, sale_at in (
                    db.query(PipelineConversation.lead_id,
                             PipelineConversation.appointment_kept_at,
                             PipelineConversation.sale_recorded_at)
                    .filter(PipelineConversation.lead_id.in_(chunk)).all()):
                if kept:
                    self.kept.add(lead_id)
                if sale_at:
                    self.sale[lead_id] = True
            try:
                from app.models.workforce_models import AIHandoff
                for (subject_id,) in (db.query(AIHandoff.subject_id)
                                      .filter(AIHandoff.subject_type == "lead",
                                              AIHandoff.subject_id.in_(chunk),
                                              AIHandoff.status.in_(
                                                  ("open", "pending",
                                                   "accepted"))).all()):
                    self.handoff.add(subject_id)
            except Exception:                                # noqa: BLE001
                # The workforce is optional in some deployments. A missing
                # handoff table means no handoffs, not a broken lifecycle.
                pass

        from app.models.models import LeadOutcome
        for i in range(0, len(ids), 500):
            for lead_id, sold in (db.query(LeadOutcome.lead_id,
                                           LeadOutcome.resulted_in_sale)
                                  .filter(LeadOutcome.lead_id.in_(ids[i:i + 500]))
                                  .all()):
                # Any recorded sale wins over a recorded non-sale.
                self.sale[lead_id] = bool(self.sale.get(lead_id) or sold)


def _system_state(lead, ctx) -> str:
    if getattr(lead, "is_duplicate", False):
        return SYS_DUPLICATE
    if getattr(lead, "is_test", False):
        return SYS_TEST
    if (getattr(lead, "status", None) or "") == "dnc":
        return SYS_SUPPRESSED
    from app.services import lead_capacity
    if lead_capacity.is_held(lead):
        return SYS_HELD
    return SYS_ACTIVE


def _appointment_state(lead, ctx) -> str:
    lid = lead.id
    if lid in ctx.has_outcome:
        return APPT_RECORDED
    if lid in ctx.kept:
        return APPT_KEPT
    if lid in ctx.booking_at:
        when = ctx.booking_at[lid]
        if when is not None and when <= ctx.now:
            return APPT_PASSED
        return APPT_CONFIRMED if lid in ctx.confirmed else APPT_BOOKED
    if (getattr(lead, "status", None) or "") == "booked":
        # Booked outside the link flow. Real, and the state has to say so.
        return APPT_BOOKED
    return APPT_NONE


def _outcome(lead, ctx) -> str:
    lid = lead.id
    status = (getattr(lead, "status", None) or "").lower()
    if status == "dnc":
        return OUT_OPTED_OUT
    if lid in ctx.has_outcome:
        return OUT_SALE if ctx.sale.get(lid) else OUT_NO_SALE
    if status == "not_interested":
        return OUT_NOT_INTERESTED
    if status == "dead":
        return OUT_NO_SALE
    if status == "cold":
        return OUT_UNREACHABLE
    return OUT_OPEN


def _communication_state(lead, ctx) -> str:
    lid = lead.id
    if lid in ctx.cadence_due:
        due = ctx.cadence_due[lid]
        return COMM_DUE if (due and due <= ctx.now) else COMM_SCHEDULED
    if lid in ctx.pipeline_next:
        nxt = ctx.pipeline_next[lid]
        return COMM_DUE if (nxt and nxt <= ctx.now) else COMM_SCHEDULED
    status = (getattr(lead, "status", None) or "").lower()
    if status in ("dnc", "dead", "cold", "not_interested"):
        return COMM_STOPPED
    last = (getattr(lead, "last_messaged_at", None)
            or getattr(lead, "last_contact_date", None))
    if last is not None:
        return COMM_AWAITING_REPLY
    return COMM_IDLE


def _sales_stage(lead, ctx, appointment: str, outcome: str) -> str:
    lid = lead.id
    if outcome != OUT_OPEN:
        return SALE_CLOSED
    if lid in ctx.handoff:
        return SALE_HANDOFF
    if appointment != APPT_NONE:
        return SALE_APPOINTMENT
    if lid in ctx.attention_reply_at:
        return SALE_ENGAGED
    status = (getattr(lead, "status", None) or "").lower()
    if status in ("replied", "hot"):
        return SALE_ENGAGED
    if lid in ctx.cadence_due or lid in ctx.pipeline_next:
        return SALE_FOLLOW_UP
    last = (getattr(lead, "last_messaged_at", None)
            or getattr(lead, "last_contact_date", None))
    if last is not None or status in ("sent",):
        return SALE_CONTACTED
    return SALE_NEW


def describe(lead, ctx: LifecycleContext) -> Dict[str, Any]:
    """All six dimensions for one lead, plus what the legacy column loses."""
    appointment = _appointment_state(lead, ctx)
    outcome = _outcome(lead, ctx)
    dims = {
        "lead_id": lead.id,
        "system_state": _system_state(lead, ctx),
        "sales_stage": _sales_stage(lead, ctx, appointment, outcome),
        "communication_state": _communication_state(lead, ctx),
        # Pointer, not a second answer. See qualification.
        "qualification": QUAL_UNKNOWN,
        "appointment_state": appointment,
        "outcome": outcome,
    }
    dims["legacy_status"] = (getattr(lead, "status", None) or "")
    dims["legacy_equivalent"] = legacy_for_dimensions(dims)
    dims["information_lost"] = information_lost(dims)
    return dims


def for_leads(db: Session, leads: Sequence[Any],
              now=None) -> Dict[str, Dict[str, Any]]:
    ctx = LifecycleContext(db, leads, now=now)
    return {l.id: describe(l, ctx) for l in leads if getattr(l, "id", None)}


def for_lead(db: Session, lead, now=None) -> Dict[str, Any]:
    return describe(lead, LifecycleContext(db, [lead], now=now))


def with_qualification(db: Session, current_user, leads: Sequence[Any],
                       channel: str = "email", request=None,
                       now=None) -> Dict[str, Dict[str, Any]]:
    """The six dimensions with the qualification one actually filled in.

    Separate entry point because it costs a scoring pass over the batch, and
    most callers of the other five do not need it. `qualification` answers it -
    nothing here re-decides eligibility.
    """
    from app.services import qualification
    out = for_leads(db, leads, now=now)
    if not leads:
        return out
    report = qualification.qualify_leads(
        db, current_user, channel=channel,
        lead_ids=[l.id for l in leads], request=request, include_leads=True)
    for d in report.get("leads", []):
        row = out.get(d["lead_id"])
        if row is not None:
            row["qualification"] = d["bucket"]
            row["qualification_channel"] = channel
            row["qualification_priority"] = d["priority"]
    return out


def migration_readiness(db: Session, leads: Sequence[Any],
                        now=None) -> Dict[str, Any]:
    """HOW MUCH IS THE ONE-COLUMN MODEL ACTUALLY COSTING, on real rows.

    Read-only. Counts the leads whose derived dimensions cannot be expressed by
    any single `Lead.status` value, and names which dimension is being lost.
    This is the number the SS7 decision should be made from - not an argument
    about vocabulary, but a count of families whose record currently cannot say
    two true things at once.
    """
    derived = for_leads(db, leads, now=now)
    lossy = {lid: row for lid, row in derived.items() if row["information_lost"]}
    per_dimension: Dict[str, int] = {}
    for row in lossy.values():
        for item in row["information_lost"]:
            key = item.split("=", 1)[0]
            per_dimension[key] = per_dimension.get(key, 0) + 1
    disagrees = [lid for lid, row in derived.items()
                 if row["legacy_status"] and row["legacy_equivalent"] != row["legacy_status"]]
    return {
        "read_only": True,
        "leads": len(derived),
        "cannot_be_expressed_in_one_column": len(lossy),
        "lost_by_dimension": per_dimension,
        "stored_status_disagrees_with_derived": len(disagrees),
        "headline": ("%d of %d leads hold state that Lead.status cannot "
                     "represent." % (len(lossy), len(derived))),
    }
