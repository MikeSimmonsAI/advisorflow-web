"""ADMINISTRATIVE DELEGATION Ã¢â‚¬â€ the two gates, beside the entitlement they extend.

THREE STATES, NOT ONE. This module exists because the codebase had one boolean
where the business has three, and collapsing them is how a funeral home's office
manager ends up able to re-register the company's A2P brand:

    FEATURE ENABLED          the customer may USE the service.
                             -> Organization.enabled_features
                             -> entitlements.require_feature()          402

    SELF-MANAGEMENT ALLOWED  the ORGANIZATION may be permitted to ADMINISTER
                             the infrastructure behind that service at all.
                             -> Organization.delegated_capabilities
                             -> GATE 1, below                           403

    ADMIN GRANT              THIS named administrator actually holds that
                             administrative capability.
                             -> user_capability_grants
                             -> GATE 2, below                           403

Restland is the worked example. `sms` is enabled, so advisors send messages all
day. `twilio_credentials` is NOT in delegated_capabilities, so nobody inside
Restland can see or change the Twilio account behind those messages - not the
office manager, not a super_admin. In an organization where God HAS delegated
it, Jerome holds the grant and Susan does not, so Jerome administers it and
Susan gets the same 403 an advisor would.

USING A FEATURE AND CONFIGURING THE INFRASTRUCTURE FOR IT ARE DIFFERENT
PERMISSIONS. That sentence is the whole module.

WHY NOT `Membership`
--------------------
`Membership` was the obvious reuse candidate and it is the wrong shape. Its
column is `role`, its vocabulary is documented as sales_manager | sales_rep, its
unique constraint is (user, scope_type, scope_id, ROLE), and `sales_access`
reads that column to decide who manages a sales team. Storing
"manage_twilio" in a column called `role` would mean a capability grant is one
careless query away from being read as a sales role. A role is who you ARE; a
capability is what you MAY DO. So this is a separate, deliberately tiny table -
which is not a second authorization framework: it is the storage for GATE 2 of
the SAME framework, resolved by a dependency written to mirror
`entitlements.require_feature` line for line.

WHY NOT A ROLE CHECK
--------------------
`require_admin` passes org_admin, super_admin and god_admin. Every sensitive
route in the audit used it, which meant holding org_admin WAS holding every
infrastructure permission in the product. Role is now only an ELIGIBILITY
filter: an advisor can never receive an infrastructure capability, and an
eligible admin still holds nothing until God says so, twice.
"""

import json
import logging
from typing import Dict, List, Optional

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.models import Organization, User, UserCapabilityGrant
from app.services import entitlements

_log = logging.getLogger(__name__)


class Capability:
    """One administrative capability.

    `requires_feature` is the link back to GATE 0: administering the Twilio
    account of a customer who is not entitled to `sms` is not a permission
    question, it is a nonsense question, and it answers 402 rather than 403.

    `delegable` is whether an ORGANIZATION may ever be allowed to self-manage
    this. Anything platform-wide - master billing, pricing, brand creation,
    platform secrets - is False and can therefore never be delegated by any
    route, however the God UI is used. That is a property of the capability,
    not a setting somebody can flip by accident.
    """

    __slots__ = ("key", "label", "requires_feature", "delegable", "why")

    def __init__(self, key, label, requires_feature=None, delegable=True, why=""):
        self.key = key
        self.label = label
        self.requires_feature = requires_feature
        self.delegable = delegable
        self.why = why


def _cap(*a, **kw):
    c = Capability(*a, **kw)
    return c.key, c


