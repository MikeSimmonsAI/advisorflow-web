"""THE DIAGNOSTIC ENGINE — what the platform is allowed to look at, and for whom.

THE SHAPE OF THE PROBLEM
------------------------
Ask [Brand] needs real diagnostic power or it is a chatbot with a search box.
Real diagnostic power over a multi-tenant platform is also the single most
dangerous thing to hand a language model. So the model gets NO ability to
query anything. It gets a list of NAMED CHECKS, each of which is ordinary
Python written here, each of which receives a context the SERVER built, and
each of which returns two summaries: one a customer may see and one only God
may see.

The model chooses WHICH check to run. It never chooses WHAT the check does,
WHICH ORGANIZATION it runs against, or WHAT COMES BACK. Those are not
parameters — there is deliberately no `organization_id` argument anywhere in
this module's public surface that a caller could supply. The org comes from
the authenticated request, every time.

    "Prompt instructions are NOT authorization."

That sentence is enforced structurally here rather than by asking the model
nicely: a check has no arguments a customer's text could reach.

TWO SUMMARIES, WRITTEN AT PRODUCTION TIME
-----------------------------------------
`customer_detail` and `technical_detail` are built separately, by the check,
at the moment it runs. There is no filter that turns one into the other. A
redaction that lives in a serializer is a redaction the next endpoint forgets,
and the failure is silent and total. So a credential, a token, a raw provider
error or another tenant's identifier is never placed in `customer_detail` in
the first place — there is nothing to leak downstream.

SEVERITY IS THE PLATFORM'S EXISTING VOCABULARY
----------------------------------------------
`app/services/severity.py` already decided the five words and, crucially, that
an unknown state is UNAVAILABLE and never HEALTHY. Every check speaks it. A
check that throws reports UNAVAILABLE — "we could not look" — which is the
honest answer and is not green.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.models import Organization, User
from app.services import severity as sev

log = logging.getLogger(__name__)

# How far back a "recent failures" check looks. One working week: long enough
# to catch a Friday-night outage on Monday morning, short enough that a
# problem fixed a fortnight ago is not still shouting.
RECENT_WINDOW_HOURS = 168

# A calendar connection that has failed this many times in a row is not having
# a bad minute; it is asking for consent it will never get by retrying.
CALENDAR_FAILURE_THRESHOLD = 3

SCOPE_ORGANIZATION = "organization"
SCOPE_PLATFORM = "platform"


class CheckResult:
    """One answer. Two audiences."""

    __slots__ = ("key", "label", "severity", "headline", "customer_detail",
                 "technical_detail", "suspected_cause", "signals",
                 "remediation_hints", "settings_path", "service")

    def __init__(self, key: str, label: str, *, severity: str, headline: str,
                 customer_detail: Optional[Dict[str, Any]] = None,
                 technical_detail: Optional[Dict[str, Any]] = None,
                 suspected_cause: Optional[str] = None,
                 signals: Optional[List[str]] = None,
                 remediation_hints: Optional[List[str]] = None,
                 settings_path: Optional[str] = None,
                 service: Optional[str] = None):
        self.key = key
        self.label = label
        self.severity = sev.normalize(severity)
        self.headline = headline
        self.customer_detail = customer_detail or {}
        self.technical_detail = technical_detail or {}
        self.suspected_cause = suspected_cause
        self.signals = signals or []
        self.remediation_hints = remediation_hints or []
        self.settings_path = settings_path
        self.service = service

    def customer_view(self) -> Dict[str, Any]:
        """Everything a customer may see about this check, and nothing else."""
        described = sev.describe(self.severity)
        return {
            "key": self.key,
            "label": self.label,
            "service": self.service,
            "severity": described["severity"],
            "severity_label": described["label"],
            "headline": self.headline,
            "detail": self.customer_detail,
            "settings_path": self.settings_path,
        }

    def technical_view(self) -> Dict[str, Any]:
        """The customer view plus the evidence, for an operator with root
        authority. A superset by construction so the two can never disagree
        about the facts they share."""
        out = self.customer_view()
        out["technical"] = self.technical_detail
        out["suspected_cause"] = self.suspected_cause
        out["signals"] = list(self.signals)
        out["remediation_hints"] = list(self.remediation_hints)
        return out


class DiagnosticContext:
    """WHAT THE SERVER DECIDED, handed to a check that cannot widen it.

    Constructed once, in `run_checks`, from the authenticated request. A check
    reads `ctx.org` and can reach nothing else; there is no db-wide escape
    hatch beyond the session it needs, and every query a check makes is
    written here in this file where it can be read in one sitting.
    """

    __slots__ = ("db", "org", "user", "platform_id", "is_god", "now")

    def __init__(self, db: Session, org: Optional[Organization],
                 user: Optional[User], is_god: bool = False,
                 now: Optional[datetime] = None):
        self.db = db
        self.org = org
        self.user = user
        self.platform_id = getattr(org, "platform_id", None) if org is not None else None
        self.is_god = bool(is_god)
        self.now = now or datetime.utcnow()

    @property
    def since(self) -> datetime:
        return self.now - timedelta(hours=RECENT_WINDOW_HOURS)


class DiagnosticCheck:
    __slots__ = ("key", "label", "scope", "description", "service", "run",
                 "god_only")

    def __init__(self, key: str, label: str, *, scope: str, description: str,
                 service: Optional[str], run: Callable[[DiagnosticContext], CheckResult],
                 god_only: bool = False):
        self.key = key
        self.label = label
        self.scope = scope
        self.description = description
        self.service = service
        self.run = run
        self.god_only = god_only


def _unavailable(key: str, label: str, why: str, service=None) -> CheckResult:
    """The honest answer when a check could not look.

    NEVER HEALTHY. `severity.py` exists because green-for-silence is the exact
    failure this platform keeps paying for.
    """
    return CheckResult(
        key, label, severity=sev.UNAVAILABLE,
        headline="We couldn't check this just now.",
        customer_detail={"note": "This isn't a report that anything is wrong — "
                                 "we simply couldn't read it."},
        technical_detail={"error": why}, service=service)


def _safe(fn):
    """A check that raises reports UNAVAILABLE instead of taking the page down.

    A diagnostic surface whose job is to explain a failure must not be the
    second failure. The exception is logged with its traceback for the
    operator and reduced to one line for the payload — the same contract
    `job_models.error_summary` documents.
    """
    def _wrapped(ctx: DiagnosticContext) -> CheckResult:
        check = REGISTRY.get(getattr(fn, "_check_key", ""), None)
        key = getattr(fn, "_check_key", fn.__name__)
        label = check.label if check is not None else key
        try:
            return fn(ctx)
        except Exception as exc:                                # noqa: BLE001
            log.exception("support_diagnostics: check %s failed", key)
            return _unavailable(key, label, " ".join(str(exc).split())[:200],
                                service=check.service if check else None)
    return _wrapped


# ══════════════════════════════════════════════════════════════════════════
# THE CHECKS
# ══════════════════════════════════════════════════════════════════════════

def _check_messaging(ctx: DiagnosticContext) -> CheckResult:
    """Can this customer's messages actually leave the building?

    Deliberately reuses `health_router._messaging_status`, which already
    mirrors `sms_service`'s real credential resolution order — org first, the
    caller's own as a fallback. A second implementation here would drift from
    the sender and start telling a properly configured customer their SMS is
    broken, which is the same class of lie as a false green.
    """
    from app.routers.health_router import _messaging_status
    if ctx.user is None:
        return _unavailable("messaging", "Text messaging",
                            "no user in context", service="sms")
    status = _messaging_status(ctx.db, ctx.user)
    ok = bool(status.connected)
    return CheckResult(
        "messaging", "Text messaging",
        severity=sev.HEALTHY if ok else sev.ACTION_REQUIRED,
        headline=("Text messaging is configured and ready." if ok
                  else "Text messaging isn't fully set up yet."),
        customer_detail={"connected": ok, "reason": status.reason},
        technical_detail={"connected": ok, "reason": status.reason,
                          "resolution": "org credentials first, user fallback"},
        suspected_cause=None if ok else "customer_configuration",
        signals=[] if ok else ["messaging.not_configured"],
        settings_path=status.settings_path, service="sms")


def _check_calendar(ctx: DiagnosticContext) -> CheckResult:
    """Calendar connections for this customer's people, and whether any of them
    is stuck in a retry loop it cannot win.

    `failure_count` on `calendar_connections` is the signal that matters. A
    connection that is nominally connected and has failed repeatedly is worse
    than a disconnected one: the customer sees no error, bookings quietly do
    not sync, and every retry burns the vendor's rate limit for nothing.
    """
    from app.models.calendar_models import CalendarConnection
    if ctx.org is None:
        return _unavailable("calendar", "Calendar sync", "no organization in context",
                            service="calendar")

    user_ids = [row[0] for row in ctx.db.query(User.id)
                .filter(User.organization_id == ctx.org.id).all()]
    if not user_ids:
        return CheckResult(
            "calendar", "Calendar sync", severity=sev.NO_DATA,
            headline="No one in this workspace has a calendar to connect yet.",
            customer_detail={"users": 0}, technical_detail={"users": 0},
            service="calendar")

    connections = (ctx.db.query(CalendarConnection)
                   .filter(CalendarConnection.user_id.in_(user_ids)).all())
    if not connections:
        return CheckResult(
            "calendar", "Calendar sync", severity=sev.NO_DATA,
            headline="No calendars are connected yet.",
            customer_detail={"connected": 0, "users": len(user_ids)},
            technical_detail={"connected": 0, "users": len(user_ids)},
            suspected_cause="customer_configuration",
            settings_path="/settings#google", service="calendar")

    live = [c for c in connections if c.is_connected]
    stuck = [c for c in live
             if (c.failure_count or 0) >= CALENDAR_FAILURE_THRESHOLD]
    scope_gap = [c for c in live if not c.calendar_scope_ok]

    if stuck:
        severity = sev.ACTION_REQUIRED
        headline = ("A connected calendar is being refused by the provider and "
                    "needs to be reconnected.")
    elif scope_gap:
        severity = sev.ATTENTION
        headline = ("A calendar is connected for email but was never granted "
                    "calendar access.")
    elif live:
        severity = sev.HEALTHY
        headline = "Calendar sync is working."
    else:
        severity = sev.ATTENTION
        headline = "Calendars were connected but are all disconnected now."

    return CheckResult(
        "calendar", "Calendar sync", severity=severity, headline=headline,
        customer_detail={
            "connected": len(live), "needs_reconnect": len(stuck),
            "missing_calendar_permission": len(scope_gap),
            # Provider NAMES are the customer's own configuration and safe.
            # The account email is not returned: it is another person's
            # identifier and nothing on this page needs it.
            "providers": sorted({c.provider for c in live}),
        },
        technical_detail={
            "connections": [
                {"provider": c.provider, "is_connected": bool(c.is_connected),
                 "failure_count": int(c.failure_count or 0),
                 "calendar_scope_ok": bool(c.calendar_scope_ok),
                 "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
                 "last_error_at": c.last_error_at.isoformat() if c.last_error_at else None,
                 # Truncated: `last_error` is a provider message, which is
                 # operator evidence and never customer-facing.
                 "last_error": (c.last_error or "")[:200] or None}
                for c in connections],
        },
        suspected_cause=("third_party_provider" if stuck else
                         "customer_configuration" if scope_gap else None),
        signals=(["calendar.provider_refusing_connection"] if stuck else
                 ["calendar.scope_incomplete"] if scope_gap else []),
        remediation_hints=(["calendar.mark_reauthorization_required",
                            "calendar.refresh_busy_cache"] if stuck else []),
        settings_path="/settings#google", service="calendar")


def _check_outbound_delivery(ctx: DiagnosticContext) -> CheckResult:
    """Did the messages this customer sent recently actually arrive?

    Reads `messages.send_state`, which `message_state.py` documents as the
    five-state vocabulary any human-facing surface should read — deliberately
    NOT the raw `delivery_status`, so a message that never reached the carrier
    cannot render here as sent.
    """
    from app.models.models import Lead, Message
    if ctx.org is None:
        return _unavailable("outbound_delivery", "Message delivery",
                            "no organization in context", service="sms")

    rows = (ctx.db.query(Message.send_state, Message.error_code)
            .join(Lead, Lead.id == Message.lead_id)
            .filter(Lead.organization_id == ctx.org.id,
                    Message.sent_at >= ctx.since)
            .all())
    if not rows:
        return CheckResult(
            "outbound_delivery", "Message delivery", severity=sev.NO_DATA,
            headline="No messages have been sent in the last week, so there is "
                     "nothing to measure.",
            customer_detail={"sent": 0, "window_hours": RECENT_WINDOW_HOURS},
            technical_detail={"sent": 0}, service="sms")

    total = len(rows)
    failed = sum(1 for state, _ in rows if state == "failed")
    blocked = sum(1 for state, _ in rows if state == "blocked")
    codes: Dict[str, int] = {}
    for state, code in rows:
        if state == "failed" and code:
            codes[str(code)] = codes.get(str(code), 0) + 1

    failure_rate = (failed * 100) // total if total else 0
    if failure_rate >= 25:
        severity, cause = sev.ACTION_REQUIRED, "third_party_provider"
    elif failed:
        severity, cause = sev.ATTENTION, "third_party_provider"
    else:
        severity, cause = sev.HEALTHY, None

    return CheckResult(
        "outbound_delivery", "Message delivery", severity=severity,
        headline=("Messages are being delivered." if not failed
                  else "%d of your last %d messages didn't get through."
                       % (failed, total)),
        customer_detail={"sent": total, "failed": failed, "blocked": blocked,
                         "failure_rate_percent": failure_rate,
                         "window_hours": RECENT_WINDOW_HOURS},
        technical_detail={"sent": total, "failed": failed, "blocked": blocked,
                          "error_codes": codes},
        suspected_cause=cause,
        signals=["sms.delivery_failures.%s" % code for code in sorted(codes)],
        service="sms")


def _check_entitlements(ctx: DiagnosticContext) -> CheckResult:
    """Is this customer entitled to the thing they are trying to use, and is
    their account in good standing?

    A 402 reads to a customer exactly like a broken feature. Naming the real
    reason here is the difference between "the product is broken" and "this
    isn't switched on for you" — two problems with two entirely different
    owners.
    """
    from app.services import entitlements as ent
    if ctx.org is None:
        return _unavailable("entitlements", "Your plan and features",
                            "no organization in context", service="billing")

    report = ent.feature_report(ctx.org)
    suspended = ent.billing_suspension_reason(ctx.db, ctx.org)
    status = (getattr(ctx.org, "billing_status", None) or "").lower()

    if suspended:
        severity, cause = sev.ACTION_REQUIRED, "customer_configuration"
        headline = "Access is limited because of a billing issue."
    elif status in ("past_due", "unpaid"):
        severity, cause = sev.ATTENTION, "customer_configuration"
        headline = "There is a payment issue on this account."
    else:
        severity, cause = sev.HEALTHY, None
        headline = "Your plan is active."

    return CheckResult(
        "entitlements", "Your plan and features", severity=severity,
        headline=headline,
        customer_detail={"enabled_features": report["enabled"],
                         "feature_mode": report["mode"],
                         "billing_status": status or "not recorded",
                         "limited": bool(suspended)},
        technical_detail={"enabled_features": report["enabled"],
                          "mode": report["mode"],
                          "billing_status": status,
                          "billing_plan_key": getattr(ctx.org, "billing_plan_key", None),
                          "pending_plan_key": getattr(ctx.org, "billing_pending_plan_key", None),
                          "suspension_reason": suspended},
        suspected_cause=cause,
        signals=["billing.%s" % status] if status in ("past_due", "unpaid") else [],
        settings_path="/billing", service="billing")


def _check_capacity(ctx: DiagnosticContext) -> CheckResult:
    """Are they up against what their plan actually bought?

    "It won't let me add another user" is a support ticket that looks like a
    bug and is a plan limit. Reads the SAME plan `plan_limits.effective_plan`
    enforces against — the entitled plan, never the pending one — so this page
    and the refusal can never disagree.
    """
    from app.models.models import Lead
    from app.services import plan_limits
    if ctx.org is None:
        return _unavailable("capacity", "Plan capacity", "no organization in context",
                            service="billing")

    plan = plan_limits.effective_plan(ctx.db, ctx.org)
    max_users = getattr(plan, "max_users", None) if plan is not None else None
    max_leads = getattr(plan, "max_leads", None) if plan is not None else None

    users = ctx.db.query(User).filter(User.organization_id == ctx.org.id,
                                      User.is_active.is_(True)).count()
    leads = ctx.db.query(Lead).filter(Lead.organization_id == ctx.org.id).count()

    def _state(used, limit) -> Tuple[str, bool]:
        if limit is None:
            return "unlimited", False
        if used >= limit:
            return "at_limit", True
        if used >= max(int(limit * 0.9), limit - 1):
            return "near_limit", False
        return "ok", False

    user_state, users_full = _state(users, max_users)
    lead_state, leads_full = _state(leads, max_leads)

    if users_full or leads_full:
        severity, headline = sev.ACTION_REQUIRED, "You've reached a limit on your plan."
    elif "near_limit" in (user_state, lead_state):
        severity, headline = sev.ATTENTION, "You're close to a limit on your plan."
    elif plan is None:
        severity, headline = sev.NO_DATA, "No plan limits are configured for this account."
    else:
        severity, headline = sev.HEALTHY, "You have room on your plan."

    return CheckResult(
        "capacity", "Plan capacity", severity=severity, headline=headline,
        customer_detail={"users": users, "max_users": max_users,
                         "leads": leads, "max_leads": max_leads,
                         "users_state": user_state, "leads_state": lead_state},
        technical_detail={"plan_key": getattr(plan, "key", None),
                          "users": users, "max_users": max_users,
                          "leads": leads, "max_leads": max_leads},
        suspected_cause="usage_capacity" if (users_full or leads_full) else None,
        signals=["capacity.users_at_limit"] if users_full else (
            ["capacity.leads_at_limit"] if leads_full else []),
        settings_path="/billing", service="billing")


def _check_ai_service(ctx: DiagnosticContext) -> CheckResult:
    """Is the AI service configured at all?

    Carries `health_router._ai_features_status`'s documented limitation
    forward rather than pretending to a certainty we do not have: a key that
    is present but rate-limited or unpaid still reports as configured, because
    proving otherwise costs a live API call on every page load. The check says
    so in the technical view so an operator is not misled by a green pill.
    """
    configured = bool(os.environ.get("OPENAI_API_KEY"))
    return CheckResult(
        "ai_service", "AI features",
        severity=sev.HEALTHY if configured else sev.ACTION_REQUIRED,
        headline=("AI features are switched on." if configured
                  else "AI features aren't available right now."),
        customer_detail={"configured": configured},
        technical_detail={
            "configured": configured,
            "limitation": "presence of the platform key only; a key that is "
                          "rate-limited or unpaid still reports configured",
        },
        suspected_cause=None if configured else "platform_defect",
        signals=[] if configured else ["ai.key_absent"],
        service="ai")


def _check_background_jobs(ctx: DiagnosticContext) -> CheckResult:
    """Are the loops that move this platform actually running?

    PLATFORM SCOPE, and therefore God-only: job health is a fact about
    AdvisorFlow, not about one customer, and telling a customer that a
    platform loop is down invites them to reason about other customers.
    Reads the same `job_runs` ledger the God job-runs screen reads.
    """
    from app.models.job_models import ALL_JOB_NAMES, JobRun

    jobs = []
    worst = []
    for name in ALL_JOB_NAMES:
        last = (ctx.db.query(JobRun)
                .filter(JobRun.job_name == name)
                .order_by(JobRun.started_at.desc()).first())
        last_ok = (ctx.db.query(JobRun.started_at)
                   .filter(JobRun.job_name == name, JobRun.status == "success")
                   .order_by(JobRun.started_at.desc()).first())
        ok_at = last_ok[0] if last_ok else None
        if last is None:
            state = sev.NO_DATA
        elif last.status == "error":
            state = sev.ACTION_REQUIRED
        elif ok_at is not None and (ctx.now - ok_at) > timedelta(hours=6):
            state = sev.ATTENTION
        elif ok_at is None:
            state = sev.UNAVAILABLE
        else:
            state = sev.HEALTHY
        worst.append(state)
        jobs.append({"job": name, "severity": state,
                     "last_status": getattr(last, "status", None),
                     "last_started_at": last.started_at.isoformat() if last else None,
                     "last_success_at": ok_at.isoformat() if ok_at else None,
                     "error_summary": getattr(last, "error_summary", None)})

    overall = sev.worst(worst)
    return CheckResult(
        "background_jobs", "Background processing", severity=overall,
        headline=("Background processing is running." if overall == sev.HEALTHY
                  else "Background processing needs attention."),
        customer_detail={"note": "Platform-level detail is not shown here."},
        technical_detail={"jobs": jobs},
        suspected_cause="platform_defect" if overall == sev.ACTION_REQUIRED else None,
        signals=["jobs.%s.failing" % j["job"] for j in jobs
                 if j["severity"] == sev.ACTION_REQUIRED],
        service="platform")


def _check_recent_platform_events(ctx: DiagnosticContext) -> CheckResult:
    """Failure events this customer's own workspace emitted recently.

    Scoped to `org_id` — a customer's own events, never the platform's stream.
    `platform_events` is best-effort instrumentation (`events/emit.py` says so
    out loud), so an empty result is NO_DATA and never HEALTHY: nothing being
    recorded is not evidence that nothing failed.
    """
    from sqlalchemy import func as sa_func
    from app.models.models import PlatformEvent
    if ctx.org is None:
        return _unavailable("recent_events", "Recent activity",
                            "no organization in context", service="platform")

    failure_types = ("sms.failed", "email.failed", "ai.action.failed",
                     "integration.failed", "payment.failed")
    rows = (ctx.db.query(PlatformEvent.event_type,
                         sa_func.count(PlatformEvent.id))
            .filter(PlatformEvent.org_id == ctx.org.id,
                    PlatformEvent.event_type.in_(failure_types),
                    PlatformEvent.occurred_at >= ctx.since)
            .group_by(PlatformEvent.event_type).all())
    counts = {str(t): int(c) for t, c in rows}
    total = sum(counts.values())

    if total == 0:
        severity = sev.NO_DATA
        headline = "No failures have been recorded for your workspace this week."
    elif total >= 10:
        severity = sev.ACTION_REQUIRED
        headline = "Your workspace has recorded repeated failures this week."
    else:
        severity = sev.ATTENTION
        headline = "A few failures have been recorded for your workspace."

    return CheckResult(
        "recent_events", "Recent activity", severity=severity, headline=headline,
        customer_detail={"failures": total, "window_hours": RECENT_WINDOW_HOURS},
        technical_detail={"counts": counts, "window_hours": RECENT_WINDOW_HOURS},
        suspected_cause=None,
        signals=["events.%s" % t for t in sorted(counts)],
        service="platform")


def _check_support_state(ctx: DiagnosticContext) -> CheckResult:
    """Does this customer already have an open ticket about something?

    This is what stops Ask AI opening a fifth ticket for the same problem. It
    is a diagnostic rather than a lookup because the ANSWER changes the advice:
    "we already have this, here is where it stands" is a better response than
    a new ticket number.
    """
    from app.models.support_models import SupportTicket, TicketStatus
    if ctx.org is None:
        return _unavailable("support_state", "Your open requests",
                            "no organization in context", service="support")

    open_tickets = (ctx.db.query(SupportTicket)
                    .filter(SupportTicket.organization_id == ctx.org.id,
                            SupportTicket.status.in_(
                                list(TicketStatus.OPEN) +
                                [TicketStatus.WAITING_ON_CUSTOMER]))
                    .order_by(SupportTicket.created_at.desc()).limit(20).all())
    waiting = [t for t in open_tickets
               if t.status == TicketStatus.WAITING_ON_CUSTOMER]

    return CheckResult(
        "support_state", "Your open requests",
        severity=sev.ATTENTION if waiting else (
            sev.NO_DATA if not open_tickets else sev.HEALTHY),
        headline=("We're waiting on a reply from you on %d request(s)." % len(waiting)
                  if waiting else
                  "You have %d open request(s)." % len(open_tickets) if open_tickets
                  else "You have no open requests."),
        customer_detail={
            "open": len(open_tickets), "waiting_on_you": len(waiting),
            "tickets": [{"ticket_number": t.ticket_number, "subject": t.subject,
                         "status": t.status, "severity": t.severity}
                        for t in open_tickets[:5]],
        },
        technical_detail={"open_ids": [t.id for t in open_tickets]},
        service="support")


REGISTRY: Dict[str, DiagnosticCheck] = {}


def _register(key: str, label: str, *, scope: str, description: str,
              service: Optional[str], fn, god_only: bool = False) -> None:
    fn._check_key = key                                       # noqa: SLF001
    REGISTRY[key] = DiagnosticCheck(key, label, scope=scope, description=description,
                                    service=service, run=_safe(fn), god_only=god_only)


_register("messaging", "Text messaging", scope=SCOPE_ORGANIZATION,
          service="sms", fn=_check_messaging,
          description="Whether this organization can send SMS: credentials and a sender.")
_register("calendar", "Calendar sync", scope=SCOPE_ORGANIZATION,
          service="calendar", fn=_check_calendar,
          description="Calendar connection state for this organization's users, "
                      "including connections stuck retrying a refused token.")
_register("outbound_delivery", "Message delivery", scope=SCOPE_ORGANIZATION,
          service="sms", fn=_check_outbound_delivery,
          description="Delivery outcomes for messages this organization sent recently.")
_register("entitlements", "Plan and features", scope=SCOPE_ORGANIZATION,
          service="billing", fn=_check_entitlements,
          description="Which features this organization is entitled to and whether "
                      "billing standing is limiting access.")
_register("capacity", "Plan capacity", scope=SCOPE_ORGANIZATION,
          service="billing", fn=_check_capacity,
          description="Users and leads used against the plan's configured limits.")
_register("ai_service", "AI features", scope=SCOPE_ORGANIZATION,
          service="ai", fn=_check_ai_service,
          description="Whether the platform AI service is configured.")
_register("recent_events", "Recent failures", scope=SCOPE_ORGANIZATION,
          service="platform", fn=_check_recent_platform_events,
          description="Failure events recorded for this organization in the last week.")
_register("support_state", "Open requests", scope=SCOPE_ORGANIZATION,
          service="support", fn=_check_support_state,
          description="Tickets this organization already has open, so a duplicate "
                      "is not created.")
_register("background_jobs", "Background processing", scope=SCOPE_PLATFORM,
          service="platform", fn=_check_background_jobs, god_only=True,
          description="Platform background loop health. God only — this is a fact "
                      "about AdvisorFlow, not about one customer.")


CUSTOMER_CHECK_KEYS = tuple(sorted(k for k, c in REGISTRY.items() if not c.god_only))
ALL_CHECK_KEYS = tuple(sorted(REGISTRY))

# The subset a customer's own System Status page renders. Deliberately not
# "everything not god_only": `support_state` and `capacity` are diagnostics,
# not status, and a status page that lists a customer's open tickets as a
# service is confusing rather than informative.
STATUS_CHECK_KEYS = ("messaging", "calendar", "outbound_delivery", "ai_service",
                     "entitlements")


# ══════════════════════════════════════════════════════════════════════════
# RUNNING THEM — the only public entry point
# ══════════════════════════════════════════════════════════════════════════

def tool_definitions(*, include_god_only: bool = False) -> List[Dict[str, Any]]:
    """The checks, as schema-constrained tool declarations for the AI layer.

    EVERY TOOL TAKES NO ARGUMENTS. That is not an oversight and it is not a
    simplification — it is the security model. A tool with an
    `organization_id` parameter is a tool a customer's typed text can aim at
    another tenant, whatever the system prompt says. The organization is
    resolved from the authenticated request before the model is called, and
    the model's only decision is WHICH named check to run.
    """
    out = []
    for key in sorted(REGISTRY):
        check = REGISTRY[key]
        if check.god_only and not include_god_only:
            continue
        out.append({
            "type": "function",
            "function": {
                "name": "check_%s" % key,
                "description": check.description,
                "parameters": {"type": "object", "properties": {},
                               "additionalProperties": False},
            },
        })
    return out


def resolve_tool_name(name: str, *, include_god_only: bool = False) -> Optional[str]:
    """Map a model-chosen tool name back to a registered check key, or None.

    An unknown name resolves to None and the caller ignores it. There is no
    fuzzy match and no fallback check: a model that invents a tool gets
    nothing, not the closest thing we could find.
    """
    if not isinstance(name, str) or not name.startswith("check_"):
        return None
    key = name[len("check_"):]
    check = REGISTRY.get(key)
    if check is None:
        return None
    if check.god_only and not include_god_only:
        return None
    return key


def run_checks(db: Session, *, org: Optional[Organization], user: Optional[User],
               keys: Optional[List[str]] = None, is_god: bool = False,
               now: Optional[datetime] = None) -> Dict[str, Any]:
    """Run named checks against ONE organization and summarize both views.

    `org` is a parameter of this function and NOT of any check, and callers
    pass the organization the request authenticated into. There is no code
    path by which a check receives an organization the caller did not already
    have authority over — the tenant boundary is enforced by the shape of the
    call, not by a filter each check remembers to apply.

    A god_only check is silently skipped for a non-god caller rather than
    raising: a customer asking a broad question should get the eight answers
    they are entitled to, not an error about the ninth they never asked for.
    """
    started = datetime.utcnow()
    ctx = DiagnosticContext(db, org, user, is_god=is_god, now=now)

    requested = list(keys) if keys else list(
        ALL_CHECK_KEYS if is_god else CUSTOMER_CHECK_KEYS)
    results: List[CheckResult] = []
    skipped: List[str] = []

    for key in requested:
        check = REGISTRY.get(key)
        if check is None:
            skipped.append(key)
            continue
        if check.god_only and not is_god:
            skipped.append(key)
            continue
        if check.scope == SCOPE_ORGANIZATION and org is None:
            skipped.append(key)
            continue
        results.append(check.run(ctx))

    severities = [r.severity for r in results]
    overall = sev.worst(severities)
    causes = [r.suspected_cause for r in results if r.suspected_cause]
    signals: List[str] = []
    hints: List[str] = []
    for r in results:
        signals.extend(r.signals)
        hints.extend(r.remediation_hints)

    duration_ms = int((datetime.utcnow() - started).total_seconds() * 1000)
    return {
        "overall_severity": overall,
        "overall_label": sev.LABELS[overall],
        "checks_run": [r.key for r in results],
        "skipped": skipped,
        "suspected_cause": _dominant_cause(causes),
        "signals": sorted(set(signals)),
        "remediation_hints": sorted(set(hints)),
        "customer_view": [r.customer_view() for r in
                          sorted(results, key=lambda r: sev.rank(r.severity))],
        "technical_view": [r.technical_view() for r in
                           sorted(results, key=lambda r: sev.rank(r.severity))],
        "duration_ms": duration_ms,
        "results": results,
    }


def _dominant_cause(causes: List[str]) -> Optional[str]:
    """One cause out of several, chosen by consequence rather than by count.

    A platform defect beats a provider problem beats a customer's own
    configuration, because that is the order in which somebody has to act and
    because the most common cause is rarely the most important one. Returning
    the modal value would let two configuration warnings out-vote one outage.
    """
    if not causes:
        return None
    priority = ["platform_defect", "third_party_provider", "usage_capacity",
                "customer_configuration", "user_question"]
    for candidate in priority:
        if candidate in causes:
            return candidate
    return causes[0]


def persist_run(db: Session, summary: Dict[str, Any], *,
                org: Optional[Organization],
                requested_by: Optional[str] = None,
                requested_by_kind: str = "ai",
                conversation_id: Optional[str] = None,
                ticket_id: Optional[str] = None):
    """Store the evidence, both views, as one row.

    Written here rather than by each caller so the redaction contract holds
    once: `customer_summary_json` receives EXACTLY `customer_view`, which the
    checks built for that audience, and `technical_summary_json` receives the
    superset. A caller cannot accidentally store the technical view in the
    customer column because it does not choose the columns.
    """
    from app.models.support_models import SupportDiagnosticRun

    run = SupportDiagnosticRun(
        organization_id=getattr(org, "id", None),
        platform_id=getattr(org, "platform_id", None),
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        requested_by=requested_by,
        requested_by_kind=requested_by_kind,
        checks_run=",".join(summary.get("checks_run", [])),
        overall_severity=summary.get("overall_severity"),
        suspected_cause=summary.get("suspected_cause"),
        confidence=summary.get("confidence"),
        customer_summary_json=json.dumps(summary.get("customer_view", []), default=str),
        technical_summary_json=json.dumps(
            {"checks": summary.get("technical_view", []),
             "signals": summary.get("signals", []),
             "remediation_hints": summary.get("remediation_hints", []),
             "skipped": summary.get("skipped", [])}, default=str),
        duration_ms=summary.get("duration_ms"),
    )
    db.add(run)
    db.flush()
    return run


def system_status(db: Session, *, org: Optional[Organization],
                  user: Optional[User]) -> Dict[str, Any]:
    """The customer-facing status board.

    CUSTOMER VIEW ONLY, and platform-scope checks are excluded by
    construction: `STATUS_CHECK_KEYS` names services this customer's own
    workspace depends on. A status page that reported platform-wide job health
    would be inviting one customer to reason about every other customer's day.

    A known platform incident is folded in by `support_incidents`, which
    supplies a sanitized statement or nothing at all.
    """
    summary = run_checks(db, org=org, user=user, keys=list(STATUS_CHECK_KEYS),
                         is_god=False)
    services = summary["customer_view"]

    incident_note = None
    try:
        from app.services import support_incidents
        incident_note = support_incidents.customer_statement(
            db, org=org, services=[s.get("service") for s in services])
    except Exception:                                          # noqa: BLE001
        log.exception("support_diagnostics: incident statement lookup failed")

    return {
        "overall_severity": summary["overall_severity"],
        "overall_label": summary["overall_label"],
        "services": services,
        "incident_note": incident_note,
        "checked_at": datetime.utcnow(),
    }
