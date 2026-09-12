"""
Appointment outcome — the authoritative record of what actually happened.

WHY THIS MODULE EXISTS
----------------------
T9 Intelligence reported appointment completion as UNKNOWN. That was not a
reporting bug; it was the truth. Nothing in the system recorded whether a
meeting took place. `sales_appointments.status` said what the calendar
INTENDED, and the only way to turn that into "it happened" was to compare
`starts_at` to `now()` — which counts every meeting nobody attended, every one
the prospect silently no-showed, and every one the rep forgot to cancel.

Fixing that in the reporting layer would have meant teaching T9 to guess
better. It is fixed here instead, at the calendar source, by asking the person
who was in the room. Everything downstream then reads a fact.

THE TWO FIELDS, AND WHY THEY ARE TWO
------------------------------------
  `status`  — lifecycle. Where the appointment sits: scheduled, completed,
              cancelled, no_show. Drives whether it still blocks calendars.
  `outcome` — verdict. What came of it: completed, no-show, follow-up
              required, proposal needed, proposal sent, won, lost, …

They are separate because "completed" and "completed, and they want a
proposal" are different facts, and collapsing them loses the one that moves
the deal. `OUTCOME_TO_STATUS` keeps them from ever contradicting each other.

NULL IS A REAL ANSWER
---------------------
An outcome is written by a human or not at all. There is no default, no
back-fill, and no scheduled job that marks old meetings complete. A past
meeting with `outcome IS NULL` means exactly "nobody has said yet", and
`pending_outcomes()` is built on that — it is the queue that makes the data
authoritative rather than merely available.

WHAT THIS MODULE WILL NOT DO
----------------------------
It does not move a deal's stage unless the caller explicitly asks AND is
authorised, and it never invents a follow-up meeting. An outcome engine that
quietly advanced pipelines would make every "Won" in the system suspect, and
the point of the whole exercise is to produce numbers a manager can trust.
"""
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.models import User
from app.models.sales_models import (
    BrandSalesOrg, Opportunity, OpportunityEvent,
    STAGE_DISCOVERY, STAGE_DEMO_BUILD, STAGE_PROPOSAL, STAGE_CLOSING,
    STAGE_WON, STAGE_LOST, STAGE_LABELS, ALL_STAGES, OPPORTUNITY_STAGES,
)
from app.models.scheduling_models import (
    SalesAppointment, AppointmentParticipant, MeetingType,
    APPT_SCHEDULED, APPT_COMPLETED, APPT_CANCELLED, APPT_NO_SHOW,
    APPOINTMENT_OUTCOMES, OUTCOME_LABELS, OUTCOME_TO_STATUS,
    OUTCOMES_OCCURRED, OUTCOMES_NOT_OCCURRED,
    OUTCOMES_REQUIRING_OPPORTUNITY,
    OUTCOME_COMPLETED, OUTCOME_NO_SHOW, OUTCOME_CANCELLED, OUTCOME_RESCHEDULED,
    OUTCOME_FOLLOW_UP, OUTCOME_PROPOSAL_NEEDED, OUTCOME_PROPOSAL_SENT,
    OUTCOME_WON, OUTCOME_LOST,
    ATTEND_UNKNOWN, ATTEND_ATTENDED, ATTEND_NO_SHOW, ATTENDANCE_STATUSES,
)

log = logging.getLogger(__name__)


class OutcomeError(Exception):
    """A refusal with a reason a salesperson can act on.

    Not an HTTPException: this module is called from a router today and could
    be called from a job tomorrow, and a service that can only fail as HTTP is
    a service that cannot be reused.
    """

    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code


# ── what a human may choose, for THIS meeting ────────────────────────────────
#
# The catalogue is per-appointment rather than global because an outcome list
# that offers impossible answers stops being read. "Won" on an internal
# pipeline review, or "Proposal sent" on a meeting with no deal attached, are
# not merely unhelpful — they are the kind of thing somebody eventually clicks.

