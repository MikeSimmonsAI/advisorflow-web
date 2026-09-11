"""THE CONTACT ELIGIBILITY ENGINE — deterministic, explainable, unoverridable.

ONE RULE ABOVE ALL OTHERS: AN AI EMPLOYEE NEVER DECIDES THAT A PERSON MAY BE
CONTACTED. It asks this module, this module answers ALLOW / DENY /
REQUIRES_REVIEW with structured reasons, and `tools.authorize` refuses on
anything that is not ALLOW. No prompt, no model output, no lead reply and no
customer instruction can turn a DENY into a send — the model never sees a
decision it is able to change, because the decision is made after it has
finished speaking and is applied to its request rather than offered to it.

IT DOES NOT RE-IMPLEMENT QUALIFICATION, IT ASKS IT.

`app/services/qualification.py` is the platform's authoritative answer to "may
this person be contacted on this channel" — DNC, opt-out columns, suppression,
TCPA consent for SMS, deceased dispositions, duplicates, capacity holds,
test records, organization-defined exclusion rules, address validity. It is
1,100 lines of decisions made carefully over months. Re-deriving any of it here
would create a second opinion, and section 12 and section 46 both say the same
thing about that. So layer 1 below is a call into it, and the mapping is:

    EXCLUDED         -> DENY              a hard platform exclusion
    REVIEW_REQUIRED  -> REQUIRES_REVIEW   a person must look at this
    READY_TO_SEND    -> continue to the workforce-specific layers

REQUIRES_REVIEW IS NOT A QUIET ALLOW. Section 12 names that failure directly.
It routes the work item to NEEDS_REVIEW and the gateway refuses the send with
`contact_requires_review`; nothing downstream may read it as permission.

WHAT THE WORKFORCE ADDS ON TOP (layers 2-6). These are questions the platform's
own send screens answer elsewhere or answer with a human in the loop, and which
an autonomous worker has to answer for itself:

    2. is this channel enabled for THIS employee
    3. is it inside the hours the customer said it may work
    4. has this person been touched too recently (frequency)
    5. has this employee used up its daily allowance
    6. has this record already told us no in a way the platform records as a
       STATUS rather than as a permission column

NOTHING HERE INVENTS CONSENT. "We have their phone number" is not consent and
never becomes it; that is qualification's rule and this module does not have a
path that can bypass it.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import (EmailMessage, Lead, Message,
                               Organization)
from app.models.workforce_models import (AIEligibilityResult, AIEmployee,
                                         AIToolExecution)
from app.services import qualification
from app.services.workforce import constants as C
from app.services.workforce import policy as wf_policy

_log = logging.getLogger(__name__)

# Bumped whenever the layers below change meaning, so a stored decision can be
# read against the rules that produced it rather than against today's.
POLICY_VERSION = "t6.1"

# How long a contact is left alone between touches when the customer has not
# said otherwise. Deliberately a day rather than an hour: the default for
# "nobody decided" should be the restrained one.
DEFAULT_MIN_HOURS_BETWEEN_TOUCHES = 24

REASONS = {
    "channel_not_enabled": "This employee is not allowed to use that channel",
    "outside_operating_hours": "Outside the hours this employee may work",
    "contacted_too_recently": "This person was contacted very recently",
    "daily_cap_reached": "This employee has reached its daily limit",
    "record_says_not_interested": "This record is already marked not interested",
    "record_is_dnc": "This record is on the do-not-contact list",
    "no_address_for_channel": "No usable address of record for that channel",
    "employee_not_running": "This employee is not currently running",
    "live_voice_disabled": "AI voice calling is disabled platform-wide",
    "tenant_mismatch": "That record does not belong to this organization",
}


def reason(code: str, detail: str = "") -> Dict[str, str]:
    out = {"code": code,
           "label": REASONS.get(code, qualification.REASONS.get(code, code))}
    if detail:
        out["detail"] = detail[:300]
    return out


class Eligibility:
    """The answer. `result` is one of ALLOW / DENY / REQUIRES_REVIEW."""

    __slots__ = ("result", "reasons", "channel", "decided_by", "lead_id",
                 "priority", "score")

    def __init__(self, result, reasons=None, channel=None, decided_by=None,
                 lead_id=None, priority=None, score=None):
        self.result = result
        self.reasons: List[Dict[str, str]] = list(reasons or [])
        self.channel = channel
        self.decided_by = decided_by
        self.lead_id = lead_id
        self.priority = priority
        self.score = score

    @property
    def allowed(self) -> bool:
        return self.result == C.ALLOW

    def as_dict(self) -> Dict:
        return {"result": self.result, "reasons": list(self.reasons),
                "channel": self.channel, "decided_by": self.decided_by,
                "lead_id": self.lead_id, "priority": self.priority,
                "score": self.score, "policy_version": POLICY_VERSION}


def _deny(code, detail="", channel=None, decided_by=None, lead_id=None):
    return Eligibility(C.DENY, [reason(code, detail)], channel=channel,
                       decided_by=decided_by, lead_id=lead_id)


def _build_context(db: Session, lead: Lead, channel: str
                   ) -> qualification.QualificationContext:
    """The same context object qualification's own entry point builds.

    Including the suppression load, which `QualificationContext.__init__`
    deliberately leaves empty for the caller to fill — see the comment in that
    constructor. Forgetting it here would mean a suppressed number qualified as
    READY, which is the single worst mistake available in this file.
    """
    org_id = lead.organization_id
    ctx = qualification.QualificationContext(
        db, [lead], org_id, qualification.org_rules(db, org_id))
    if channel in (qualification.CHANNEL_SMS, qualification.CHANNEL_VOICE) and org_id:
        ctx.suppressed_phones = qualification.load_suppressed_phones(db, org_id)
    return ctx


def _last_touch_at(db: Session, lead_id: str) -> Optional[datetime]:
    """The most recent OUTBOUND touch on this record, any channel, any sender.

    Any sender, deliberately: a frequency cap that only counted this
    employee's own sends would let two employees on the same record produce
    twice the contact rate the customer configured.
    """
    newest = None
    row = (db.query(Message.sent_at)
           .filter(Message.lead_id == lead_id)
           .order_by(Message.sent_at.desc()).first())
    if row and row[0]:
        newest = row[0]
    row = (db.query(EmailMessage.sent_at)
           .filter(EmailMessage.lead_id == lead_id)
           .order_by(EmailMessage.sent_at.desc()).first())
    if row and row[0] and (newest is None or row[0] > newest):
        newest = row[0]
    return newest


def _sends_today(db: Session, employee_id: str, now: datetime) -> int:
    """How many outward-reaching tool calls this employee has ALLOWED today.

    Counted from the tool-execution ledger rather than from messages, because
    the cap is on what the EMPLOYEE did — a send that the platform later failed
    to deliver still used the allowance, and a message written by a person does
    not.
    """
    start = datetime(now.year, now.month, now.day)
    from app.services.workforce import registry as _registry
    return (db.query(AIToolExecution)
            .filter(AIToolExecution.employee_id == employee_id,
                    AIToolExecution.decision == "allowed",
                    AIToolExecution.simulated.is_(False),
                    AIToolExecution.tool_key.in_(list(_registry.EXECUTING_TOOL_KEYS)),
                    AIToolExecution.created_at >= start)
            .count())


def evaluate(db: Session, *, employee: AIEmployee, lead: Lead, channel: str,
             pol: Optional[wf_policy.EffectivePolicy] = None,
             now: Optional[datetime] = None,
             enforce_hours: bool = True,
             enforce_frequency: bool = True,
             daily_cap: Optional[int] = None) -> Eligibility:
    """May this employee contact this person, on this channel, right now?

    `enforce_hours` / `enforce_frequency` exist for the READ path: asking
    whether a record is eligible at all (for the queue's own bookkeeping) is a
    different question from whether it may be contacted at this instant, and
    conflating them would make the whole queue look ineligible overnight.
    THE SEND PATH ALWAYS PASSES THEM AS TRUE — `tools.authorize` does not offer
    a way to turn them off.
    """
    now = now or datetime.utcnow()
    channel = (channel or "").lower()
    pol = pol or wf_policy.resolve(db, employee)

    # ── LAYER 0: TENANCY. Before any question about the person. ─────────────
    #
    # A lead id that belongs to another organization is not an eligibility
    # question, it is an attack, and it is answered here rather than by
    # whatever the qualification engine would have made of a foreign row.
    if lead is None:
        return _deny(C.DENY_RECORD_NOT_FOUND, "No such record.",
                     channel=channel, decided_by="tenancy")
    if str(lead.organization_id) != str(employee.organization_id):
        _log.warning("AUDIT: workforce eligibility refused cross-tenant lead "
                     "%s for employee %s (org %s)", lead.id, employee.id,
                     employee.organization_id)
        return _deny("tenant_mismatch", "", channel=channel,
                     decided_by="tenancy", lead_id=lead.id)

    if channel not in C.ALL_CHANNELS:
        return _deny(C.DENY_BAD_ARGUMENTS, "Unknown channel %r" % channel,
                     channel=channel, decided_by="arguments", lead_id=lead.id)

    # ── LAYER 1: THE PLATFORM'S OWN ANSWER. Authoritative. ──────────────────
    ctx = _build_context(db, lead, channel)
    decision = qualification.qualify_one(lead, channel, ctx)
    bucket = decision.get("bucket")
    if bucket == qualification.EXCLUDED:
        return Eligibility(C.DENY, decision.get("reasons") or [],
                           channel=channel, decided_by="platform_qualification",
                           lead_id=lead.id)
    if bucket == qualification.REVIEW:
        # NOT AN ALLOW. Routed to a person; the gateway refuses the send.
        return Eligibility(C.REQUIRES_REVIEW, decision.get("reasons") or [],
                           channel=channel, decided_by="platform_qualification",
                           lead_id=lead.id, priority=decision.get("priority"),
                           score=decision.get("score"))

    reasons: List[Dict[str, str]] = []

    # ── LAYER 2: IS THIS EMPLOYEE ALLOWED THIS CHANNEL AT ALL ───────────────
    if not wf_policy.channel_enabled(pol, channel):
        return _deny("channel_not_enabled",
                     "%s is not enabled for %s." % (channel, employee.name),
                     channel=channel, decided_by="employee_policy",
                     lead_id=lead.id)

    # ── LAYER 3: VOICE IS ARCHITECTURALLY PRESENT AND OPERATIONALLY OFF ─────
    if channel == C.CHANNEL_VOICE:
        from app.services.workforce import activation, outbound
        # The simulator drives the voice PATH against a fake adapter so the
        # architecture is exercised; no call is placed by either branch. See
        # gate 7 in tools.authorize for the matching allowance.
        simulating = outbound.is_fully_simulated()
        if not activation.live_voice_enabled() and not simulating:
            return _deny("live_voice_disabled",
                         "AI voice is disabled in this deployment.",
                         channel=channel, decided_by="platform_policy",
                         lead_id=lead.id)

    # ── LAYER 4: HOURS ──────────────────────────────────────────────────────
    if enforce_hours:
        ok, why = wf_policy.within_operating_hours(pol, now)
        if not ok:
            return _deny("outside_operating_hours", why, channel=channel,
                         decided_by="employee_policy", lead_id=lead.id)

    # ── LAYER 5: FREQUENCY ──────────────────────────────────────────────────
    if enforce_frequency:
        cfg = wf_policy.json_obj(employee.config)
        try:
            min_hours = int(cfg.get("min_hours_between_touches",
                                    DEFAULT_MIN_HOURS_BETWEEN_TOUCHES))
        except (TypeError, ValueError):
            min_hours = DEFAULT_MIN_HOURS_BETWEEN_TOUCHES
        if min_hours > 0:
            last = _last_touch_at(db, lead.id)
            if last is not None and (now - last) < timedelta(hours=min_hours):
                return _deny(
                    "contacted_too_recently",
                    "Last contacted %s; minimum gap is %dh."
                    % (last.strftime("%Y-%m-%d %H:%M"), min_hours),
                    channel=channel, decided_by="employee_policy",
                    lead_id=lead.id)

    # ── LAYER 6: THE EMPLOYEE'S DAILY ALLOWANCE ─────────────────────────────
    cap = daily_cap if daily_cap is not None else employee.daily_work_cap
    if cap:
        used = _sends_today(db, employee.id, now)
        if used >= int(cap):
            return _deny("daily_cap_reached",
                         "%d of %d used today." % (used, int(cap)),
                         channel=channel, decided_by="employee_policy",
                         lead_id=lead.id)

    # ── LAYER 7: STATUSES THE PLATFORM KEEPS OUTSIDE THE PERMISSION COLUMNS ─
    #
    # qualification already excludes status == "dnc". `not_interested` is a
    # workforce concept rather than a platform permission, so it is checked
    # here: a person who said no once must not be re-worked by a second
    # employee that was pointed at the same audience.
    status = (getattr(lead, "status", None) or "").lower()
    if status in ("not_interested", "declined"):
        return _deny("record_says_not_interested",
                     "Record status is %s." % status, channel=channel,
                     decided_by="record_status", lead_id=lead.id)

    return Eligibility(C.ALLOW, reasons, channel=channel,
                       decided_by="all_layers_passed", lead_id=lead.id,
                       priority=decision.get("priority"),
                       score=decision.get("score"))


def record(db: Session, elig: Eligibility, *, employee: AIEmployee,
           work_item_id: Optional[str] = None,
           subject_type: str = "lead") -> AIEligibilityResult:
    """Persist the decision so a refusal can be explained months later."""
    import json
    row = AIEligibilityResult(
        organization_id=employee.organization_id,
        employee_id=employee.id,
        work_item_id=work_item_id,
        subject_type=subject_type,
        subject_id=elig.lead_id or "",
        channel=elig.channel or "",
        result=elig.result,
        reasons=json.dumps(elig.reasons)[:8000],
        decided_by=elig.decided_by,
        policy_version=POLICY_VERSION,
    )
    db.add(row)
    db.flush()
    return row


def evaluate_and_record(db: Session, *, employee: AIEmployee, lead: Lead,
                        channel: str, work_item_id: Optional[str] = None,
                        **kw) -> Eligibility:
    elig = evaluate(db, employee=employee, lead=lead, channel=channel, **kw)
    try:
        record(db, elig, employee=employee, work_item_id=work_item_id)
    except Exception:                                        # noqa: BLE001
        # A LEDGER FAILURE MUST NOT TURN A DENY INTO AN ALLOW. The decision is
        # already made and is returned regardless; only the record is lost.
        _log.exception("workforce: could not record eligibility decision")
    return elig


def summarize_org(db: Session, organization_id: str, *, days: int = 7) -> Dict:
    """Counts by result and reason, for the supervisor and the God screen."""
    since = datetime.utcnow() - timedelta(days=max(1, int(days)))
    rows = (db.query(AIEligibilityResult)
            .filter(AIEligibilityResult.organization_id == organization_id,
                    AIEligibilityResult.created_at >= since)
            .all())
    counts = {C.ALLOW: 0, C.DENY: 0, C.REQUIRES_REVIEW: 0}
    by_reason: Dict[str, int] = {}
    import json as _json
    for r in rows:
        counts[r.result] = counts.get(r.result, 0) + 1
        try:
            for item in _json.loads(r.reasons or "[]"):
                code = item.get("code")
                if code:
                    by_reason[code] = by_reason.get(code, 0) + 1
        except (ValueError, TypeError):
            continue
    return {
        "window_days": int(days),
        "total": len(rows),
        "counts": counts,
        "top_reasons": sorted(
            ({"code": k, "label": REASONS.get(k, qualification.REASONS.get(k, k)),
              "count": v} for k, v in by_reason.items()),
            key=lambda d: -d["count"])[:15],
        "policy_version": POLICY_VERSION,
    }
