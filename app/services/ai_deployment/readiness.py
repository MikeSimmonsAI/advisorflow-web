"""READINESS - decided by code, in a fixed order, every single time.

NEVER LET THE MODEL DECIDE READINESS (section 5). Nothing in this file calls a
model, reads model output, or consults anything a model wrote. Every check is a
question about rows in a database and flags in an environment, and every one of
them returns the same answer twice in a row given the same facts.

THREE VERDICTS AND THE MIDDLE ONE IS NOT A SOFTER NO.

    READY             every blocking check passed and nothing wants a person.
    NOT_READY         at least one blocking check failed. Named, with the fix.
    REVIEW_REQUIRED   everything blocking passed and something should be looked
                      at by a person before anybody switches this on.

REVIEW_REQUIRED NEVER BEHAVES LIKE READY. `activation.request` refuses it
exactly as it refuses NOT_READY, and the only way past it is a person who has
seen what it is about. That is the same rule T6's contact eligibility keeps
about its own REQUIRES_REVIEW, for the same reason: a maybe that is treated as
a yes is a no that nobody reads.

READINESS IS RE-EVALUATED AT ACTIVATION, NOT READ FROM THE ROW. The stored
snapshot on `ai_employee_deployments` is what a screen shows; the activation
path computes a fresh one. A deployment that was ready yesterday and lost its
handoff owner overnight must not activate on the strength of yesterday's
answer - which is the same execution-time argument T6 makes about activation
itself.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.ai_deployment_models import AIEmployeeDeployment
from app.models.models import Organization, User
from app.services.ai_deployment import capacity, catalog, commerce
from app.services.ai_deployment import configuration as cfg_mod
from app.services.ai_deployment import constants as D

_log = logging.getLogger(__name__)


class Check:
    """One named question, its answer, and what to do about a bad one."""

    __slots__ = ("key", "label", "passed", "severity", "detail", "fix")

    def __init__(self, key, label, passed, severity=D.SEVERITY_BLOCKING,
                 detail="", fix=""):
        self.key = key
        self.label = label
        self.passed = bool(passed)
        self.severity = severity
        self.detail = detail
        self.fix = fix

    def as_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "label": self.label, "passed": self.passed,
                "severity": self.severity, "detail": self.detail,
                "fix": self.fix}


class Readiness:
    """The verdict, with every check that produced it."""

    __slots__ = ("verdict", "checks", "checked_at")

    def __init__(self, verdict, checks, checked_at=None):
        self.verdict = verdict
        self.checks = list(checks)
        self.checked_at = checked_at or datetime.utcnow()

    @property
    def is_ready(self) -> bool:
        return self.verdict == D.READY_YES

    @property
    def blocking(self) -> List[Check]:
        return [c for c in self.checks
                if not c.passed and c.severity == D.SEVERITY_BLOCKING]

    @property
    def review(self) -> List[Check]:
        return [c for c in self.checks
                if not c.passed and c.severity == D.SEVERITY_REVIEW]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "verdict_label": {
                D.READY_YES: "Ready",
                D.READY_NO: "Not ready",
                D.READY_REVIEW: "Needs a person to look",
            }.get(self.verdict, self.verdict),
            "checked_at": self.checked_at.isoformat(),
            "checks": [c.as_dict() for c in self.checks],
            "blocking": [c.as_dict() for c in self.blocking],
            "review": [c.as_dict() for c in self.review],
            "passed_count": sum(1 for c in self.checks if c.passed),
            "total": len(self.checks),
        }


def _verdict(checks: List[Check]) -> str:
    if any(not c.passed and c.severity == D.SEVERITY_BLOCKING for c in checks):
        return D.READY_NO
    if any(not c.passed and c.severity == D.SEVERITY_REVIEW for c in checks):
        return D.READY_REVIEW
    return D.READY_YES


# ---------------------------------------------------------------------------
# THE CHECKS, IN ORDER
# ---------------------------------------------------------------------------

def evaluate(db: Session, deployment: AIEmployeeDeployment) -> Readiness:
    """Every readiness question, asked in the order a person would ask them.

    CHEAPEST AND MOST ABSOLUTE FIRST, which is the same ordering T7's gate
    chain uses. A deployment that is retired does not need its calendar
    checked, and a customer with no entitlement does not need to be told about
    their handoff owner first.
    """
    checks: List[Check] = []
    org = (db.query(Organization)
           .filter(Organization.id == deployment.organization_id).first())

    # 1. THE DEPLOYMENT ITSELF -------------------------------------------
    checks.append(Check(
        "deployment_live", "This deployment has not been retired",
        deployment.state != D.RETIRED,
        detail=("Retired." if deployment.state == D.RETIRED else "Active record."),
        fix="Hire a new AI employee rather than reviving a retired one."))

    # 2. TENANT ------------------------------------------------------------
    checks.append(Check(
        "tenant_valid", "The workspace exists",
        org is not None,
        detail=("Workspace found." if org is not None
                else "This deployment names an organization that no longer "
                     "exists."),
        fix="Contact the platform operator."))
    if org is None:
        return Readiness(_verdict(checks), checks)

    # 3. TEMPLATE ----------------------------------------------------------
    from app.services.workforce import registry as wf_registry
    spec = wf_registry.template(deployment.template_key)
    checks.append(Check(
        "template_valid", "The job still exists on the platform",
        spec is not None,
        detail=("%s." % spec.name if spec else
                "This job is no longer part of the platform library."),
        fix="Retire this deployment and hire a current job."))
    if spec is None:
        return Readiness(_verdict(checks), checks)

    reqs = catalog.requirements_for(deployment.template_key)
    config = cfg_mod.config_of(deployment)

    # 4. BRAND OFFERING ----------------------------------------------------
    offer = commerce.resolve_offer(db, org, deployment.template_key)
    checks.append(Check(
        "brand_offers_it", "The brand still offers this job",
        offer.state != D.COMM_NOT_OFFERED,
        detail=offer.detail or "",
        fix="The brand switches the offering back on."))

    # 5. COMMERCIAL ENTITLEMENT -------------------------------------------
    checks.append(Check(
        "entitlement", "The account is entitled to this AI employee",
        offer.is_live,
        detail=(D.COMMERCIAL_LABELS.get(offer.state, offer.state)
                + (" - " + offer.detail if offer.detail else "")),
        fix=("Wait for the payment to arrive."
             if offer.state == D.COMM_PENDING
             else "Add this AI employee to the account.")))

    # 6. PACKAGE AND CAPACITY ---------------------------------------------
    cap = capacity.may_hire(db, org, deployment.template_key, terms=offer.terms)
    # An EXISTING deployment does not have to fit under the limit again - it is
    # already counted. What still applies is package eligibility, which can
    # change underneath a customer when their package does.
    package_ok = cap.allowed or cap.code != D.R_PACKAGE_INELIGIBLE
    checks.append(Check(
        "package_eligible", "The package may hold this AI employee",
        package_ok,
        detail=(cap.reason or "Package permits it."),
        fix="Changing package is a conversation with the account manager."))

    # 7. CUSTOMER FEATURES -------------------------------------------------
    from app.services import entitlements as platform_entitlements
    missing_features = [f for f in reqs["features"]
                        if f in platform_entitlements.FEATURES
                        and not platform_entitlements.org_has_feature(org, f)]
    checks.append(Check(
        "features_enabled", "The workspace is enabled for what this job needs",
        not missing_features,
        detail=("All required features are on."
                if not missing_features
                else "Not enabled for: %s." % ", ".join(missing_features)),
        fix="An operator enables these in the customer's Features settings."))

    # 8. REQUIRED BUSINESS CONFIGURATION ----------------------------------
    schema = cfg_mod.schema_for(db, org.id, deployment.template_key)
    missing = []
    for field in schema["fields"]:
        if not field["required"]:
            continue
        value = config.get(field["key"])
        if value in (None, "", [], {}):
            missing.append(field["label"])
    checks.append(Check(
        "configuration_complete", "The business questions have been answered",
        not missing,
        detail=("Everything required has been answered."
                if not missing
                else "Still needed: %s." % "; ".join(missing[:6])),
        fix="Finish setting the employee up."))

    # 9. CHANNELS ----------------------------------------------------------
    chosen = [c for c in (config.get("channels") or []) if c in reqs["channels"]]
    if reqs["channels"]:
        checks.append(Check(
            "channel_chosen", "A way of contacting people has been chosen",
            bool(chosen),
            detail=("Chosen: %s." % ", ".join(chosen) if chosen
                    else "No channel has been chosen."),
            fix="Choose at least one channel this employee may use."))
        checks.extend(_channel_checks(db, org, chosen))

    # 10. CALENDAR ---------------------------------------------------------
    if reqs["needs_calendar"]:
        checks.extend(_calendar_checks(db, org, config))

    # 11. HANDOFF ----------------------------------------------------------
    checks.extend(_handoff_checks(db, org, deployment, config))

    # 12. THE ENGINE -------------------------------------------------------
    checks.extend(_engine_checks(db, deployment))

    # 13. ACTIVATION SCOPES ------------------------------------------------
    checks.extend(_activation_scope_checks(db, org, deployment))

    # 14. LIVE-CHANNEL REQUIREMENTS ---------------------------------------
    checks.extend(_live_channel_checks(config, chosen))

    return Readiness(_verdict(checks), checks)


def _channel_checks(db: Session, org: Organization,
                    chosen: List[str]) -> List[Check]:
    out: List[Check] = []
    if "sms" in chosen:
        number = getattr(org, "org_twilio_phone_number", None)
        has_advisor_number = bool(
            db.query(User)
            .filter(User.organization_id == org.id,
                    User.twilio_phone_number.isnot(None)).first())
        out.append(Check(
            "sms_sender", "There is a number this business can text from",
            bool(number or has_advisor_number),
            detail=("A sending number is configured." if (number
                                                          or has_advisor_number)
                    else "No sending number is configured for this business."),
            fix="Configure the organization's SMS number in settings."))
    if "email" in chosen:
        sender = (getattr(org, "from_email", None)
                  or getattr(org, "support_email", None))
        out.append(Check(
            "email_sender", "There is an address this business sends from",
            bool(sender),
            severity=D.SEVERITY_REVIEW,
            detail=("Sending as %s." % sender if sender
                    else "No organization sender address is configured, so "
                         "email would go out under the platform default."),
            fix="Set the organization's sending address in settings."))
    return out


def _calendar_checks(db: Session, org: Organization,
                     config: Dict[str, Any]) -> List[Check]:
    from app.models.calendar_models import CalendarConnection
    owner = config.get("booking_owner") or config.get("handoff_user_id")
    out = [Check(
        "booking_owner", "Somebody's calendar has been named",
        bool(owner),
        detail=("Booking into a named calendar." if owner
                else "No calendar owner has been chosen, so this employee has "
                     "nowhere to book."),
        fix="Choose whose calendar this employee books into.")]
    if owner:
        connected = (db.query(CalendarConnection)
                     .filter(CalendarConnection.user_id == owner,
                             CalendarConnection.is_connected.is_(True))
                     .first())
        out.append(Check(
            "calendar_connected", "That calendar is connected",
            connected is not None,
            severity=D.SEVERITY_REVIEW,
            detail=("Connected." if connected is not None
                    else "That person has no connected calendar, so "
                         "availability will come from the platform's own "
                         "rules only."),
            fix="Connect the calendar, or confirm the platform's availability "
                "is the intended source."))
    return out


def _handoff_checks(db: Session, org: Organization, deployment,
                    config: Dict[str, Any]) -> List[Check]:
    out: List[Check] = []
    user_id = config.get("handoff_user_id") or config.get("handoff_to")
    queue = config.get("handoff_team")
    target = bool(user_id or queue)
    out.append(Check(
        "handoff_destination", "There is somewhere to hand a person to",
        target,
        detail=("A handoff destination is configured." if target
                else "Nothing is configured, so a conversation that needs a "
                     "person would have nowhere to go."),
        fix="Name the person or team who takes over."))
    if user_id:
        exists = (db.query(User)
                  .filter(User.id == user_id,
                          User.organization_id == org.id,
                          User.is_active.is_(True)).first())
        out.append(Check(
            "handoff_owner_active", "That person is still in the workspace",
            exists is not None,
            detail=("Found." if exists is not None
                    else "The person configured to receive handoffs is no "
                         "longer active in this workspace."),
            fix="Choose somebody who still works here."))
    peer = config.get("handoff_to_employee_id")
    if peer:
        loop = cfg_mod.detect_loop(db, organization_id=org.id,
                                   from_deployment_id=deployment.id,
                                   to_deployment_id=peer)
        out.append(Check(
            "handoff_no_loop", "AI-to-AI handoff does not go in a circle",
            not loop,
            detail=("No loop." if not loop
                    else "Work would circle: %s." % " -> ".join(loop)),
            fix="Point the last employee in the chain at a person."))
    return out


def _engine_checks(db: Session, deployment) -> List[Check]:
    """Is T6 able to answer for this employee, and can T7 reach anything?

    T7's availability is REVIEW rather than BLOCKING on purpose. An employee
    can legitimately be made ready while the operations layer is switched off -
    that is the entire shape of a dark launch - and refusing readiness on it
    would mean nothing could ever be prepared before a launch window.
    """
    out: List[Check] = []
    try:
        from app.services.workforce import activation as wf_activation
        from app.services.workforce import policy as wf_policy
        engine_ok = wf_activation is not None and wf_policy is not None
    except Exception:                                         # noqa: BLE001
        engine_ok = False
    out.append(Check(
        "workforce_engine", "The AI workforce engine is present",
        engine_ok,
        detail=("Present." if engine_ok else "Not available in this build."),
        fix="This is a platform deployment problem."))

    if deployment.employee_id and engine_ok:
        from app.models.workforce_models import AIEmployee
        from app.services.workforce import policy as wf_policy
        emp = (db.query(AIEmployee)
               .filter(AIEmployee.id == deployment.employee_id,
                       AIEmployee.organization_id == deployment.organization_id)
               .first())
        out.append(Check(
            "actor_exists", "The AI employee record exists",
            emp is not None,
            detail=("Found." if emp is not None
                    else "The employee behind this deployment is missing."),
            fix="Retire this deployment and hire again."))
        if emp is not None:
            pol = wf_policy.resolve(db, emp)
            out.append(Check(
                "authority_granted", "It has been granted something to do",
                bool(pol.tool_keys),
                detail=("%d actions granted." % len(pol.tool_keys)
                        if pol.tool_keys
                        else "No actions are granted to this employee, so it "
                             "could not do anything if it were switched on."),
                fix="Re-run setup, which grants the job's own actions."))

    try:
        from app.services.ai_operations import contracts as t7_contracts
        from app.services.ai_operations import flags as t7_flags
        avail = t7_contracts.availability()
        state = t7_flags.state()
        t7_present = all(avail.values())
        out.append(Check(
            "operations_layer", "The operations layer is present",
            t7_present, severity=D.SEVERITY_REVIEW,
            detail=("Present; sending is %s."
                    % ("enabled" if state.get("live_send_enabled")
                       else "simulated")),
            fix="No action needed before a launch window."))
    except Exception as exc:                                  # noqa: BLE001
        out.append(Check(
            "operations_layer", "The operations layer is present",
            False, severity=D.SEVERITY_REVIEW,
            detail="Not available in this build (%s)." % str(exc)[:80],
            fix="No action needed before a launch window."))
    return out


def _activation_scope_checks(db: Session, org: Organization,
                             deployment) -> List[Check]:
    """Would T6 let this run, at every scope above it?

    REPORTED, NOT ENFORCED AS BLOCKING. A platform that is still at `off` is
    the correct state for a dark launch, and a customer preparing an employee
    during one is doing the right thing. What must never happen is a screen
    saying READY beside an employee that four scopes forbid, so this is
    surfaced as a review item with the whole chain attached.
    """
    from app.services.workforce import activation as wf_activation
    from app.services.workforce import constants as WC

    resolved = wf_activation.resolve(
        db, organization_id=org.id,
        platform_id=getattr(org, "platform_id", None))
    would_run = (not resolved.killed) and resolved.state != WC.OFF
    chain = " -> ".join("%s:%s" % (c.get("scope"), c.get("state"))
                        for c in (resolved.chain or []))
    return [
        Check("kill_switch_clear", "No kill switch is engaged",
              not resolved.killed,
              detail=("Clear." if not resolved.killed
                      else "A kill switch is engaged at the %s level."
                           % (resolved.killed_by_scope or "platform")),
              fix="Release the kill switch before switching anything on."),
        Check("scopes_permit_running",
              "The platform, brand and customer scopes permit running",
              would_run, severity=D.SEVERITY_REVIEW,
              detail=("Effective stage: %s (%s)." % (resolved.state, chain)),
              fix="An operator raises the enclosing scopes when the launch "
                  "window opens."),
    ]


def _live_channel_checks(config: Dict[str, Any],
                         chosen: List[str]) -> List[Check]:
    """The channels whose live use is a decision somebody has to make."""
    out: List[Check] = []
    wants_voice = bool(config.get("voice_enabled")) or "voice" in chosen
    if wants_voice:
        try:
            from app.services.workforce import activation as wf_activation
            voice_on = bool(wf_activation.live_voice_enabled())
        except Exception:                                     # noqa: BLE001
            voice_on = False
        out.append(Check(
            "voice_permitted", "Live voice is permitted in this deployment",
            voice_on, severity=D.SEVERITY_REVIEW,
            detail=("Live voice is enabled." if voice_on
                    else "Live AI voice is switched off platform-wide, so this "
                         "employee will not place calls whatever else is "
                         "configured."),
            fix="Enabling live voice is a separate decision with a legal "
                "component in each jurisdiction."))
    out.append(Check(
        "outreach_shape_stated",
        "Somebody has said whether it reaches out, answers, or both",
        bool(config.get("inbound_enabled")) or bool(config.get(
            "outbound_enabled")),
        severity=D.SEVERITY_REVIEW,
        detail=("Stated." if (config.get("inbound_enabled")
                              or config.get("outbound_enabled"))
                else "Neither inbound nor outbound responsibility has been "
                     "stated."),
        fix="Say whether this employee starts conversations, answers them, or "
            "both."))
    return out


# ---------------------------------------------------------------------------
# STORING THE ANSWER
# ---------------------------------------------------------------------------

def refresh(db: Session, deployment: AIEmployeeDeployment) -> Readiness:
    """Evaluate and write the snapshot onto the deployment.

    The snapshot is what a screen reads. Nothing that GRANTS anything reads it:
    `activation.request` calls `evaluate` itself, because a stored verdict is
    an answer from whenever it was stored.
    """
    import json
    result = evaluate(db, deployment)
    deployment.readiness_state = result.verdict
    deployment.readiness_detail = json.dumps(result.as_dict())[:8000]
    deployment.readiness_checked_at = result.checked_at
    db.flush()
    return result


def stored(deployment: AIEmployeeDeployment) -> Optional[Dict[str, Any]]:
    import json
    if not deployment.readiness_detail:
        return None
    try:
        return json.loads(deployment.readiness_detail)
    except (ValueError, TypeError):
        return None
