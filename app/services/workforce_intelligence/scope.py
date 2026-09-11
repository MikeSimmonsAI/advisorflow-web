"""THE ONE PLACE T9 DECIDES WHAT A CALLER MAY SEE.

EVERY QUERY IN THIS PACKAGE TAKES A SCOPE, AND A SCOPE IS BUILT HERE.

Section 17 and section 18 together are the reason this module exists as a
module rather than as a filter each function remembers. A management layer
reads across work items, runs, tool executions, conversations, communications,
actions, audit entries, handoffs, deployments, counters and its own management
tables. That is fifteen-odd places a tenant filter has to be right, on every
one of a dozen screens, forever. Written out fifteen times it will be wrong
somewhere within a month, and the failure is silent: a query missing its
filter returns MORE rows, not fewer, so nothing errors and a number is simply
too big.

So the filter is a value. `Scope.org_filter(column)` returns the SQL clause,
and a function that forgot to apply it has an unfiltered query that is visible
in review rather than a subtly larger count that is not.

AGGREGATE LEAKAGE IS STILL TENANT LEAKAGE. A COUNT, a SUM, an AVG, a
benchmark, a cached payload and a background rollup are all covered by the
same clause, because they all go through the same helper. Section 17 lists
those explicitly, and the reason they need listing is that they are the ones
people forget: nobody ships a raw cross-tenant SELECT, and plenty of systems
ship a cross-tenant average.

GOD IS AN AUTHORITY, NOT A SCOPE. A god_admin asking about one customer gets
an ORGANIZATION scope with that one organization in it, and the answer is byte
for byte the answer the customer's own manager gets. The only thing God's
authority changes is WHICH scopes may be constructed - it may construct a
platform scope, and nobody else may. It does NOT create a second shape of any
payload, and it grants no new root: `require_god` is the same dependency every
other platform-control route uses.

A WHITE-LABEL OWNER IS NOT A ROOT EITHER. A brand scope is bounded by a
subquery over `organizations.platform_id`, so the widest thing a brand
executive can ask for is their own brand's customers. There is no argument
anywhere in this package that widens that, because the widening would have to
happen here and this is the whole file.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

from sqlalchemy import false, select
from sqlalchemy.orm import Session

from app.models.models import Organization, User
from app.services.workforce_intelligence import constants as C

_log = logging.getLogger(__name__)
_sec = logging.getLogger("security.authz")


class ScopeRefused(Exception):
    """A caller asked about something outside their scope.

    Carries a code so a router can answer 404 rather than 403 where telling
    the caller a record exists elsewhere would itself be a disclosure - the
    same choice T7's router makes for another tenant's conversation.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Scope:
    """What one caller may see, as data.

    `organization_ids` being None means EVERY organization, and is reachable
    only through `for_platform`, which only a god_admin may call. Everywhere
    else it is an explicit tuple, and an EMPTY tuple means nothing - which
    `org_filter` renders as a false clause rather than as an absent filter.
    That direction matters: an empty IN list that silently becomes "no filter"
    is how a scope bug turns into a platform-wide read.
    """

    scope_type: str
    scope_id: str
    actor_user_id: Optional[str]
    is_god: bool = False
    read_only: bool = False
    organization_ids: Optional[Tuple[str, ...]] = None
    platform_ids: Optional[Tuple[str, ...]] = None
    label: str = ""
    _all_organizations: bool = field(default=False)

    # -- the filter ---------------------------------------------------------

    def org_filter(self, column):
        """The clause that confines a query on `column` to this scope.

        THREE ANSWERS, AND THE MIDDLE ONE IS THE IMPORTANT ONE:

            platform scope held by God  -> no restriction, deliberately
            brand scope                 -> IN (subquery over this brand)
            organization scope          -> IN (explicit ids)
            anything with no ids        -> FALSE

        The subquery rather than an enumerated list is what makes a brand with
        two thousand customers answerable without materialising two thousand
        ids into a Python list and an IN clause - and it stays a hard filter,
        which an enumerated list truncated by a limit would not.
        """
        if self._all_organizations:
            return None
        if self.scope_type == C.SCOPE_BRAND and self.platform_ids:
            return column.in_(
                select(Organization.id).where(
                    Organization.platform_id.in_(list(self.platform_ids))))
        if self.organization_ids:
            return column.in_(list(self.organization_ids))
        return false()

    def apply(self, query, column):
        """`query`, confined to this scope. Use this, not a hand-written filter."""
        clause = self.org_filter(column)
        if clause is None:
            return query
        return query.filter(clause)

    def covers(self, organization_id: Optional[str]) -> bool:
        if not organization_id:
            return False
        if self._all_organizations:
            return True
        if self.organization_ids is not None:
            return str(organization_id) in set(self.organization_ids)
        return False

    def assert_covers(self, db: Session, organization_id: Optional[str]) -> str:
        """Refuse, loudly and in the log, before any query runs.

        Used where a caller supplies an id - a drill-down target, an employee,
        a deployment, a conversation. Section 18 asks for a hostile drill-down
        target to be refused, and this is where that happens.
        """
        if self.covers(organization_id):
            return str(organization_id)
        if self._all_organizations and organization_id:
            return str(organization_id)
        if self.scope_type == C.SCOPE_BRAND and organization_id:
            org = (db.query(Organization.id)
                   .filter(Organization.id == organization_id,
                           Organization.platform_id.in_(
                               list(self.platform_ids or ())))
                   .first())
            if org is not None:
                return str(organization_id)
        _sec.warning("t9 scope refusal actor=%s scope=%s/%s target_org=%s",
                     self.actor_user_id, self.scope_type, self.scope_id,
                     organization_id)
        raise ScopeRefused(C.R_TENANT_MISMATCH,
                           "That record is not in this workspace.")

    # -- keys ---------------------------------------------------------------

    def cache_key(self, view_key: str, window_key: str) -> Tuple[str, str, str, str]:
        """The read-model key. THE SCOPE IS PART OF IT, always.

        A cache keyed on the view alone is a cross-tenant leak with a tidy
        name. This returns the whole tuple so a caller cannot construct half
        of one.
        """
        return (self.scope_type, self.scope_id or "", view_key,
                window_key or C.DEFAULT_WINDOW)

    def as_dict(self) -> dict:
        return {
            "scope_type": self.scope_type,
            "scope_id": self.scope_id,
            "label": self.label,
            "read_only": self.read_only,
            "organization_count": (None if self._all_organizations
                                   else len(self.organization_ids or ())),
            "platform_scoped": self.scope_type == C.SCOPE_BRAND,
            "platform_wide": self._all_organizations,
        }


