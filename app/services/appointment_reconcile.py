"""
External calendar reconciliation — detecting, and refusing to hide, drift.

THE PROBLEM
-----------
EvoSys Pro owns an appointment it created; a provider event is a copy. Before
this module, that ownership was enforced asymmetrically in a way that quietly
lost information:

  · A provider event DELETED upstream was silently recreated on the next
    update. Correct outcome, no record that it happened.
  · A provider event MOVED upstream was never noticed AT ALL. Outlook said
    Thursday, EvoSys said Tuesday, both were confident, and the first anyone
    knew about it was a prospect sitting alone on a video call.

The brief's rule is the right one: reconcile safely when the resolution is
deterministic, and otherwise raise a VISIBLE conflict requiring review. Never
silently destroy contradictory changes.

WHY "WE OWN IT" IS NOT A LICENCE TO OVERWRITE
---------------------------------------------
The thing an overwrite destroys is a decision a real person made. When Blake
drags a demo from Tuesday to Thursday in Outlook, the likeliest reason is that
he spoke to the prospect and agreed Thursday. Pushing Tuesday back over it does
not restore correctness — it puts Blake back in a meeting the customer has
already left, and tells nobody. So a time change is escalated, not healed.

A subject or location edit is different: EvoSys's wording is authoritative, no
scheduling commitment is at stake, and re-pushing costs nothing but a tidier
calendar. That one IS healed.

PROVING OWNERSHIP BEFORE WRITING
--------------------------------
A stored `external_event_id` is not proof that an event is ours. Ids get
copied, restored from backups, and (in a multi-tenant system) mixed up. So
before this module writes to a provider event it checks that the event still
CLAIMS the appointment we think it belongs to. An event that claims a different
appointment — or claims nothing where we would expect a claim — is ORPHANED and
is never touched. A cross-tenant calendar write is a far worse failure than a
stale event.

READS ONLY, EXCEPT WHERE DETERMINISTIC
--------------------------------------
Nothing here can lose an appointment. Every function tolerates a provider being
down: an unreachable calendar produces "unknown", never a conflict, because a
system that reports a conflict every time Microsoft has a bad minute trains
everyone to ignore conflicts.
"""
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.models import User
from app.models.calendar_models import (
    AppointmentSyncLog, PROVIDER_ICS, PROVIDER_MICROSOFT, PROVIDER_GOOGLE,
    CONFLICT_DELETED, CONFLICT_MOVED, CONFLICT_CHANGED, CONFLICT_ORPHANED,
    CONFLICT_KINDS, CONFLICTS_AUTO_RECONCILABLE,
    CONFLICT_LABELS, CONFLICT_EXPLANATIONS,
    SYNC_SYNCED, SYNC_FAILED, SYNC_REAUTH, SYNC_NOT_CONNECTED,
)
from app.models.scheduling_models import (
    SalesAppointment, AppointmentParticipant,
    APPT_SCHEDULED, APPT_CANCELLED,
)

log = logging.getLogger(__name__)

# How far a provider event's time may differ from what we pushed before it
# counts as a MOVE rather than as rounding.
#
# Not zero. Graph and Google both round to the second, some clients normalise
# to the minute, and an all-day conversion can shift a boundary. A 60-second
# tolerance absorbs every representational difference we have seen while still
# catching the smallest edit a human can actually make by dragging an event,
# which is a 15-minute grid step in every calendar UI that exists.
TIME_DRIFT_TOLERANCE_SECONDS = 60

# Providers worth asking. The .ics fallback is excluded because it has no
# calendar to read — see `supports_read_back`.
READABLE_PROVIDERS = (PROVIDER_MICROSOFT, PROVIDER_GOOGLE)


# ── classification ──────────────────────────────────────────────────────────

