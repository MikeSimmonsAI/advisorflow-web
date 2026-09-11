"""MAY THIS PERSON BE CONTACTED, ON THIS CHANNEL, RIGHT NOW.

NO OUTBOUND COMMUNICATION HAPPENS BECAUSE A MODEL DECIDED TO SEND IT. Every
outbound attempt is answered here first, from the platform's own authorities,
and the answer is one of three words: ALLOW, DENY, REQUIRES_REVIEW.

THREE PRINCIPLES, AND EVERY DECISION BELOW FOLLOWS FROM THEM.

    THIS ENGINE INVENTS NO POLICY. It consults what the platform already
    keeps: `compliance_service.check_compliance_preflight` (the DNC, capacity
    and email-permission authority), `suppression_entries` (the phone
    authority), the lead's own permission columns, the customer's feature
    entitlements, and the employee's configured channels. Where the business
    has not made a rule, this engine does not make one up — it records what
    it saw and, where the uncertainty is material, asks for review.

    SCRAPED OR PUBLIC CONTACT DATA IS NOT CONSENT, and nothing here
    manufactures a consent record. A contact whose permission state is
    unknown is contacted only where the platform's existing send paths would
    already contact them; the AI employee gets no wider licence than a person
    using the product by hand, ever.

    A REFUSAL IS EXPLAINABLE. Every answer carries reason codes — a test can
    assert on them and a screen can render them months later to somebody who
    is not looking at the lead as it is today. `contact_not_eligible` on its
    own is not an answer to "why".

TWO ENGINES MAY BOTH SAY NO AND NEITHER MAY SAY YES FOR THE OTHER. When T6's
own eligibility engine is deployed, its answer is merged in — and merging can
only tighten. That asymmetry is deliberate: two systems that can each grant
permission is one system too many.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.services.ai_operations import constants as C
from app.services.ai_operations import contracts

_log = logging.getLogger(__name__)

POLICY_VERSION = "ops-eligibility-1"

# Ranked worst-first: merging two answers takes the more restrictive.
_RANK = {C.ALLOW: 0, C.REQUIRES_REVIEW: 1, C.DENY: 2}


@dataclass
class Decision:
    """The answer, with its working shown."""

    result: str = C.ALLOW
    reasons: List[Dict[str, Any]] = field(default_factory=list)
    decided_by: str = "ai_operations"
    policy_version: str = POLICY_VERSION
    channel: Optional[str] = None
    subject_id: Optional[str] = None
    notes: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.result == C.ALLOW

    @property
    def needs_review(self) -> bool:
        return self.result == C.REQUIRES_REVIEW

    @property
    def primary_code(self) -> Optional[str]:
        return self.reasons[0]["code"] if self.reasons else None

    def add(self, result: str, code: str, detail: str, *,
            decided_by: Optional[str] = None) -> "Decision":
        """Record an answer. TIGHTENS ONLY — a later ALLOW never clears an
        earlier DENY, which is what makes the order of the checks below a
        readability choice rather than a correctness one."""
        if result != C.ALLOW:
            self.reasons.append({"code": code, "detail": detail,
                                 "result": result})
            if _RANK[result] > _RANK[self.result]:
                self.result = result
                self.decided_by = decided_by or self.decided_by
        else:
            self.notes.append({"code": code, "detail": detail})
        return self

    def as_dict(self) -> Dict:
        return {
            "result": self.result,
            "reasons": list(self.reasons),
            "notes": list(self.notes),
            "decided_by": self.decided_by,
            "policy_version": self.policy_version,
            "channel": self.channel,
            "subject_id": self.subject_id,
        }


# ── the address of record, per channel ──────────────────────────────────────

def address_for(lead, channel: str) -> Optional[str]:
    """Which address this channel would actually use.

    `callback_phone` is NOT used. It is a number a family mentioned once on a
    call; the number every prior message, suppression check and consent
    record is reconciled against is `phone`, and quietly texting a different
    one is how a lead's identity drifts. Promoting a callback number to
    primary is an explicit human edit.
    """
    if channel == C.CHANNEL_EMAIL:
        return getattr(lead, "email", None)
    return getattr(lead, "phone", None)


def _permission_column(channel: str) -> str:
    return {C.CHANNEL_SMS: "allow_sms",
            C.CHANNEL_EMAIL: "allow_email",
            C.CHANNEL_VOICE: "allow_voice"}.get(channel, "allow_sms")


def _within_window(now: datetime, tzname: str,
                   window: Optional[Dict] = None) -> Tuple[bool, str]:
    """Is `now` inside the permitted communication window?

    THE WINDOW IS AN OUTER BOUND, NOT A PREFERENCE. A customer may configure
    a narrower one; nothing configures a wider one, because the harm this
    prevents — a text at three in the morning — is harm to a person who is
    not the customer.

    Compared in the employee's configured timezone when `zoneinfo` can
    resolve it, and in UTC otherwise. Never silently in the server's local
    time, which on Render is UTC and on a developer's machine is not.
    """
    start = int((window or {}).get("start_hour", C.DEFAULT_WINDOW_START_HOUR))
    end = int((window or {}).get("end_hour", C.DEFAULT_WINDOW_END_HOUR))
    start = max(0, min(23, start))
    end = max(0, min(23, end))
    # A configured window may only NARROW the platform default.
    start = max(start, C.DEFAULT_WINDOW_START_HOUR)
    end = min(end, C.DEFAULT_WINDOW_END_HOUR)

    local = now
    try:
        from datetime import timezone as _tz
        from zoneinfo import ZoneInfo
        aware = now if now.tzinfo else now.replace(tzinfo=_tz.utc)
        local = aware.astimezone(ZoneInfo(tzname or C.DEFAULT_WINDOW_TIMEZONE))
    except Exception:                                        # noqa: BLE001
        _log.debug("ai_operations: timezone %r unresolved; comparing in UTC",
                   tzname)
    hour = local.hour
    if start <= hour < end:
        return True, ""
    return False, ("%s local time is outside the permitted contact window "
                   "(%02d:00–%02d:00 %s)."
                   % (local.strftime("%H:%M"), start, end,
                      tzname or C.DEFAULT_WINDOW_TIMEZONE))


# ═══════════════════════════════════════════════════════════════════════════
# THE GATE
# ═══════════════════════════════════════════════════════════════════════════

def evaluate(db: Session, *, ctx: contracts.EmployeeContext, lead,
             channel: str, thread=None, now: Optional[datetime] = None,
             window: Optional[Dict] = None) -> Decision:
    """The full contact-eligibility answer for one attempt.

    Never raises. A gate that cannot be evaluated is a gate that says
    REQUIRES_REVIEW, never one that says ALLOW: an engine that fails open
    under load is an engine that texts somebody who opted out on the day the
    database was slow.
    """
    now = now or datetime.utcnow()
    d = Decision(channel=channel,
                 subject_id=getattr(lead, "id", None) if lead else None)

    # ── 1. THE RECORD EXISTS AND IS IN THIS TENANT ─────────────────────────
    #
    # First because it is the most absolute: everything below reads columns
    # off this row, and reading another tenant's row is the leak.
    if lead is None:
        return d.add(C.DENY, C.E_RECORD_NOT_FOUND,
                     "The contact record could not be loaded.")
    if getattr(lead, "organization_id", None) != ctx.organization_id:
        return d.add(C.DENY, C.E_TENANT_MISMATCH,
                     "The contact belongs to a different organization than "
                     "the employee attempting to reach them.")

    # ── 2. THE CHANNEL IS ONE THIS EMPLOYEE HOLDS ──────────────────────────
    if channel not in C.ALL_CHANNELS:
        return d.add(C.DENY, C.E_CHANNEL_PERMISSION,
                     "'%s' is not a channel this platform supports." % channel)
    if not ctx.channel_allowed(channel):
        d.add(C.DENY, C.E_CHANNEL_NOT_ENABLED,
              "This employee is not configured for %s." % channel)

    # ── 3. THE CUSTOMER'S FEATURE IS ON ────────────────────────────────────
    #
    # Enforced in every stage including simulation. An organization whose
    # `sms` feature is off must not have an AI employee simulating a world in
    # which it is on, because that simulation is what somebody will later
    # point at as proof it works.
    try:
        from app.models.models import Organization
        from app.services import entitlements as platform_entitlements
        org = (db.query(Organization)
               .filter(Organization.id == ctx.organization_id).first())
        feature = C.CHANNEL_FEATURE.get(channel)
        if org is not None and feature and not platform_entitlements.org_has_feature(
                org, feature):
            d.add(C.DENY, C.E_CHANNEL_FEATURE_OFF,
                  "This organization is not enabled for the '%s' feature."
                  % feature)
    except Exception as exc:                                 # noqa: BLE001
        d.add(C.REQUIRES_REVIEW, C.E_POLICY_REVIEW,
              "The customer's feature entitlements could not be read (%s)."
              % exc.__class__.__name__)

    # ── 4. A PERSON MAY ALREADY OWN THIS CONVERSATION ──────────────────────
    #
    # Checked here rather than only in the orchestrator so that a scheduled
    # follow-up re-evaluated at execution time sees it too.
    if thread is not None and getattr(thread, "human_owner_user_id", None):
        d.add(C.DENY, C.E_HUMAN_OWNED,
              "A person has taken over this conversation; the AI employee "
              "does not act while they own it.")
    if thread is not None and (getattr(thread, "stop_reason", None)
                               in C.HARD_STOP_REASONS):
        d.add(C.DENY, C.E_ALREADY_RESPONDED_STOP,
              "This conversation was stopped (%s) and does not resume."
              % thread.stop_reason)

    # ── 5. THE PLATFORM'S OWN COMPLIANCE AUTHORITY ─────────────────────────
    #
    # `check_compliance_preflight` is the existing gate every send path in the
    # product consults: DNC on the lead, the plan-capacity hold, the phone
    # suppression list, and the email permission columns. Calling it rather
    # than re-implementing it is the whole point — a second implementation
    # would drift, and the day it drifted the AI would be the path that still
    # sent.
    try:
        from app.services import compliance_service
        preflight_channel = (compliance_service.CHANNEL_EMAIL
                             if channel == C.CHANNEL_EMAIL
                             else compliance_service.CHANNEL_SMS)
        compliance_service.check_compliance_preflight(db, lead,
                                                      channel=preflight_channel)
    except ValueError as exc:
        d.add(C.DENY, C.E_PLATFORM_REFUSED, str(exc),
              decided_by="compliance_service.check_compliance_preflight")
    except Exception as exc:                                 # noqa: BLE001
        # AN UNREADABLE COMPLIANCE AUTHORITY IS NOT A PERMISSION.
        d.add(C.REQUIRES_REVIEW, C.E_POLICY_REVIEW,
              "The compliance preflight could not be evaluated (%s)."
              % exc.__class__.__name__)

    # Voice is not covered by the preflight's channel vocabulary, so the two
    # phone-shaped rules are asked directly for it. Deliberately the same two
    # rules rather than new ones.
    if channel == C.CHANNEL_VOICE:
        status = getattr(getattr(lead, "status", None), "value",
                         getattr(lead, "status", None))
        if status == "dnc":
            d.add(C.DENY, C.E_DNC,
                  "This contact is marked do-not-contact.")
        try:
            from app.services import compliance_service as _cs
            phone = getattr(lead, "phone", None)
            if phone and _cs.is_phone_suppressed(db, ctx.organization_id,
                                                 phone):
                d.add(C.DENY, C.E_SUPPRESSED,
                      "This number is on the organization's suppression list.")
        except Exception:                                    # noqa: BLE001
            d.add(C.REQUIRES_REVIEW, C.E_POLICY_REVIEW,
                  "The suppression list could not be read for a voice call.")

    # ── 6. THE CONTACT'S OWN CHANNEL PERMISSION ────────────────────────────
    #
    # ONLY AN EXPLICIT FALSE BLOCKS. NULL means the source system never said,
    # which is the state most imported rows are in and is not a denial — the
    # platform's own reading, in compliance_service._check_email_permission.
    # Reading NULL as a denial here would silently stop mail the product
    # sends today; reading it as consent would be inventing one. It is
    # recorded as a note either way, so a reviewer can see what was known.
    column = _permission_column(channel)
    permission = getattr(lead, column, None)
    if permission is False:
        d.add(C.DENY, C.E_CHANNEL_PERMISSION,
              "This contact has opted out of %s." % channel)
    elif permission is None:
        d.add(C.ALLOW, C.E_CONSENT_UNKNOWN,
              "No explicit %s preference of record; the source system never "
              "stated one." % channel)

    # ── 7. THERE IS SOMEWHERE TO SEND IT ───────────────────────────────────
    address = address_for(lead, channel)
    if not address:
        d.add(C.DENY, C.E_NO_ADDRESS,
              "This contact has no %s address of record."
              % ("email" if channel == C.CHANNEL_EMAIL else "phone number"))
    if channel == C.CHANNEL_EMAIL and (getattr(lead, "manual_flag", None)
                                       or "") == "bad_email":
        d.add(C.DENY, C.E_BAD_ADDRESS,
              "This email address is flagged as unusable.")

    # ── 8. RECORDS THAT ARE NOT REALLY PEOPLE ──────────────────────────────
    #
    # A test record and an unresolved duplicate are both "do not contact
    # automatically, ask a person" rather than "never contact": a duplicate
    # is a data-quality condition and treating it as a DNC is a mistake this
    # codebase has already made once and undone.
    if getattr(lead, "is_test", False):
        d.add(C.DENY, C.E_TEST_RECORD,
              "This is a test record and is not contactable.")
    if getattr(lead, "is_duplicate", False) and not getattr(
            lead, "duplicate_resolved_at", None):
        d.add(C.REQUIRES_REVIEW, C.E_DUPLICATE_RECORD,
              "This record is flagged as a possible duplicate; a person "
              "should resolve it before an employee works it.")
    if getattr(lead, "capacity_state", None) == "over_capacity":
        d.add(C.DENY, C.E_CAPACITY_HOLD,
              "This contact is held over plan capacity.")

    # ── 9. THE TIME OF DAY ─────────────────────────────────────────────────
    #
    # Email is exempt: an email arriving at 3am wakes nobody, and holding
    # queued mail until morning would make every overnight reply look
    # unanswered. SMS and voice are not exempt.
    if channel in (C.CHANNEL_SMS, C.CHANNEL_VOICE):
        ok, why = _within_window(now, ctx.timezone, window)
        if not ok:
            d.add(C.DENY, C.E_OUTSIDE_WINDOW, why)

    # ── 10. T6'S OWN ANSWER, MERGED — TIGHTENING ONLY ──────────────────────
    delegated = contracts.delegate_eligibility(db, ctx, lead=lead,
                                               channel=channel)
    if delegated is not None:
        result, reasons = delegated
        if result != C.ALLOW:
            for r in (reasons or [{"code": "workforce_eligibility",
                                   "detail": "The workforce eligibility "
                                             "engine refused."}]):
                d.add(result, r.get("code", "workforce_eligibility"),
                      r.get("detail", ""),
                      decided_by="workforce.eligibility")

    _mirror(db, ctx, lead=lead, channel=channel, decision=d, thread=thread)
    return d


def _mirror(db: Session, ctx: contracts.EmployeeContext, *, lead, channel: str,
            decision: Decision, thread=None) -> None:
    """Store the answer in T6's `ai_eligibility_results` when that table
    exists, so one table holds every eligibility decision the workforce made
    rather than two that have to be reconciled."""
    if not contracts.T6_PRESENT:
        return
    try:
        import json

        from app.models.workforce_models import AIEligibilityResult
        db.add(AIEligibilityResult(
            organization_id=ctx.organization_id, employee_id=ctx.employee_id,
            work_item_id=(getattr(thread, "work_item_id", None)
                          if thread is not None else None),
            subject_type="lead", subject_id=getattr(lead, "id", "") or "",
            channel=channel, result=decision.result,
            reasons=json.dumps(decision.reasons),
            decided_by=decision.decided_by,
            policy_version=decision.policy_version))
        db.flush()
    except Exception as exc:                                 # noqa: BLE001
        _log.info("ai_operations: eligibility mirror skipped (%s)", exc)