# What each outcome is FOR, in the words a rep needs at the moment of choosing.
# Beside the vocabulary so no screen writes its own version.
OUTCOME_HINTS = {
    OUTCOME_COMPLETED:       "It happened and there is nothing outstanding.",
    OUTCOME_FOLLOW_UP:       "It happened and something is owed — set the next action.",
    OUTCOME_PROPOSAL_NEEDED: "It happened and they are waiting on a proposal.",
    OUTCOME_PROPOSAL_SENT:   "It happened and the proposal has gone out.",
    OUTCOME_WON:             "It happened and they said yes.",
    OUTCOME_LOST:            "It happened and the deal is dead.",
    OUTCOME_NO_SHOW:         "Nobody from the prospect's side attended.",
    OUTCOME_CANCELLED:       "It was called off and did not take place.",
    OUTCOME_RESCHEDULED:     "It did not take place at this time; it moved.",
}

# The stage each outcome would move a linked deal to, when the caller asks for
# it. NOT applied automatically — see `record_outcome`.
OUTCOME_SUGGESTED_STAGE = {
    OUTCOME_PROPOSAL_NEEDED: STAGE_PROPOSAL,
    OUTCOME_PROPOSAL_SENT:   STAGE_PROPOSAL,
    OUTCOME_WON:             STAGE_WON,
    OUTCOME_LOST:            STAGE_LOST,
}

# A sensible next action per outcome, offered as a default the rep can replace.
# Offered, never imposed: `record_outcome` writes a next action only when one
# is supplied.
OUTCOME_DEFAULT_NEXT_ACTION = {
    OUTCOME_FOLLOW_UP:       "Follow up after meeting",
    OUTCOME_PROPOSAL_NEEDED: "Build and send proposal",
    OUTCOME_PROPOSAL_SENT:   "Chase proposal decision",
    OUTCOME_NO_SHOW:         "Re-engage after no-show",
    OUTCOME_RESCHEDULED:     "Confirm the new time",
}


def outcome_catalog(db: Session, appt: SalesAppointment) -> dict:
    """Which outcomes are valid for this specific meeting, and why.

    Returns the full vocabulary annotated rather than a filtered list, so the
    UI can grey out an unavailable option WITH its reason instead of silently
    omitting it and leaving the rep wondering where "Won" went.
    """
    mt = (db.query(MeetingType).filter(MeetingType.id == appt.meeting_type_id).first()
          if appt.meeting_type_id else None)
    is_internal = bool(mt and mt.is_internal)
    has_opp = bool(appt.opportunity_id)

    options = []
    for key in APPOINTMENT_OUTCOMES:
        unavailable = None
        if key in OUTCOMES_REQUIRING_OPPORTUNITY and not has_opp:
            unavailable = "This meeting is not attached to a deal."
        elif is_internal and key == OUTCOME_NO_SHOW:
            # "No-show" is about the PROSPECT. On an internal meeting there is
            # no prospect to fail to arrive, and recording one would put a
            # phantom no-show into every attendance metric.
            unavailable = "This is an internal meeting — there is no prospect to no-show."
        options.append({
            "value": key,
            "label": OUTCOME_LABELS.get(key, key),
            "hint": OUTCOME_HINTS.get(key),
            "occurred": key in OUTCOMES_OCCURRED,
            "available": unavailable is None,
            "unavailable_reason": unavailable,
            "suggested_stage": OUTCOME_SUGGESTED_STAGE.get(key),
            "suggested_stage_label": STAGE_LABELS.get(OUTCOME_SUGGESTED_STAGE.get(key)),
            "default_next_action": OUTCOME_DEFAULT_NEXT_ACTION.get(key),
        })

    return {
        "appointment_id": appt.id,
        "is_internal": is_internal,
        "has_opportunity": has_opp,
        "current_outcome": appt.outcome,
        "current_outcome_label": OUTCOME_LABELS.get(appt.outcome) if appt.outcome else None,
        "options": options,
        "attendance_statuses": list(ATTENDANCE_STATUSES),
    }


# ── the write ────────────────────────────────────────────────────────────────

def _stamp_occurrence(appt: SalesAppointment, outcome: str, now: datetime) -> None:
    """Settle `status`, `occurred` and `completed_at` from the outcome.

    One place, so the three can never disagree. They did disagree in an earlier
    draft of this work — recording a no-show left `status` at "scheduled", and
    the participant rows kept blocking everybody's calendar for a meeting that
    never happened.
    """
    appt.outcome = outcome
    appt.status = OUTCOME_TO_STATUS.get(outcome, appt.status)

    if outcome in OUTCOMES_OCCURRED:
        appt.occurred = True
        appt.completed_at = appt.completed_at or now
    elif outcome in OUTCOMES_NOT_OCCURRED:
        appt.occurred = False
        # Deliberately left NULL. `completed_at` means "the meeting finished",
        # and stamping it on a no-show would make every duration and
        # attendance report count a meeting that nobody was in.
        appt.completed_at = None
    else:                                                  # pragma: no cover
        appt.occurred = None

    if outcome == OUTCOME_CANCELLED:
        appt.cancelled_at = appt.cancelled_at or now