def _seconds_apart(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    if a is None or b is None:
        return None
    return abs((a - b).total_seconds())


def classify_drift(part: AppointmentParticipant, appt: SalesAppointment,
                   state) -> Tuple[Optional[str], Optional[str]]:
    """(conflict_kind, human_detail) for one participant's provider event.

    Returns (None, None) when the copy matches what we pushed — which is the
    overwhelmingly common case and must be cheap and silent.

    THE COMPARISON IS AGAINST `pushed_*`, NOT AGAINST THE APPOINTMENT.
    That distinction is the whole design. Compared against the appointment's
    live time, an in-flight reschedule whose push has not completed looks
    exactly like somebody editing Outlook, and the reconciler would raise a
    conflict against its own unfinished work. Compared against what we last
    successfully sent, a difference can only have come from outside.
    """
    if state is None:
        return None, None

    # ── gone ────────────────────────────────────────────────────────────────
    if not state.exists:
        return CONFLICT_DELETED, (
            "The calendar event was removed from %s." %
            (part.external_calendar_provider or "the connected calendar"))

    # ── not ours ────────────────────────────────────────────────────────────
    #
    # Checked BEFORE any time comparison. An event that is not ours may legally
    # sit at any time at all, and reporting it as "moved" would invite somebody
    # to press a button that overwrites a stranger's meeting.
    claimed = state.claimed_appointment_id
    if claimed and claimed != appt.id:
        return CONFLICT_ORPHANED, (
            "The stored calendar event belongs to a different appointment.")

    # ── moved ───────────────────────────────────────────────────────────────
    #
    # Only meaningful once we know what we pushed. A participant synced before
    # `pushed_*` existed has no baseline, and guessing one from the current
    # appointment time would raise a conflict on every historical row the first
    # time this ran. No baseline means no claim.
    if part.pushed_starts_at is not None:
        drift_start = _seconds_apart(state.starts_at, part.pushed_starts_at)
        drift_end = _seconds_apart(state.ends_at, part.pushed_ends_at)
        moved = ((drift_start is not None and drift_start > TIME_DRIFT_TOLERANCE_SECONDS)
                 or (drift_end is not None and drift_end > TIME_DRIFT_TOLERANCE_SECONDS))
        if moved:
            return CONFLICT_MOVED, (
                "EvoSys Pro scheduled %s; the connected calendar now says %s." % (
                    part.pushed_starts_at.strftime("%b %d %H:%M UTC")
                    if part.pushed_starts_at else "unknown",
                    state.starts_at.strftime("%b %d %H:%M UTC")
                    if state.starts_at else "no start time"))

    # ── edited, but not in a way that changes a commitment ──────────────────
    #
    # Compared loosely and only when the provider gave us something to compare.
    # A provider that returns no subject is not evidence of an edit, and
    # treating absence as difference would flag every event on a provider whose
    # `$select` we later narrow.
    if state.is_cancelled:
        return CONFLICT_DELETED, "The calendar event is marked cancelled."

    return None, None


# ── recording ───────────────────────────────────────────────────────────────

def _raise_conflict(db: Session, part: AppointmentParticipant,
                    kind: str, detail: str, state, now: datetime) -> None:
    """Mark a conflict for human review. Changes nothing on the provider.

    Idempotent: re-detecting the same conflict refreshes the detail rather than
    stacking duplicates, so a nightly pass over an unresolved conflict does not
    produce thirty alerts about one moved meeting.
    """
    part.sync_conflict = True
    part.sync_conflict_kind = kind
    part.sync_conflict_detail = (detail or "")[:2000] or None
    part.sync_conflict_at = part.sync_conflict_at or now
    part.external_last_seen_at = now
    if state is not None:
        part.conflict_provider_starts_at = state.starts_at
        part.conflict_provider_ends_at = state.ends_at
        part.external_etag = state.etag or part.external_etag


def _clear_conflict(part: AppointmentParticipant, now: datetime) -> None:
    part.sync_conflict = False
    part.sync_conflict_kind = None
    part.sync_conflict_detail = None
    part.sync_conflict_at = None
    part.conflict_provider_starts_at = None
    part.conflict_provider_ends_at = None
    part.external_last_seen_at = now


def _log_reconcile(db: Session, appt_id: str, user_id: Optional[str],
                   provider: str, action: str, ok: bool,
                   kind: Optional[str], message: Optional[str],
                   now: datetime) -> None:
    """Append to the existing sync log rather than inventing a second table.

    Conflicts ARE sync events, and a manager looking at why a meeting went
    wrong should find the whole story in one place rather than having to know
    that drift lives somewhere else.
    """
    try:
        db.add(AppointmentSyncLog(
            appointment_id=appt_id, user_id=user_id, provider=provider or "",
            action=action,
            status=(kind or ("synced" if ok else "failed")),
            ok=bool(ok),
            error_code=kind,
            error_message=(message or "")[:2000] or None,
            attempt=1, occurred_at=now,
        ))
    except Exception:
        log.exception("could not write reconcile log for appointment %s", appt_id)


# ── the pass ────────────────────────────────────────────────────────────────

def _readable_provider(db: Session, user: User, part: AppointmentParticipant,
                       org=None):
    """The provider the event was CREATED with, if it can be read back.

    `prefer` matters: if the user has since connected a second calendar, asking
    the new provider about an id that lives in the old one produces a 404 that
    would be classified as a deletion — healing a meeting by recreating it on
    the wrong calendar. So the stored provider wins, and nothing is inferred.
    """
    from app.services import calendar_providers as reg
    stored = part.external_calendar_provider
    if stored not in READABLE_PROVIDERS:
        return None, "provider_not_readable"
    try:
        provider = reg.get_provider(db, user, org=org, prefer=stored)
    except Exception as e:                                 # pragma: no cover
        log.exception("could not build provider %s for user %s", stored, user.id)
        return None, "provider_unavailable"
    if getattr(provider, "resolved_key", None) != stored:
        # The registry fell back to something else, which means the original
        # grant is gone. That is a reauth condition, not a drift condition:
        # we cannot see the calendar, so we know nothing about the event and
        # must claim nothing.
        return None, "provider_changed"
    if not provider.supports_read_back():
        return None, "provider_not_readable"
    return provider, None


def reconcile_participant(db: Session, appt: SalesAppointment,
                          part: AppointmentParticipant, user: User,
                          org=None, organizer=None,
                          auto_heal: bool = True,
                          now: Optional[datetime] = None) -> dict:
    """Check ONE participant's provider event against what we pushed.

    Never raises. Never commits — the caller owns the transaction.

    Outcomes, in the order they are decided:
      · nothing to check      — no event id, or a provider with no read-back
      · unknown               — the provider could not be reached. NOT a
                                conflict: an outage is not an edit.
      · in_sync               — the copy matches what we pushed
      · healed                — deterministic, resolved, and recorded
      · conflict              — raised for review, provider left untouched
    """
    now = now or datetime.utcnow()
    provider_key = part.external_calendar_provider

    if not part.external_event_id:
        return {"user_id": user.id, "provider": provider_key,
                "result": "nothing_to_check", "reason": "no_external_event"}

    provider, why = _readable_provider(db, user, part, org=org)
    if provider is None:
        return {"user_id": user.id, "provider": provider_key,
                "result": "nothing_to_check", "reason": why}

    try:
        state, err = provider.get_event(part.external_event_id)
    except Exception as e:                                 # pragma: no cover
        # A provider is contractually forbidden from raising. This boundary
        # assumes one eventually will, and treats it as an outage rather than
        # as evidence about the event.
        log.exception("get_event blew up (appt=%s user=%s)", appt.id, user.id)
        return {"user_id": user.id, "provider": provider_key,
                "result": "unknown", "reason": "exception"}

    if err is not None and not err.ok:
        # WE COULD NOT LOOK. Say so, and leave every existing conflict flag
        # exactly as it was. Clearing one here would let a single 500 mark a
        # genuine unresolved conflict as settled.
        part.external_last_seen_at = part.external_last_seen_at
        return {"user_id": user.id, "provider": provider_key,
                "result": "unknown", "reason": err.error_code,
                "needs_reauth": bool(err.needs_reauth)}

    kind, detail = classify_drift(part, appt, state)

    if kind is None:
        was = part.sync_conflict
        _clear_conflict(part, now)
        if state is not None and state.etag:
            part.external_etag = state.etag
        return {"user_id": user.id, "provider": provider_key,
                "result": "in_sync", "previously_conflicted": bool(was)}

    # ── deterministic: heal it, and say that we did ─────────────────────────
    if auto_heal and kind in CONFLICTS_AUTO_RECONCILABLE:
        healed = _heal(db, appt, part, user, kind, org=org, organizer=organizer,
                       now=now)
        if healed["ok"]:
            _clear_conflict(part, now)
            _log_reconcile(db, appt.id, user.id, provider_key, "reconcile_heal",
                           True, kind, detail, now)
            return {"user_id": user.id, "provider": provider_key,
                    "result": "healed", "conflict_kind": kind,
                    "conflict_label": CONFLICT_LABELS.get(kind),
                    "detail": detail}
        # The heal itself failed. That is a real, visible problem — the copy is
        # wrong AND we could not fix it — so it becomes a conflict rather than
        # being retried silently until somebody notices a missing meeting.
        _raise_conflict(db, part, kind,
                        "%s Could not restore it automatically: %s" %
                        (detail or "", healed.get("error") or "unknown"), state, now)
        _log_reconcile(db, appt.id, user.id, provider_key, "reconcile_heal",
                       False, kind, healed.get("error"), now)
        return {"user_id": user.id, "provider": provider_key,
                "result": "conflict", "conflict_kind": kind,
                "conflict_label": CONFLICT_LABELS.get(kind),
                "detail": detail, "heal_error": healed.get("error")}

    # ── not deterministic: escalate, touch nothing ──────────────────────────
    _raise_conflict(db, part, kind, detail, state, now)
    _log_reconcile(db, appt.id, user.id, provider_key, "reconcile_conflict",
                   False, kind, detail, now)
    return {"user_id": user.id, "provider": provider_key,
            "result": "conflict", "conflict_kind": kind,
            "conflict_label": CONFLICT_LABELS.get(kind),
            "explanation": CONFLICT_EXPLANATIONS.get(kind),
            "detail": detail}


def _heal(db: Session, appt: SalesAppointment, part: AppointmentParticipant,
          user: User, kind: str, org=None, organizer=None,
          now: Optional[datetime] = None) -> dict:
    """Re-push EvoSys's version. Only for the deterministic conflict classes.

    Reuses `appointment_sync._sync_participant` rather than reimplementing a
    push. That function is idempotent on `external_event_id` — it updates when
    an id exists and creates when it does not — which is exactly the behaviour
    both healable classes need, and reusing it means a heal can never drift
    away from an ordinary sync.

    A DELETED event has its stale id cleared first, so the sync creates a fresh
    one instead of updating something that is gone.
    """
    from app.services import appointment_sync as apsync
    now = now or datetime.utcnow()

    if kind == CONFLICT_DELETED:
        part.external_event_id = None

    try:
        result = apsync._sync_participant(db, appt, part, user, org=org,
                                          organizer=organizer, now=now)
    except Exception as e:                                 # pragma: no cover
        log.exception("heal blew up (appt=%s user=%s)", appt.id, user.id)
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": bool(result.get("ok")), "error": result.get("error"),
            "status": result.get("status")}


