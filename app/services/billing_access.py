"""BILLING AUTHORITY — which customer's money this request may touch.

THE DEFECT THIS REPLACES

Every tenant billing route used to do the same two things:

    current_user: User = Depends(_require_admin)          # a ROLE check
    org = db.query(Organization).filter(
        Organization.id == current_user.organization_id)  # a LEGACY column

`users.organization_id` is the tenant a person was historically attached to. It
is not the workspace they are standing in. For anybody who holds more than one
membership those are different answers, and billing was using the wrong one -
so a dual-role user's billing page showed, and could change, whichever
organization their column happened to name.

ACTIVE WORKSPACE AUTHORITY FIRST

`lead_scope.active_workspace_org_id` is this codebase's existing seam for
"which workspace is this request in". It resolves an X-Workspace-Id selection
ONLY against a membership the caller actually holds - a browser can pick among
the workspaces it has, it cannot assert one it does not - and falls back to the
legacy column when no workspace was selected, which is the same answer that
column already gave for single-workspace users.

Billing now asks that seam, and every read and write is scoped to what it
returns. Nothing here accepts an organization id from the caller. There is no
org_id path parameter, no body field and no query string that can widen scope,
which is what makes URL editing and UUID guessing non-events rather than
defended attacks.

WHO HOLDS BILLING ACCESS

    god / platform      per the existing platform model, unchanged
    org_admin of the    baseline billing_view + billing_manage, for THAT
    ACTIVE workspace    organization only
    anybody else        only via an explicit capability grant

The baseline is the important half. Registering these as ordinary two-gate
capabilities would have been stricter and would also have taken billing away
from every existing customer administrator the moment it deployed, until
somebody delegated and granted per organization - a lockout on a payments
screen. A grant EXTENDS the baseline (it is how a bookkeeper who is not an
org_admin gets access); it does not gate it.

`effective_role` is what decides "org_admin", not `users.role`. A BookaBoost
salesperson who administers one customer workspace holds org_admin ON THAT
MEMBERSHIP while their platform role says advisor. Reading the column would
give them nothing here, and would give them their own default org's billing
somewhere else - both wrong.

billing_manage implies billing_view: anyone trusted to change what a customer
is charged can necessarily read it.
"""

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.models import Organization, User
from app.services import capabilities as caps
from app.services import lead_scope

logger = logging.getLogger(__name__)

BILLING_VIEW = "billing_view"
BILLING_MANAGE = "billing_manage"

# The workspace roles that hold billing as a baseline. super_admin and
# god_admin are platform identities and are handled separately by
# capabilities.is_god / the platform capability path; org_admin is the customer
# administrator this baseline exists for.
_BASELINE_ROLES = ("org_admin", "super_admin", "god_admin")


class BillingScope:
    """The resolved answer: this caller, in this organization, may do this.

    Routes receive one of these instead of a bare user, so no endpoint has to
    re-derive the organization and none of them can disagree about it.
    """

    __slots__ = ("user", "organization", "can_view", "can_manage")

    def __init__(self, user, organization, can_view, can_manage):
        self.user = user
        self.organization = organization
        self.can_view = can_view
        self.can_manage = can_manage

    @property
    def organization_id(self) -> Optional[str]:
        return self.organization.id if self.organization is not None else None

    def __repr__(self):                                  # pragma: no cover
        return "<BillingScope user=%s org=%s view=%s manage=%s>" % (
            getattr(self.user, "id", None), self.organization_id,
            self.can_view, self.can_manage)


