"""THE GUIDED ONBOARDING, AND WHY ALMOST NONE OF IT IS GATED.

FIFTEEN STEPS, ONE RULE
-----------------------
Each step answers its own question from the rows that already exist — the
implementation, its milestones, its integrations, its checks, its intake, the
opportunity it came from, the commercial agreement beside it — and NO step
consults another step's answer to decide whether it may run.

That is the whole design. A customer whose settlement frequency has not been
agreed can still have users created, calendars connected, data imported and AI
configured, because none of those things depend on a settlement frequency. Only
the two acts that genuinely need complete terms — activating the agreement, and
calculating money — are blocked, and they are blocked BY NAME with the missing
term quoted back.

THE STATUS VOCABULARY
---------------------
  complete              done, the ordinary way
  completed_previously  done before this workflow existed; who decided, when,
                        why — and no invented date
  waived                deliberately skipped by an authorised person, with a
                        reason
  not_required          does not apply to this customer at all
  terms_required        a commercial term is missing; shown on the commercial
                        steps only, and never used to stop anything else
  blocked               something concrete is in the way, named in `reasons`
  incomplete            simply not done yet

`incomplete` and `blocked` are different on purpose. The first is work
remaining; the second is work that CANNOT proceed, and a screen that renders
them the same way sends somebody looking for a problem that is not there.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.commercial_models import (
    AG_ACTIVE, AG_APPROVED, AG_ENDED, ITEM_DEMO, ITEM_DISCOVERY,
    ITEM_INTAKE_STEP, ITEM_INTEGRATION, ITEM_MILESTONE,
    MODE_COMPLETED_PREVIOUSLY, MODE_NOT_APPLICABLE, MODE_WAIVED,
)
from app.models.implementation_models import (
    IMPL_LIVE, IMPL_READY_FOR_LAUNCH, Implementation, ImplementationMilestone,
    MILESTONE_DONE, MILESTONE_SETTLED, MILESTONE_SKIPPED,
)
from app.models.launch_delivery_models import (
    CHECK_PASS, INT_NOT_APPLICABLE, INT_VERIFIED, ImplementationCheck,
    ImplementationIntegration,
)
from app.models.models import Organization, User
from app.models.sales_models import Opportunity
from app.services.commercial import agreements as ag
from app.services.commercial import overrides as ov
from app.services.commercial import terms as t

S_COMPLETE             = "complete"
S_COMPLETED_PREVIOUSLY = "completed_previously"
S_WAIVED               = "waived"
S_NOT_REQUIRED         = "not_required"
S_TERMS_REQUIRED       = "terms_required"
S_BLOCKED              = "blocked"
S_INCOMPLETE           = "incomplete"

STEP_STATUSES = (S_COMPLETE, S_COMPLETED_PREVIOUSLY, S_WAIVED, S_NOT_REQUIRED,
                 S_TERMS_REQUIRED, S_BLOCKED, S_INCOMPLETE)

STATUS_LABELS = {
    S_COMPLETE:             "Complete",
    S_COMPLETED_PREVIOUSLY: "Completed previously",
    S_WAIVED:               "Waived",
    S_NOT_REQUIRED:         "Not required",
    S_TERMS_REQUIRED:       "Terms required",
    S_BLOCKED:              "Blocked",
    S_INCOMPLETE:           "Not started",
}

# Statuses that mean "nothing further is owed on this step".
SETTLED_STATUSES = (S_COMPLETE, S_COMPLETED_PREVIOUSLY, S_WAIVED, S_NOT_REQUIRED)

_OVERRIDE_STATUS = {
    MODE_COMPLETED_PREVIOUSLY: S_COMPLETED_PREVIOUSLY,
    MODE_WAIVED:               S_WAIVED,
    MODE_NOT_APPLICABLE:       S_NOT_REQUIRED,
}

# The flow. `customer_visible` decides whether the customer's own screen shows
# the step at all — the approval of commercial terms is the brand's business.
STEP_SCHEMA: List[Dict[str, Any]] = [
    {"key": "customer_organization", "n": 1, "label": "Customer",
     "description": "The company and its workspace.", "customer_visible": True},
    {"key": "primary_contact", "n": 2, "label": "Primary contact",
     "description": "Who we deal with.", "customer_visible": True},
    {"key": "commercial_model", "n": 3, "label": "Commercial model",
     "description": "What kind of arrangement this is.", "customer_visible": True},
    {"key": "commercial_terms", "n": 4, "label": "Commercial terms",
     "description": "The business questions behind the arrangement.",
     "customer_visible": True},
    {"key": "demo_discovery", "n": 5, "label": "Demo and discovery",
     "description": "What was shown and what was learned.", "customer_visible": True},
    {"key": "agreement_approval", "n": 6, "label": "Agreement and approval",
     "description": "Sign-off on the terms.", "customer_visible": False},
    {"key": "workspace_setup", "n": 7, "label": "Workspace setup",
     "description": "The customer's own environment.", "customer_visible": True},
    {"key": "users_roles", "n": 8, "label": "Users and roles",
     "description": "The customer's staff accounts.", "customer_visible": True},
    {"key": "business_profile", "n": 9, "label": "Business profile",
     "description": "Company details, hours, branding.", "customer_visible": True},
    {"key": "integrations", "n": 10, "label": "Integrations",
     "description": "The systems this connects to.", "customer_visible": True},
    {"key": "calendar_booking", "n": 11, "label": "Calendar and booking",
     "description": "How appointments are taken.", "customer_visible": True},
    {"key": "lead_data_intake", "n": 12, "label": "Lead and data intake",
     "description": "Existing contacts brought across.", "customer_visible": True},
    {"key": "ai_workforce_setup", "n": 13, "label": "AI workforce",
     "description": "AI employees configured for this customer.",
     "customer_visible": True},
    {"key": "readiness", "n": 14, "label": "Readiness",
     "description": "End-to-end tests of what this customer will use.",
     "customer_visible": True},
    {"key": "launch_preparation", "n": 15, "label": "Launch preparation",
     "description": "Final review before going live.", "customer_visible": True},
]

STEP_BY_KEY = {s["key"]: s for s in STEP_SCHEMA}

# Which milestone key, if any, each step reads. A step whose milestone does not
# exist on this implementation is NOT_REQUIRED — the customer bought a package
# that does not include it, and an absent step is not an unfinished one.
_STEP_MILESTONE = {
    "workspace_setup": "business_profile",
    "users_roles": "customer_users",
    "calendar_booking": "calendar",
    "lead_data_intake": "lead_import",
    "launch_preparation": "launch",
}


def _step(key: str, status: str, detail: Optional[str] = None,
          reasons: Optional[List[str]] = None,
          override: Optional[Dict[str, Any]] = None,
          extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    schema = STEP_BY_KEY[key]
    out = {
        "key": key,
        "n": schema["n"],
        "label": schema["label"],
        "description": schema["description"],
        "customer_visible": schema["customer_visible"],
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "detail": detail,
        "reasons": reasons or [],
        "override": override,
        "settled": status in SETTLED_STATUSES,
    }
    if extra:
        out.update(extra)
    return out


def _milestones(db: Session, impl: Implementation) -> Dict[str, ImplementationMilestone]:
    rows = (db.query(ImplementationMilestone)
            .filter(ImplementationMilestone.implementation_id == impl.id).all())
    return {r.key: r for r in rows}


def _milestone_step(key: str, milestones, overrides) -> Dict[str, Any]:
    """A step backed by one milestone row, with an override taken into account.

    The override is consulted FIRST and reported as itself, so a waived step
    reads as waived rather than as a mysteriously completed one.
    """
    mkey = _STEP_MILESTONE[key]
    row = milestones.get(mkey)
    o = overrides.get((ITEM_MILESTONE, mkey))

    if o is not None and o.mode in _OVERRIDE_STATUS:
        return _step(key, _OVERRIDE_STATUS[o.mode],
                     detail=o.reason, override=ov.public(o))

    if row is None:
        return _step(key, S_NOT_REQUIRED,
                     detail="This customer's programme does not include this step.")
    if row.status == MILESTONE_DONE:
        return _step(key, S_COMPLETE, detail=row.label)
    if row.status == MILESTONE_SKIPPED:
        return _step(key, S_NOT_REQUIRED, detail=row.notes or row.label)
    if row.status == "blocked":
        return _step(key, S_BLOCKED,
                     detail=row.label,
                     reasons=[row.notes or "This step is marked blocked."])
    return _step(key, S_INCOMPLETE, detail=row.label)


def steps(db: Session, impl: Implementation) -> List[Dict[str, Any]]:
    """Every step, each answered independently of every other."""
    org = (db.query(Organization)
           .filter(Organization.id == impl.organization_id).first())
    opp = (db.query(Opportunity)
           .filter(Opportunity.id == impl.opportunity_id).first()
           if impl.opportunity_id else None)
    agreement = (ag.current_for_organization(db, impl.organization_id)
                 if impl.organization_id else None)
    milestones = _milestones(db, impl)
    overrides = ov.active_map(db, impl.id)

    out: List[Dict[str, Any]] = []

    # 1 ── the tenant exists. If this is being rendered at all, it does.
    out.append(_step("customer_organization", S_COMPLETE,
                     detail=(org.name if org else None),
                     extra={"organization_id": impl.organization_id}))

    # 2 ── primary contact
    contact_name = getattr(opp, "contact_name", None)
    contact_email = getattr(opp, "email", None)
    contact_phone = getattr(opp, "phone", None)
    if contact_name and (contact_email or contact_phone):
        out.append(_step("primary_contact", S_COMPLETE, detail=contact_name,
                         extra={"contact": {"name": contact_name,
                                            "email": contact_email,
                                            "phone": contact_phone}}))
    else:
        missing = [w for w, v in (("a name", contact_name),
                                  ("an email address or phone number",
                                   contact_email or contact_phone)) if not v]
        out.append(_step("primary_contact", S_INCOMPLETE,
                         detail="The primary contact needs " + " and ".join(missing) + ".",
                         extra={"contact": {"name": contact_name,
                                            "email": contact_email,
                                            "phone": contact_phone}}))

    # 3 ── commercial model
    if agreement is None:
        out.append(_step("commercial_model", S_INCOMPLETE,
                         detail="No commercial arrangement has been recorded "
                                "for this customer yet."))
    else:
        out.append(_step("commercial_model", S_COMPLETE,
                         detail=ag.AGREEMENT_TYPE_LABELS.get(
                             agreement.agreement_type, agreement.agreement_type),
                         extra={"agreement_id": agreement.id,
                                "agreement_type": agreement.agreement_type}))

    # 4 ── commercial terms
    #
    # TERMS_REQUIRED here is a statement about the TERMS. It is not consulted
    # by any step below it, and it never becomes a reason another step cannot
    # run.
    if agreement is None:
        out.append(_step("commercial_terms", S_INCOMPLETE,
                         detail="There is no agreement to hold terms yet."))
    else:
        comp = t.completeness(db, agreement)
        missing_rows = t.missing_required(db, agreement)
        if comp["terms_complete"]:
            out.append(_step("commercial_terms", S_COMPLETE,
                             detail="Every term this arrangement needs has been "
                                    "answered.",
                             extra={"completeness": comp}))
        else:
            out.append(_step("commercial_terms", S_TERMS_REQUIRED,
                             detail="%d of %d terms still to answer."
                                    % (comp["required_missing"], comp["required_total"]),
                             reasons=["\"%s\" has not been answered." % r["label"]
                                      for r in missing_rows],
                             extra={"completeness": comp}))

    # 5 ── demo and discovery
    out.append(_demo_step(opp, overrides))

    # 6 ── agreement and approval
    out.append(_approval_step(db, agreement))

    # 7-9, 11, 12, 15 ── milestone-backed steps
    out.append(_milestone_step("workspace_setup", milestones, overrides))
    out.append(_milestone_step("users_roles", milestones, overrides))
    out.append(_business_profile_step(db, impl, overrides))
    out.append(_integrations_step(db, impl, overrides))
    out.append(_milestone_step("calendar_booking", milestones, overrides))
    out.append(_milestone_step("lead_data_intake", milestones, overrides))
    out.append(_ai_step(db, impl))
    out.append(_readiness_step(db, impl))
    out.append(_milestone_step("launch_preparation", milestones, overrides))

    out.sort(key=lambda s: s["n"])
    return out


def _demo_step(opp: Optional[Opportunity], overrides) -> Dict[str, Any]:
    """The step this whole override mechanism was built for.

    A demo given before the workflow existed is satisfied by a recorded
    decision, not by a fabricated completion. The step reports WHICH it was.
    """
    o_demo = overrides.get((ITEM_DEMO, "demo"))
    o_disc = overrides.get((ITEM_DISCOVERY, "discovery"))

    demo_status = getattr(opp, "demo_status", None)
    discovery_done = bool(getattr(opp, "discovery_completed_at", None))

    if o_demo is not None and o_demo.mode in _OVERRIDE_STATUS:
        return _step("demo_discovery", _OVERRIDE_STATUS[o_demo.mode],
                     detail=o_demo.reason, override=ov.public(o_demo),
                     extra={"demo_status": demo_status,
                            "discovery_completed": discovery_done,
                            "discovery_override": ov.public(o_disc)})

    if demo_status in ("delivered", "ready"):
        return _step("demo_discovery", S_COMPLETE,
                     detail="A demo has been %s." % demo_status,
                     extra={"demo_status": demo_status,
                            "discovery_completed": discovery_done,
                            "discovery_override": ov.public(o_disc)})

    return _step("demo_discovery", S_INCOMPLETE,
                 detail=("No demo has been recorded for this customer. If one "
                         "was given before this workflow existed, record it as "
                         "completed previously rather than repeating it."),
                 extra={"demo_status": demo_status,
                        "discovery_completed": discovery_done,
                        "discovery_override": ov.public(o_disc)})


def _approval_step(db: Session, agreement) -> Dict[str, Any]:
    if agreement is None:
        return _step("agreement_approval", S_INCOMPLETE,
                     detail="There is no agreement to approve yet.")
    if agreement.status in (AG_APPROVED, AG_ACTIVE):
        return _step("agreement_approval", S_COMPLETE,
                     detail=("Approved and in force." if agreement.status == AG_ACTIVE
                             else "Approved, not yet in force."),
                     extra={"agreement_id": agreement.id,
                            "agreement_status": agreement.status})
    if agreement.status == AG_ENDED:
        return _step("agreement_approval", S_NOT_REQUIRED,
                     detail="This agreement has ended.",
                     extra={"agreement_id": agreement.id})

    blockers = ag.activation_blockers(db, agreement)
    if blockers:
        return _step("agreement_approval", S_BLOCKED,
                     detail="The agreement cannot be approved until its terms "
                            "are complete.",
                     reasons=blockers,
                     extra={"agreement_id": agreement.id,
                            "agreement_status": agreement.status})
    return _step("agreement_approval", S_INCOMPLETE,
                 detail="The terms are complete and waiting for sign-off.",
                 extra={"agreement_id": agreement.id,
                        "agreement_status": agreement.status})


def _business_profile_step(db: Session, impl: Implementation,
                           overrides) -> Dict[str, Any]:
    """Backed by the launch engine's own intake, not by a milestone.

    The intake is where the customer actually types their company details, so
    this step reports what the intake says rather than what somebody ticked.
    """
    from app.services import launch_intake as li

    o = overrides.get((ITEM_INTAKE_STEP, "company"))
    if o is not None and o.mode in _OVERRIDE_STATUS:
        return _step("business_profile", _OVERRIDE_STATUS[o.mode],
                     detail=o.reason, override=ov.public(o))

    try:
        overview = li.overview(db, impl.id, impl.organization_id)
    except Exception:
        return _step("business_profile", S_INCOMPLETE,
                     detail="The intake has not been started.")

    company = next((s for s in overview.get("steps", [])
                    if s.get("key") == "company"), None)
    pct = int((company or {}).get("completion_pct") or 0)
    if pct >= 100:
        return _step("business_profile", S_COMPLETE,
                     detail="Company information is complete.",
                     extra={"intake_pct": overview.get("overall_pct")})
    return _step("business_profile", S_INCOMPLETE,
                 detail="Company information is %d%% complete." % pct,
                 extra={"intake_pct": overview.get("overall_pct")})


def _integrations_step(db: Session, impl: Implementation,
                       overrides) -> Dict[str, Any]:
    rows = (db.query(ImplementationIntegration)
            .filter(ImplementationIntegration.implementation_id == impl.id).all())
    if not rows:
        return _step("integrations", S_NOT_REQUIRED,
                     detail="No integrations are required for this customer.")

    outstanding = []
    for r in rows:
        if r.status in (INT_VERIFIED, INT_NOT_APPLICABLE):
            continue
        o = overrides.get((ITEM_INTEGRATION, r.key))
        if o is not None and o.mode in _OVERRIDE_STATUS:
            continue
        if r.is_required:
            outstanding.append(r.label)

    if not outstanding:
        return _step("integrations", S_COMPLETE,
                     detail="%d integration(s) settled." % len(rows))
    return _step("integrations", S_INCOMPLETE,
                 detail="%d integration(s) still to verify." % len(outstanding),
                 reasons=outstanding)


def _ai_step(db: Session, impl: Implementation) -> Dict[str, Any]:
    """Reports what T8 holds. Does not authorise anything.

    An AI employee is deployed when T8's own entitlement and readiness say so.
    A commercial agreement being approved is NOT that, and this step makes no
    attempt to make it so — it reads the deployments and reports them.
    """
    from app.models.ai_deployment_models import AIEmployeeDeployment

    rows = (db.query(AIEmployeeDeployment)
            .filter(AIEmployeeDeployment.organization_id == impl.organization_id)
            .all())
    if not rows:
        return _step("ai_workforce_setup", S_NOT_REQUIRED,
                     detail="No AI employees have been selected for this "
                            "customer.",
                     extra={"deployment_count": 0})

    configured = [r for r in rows if r.configured_at is not None]
    if len(configured) == len(rows):
        return _step("ai_workforce_setup", S_COMPLETE,
                     detail="%d AI employee(s) configured." % len(rows),
                     extra={"deployment_count": len(rows),
                            "configured_count": len(configured)})
    return _step("ai_workforce_setup", S_INCOMPLETE,
                 detail="%d of %d AI employee(s) configured."
                        % (len(configured), len(rows)),
                 extra={"deployment_count": len(rows),
                        "configured_count": len(configured)})


def _readiness_step(db: Session, impl: Implementation) -> Dict[str, Any]:
    rows = (db.query(ImplementationCheck)
            .filter(ImplementationCheck.implementation_id == impl.id).all())
    if not rows:
        return _step("readiness", S_NOT_REQUIRED,
                     detail="No readiness checks are defined for this customer.")
    outstanding = [r.label for r in rows
                   if r.is_required and r.status != CHECK_PASS]
    if not outstanding:
        return _step("readiness", S_COMPLETE,
                     detail="%d check(s) passed." % len(rows))
    return _step("readiness", S_INCOMPLETE,
                 detail="%d required check(s) outstanding." % len(outstanding),
                 reasons=outstanding)


def progress(db: Session, impl: Implementation) -> Dict[str, Any]:
    """The whole flow, plus the two counts a screen actually needs."""
    rows = steps(db, impl)
    settled = [s for s in rows if s["settled"]]
    blocked = [s for s in rows if s["status"] == S_BLOCKED]
    return {
        "implementation_id": impl.id,
        "organization_id": impl.organization_id,
        "implementation_status": impl.status,
        "is_live": impl.status == IMPL_LIVE,
        "ready_for_launch": impl.status in (IMPL_READY_FOR_LAUNCH, IMPL_LIVE),
        "steps": rows,
        "step_total": len(rows),
        "step_settled": len(settled),
        "step_blocked": len(blocked),
        "overall_pct": int(round(100 * len(settled) / len(rows))) if rows else 0,
        "note": "Steps are independent. An incomplete commercial term blocks "
                "activation and settlement, and nothing else.",
    }