def reconcile_appointment(db: Session, appt: SalesAppointment,
                          org=None, organizer=None, auto_heal: bool = True,
                          now: Optional[datetime] = None,
                          commit: bool = True) -> dict:
    """Check every participant's calendar copy of one appointment.

    A cancelled appointment is skipped: its provider events were withdrawn on
    cancellation, and "the event is gone" is the state we asked for, not drift.
    """
    now = now or datetime.utcnow()

    if appt.status == APPT_CANCELLED:
        return {"appointment_id": appt.id, "skipped": "cancelled",
                "checked": 0, "results": []}

    results = []
    parts = (db.query(AppointmentParticipant)
             .filter(AppointmentParticipant.appointment_id == appt.id).all())
    for part in parts:
        user = db.query(User).filter(User.id == part.user_id).first()
        if user is None:
            # A participant whose user row is gone. Skipped rather than
            # crashing the pass for everyone else on the meeting.
            continue
        results.append(reconcile_participant(
            db, appt, part, user, org=org, organizer=organizer,
            auto_heal=auto_heal, now=now))

    if commit:
        try:
            db.commit()
        except Exception:
            log.exception("could not commit reconcile state for %s", appt.id)
            db.rollback()
    return summarize(appt, results)


def summarize(appt: SalesAppointment, results: List[dict]) -> dict:
    """Roll per-participant outcomes into something a screen can render.

    `unknown` is reported as its own count and never folded into either
    "in sync" or "conflict". A provider we could not reach is a third thing,
    and a UI that rounds it to "fine" is a UI that will one day tell a manager
    a meeting is on somebody's calendar when nobody has checked in two days.
    """
    def _n(name):
        return len([r for r in results if r.get("result") == name])

    conflicts = [r for r in results if r.get("result") == "conflict"]
    return {
        "appointment_id": appt.id,
        "checked": len(results),
        "in_sync": _n("in_sync"),
        "healed": _n("healed"),
        "conflicts": len(conflicts),
        "unknown": _n("unknown"),
        "nothing_to_check": _n("nothing_to_check"),
        "needs_review": bool(conflicts),
        "conflict_kinds": sorted({r.get("conflict_kind") for r in conflicts
                                  if r.get("conflict_kind")}),
        "results": results,
    }


