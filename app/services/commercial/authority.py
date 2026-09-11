"""WHO MAY CHANGE THE ECONOMICS OF A DEAL.

This module adds no role system. It reads the one the platform already has —
`memberships` scoped to a brand sales org, plus god — and answers a narrower
question: which of the material commercial acts is this caller allowed to
perform on THIS agreement.

THE SEPARATION THAT MATTERS
---------------------------
Three refusals are the point of the file, and each of them is asked for by
name:

  A CUSTOMER ADMIN CANNOT CHANGE PLATFORM ECONOMICS. They may answer the
  business questions their own onboarding puts to them, and nothing else. Not
  a percentage, not a status, not an approval, not another customer's anything.

  A SALESPERSON CANNOT ACTIVATE TERMS THEY ARE NOT AUTHORISED TO GIVE. A rep
  drafts and collects; a manager or a commercial approver approves; activation
  is a separate act again, because "these terms are acceptable" and "this
  arrangement is now in force" are two decisions and a system that fuses them
  cannot record the first without doing the second.

  NOBODY ACTS ACROSS A BOUNDARY. Every capability is resolved against the
  agreement's own brand and organization. A manager of brand A has no standing
  on brand B's agreement, and neither has a customer admin of org A on org B's.

ROLE_COMMERCIAL_APPROVER is a `memberships.role` VALUE, not a new table and not
a new scope. A brand that wants finance sign-off separate from sales
management grants that role on its brand sales org; a brand that does not,
does not, and its managers approve as before.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, status as http_status
from sqlalchemy.orm import Session

from app.models.commercial_models import CommercialAgreement
from app.models.models import User
from app.models.sales_models import (
    ROLE_BRAND_EXECUTIVE, ROLE_SALES_MANAGER, ROLE_SALES_REP, SCOPE_BRAND_SALES_ORG,
    Membership,
)
from app.services.sales_access import is_god, sales_memberships

# A brand's finance / commercial sign-off, expressed in the existing role
# column. Kept here rather than in sales_models so that nothing in the sales
# workspace has to change to support it.
ROLE_COMMERCIAL_APPROVER = "commercial_approver"

# ── capabilities ────────────────────────────────────────────────────────────
CAP_VIEW_INTERNAL       = "view_internal"
CAP_CREATE              = "create_agreement"
CAP_EDIT_TERMS          = "edit_terms"
CAP_EDIT_ECONOMICS      = "edit_economics"       # percentages, parties, money
CAP_ANSWER_CUSTOMER_Q   = "answer_customer_questions"
CAP_APPROVE             = "approve_agreement"
CAP_ACTIVATE            = "activate_agreement"
CAP_SUSPEND             = "suspend_agreement"
CAP_END                 = "end_agreement"
CAP_OVERRIDE_MILESTONE  = "override_milestone"
CAP_RECORD_COLLECTIONS  = "record_collections"
CAP_APPROVE_COLLECTIONS = "approve_collections"
CAP_CALCULATE_SETTLEMENT = "calculate_settlement"
CAP_APPROVE_SETTLEMENT  = "approve_settlement"
CAP_MANAGE_QUESTIONS    = "manage_question_definitions"

CAPABILITIES = (
    CAP_VIEW_INTERNAL, CAP_CREATE, CAP_EDIT_TERMS, CAP_EDIT_ECONOMICS,
    CAP_ANSWER_CUSTOMER_Q, CAP_APPROVE, CAP_ACTIVATE, CAP_SUSPEND, CAP_END,
    CAP_OVERRIDE_MILESTONE, CAP_RECORD_COLLECTIONS, CAP_APPROVE_COLLECTIONS,
    CAP_CALCULATE_SETTLEMENT, CAP_APPROVE_SETTLEMENT, CAP_MANAGE_QUESTIONS,
)

# ── who is what ─────────────────────────────────────────────────────────────
ACTOR_GOD               = "god"
ACTOR_COMMERCIAL_APPROVER = "commercial_approver"
ACTOR_SALES_MANAGER     = "sales_manager"
ACTOR_BRAND_EXECUTIVE   = "brand_executive"
ACTOR_SALES_REP         = "sales_rep"
ACTOR_CUSTOMER_ADMIN    = "customer_admin"
ACTOR_CUSTOMER_USER     = "customer_user"
ACTOR_NONE              = "none"

# Capability grants, per actor kind. Deliberately a table rather than a chain
# of ifs: the answer to "what can a rep do" should be readable in one place by
# somebody who does not write Python.
_GRANTS = {
    ACTOR_GOD: set(CAPABILITIES),
    ACTOR_COMMERCIAL_APPROVER: {
        CAP_VIEW_INTERNAL, CAP_CREATE, CAP_EDIT_TERMS, CAP_EDIT_ECONOMICS,
        CAP_ANSWER_CUSTOMER_Q, CAP_APPROVE, CAP_ACTIVATE, CAP_SUSPEND, CAP_END,
        CAP_OVERRIDE_MILESTONE, CAP_RECORD_COLLECTIONS, CAP_APPROVE_COLLECTIONS,
        CAP_CALCULATE_SETTLEMENT, CAP_APPROVE_SETTLEMENT,
    },
    ACTOR_SALES_MANAGER: {
        CAP_VIEW_INTERNAL, CAP_CREATE, CAP_EDIT_TERMS, CAP_EDIT_ECONOMICS,
        CAP_ANSWER_CUSTOMER_Q, CAP_APPROVE, CAP_ACTIVATE, CAP_SUSPEND, CAP_END,
        CAP_OVERRIDE_MILESTONE, CAP_RECORD_COLLECTIONS, CAP_APPROVE_COLLECTIONS,
        CAP_CALCULATE_SETTLEMENT,
    },
    # An executive reads the brand's commercial position. They do not sign for
    # it — a reporting role that can activate terms is a reporting role that
    # can commit the brand by accident.
    ACTOR_BRAND_EXECUTIVE: {CAP_VIEW_INTERNAL},
    # A rep works the deal: drafts the arrangement, records what the customer
    # tells them, and hands it up. No approval, no activation, and no edit to
    # the economics — the split is the brand's money, not the seller's.
    ACTOR_SALES_REP: {
        CAP_VIEW_INTERNAL, CAP_CREATE, CAP_EDIT_TERMS, CAP_ANSWER_CUSTOMER_Q,
        CAP_RECORD_COLLECTIONS,
    },
    # The customer answers the questions their own onboarding asks them. That
    # is the whole grant.
    ACTOR_CUSTOMER_ADMIN: {CAP_ANSWER_CUSTOMER_Q},
    ACTOR_CUSTOMER_USER: set(),
    ACTOR_NONE: set(),
}

# Roles inside a customer tenant that count as its administrator. These are the
# platform's existing user roles; no new one is introduced.
_CUSTOMER_ADMIN_ROLES = ("admin", "super_admin", "owner", "org_admin")


def _brand_roles(user: User, db: Session, brand_sales_org_id: Optional[str]):
    for m in sales_memberships(user, db):
        if brand_sales_org_id and m.scope_id != brand_sales_org_id:
            continue
        yield m.role


def actor_kind(db: Session, user: Optional[User], *,
               brand_sales_org_id: Optional[str] = None,
               organization_id: Optional[str] = None) -> str:
    """What this caller is, RELATIVE TO THIS AGREEMENT.

    The two scope arguments are not decoration. A sales manager is only a sales
    manager of the brand they are a member of, and a customer admin is only an
    admin of their own organization; passing the agreement's own ids is what
    turns a role into an authorisation.
    """
    if user is None:
        return ACTOR_NONE
    if is_god(user):
        return ACTOR_GOD

    roles = set(_brand_roles(user, db, brand_sales_org_id))
    if ROLE_COMMERCIAL_APPROVER in roles:
        return ACTOR_COMMERCIAL_APPROVER
    if ROLE_SALES_MANAGER in roles:
        return ACTOR_SALES_MANAGER
    if ROLE_BRAND_EXECUTIVE in roles:
        return ACTOR_BRAND_EXECUTIVE
    if ROLE_SALES_REP in roles:
        return ACTOR_SALES_REP

    user_org = getattr(user, "organization_id", None)
    if organization_id and user_org and user_org == organization_id:
        if (getattr(user, "role", None) or "").lower() in _CUSTOMER_ADMIN_ROLES:
            return ACTOR_CUSTOMER_ADMIN
        return ACTOR_CUSTOMER_USER

    return ACTOR_NONE


def capabilities(db: Session, user: Optional[User], *,
                 brand_sales_org_id: Optional[str] = None,
                 organization_id: Optional[str] = None) -> set:
    return set(_GRANTS.get(
        actor_kind(db, user, brand_sales_org_id=brand_sales_org_id,
                   organization_id=organization_id), set()))


def can(db: Session, user: Optional[User], capability: str, *,
        brand_sales_org_id: Optional[str] = None,
        organization_id: Optional[str] = None) -> bool:
    return capability in capabilities(
        db, user, brand_sales_org_id=brand_sales_org_id,
        organization_id=organization_id)


def can_on(db: Session, user: Optional[User], capability: str,
           agreement: CommercialAgreement) -> bool:
    return can(db, user, capability,
               brand_sales_org_id=agreement.brand_sales_org_id,
               organization_id=agreement.organization_id)


_CAPABILITY_WORDS = {
    CAP_EDIT_ECONOMICS:      "change the commercial terms of an agreement",
    CAP_APPROVE:             "approve commercial terms",
    CAP_ACTIVATE:            "activate a commercial agreement",
    CAP_SUSPEND:             "suspend a commercial agreement",
    CAP_END:                 "end a commercial agreement",
    CAP_OVERRIDE_MILESTONE:  "override an onboarding step",
    CAP_APPROVE_COLLECTIONS: "approve collection records",
    CAP_CALCULATE_SETTLEMENT: "calculate a settlement",
    CAP_APPROVE_SETTLEMENT:  "approve a settlement",
    CAP_MANAGE_QUESTIONS:    "change commercial question definitions",
}


def assert_can(db: Session, user: Optional[User], capability: str, *,
               brand_sales_org_id: Optional[str] = None,
               organization_id: Optional[str] = None) -> None:
    if can(db, user, capability, brand_sales_org_id=brand_sales_org_id,
           organization_id=organization_id):
        return
    raise HTTPException(
        status_code=http_status.HTTP_403_FORBIDDEN,
        detail="Your account is not authorised to %s."
               % _CAPABILITY_WORDS.get(capability, "perform this action"),
    )


def assert_can_on(db: Session, user: Optional[User], capability: str,
                  agreement: CommercialAgreement) -> None:
    assert_can(db, user, capability,
               brand_sales_org_id=agreement.brand_sales_org_id,
               organization_id=agreement.organization_id)


def grant_commercial_approver(db: Session, user_id: str,
                              brand_sales_org_id: str,
                              granted_by: Optional[str] = None) -> Membership:
    """Give somebody the brand's commercial sign-off.

    Idempotent: re-granting reactivates the existing row rather than creating a
    second one, because `memberships` carries a uniqueness constraint on
    (user, scope, role) and a duplicate would fail at the database instead of
    here.
    """
    row = (db.query(Membership)
           .filter(Membership.user_id == user_id,
                   Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                   Membership.scope_id == brand_sales_org_id,
                   Membership.role == ROLE_COMMERCIAL_APPROVER)
           .first())
    if row is None:
        row = Membership(user_id=user_id, scope_type=SCOPE_BRAND_SALES_ORG,
                         scope_id=brand_sales_org_id,
                         role=ROLE_COMMERCIAL_APPROVER,
                         is_active=True, granted_by=granted_by)
        db.add(row)
    else:
        row.is_active = True
    db.flush()
    return row