def _has_grant(db: Session, user: User, org: Organization, key: str) -> bool:
    """An explicit capability grant on the ACTIVE organization.

    BOTH GATES, CHECKED DIRECTLY, AND WHY NOT `caps.resolve`.

    `resolve()` refuses anyone whose role is outside ELIGIBLE_ADMIN_ROLES
    BEFORE it looks at grants, because the capabilities it was built for -
    Twilio credentials, A2P registration - administer infrastructure and are
    genuinely administrator-only. Billing is not like that: the whole reason
    billing_view exists as a capability rather than a role check is so a
    bookkeeper who is NOT an org_admin can reconcile the books.

    So this asks the framework's own two primitives instead of its
    administrator-shaped wrapper. The gates are unchanged and both still
    apply - the ORGANIZATION must be permitted to self-manage the capability,
    and THIS person must hold an active grant for it in THIS organization.
    Nothing about the global framework is altered; billing simply declines to
    inherit a role restriction that was written for a different problem.

    A failure to resolve is a denial. A capability check that cannot run must
    never read as a capability check that passed.
    """
    try:
        if not caps.org_may_self_manage(org, key):
            return False
        return caps.user_has_grant(db, user.id, org.id, key)
    except Exception:
        logger.warning("billing capability %s could not be resolved for user=%s "
                       "org=%s", key, getattr(user, "id", None),
                       getattr(org, "id", None))
        return False


def resolve_billing_scope(db: Session, user: User,
                          request: Optional[Request] = None) -> BillingScope:
    """What this caller may do with billing, and whose billing it is.

    Never raises for authorization: it reports. The dependencies below turn a
    report into a refusal, so a caller that needs to ASK without being refused
    (a UI deciding whether to render a button) can use this directly.
    """
    if caps.is_god(user):
        # The platform owner's authority is not a customer's grant. Which
        # organization they are acting on still comes from the active
        # workspace - god with no customer selected has no billing subject,
        # which is correct: there is nobody to bill.
        org_id = lead_scope.active_workspace_org_id(user, db, request)
        org = (db.query(Organization).filter(Organization.id == org_id).first()
               if org_id else None)
        return BillingScope(user, org, True, True)

    org_id = lead_scope.active_workspace_org_id(user, db, request)
    if not org_id:
        # A platform-only identity - a brand salesperson with no customer
        # membership - has no billing subject at all.
        return BillingScope(user, None, False, False)

    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        return BillingScope(user, None, False, False)

    role = lead_scope.effective_role(user, db, request)
    baseline = role in _BASELINE_ROLES

    can_manage = baseline or _has_grant(db, user, org, BILLING_MANAGE)
    # billing_manage implies billing_view.
    can_view = can_manage or baseline or _has_grant(db, user, org, BILLING_VIEW)
    return BillingScope(user, org, can_view, can_manage)


def _deny(what: str):
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have permission to %s billing for this "
               "organization." % what)


def require_billing_view(request: Request,
                         db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)
                         ) -> BillingScope:
    """FastAPI dependency: read access to the ACTIVE organization's billing."""
    scope = resolve_billing_scope(db, current_user, request)
    if scope.organization is None or not scope.can_view:
        logger.info("AUDIT: billing view DENIED user=%s org=%s",
                    getattr(current_user, "id", None), scope.organization_id)
        raise _deny("view")
    return scope


def require_billing_manage(request: Request,
                           db: Session = Depends(get_db),
                           current_user: User = Depends(get_current_user)
                           ) -> BillingScope:
    """FastAPI dependency: mutate the ACTIVE organization's billing."""
    scope = resolve_billing_scope(db, current_user, request)
    if scope.organization is None or not scope.can_manage:
        logger.info("AUDIT: billing manage DENIED user=%s org=%s",
                    getattr(current_user, "id", None), scope.organization_id)
        raise _deny("manage")
    return scope


def assert_owns_stripe_customer(scope: BillingScope,
                                stripe_customer_id: Optional[str]) -> None:
    """Refuse a Stripe customer reference that is not this organization's.

    Nothing in the current routes takes a customer id from the caller, so this
    is a guard for the operations P4 adds rather than a fix for a live hole.
    It exists now because the moment an endpoint DOES accept one, the check has
    to already be somewhere obvious rather than invented at that call site.
    """
    if not stripe_customer_id:
        return
    org = scope.organization
    if org is None or getattr(org, "stripe_customer_id", None) != stripe_customer_id:
        logger.info("AUDIT: billing customer mismatch user=%s org=%s claimed=%s",
                    getattr(scope.user, "id", None),
                    scope.organization_id, stripe_customer_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such billing customer for this organization.")