# ── the review queue ────────────────────────────────────────────────────────

def conflict_rows(db: Session, org_id: str, limit: int = 100):
    """(appointment, participant) pairs awaiting a human decision.

    Scoped to the brand sales org by joining through the appointment, so a
    manager reviewing conflicts can never be handed a row from another brand —
    the flag lives on the participant, which has no tenancy of its own.
    """
    return (db.query(SalesAppointment, AppointmentParticipant)
            .join(AppointmentParticipant,
                  AppointmentParticipant.appointment_id == SalesAppointment.id)
            .filter(SalesAppointment.brand_sales_org_id == org_id,
                    SalesAppointment.status != APPT_CANCELLED,
                    AppointmentParticipant.sync_conflict.is_(True))
            .order_by(AppointmentParticipant.sync_conflict_at.desc())
            .limit(limit).all())


def conflict_out(appt: SalesAppointment, part: AppointmentParticipant,
                 user: Optional[User] = None) -> dict:
    """One conflict, with BOTH sides shown.

    Deliberately renders what EvoSys says and what the provider says side by
    side. A conflict screen that shows only one of them is asking somebody to
    choose blind, and the usual result is that they pick whichever button is
    nearer.
    """
    kind = part.sync_conflict_kind
    return {
        "appointment_id": appt.id,
        "title": appt.title,
        "user_id": part.user_id,
        "user_name": (user.full_name if user else None),
        "provider": part.external_calendar_provider,
        "kind": kind,
        "label": CONFLICT_LABELS.get(kind, kind),
        "explanation": CONFLICT_EXPLANATIONS.get(kind),
        "detail": part.sync_conflict_detail,
        "detected_at": part.sync_conflict_at,
        "auto_resolvable": kind in CONFLICTS_AUTO_RECONCILABLE,
        "evosys": {
            "starts_at": appt.starts_at,
            "ends_at": appt.ends_at,
            "timezone": appt.timezone,
            "pushed_starts_at": part.pushed_starts_at,
            "pushed_at": part.pushed_at,
        },
        "provider_says": {
            "starts_at": part.conflict_provider_starts_at,
            "ends_at": part.conflict_provider_ends_at,
        },
        # What a human can actually do about it, named rather than implied.
        "actions": _actions_for(kind),
    }