CAPABILITIES: Dict[str, Capability] = dict([
    # Ã¢â€â‚¬Ã¢â€â‚¬ Delegable: a customer MAY be trusted with these, but never by default Ã¢â€â‚¬Ã¢â€â‚¬
    _cap("twilio_credentials",
         "Twilio account credentials (Account SID and Auth Token)",
         requires_feature="sms", delegable=True,
         why="The credentials that bill and send. An organization that manages "
             "its own Twilio account needs these; one on the platform's account "
             "must never see them."),
    _cap("twilio_numbers",
         "Sending number assignment and provisioning",
         requires_feature="sms", delegable=True,
         why="Which number a person sends from. Reassigning a number moves "
             "carrier reputation and inbound replies with it."),
    _cap("a2p_10dlc",
         "A2P 10DLC brand and campaign registration",
         requires_feature="sms", delegable=True,
         why="An A2P brand is bound permanently to the Twilio account that "
             "created it. Registering against the wrong account is the one "
             "messaging mistake that is genuinely painful to unwind."),
    _cap("platform_health",
         "Platform and system health",
         requires_feature=None, delegable=True,
         why="Named 'God-only by default' rather than never-delegable, so an "
             "organization can be given its own health view without any change "
             "here."),

    # Ã¢â€â‚¬Ã¢â€â‚¬ Never delegable: platform-wide, God only, full stop Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    _cap("platform_billing",
         "Master billing across every customer",
         requires_feature=None, delegable=False,
         why="Spans customers, so no single customer can be entitled to it."),
    _cap("pricing_admin",
         "Package and pricing administration",
         requires_feature=None, delegable=False,
         why="Prices are the platform's commercial model, not a tenant setting."),
    _cap("brand_admin",
         "Brand creation, deletion and cross-brand administration",
         requires_feature=None, delegable=False,
         why="A brand contains customers. Nothing inside one customer may "
             "administer the container."),
    _cap("platform_secrets",
         "Platform secrets and shared integration credentials",
         requires_feature=None, delegable=False,
         why="Shared across every tenant by definition."),

    # Ã¢â€â‚¬Ã¢â€â‚¬ Lead Import Intelligence (feature capabilities Ã¢â‚¬â€ role-resolved) Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    _cap("import_leads",
         "Upload and process CSV / Excel / Google Contacts lead imports",
         requires_feature=None, delegable=True,
         why="Org admins auto-qualify by role; advisors or managers need an "
             "explicit grant to upload on the org's behalf."),
    _cap("import_review",
         "Review staged import rows and set accept / merge / reject decisions",
         requires_feature=None, delegable=True,
         why="Anyone with import_review may triage rows but cannot commit them."),
    _cap("import_commit",
         "Commit reviewed import rows to live leads",
         requires_feature=None, delegable=True,
         why="Separate from review so a manager can review without having the "
             "power to write to the live database."),
    _cap("import_admin",
         "Archive and manage import batches",
         requires_feature=None, delegable=True,
         why="Administrative housekeeping Ã¢â‚¬â€ archive batches, purge old staging "
             "data. Separate from commit so batch management does not require "
             "commit authority."),
])

CAPABILITIES["import_stage"] = CAPABILITIES["import_leads"]
CAPABILITIES["lead_import_stage"] = CAPABILITIES["import_leads"]
CAPABILITIES["import_manage"] = CAPABILITIES["import_admin"]

ALL_CAPABILITY_KEYS = tuple(sorted(CAPABILITIES))
DELEGABLE_KEYS = tuple(sorted(k for k, c in CAPABILITIES.items() if c.delegable))

# WHO MAY EVER HOLD AN INFRASTRUCTURE CAPABILITY.
#
# This is an eligibility filter, never a grant. An advisor is excluded here so
# that a stray grant row - written by a bug, a bad import, or a future endpoint
# nobody has reviewed yet - still cannot give an advisor the Twilio account.
# god_admin is absent on purpose: the owner never reaches the gates at all.
ELIGIBLE_ADMIN_ROLES = ("org_admin", "super_admin")


def is_god(user) -> bool:
    return getattr(user, "role", None) == "god_admin"


def normalize_capability_keys(keys: Optional[List[str]],
                              delegable_only: bool = False) -> List[str]:
    """Clean and validate a list of capability keys, or 400.

    Mirrors `entitlements.normalize_keys` deliberately, including rejecting an
    unregistered key rather than storing it: an allow-list that quietly fills
    with typos grants nothing and nobody ever notices.
    """
    if keys is None:
        return []
    cleaned, unknown, undelegable = [], [], []
    for k in keys:
        k = (k or "").strip().lower()
        if not k:
            continue
        if k not in CAPABILITIES:
            unknown.append(k)
        elif delegable_only and not CAPABILITIES[k].delegable:
            undelegable.append(k)
        elif k not in cleaned:
            cleaned.append(k)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail="Unknown capability key(s): %s. Valid keys: %s"
                   % (", ".join(sorted(set(unknown))), ", ".join(ALL_CAPABILITY_KEYS)))
    if undelegable:
        raise HTTPException(
            status_code=400,
            detail="These capabilities are platform-wide and can never be "
                   "delegated to an organization: %s"
                   % ", ".join(sorted(set(undelegable))))
    return sorted(cleaned)


# Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
# GATE 1 Ã¢â‚¬â€ ORGANIZATION SELF-MANAGEMENT ENTITLEMENT
# Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

