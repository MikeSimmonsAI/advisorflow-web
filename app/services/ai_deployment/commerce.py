"""THE T2 BOUNDARY - and nothing on the other side of it.

T2 OWNS CATALOGUE, CHECKOUT, BILLING AND ENTITLEMENT. This module does not
price anything, does not create a Stripe object, does not start a checkout,
does not decide what an AI employee costs and does not invent a plan. It asks
T2 questions and reports the answers, and it ASKS FOR A DEPLOYMENT TO BE
SUSPENDED when the answer changes for the worse.

WHAT IT ASKS

    "Is this job offered by this brand, and under what arrangement?"
        -> `ai_offering_terms` (brand configuration) plus `ai_brand_offerings`
           (T6's own brand layer). No prices in either.

    "Is there a catalogue item behind it?"
        -> `brand_catalog.resolve`, brand-scoped, which is the one function
           that answers whether a brand has such an item.

    "Has this customer got it?"
        -> `workforce.entitlement.entitlement_state`, which is already the
           single place that reads `CatalogPurchase` against
           `PurchaseStatus.LIVE`. Re-deriving it here would be a second
           opinion about whether somebody has paid, and two opinions about
           money is one too many.

    "Does their package include it?"
        -> `plan_limits.effective_plan`, the same resolution every capacity
           guard on the platform already uses.

WHY A MIRROR COLUMN EXISTS AT ALL. `ai_employee_deployments.commercial_state`
is written from these answers so a screen can say why an employee stopped
without re-deriving four things, and so a transition can record what was true
when it happened. NOTHING READS THAT COLUMN TO DECIDE WHETHER A TOOL MAY RUN.
That question is answered live, on every single call, by T6's gateway against
T2's own tables - which is why a stale mirror is a cosmetic problem rather than
a security one.

NEVER ACTIVATE BECAUSE CHECKOUT WAS OPENED. `PENDING` is somebody who has not
paid. It is reported, it is never entitlement, and it is not in
`COMMERCIALLY_LIVE`.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.ai_deployment_models import AIDeploymentEvent, AIOfferingTerms
from app.models.models import Organization
from app.services.ai_deployment import constants as D

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SMALL READERS
# ---------------------------------------------------------------------------

def json_list(raw, default=None) -> Optional[List]:
    """Parse a JSON list column. A CORRUPT VALUE IS NOT A LICENCE."""
    if raw is None:
        return default
    if isinstance(raw, (list, tuple)):
        return list(raw)
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        return default
    return list(val) if isinstance(val, list) else default


def json_obj(raw, default=None) -> Dict:
    if raw is None:
        return dict(default or {})
    if isinstance(raw, dict):
        return dict(raw)
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        return dict(default or {})
    return dict(val) if isinstance(val, dict) else dict(default or {})


def terms_for(db: Session, platform_id: Optional[str],
              template_key: str) -> Optional[AIOfferingTerms]:
    """This brand's commercial terms for one job, or None.

    None is a real answer and the common one: a brand that has never configured
    commercial terms for a job is a brand that has not decided to sell it, and
    the catalogue reports it as not offered rather than guessing a default.
    """
    if not platform_id or not template_key:
        return None
    return (db.query(AIOfferingTerms)
            .filter(AIOfferingTerms.platform_id == platform_id,
                    AIOfferingTerms.template_key == template_key)
            .first())


def plan_key_for(db: Session, org: Optional[Organization]) -> Optional[str]:
    """The customer's package key, through the platform's own resolution.

    `plan_limits.effective_plan` is deliberately the source: it already knows
    that `billing_plan_key` and not the pending one decides what applies now,
    and it already falls back the way every capacity guard falls back.
    """
    if org is None:
        return None
    try:
        from app.services import plan_limits
        plan = plan_limits.effective_plan(db, org)
        return getattr(plan, "key", None)
    except Exception as exc:                                  # noqa: BLE001
        _log.info("ai_deployment: could not resolve plan for %s (%s)",
                  getattr(org, "id", None), exc)
        return None


def catalog_item(db: Session, platform_id: Optional[str],
                 item_key: Optional[str]):
    """The T2 catalogue row behind this job, or None. Brand-scoped, always."""
    if not platform_id or not item_key:
        return None
    try:
        from app.services import brand_catalog
        return brand_catalog.resolve(db, platform_id, item_key)
    except Exception as exc:                                  # noqa: BLE001
        _log.info("ai_deployment: catalogue lookup failed for %s (%s)",
                  item_key, exc)
        return None


def _pending_purchase(db: Session, organization_id: str,
                      item_key: Optional[str]):
    """An unpaid checkout for this item, if one is outstanding.

    Reported so a screen can say "awaiting payment" rather than "not
    available", which are opposite facts to the person who just paid.
    """
    if not item_key:
        return None
    try:
        from app.models.purchase_models import CatalogPurchase, PurchaseStatus
        return (db.query(CatalogPurchase)
                .filter(CatalogPurchase.organization_id == organization_id,
                        CatalogPurchase.item_key == item_key,
                        CatalogPurchase.status == PurchaseStatus.PENDING)
                .order_by(CatalogPurchase.created_at.desc())
                .first())
    except Exception as exc:                                  # noqa: BLE001
        _log.info("ai_deployment: pending purchase lookup failed (%s)", exc)
        return None


# ---------------------------------------------------------------------------
# THE ANSWER
# ---------------------------------------------------------------------------

class Offer:
    """Everything commercial about one job for one customer, in one object.

    `state` is the mirror value. `blockers` are sentences for the person who
    has to fix it. `where_to_buy` names the catalogue item and who may sell it,
    because a customer told "not available" with no route to acquiring it is a
    customer who phones support.
    """

    __slots__ = ("template_key", "offering", "terms", "item", "state",
                 "detail", "entitlement", "purchase_id", "plan_key",
                 "blockers", "where_to_buy")

    def __init__(self, template_key, offering=None, terms=None, item=None,
                 state=D.COMM_UNKNOWN, detail="", entitlement=None,
                 purchase_id=None, plan_key=None, blockers=None,
                 where_to_buy=None):
        self.template_key = template_key
        self.offering = offering
        self.terms = terms
        self.item = item
        self.state = state
        self.detail = detail
        self.entitlement = entitlement or {}
        self.purchase_id = purchase_id
        self.plan_key = plan_key
        self.blockers = list(blockers or [])
        self.where_to_buy = where_to_buy

    @property
    def is_live(self) -> bool:
        """May an employee on this arrangement hold a live stage?"""
        return self.state in D.COMMERCIALLY_LIVE

    @property
    def commercial_mode(self) -> str:
        return getattr(self.terms, "commercial_mode", None) or D.MODE_ADDON

    @property
    def catalog_item_key(self) -> Optional[str]:
        return getattr(self.terms, "catalog_item_key", None)

    @property
    def entitlement_key(self) -> Optional[str]:
        """The key T6's own gateway will ask the catalogue about.

        Resolved the same way `workforce.policy` resolves it - the brand
        offering's override first, then the template's - so the commercial
        answer shown on a screen and the one enforced on a tool call are the
        same key. Two spellings here would mean a screen saying "entitled"
        above an employee the gateway refuses.
        """
        from app.services.workforce import registry as wf_registry
        if self.offering is not None and self.offering.entitlement_key:
            return self.offering.entitlement_key
        spec = wf_registry.template(self.template_key)
        return spec.entitlement_key if spec else None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "template_key": self.template_key,
            "commercial_state": self.state,
            "commercial_label": D.COMMERCIAL_LABELS.get(self.state, self.state),
            "commercial_mode": self.commercial_mode,
            "commercial_mode_label": D.MODE_LABELS.get(self.commercial_mode,
                                                       self.commercial_mode),
            "detail": self.detail,
            "entitlement_key": self.entitlement_key,
            "catalog_item_key": self.catalog_item_key,
            "plan_key": self.plan_key,
            "is_live": self.is_live,
            "blockers": list(self.blockers),
            "where_to_buy": self.where_to_buy,
        }


def resolve_offer(db: Session, org: Organization, template_key: str) -> Offer:
    """What this customer's commercial position on one job actually is.

    THE ORDER OF THE QUESTIONS IS THE ANSWER'S QUALITY. "Not offered" outranks
    "not entitled", because a customer cannot buy what the brand does not sell
    and telling them to buy it would be wrong. "Included" outranks a purchase
    lookup, because a package that includes a job makes buying it separately a
    double charge nobody should be invited to make.
    """
    from app.models.workforce_models import AIBrandOffering, AIEmployeeTemplate
    from app.services.workforce import registry as wf_registry

    spec = wf_registry.template(template_key)
    if spec is None:
        return Offer(template_key, state=D.COMM_NOT_OFFERED,
                     detail="No such AI employee job exists on this platform.",
                     blockers=["Unknown job."])

    platform_id = getattr(org, "platform_id", None)
    offering = None
    if platform_id:
        offering = (db.query(AIBrandOffering)
                    .join(AIEmployeeTemplate,
                          AIBrandOffering.template_id == AIEmployeeTemplate.id)
                    .filter(AIBrandOffering.platform_id == platform_id,
                            AIEmployeeTemplate.key == template_key)
                    .first())
    terms = terms_for(db, platform_id, template_key)
    plan_key = plan_key_for(db, org)
    offer = Offer(template_key, offering=offering, terms=terms,
                  plan_key=plan_key)

    # --- Does the brand offer it at all? -----------------------------------
    if offering is None or not offering.is_enabled:
        offer.state = D.COMM_NOT_OFFERED
        offer.detail = ("This AI employee is not currently offered here.")
        offer.blockers.append(
            "The brand has not enabled this job for its customers.")
        return offer
    if terms is None or not terms.is_available:
        offer.state = D.COMM_NOT_OFFERED
        offer.detail = ("This AI employee is offered but has no commercial "
                        "terms configured yet.")
        offer.blockers.append(
            "No commercial terms are configured for this job, so it cannot "
            "be acquired.")
        return offer

    # --- Is the customer's package allowed to hold it? ---------------------
    eligible = json_list(terms.eligible_plan_keys, []) or []
    if eligible and (plan_key or "") not in eligible:
        offer.state = D.COMM_NOT_OFFERED
        offer.detail = ("This AI employee is not available on the current "
                        "package.")
        offer.blockers.append(
            "This job is available on a different package. Changing package "
            "is a conversation with your account manager.")
        return offer

    # --- Included with the package? ----------------------------------------
    included = json_list(terms.included_plan_keys, []) or []
    if plan_key and plan_key in included:
        offer.state = D.COMM_INCLUDED
        offer.detail = "Included with the current package."
        return offer
    if terms.commercial_mode == D.MODE_INCLUDED and not included:
        # CONFIGURED AS INCLUDED AND INCLUDED WITH NOTHING. That is a
        # configuration mistake rather than a free employee, and the safe
        # reading is that nobody has it.
        offer.state = D.COMM_NOT_OFFERED
        offer.detail = ("This AI employee is marked as included with a "
                        "package, and no package has been named.")
        offer.blockers.append(
            "The brand has not said which packages include this job.")
        return offer

    # --- Bought through the catalogue? -------------------------------------
    item = catalog_item(db, platform_id, terms.catalog_item_key)
    offer.item = item
    if item is None:
        offer.state = D.COMM_NOT_OFFERED
        offer.detail = ("This AI employee has no catalogue item behind it "
                        "yet, so it is not commercially available.")
        offer.blockers.append(
            "No catalogue item is published for this job. A price is a brand "
            "decision and nothing here invents one.")
        return offer

    from app.services.workforce import entitlement as wf_entitlement
    ent = wf_entitlement.entitlement_state(db, org.id, offer.entitlement_key)
    offer.entitlement = ent
    offer.where_to_buy = {
        "catalog_item_key": item.key,
        "name": item.name,
        "self_service": bool(item.self_service),
        "seller_assisted": bool(item.seller_assisted),
        "pricing_mode": item.pricing_mode,
    }

    if ent.get("satisfied"):
        offer.state = D.COMM_ENTITLED
        offer.detail = ent.get("detail") or "On the account."
        offer.purchase_id = ent.get("purchase_id")
        return offer

    pending = _pending_purchase(db, org.id, item.key)
    if pending is not None:
        offer.state = D.COMM_PENDING
        offer.purchase_id = pending.id
        offer.detail = ("A checkout for this AI employee is open and has not "
                        "been paid.")
        offer.blockers.append(
            "Opening a checkout is not paying for one. This employee stays "
            "off until the payment arrives.")
        return offer

    offer.state = D.COMM_AVAILABLE
    offer.detail = ent.get("detail") or "Available to add to the account."
    offer.blockers.append("This AI employee has not been added to the account.")
    return offer


# ---------------------------------------------------------------------------
# RECONCILIATION - the direction commerce is allowed to push
# ---------------------------------------------------------------------------
#
# Entitlement arriving NEVER starts anything. Entitlement leaving ALWAYS stops
# it. That asymmetry is the whole of section 12, and it is what makes a webhook
# safe to wire into this: the worst a mistaken reconcile can do is stop an
# employee, and the worst a missed one can do is leave a screen stale, because
# the live gateway is still asking T2 on every call.

def reconcile_deployment(db: Session, deployment, *, reason: str = "",
                         actor_kind: str = D.ACTOR_COMMERCE) -> Dict[str, Any]:
    """Re-read T2's answer for one deployment and act on it if it worsened.

    Returns what it saw and what it did. Never commits - the caller owns the
    transaction, which matters because the most important caller is a webhook
    handler that is already inside one.
    """
    from app.services.ai_deployment import lifecycle

    org = (db.query(Organization)
           .filter(Organization.id == deployment.organization_id).first())
    if org is None:
        return {"deployment_id": deployment.id, "changed": False,
                "detail": "No such organization."}

    before_state = deployment.state
    before_commercial = deployment.commercial_state
    offer = resolve_offer(db, org, deployment.template_key)

    deployment.commercial_state = (
        D.COMM_LAPSED
        if (before_commercial in D.COMMERCIALLY_LIVE
            and offer.state in (D.COMM_AVAILABLE, D.COMM_NOT_OFFERED))
        else offer.state)
    deployment.commercial_detail = (offer.detail or "")[:255] or None
    deployment.commercial_checked_at = datetime.utcnow()
    deployment.entitlement_key = offer.entitlement_key
    deployment.catalog_item_key = offer.catalog_item_key
    if offer.purchase_id:
        deployment.purchase_id = offer.purchase_id
    # FLUSHED BEFORE THE TRANSITION, and that is not tidiness.
    #
    # `lifecycle.transition` moves the row with a conditional UPDATE and then
    # EXPIRES this object so it reloads. On a session with autoflush off - which
    # is how both the application and the test suite configure theirs - the
    # mirror assignments above are still pending in memory at that point, and
    # expiring throws them away. The deployment would then be suspended with a
    # commercial_state that still said `entitled`, which is precisely the
    # screen that sends somebody to support.
    db.flush()

    acted = None
    if deployment.state == D.RETIRED:
        acted = None
    elif not offer.is_live and deployment.state not in (D.SUSPENDED,):
        # THE EMPLOYEE STOPS. Every state that is not already suspended goes to
        # suspended, including the dormant ones: a customer who loses
        # entitlement while still configuring must not be able to finish and
        # switch on.
        lifecycle.transition(
            db, deployment, D.SUSPENDED,
            reason=(reason or deployment.commercial_detail
                    or "Commercial entitlement is no longer live."),
            actor_kind=actor_kind,
            detail={"commercial_state": deployment.commercial_state,
                    "was": before_state})
        lifecycle.stop_operational_work(
            db, deployment,
            reason="entitlement no longer live")
        acted = "suspended"
    elif offer.is_live and deployment.state == D.SUSPENDED:
        # ENTITLEMENT CAME BACK. The deployment becomes eligible again and
        # NOTHING STARTS. It lands at READY (or VALIDATION_REQUIRED when its
        # configuration no longer passes), and a person switches it on.
        target = (D.VALIDATION_REQUIRED
                  if deployment.readiness_state == D.READY_NO else D.READY)
        lifecycle.transition(
            db, deployment, target,
            reason="Entitlement restored. This employee stays off until "
                   "somebody switches it on.",
            actor_kind=actor_kind,
            detail={"commercial_state": deployment.commercial_state})
        acted = "restored_to_%s" % target

    db.flush()
    return {
        "deployment_id": deployment.id,
        "commercial_state": deployment.commercial_state,
        "state": deployment.state,
        "changed": bool(acted) or before_commercial != deployment.commercial_state,
        "action": acted,
        "detail": deployment.commercial_detail,
    }


def reconcile_organization(db: Session, organization_id: str, *,
                           reason: str = "",
                           actor_kind: str = D.ACTOR_COMMERCE) -> Dict[str, Any]:
    """Re-check every non-retired deployment this customer holds.

    NEVER RAISES. The callers are a Stripe webhook, an add-on removal and a
    maintenance sweep, and none of them may fail because an AI employee's
    bookkeeping did. A reconcile that could take down billing would be a worse
    defect than the one it exists to prevent.
    """
    from app.models.ai_deployment_models import AIEmployeeDeployment
    out: List[Dict[str, Any]] = []
    try:
        rows = (db.query(AIEmployeeDeployment)
                .filter(AIEmployeeDeployment.organization_id == organization_id,
                        AIEmployeeDeployment.state != D.RETIRED)
                .all())
        for row in rows:
            out.append(reconcile_deployment(db, row, reason=reason,
                                            actor_kind=actor_kind))
    except Exception as exc:                                  # noqa: BLE001
        _log.warning("ai_deployment: reconcile failed for org %s (%s)",
                     organization_id, exc)
        return {"organization_id": organization_id, "checked": 0,
                "error": str(exc)[:200]}
    return {
        "organization_id": organization_id,
        "checked": len(out),
        "suspended": sum(1 for r in out if r.get("action") == "suspended"),
        "restored": sum(1 for r in out
                        if (r.get("action") or "").startswith("restored")),
        "results": out,
    }


def on_commercial_change(db: Session, organization_id: Optional[str], *,
                         reason: str) -> Dict[str, Any]:
    """THE ONE ENTRY POINT T2 CALLS. Wrapped so it can never fail its caller.

    Wired into subscription cancellation, add-on removal and checkout
    withdrawal. Every one of those already committed its own work before
    calling here, so a failure in this function leaves the commercial record
    correct and only this layer's mirror stale - which the next reconcile, or
    the live gateway, corrects anyway.
    """
    if not organization_id:
        return {"checked": 0}
    try:
        return reconcile_organization(db, organization_id, reason=reason)
    except Exception as exc:                                  # noqa: BLE001
        _log.warning("ai_deployment: commercial-change hook failed (%s)", exc)
        return {"organization_id": organization_id, "checked": 0,
                "error": str(exc)[:200]}


# ---------------------------------------------------------------------------
# EVENTS
# ---------------------------------------------------------------------------

def record_event(db: Session, deployment, *, from_state: Optional[str],
                 to_state: str, reason: Optional[str], actor_kind: str,
                 actor_id: Optional[str] = None,
                 detail: Optional[Dict] = None) -> AIDeploymentEvent:
    """One transition, written down. Called by the lifecycle, never directly.

    Lives here rather than in lifecycle.py so that the commercial and readiness
    snapshots on the row come from one place - an event recorded without them
    is an event that cannot answer "what was true when this happened".
    """
    row = AIDeploymentEvent(
        deployment_id=deployment.id,
        organization_id=deployment.organization_id,
        from_state=from_state, to_state=to_state,
        reason=(reason or "")[:255] or None,
        actor_kind=actor_kind, actor_id=actor_id,
        commercial_state=deployment.commercial_state,
        readiness_state=deployment.readiness_state,
        detail=json.dumps(detail or {})[:8000])
    db.add(row)
    db.flush()
    return row