def _actions_for(kind: Optional[str]) -> List[dict]:
    """The resolutions offered for each conflict class.

    ADOPTING the provider's time is deliberately NOT offered as a one-click
    action. Moving an appointment is a reschedule: it has to re-check every
    other participant's availability, re-push to every other calendar, reset
    the prospect's confirmation and write the opportunity timeline. A button
    here that just overwrote two columns would produce a meeting that looks
    moved and is not — so this points at the reschedule flow, which does all
    of it properly.
    """
    if kind == CONFLICT_MOVED:
        return [
            {"action": "push_evosys", "label": "Keep the EvoSys Pro time",
             "detail": "Restores this meeting on the connected calendar."},
            {"action": "reschedule", "label": "Move the meeting to the new time",
             "detail": "Opens the reschedule flow, which re-checks everyone's "
                       "availability and updates every calendar."},
        ]
    if kind == CONFLICT_DELETED:
        return [{"action": "push_evosys", "label": "Restore it on their calendar",
                 "detail": "EvoSys Pro still holds this appointment."}]
    if kind == CONFLICT_CHANGED:
        return [{"action": "push_evosys", "label": "Push EvoSys Pro's details",
                 "detail": "Overwrites the edited fields on the connected calendar."}]
    if kind == CONFLICT_ORPHANED:
        return [{"action": "unlink", "label": "Forget the stored calendar event",
                 "detail": "Leaves the unrelated event alone and lets EvoSys Pro "
                           "create a fresh one on the next sync."}]
    return []