def self_managed_by(org: Optional[Organization]) -> List[str]:
    """Capabilities this organization is permitted to administer itself.

    NULL READS AS EMPTY HERE, and that is the opposite of `enabled_features` on
    purpose. An org whose `enabled_features` is NULL predates entitlement and
    keeps everything, because switching working customers off overnight would
    have been the wrong failure. A `delegated_capabilities` of NULL means God
    has never delegated anything, and the safe reading of "never said" for
    infrastructure administration is NO. Legacy customers therefore arrive at
    this gate closed, which is the intended default for every one of them.
    """
    if org is None:
        return []
    raw = getattr(org, "delegated_capabilities", None)
    if raw is None:
        return []
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        # A corrupt list is not a licence to self-manage the Twilio account.
        return []
    if not isinstance(val, list):
        return []
    return [k for k in val if isinstance(k, str) and k in CAPABILITIES]


def org_may_self_manage(org: Optional[Organization], key: str) -> bool:
    cap = CAPABILITIES.get(key)
    if cap is None or not cap.delegable:
        return False
    return key in self_managed_by(org)


def set_self_management(db: Session, org: Organization, actor: User,
                        keys: Optional[List[str]]) -> List[str]:
    """Replace an organization's self-management allow-list. Audited."""
    from app.routers.audit_log_router import log_action

    before = self_managed_by(org)
    after = normalize_capability_keys(keys, delegable_only=True)
    org.delegated_capabilities = json.dumps(after)
    db.flush()
    log_action(
        db, org.id, actor.id,
        action="customer.self_management_set", target_type="organization",
        target_id=org.id, platform_id=getattr(org, "platform_id", None),
        before={"delegated_capabilities": before},
        after={"delegated_capabilities": after},
        commit=False,
    )
    return after


# Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
# GATE 2 Ã¢â‚¬â€ SPECIFIC ADMIN DELEGATION
# Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

def grants_for(db: Session, user_id: str, org_id: str) -> List[str]:
    """Capability keys actively granted to this user INSIDE this organization.

    Scoped by organization as well as by user because the same person can be an
    admin in more than one customer over time, and a grant made for one is not a
    statement about another.
    """
    if not user_id or not org_id:
        return []
    rows = (db.query(UserCapabilityGrant)
            .filter(UserCapabilityGrant.user_id == user_id,
                    UserCapabilityGrant.organization_id == org_id,
                    UserCapabilityGrant.is_active.is_(True))
            .all())
    return sorted({r.capability for r in rows if r.capability in CAPABILITIES})


def user_has_grant(db: Session, user_id: str, org_id: str, key: str) -> bool:
    return key in grants_for(db, user_id, org_id)


def set_user_grants(db: Session, target: User, org: Organization, actor: User,
                    keys: Optional[List[str]]) -> List[str]:
    """Replace one administrator's capability grants inside one organization.

    REFUSES AN INELIGIBLE TARGET rather than writing a row that the gate would
    then ignore. A grant sitting in the table that can never take effect is
    worse than no grant: the God UI would show Susan as an authorized
    administrator while every request she made was refused.
    """
    from app.routers.audit_log_router import log_action

    if is_god(target):
        raise HTTPException(
            status_code=400,
            detail="The platform owner already holds every capability and is "
                   "not granted them per-organization.")
    if getattr(target, "role", None) not in ELIGIBLE_ADMIN_ROLES:
        raise HTTPException(
            status_code=400,
            detail="%s is a %s. Infrastructure administration can only be "
                   "granted to an organization administrator (%s)."
                   % (target.email, getattr(target, "role", "user"),
                      " or ".join(ELIGIBLE_ADMIN_ROLES)))

    before = grants_for(db, target.id, org.id)
    after = normalize_capability_keys(keys)

    existing = (db.query(UserCapabilityGrant)
                .filter(UserCapabilityGrant.user_id == target.id,
                        UserCapabilityGrant.organization_id == org.id)
                .all())
    by_key = {r.capability: r for r in existing}

    for key in after:
        row = by_key.get(key)
        if row is None:
            db.add(UserCapabilityGrant(
                user_id=target.id, organization_id=org.id, capability=key,
                is_active=True, granted_by=actor.id))
        else:
            row.is_active = True
            row.granted_by = actor.id
    for key, row in by_key.items():
        if key not in after:
            # Deactivated, never deleted: who used to hold the Twilio account is
            # exactly the question an incident asks afterwards.
            row.is_active = False

    db.flush()
    log_action(
        db, org.id, actor.id,
        action="customer.admin_capabilities_set", target_type="user",
        target_id=target.id, platform_id=getattr(org, "platform_id", None),
        before={"capabilities": before}, after={"capabilities": after},
        commit=False,
    )
    return after


# Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
# RESOLUTION Ã¢â‚¬â€ both gates, in one place, in one order
# Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

