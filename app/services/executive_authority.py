"""EXECUTIVE PORTFOLIO AUTHORITY — the one answer to "which organizations?"

═══════════════════════════════════════════════════════════════════════════
THE DEFECT THIS MODULE EXISTS TO CLOSE
═══════════════════════════════════════════════════════════════════════════

Executive visibility was BRAND-WIDE. `require_brand_executive` resolved a
`Membership(scope_type="platform", scope_id=<platform_id>)` into a Platform,
and every executive surface then ran:

    db.query(Organization).filter(Organization.platform_id == platform_id)

so holding an executive grant on a brand exposed EVERY organization on that
brand. The grant record had no organization dimension at all, which means
"assign this executive Restland only" was not merely unimplemented — it was
inexpressible. Two executives under the same white-label brand saw each
other's entire portfolio, and a third one added tomorrow would have seen both.

That is the rule this module enforces instead:

    AN EXECUTIVE SEES EXACTLY THE ORGANIZATIONS THEY WERE ASSIGNED.
    A ROLE IS NOT A PORTFOLIO.

═══════════════════════════════════════════════════════════════════════════
WHERE AN ASSIGNMENT LIVES
═══════════════════════════════════════════════════════════════════════════

    Membership(
        user_id    = <the executive>,
        scope_type = SCOPE_CUSTOMER_ORG,      # "customer_org"
        scope_id   = <organization id>,        # ONE organization
        role       = ROLE_BRAND_EXECUTIVE,
        is_active  = True,
        granted_by = <the owner who assigned it>,
    )

Nothing new was invented for this. `SCOPE_CUSTOMER_ORG` already existed, and
`Membership` already carried `granted_by` and `created_at`, so assignment
history is durable and auditable from the day it is written. What was missing
was anything that read those rows for an executive.

TWO ROWS, TWO DIFFERENT QUESTIONS, AND THEY MUST NOT BE CONFLATED:

    platform-scoped grant  → WHICH BRAND may this person enter at all
    org-scoped assignment  → WHICH CUSTOMERS inside it may they see

The platform grant is still required. An assignment without it does not let
somebody into the Executive Suite, and a grant without assignments produces an
empty portfolio rather than everything.

═══════════════════════════════════════════════════════════════════════════
WHY THE OWNER IS DIFFERENT, AND WHY THAT IS NOT AN EXCEPTION TO THE RULE
═══════════════════════════════════════════════════════════════════════════

`god_admin` reaches the Executive Suite through brand SELECTION, not through a
grant — `require_brand_executive` hands owners a sentinel membership and no
database row exists. The owner owns the estate; narrowing them to an assigned
list would mean the platform owner could be locked out of their own customers
by an assignment they forgot to make, and would need a fake assignment row per
organization to see what is already theirs.

So the owner is brand-wide WITHIN THE BRAND THEY SELECTED, which is the same
isolation every other executive query has: exactly one platform, never all of
them. That is a different authority answering a different question, not a hole
in this one — and `is_owner_view` on the result says which path produced it, so
no caller has to guess.

═══════════════════════════════════════════════════════════════════════════
THE INTERSECTION IS NOT OPTIONAL
═══════════════════════════════════════════════════════════════════════════

An assignment row is a (user, org) pair and carries no brand of its own. If one
ever pointed at an organization in a DIFFERENT brand — a mistake, a moved
customer, a stale row after a re-platforming — honouring it would let a
portfolio cross a brand boundary, which is the one thing white-label isolation
cannot survive. So every assignment is intersected with the platform the
executive is currently authorized for, and an out-of-brand assignment grants
nothing.

═══════════════════════════════════════════════════════════════════════════
ONE AUTHORITY, CONSUMED EVERYWHERE
═══════════════════════════════════════════════════════════════════════════

    Executive user
          ↓
    authorized_org_ids()          ← this module, the only implementation
          ↓
    Command Center · Organizations · Revenue · Drill-down · legacy endpoints

`executive_portfolio.rows()` REQUIRES the id list as a keyword argument with no
default. That is deliberate: a service that could be called without a scope
would eventually be called without one, and the failure would be silent and
total. Making it impossible to call wrongly is worth more than a convenient
default.

Frontend filtering is never the boundary. Every check here runs server-side,
and a pasted organization id that is not in the authorized set is answered
identically to one that does not exist — a 403 would confirm the organization
is real, which is itself a leak.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Organization, User
from app.models.sales_models import (ROLE_BRAND_EXECUTIVE, SCOPE_CUSTOMER_ORG,
                                     Membership)

# What produced a portfolio. Reported so a screen can explain an empty one
# rather than looking broken, and so tests can assert which path ran.
SOURCE_OWNER = "owner_brand_wide"
SOURCE_ASSIGNED = "explicit_assignment"
SOURCE_NONE = "no_assignments"


def is_owner(user: User) -> bool:
    """The platform owner, who reaches this suite by selecting a brand."""
    return getattr(user, "role", None) == "god_admin"


def assigned_org_ids(db: Session, user_id: str) -> List[str]:
    """Every organization explicitly assigned to this person, ANY brand.

    Raw assignment rows, deliberately un-intersected: the God Mode management
    screen needs to show what has been assigned even if a row has drifted out
    of the brand it was made in, and hiding such a row would make it
    unremovable through the UI.

    Authorization must use `authorized_org_ids`, never this.
    """
    rows = (db.query(Membership.scope_id)
            .filter(Membership.user_id == user_id,
                    Membership.scope_type == SCOPE_CUSTOMER_ORG,
                    Membership.role == ROLE_BRAND_EXECUTIVE,
                    Membership.is_active.is_(True))
            .all())
    return sorted({r[0] for r in rows if r[0]})


def portfolio_authority(db: Session, user: User,
                        platform_id: str) -> Dict[str, Any]:
    """WHICH ORGANIZATIONS MAY THIS PERSON SEE, and how that was decided.

    Returns {"org_ids": [...], "source": ..., "is_owner_view": bool}.

    The list is the whole boundary. Every executive surface passes it into the
    portfolio service, so there is exactly one place that can be wrong and
    exactly one place to test.
    """
    if is_owner(user):
        # THE OWNER'S ESTATE, narrowed to the brand they selected — the same
        # single-platform isolation every other executive query has.
        ids = [r[0] for r in
               db.query(Organization.id)
               .filter(Organization.platform_id == platform_id).all()]
        return {"org_ids": sorted(ids), "source": SOURCE_OWNER,
                "is_owner_view": True}

    # THE ASSIGNMENT AND THE INTERSECTION IN ONE QUERY.
    #
    # Joining Organization to the assignment rows applies both boundaries at
    # once: the row must be an active org-scoped executive assignment for THIS
    # person, and the organization must be in THIS brand. An assignment
    # pointing at another brand's customer therefore returns nothing rather
    # than being fetched and then discarded.
    #
    # One round trip also matters because this runs on every executive request,
    # including the per-organization authorization check.
    ids = [r[0] for r in
           db.query(Organization.id)
           .join(Membership, Membership.scope_id == Organization.id)
           .filter(Membership.user_id == user.id,
                   Membership.scope_type == SCOPE_CUSTOMER_ORG,
                   Membership.role == ROLE_BRAND_EXECUTIVE,
                   Membership.is_active.is_(True),
                   Organization.platform_id == platform_id)
           .all()]
    if not ids:
        # AN EMPTY PORTFOLIO IS A REAL, EXPLAINABLE STATE, not an error and
        # certainly not a reason to fall back to the whole brand. An executive
        # who has been granted a brand but assigned no customers has been half
        # set up, and the screens say so.
        return {"org_ids": [], "source": SOURCE_NONE, "is_owner_view": False}
    return {"org_ids": sorted(set(ids)), "source": SOURCE_ASSIGNED,
            "is_owner_view": False}


def authorized_org_ids(db: Session, user: User, platform_id: str) -> List[str]:
    """The authorized set alone, for callers that need nothing else."""
    return portfolio_authority(db, user, platform_id)["org_ids"]


def may_view_org(db: Session, user: User, platform_id: str,
                 org_id: Optional[str]) -> bool:
    """May this person open THIS organization?

    Used by every per-organization route. Callers must answer a False with the
    SAME 404 they would give for an organization that does not exist: a 403
    tells the caller the id is real, which is exactly what somebody probing
    ids is trying to learn.
    """
    if not org_id:
        return False
    return org_id in set(authorized_org_ids(db, user, platform_id))


# ── assignment, for the owner's management screen ───────────────────────────

def assign(db: Session, *, executive_user_id: str, organization_id: str,
           granted_by_user_id: Optional[str]) -> Dict[str, Any]:
    """Give one executive one organization. Idempotent.

    Reactivates a previously revoked row rather than writing a second one, so
    the assignment history stays a single readable thread per (person,
    organization) instead of a pile of duplicates.
    """
    existing = (db.query(Membership)
                .filter(Membership.user_id == executive_user_id,
                        Membership.scope_type == SCOPE_CUSTOMER_ORG,
                        Membership.scope_id == organization_id,
                        Membership.role == ROLE_BRAND_EXECUTIVE)
                .first())
    if existing is not None:
        if existing.is_active:
            return {"status": "already_assigned", "membership_id": existing.id}
        existing.is_active = True
        existing.granted_by = granted_by_user_id
        db.commit()
        return {"status": "reassigned", "membership_id": existing.id}

    mem = Membership(user_id=executive_user_id,
                     scope_type=SCOPE_CUSTOMER_ORG,
                     scope_id=organization_id,
                     role=ROLE_BRAND_EXECUTIVE,
                     is_active=True,
                     granted_by=granted_by_user_id)
    db.add(mem)
    db.commit()
    return {"status": "assigned", "membership_id": mem.id}


def unassign(db: Session, *, executive_user_id: str,
             organization_id: str) -> Dict[str, Any]:
    """Take one organization away. DEACTIVATES, never deletes.

    The row stays so the history of who was given what, by whom and when
    survives the removal. Deleting it would erase the only record that the
    access ever existed, which is the thing an audit is for.

    Visibility is recomputed on every request from `is_active`, so the effect
    is immediate — there is no cached portfolio to invalidate.
    """
    mem = (db.query(Membership)
           .filter(Membership.user_id == executive_user_id,
                   Membership.scope_type == SCOPE_CUSTOMER_ORG,
                   Membership.scope_id == organization_id,
                   Membership.role == ROLE_BRAND_EXECUTIVE,
                   Membership.is_active.is_(True))
           .first())
    if mem is None:
        return {"status": "not_assigned"}
    mem.is_active = False
    db.commit()
    return {"status": "unassigned", "membership_id": mem.id}
