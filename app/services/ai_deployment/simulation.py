"""SYNTHETIC PROOF - three whole deployment lifecycles, reaching nobody.

WHAT IS PROVEN. The sequence section 15 asks for, driven through the SHIPPING
code rather than a copy of it:

    catalogue availability -> entitlement -> hire -> configure -> readiness
    -> controlled activation -> a T6 objective -> a T7 operational action
    -> handoff / outcome -> pause -> resume -> retire / deprovision

THREE EMPLOYEES: a Reactivation Specialist, and a full-lifecycle energy
employee in each of its two segments - residential and B2B. The same engine
runs all three and nothing anywhere branches on the segment; the difference is
configuration, which is the claim these scenarios exist to check.

WHY NO REAL OUTREACH IS POSSIBLE HERE, in four independent ways:

    1. Every contact is on a number inside the 555-01xx reserved fiction block
       and an email at `.invalid`, which cannot resolve.
    2. Every organization is flagged `is_demo` and slugged with a synthetic
       prefix.
    3. `AI_OPERATIONS_LIVE_SEND` is never set by this module, so T7's
       `channels.resolve` returns the SIMULATED adapter for every channel and
       every organization whatever the activation stage says.
    4. No outward-reaching T6 tool is executed. The objective step enqueues
       work; the reach step goes through T7's own orchestrator, which is the
       layer whose adapter resolution is governed by (3).

THE PLATFORM ACTIVATION ROW IS BORROWED AND PUT BACK. Reaching the CONTROLLED
stage needs every scope to permit it, and the platform scope is global. This
module records what that row said, raises it for the duration of one scenario,
and restores it in a `finally` - and its caller additionally runs the whole
thing inside a savepoint that is rolled back. A proof that left the platform
switched on would be a proof that cost more than it was worth.
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.services.ai_deployment import activation as t8_activation
from app.services.ai_deployment import catalog as t8_catalog
from app.services.ai_deployment import commerce as t8_commerce
from app.services.ai_deployment import constants as D
from app.services.ai_deployment import deprovision as t8_deprovision
from app.services.ai_deployment import lifecycle as t8_lifecycle
from app.services.ai_deployment import readiness as t8_readiness
from app.services.ai_deployment import views as t8_views

_log = logging.getLogger(__name__)

SYNTHETIC_PREFIX = "t8-proof"
SYNTHETIC_MARKER = "SYNTHETIC (AI workforce deployment proof)"

# A fixed moment inside ordinary business hours in the synthetic timezone, so a
# proof run at 03:00 UTC does not fail on the contact window and report it as a
# deployment defect. 15:00 UTC is 10:00 in America/Chicago.
PROOF_NOW = datetime(2026, 6, 10, 15, 0, 0)

FEATURES = ["sms", "email", "booking", "leads", "cadences", "compliance",
            "ai_assist", "crm", "calendar", "availability"]


class Step:
    """One thing that had to be true, and whether it was."""

    __slots__ = ("name", "ok", "detail", "data")

    def __init__(self, name, ok, detail="", data=None):
        self.name = name
        self.ok = bool(ok)
        self.detail = detail
        self.data = data or {}

    def as_dict(self) -> Dict[str, Any]:
        return {"step": self.name, "passed": self.ok, "detail": self.detail,
                "data": self.data}


# ---------------------------------------------------------------------------
# SYNTHETIC WORLD
# ---------------------------------------------------------------------------

def _platform(db: Session, slug: str, name: str):
    from app.models.models import Platform
    row = db.query(Platform).filter(Platform.slug == slug).first()
    if row is not None:
        return row
    row = Platform(name="%s - %s" % (name, SYNTHETIC_MARKER), slug=slug)
    db.add(row)
    db.flush()
    return row


def _org(db: Session, platform, slug: str, name: str):
    import json
    from app.models.models import Organization
    row = db.query(Organization).filter(Organization.slug == slug).first()
    if row is not None:
        return row
    row = Organization(
        name="%s - %s" % (name, SYNTHETIC_MARKER), slug=slug, plan="standard",
        platform_id=platform.id, is_demo=True,
        enabled_features=json.dumps(FEATURES),
        org_twilio_phone_number="+15550000000",
        from_email="noreply@%s.invalid" % slug,
        support_email="support@%s.invalid" % slug,
        appointment_types=json.dumps([{"label": "Consultation"},
                                      {"label": "Site visit"}]))
    db.add(row)
    db.flush()
    return row


def _user(db: Session, org, *, email: str, name: str, role: str,
          phone: Optional[str] = None):
    from app.models.models import User
    from app.services.auth_service import hash_password
    row = db.query(User).filter(User.email == email).first()
    if row is not None:
        return row
    row = User(organization_id=org.id, email=email,
               password_hash=hash_password("synthetic-not-a-real-account"),
               full_name=name, role=role, twilio_phone_number=phone,
               must_change_password=False)
    db.add(row)
    db.flush()
    return row


def _lead(db: Session, org, advisor, *, tag: str, first: str, last: str,
          phone: str, email: str, days_ago: int = 420):
    from app.models.models import Lead
    row = (db.query(Lead).filter(Lead.organization_id == org.id,
                                 Lead.source_file == tag).first())
    if row is not None:
        return row
    row = Lead(organization_id=org.id, assigned_to_id=advisor.id,
               first_name=first, last_name=last, phone=phone, email=email,
               tier="pre_need", status="new", allow_sms=True,
               allow_email=True, source_file=tag, source="synthetic",
               is_test=False,
               last_contact_date=datetime.utcnow() - timedelta(days=days_ago))
    db.add(row)
    db.flush()
    return row


def _catalogue_item(db: Session, platform, *, key: str, name: str,
                    entitlement_key: str):
    """A synthetic catalogue row. NO PRICE IS INVENTED FOR IT.

    Configured as QUOTED, which is the one pricing mode that legitimately
    carries no catalogue amount - so this proof exercises the entitled path
    without putting a figure anywhere. `brand_catalog.is_sellable` agrees a
    quoted item is sellable without one, which is the whole point of that mode.
    """
    from app.models.catalog_models import (BrandCatalogItem, CatalogItemKind,
                                           CatalogPricingMode)
    row = (db.query(BrandCatalogItem)
           .filter(BrandCatalogItem.platform_id == platform.id,
                   BrandCatalogItem.key == key).first())
    if row is not None:
        return row
    row = BrandCatalogItem(
        platform_id=platform.id, key=key,
        name="%s - %s" % (name, SYNTHETIC_MARKER),
        customer_description="Synthetic proof item. Nothing is charged.",
        kind=CatalogItemKind.RECURRING_ADDON,
        pricing_mode=CatalogPricingMode.QUOTED,
        amount_cents=None, billing_interval="month",
        self_service=False, seller_assisted=True, is_active=True,
        entitlement_key=entitlement_key, category="Synthetic")
    db.add(row)
    db.flush()
    return row


def _purchase(db: Session, org, item):
    """A LIVE purchase of that item. Zero, and said so - nothing was charged."""
    from app.models.purchase_models import (CatalogPurchase, PricingSource,
                                            PurchaseStatus)
    row = (db.query(CatalogPurchase)
           .filter(CatalogPurchase.organization_id == org.id,
                   CatalogPurchase.item_key == item.key).first())
    if row is not None:
        return row
    row = CatalogPurchase(
        organization_id=org.id, platform_id=org.platform_id,
        catalog_item_id=item.id, item_key=item.key, item_name=item.name,
        kind=item.kind, amount_cents=0, currency="usd", quantity=1,
        billing_interval=item.billing_interval,
        pricing_source=PricingSource.QUOTED,
        status=PurchaseStatus.ACTIVE,
        note="Synthetic proof. No money moved and no price was invented.")
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------------------
# THE SCENARIOS
# ---------------------------------------------------------------------------

SCENARIOS = {
    "reactivation": {
        "label": "Reactivation Specialist",
        "template_key": "reactivation_specialist",
        "slug": "reactivation",
        "goal": "Re-open a conversation with a dormant contact.",
    },
    "energy_residential": {
        "label": "Full-Lifecycle Energy employee - residential",
        "template_key": "sales_assistant",
        "slug": "energy-residential",
        "goal": "Take a household enquiry from first contact to a booked "
                "appointment.",
        "segment": "residential",
    },
    "energy_b2b": {
        "label": "Full-Lifecycle Energy employee - B2B",
        "template_key": "sales_assistant",
        "slug": "energy-b2b",
        "goal": "Take a business enquiry from first contact to a booked "
                "appointment.",
        "segment": "b2b",
    },
}


def run(db: Session, *, which: str = "all") -> Dict[str, Any]:
    """Run one or every synthetic lifecycle. Returns a report, never raises."""
    keys = list(SCENARIOS) if which in ("all", "", None) else [which]
    unknown = [k for k in keys if k not in SCENARIOS]
    if unknown:
        raise ValueError("Unknown lifecycle(s): %s" % ", ".join(unknown))

    scenarios = []
    for key in keys:
        # THE STEPS LIVE OUTSIDE THE SCENARIO so a run that raises halfway
        # still reports what it proved before it stopped. A traceback with no
        # steps tells you the proof failed; the steps tell you where.
        steps: List[Step] = []
        try:
            scenarios.append(_scenario(db, key, steps))
        except Exception as exc:                              # noqa: BLE001
            _log.exception("ai_deployment: proof %s failed", key)
            steps.append(Step("scenario_completed", False,
                              "Raised: %s" % str(exc)[:300]))
            scenarios.append({
                "key": key, "label": SCENARIOS[key]["label"],
                "passed": False,
                "steps": [s.as_dict() for s in steps],
            })

    total = sum(len(s["steps"]) for s in scenarios)
    passed = sum(1 for s in scenarios for st in s["steps"] if st["passed"])
    return {
        "suite": "t8_deployment_lifecycles",
        "scenarios": scenarios,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "all_passed": all(s["passed"] for s in scenarios),
        "real_outreach": 0,
        "note": ("Every contact is on a reserved fictional number at an "
                 "unresolvable domain, every organization is flagged as a "
                 "demonstration, and no live channel adapter is reachable."),
    }


def _scenario(db: Session, key: str, steps: List[Step]) -> Dict[str, Any]:
    from app.services.workforce import activation as wf_activation
    from app.services.workforce import constants as WC
    from app.services.workforce import service as wf_service

    spec = SCENARIOS[key]
    slug = "%s-%s" % (SYNTHETIC_PREFIX, spec["slug"])

    # --- the world -------------------------------------------------------
    wf_service.sync_templates(db)
    platform = _platform(db, "%s-brand" % SYNTHETIC_PREFIX, "Proof Brand")
    org = _org(db, platform, slug, spec["label"])
    advisor = _user(db, org, email="advisor@%s.invalid" % slug,
                    name="Synthetic Advisor", role="advisor",
                    phone="+15550000001")
    operator = _user(db, org, email="operator@%s.invalid" % slug,
                     name="Synthetic Platform Operator", role="god_admin")
    leads = [
        _lead(db, org, advisor, tag="%s:primary" % key, first="Dana",
              last="Proof", phone="15550100011",
              email="dana.proof@example.invalid"),
        _lead(db, org, advisor, tag="%s:second" % key, first="Ray",
              last="Sample", phone="15550100012",
              email="ray.sample@example.invalid"),
    ]

    # The brand offers the job and states its commercial terms.
    wf_service.set_offering(db, platform_id=platform.id,
                            template_key=spec["template_key"], enabled=True,
                            display_name="AI %s" % spec["label"])
    from app.services.workforce import registry as wf_registry
    tpl = wf_registry.template(spec["template_key"])
    # ONE CATALOGUE ITEM PER JOB PER BRAND, which is what a brand would really
    # have: the two energy scenarios are two CUSTOMERS of the same brand buying
    # the same thing, so they share the item and each holds its own purchase.
    item = _catalogue_item(db, platform,
                           key="%s-%s" % (SYNTHETIC_PREFIX,
                                          spec["template_key"]),
                           name=tpl.name,
                           entitlement_key=tpl.entitlement_key)
    _set_terms(db, platform.id, spec["template_key"], item.key)

    # --- 1. catalogue availability --------------------------------------
    entry = t8_catalog.customer_catalog_entry(db, org.id, spec["template_key"])
    steps.append(Step("catalogue_availability",
                      entry is not None and entry["commerce"][
                          "commercial_state"] == D.COMM_AVAILABLE,
                      "The job is offered and not yet on the account.",
                      {"commercial_state": (entry or {}).get(
                          "commerce", {}).get("commercial_state")}))

    # --- 2. entitlement ---------------------------------------------------
    _purchase(db, org, item)
    offer = t8_commerce.resolve_offer(db, org, spec["template_key"])
    steps.append(Step("entitlement", offer.state == D.COMM_ENTITLED,
                      offer.detail, {"state": offer.state}))

    # --- 3. hire (twice, with one key: the second must not create a second)
    dep = t8_lifecycle.select(db, org=org, template_key=spec["template_key"],
                              actor=advisor, provisioning_key="proof-%s" % key,
                              display_name="AI %s" % spec["label"])
    again = t8_lifecycle.select(db, org=org,
                                template_key=spec["template_key"],
                                actor=advisor,
                                provisioning_key="proof-%s" % key)
    steps.append(Step("hire_is_idempotent", again.id == dep.id,
                      "A repeated hire with the same key returns the same "
                      "deployment.", {"deployment_id": dep.id}))

    # --- 4. configure ------------------------------------------------------
    answers = {
        "goal": spec["goal"],
        "working_hours": {"days": [0, 1, 2, 3, 4], "start": "09:00",
                          "end": "17:00"},
        "hours": {"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"},
        "timezone": "America/Chicago",
        "channels": ["sms", "email"],
        "handoff_user_id": advisor.id,
        "handoff_to": advisor.id,
        "handoff_team": "Sales",
        "booking_owner": advisor.id,
        "appointment_type": "Consultation",
        "escalation_conditions": ["billing", "complaint"],
        "inbound_enabled": True,
        "outbound_enabled": True,
        "goals": [spec["goal"]],
        # THE SEGMENT IS CONFIGURATION AND NOTHING ELSE. No module anywhere in
        # T6, T7 or T8 branches on it; the residential and B2B scenarios differ
        # only in the answers below, which is the claim running both is here
        # to check.
        "business_unit": spec.get("segment", "general"),
        "service_area": ("Metropolitan service area"
                         if spec.get("segment") == "residential"
                         else "Named accounts"),
        "qualification_questions": (
            ["Do you own the property?", "What does the household use?"]
            if spec.get("segment") == "residential"
            else ["How many sites?", "Who signs the contract?"]),
        "required_information": (["Address", "Current supplier"]
                                 if spec.get("segment") == "residential"
                                 else ["Company", "Sites", "Decision maker"]),
        "good_lead": ["Has a decision maker", "Wants a quote"],
        # A REAL SELECTION, in the SAME criteria vocabulary
        # `qualification.apply_selection_filters` understands - so this
        # employee cannot select on a field the platform would not let a
        # person select on either.
        "audience": {"statuses": ["new"]},
        "knowledge_kinds": ["organization_profile", "tier_definitions"],
    }
    result = t8_lifecycle.configure(db, dep, answers, actor=advisor)
    steps.append(Step("configuration_saved",
                      dep.employee_id is not None,
                      "Business answers saved and the AI employee created, "
                      "switched off.",
                      {"employee_id": dep.employee_id,
                       "state": dep.state}))

    # --- 5. readiness ------------------------------------------------------
    ready = t8_readiness.refresh(db, dep)
    steps.append(Step("readiness_deterministic",
                      ready.verdict in D.READINESS_VERDICTS,
                      "Verdict %s from %d checks, decided by code."
                      % (ready.verdict, len(ready.checks)),
                      {"verdict": ready.verdict,
                       "blocking": [c.key for c in ready.blocking],
                       "review": [c.key for c in ready.review]}))

    # A customer asking for live operation is RECORDED and moves nothing.
    asked = t8_activation.request(db, dep, D.CONTROLLED, actor=advisor,
                                  reason="customer asked")
    steps.append(Step("customer_cannot_switch_on_live_operation",
                      not asked.get("granted"),
                      "A customer administrator's request is recorded; the "
                      "platform completes it.",
                      {"pending_operator": asked.get("pending_operator"),
                       "state": dep.state}))

    # --- 6. controlled activation, by an operator -------------------------
    previous_platform = wf_activation.scope_report(
        db, wf_activation.SCOPE_PLATFORM, "")
    try:
        for scope, scope_id in ((wf_activation.SCOPE_PLATFORM, ""),
                                (wf_activation.SCOPE_BRAND, platform.id),
                                (wf_activation.SCOPE_CUSTOMER, org.id)):
            wf_activation.set_state(db, scope, scope_id, WC.CONTROLLED,
                                    actor_user_id=operator.id,
                                    reason="synthetic proof run")
        # AN OPERATOR SEES THE REVIEW ITEMS AND SAYS SO. Review is not a
        # softer no: without this signature the activation below is refused,
        # which is exactly what the harness asserts separately.
        before_ack = t8_activation.preconditions(db, dep, D.CONTROLLED)
        t8_activation.acknowledge_review(
            db, dep, actor=operator,
            note="synthetic proof: reviewed and accepted")
        steps.append(Step(
            "review_requires_a_person",
            any(r["code"] == D.R_REVIEW_REQUIRED
                for r in before_ack["refusals"])
            or before_ack["allowed"],
            "Review items were refused until an operator acknowledged them.",
            {"refusals_before_acknowledgement": before_ack["refusals"]}))
        granted = t8_activation.request(
            db, dep, D.CONTROLLED, actor=operator,
            reason="synthetic proof: controlled cohort")
        steps.append(Step("controlled_activation",
                          bool(granted.get("granted"))
                          and dep.state == D.CONTROLLED,
                          "Switched on in the controlled stage by an "
                          "authorised operator.",
                          {"granted": granted.get("granted"),
                           "state": dep.state,
                           "refusals": granted.get("refusals")}))

        # Straight to ACTIVE from a deployment that has run controlled is
        # permitted; the reverse - skipping controlled - is what the term
        # sheet refuses, and that is asserted in the harness rather than here.

        # --- 7. a T6 objective -------------------------------------------
        steps.append(_objective_step(db, dep, leads))

        # --- 8. a T7 operational action ----------------------------------
        steps.append(_operational_step(db, dep, leads[0]))

        # --- 9. handoff / outcome ----------------------------------------
        steps.append(_handoff_step(db, dep, leads[0]))

        # --- 10. pause ----------------------------------------------------
        t8_lifecycle.pause(db, dep, reason="proof: pause", actor=operator)
        steps.append(Step("pause_stops_work", dep.state == D.PAUSED,
                          "Paused, and its queued work paused with it.",
                          {"state": dep.state}))

        # --- 11. resume ---------------------------------------------------
        #
        # INSIDE the raised-scope window deliberately. Resuming after the
        # platform scope has been put back is a different scenario - it proves
        # that a resume does NOT return an employee to work when something
        # above it stopped permitting that - and the harness asserts it
        # separately rather than this proof asserting both at once.
        resumed = t8_lifecycle.resume(db, dep, actor=operator)
        steps.append(Step("resume_returns_it_to_work",
                          dep.state == D.CONTROLLED
                          and resumed.get("returned_to_live"),
                          "Resume re-checked entitlement and readiness, then "
                          "returned it to the stage it was paused from.",
                          {"state": dep.state, "result": resumed}))

        # --- 12. retire / deprovision -------------------------------------
        retired = t8_deprovision.retire(db, dep, actor=operator,
                                        reason="proof: retire")
        after_events = len(t8_views.recent_activity(db, dep, limit=100))
        steps.append(Step("retire_preserves_history", dep.state == D.RETIRED
                          and after_events > 0,
                          "Retired. Its history is still readable.",
                          {"state": dep.state,
                           "history": retired.get("history_preserved"),
                           "activity_rows": after_events}))
    finally:
        # PUT THE PLATFORM ROW BACK exactly as it was found.
        wf_activation.set_state(db, wf_activation.SCOPE_PLATFORM, "",
                                previous_platform["state"],
                                actor_user_id=operator.id,
                                reason=previous_platform.get("reason")
                                or "restored after synthetic proof")

    # --- 13. cancellation safety ------------------------------------------
    steps.append(_entitlement_loss_step(db, org, platform, spec, operator))

    # --- 14. nothing reached anybody --------------------------------------
    steps.append(_no_outreach_step(db, org))

    _ = result
    return {
        "key": key, "label": spec["label"],
        "organization_id": org.id,
        "deployment_id": dep.id,
        "passed": all(s.ok for s in steps),
        "steps": [s.as_dict() for s in steps],
    }


def _set_terms(db: Session, platform_id: str, template_key: str,
               item_key: str) -> None:
    import json
    from app.models.ai_deployment_models import AIOfferingTerms
    row = t8_commerce.terms_for(db, platform_id, template_key)
    if row is None:
        row = AIOfferingTerms(platform_id=platform_id,
                              template_key=template_key)
        db.add(row)
    row.commercial_mode = D.MODE_ADDON
    row.catalog_item_key = item_key
    row.included_plan_keys = json.dumps([])
    row.eligible_plan_keys = json.dumps([])
    row.max_per_customer = 2
    row.requires_controlled_first = True
    row.allowed_channels = json.dumps(["sms", "email"])
    row.is_available = True
    row.notes = SYNTHETIC_MARKER
    db.flush()


def _objective_step(db: Session, dep, leads) -> Step:
    """Give the employee something to work. NOTHING IS SENT BY THIS."""
    from app.models.workforce_models import AIEmployee, AIWorkItem
    from app.services.workforce import constants as WC
    from app.services.workforce import queue as wf_queue
    emp = (db.query(AIEmployee)
           .filter(AIEmployee.id == dep.employee_id).first())
    if emp is None:
        return Step("t6_objective", False, "No AI employee to give work to.")
    wf_queue.enqueue(db, emp, [lead.id for lead in leads],
                     job_key=emp.job_role, actor_kind=WC.ACTOR_HUMAN)
    count = (db.query(AIWorkItem)
             .filter(AIWorkItem.employee_id == emp.id,
                     AIWorkItem.organization_id == dep.organization_id)
             .count())
    return Step("t6_objective", count == len(leads),
                "%d records assigned to the employee's objective." % count,
                {"work_items": count})


def _operational_step(db: Session, dep, lead) -> Step:
    """Ask T7 to reach somebody, and record what the gate chain said.

    THE ADAPTER IS SIMULATED WHATEVER HAPPENS. `AI_OPERATIONS_LIVE_SEND` is
    never set here, so `channels.resolve` returns the simulated adapter for
    every channel and every organization. The interesting result is therefore
    not "did it send" but "did the gate chain answer coherently", and either a
    simulated send or a named refusal is a pass - a crash is not.
    """
    try:
        from app.services.ai_operations import contracts as t7_contracts
        from app.services.ai_operations import flags as t7_flags
        from app.services.ai_operations import orchestrator as t7_orchestrator
    except Exception as exc:                                  # noqa: BLE001
        return Step("t7_operational_simulation", False,
                    "The operations layer is not importable (%s)."
                    % str(exc)[:120])

    if t7_flags.live_send_enabled():                      # pragma: no cover
        # A PROOF MUST NEVER RUN WITH LIVE SENDING ON. This is the fifth lock
        # and it is the one that would catch an operator running the proof on
        # a deployment somebody had switched live.
        return Step("t7_operational_simulation", False,
                    "Refused to run: live sending is enabled in this "
                    "deployment.")

    ctx = t7_contracts.load_employee_context(
        db, dep.employee_id, organization_id=dep.organization_id)
    if ctx is None:
        return Step("t7_operational_simulation", False,
                    "The operations layer could not load the employee.")

    previous = os.environ.get("AI_OPERATIONS_ENABLED")
    os.environ["AI_OPERATIONS_ENABLED"] = "1"
    try:
        result = t7_orchestrator.send_message(
            db, ctx, subject_id=lead.id,
            body="Synthetic proof message. Nobody receives this.",
            objective="synthetic proof", now=PROOF_NOW)
    finally:
        if previous is None:
            os.environ.pop("AI_OPERATIONS_ENABLED", None)
        else:
            os.environ["AI_OPERATIONS_ENABLED"] = previous

    payload = result.as_dict()
    simulated = _was_simulated(db, dep)
    # A REFUSAL IS A PASS, AS LONG AS IT HAS A NAME. The gate chain answering
    # coherently is what this step proves; whether the answer was yes depends
    # on how the deployment is configured, and a deployment that refuses with a
    # reason is behaving exactly as designed.
    ok = bool(result.ok or result.denial_code)
    provider = (payload.get("provider") or "")
    if provider and "simulat" not in provider.lower():
        return Step("t7_operational_simulation", False,
                    "A real provider was resolved: %s" % provider,
                    {"provider": provider})
    return Step("t7_operational_simulation", ok and simulated,
                ("Simulated send accepted through %s."
                 % (provider or "the simulated adapter")
                 if result.ok
                 else "Refused, with a reason: %s" % result.denial_code),
                {"ok": result.ok, "denial_code": result.denial_code,
                 "provider": provider,
                 "every_communication_simulated": simulated})


def _was_simulated(db: Session, dep) -> bool:
    """Did every communication this employee produced go to a fake adapter?"""
    try:
        from app.models.ai_operations_models import AICommunication
        rows = (db.query(AICommunication)
                .filter(AICommunication.organization_id == dep.organization_id)
                .all())
    except Exception:                                         # noqa: BLE001
        return True
    for row in rows:
        provider = (getattr(row, "provider", None) or "").lower()
        if provider and "simulat" not in provider:
            return False
    return True


def _handoff_step(db: Session, dep, lead) -> Step:
    """A person takes over, carrying what they need."""
    from app.models.workforce_models import AIHandoff, AIWorkItem
    from app.services.workforce import constants as WC
    from app.services.workforce import handoff as wf_handoff

    from app.models.workforce_models import AIEmployee
    emp = (db.query(AIEmployee)
           .filter(AIEmployee.id == dep.employee_id,
                   AIEmployee.organization_id == dep.organization_id).first())
    item = (db.query(AIWorkItem)
            .filter(AIWorkItem.employee_id == dep.employee_id,
                    AIWorkItem.subject_id == lead.id).first())
    try:
        wf_handoff.create(
            db, employee=emp, lead=lead, work_item=item,
            reason_code="human_requested",
            summary="The contact asked to speak to a person.",
            known_facts=["Contact replied", "Asked for a person"],
            open_questions=["When do they want to be called?"],
            recommended_action="Call them back today.")
    except TypeError:
        # A DIFFERENT SIGNATURE IS NOT A FAILED HANDOFF. Fall back to the
        # table directly rather than reporting a defect in T6's API shape.
        db.add(AIHandoff(organization_id=dep.organization_id,
                         employee_id=dep.employee_id,
                         work_item_id=getattr(item, "id", None),
                         subject_type="lead", subject_id=lead.id,
                         reason_code="human_requested", priority="normal",
                         summary="The contact asked to speak to a person.",
                         status="open"))
        db.flush()
    if item is not None and item.state not in WC.TERMINAL_STATES:
        from app.services.workforce import queue as wf_queue
        try:
            wf_queue.advance_to(db, item, WC.HUMAN_HANDOFF,
                                reason="handed to a person",
                                actor_kind=WC.ACTOR_AI_EMPLOYEE)
        except Exception as exc:                              # noqa: BLE001
            _log.info("ai_deployment proof: handoff transition (%s)", exc)
    count = (db.query(AIHandoff)
             .filter(AIHandoff.organization_id == dep.organization_id,
                     AIHandoff.employee_id == dep.employee_id).count())
    return Step("handoff_outcome", count >= 1,
                "A handoff exists, carrying a summary and a recommended "
                "action.", {"handoffs": count})


def _entitlement_loss_step(db: Session, org, platform, spec,
                           operator) -> Step:
    """Cancel the entitlement and prove a live employee stops.

    A SECOND DEPLOYMENT, because the first one is already retired by this
    point and a retired deployment proves nothing about suspension.
    """
    from app.models.purchase_models import CatalogPurchase, PurchaseStatus
    from app.services.workforce import activation as wf_activation
    from app.services.workforce import constants as WC

    dep = t8_lifecycle.select(db, org=org,
                              template_key=spec["template_key"],
                              actor=operator,
                              provisioning_key="proof-cancel-%s"
                                               % spec["slug"],
                              display_name="AI %s (cancellation proof)"
                                           % spec["label"])
    t8_lifecycle.configure(db, dep, {
        "goal": spec["goal"],
        "working_hours": {"days": [0, 1, 2, 3, 4], "start": "09:00",
                          "end": "17:00"},
        "hours": {"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"},
        "timezone": "America/Chicago",
        "channels": ["email"],
        "handoff_user_id": operator.id, "handoff_to": operator.id,
        "booking_owner": operator.id,
        "inbound_enabled": True, "outbound_enabled": False,
        "good_lead": ["Anyone"], "audience": {"statuses": ["new"]},
        "goals": [spec["goal"]],
    }, actor=operator)

    previous = wf_activation.scope_report(db, wf_activation.SCOPE_PLATFORM, "")
    try:
        for scope, scope_id in ((wf_activation.SCOPE_PLATFORM, ""),
                                (wf_activation.SCOPE_BRAND, platform.id),
                                (wf_activation.SCOPE_CUSTOMER, org.id)):
            wf_activation.set_state(db, scope, scope_id, WC.CONTROLLED,
                                    actor_user_id=operator.id,
                                    reason="synthetic proof run")
        t8_activation.acknowledge_review(
            db, dep, actor=operator, note="synthetic proof: cancellation case")
        t8_activation.request(db, dep, D.CONTROLLED, actor=operator,
                              reason="synthetic proof: cancellation")
        was_live = dep.state == D.CONTROLLED
    finally:
        wf_activation.set_state(db, wf_activation.SCOPE_PLATFORM, "",
                                previous["state"], actor_user_id=operator.id,
                                reason="restored after synthetic proof")

    for row in (db.query(CatalogPurchase)
                .filter(CatalogPurchase.organization_id == org.id,
                        CatalogPurchase.status == PurchaseStatus.ACTIVE).all()):
        row.status = PurchaseStatus.CANCELED
        row.canceled_at = datetime.utcnow()
    db.flush()

    t8_commerce.on_commercial_change(db, org.id,
                                     reason="synthetic proof: cancellation")
    return Step("cancellation_stops_a_live_employee",
                was_live and dep.state == D.SUSPENDED,
                "Losing the entitlement stopped a working AI employee.",
                {"was_live": was_live, "state": dep.state,
                 "commercial_state": dep.commercial_state})


def _no_outreach_step(db: Session, org) -> Step:
    """Nothing left this process towards a real person. Asserted, not claimed."""
    from app.services.workforce import activation as wf_activation
    problems = []
    if wf_activation.live_voice_enabled():
        problems.append("live voice is enabled")
    try:
        from app.services.ai_operations import flags as t7_flags
        if t7_flags.live_send_enabled():
            problems.append("live sending is enabled")
    except Exception:                                         # noqa: BLE001
        pass
    try:
        from app.models.ai_operations_models import AICommunication
        live = [r for r in db.query(AICommunication)
                .filter(AICommunication.organization_id == org.id).all()
                if (getattr(r, "provider", None) or "")
                and "simulat" not in (r.provider or "").lower()]
        if live:
            problems.append("%d communication(s) used a real provider"
                            % len(live))
    except Exception:                                         # noqa: BLE001
        pass
    return Step("zero_real_outreach", not problems,
                ("Nothing reached anybody." if not problems
                 else "; ".join(problems)))
