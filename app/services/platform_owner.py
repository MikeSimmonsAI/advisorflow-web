"""THE PLATFORM OWNER IS NEUTRAL.

The owner sits above every platform, every brand and every customer. That is a
statement about authority, and until now it was contradicted by a row in the
database: `app/main.py` created an organization called 'org-god-platform' on
every boot and made the god_admin a MEMBER of it.

Why that matters, concretely. Roughly 182 sites across 40 routers do one of:

    .filter(Model.organization_id == current_user.organization_id)   # read
    Model(organization_id=current_user.organization_id, ...)          # write

Three routers check `_god_all_orgs` before filtering. The other ~179 take the
attribute at face value. Because the owner's organization_id was a REAL org id
rather than NULL, those sites did not fail loudly and did not return empty -
they quietly read and wrote the platform pseudo-org's own tenant data. An owner
who imported a lead without first selecting a customer put that lead in
'org-god-platform', where it belonged to nobody and appeared to have vanished.
`lead_scraper_router.py` is the one place that ever named this:

    "Getting this wrong means leads silently land in the god platform org and
     look like they vanished, so this refuses to guess"

That refusal existed in one router. This module makes it the rule.

TWO PARTS, AND THEY ARE DIFFERENT.

READS may legitimately fan out. "Show me every lead across every customer" is a
real owner capability and `_god_all_orgs` already serves it. Nothing here
narrows a read.

WRITES may not guess. There is no sensible default customer for a write, so a
write performed with no customer selected is refused - it is not silently
attributed to the platform. `require_tenant_context` is that refusal, and
`tenant_write_org_id` is the same refusal for code that already has the user.

The pseudo-org row itself is NOT deleted here. Deleting production rows on a
hunch is how you lose data that turned out to matter, and this codebase has a
standing rule against it. It is instead made inert: excluded from customer
listings, refused as a write target, and reported by the audit endpoint so the
owner can decide what to do with it deliberately.
"""

from typing import Optional

from fastapi import Depends, HTTPException, status

from app.deps import get_current_user
from app.models.models import User, Organization

# The pseudo-organization app/main.py has created on every boot since the
# beginning. Named once, here, so the three places that need to know about it
# stop hardcoding the literal.
GOD_PLATFORM_ORG_ID = "org-god-platform"
GOD_PLATFORM_ORG_SLUG = "advisorflow-platform"

# Its `plan` value, which is how you recognise it if the id was ever changed.
GOD_PLATFORM_ORG_PLAN = "god"


def is_platform_owner(user: Optional[User]) -> bool:
    return bool(user is not None and getattr(user, "role", None) == "god_admin")


def is_platform_pseudo_org(org_id: Optional[str]) -> bool:
    """True for the platform's own placeholder organization.

    Not a customer. Never a valid write target. Never shown in a customer list.
    """
    return bool(org_id) and str(org_id) == GOD_PLATFORM_ORG_ID


def selected_org_id(user: Optional[User]) -> Optional[str]:
    """The customer organization currently in context, or None.

    For a tenant user this is simply their own org - they are always 'in
    context' and cannot leave it. For the owner it is whatever
    `get_current_user` resolved from X-Org-Override, and None means the owner
    is at the platform level with nobody selected.
    """
    if user is None:
        return None
    org_id = getattr(user, "organization_id", None)
    if org_id is None or is_platform_pseudo_org(org_id):
        return None
    return str(org_id)


def has_tenant_context(user: Optional[User]) -> bool:
    return selected_org_id(user) is not None


_NO_CONTEXT_DETAIL = (
    "No customer organization is selected. This action writes records that must "
    "belong to a specific customer, and the platform account is not a customer. "
    "Enter a customer context first, then retry."
)


def tenant_write_org_id(user: User) -> str:
    """The organization id a write should be attributed to, or a clean refusal.

    Use this instead of reading `current_user.organization_id` directly on any
    path that CREATES a tenant row. The difference is the whole point: the
    attribute will happily hand you a value that is wrong, and this will not.
    """
    org_id = selected_org_id(user)
    if org_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_NO_CONTEXT_DETAIL)
    return org_id


def require_tenant_context(user: User = Depends(get_current_user)) -> User:
    """Route guard: this route writes tenant records, so a customer must be selected.

    A tenant user always satisfies this - their org is their context and they
    have no way to be without one. In practice it only ever stops the owner,
    which is exactly who it was written for.
    """
    if not has_tenant_context(user):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_NO_CONTEXT_DETAIL)
    return user


def exclude_platform_org(query, model=Organization):
    """Drop the platform pseudo-org from an organization listing.

    It is not a customer, so it has no business appearing in a customer picker,
    a customer count, or a provisioning target list.
    """
    return query.filter(model.id != GOD_PLATFORM_ORG_ID)


def owner_state(db, user: User) -> dict:
    """Read-only description of how neutral the owner actually is right now.

    Deliberately reports rather than repairs. The mission rule is 'audit the
    existing platform-owner database state first, do not blindly delete/null
    memberships', so this is the audit and the repair is a separate, explicit,
    audited call.
    """
    from app.models.sales_models import Membership

    # Read the STORED row, not the request-scoped one.
    #
    # `get_current_user` neutralises a context-less owner in memory, which is
    # the whole point of this work - but it means the `user` handed to a route
    # already reports organization_id=None even when the database still says
    # 'org-god-platform'. An audit that reported the value it had just
    # overwritten would always say "already neutral" and would be worthless.
    stored = db.query(User).filter(User.id == user.id).first()
    org_id = getattr(stored, "organization_id", None) if stored else None
    pseudo = db.query(Organization).filter(Organization.id == GOD_PLATFORM_ORG_ID).first()

    memberships = (
        db.query(Membership).filter(Membership.user_id == user.id).all()
        if user is not None else []
    )

    return {
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
        "organization_id": org_id,
        "attached_to_pseudo_org": is_platform_pseudo_org(org_id),
        "attached_to_customer_org": bool(org_id) and not is_platform_pseudo_org(org_id),
        "is_neutral": org_id is None,
        "platform_id": getattr(user, "platform_id", None),
        "pseudo_org_exists": pseudo is not None,
        "pseudo_org": None if pseudo is None else {
            "id": pseudo.id, "name": pseudo.name, "slug": pseudo.slug,
            "plan": pseudo.plan, "platform_id": pseudo.platform_id,
            "is_active": bool(pseudo.is_active),
        },
        "memberships": [
            {
                "id": m.id, "scope_type": m.scope_type, "scope_id": m.scope_id,
                "role": m.role, "is_active": bool(m.is_active),
                # A brand-sales membership on the owner is LEGITIMATE - Mike
                # genuinely sells, and seed_evosyspro_sales.py grants it on
                # purpose ("Already exists as god_admin - attach a membership,
                # never duplicate"). It is reported, never assumed disposable.
                "verdict": "legitimate secondary membership"
                           if m.scope_type == "brand_sales_org"
                           else "review - not a brand-sales seat",
            }
            for m in memberships
        ],
    }