# ---------------------------------------------------------------------------
# THE CONSTRUCTORS - the only ways a Scope comes into existence
# ---------------------------------------------------------------------------


def _is_god(user: Optional[User]) -> bool:
    return getattr(user, "role", None) == "god_admin"


def _observing(request) -> bool:
    """Is this caller inside Executive Observation Mode?

    An executive observing a customer reads; they do not act. The mutation
    guard `require_not_observation` is the security boundary and stays on
    every write route; this flag is what lets a READ payload tell the screen
    which buttons are not available, so the two cannot disagree.
    """
    if request is None:
        return False
    return getattr(request.state, "executive_observation", None) is not None


def for_organization(db: Session, user: User, request=None) -> Scope:
    """The caller's OWN customer workspace.

    THE CUSTOMER SURFACE TAKES NO ORGANIZATION ARGUMENT, so there is no
    parameter anywhere on it that could widen what a caller sees - the same
    property T7's router relies on. The organization is resolved from the
    session, once, here.
    """
    from app.services.lead_scope import active_workspace_org_id

    org_id = active_workspace_org_id(user, db, request)
    if not org_id:
        raise ScopeRefused(
            "no_workspace_selected",
            "No workspace is selected. Choose a customer workspace before "
            "opening AI Workforce Command.")
    org = db.query(Organization).filter(Organization.id == org_id).first()
    return Scope(
        scope_type=C.SCOPE_ORGANIZATION,
        scope_id=str(org_id),
        actor_user_id=getattr(user, "id", None),
        is_god=_is_god(user),
        read_only=_observing(request),
        organization_ids=(str(org_id),),
        platform_ids=((str(org.platform_id),)
                      if org is not None and org.platform_id else None),
        label=(getattr(org, "name", None) or "This workspace"),
    )


def for_organization_as_operator(db: Session, actor: User,
                                 organization_id: str,
                                 request=None) -> Scope:
    """One named customer, asked about by somebody above them.

    THE ANSWER IS THE SAME ANSWER THE CUSTOMER GETS. What differs is who is
    allowed to ask, and that question is answered by `load_org_in_scope` -
    the existing platform guard, which refuses with 404 rather than 403 so
    another platform's customers cannot be enumerated one id at a time.

    A brand operator reaches only their own brand's customers through it. A
    god_admin reaches every organization, which is what the owner control
    plane is for and is not a new root created here.
    """
    from app.deps import load_org_in_scope

    org = load_org_in_scope(db, actor, organization_id)
    return Scope(
        scope_type=C.SCOPE_ORGANIZATION,
        scope_id=str(org.id),
        actor_user_id=getattr(actor, "id", None),
        is_god=_is_god(actor),
        read_only=_observing(request),
        organization_ids=(str(org.id),),
        platform_ids=((str(org.platform_id),) if org.platform_id else None),
        label=(org.name or "Customer"),
    )