def _release_calendar(db: Session, appt: SalesAppointment) -> None:
    """Stop a meeting that did not happen from blocking anybody's time.

    Flipping `is_blocking` off is what releases the slot for both the
    availability engine and the Postgres exclusion constraint, whose predicate
    is `WHERE (is_blocking)`. A cancelled or rescheduled meeting that keeps
    blocking is how a rep's week silently fills with ghosts.

    A COMPLETED meeting is left blocking, which is correct: it really did
    occupy that time, and releasing it would let a later booking be placed on
    top of history.
    """
    db.query(AppointmentParticipant).filter(
        AppointmentParticipant.appointment_id == appt.id).update(
        {"is_blocking": False}, synchronize_session=False)


def record_outcome(db: Session, appt: SalesAppointment, actor: User,
                   outcome: str,
                   notes: Optional[str] = None,
                   attendance: Optional[dict] = None,
                   next_action: Optional[str] = None,
                   next_action_due_at: Optional[datetime] = None,
                   advance_stage: bool = False,
                   can_edit_opportunity: bool = False,
                   now: Optional[datetime] = None) -> dict:
    """Record what happened. THE authoritative write.

    Does not commit — the caller owns the transaction, so an outcome and the
    stage move it triggers land together or not at all.

    `advance_stage` is opt-in and additionally gated on `can_edit_opportunity`,
    which the CALLER resolves from the real authorisation rules. Two flags
    rather than one because they answer different questions: did the human ask
    for this, and are they allowed to. Collapsing them would let a permitted
    user move a stage they never intended to, and an eager UI default would
    quietly rewrite pipelines.
    """
    now = now or datetime.utcnow()

    if outcome not in APPOINTMENT_OUTCOMES:
        raise OutcomeError("Unknown outcome '%s'." % outcome)

    catalog = outcome_catalog(db, appt)
    chosen = next((o for o in catalog["options"] if o["value"] == outcome), None)
    if chosen and not chosen["available"]:
        raise OutcomeError(chosen["unavailable_reason"] or
                           "That outcome does not apply to this meeting.")

    # Refuse a verdict on a meeting that has not happened yet. A rep marking
    # tomorrow's demo "completed" is either a mis-click or a habit that would
    # make the completion data worthless — which is the exact problem this
    # module exists to fix, so it is refused rather than merely discouraged.
    #
    # `cancelled` and `rescheduled` are the exceptions: both are legitimate
    # statements about a FUTURE meeting.
    if appt.starts_at > now and outcome not in (OUTCOME_CANCELLED, OUTCOME_RESCHEDULED):
        raise OutcomeError(
            "This meeting has not happened yet. Only a cancellation or a "
            "reschedule can be recorded before it starts.")

    previous = appt.outcome
    _stamp_occurrence(appt, outcome, now)

    appt.outcome_notes = (notes or "").strip() or None
    appt.outcome_recorded_at = now
    appt.outcome_recorded_by = actor.id

    if outcome in OUTCOMES_NOT_OCCURRED:
        _release_calendar(db, appt)

    # ── per-person attendance ───────────────────────────────────────────────
    # Optional and per-participant, because "the meeting happened" and "Blake
    # was in it" are different facts, and a manager coaching a rep needs the
    # second one. Only known participants can be written: an unknown user id is
    # ignored rather than trusted, so a hostile body cannot reach another
    # meeting's rows through this path.
    if attendance:
        parts = {p.user_id: p for p in db.query(AppointmentParticipant).filter(
            AppointmentParticipant.appointment_id == appt.id).all()}
        for uid, state in (attendance or {}).items():
            p = parts.get(uid)
            if p is None:
                continue
            if state not in ATTENDANCE_STATUSES:
                raise OutcomeError("Unknown attendance status '%s'." % state)
            p.attendance_status = state
    elif outcome in OUTCOMES_OCCURRED:
        # Nothing per-person was supplied. The MEETING is known to have
        # happened, so leave each participant at 'unknown' rather than
        # asserting everybody was present — the one thing we were not told.
        pass

    # ── the deal ────────────────────────────────────────────────────────────
    opp = (db.query(Opportunity).filter(Opportunity.id == appt.opportunity_id).first()
           if appt.opportunity_id else None)
    stage_moved = None

    if opp is not None:
        if next_action:
            opp.next_action = next_action.strip()[:255]
            opp.next_action_due_at = next_action_due_at
        elif next_action_due_at and opp.next_action:
            # A due date with no new action re-dates the existing one, which is
            # what "chase this again on Friday" means.
            opp.next_action_due_at = next_action_due_at

        if outcome == OUTCOME_PROPOSAL_SENT and not opp.proposal_sent_at:
            opp.proposal_sent_at = now

        target = OUTCOME_SUGGESTED_STAGE.get(outcome)
        if advance_stage and can_edit_opportunity and target and opp.stage != target:
            stage_moved = _move_stage(db, opp, actor, target, now)

        db.add(OpportunityEvent(
            opportunity_id=opp.id,
            event_type="appointment_outcome",
            summary="%s · %s" % (appt.title, OUTCOME_LABELS.get(outcome, outcome)),
            detail=_timeline_detail(appt, outcome, notes, stage_moved),
            actor_user_id=actor.id))

    if previous and previous != outcome:
        log.info("appointment %s outcome changed %s -> %s by %s",
                 appt.id, previous, outcome, actor.id)

    return {
        "appointment_id": appt.id,
        "outcome": outcome,
        "outcome_label": OUTCOME_LABELS.get(outcome, outcome),
        "previous_outcome": previous,
        "status": appt.status,
        "occurred": appt.occurred,
        "completed_at": appt.completed_at,
        "stage_moved_to": stage_moved,
        "stage_moved_to_label": STAGE_LABELS.get(stage_moved) if stage_moved else None,
        "next_action": opp.next_action if opp else None,
        "next_action_due_at": opp.next_action_due_at if opp else None,
        "calendar_released": outcome in OUTCOMES_NOT_OCCURRED,
    }


