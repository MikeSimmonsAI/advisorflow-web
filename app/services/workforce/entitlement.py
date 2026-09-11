"""THE COMMERCIAL BOUNDARY — and nothing on the other side of it.

T2 OWNS BILLING, CATALOGUE AND COMMERCE. This module does not price anything,
does not create a Stripe object, does not decide what an AI employee costs and
does not invent a plan. Section 28 is explicit and this file is deliberately
small because of it: it ASKS the catalogue a question and reports the answer.

THE QUESTION IT ASKS. "Has this customer bought the thing whose entitlement key
is X?" The catalogue already answers that — `BrandCatalogItem.entitlement_key`
exists and `CatalogPurchase` records what was actually bought, with
`PurchaseStatus.LIVE` naming the states that the purchase model's own header
says count towards entitlements. Re-deriving any of that here would be a second
opinion about whether somebody has paid, and two opinions about money is one
too many.

NO PRICES ARE INVENTED, ANYWHERE. No AI employee in this build carries an
amount, and the seeder deliberately creates no catalogue item: an entitlement
key with no catalogue item behind it resolves to "not sold yet", which is both
true and the safe answer. When a price is eventually approved, a catalogue item
carrying that key is all that has to exist — no code here changes.

WHEN IT BITES. Enforced for any tool that REACHES A REAL PERSON OR PROVIDER,
and only when the activation stage is one where such a tool could run at all.
Simulation and shadow evaluate the same check and RECORD the answer without
enforcing it, because requiring a customer to buy something before their own
demonstration can be simulated is a commercial decision nobody made — and
because in those stages nothing reaches anybody regardless.

Since no AI employee has an approved price, nothing is purchasable, so nothing
resolves to entitled, so no executing tool can run for any real customer in
this deployment. That is the dark launch expressed as a property rather than
as a promise.
"""

import logging
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.models.models import Organization
from app.services.workforce import constants as C

_log = logging.getLogger(__name__)


def _live_statuses():
    from app.models.purchase_models import PurchaseStatus
    return tuple(PurchaseStatus.LIVE)


def entitlement_state(db: Session, organization_id: str,
                      entitlement_key: Optional[str]) -> Dict:
    """Has this customer bought the thing this employee is sold as?

    Returns a dict rather than a bool so a screen can explain the answer and a
    tool execution row can record it. `catalogue_present` distinguishes the two
    "no" cases that matter: the brand has never listed this for sale, versus
    it is listed and this customer has not bought it.
    """
    if not entitlement_key:
        return {"required": False, "satisfied": True,
                "entitlement_key": None, "catalogue_present": False,
                "detail": "This capability carries no commercial entitlement."}

    try:
        from app.models.catalog_models import BrandCatalogItem
        from app.models.purchase_models import CatalogPurchase
    except Exception as exc:                                    # noqa: BLE001
        # THE CATALOGUE INTERFACE IS NOT ON THIS BRANCH YET.
        #
        # Section 65: a boundary that cannot be integrated is a boundary that
        # stays closed, not one that is assumed open. Reporting "not satisfied"
        # leaves commercial activation disabled, which is the instruction.
        _log.info("workforce entitlement: catalogue models unavailable (%s)", exc)
        return {"required": True, "satisfied": False,
                "entitlement_key": entitlement_key, "catalogue_present": False,
                "detail": "The platform catalogue is not available in this "
                          "build, so no commercial entitlement can be "
                          "confirmed."}

    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    platform_id = getattr(org, "platform_id", None)

    item_q = (db.query(BrandCatalogItem)
              .filter(BrandCatalogItem.entitlement_key == entitlement_key,
                      BrandCatalogItem.is_active.is_(True)))
    if platform_id:
        item_q = item_q.filter(BrandCatalogItem.platform_id == platform_id)
    items = item_q.all()
    if not items:
        return {"required": True, "satisfied": False,
                "entitlement_key": entitlement_key, "catalogue_present": False,
                "detail": "No catalogue item is published for '%s', so this "
                          "employee is not commercially available yet."
                          % entitlement_key}

    item_ids = [i.id for i in items]
    purchase = (db.query(CatalogPurchase)
                .filter(CatalogPurchase.organization_id == organization_id,
                        CatalogPurchase.catalog_item_id.in_(item_ids),
                        CatalogPurchase.status.in_(_live_statuses()))
                .first())
    if purchase is None:
        return {"required": True, "satisfied": False,
                "entitlement_key": entitlement_key, "catalogue_present": True,
                "detail": "This organization has not purchased '%s'."
                          % (items[0].name or entitlement_key)}

    return {"required": True, "satisfied": True,
            "entitlement_key": entitlement_key, "catalogue_present": True,
            "purchase_id": purchase.id,
            "detail": "Purchased: %s" % (purchase.item_name or entitlement_key)}


def feature_state(db: Session, organization_id: str,
                  feature_key: Optional[str]) -> Dict:
    """Is the customer's FEATURE flag on for this capability?

    A different question from entitlement, answered by the platform's existing
    allow-list (app/services/entitlements.py). An organization whose `sms`
    feature is off must not have an AI employee sending SMS whatever anybody
    bought — and the direction of that gate is the safe one, so it is enforced
    in every stage, simulation included.
    """
    if not feature_key:
        return {"required": False, "satisfied": True, "feature": None,
                "detail": "No customer feature is required."}
    from app.services import entitlements as platform_entitlements
    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    ok = platform_entitlements.org_has_feature(org, feature_key)
    return {
        "required": True, "satisfied": bool(ok), "feature": feature_key,
        "detail": ("Feature '%s' is enabled." % feature_key) if ok else
                  ("This organization is not enabled for '%s' (%s)."
                   % (feature_key,
                      platform_entitlements.FEATURES.get(feature_key, feature_key))),
    }


def check(db: Session, organization_id: str, *, entitlement_key: Optional[str],
          feature_key: Optional[str], reaches_outside: bool,
          activation_state: str) -> Dict:
    """The combined commercial answer for one tool call.

    `enforced` says whether a failure here should REFUSE the call in this
    stage. The distinction is recorded on the tool execution row so a later
    reader can tell an unenforced observation from an enforced refusal.
    """
    ent = entitlement_state(db, organization_id, entitlement_key)
    feat = feature_state(db, organization_id, feature_key)

    enforced_entitlement = bool(reaches_outside
                                and activation_state in C.EXECUTING_STAGES)

    denial_code = None
    denial_reason = None
    # Feature first: it is enforced in every stage, so it is the more absolute
    # of the two and should be the reason a caller is told about.
    if not feat["satisfied"]:
        denial_code = C.DENY_FEATURE_OFF
        denial_reason = feat["detail"]
    elif enforced_entitlement and not ent["satisfied"]:
        denial_code = C.DENY_NOT_ENTITLED
        denial_reason = ent["detail"]

    return {
        "entitlement": ent,
        "feature": feat,
        "entitlement_enforced": enforced_entitlement,
        "allowed": denial_code is None,
        "denial_code": denial_code,
        "denial_reason": denial_reason,
    }