def for_brand(db: Session, user: User, platform_id: str,
              request=None) -> Scope:
    """One white-label brand's customers, and nobody else's.

    AUTHORITY IS CHECKED BY THE CALLER, NOT INVENTED HERE. The router uses
    `require_brand_executive`, which already proves the caller holds a
    brand_executive membership for exactly one platform (or is a god_admin
    with an explicit brand context selected). This function narrows to that
    platform; it never widens, and it never decides who is an executive.

    The guard below is belt and braces rather than the boundary: a caller who
    somehow reached here for a platform they do not hold is refused, and the
    refusal is logged under security.authz so it is visible rather than
    merely prevented.
    """
    if not platform_id:
        raise ScopeRefused(C.R_NOT_AUTHORIZED,
                           "Select a brand before opening this view.")
    if not _is_god(user):
        held = getattr(user, "platform_id", None)
        selected = getattr(user, "_selected_brand_id", None)
        if str(platform_id) not in {str(held or ""), str(selected or "")}:
            from app.models.sales_models import (Membership,
                                                 ROLE_BRAND_EXECUTIVE,
                                                 SCOPE_PLATFORM)
            mem = (db.query(Membership.id)
                   .filter(Membership.user_id == getattr(user, "id", None),
                           Membership.scope_type == SCOPE_PLATFORM,
                           Membership.scope_id == str(platform_id),
                           Membership.role == ROLE_BRAND_EXECUTIVE,
                           Membership.is_active.is_(True))
                   .first())
            if mem is None:
                _sec.warning("t9 brand scope refusal actor=%s platform=%s",
                             getattr(user, "id", None), platform_id)
                raise ScopeRefused(C.R_NOT_AUTHORIZED,
                                   "That brand is not in your scope.")
    from app.models.models import Platform
    brand = db.query(Platform).filter(Platform.id == platform_id).first()
    return Scope(
        scope_type=C.SCOPE_BRAND,
        scope_id=str(platform_id),
        actor_user_id=getattr(user, "id", None),
        is_god=_is_god(user),
        read_only=_observing(request),
        organization_ids=None,
        platform_ids=(str(platform_id),),
        label=(getattr(brand, "name", None) or "This brand"),
    )


def for_platform(db: Session, user: User) -> Scope:
    """Every organization on the box. GOD ONLY, and it says so in one place.

    This is the ONLY constructor that can produce a scope with no
    organization filter, and it refuses anybody who is not a god_admin. There
    is no "platform superadmin", no "root admin" and no second control plane:
    a super_admin calling this is refused here exactly as they would be by
    `require_god` on the route.
    """
    if not _is_god(user):
        _sec.warning("t9 platform scope refusal actor=%s role=%s",
                     getattr(user, "id", None), getattr(user, "role", None))
        raise ScopeRefused(C.R_NOT_AUTHORIZED,
                           "Platform-wide workforce intelligence is God Mode "
                           "only.")
    return Scope(
        scope_type=C.SCOPE_PLATFORM,
        scope_id=C.PLATFORM_SCOPE_ID,
        actor_user_id=getattr(user, "id", None),
        is_god=True,
        read_only=False,
        organization_ids=None,
        platform_ids=None,
        label="All brands",
        _all_organizations=True,
    )


def for_system(organization_ids: Sequence[str], *,
               platform_id: Optional[str] = None) -> Scope:
    """A scope for a background pass, over an explicit list and nothing more.

    Background aggregation is the other half of section 17's warning. A cron
    that runs "for every organization" and writes one cache is how a rollup
    leaks; this constructor takes the ids it may touch and produces a scope
    that cannot reach past them, so the pass is confined by the same mechanism
    a request is.
    """
    ids = tuple(str(i) for i in organization_ids if i)
    return Scope(
        scope_type=C.SCOPE_ORGANIZATION if len(ids) == 1 else C.SCOPE_BRAND,
        scope_id=(ids[0] if len(ids) == 1 else str(platform_id or "")),
        actor_user_id=None,
        is_god=False,
        read_only=True,
        organization_ids=ids,
        platform_ids=((str(platform_id),) if platform_id else None),
        label="Background aggregation",
    )


def organization_ids_in(db: Session, scope: Scope,
                        limit: int = 5000) -> Tuple[str, ...]:
    """The organizations this scope actually covers, materialised.

    Only for the passes that genuinely have to iterate - the attention pass
    writes per-organization rows and therefore needs the list. Read paths use
    `org_filter` instead and never materialise anything.
    """
    if scope.organization_ids is not None:
        return scope.organization_ids
    q = db.query(Organization.id)
    if scope.scope_type == C.SCOPE_BRAND and scope.platform_ids:
        q = q.filter(Organization.platform_id.in_(list(scope.platform_ids)))
    elif not scope._all_organizations:
        return ()
    return tuple(str(row[0]) for row in q.limit(limit).all())