def _timeline_detail(appt, outcome, notes, stage_moved) -> str:
    bits = [OUTCOME_LABELS.get(outcome, outcome)]
    if outcome in OUTCOMES_OCCURRED:
        bits.append("meeting took place")
    elif outcome in OUTCOMES_NOT_OCCURRED:
        bits.append("meeting did not take place")
    if stage_moved:
        bits.append("stage → %s" % STAGE_LABELS.get(stage_moved, stage_moved))
    if notes:
        bits.append((notes or "").strip()[:400])
    return " · ".join(bits)


def _move_stage(db: Session, opp: Opportunity, actor: User,
                target: str, now: datetime) -> Optional[str]:
    """Move a deal's stage with the SAME semantics as the opportunity API.

    Duplicated behaviour is how two screens end up disagreeing about whether a
    deal is won, so the terminal stamps here mirror `patch_opportunity` exactly
    — status, won_at, lost_at, stage_changed_at, and the timeline entry.
    """
    if target not in ALL_STAGES:                           # pragma: no cover
        return None
    old = opp.stage
    opp.stage = target
    opp.stage_changed_at = now
    if target == STAGE_WON:
        opp.status = "won"
        opp.won_at = opp.won_at or now
    elif target == STAGE_LOST:
        opp.status = "lost"
        opp.lost_at = opp.lost_at or now
    db.add(OpportunityEvent(
        opportunity_id=opp.id, event_type="stage_changed",
        summary="Stage: %s → %s" % (STAGE_LABELS.get(old, old),
                                    STAGE_LABELS.get(target, target)),
        detail="Recorded from a meeting outcome",
        actor_user_id=actor.id))
    return target


# ── the queue that makes the data authoritative ──────────────────────────────

def pending_outcome_query(db: Session, org_id: str):
    """Meetings whose time has passed with no verdict recorded.

    This is the whole mechanism. Without a queue, an optional field is filled
    in when somebody feels like it and the resulting dataset is worse than
    nothing, because its gaps are invisible. With one, "unrecorded" is a
    visible number a manager can drive to zero.

    Only `scheduled` rows are candidates: a meeting already cancelled has its
    answer, and one already carrying an outcome is done.
    """
    return (db.query(SalesAppointment)
            .filter(SalesAppointment.brand_sales_org_id == org_id,
                    SalesAppointment.status == APPT_SCHEDULED,
                    SalesAppointment.outcome.is_(None),
                    SalesAppointment.ends_at < datetime.utcnow()))