class Decision:
    """Why a capability was allowed or refused. Returned rather than raised so
    the God UI and the gate script can both ask the question without provoking
    an HTTP error, and so the answer shown on screen is produced by the SAME
    code that guards the route. A screen that computes its own version of the
    rule is a screen that will eventually disagree with the server."""

    __slots__ = ("allowed", "reason", "status", "stage")

    def __init__(self, allowed, reason="", status=403, stage=""):
        self.allowed = allowed
        self.reason = reason
        self.status = status
        self.stage = stage

    def __bool__(self):
        return self.allowed


def resolve(db: Session, user: User, org: Optional[Organization],
            key: str) -> Decision:
    """THE decision. Order is fixed and every step can only DENY.

        authenticated  -> get_current_user already refused otherwise
        god            -> ALLOW, both gates bypassed
        organization   -> must be standing inside one
        feature        -> the customer must own the service being administered
        role           -> only an eligible admin may hold infrastructure at all
        GATE 1         -> the organization must be permitted to self-manage it
        GATE 2         -> this administrator must actually hold it
        ALLOW

    Feature comes before role deliberately. "Your organization does not have
    voice" is a truer answer than "you are not an administrator" for someone who
    is in fact an administrator of a customer that never bought voice.
    """
    cap = CAPABILITIES.get(key)
    if cap is None:
        raise RuntimeError("resolve(): %r is not a registered capability" % key)

    if is_god(user):
        return Decision(True, "platform owner", 200, "god")

    if org is None:
        return Decision(
            False, "This is a customer administration capability and your "
                   "account is not inside a customer organization.",
            403, "organization")

    if cap.requires_feature and not entitlements.org_has_feature(
            org, cap.requires_feature):
        return Decision(
            False, "%s is not enabled for this organization, so there is "
                   "nothing to administer under '%s'."
                   % (cap.requires_feature, cap.label),
            402, "feature")

    if getattr(user, "role", None) not in ELIGIBLE_ADMIN_ROLES:
        return Decision(
            False, "%s is administered by an organization administrator, not "
                   "by every user of the feature." % cap.label,
            403, "role")

    if not cap.delegable:
        return Decision(
            False, "%s is administered by AdvisorFlow and is not delegated to "
                   "customer organizations." % cap.label,
            403, "gate1")

    if not org_may_self_manage(org, key):
        return Decision(
            False, "%s is currently administered by AdvisorFlow for this "
                   "organization. Contact support to request self-management."
                   % cap.label,
            403, "gate1")

    if not user_has_grant(db, user.id, org.id, key):
        return Decision(
            False, "Your organization may manage %s, but you are not one of "
                   "its authorized administrators for it." % cap.label,
            403, "gate2")

    return Decision(True, "granted", 200, "allow")


def require_capability(key: str):
    """Dependency factory. Mirrors `entitlements.require_feature` exactly.

    Applied per route rather than at include time, because these capabilities
    guard a handful of endpoints scattered across routers whose other endpoints
    are ordinary customer work - `/org-settings/twilio` is infrastructure,
    `/org-settings/branding` is not, and they live in the same file.
    """
    if key not in CAPABILITIES:
        raise RuntimeError("require_capability(%r): not a registered capability" % key)

    def _dep(user: User = Depends(get_current_user),
             db: Session = Depends(get_db)) -> User:
        org = None
        org_id = getattr(user, "organization_id", None)
        if org_id:
            org = db.query(Organization).filter(Organization.id == org_id).first()
        decision = resolve(db, user, org, key)
        if not decision.allowed:
            _log.info(
                "AUDIT: capability DENIED at stage=%s user=%s role=%s org=%s cap=%s",
                decision.stage, getattr(user, "email", "?"),
                getattr(user, "role", "?"), org_id, key)
            raise HTTPException(status_code=decision.status,
                                detail=decision.reason)
        return user

    return _dep


