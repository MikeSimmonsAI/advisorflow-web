"""THE AUTHORIZED LEAD SCOPE — one source, used by every lead-touching route.

An advisor could reach 98 routes that query `Lead`. Fifteen scoped to the
advisor who owns the record. The other 83 either stopped at the organization or
did not filter at all, so `POST /email/send/{lead_id}`, `GET /leads/{lead_id}/
activity`, `GET /survey/results/{lead_id}` and dozens like them would answer for
ANY lead in the organization, whoever it belonged to. `GET /leads` was correct;
almost everything around it was not.

That is the shape of the bug: authorization written per route, 98 times, by
hand. Each site was individually plausible and the set was collectively wrong,
and no amount of care applied route-by-route survives the next route somebody
adds. So there is now one function that answers "which leads may this person
see", one that answers "may this person touch THIS lead", and every route calls
one of them.

    authorized_lead_query(db, user)     the list/count/search/export scope
    load_lead_in_scope(db, user, id)    one record, or 404
    assert_leads_in_scope(db, user, ids) a batch, or 403 naming the count
    reject_ownership_fields(user, ...)  advisors cannot rewrite ownership

WHY 404 AND NOT 403 for a lead outside scope: 403 confirms the record exists.
Repeated against a range of ids that is an enumeration oracle for another
advisor's book. `load_org_in_scope` in app/deps.py already made this decision
for organizations and says so; this follows it.

WHAT COUNTS AS SCOPE
    god_admin, neutral      every organization        (the owner control plane)
    god_admin, in a tenant  that organization
    super_admin/org_admin   their own organization
    advisor                 their own organization AND their own leads
    anything else           DENIED

Deny-by-default on the last line is deliberate. A role this function has never
heard of gets nothing rather than falling through to the organization scope,
because the failure mode of guessing is exactly the one being fixed.
"""

import logging
from typing import Iterable, List, Optional, Sequence

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.models.models import Lead, User

# A dedicated logger so denials can be shipped somewhere without dragging the
# rest of the application log with them.
_sec = logging.getLogger("security.authz")

# Roles that see the whole organization's leads.
MANAGER_ROLES = ("org_admin", "super_admin")
# Roles confined to their own assigned leads.
OWNER_SCOPED_ROLES = ("advisor",)
GOD_ROLE = "god_admin"


def is_god(user: User) -> bool:
    return getattr(user, "role", None) == GOD_ROLE


def is_manager(user: User) -> bool:
    """Sees the whole organization. god included."""
    return getattr(user, "role", None) in MANAGER_ROLES + (GOD_ROLE,)


def is_owner_scoped(user: User) -> bool:
    """Sees only their own leads. The plain advisor."""
    return getattr(user, "role", None) in OWNER_SCOPED_ROLES


def active_workspace_org_id(user: User) -> Optional[str]:
    """WHICH CUSTOMER WORKSPACE IS THIS REQUEST IN — the one seam for that answer.

    Today the answer is `users.organization_id`, because that column is how a
    person is put inside a customer organization and there is no other path.
    It is read HERE and nowhere else in this module on purpose.

    The product has two independent access contexts - the brand sales back
    office (a Platform / BrandSalesOrg) and a customer workspace (an
    Organization) - and one person may legitimately hold both, or several of
    the second. `Membership` already models that: it is polymorphic over
    scope_type, and SCOPE_CUSTOMER_ORG is already one of its declared scopes.
    What does not exist yet is any code that WRITES a customer_org membership
    or lets a person choose between two of them, so a single column is still
    the true answer and pretending otherwise here would be inventing state.

    When workspace entry does move to memberships, this function resolves the
    selected workspace and every lead-authorization decision in the product
    follows automatically, because they all run through the query below. A
    platform-only identity keeps returning None here and is refused lead access
    by that alone - which is the rule Mike stated: a salesperson with no
    workspace membership gets no customer workspace data.
    """
    return getattr(user, "organization_id", None)


def log_denial(user: Optional[User], reason: str,
               resource_id: Optional[str] = None,
               request: Optional[Request] = None,
               endpoint: Optional[str] = None,
               method: Optional[str] = None) -> None:
    """Record a refused access attempt.

    Deliberately records WHO, WHERE and WHY and nothing else. No token, no
    password, no request body: a security log that quietly accumulates the
    payloads of failed requests becomes its own disclosure problem, and the
    fields below are enough to answer "who tried to read what".
    """
    _sec.warning(
        "AUTHZ DENIED user=%s org=%s role=%s method=%s endpoint=%s resource=%s reason=%s",
        getattr(user, "id", None), getattr(user, "organization_id", None),
        getattr(user, "role", None),
        method or (request.method if request is not None else None),
        endpoint or (str(request.url.path) if request is not None else None),
        resource_id, reason,
    )


def _deny(user, reason, resource_id=None, request=None, code=403):
    log_denial(user, reason, resource_id, request)
    raise HTTPException(status_code=code, detail=reason)


