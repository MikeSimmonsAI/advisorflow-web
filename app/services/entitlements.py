"""FEATURE ENTITLEMENT — enforced on the server, where it counts.

`Organization.enabled_features` already existed: a JSON allow-list, written by
`PATCH /org-settings/features`, read by exactly two places — the org settings
payload and the org list. Nothing in the backend consulted it before doing work.
Enforcement lived in `Layout.jsx` and `Overview.jsx`, which decide whether to
draw a nav item.

That is not entitlement, it is decoration. main.py already says so about a
different feature, in a comment that turned out to apply here too:

    "Hiding the nav item is not access control - the Lead Scraper already
     taught us that."

So this module adds the missing half. `require_feature("campaigns")` is a real
dependency that returns 402 when a customer is not entitled to the thing they
just asked for, whatever the browser chose to render.

THE TWO-KEY RULE. The mission's wording is "Organization feature entitlement and
user permission remain separate. Access requires BOTH." Those are genuinely
different questions - "did this customer buy campaigns" and "is this particular
advisor allowed to send one" - and collapsing them is how a customer who paid
for a feature finds every one of their staff able to use it. This file answers
only the first. Role guards (`require_admin` and friends) answer the second, and
both must pass.

NULL IS NOT EMPTY. An organization whose `enabled_features` is NULL predates
entitlement and keeps everything, exactly as the column's own comment says.
Customers created by `customer_provisioning.create_customer` start with `[]` -
an explicit empty allow-list - so new customers start with nothing switched on
rather than everything. Reading NULL as "deny" would have switched off every
existing customer the moment this shipped.
"""

import json
from typing import Dict, List, Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.models import Organization, User

# The registry. A feature that is not here cannot be granted, which stops the
# allow-list quietly filling with typos that grant nothing and are never noticed.
FEATURES: Dict[str, str] = {
    "leads":        "Lead management and the leads list",
    "campaigns":    "Bulk campaigns and campaign builder",
    "cadences":     "Automated multi-touch cadences",
    "sms":          "Outbound SMS",
    "email":        "Outbound email",
    "voice":        "Outbound and inbound voice / AI calling",
    "crm":          "Native CRM contacts and pipeline",
    "booking":      "Public booking pages and appointment scheduling",
    "calendar":     "Calendar connections and availability",
    "reports":      "Reporting and analytics",
    "imports":      "CSV and bulk data import",
    "ai_assist":    "AI drafting, classification and suggestions",
    "compliance":   "Suppression lists and DNC handling",
    "case_files":   "Case files / family file review",
}

ALL_FEATURE_KEYS = tuple(sorted(FEATURES))


def normalize_keys(keys: Optional[List[str]]) -> List[str]:
    if keys is None:
        return []
    cleaned, unknown = [], []
    for k in keys:
        k = (k or "").strip().lower()
        if not k:
            continue
        if k not in FEATURES:
            unknown.append(k)
        elif k not in cleaned:
            cleaned.append(k)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail="Unknown feature key(s): %s. Valid keys: %s"
                   % (", ".join(sorted(set(unknown))), ", ".join(ALL_FEATURE_KEYS)))
    return sorted(cleaned)


def enabled_for(org: Optional[Organization]) -> Optional[List[str]]:
    """The org's allow-list, or None meaning 'everything' (legacy orgs)."""
    if org is None:
        return []
    raw = getattr(org, "enabled_features", None)
    if raw is None:
        return None            # legacy: all features
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        # A corrupt allow-list is not a licence to enable everything.
        return []
    return [k for k in val if isinstance(k, str)] if isinstance(val, list) else []


def org_has_feature(org: Optional[Organization], key: str) -> bool:
    allowed = enabled_for(org)
    if allowed is None:
        return True
    return key in allowed


def set_features(db: Session, org: Organization, actor: User,
                 keys: Optional[List[str]]) -> List[str]:
    """Replace an organization's allow-list. None restores the legacy 'all'."""
    from app.routers.audit_log_router import log_action

    before = enabled_for(org)
    if keys is None:
        org.enabled_features = None
        after = None
    else:
        after = normalize_keys(keys)
        org.enabled_features = json.dumps(after)
    db.flush()
    log_action(
        db, org.id, actor.id,
        action="customer.features_set", target_type="organization", target_id=org.id,
        platform_id=org.platform_id,
        before={"enabled_features": before}, after={"enabled_features": after},
        commit=False,
    )
    return after if after is not None else list(ALL_FEATURE_KEYS)


def feature_report(org: Optional[Organization]) -> Dict:
    allowed = enabled_for(org)
    return {
        "mode": "all" if allowed is None else "allow_list",
        "enabled": list(ALL_FEATURE_KEYS) if allowed is None else allowed,
        "available": [{"key": k, "label": FEATURES[k],
                       "enabled": True if allowed is None else (k in allowed)}
                      for k in ALL_FEATURE_KEYS],
        "enabled_count": len(ALL_FEATURE_KEYS) if allowed is None else len(allowed),
    }


def require_feature(key: str):
    """Dependency factory: this route needs the customer to be entitled to `key`.

    god_admin passes — the owner operating inside a customer is not the customer
    and must be able to configure a feature that is currently switched off.
    Everyone else is checked against the stored allow-list, not against what
    their browser decided to render.
    """
    if key not in FEATURES:
        raise RuntimeError("require_feature(%r): not a registered feature key" % key)

    def _dep(user: User = Depends(get_current_user),
             db: Session = Depends(get_db)) -> User:
        if getattr(user, "role", None) == "god_admin":
            return user
        org_id = getattr(user, "organization_id", None)
        if org_id is None:
            # Brand-sales staff and other non-tenant identities. They have no
            # entitlement because they have no tenant; require_tenant_user is
            # the guard that explains that properly.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This is a customer workspace feature and your account has no "
                       "customer organization.")
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not org_has_feature(org, key):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="This organization is not enabled for '%s' (%s). An operator can "
                       "enable it in the customer's Features settings."
                       % (key, FEATURES[key]))
        return user

    return _dep