def require_feature_capability(key: str):
    """Feature-level capability gate for import and similar product features.

    Resolution order differs from require_capability deliberately:
        god_admin    -> ALLOW (always)
        super_admin  -> ALLOW (platform staff, always)
        org_admin    -> ALLOW by role alone (no org-level delegation needed)
        others       -> must hold an explicit UserCapabilityGrant

    An org_admin can import leads on day one without God having to delegate
    'import_leads' to every customer org. That is the right default for a
    product feature that all customers use, not for access to Twilio credentials
    that only some customers self-manage.

    Uses the SAME CAPABILITIES registry and the SAME UserCapabilityGrant table
    as require_capability Ã¢â‚¬â€ this is not a second authorization system. Only the
    resolution path differs.
    """
    if key not in CAPABILITIES:
        raise RuntimeError(
            "require_feature_capability(%r): not a registered capability" % key)

    def _dep(user: User = Depends(get_current_user),
             db: Session = Depends(get_db)) -> User:
        role = getattr(user, "role", None)

        # God and super_admin: always allowed
        if role in ("god_admin", "super_admin"):
            return user

        # org_admin: allowed by role (no explicit grant needed for feature use)
        if role == "org_admin":
            return user

        # Everyone else: must have an explicit UserCapabilityGrant
        org_id = getattr(user, "organization_id", None)
        if org_id and user_has_grant(db, user.id, org_id, key):
            return user

        _log.info(
            "AUDIT: feature-capability DENIED user=%s role=%s cap=%s",
            getattr(user, "email", "?"), role, key)
        cap = CAPABILITIES[key]
        raise HTTPException(
            status_code=403,
            detail="You do not have access to %s. "
                   "Contact your organization administrator." % cap.label)

    return _dep


# Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
# REPORTING Ã¢â‚¬â€ the three states, kept visibly separate
# Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

def administration_report(db: Session, org: Organization) -> Dict:
    """Everything the God control screen needs, as THREE named blocks.

    Deliberately not flattened into one list of booleans. The screen must be
    able to say "Restland can use SMS", "Restland may not manage Twilio" and
    "nobody at Restland is an authorized Twilio administrator" as three separate
    sentences, because they are three separate decisions and inferring one from
    another is how the wrong one gets changed.
    """
    allowed = self_managed_by(org)
    admins = (db.query(User)
              .filter(User.organization_id == org.id,
                      User.role.in_(ELIGIBLE_ADMIN_ROLES))
              .order_by(User.full_name)
              .all())
    everyone = (db.query(User)
                .filter(User.organization_id == org.id)
                .count())

    return {
        # 1. FEATURES ENABLED Ã¢â‚¬â€ what the customer may USE.
        "features": entitlements.feature_report(org),

        # 2. SELF-MANAGEMENT ALLOWED Ã¢â‚¬â€ what the ORGANIZATION may ADMINISTER.
        "self_management": {
            "allowed": allowed,
            "allowed_count": len(allowed),
            "available": [
                {
                    "key": c.key,
                    "label": c.label,
                    "why": c.why,
                    "delegable": c.delegable,
                    "requires_feature": c.requires_feature,
                    "feature_enabled": (
                        True if c.requires_feature is None
                        else entitlements.org_has_feature(org, c.requires_feature)),
                    "allowed": c.key in allowed,
                    # Why the checkbox is disabled, said in words rather than
                    # left for the operator to work out.
                    "blocked_reason": (
                        "Platform-wide Ã¢â‚¬â€ never delegated to a customer."
                        if not c.delegable else
                        ("Requires the '%s' feature, which is not enabled."
                         % c.requires_feature)
                        if c.requires_feature and not entitlements.org_has_feature(
                            org, c.requires_feature)
                        else None),
                }
                for c in (CAPABILITIES[k] for k in ALL_CAPABILITY_KEYS)
            ],
        },

        # 3. AUTHORIZED ADMINISTRATORS Ã¢â‚¬â€ who actually holds each capability.
        "administrators": {
            "eligible_roles": list(ELIGIBLE_ADMIN_ROLES),
            "users_in_org": everyone,
            "eligible_count": len(admins),
            "users": [
                {
                    "id": u.id,
                    "full_name": u.full_name,
                    "email": u.email,
                    "role": u.role,
                    "is_active": bool(u.is_active),
                    "capabilities": grants_for(db, u.id, org.id),
                    # What this person can ACTUALLY do right now, both gates
                    # applied Ã¢â‚¬â€ so the screen never shows a grant that is inert
                    # because the organization is not self-managing.
                    "effective": [
                        k for k in ALL_CAPABILITY_KEYS
                        if resolve(db, u, org, k).allowed
                    ],
                }
                for u in admins
            ],
        },
    }


def my_capabilities(db: Session, user: User) -> Dict:
    """What the CALLER may administer. The frontend asks this instead of
    deciding from `role`, which is how the sidebar came to disagree with the
    server in the first place."""
    if is_god(user):
        return {"is_god": True, "capabilities": list(ALL_CAPABILITY_KEYS)}
    org = None
    org_id = getattr(user, "organization_id", None)
    if org_id:
        org = db.query(Organization).filter(Organization.id == org_id).first()
    return {
        "is_god": False,
        "capabilities": [k for k in ALL_CAPABILITY_KEYS
                         if resolve(db, user, org, k).allowed],
    }
