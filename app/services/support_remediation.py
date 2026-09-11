"""THE AUTO-FIXER — a registry, not a licence.

WHAT THIS REFUSES TO BE
-----------------------
There is no path here by which a language model executes arbitrary SQL, an
arbitrary mutation, or a shell command. There is no `run(sql)` and no
`update(table, where, values)`. What exists is a REGISTRY of named
remediations, each written by hand, each with a validator, a snapshot, an
executor, a verifier and a customer-safe explanation. The model — or a sensor,
or a person — chooses a NAME from that registry. Everything a fix does was
written by somebody who thought about what it does.

THE CONTRACT, IN ORDER, EVERY TIME
-----------------------------------
    DETECT → VALIDATE → SNAPSHOT → AUTHORIZE → EXECUTE → VERIFY → RECORD → REPORT

VERIFY IS NOT OPTIONAL AND IS NOT `return True`. A fix is FIXED when a second,
independent read says the condition is gone. `SupportFixRun.verified` is a
separate column from `status` precisely so "it ran" and "we checked" can never
be confused, and nothing is ever REPORTED to a customer as fixed unless
`verified` is True.

RISK CLASSES
------------
    SAFE_AUTO     repairs DERIVED state. Idempotent, no customer data, no
                  third-party call, no money, no permissions. May run
                  automatically.
    CONTROLLED    touches state a customer can see. Runs automatically only
                  where a `SupportFixPolicy` row explicitly enables it for
                  that scope. Default is off, everywhere, always.
    GOD_APPROVAL  diagnosed and prepared; a named god_admin presses the
                  button. Nothing executes from a conversation.
    ENGINEERING   there IS no runtime remediation. Produce evidence and a
                  recommendation. This module does not write or deploy code,
                  and building something that did would be building the thing
                  the specification says not to build.

WHY EACH REGISTERED FIX'S SAFETY WAS CHECKED RATHER THAN ASSUMED
-----------------------------------------------------------------
"Retry a failed job" sounds safe and is not: in this codebase a failed message
retry would put a real SMS in front of a real family, so no such remediation
is registered. What IS registered is the set whose blast radius was read in
the code: cached availability windows that `calendar_models` documents AS a
cache, an SLA state column this module owns, an entitlement snapshot derived
from the catalogue, and a tier seed that refuses to run unless the table is
genuinely empty.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.models import Organization, User
from app.models.support_models import (
    AuthorizationSource, FixStatus, RiskClass, SupportFixPolicy, SupportFixRun,
)

log = logging.getLogger(__name__)

SCOPE_ORGANIZATION = "organization"
SCOPE_TICKET = "ticket"
SCOPE_PLATFORM = "platform"

# Mirrors `support_diagnostics.CALENDAR_FAILURE_THRESHOLD`. Imported rather
# than re-typed so a change to what "stuck" means moves the detector and the
# fix together — a fix whose trigger disagrees with its detector runs on
# connections nobody reported and skips the ones somebody did.
from app.services.support_diagnostics import CALENDAR_FAILURE_THRESHOLD  # noqa: E402


class FixRefused(Exception):
    """This fix will not run, and the reason is safe to show a human."""


class FixContext:
    """Everything a remediation may touch. Built by the caller, never by a model."""

    __slots__ = ("db", "org", "actor", "ticket", "is_god", "now", "params")

    def __init__(self, db: Session, *, org: Optional[Organization] = None,
                 actor: Optional[User] = None, ticket=None, is_god: bool = False,
                 now: Optional[datetime] = None,
                 params: Optional[Dict[str, Any]] = None):
        self.db = db
        self.org = org
        self.actor = actor
        self.ticket = ticket
        self.is_god = bool(is_god)
        self.now = now or datetime.utcnow()
        # PARAMS ARE NEVER CUSTOMER TEXT. Only a server-side caller populates
        # this, and every registered fix that reads it validates what it finds.
        self.params = params or {}


class Remediation:
    __slots__ = ("action_key", "label", "description", "risk_class", "scope",
                 "service", "resource_type", "idempotent", "validate",
                 "snapshot", "execute", "verify", "rollback_note",
                 "customer_explanation", "technical_explanation")

    def __init__(self, action_key: str, label: str, *, description: str,
                 risk_class: str, scope: str, service: Optional[str],
                 resource_type: Optional[str], idempotent: bool,
                 validate: Callable[[FixContext], Tuple[bool, str]],
                 snapshot: Callable[[FixContext], Dict[str, Any]],
                 execute: Callable[[FixContext], Dict[str, Any]],
                 verify: Callable[[FixContext], Tuple[bool, str, Dict[str, Any]]],
                 customer_explanation: str, technical_explanation: str,
                 rollback_note: Optional[str] = None):
        self.action_key = action_key
        self.label = label
        self.description = description
        self.risk_class = risk_class
        self.scope = scope
        self.service = service
        self.resource_type = resource_type
        self.idempotent = idempotent
        self.validate = validate
        self.snapshot = snapshot
        self.execute = execute
        self.verify = verify
        self.customer_explanation = customer_explanation
        self.technical_explanation = technical_explanation
        self.rollback_note = rollback_note

    def as_dict(self) -> Dict[str, Any]:
        return {
            "action_key": self.action_key,
            "label": self.label,
            "description": self.description,
            "risk_class": self.risk_class,
            "risk_label": RiskClass.LABELS[self.risk_class],
            "scope": self.scope,
            "service": self.service,
            "resource_type": self.resource_type,
            "idempotent": self.idempotent,
            "requires_approval": self.risk_class == RiskClass.GOD_APPROVAL,
            "executable": self.risk_class != RiskClass.ENGINEERING,
            "customer_explanation": self.customer_explanation,
            "technical_explanation": self.technical_explanation,
            "rollback": self.rollback_note,
        }


REGISTRY: Dict[str, Remediation] = {}


def _register(remediation: Remediation) -> None:
    REGISTRY[remediation.action_key] = remediation


# ══════════════════════════════════════════════════════════════════════════
# SAFE_AUTO — derived state only
# ══════════════════════════════════════════════════════════════════════════

def _sla_validate(ctx: FixContext) -> Tuple[bool, str]:
    if ctx.ticket is None:
        return False, "There is no ticket to recalculate."
    return True, ""


def _sla_snapshot(ctx: FixContext) -> Dict[str, Any]:
    t = ctx.ticket
    return {"sla_state": t.sla_state, "first_response_due_at":
            t.first_response_due_at.isoformat() if t.first_response_due_at else None,
            "sla_elapsed_minutes": int(t.sla_elapsed_minutes or 0)}


def _sla_execute(ctx: FixContext) -> Dict[str, Any]:
    from app.services import support_sla
    before = ctx.ticket.sla_state
    result = support_sla.refresh(ctx.db, ctx.ticket, now=ctx.now)
    ctx.db.flush()
    return {"from": before, "to": result["state"], "changed": before != result["state"]}


def _sla_verify(ctx: FixContext) -> Tuple[bool, str, Dict[str, Any]]:
    """A SECOND, INDEPENDENT EVALUATION.

    Not a re-read of what we just wrote — `evaluate()` recomputes from the
    ticket's timestamps, so this genuinely asks "is the stored value now the
    right one" rather than "did the assignment happen".
    """
    from app.services import support_sla
    expected = support_sla.evaluate(ctx.db, ctx.ticket, now=ctx.now)["state"]
    ok = ctx.ticket.sla_state == expected
    return ok, ("The SLA state now matches the ticket's own history."
                if ok else "The stored SLA state still disagrees with the computed one."), \
        {"stored": ctx.ticket.sla_state, "computed": expected}


_register(Remediation(
    "sla.recompute_ticket_sla", "Recalculate SLA state",
    description="Recompute a ticket's cached SLA state from its own timestamps.",
    risk_class=RiskClass.SAFE_AUTO, scope=SCOPE_TICKET, service="support",
    resource_type="support_ticket", idempotent=True,
    validate=_sla_validate, snapshot=_sla_snapshot, execute=_sla_execute,
    verify=_sla_verify,
    customer_explanation="We refreshed the status shown on your request.",
    technical_explanation="support_tickets.sla_state is a cache for queue "
                          "filtering; support_sla.evaluate is the authority. "
                          "This re-syncs the cache. No customer data is touched.",
    rollback_note="Not required — the value is derived and recomputing it "
                  "again always converges."))


def _clock_validate(ctx: FixContext) -> Tuple[bool, str]:
    from app.models.support_models import TicketStatus
    t = ctx.ticket
    if t is None:
        return False, "There is no ticket to repair."
    if t.sla_paused_at is None:
        return False, "This ticket's clock is not paused, so there is nothing to resume."
    if t.status == TicketStatus.WAITING_ON_CUSTOMER:
        return False, ("This ticket is legitimately waiting on the customer. "
                       "Resuming here would restart a clock that is correctly stopped.")
    return True, ""


def _clock_snapshot(ctx: FixContext) -> Dict[str, Any]:
    t = ctx.ticket
    return {"status": t.status, "sla_paused_at": t.sla_paused_at.isoformat()
            if t.sla_paused_at else None,
            "first_response_due_at": t.first_response_due_at.isoformat()
            if t.first_response_due_at else None}


def _clock_execute(ctx: FixContext) -> Dict[str, Any]:
    from app.services import support_sla
    resumed = support_sla.resume(ctx.db, ctx.ticket, now=ctx.now)
    ctx.db.flush()
    return {"resumed": resumed}


def _clock_verify(ctx: FixContext) -> Tuple[bool, str, Dict[str, Any]]:
    ok = ctx.ticket.sla_paused_at is None
    return ok, ("The response clock is running again." if ok
                else "The clock is still paused."), \
        {"sla_paused_at": ctx.ticket.sla_paused_at.isoformat()
         if ctx.ticket.sla_paused_at else None}


_register(Remediation(
    "ticket.resume_stalled_sla_clock", "Resume a stalled response clock",
    description="A ticket that left Waiting-on-Customer but whose SLA clock was "
                "never restarted.",
    risk_class=RiskClass.SAFE_AUTO, scope=SCOPE_TICKET, service="support",
    resource_type="support_ticket", idempotent=True,
    validate=_clock_validate, snapshot=_clock_snapshot, execute=_clock_execute,
    verify=_clock_verify,
    customer_explanation="We restarted the response clock on your request, so "
                         "it is back in our queue.",
    technical_explanation="Pushes first_response_due_at out by the paused "
                          "interval and clears sla_paused_at. Refuses on a "
                          "ticket still in WAITING_ON_CUSTOMER.",
    rollback_note="Pausing again restores the previous state; the elapsed "
                  "total is never reduced by either direction."))


def _snapshot_validate(ctx: FixContext) -> Tuple[bool, str]:
    if ctx.ticket is None:
        return False, "There is no ticket to refresh."
    if ctx.org is None:
        return False, "The ticket has no customer organization in context."
    return True, ""


def _snapshot_snapshot(ctx: FixContext) -> Dict[str, Any]:
    return {"entitlement_json": (ctx.ticket.entitlement_json or "")[:2000] or None}


def _snapshot_execute(ctx: FixContext) -> Dict[str, Any]:
    from app.services import support_entitlements
    entitlement = support_entitlements.resolve(ctx.db, ctx.org)
    ctx.ticket.entitlement_json = json.dumps(entitlement, default=str)
    ctx.db.flush()
    return {"plan_key": entitlement.get("plan_key"), "queue": entitlement.get("queue"),
            "source": entitlement.get("source")}


def _snapshot_verify(ctx: FixContext) -> Tuple[bool, str, Dict[str, Any]]:
    raw = ctx.ticket.entitlement_json
    try:
        parsed = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        parsed = None
    ok = isinstance(parsed, dict) and "queue" in parsed
    return ok, ("The plan details on this request are readable again."
                if ok else "The stored plan details are still unreadable."), \
        {"readable": ok}


_register(Remediation(
    "ticket.refresh_entitlement_snapshot", "Repair a ticket's plan snapshot",
    description="Rebuild the support entitlement snapshot on a ticket whose "
                "stored copy is missing or corrupt.",
    risk_class=RiskClass.SAFE_AUTO, scope=SCOPE_TICKET, service="support",
    resource_type="support_ticket", idempotent=True,
    validate=_snapshot_validate, snapshot=_snapshot_snapshot,
    execute=_snapshot_execute, verify=_snapshot_verify,
    customer_explanation="We repaired the plan details attached to your request.",
    technical_explanation="Rewrites support_tickets.entitlement_json from "
                          "support_entitlements.resolve(). NOTE: this makes the "
                          "ticket adopt TODAY'S entitlement, so it is a repair "
                          "for an unreadable snapshot and not a routine refresh.",
    rollback_note="The previous value is recorded in before_state_json."))


def _busy_validate(ctx: FixContext) -> Tuple[bool, str]:
    if ctx.org is None:
        return False, "There is no organization in context."
    return True, ""


def _org_calendar_connections(ctx: FixContext):
    from app.models.calendar_models import CalendarConnection
    user_ids = [row[0] for row in ctx.db.query(User.id)
                .filter(User.organization_id == ctx.org.id).all()]
    if not user_ids:
        return []
    return (ctx.db.query(CalendarConnection)
            .filter(CalendarConnection.user_id.in_(user_ids)).all())


def _busy_snapshot(ctx: FixContext) -> Dict[str, Any]:
    return {"connections": [
        {"provider": c.provider,
         "busy_fetched_at": c.busy_fetched_at.isoformat() if c.busy_fetched_at else None}
        for c in _org_calendar_connections(ctx)]}


def _busy_execute(ctx: FixContext) -> Dict[str, Any]:
    cleared = 0
    for conn in _org_calendar_connections(ctx):
        if conn.busy_fetched_at is not None or conn.busy_window_start is not None:
            conn.busy_fetched_at = None
            conn.busy_window_start = None
            conn.busy_window_end = None
            cleared += 1
    ctx.db.flush()
    return {"connections_cleared": cleared}


def _busy_verify(ctx: FixContext) -> Tuple[bool, str, Dict[str, Any]]:
    remaining = [c for c in _org_calendar_connections(ctx)
                 if c.busy_fetched_at is not None]
    ok = not remaining
    return ok, ("Availability will be read fresh from the calendar provider "
                "on the next lookup." if ok
                else "Some cached availability windows are still marked fresh."), \
        {"still_cached": len(remaining)}


_register(Remediation(
    "calendar.refresh_busy_cache", "Force a fresh availability read",
    description="Clear the cached free/busy window markers so the next "
                "availability lookup re-reads the provider.",
    risk_class=RiskClass.SAFE_AUTO, scope=SCOPE_ORGANIZATION, service="calendar",
    resource_type="calendar_connection", idempotent=True,
    validate=_busy_validate, snapshot=_busy_snapshot, execute=_busy_execute,
    verify=_busy_verify,
    customer_explanation="We cleared the saved copy of your calendar's busy "
                         "times so the next check reads it fresh.",
    technical_explanation="Nulls busy_fetched_at / busy_window_start / "
                          "busy_window_end on calendar_connections. "
                          "calendar_models documents these as a CACHE whose "
                          "only purpose is avoiding a vendor round trip; "
                          "external_busy_blocks rows are NOT deleted, so no "
                          "record of anything is lost.",
    rollback_note="Not required — the cache repopulates on the next lookup."))


# ══════════════════════════════════════════════════════════════════════════
# CONTROLLED — customer-visible state, policy gated
# ══════════════════════════════════════════════════════════════════════════

def _reauth_validate(ctx: FixContext) -> Tuple[bool, str]:
    if ctx.org is None:
        return False, "There is no organization in context."
    stuck = [c for c in _org_calendar_connections(ctx)
             if c.is_connected and (c.failure_count or 0) >= CALENDAR_FAILURE_THRESHOLD]
    if not stuck:
        return False, ("No calendar connection is stuck retrying, so there is "
                       "nothing to move to reconnect-required.")
    return True, ""


def _reauth_snapshot(ctx: FixContext) -> Dict[str, Any]:
    return {"connections": [
        {"provider": c.provider, "is_connected": bool(c.is_connected),
         "failure_count": int(c.failure_count or 0),
         "last_error_at": c.last_error_at.isoformat() if c.last_error_at else None}
        for c in _org_calendar_connections(ctx)]}


_REAUTH_MESSAGE = ("Reconnection required — the calendar provider is refusing "
                   "the saved authorization. Reconnect this calendar in Settings.")


def _reauth_execute(ctx: FixContext) -> Dict[str, Any]:
    moved = []
    for conn in _org_calendar_connections(ctx):
        if conn.is_connected and (conn.failure_count or 0) >= CALENDAR_FAILURE_THRESHOLD:
            conn.is_connected = False
            conn.disconnected_at = ctx.now
            conn.last_error = _REAUTH_MESSAGE
            conn.last_error_at = ctx.now
            moved.append(conn.provider)
    ctx.db.flush()
    return {"providers_moved": moved, "count": len(moved)}


def _reauth_verify(ctx: FixContext) -> Tuple[bool, str, Dict[str, Any]]:
    remaining = [c for c in _org_calendar_connections(ctx)
                 if c.is_connected and (c.failure_count or 0) >= CALENDAR_FAILURE_THRESHOLD]
    ok = not remaining
    return ok, ("No calendar is stuck retrying an authorization the provider "
                "has refused." if ok
                else "A calendar is still marked connected while failing."), \
        {"still_stuck": len(remaining)}


_register(Remediation(
    "calendar.mark_reauthorization_required", "Stop a futile calendar retry loop",
    description="A calendar that reports connected while the provider refuses "
                "its authorization is moved to reconnect-required so the "
                "customer is shown a Reconnect action instead of silence.",
    risk_class=RiskClass.CONTROLLED, scope=SCOPE_ORGANIZATION, service="calendar",
    resource_type="calendar_connection", idempotent=True,
    validate=_reauth_validate, snapshot=_reauth_snapshot, execute=_reauth_execute,
    verify=_reauth_verify,
    customer_explanation="Your calendar's connection has expired. We've stopped "
                         "the failing retries and put a Reconnect button in "
                         "Settings — reconnecting takes about a minute.",
    technical_explanation="Sets calendar_connections.is_connected = False and "
                          "writes a reconnect message into last_error for "
                          "connections whose failure_count has passed the "
                          "threshold. CONTROLLED rather than safe: it changes "
                          "state the customer sees and stops sync until they "
                          "act, so a false positive is visible to them.",
    rollback_note="Only the customer can undo this, by reconnecting. That is "
                  "why it is policy-gated rather than automatic."))


def _tiers_validate(ctx: FixContext) -> Tuple[bool, str]:
    from app.models.models import TierDefinition
    if ctx.org is None:
        return False, "There is no organization in context."
    count = (ctx.db.query(TierDefinition)
             .filter(TierDefinition.organization_id == ctx.org.id).count())
    if count > 0:
        # THE GUARD THAT MAKES THIS SAFE TO REGISTER AT ALL. Seeding over
        # existing rows would overwrite an organization's own configuration,
        # and `clear_and_reseed_tier_definitions` exists precisely because
        # that is a deliberate, destructive act somebody has to choose.
        return False, ("This organization already has %d lead tier definitions. "
                       "Re-seeding would overwrite their own configuration."
                       % count)
    return True, ""


def _tiers_snapshot(ctx: FixContext) -> Dict[str, Any]:
    from app.models.models import TierDefinition
    return {"tier_definition_count": (
        ctx.db.query(TierDefinition)
        .filter(TierDefinition.organization_id == ctx.org.id).count())}


def _tiers_execute(ctx: FixContext) -> Dict[str, Any]:
    from app.services.tier_config_service import seed_default_tier_definitions
    industry = (getattr(ctx.org, "industry", None) or "funeral")
    created = seed_default_tier_definitions(ctx.db, ctx.org.id, industry=industry)
    ctx.db.flush()
    return {"industry": industry, "created": len(created or [])}


def _tiers_verify(ctx: FixContext) -> Tuple[bool, str, Dict[str, Any]]:
    from app.models.models import TierDefinition
    count = (ctx.db.query(TierDefinition)
             .filter(TierDefinition.organization_id == ctx.org.id).count())
    ok = count > 0
    return ok, ("Lead tiers are configured for this workspace." if ok
                else "This workspace still has no lead tier definitions."), \
        {"tier_definition_count": count}


_register(Remediation(
    "org.reseed_tier_definitions", "Restore missing lead tier configuration",
    description="An organization with NO tier definitions was never fully "
                "provisioned; seed the industry default set.",
    risk_class=RiskClass.CONTROLLED, scope=SCOPE_ORGANIZATION, service="platform",
    resource_type="organization", idempotent=False,
    validate=_tiers_validate, snapshot=_tiers_snapshot, execute=_tiers_execute,
    verify=_tiers_verify,
    customer_explanation="Your workspace was missing its lead categories. "
                         "We've restored the standard set — you can rename or "
                         "change them in Settings.",
    technical_explanation="Calls tier_config_service.seed_default_tier_definitions. "
                          "REFUSES if any row already exists, so it can only "
                          "ever fill a genuine void, never overwrite a "
                          "customer's own configuration.",
    rollback_note="Seeded rows are listed in after_state_json and can be "
                  "removed individually; there is no bulk undo, which is why "
                  "this is policy-gated."))


# ══════════════════════════════════════════════════════════════════════════
# GOD_APPROVAL — money, permissions, third parties
# ══════════════════════════════════════════════════════════════════════════

def _resync_validate(ctx: FixContext) -> Tuple[bool, str]:
    if ctx.org is None:
        return False, "There is no organization in context."
    if not getattr(ctx.org, "stripe_subscription_id", None):
        return False, ("This organization has no subscription recorded, so "
                       "there is no mirror to refresh.")
    return True, ""


def _resync_snapshot(ctx: FixContext) -> Dict[str, Any]:
    org = ctx.org
    return {"billing_status": getattr(org, "billing_status", None),
            "billing_plan_key": getattr(org, "billing_plan_key", None),
            "billing_commitment": getattr(org, "billing_commitment", None),
            "billing_current_period_end": (
                org.billing_current_period_end.isoformat()
                if getattr(org, "billing_current_period_end", None) else None)}


def _resync_execute(ctx: FixContext) -> Dict[str, Any]:
    from app.services.billing_resync import resync_subscription
    return resync_subscription(ctx.db, ctx.org, dry_run=False)


def _resync_verify(ctx: FixContext) -> Tuple[bool, str, Dict[str, Any]]:
    """A DRY RUN AS THE VERIFIER.

    `resync_subscription(dry_run=True)` re-reads the processor and reports what
    a refresh WOULD change. An empty `changed` list after a real refresh is
    positive evidence that the mirror now agrees with Stripe — which is a
    genuinely independent check, not a re-read of our own write.
    """
    from app.services.billing_resync import resync_subscription
    result = resync_subscription(ctx.db, ctx.org, dry_run=True)
    changed = result.get("changed") or []
    ok = not changed
    return ok, ("The local subscription record now matches the payment "
                "processor." if ok else
                "The local record still differs from the payment processor."), \
        {"still_different": changed}


_register(Remediation(
    "billing.resync_subscription_mirror", "Refresh a subscription from Stripe",
    description="Re-read one organization's subscription from the payment "
                "processor and correct the local mirror.",
    risk_class=RiskClass.GOD_APPROVAL, scope=SCOPE_ORGANIZATION, service="billing",
    resource_type="organization", idempotent=True,
    validate=_resync_validate, snapshot=_resync_snapshot, execute=_resync_execute,
    verify=_resync_verify,
    customer_explanation="We refreshed your subscription details from our "
                         "payment processor.",
    technical_explanation="Delegates to the existing billing_resync service — "
                          "there is no second billing implementation here. "
                          "GOD_APPROVAL because it writes billing columns that "
                          "gate access and feed compensation.",
    rollback_note="The prior mirror is in before_state_json. Stripe remains "
                  "the source of truth, so a 'rollback' would mean writing "
                  "values Stripe disagrees with and is deliberately not offered."))


# ══════════════════════════════════════════════════════════════════════════
# ENGINEERING — evidence, never execution
# ══════════════════════════════════════════════════════════════════════════

def _eng_validate(ctx: FixContext) -> Tuple[bool, str]:
    return False, ("This is an engineering fix. The platform does not modify "
                   "or deploy its own application code; the evidence and a "
                   "recommendation are recorded for a person to act on.")


def _eng_never(ctx: FixContext):
    raise FixRefused("An engineering remediation is never executed by the platform.")


_register(Remediation(
    "platform.engineering_fix_required", "Engineering fix required",
    description="No runtime remediation exists. Record evidence, suspected "
                "root cause, affected scope and a recommended code change.",
    risk_class=RiskClass.ENGINEERING, scope=SCOPE_PLATFORM, service="platform",
    resource_type=None, idempotent=True,
    validate=_eng_validate, snapshot=lambda ctx: {}, execute=_eng_never,
    verify=lambda ctx: (False, "Not applicable.", {}),
    customer_explanation="We've identified the cause and passed it to our "
                         "engineering team. We'll update you here.",
    technical_explanation="Terminal class. execute() raises by construction so "
                          "that no future caller can turn this into an "
                          "autonomous production coder by supplying a handler.",
    rollback_note=None))


# ══════════════════════════════════════════════════════════════════════════
# AUTHORIZATION — resolved on the server, before anything runs
# ══════════════════════════════════════════════════════════════════════════

class Authorization:
    """The answer to "may this run, now, here, for this caller?"."""

    __slots__ = ("allowed", "source", "reason", "stage")

    def __init__(self, allowed: bool, *, source: Optional[str] = None,
                 reason: str = "", stage: str = ""):
        self.allowed = bool(allowed)
        self.source = source
        self.reason = reason
        self.stage = stage

    def __bool__(self) -> bool:
        return self.allowed


def policy_for(db: Session, action_key: str, *, org: Optional[Organization],
               platform_id: Optional[str]) -> Optional[SupportFixPolicy]:
    """The most specific active policy for this action, or None.

    ORGANIZATION BEATS PLATFORM, and there is no third level. A policy system
    with inheritance is a policy system nobody can read in one line, and the
    failure mode of that is granting more than anybody intended.
    """
    rows = (db.query(SupportFixPolicy)
            .filter(SupportFixPolicy.action_key == action_key,
                    SupportFixPolicy.is_active.is_(True))
            .all())
    org_id = getattr(org, "id", None)
    for row in rows:
        if org_id and row.organization_id == org_id:
            return row
    for row in rows:
        if platform_id and row.platform_id == platform_id and not row.organization_id:
            return row
    return None


def _runs_today(db: Session, action_key: str, org: Optional[Organization],
                now: datetime) -> int:
    """How many times this repair ACTUALLY RAN today, for this scope.

    COUNTS EXECUTIONS, NOT ATTEMPTS, and the distinction is load-bearing
    twice over:

      * `execute_fix` creates the run row BEFORE it asks for authority, so
        the in-flight attempt is already in the table when this is called.
        Counting every row would make a cap of 1 refuse the FIRST run — the
        attempt would consume its own budget and the repair would never once
        execute.

      * A refusal is not a use. A cap is a limit on how much this repair may
        CHANGE in a day; a proposal that was declined, or a validation that
        found nothing to do, changed nothing and must not spend the budget
        that a real problem later in the day needs.
    """
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    q = (db.query(SupportFixRun)
         .filter(SupportFixRun.action_key == action_key,
                 SupportFixRun.created_at >= start,
                 SupportFixRun.status.in_([FixStatus.FIXED, FixStatus.FAILED,
                                           FixStatus.ROLLED_BACK,
                                           FixStatus.EXECUTING,
                                           FixStatus.VERIFYING])))
    if org is not None:
        q = q.filter(SupportFixRun.organization_id == org.id)
    return q.count()


def authorization_for(db: Session, remediation: Remediation, ctx: FixContext, *,
                      requested_by_kind: str = "ai") -> Authorization:
    """WHO may run this, and on whose authority. Server side, always.

    `requested_by_kind` is the CALLER'S identity as the request resolved it —
    "ai", "customer", "god", "system" — never a value that arrived in a
    payload. A customer cannot become "god" by typing it, because nothing here
    reads what they typed.
    """
    if remediation.risk_class == RiskClass.ENGINEERING:
        return Authorization(
            False, reason="This needs an engineering change. The platform does "
                          "not modify its own code.", stage="engineering")

    if remediation.risk_class == RiskClass.GOD_APPROVAL:
        if ctx.is_god and requested_by_kind == "god":
            return Authorization(True, source=AuthorizationSource.GOD_APPROVAL,
                                 reason="Approved by the platform owner.",
                                 stage="god")
        return Authorization(
            False, reason="This change needs approval from the platform team "
                          "before it can run.", stage="approval_required")

    if remediation.risk_class == RiskClass.CONTROLLED:
        # ROOT AUTHORITY STILL MEANS ROOT. God running a controlled fix
        # directly is not a policy bypass — it is the authority the policy is
        # a delegation of.
        if ctx.is_god and requested_by_kind == "god":
            return Authorization(True, source=AuthorizationSource.GOD_APPROVAL,
                                 reason="Run by the platform owner.", stage="god")
        policy = policy_for(db, remediation.action_key, org=ctx.org,
                            platform_id=getattr(ctx.org, "platform_id", None))
        if policy is None or not policy.auto_execute:
            return Authorization(
                False, reason="Automatic repair is not enabled for this type of "
                              "problem in this workspace.", stage="policy")
        if policy.max_runs_per_day is not None:
            used = _runs_today(db, remediation.action_key, ctx.org, ctx.now)
            if used >= int(policy.max_runs_per_day):
                return Authorization(
                    False, reason="This repair has already run its maximum "
                                  "number of times today.", stage="rate_limit")
        return Authorization(True, source=AuthorizationSource.POLICY_CONTROLLED,
                             reason="Enabled by platform policy for this scope.",
                             stage="policy")

    # SAFE_AUTO. Derived state only; a customer pressing "try again" on their
    # own ticket is a legitimate authority for it, and the org/ticket in the
    # context is the one their request authenticated into.
    if requested_by_kind == "customer":
        return Authorization(True, source=AuthorizationSource.CUSTOMER_SELF_SERVICE,
                             reason="Requested by the customer on their own record.",
                             stage="safe_auto")
    if requested_by_kind == "god":
        return Authorization(True, source=AuthorizationSource.GOD_APPROVAL,
                             reason="Run by the platform owner.", stage="god")
    return Authorization(True, source=AuthorizationSource.POLICY_AUTO,
                         reason="Registered as a safe automatic repair.",
                         stage="safe_auto")


# ══════════════════════════════════════════════════════════════════════════
# EXECUTION — the contract, in one function, for every fix
# ══════════════════════════════════════════════════════════════════════════

def _new_run(db: Session, remediation: Remediation, ctx: FixContext, *,
             detection_source: Optional[str], diagnosis: Optional[str],
             confidence: Optional[str], signature: Optional[str],
             conversation_id: Optional[str] = None,
             diagnostic_run_id: Optional[str] = None) -> SupportFixRun:
    run = SupportFixRun(
        action_key=remediation.action_key,
        organization_id=getattr(ctx.org, "id", None),
        platform_id=getattr(ctx.org, "platform_id", None),
        ticket_id=getattr(ctx.ticket, "id", None),
        conversation_id=conversation_id,
        diagnostic_run_id=diagnostic_run_id,
        status=FixStatus.DETECTED,
        risk_class=remediation.risk_class,
        target_resource_type=remediation.resource_type,
        detection_source=detection_source,
        diagnosis=diagnosis,
        confidence=confidence,
        issue_signature=signature,
        customer_explanation=remediation.customer_explanation,
        technical_explanation=remediation.technical_explanation,
        rollback_available=bool(remediation.rollback_note),
    )
    db.add(run)
    db.flush()
    return run


def _audit(db: Session, run: SupportFixRun, ctx: FixContext, action: str,
           before: Optional[Dict[str, Any]] = None,
           after: Optional[Dict[str, Any]] = None) -> None:
    """Write the HUMAN-ACTOR security ledger row, when there is a human actor.

    ═══════════════════════════════════════════════════════════════════════
    WHY THIS IS CONDITIONAL, AND WHERE THE REST OF THE AUDIT LIVES
    ═══════════════════════════════════════════════════════════════════════

    `audit_log_entries.actor_user_id` is NOT NULL. That is correct for what
    that table is: a ledger of WHO DID WHAT, read by administrators, where a
    row with no actor answers the only question it exists to answer with
    "nobody". A repair authorized by standing policy has no human actor by
    construction, so there is no honest row to write there.

    Attempting one anyway is not a harmless no-op — it is an IntegrityError
    on flush, and because the caller owns the transaction, it rolls back the
    REPAIR as well. An audit attempt that destroys the thing it was auditing
    is the worst available outcome, and this function used to do exactly that
    for every automatic run.

    THE FIX RUN IS THE RECORD OF RECORD FOR A SYSTEM-AUTHORIZED REPAIR, and
    it is a fuller one than the generic ledger could hold: `SupportFixRun`
    carries the action, the risk class, the authorization SOURCE, the
    approver when there was one, the detection source, the diagnosis, the
    before and after states, the verification result and the timings. Nothing
    is lost by not duplicating a subset of that under a fabricated actor.

    So: a human action is audited in BOTH places, and an automatic one is
    audited in the place that can describe it. The belt-and-braces is the
    savepoint below — any other failure in the ledger write is contained and
    cannot take the repair with it.
    """
    actor_id = getattr(ctx.actor, "id", None)
    if not actor_id:
        log.debug("support_remediation: %s on fix run %s was authorized by %s "
                  "and has no human actor; the fix run row is its audit record",
                  action, run.id, run.authorization_source or "policy")
        return

    try:
        # A NESTED TRANSACTION so a failure here cannot poison the caller's.
        # Without it, any IntegrityError in the ledger marks the whole session
        # for rollback and the repair that just succeeded is undone.
        with db.begin_nested():
            from app.routers.audit_log_router import log_action
            log_action(
                db, getattr(ctx.org, "id", None), actor_id,
                action=action, target_type="support_fix_run", target_id=run.id,
                platform_id=getattr(ctx.org, "platform_id", None),
                before=before, after=after,
                note="%s (%s)" % (run.action_key, run.risk_class),
                commit=False)
    except Exception:                                          # noqa: BLE001
        # A fix that ran unaudited is worth shouting about — but not worth
        # failing the fix over, and definitely not worth rolling it back.
        log.exception("support_remediation: audit write failed for fix run %s",
                      run.id)


def execute_fix(db: Session, action_key: str, ctx: FixContext, *,
                requested_by_kind: str = "ai",
                detection_source: Optional[str] = None,
                diagnosis: Optional[str] = None,
                confidence: Optional[str] = None,
                signature: Optional[str] = None,
                conversation_id: Optional[str] = None,
                diagnostic_run_id: Optional[str] = None,
                existing_run: Optional[SupportFixRun] = None) -> SupportFixRun:
    """Run one registered remediation, end to end, and record all of it.

    ALWAYS RETURNS A ROW. A refusal is a row with status APPROVAL_REQUIRED,
    FAILED or ESCALATED and a reason on it — never a silent no-op and never a
    bare exception, because "we decided not to" is exactly the thing an
    operator needs to be able to read back later.
    """
    remediation = REGISTRY.get(action_key)
    if remediation is None:
        raise FixRefused("There is no registered repair called %r." % action_key)

    run = existing_run or _new_run(
        db, remediation, ctx, detection_source=detection_source,
        diagnosis=diagnosis, confidence=confidence, signature=signature,
        conversation_id=conversation_id, diagnostic_run_id=diagnostic_run_id)
    run.status = FixStatus.DIAGNOSED
    run.started_at = ctx.now

    # ── VALIDATE. Before authority, deliberately: "there is nothing to fix"
    #    is a better answer than "you are not allowed to fix nothing".
    try:
        ok, why = remediation.validate(ctx)
    except Exception as exc:                                   # noqa: BLE001
        log.exception("support_remediation: validate failed for %s", action_key)
        return _fail(db, run, ctx, "Could not check whether this repair applies.",
                     exc)
    if not ok:
        run.status = FixStatus.RECOMMENDED
        run.verification_message = why
        run.finished_at = datetime.utcnow()
        db.flush()
        return run

    # ── AUTHORIZE
    auth = authorization_for(db, remediation, ctx, requested_by_kind=requested_by_kind)
    if not auth:
        run.status = (FixStatus.APPROVAL_REQUIRED
                      if remediation.risk_class in (RiskClass.GOD_APPROVAL,
                                                    RiskClass.CONTROLLED)
                      else FixStatus.ESCALATED)
        run.verification_message = auth.reason
        run.finished_at = datetime.utcnow()
        db.flush()
        _audit(db, run, ctx, "support.fix_refused",
               after={"stage": auth.stage, "reason": auth.reason})
        return run

    run.authorization_source = auth.source
    run.authorized_by = getattr(ctx.actor, "id", None)
    run.authorized_at = datetime.utcnow()
    run.status = FixStatus.AUTHORIZED
    db.flush()

    # ── SNAPSHOT. After authorization and before execution, so the recorded
    #    "before" is the state the change actually acted on.
    try:
        before = remediation.snapshot(ctx)
    except Exception:                                          # noqa: BLE001
        log.exception("support_remediation: snapshot failed for %s", action_key)
        before = {"note": "before-state could not be captured"}
    run.before_state_json = json.dumps(before, default=str)

    # ── EXECUTE
    run.status = FixStatus.EXECUTING
    db.flush()
    try:
        result = remediation.execute(ctx)
    except FixRefused as exc:
        return _fail(db, run, ctx, str(exc), exc)
    except Exception as exc:                                   # noqa: BLE001
        log.exception("support_remediation: execute failed for %s", action_key)
        return _fail(db, run, ctx, "The repair did not complete.", exc)
    run.execution_result_json = json.dumps(result, default=str)

    # ── VERIFY. A 200 is not a fix.
    run.status = FixStatus.VERIFYING
    db.flush()
    try:
        verified, message, evidence = remediation.verify(ctx)
    except Exception as exc:                                   # noqa: BLE001
        log.exception("support_remediation: verify failed for %s", action_key)
        run.verified = False
        run.verification_message = ("The repair ran but could not be verified, "
                                    "so it is not being reported as fixed.")
        run.error_summary = " ".join(str(exc).split())[:500]
        run.status = FixStatus.FAILED
        _finish(db, run, ctx)
        _audit(db, run, ctx, "support.fix_unverified", before=before)
        return run

    run.verified = bool(verified)
    run.verification_message = message
    run.verification_result_json = json.dumps(evidence, default=str)

    try:
        after = remediation.snapshot(ctx)
    except Exception:                                          # noqa: BLE001
        after = {"note": "after-state could not be captured"}
    run.after_state_json = json.dumps(after, default=str)

    run.status = FixStatus.FIXED if verified else FixStatus.FAILED
    _finish(db, run, ctx)
    _audit(db, run, ctx,
           "support.fix_applied" if verified else "support.fix_failed",
           before=before, after=after)
    _record_signature_outcome(db, run)
    return run


def _fail(db: Session, run: SupportFixRun, ctx: FixContext, message: str,
          exc: Optional[Exception] = None) -> SupportFixRun:
    run.status = FixStatus.FAILED
    run.verified = False
    run.verification_message = message
    if exc is not None:
        # One line, no stack, no connection string — the same contract
        # job_models.error_summary documents for exactly this reason.
        run.error_summary = " ".join(str(exc).split())[:500]
    _finish(db, run, ctx)
    _audit(db, run, ctx, "support.fix_failed", after={"message": message})
    _record_signature_outcome(db, run)
    return run


def _finish(db: Session, run: SupportFixRun, ctx: FixContext) -> None:
    run.finished_at = datetime.utcnow()
    if run.started_at is not None:
        run.duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)
    db.flush()


def _record_signature_outcome(db: Session, run: SupportFixRun) -> None:
    """Feed the recurring-issue counters, best effort.

    Best effort on purpose: intelligence about a fix must never be able to
    fail the fix. `support_incidents` owns the counters; this is the one call
    site that increments the auto-fix ones.
    """
    if not run.issue_signature:
        return
    try:
        from app.services import support_incidents
        support_incidents.record_fix_outcome(db, run)
    except Exception:                                          # noqa: BLE001
        log.exception("support_remediation: could not record signature outcome "
                      "for fix run %s", run.id)


def propose_fix(db: Session, action_key: str, ctx: FixContext, *,
                detection_source: Optional[str] = None,
                diagnosis: Optional[str] = None,
                confidence: Optional[str] = None,
                signature: Optional[str] = None,
                conversation_id: Optional[str] = None,
                diagnostic_run_id: Optional[str] = None) -> SupportFixRun:
    """Prepare a fix that a person must authorize. Nothing is executed here.

    This is what Ask AI does when it has diagnosed something it may not touch:
    the evidence, the diagnosis and the exact registered action are recorded
    so the God console can approve or reject a concrete proposal rather than
    a description of one.
    """
    remediation = REGISTRY.get(action_key)
    if remediation is None:
        raise FixRefused("There is no registered repair called %r." % action_key)

    run = _new_run(db, remediation, ctx, detection_source=detection_source,
                   diagnosis=diagnosis, confidence=confidence, signature=signature,
                   conversation_id=conversation_id,
                   diagnostic_run_id=diagnostic_run_id)
    try:
        ok, why = remediation.validate(ctx)
    except Exception:                                          # noqa: BLE001
        log.exception("support_remediation: validate failed for %s", action_key)
        ok, why = False, "Could not check whether this repair applies."

    try:
        run.before_state_json = json.dumps(remediation.snapshot(ctx), default=str)
    except Exception:                                          # noqa: BLE001
        run.before_state_json = None

    if not ok:
        run.status = FixStatus.RECOMMENDED
        run.verification_message = why
    elif remediation.risk_class == RiskClass.ENGINEERING:
        run.status = FixStatus.ESCALATED
        run.verification_message = ("An engineering change is required. Evidence "
                                    "and a recommendation have been recorded.")
    else:
        run.status = FixStatus.APPROVAL_REQUIRED
        run.verification_message = ("Prepared and waiting for approval from the "
                                    "platform team.")
    db.flush()
    return run


def approve_and_execute(db: Session, run: SupportFixRun, *, god_user: User,
                        org: Optional[Organization] = None, ticket=None,
                        note: Optional[str] = None,
                        now: Optional[datetime] = None) -> SupportFixRun:
    """A named god_admin authorizes a prepared fix, and it runs.

    The approver is recorded on the row before execution, not after, so a fix
    that crashes mid-flight still says who authorized it. `approval_note` is
    the operator's own words and is kept verbatim.
    """
    if run.status not in (FixStatus.APPROVAL_REQUIRED, FixStatus.RECOMMENDED,
                          FixStatus.FAILED):
        raise FixRefused("This repair is %s and is not waiting for approval."
                         % run.status)

    remediation = REGISTRY.get(run.action_key)
    if remediation is None:
        raise FixRefused("There is no registered repair called %r." % run.action_key)
    if remediation.risk_class == RiskClass.ENGINEERING:
        raise FixRefused("An engineering remediation is never executed by the "
                         "platform.")

    if org is None and run.organization_id:
        org = db.query(Organization).filter(
            Organization.id == run.organization_id).first()
    if ticket is None and run.ticket_id:
        from app.models.support_models import SupportTicket
        ticket = db.query(SupportTicket).filter(
            SupportTicket.id == run.ticket_id).first()

    run.approval_note = note
    ctx = FixContext(db, org=org, actor=god_user, ticket=ticket, is_god=True,
                     now=now)
    return execute_fix(db, run.action_key, ctx, requested_by_kind="god",
                       existing_run=run)


def reject_proposal(db: Session, run: SupportFixRun, *, god_user: User,
                    note: Optional[str] = None) -> SupportFixRun:
    """Decline a prepared fix, with the reason on the record."""
    if run.status not in (FixStatus.APPROVAL_REQUIRED, FixStatus.RECOMMENDED):
        raise FixRefused("This repair is %s and cannot be rejected." % run.status)
    run.status = FixStatus.ESCALATED
    run.approval_note = note
    run.authorized_by = god_user.id
    run.verification_message = "Declined by the platform team."
    run.finished_at = datetime.utcnow()
    db.flush()
    ctx = FixContext(db, actor=god_user, is_god=True)
    _audit(db, run, ctx, "support.fix_rejected", after={"note": note})
    return run


# ══════════════════════════════════════════════════════════════════════════
# READ SURFACES
# ══════════════════════════════════════════════════════════════════════════

def list_registry(*, risk_classes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """The whole registry, for the God console.

    Every visible action on that screen is one of these, which is what makes
    "no dead buttons" checkable rather than aspirational: a button that does
    not correspond to a registry entry has nothing to call.
    """
    out = [r.as_dict() for r in REGISTRY.values()]
    if risk_classes:
        out = [r for r in out if r["risk_class"] in risk_classes]
    order = {cls: i for i, cls in enumerate(RiskClass.ALL)}
    return sorted(out, key=lambda r: (order.get(r["risk_class"], 99), r["action_key"]))


def available_for_signals(signals: List[str],
                          hints: List[str]) -> List[Dict[str, Any]]:
    """Which registered repairs the current evidence points at.

    Driven by the diagnostic checks' own `remediation_hints`, so the mapping
    from "what we saw" to "what we could do" lives beside the detection rather
    than in a lookup table that drifts from it.
    """
    keys = [k for k in dict.fromkeys(hints) if k in REGISTRY]
    return [REGISTRY[k].as_dict() for k in keys]


def run_summary(run: SupportFixRun, *, technical: bool = False) -> Dict[str, Any]:
    """One fix run, for a screen. Two audiences, one function, one switch.

    A CUSTOMER IS NEVER TOLD SOMETHING WAS FIXED UNLESS IT WAS VERIFIED. The
    `fixed` boolean below is `verified`, not `status == FIXED`, and those can
    differ — which is the entire reason both columns exist.
    """
    out = {
        "id": run.id,
        "action_key": run.action_key,
        "status": run.status,
        "risk_class": run.risk_class,
        "risk_label": RiskClass.LABELS.get(run.risk_class, run.risk_class),
        "fixed": bool(run.verified),
        "explanation": run.customer_explanation,
        "message": run.verification_message,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
    }
    if not technical:
        return out

    def _load(raw):
        try:
            return json.loads(raw) if raw else None
        except (TypeError, ValueError):
            return {"unparseable": True}

    out.update({
        "organization_id": run.organization_id,
        "platform_id": run.platform_id,
        "ticket_id": run.ticket_id,
        "diagnosis": run.diagnosis,
        "confidence": run.confidence,
        "issue_signature": run.issue_signature,
        "detection_source": run.detection_source,
        "authorization_source": run.authorization_source,
        "authorized_by": run.authorized_by,
        "authorized_at": run.authorized_at,
        "approval_note": run.approval_note,
        "before_state": _load(run.before_state_json),
        "after_state": _load(run.after_state_json),
        "execution_result": _load(run.execution_result_json),
        "verification_result": _load(run.verification_result_json),
        "verified": bool(run.verified),
        "technical_explanation": run.technical_explanation,
        "rollback_available": bool(run.rollback_available),
        "error_summary": run.error_summary,
        "duration_ms": run.duration_ms,
    })
    return out