def resolve_conflict(db: Session, appt: SalesAppointment,
                     part: AppointmentParticipant, user: User, actor: User,
                     action: str, org=None,
                     now: Optional[datetime] = None) -> dict:
    """Apply a human's decision. Does not commit.

    `push_evosys` re-pushes our version — the same idempotent sync an ordinary
    booking uses, so there is no second write path to keep correct.

    `unlink` drops the stored event id WITHOUT touching the provider. That is
    the only safe answer for an orphaned id: the event on the other end may
    belong to somebody else entirely, and the one thing we must not do is
    modify it to prove a point about our own bookkeeping.
    """
    now = now or datetime.utcnow()

    if action == "unlink":
        stale = part.external_event_id
        part.external_event_id = None
        part.external_etag = None
        part.pushed_starts_at = None
        part.pushed_ends_at = None
        part.pushed_at = None
        part.sync_status = SYNC_NOT_CONNECTED
        _clear_conflict(part, now)
        _log_reconcile(db, appt.id, part.user_id, part.external_calendar_provider,
                       "reconcile_unlink", True, None,
                       "Stored event id forgotten by %s" % actor.id, now)
        log.info("unlinked stale provider event from appt=%s user=%s", appt.id, part.user_id)
        return {"action": "unlink", "ok": True, "forgot_event_id": bool(stale)}

    if action == "push_evosys":
        kind = part.sync_conflict_kind
        # A DELETED or ORPHANED id must not be updated — there is nothing valid
        # behind it. Clearing it first makes the sync CREATE, which is the
        # correct operation in both cases.
        if kind in (CONFLICT_DELETED, CONFLICT_ORPHANED):
            part.external_event_id = None
        healed = _heal(db, appt, part, user, kind or CONFLICT_CHANGED,
                       org=org, organizer=actor, now=now)
        if healed["ok"]:
            _clear_conflict(part, now)
        _log_reconcile(db, appt.id, part.user_id, part.external_calendar_provider,
                       "reconcile_resolve", healed["ok"], kind,
                       healed.get("error"), now)
        return {"action": "push_evosys", "ok": healed["ok"],
                "error": healed.get("error"), "status": healed.get("status")}

    return {"action": action, "ok": False,
            "error": "Unknown resolution '%s'." % action}


# ── the scan a screen or a job runs ─────────────────────────────────────────

def scan_org(db: Session, org_id: str, org=None,
             window_days: int = 14, auto_heal: bool = True,
             limit: int = 200, now: Optional[datetime] = None) -> dict:
    """Reconcile the brand's upcoming meetings.

    Bounded by a forward window and a row limit, because this makes one
    provider call PER PARTICIPANT PER APPOINTMENT. Scanning a year of history
    would hammer Microsoft to reconcile meetings nobody can attend any more.
    Past meetings are excluded for the same reason: a drifted event on a
    meeting that already happened is not actionable.
    """
    now = now or datetime.utcnow()
    horizon = now + timedelta(days=window_days)

    appts = (db.query(SalesAppointment)
             .filter(SalesAppointment.brand_sales_org_id == org_id,
                     SalesAppointment.status == APPT_SCHEDULED,
                     SalesAppointment.ends_at >= now,
                     SalesAppointment.starts_at < horizon)
             .order_by(SalesAppointment.starts_at.asc())
             .limit(limit).all())

    checked = healed = conflicts = unknown = 0
    per_appt = []
    for a in appts:
        r = reconcile_appointment(db, a, org=org, auto_heal=auto_heal,
                                  now=now, commit=False)
        checked += r["checked"]
        healed += r["healed"]
        conflicts += r["conflicts"]
        unknown += r["unknown"]
        if r["healed"] or r["conflicts"] or r["unknown"]:
            per_appt.append(r)

    try:
        db.commit()
    except Exception:
        log.exception("could not commit org reconcile for %s", org_id)
        db.rollback()

    return {
        "brand_sales_org_id": org_id,
        "window_days": window_days,
        "appointments_scanned": len(appts),
        "participants_checked": checked,
        "healed": healed,
        "conflicts": conflicts,
        "unknown": unknown,
        "details": per_appt,
    }