def pending_outcomes(db: Session, org_id: str,
                     user: Optional[User] = None,
                     restrict_to_user: bool = False,
                     limit: int = 100) -> List[SalesAppointment]:
    """The queue, optionally narrowed to one person's own meetings.

    `restrict_to_user` is decided by the CALLER from real permissions, not
    guessed here — a rep sees what they owe, a manager sees the brand's.
    """
    q = pending_outcome_query(db, org_id)
    if restrict_to_user and user is not None:
        own = [r[0] for r in db.query(AppointmentParticipant.appointment_id)
               .filter(AppointmentParticipant.user_id == user.id).all()]
        q = q.filter(SalesAppointment.id.in_(own or [""]))
    return q.order_by(SalesAppointment.starts_at.desc()).limit(limit).all()


# ── what T9 / T10 read ───────────────────────────────────────────────────────

def completion_facts(db: Session, org_id: str,
                     start_utc: Optional[datetime] = None,
                     end_utc: Optional[datetime] = None) -> dict:
    """Authoritative completion and outcome counts for a window.

    THE CONTRACT WITH T9. Every number here comes from a recorded human
    verdict; none is inferred from a clock. `unrecorded` is reported as its own
    figure rather than being folded into either side, because an intelligence
    layer that cannot see how much it does not know will state a completion
    rate with unearned confidence — which is precisely the failure this whole
    piece of work exists to end.
    """
    q = db.query(SalesAppointment).filter(
        SalesAppointment.brand_sales_org_id == org_id)
    if start_utc:
        q = q.filter(SalesAppointment.starts_at >= start_utc)
    if end_utc:
        q = q.filter(SalesAppointment.starts_at < end_utc)

    rows = q.all()
    now = datetime.utcnow()

    by_outcome = {}
    occurred = not_occurred = unrecorded = future = 0
    for a in rows:
        if a.outcome:
            by_outcome[a.outcome] = by_outcome.get(a.outcome, 0) + 1
            if a.occurred is True:
                occurred += 1
            elif a.occurred is False:
                not_occurred += 1
        elif a.ends_at >= now:
            future += 1
        else:
            unrecorded += 1

    decided = occurred + not_occurred
    return {
        "brand_sales_org_id": org_id,
        "window": {"start_utc": start_utc, "end_utc": end_utc},
        "total": len(rows),
        "occurred": occurred,
        "did_not_occur": not_occurred,
        "still_upcoming": future,
        # The honest denominator. A rate computed over `decided` is a rate over
        # what is actually known, and `unrecorded` says how much that excludes.
        "unrecorded": unrecorded,
        "decided": decided,
        "completion_rate": round(occurred / decided, 4) if decided else None,
        "completion_rate_basis": "recorded outcomes only",
        "by_outcome": {k: {"count": v, "label": OUTCOME_LABELS.get(k, k)}
                       for k, v in sorted(by_outcome.items())},
        # Stated explicitly so a consumer cannot mistake this for an estimate.
        "authoritative": True,
        "inferred_from_clock": False,
    }


def outcome_out(appt: SalesAppointment) -> dict:
    """The outcome block every appointment serializer embeds.

    `needs_outcome` is computed here rather than in the UI so the calendar, the
    mobile view and the manager queue cannot each arrive at a different answer
    about which meetings are owed a verdict.
    """
    now = datetime.utcnow()
    return {
        "outcome": appt.outcome,
        "outcome_label": OUTCOME_LABELS.get(appt.outcome) if appt.outcome else None,
        "outcome_notes": appt.outcome_notes,
        "outcome_recorded_at": appt.outcome_recorded_at,
        "outcome_recorded_by": appt.outcome_recorded_by,
        "occurred": appt.occurred,
        "completed_at": appt.completed_at,
        "followup_appointment_id": appt.followup_appointment_id,
        "needs_outcome": bool(
            appt.status == APPT_SCHEDULED
            and not appt.outcome
            and appt.ends_at
            and appt.ends_at < now),
    }