def authorized_lead_query(db: Session, user: User, *columns,
                          request: Optional[Request] = None):
    """THE scope. Every list, count, search, filter and export starts here.

    Pass columns to select a lean projection, exactly as the leads list does:

        q = authorized_lead_query(db, user, Lead.id, Lead.first_name)
        q = authorized_lead_query(db, user)          # full entities

    The caller may narrow this further. It must never widen it, and it cannot:
    the returned query already carries the filters, so an added `.filter()` can
    only intersect. That is the property that makes one function enough - a
    query object cannot be un-filtered by a later call.
    """
    q = db.query(*columns) if columns else db.query(Lead)

    if is_god(user):
        # Neutral owner: every organization. Inside a customer: that one.
        # `_god_all_orgs` is set by get_current_user when no X-Org-Override is
        # present, and organization_id is None in the same state - either is
        # sufficient, both are checked because they are set independently.
        if getattr(user, "_god_all_orgs", False) or not active_workspace_org_id(user):
            return q
        return q.filter(Lead.organization_id == active_workspace_org_id(user))

    org_id = active_workspace_org_id(user)
    if not org_id:
        # A brand-sales identity or a context-less account. `require_tenant_user`
        # normally catches this first; if some route forgets that dependency,
        # filtering on NULL would render as IS NULL and quietly return nothing,
        # which looks like authorization but is a schema accident.
        _deny(user, "This account is not inside a customer organization.",
              request=request)

    q = q.filter(Lead.organization_id == org_id)

    if is_manager(user):
        return q
    if is_owner_scoped(user):
        return q.filter(Lead.assigned_to_id == user.id)

    _deny(user, "Your role is not permitted to read lead data.", request=request)


def load_lead_in_scope(db: Session, user: User, lead_id: str,
                       request: Optional[Request] = None) -> Lead:
    """One lead the caller is entitled to, else 404.

    THE ID IS APPLIED INSIDE THE AUTHORIZED QUERY, not looked up first and
    checked afterwards. Fetch-then-check is the pattern that leaks: the row is
    already in memory, and every future edit to the function is one `return`
    away from handing it back.
    """
    lead = authorized_lead_query(db, user, request=request).filter(
        Lead.id == lead_id).first()
    if lead is None:
        # 404, NOT 403 - a 403 here confirms the lead exists, which turns this
        # endpoint into an enumeration oracle for another advisor's book.
        log_denial(user, "lead outside authorized scope", lead_id, request)
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


def authorized_lead_ids(db: Session, user: User,
                        request: Optional[Request] = None) -> List[str]:
    """Every lead id in scope. For subqueries on tables that hang off a lead."""
    return [r[0] for r in authorized_lead_query(db, user, Lead.id, request=request).all()]


def assert_leads_in_scope(db: Session, user: User, lead_ids: Sequence[str],
                          request: Optional[Request] = None) -> List[str]:
    """Every id in a batch must be in scope, or the whole request is refused.

    REFUSES RATHER THAN SILENTLY DROPPING. A batch send that quietly skipped the
    ids you were not allowed to touch would report success for a job it did not
    do, and the caller would never learn which half went out. The count is named
    so the refusal is actionable; the ids are not, because listing them back
    would confirm exactly what the caller was probing for.
    """
    ids = [i for i in (lead_ids or []) if i]
    if not ids:
        return []
    allowed = {
        r[0] for r in authorized_lead_query(db, user, Lead.id, request=request)
        .filter(Lead.id.in_(ids)).all()
    }
    missing = [i for i in ids if i not in allowed]
    if missing:
        log_denial(user, "batch contained %d lead(s) outside scope" % len(missing),
                   None, request)
        raise HTTPException(
            status_code=403,
            detail="%d of %d selected leads are not yours to act on."
                   % (len(missing), len(ids)))
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# MUTATIONS — ownership is not a field the client may set
# ─────────────────────────────────────────────────────────────────────────────

# Fields that decide who owns a record and which tenant it belongs to. An
# advisor sending any of these is trying to move a lead, whether or not they
# know it.
OWNERSHIP_FIELDS = frozenset({
    "assigned_to_id", "assigned_user_id", "organization_id", "org_id",
    "owner_id", "owner_user_id", "batch_id", "import_id", "import_list_name",
    "created_by", "created_by_id", "source_user_id", "imported_by_name",
    "imported_by_id",
})


def reject_ownership_fields(user: User, payload, request: Optional[Request] = None) -> None:
    """Refuse an advisor's attempt to rewrite ownership through the body.

    Reads `model_fields_set` on a pydantic model, so a field the caller
    explicitly sent is refused while a field merely defaulting to None is not -
    the difference between "I did not mention this" and "I set this to null",
    which for `assigned_to_id` is the difference between an ordinary edit and
    unassigning somebody else's lead.

    Managers and god pass: reassignment is a real capability they hold, and it
    has its own endpoint with its own audit.
    """
    if not is_owner_scoped(user):
        return
    if payload is None:
        return
    sent = getattr(payload, "model_fields_set", None)
    if sent is None:
        sent = set(payload.keys()) if isinstance(payload, dict) else set()
    offending = OWNERSHIP_FIELDS.intersection(sent)
    if offending:
        log_denial(user, "advisor attempted to set ownership field(s): %s"
                   % ", ".join(sorted(offending)), None, request)
        raise HTTPException(
            status_code=403,
            detail="You cannot change lead ownership or import attribution. "
                   "Ask an administrator to reassign the lead.")
