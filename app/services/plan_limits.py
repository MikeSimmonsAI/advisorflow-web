"""
Plan limits — server-side enforcement of what a subscription actually buys.

═══════════════════════════════════════════════════════════════════════════
THE GAP THIS CLOSES
═══════════════════════════════════════════════════════════════════════════

`brand_billing_plans` has carried `max_leads` and `max_users` since the
catalogue was built. They were returned to the UI, rendered on plan cards, and
ENFORCED BY NOTHING - no query anywhere read them. A Starter customer whose
plan advertised "up to 2 users" could add fifty, and the only thing standing
between them and doing so was a number on a marketing card.

Frontend hiding is not enforcement.

═══════════════════════════════════════════════════════════════════════════
WHICH PLAN'S LIMIT APPLIES, WHICH IS THE WHOLE SUBTLETY
═══════════════════════════════════════════════════════════════════════════

`Organization.billing_plan_key` - what they are entitled to TODAY - and never
`billing_pending_plan_key`.

That distinction is the decided downgrade policy expressed in code. A customer
who schedules a downgrade from Professional to Starter has ALREADY PAID for
Professional through the end of the period. Reading the pending key would take
their fourth and fifth user away the moment they clicked the button, for a
change that has not happened and money they have not saved.

The webhook clears the pending marker when Stripe's schedule actually
advances, and at that moment - not before - this function starts returning the
lower limit.

═══════════════════════════════════════════════════════════════════════════
WHAT THIS DELIBERATELY DOES NOT DO
═══════════════════════════════════════════════════════════════════════════

NO OVERAGE BILLING. Nothing here charges for exceeding a limit, because no
metering or overage policy has been decided and inventing one would be
inventing revenue.

NO RETROACTIVE ENFORCEMENT. An organization already over its limit - because
it downgraded, or because the limit was introduced after they grew - is not
broken, locked, or pruned. Existing records keep working; the limit stops the
NEXT addition. Deleting or disabling a customer's users to make a number fit
is not a billing decision anyone would sanction.

NO LIMIT WHERE NONE IS CONFIGURED. NULL means unlimited, and a brand that has
not set a number gets no ceiling rather than a guessed one.

═══════════════════════════════════════════════════════════════════════════
ONE GUARD, CALLED EVERYWHERE - AND EVERY EXCEPTION NAMED OUT LOUD
═══════════════════════════════════════════════════════════════════════════

`require_capacity()` is the ONLY place a limit is enforced. Every path that
can add a user to a customer organization, or create a lead in one, calls it.
Not the frontend, not each router's own arithmetic - here.

The paths that legitimately do NOT count against a customer's plan still call
it, passing an explicit `bypass=` reason from `BYPASS_REASONS`. That is the
difference between a considered exception and an oversight: a bypass has a
name, is refused unless the caller is privileged enough to use it, writes an
audit row, and is covered by a test. A path that simply never called the guard
would look identical from the outside and mean something entirely different.

`test_plan_limits_coverage.py` walks the source tree and fails if a new
`User(` or `Lead(` construction site appears in a customer path without
reaching this module, so the next such path cannot be added silently.

═══════════════════════════════════════════════════════════════════════════
WHY THE ORGANIZATION ROW IS LOCKED
═══════════════════════════════════════════════════════════════════════════

Count-then-insert is a race. Two requests to add the second user of a 2-user
plan both COUNT 1, both conclude there is room, and both INSERT: three users
on a two-user plan, with neither request having done anything wrong.

So the check takes a row lock on the organization first (`SELECT ... FOR
UPDATE`). Concurrent additions for the SAME organization serialize behind it;
different organizations never contend. The lock is held to the end of the
caller's transaction, which is what makes the count still true at INSERT time.

On SQLite - tests only - `FOR UPDATE` is not supported and is skipped. That is
safe there and nowhere else: SQLite serializes writers itself, and the test
suite uses a single connection.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.models import Organization, User

log = logging.getLogger(__name__)


LIMIT_USERS = "max_users"
LIMIT_LEADS = "max_leads"


# ── The named exceptions ────────────────────────────────────────────────────
#
# A bypass is not "skip the check". It is a DECLARED, PRIVILEGED, AUDITED
# reason that this particular addition is not a customer consuming their plan.
# Each value maps to who is allowed to invoke it.

BYPASS_PLATFORM_PROVISIONING = "platform_provisioning"
BYPASS_DEMO_SEED = "demo_seed"
BYPASS_SYSTEM_MIGRATION = "system_migration"

BYPASS_REASONS = {
    # God/platform staff standing up or repairing a customer. The platform
    # operator is not a customer buying seats; refusing them would mean a
    # customer who has outgrown their plan can never be helped.
    BYPASS_PLATFORM_PROVISIONING: {
        "roles": ("god_admin",),
        "why": "platform operator provisioning or repairing a customer org",
    },
    # Demo and sample fixtures. These rows are disposable and exist to show
    # the product, not because a customer generated business.
    BYPASS_DEMO_SEED: {
        "roles": ("god_admin", "super_admin"),
        "why": "demo/sample fixture data, not customer-generated records",
    },
    # Backfills and data migrations run by the platform. Never reachable from
    # a customer-facing request.
    BYPASS_SYSTEM_MIGRATION: {
        "roles": ("god_admin",),
        "why": "platform data migration or backfill",
    },
}


class LimitBypassDenied(HTTPException):
    """Someone asked for a bypass they are not entitled to use."""

    def __init__(self, detail: str):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _lock_org(db: Session, org: Optional[Organization]) -> None:
    """Serialize concurrent capacity checks for this one organization.

    Held until the caller's transaction ends, which is the point: the count
    taken after this line is still true when the caller INSERTs.
    """
    if org is None:
        return
    try:
        dialect = db.bind.dialect.name if db.bind is not None else ""
    except Exception:
        dialect = ""
    if dialect != "postgresql":
        # SQLite (tests) has no row-level FOR UPDATE and serializes writers
        # itself. Any other dialect: fail open on the LOCK, never on the CHECK
        # - the count below still runs.
        return
    try:
        db.execute(text("SELECT 1 FROM organizations WHERE id = :oid FOR UPDATE"),
                   {"oid": org.id})
    except Exception:                                    # pragma: no cover
        log.warning("plan_limits: could not lock organization %s for capacity "
                    "check; proceeding with an unlocked count", org.id)


def effective_plan(db: Session, org: Optional[Organization]):
    """The plan whose limits apply RIGHT NOW.

    Deliberately `billing_plan_key`, never `billing_pending_plan_key` - see the
    module docstring. Returns None when the organization has no resolvable
    plan, which is a real and common state (never subscribed, mid-provisioning,
    brand catalogue not yet seeded) and must not be treated as "zero of
    everything".
    """
    if org is None:
        return None
    from app.services import billing_catalog
    return billing_catalog.resolve_plan(
        db, getattr(org, "platform_id", None),
        getattr(org, "billing_plan_key", None) or getattr(org, "plan", None))


def current_snapshot(db: Session, org: Optional[Organization]):
    """The live entitlement snapshot for a customer who sits on no catalogue tier.

    A Custom deal bills against an inline Stripe price and therefore leaves the
    organization with no `billing_plan_key` — deliberately. This is where what
    that customer actually bought is recorded. Superseded rows are excluded but
    kept, so a renegotiation does not erase what was true before it.
    """
    if org is None:
        return None
    from app.models.billing_models import CustomerEntitlementSnapshot
    return (db.query(CustomerEntitlementSnapshot)
            .filter(CustomerEntitlementSnapshot.organization_id == org.id,
                    CustomerEntitlementSnapshot.superseded_at.is_(None))
            .order_by(CustomerEntitlementSnapshot.created_at.desc())
            .first())


def _snapshot_unlimited(snapshot) -> list:
    """Dimensions the agreement explicitly says are uncapped."""
    raw = getattr(snapshot, "unlimited_json", None)
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return [k for k in val if isinstance(k, str)] if isinstance(val, list) else []


def limit_for(db: Session, org: Optional[Organization], key: str) -> Optional[int]:
    """The configured ceiling, or None for unlimited / unconfigured.

    RESOLUTION ORDER, and the reason for it:

      1. THE CATALOGUE PLAN, where the organization is on one. Unchanged.
      2. THE ENTITLEMENT SNAPSHOT, for a customer who is not — a Custom deal.
         Without this step such a customer has no ceiling on anything, which is
         the one category of customer whose terms were individually negotiated
         and so the last place an accidental "unlimited" belongs.

    NULL still returns None at both levels, and that is deliberate: this
    function answers "what is the ceiling", and it must not start refusing
    things for organizations that have never had one. Whether a NULL means
    *agreed unlimited* or *nobody has decided* is a different question, and
    `entitlement_state()` is where it gets a truthful answer rather than being
    collapsed into an enforcement decision nobody made.
    """
    plan = effective_plan(db, org)
    source = plan
    if source is None:
        source = current_snapshot(db, org)
    if source is None:
        return None
    value = getattr(source, key, None)
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


# Every ceiling a snapshot can record. Named here so `entitlement_state` reports
# the same list the God write path accepts, and a new dimension cannot be added
# to one without the other noticing.
SNAPSHOT_DIMENSIONS = (
    "max_leads", "max_users", "max_locations",
    "sms_monthly_allowance", "voice_minutes_monthly_allowance",
    "email_monthly_allowance",
)

ENTITLEMENT_CATALOGUE = "catalogue_plan"
ENTITLEMENT_SNAPSHOT = "custom_agreement_snapshot"
ENTITLEMENT_NONE = "unset"


def entitlement_state(db: Session, org: Optional[Organization]) -> dict:
    """WHAT THIS CUSTOMER IS ENTITLED TO, AND HOW CONFIDENTLY WE KNOW IT.

    Separate from `limit_for` on purpose. `limit_for` answers a yes/no question
    at the moment somebody adds a user; this answers the reporting question — is
    this customer on a tier, on a negotiated agreement, or on nothing at all,
    and which dimensions has nobody actually decided?

    THE DISTINCTION THAT MATTERS. A NULL ceiling has two completely different
    meanings and no enforcement path can tell them apart:

        agreed as uncapped   — a real commercial term, named in `unlimited_json`
        never recorded       — nobody decided, and it shows up in
                               `unset_dimensions` as NEEDS CONFIGURATION

    Reporting them as one thing is how a Custom customer ends up quietly
    unlimited and nobody notices until they are running 40,000 leads on a deal
    that agreed 5,000.
    """
    if org is None:
        return {"source": ENTITLEMENT_NONE, "limits": {},
                "unset_dimensions": list(SNAPSHOT_DIMENSIONS),
                "agreed_unlimited": [], "policy_required": True,
                "explanation": "No organization."}

    plan = effective_plan(db, org)
    if plan is not None:
        limits = {k: getattr(plan, k, None) for k in ("max_leads", "max_users")}
        return {
            "source": ENTITLEMENT_CATALOGUE,
            "plan_key": plan.key,
            "limits": limits,
            # A catalogue tier's NULL genuinely is "unlimited" — it is the
            # brand's own published rate card, and the brand decided.
            "unset_dimensions": [],
            "agreed_unlimited": [k for k, v in limits.items() if v is None],
            "policy_required": False,
            "explanation": "On the %s tier; its published ceilings apply." % plan.key,
        }

    snap = current_snapshot(db, org)
    if snap is None:
        return {
            "source": ENTITLEMENT_NONE,
            "plan_key": None,
            "limits": {},
            "unset_dimensions": list(SNAPSHOT_DIMENSIONS),
            "agreed_unlimited": [],
            "policy_required": True,
            "explanation": (
                "This customer is on no catalogue tier and has no recorded "
                "entitlement agreement, so no ceiling applies to anything. That "
                "is NEEDS CONFIGURATION, not a decision — record what the deal "
                "actually agreed."),
        }

    unlimited = _snapshot_unlimited(snap)
    limits = {k: getattr(snap, k, None) for k in SNAPSHOT_DIMENSIONS}
    unset = [k for k in SNAPSHOT_DIMENSIONS
             if limits.get(k) is None and k not in unlimited]
    return {
        "source": ENTITLEMENT_SNAPSHOT,
        "plan_key": None,
        "snapshot_id": snap.id,
        "opportunity_id": snap.opportunity_id,
        "limits": limits,
        "unset_dimensions": unset,
        "agreed_unlimited": unlimited,
        "policy_required": bool(unset),
        "explanation": (
            "Entitlements come from this customer's own agreement, not a "
            "catalogue tier."
            + (" Not yet recorded: %s." % ", ".join(unset) if unset else "")),
    }


def usage_for(db: Session, org: Organization, key: str) -> int:
    """How many they are using today. Counts only what the limit is about."""
    if key == LIMIT_USERS:
        # A SEAT IS A PERSON WITH ACCESS, NOT A ROW IN `users`.
        #
        # Counting `User.organization_id` alone missed an entire second door.
        # `workspace_access.grant_workspace_membership` gives somebody a live
        # SCOPE_CUSTOMER_ORG membership in a customer's workspace while their
        # `User.organization_id` still points somewhere else - a brand-sales
        # person working inside a customer, most obviously. They log in, they
        # work the customer's leads, and under the old count they were free.
        #
        # So both are counted, DISTINCT: a person homed in the org who also
        # holds a membership to it is one seat, not two.
        #
        # ACTIVE only, on both sides. A deactivated account or a revoked
        # membership is not consuming a seat, and counting them would mean an
        # organization could never recover from its ceiling except by deleting
        # people.
        from app.models.sales_models import Membership, SCOPE_CUSTOMER_ORG

        homed = {r[0] for r in db.query(User.id)
                 .filter(User.organization_id == org.id,
                         User.is_active == True)             # noqa: E712
                 .all()}

        seconded = {r[0] for r in db.query(Membership.user_id)
                    .join(User, User.id == Membership.user_id)
                    .filter(Membership.scope_type == SCOPE_CUSTOMER_ORG,
                            Membership.scope_id == org.id,
                            Membership.is_active == True,    # noqa: E712
                            User.is_active == True)          # noqa: E712
                    .all()}

        return len(homed | seconded)
    if key == LIMIT_LEADS:
        # HELD LEADS DO NOT CONSUME THE PLAN.
        #
        # An inbound prospect that arrived while the customer was at their
        # ceiling is kept rather than dropped (lead_capacity), but it is not
        # something they are using - they cannot text it, mail it, or work it.
        # Counting it would produce "2,600 of 2,500 used", a number the
        # customer can neither act on nor reduce, and would mean the ceiling
        # stopped meaning anything the moment it was crossed.
        #
        # `IS NOT DISTINCT FROM NULL` in ORM form: every pre-existing row has
        # capacity_state NULL and must keep counting.
        from app.models.models import Lead
        from app.services.lead_capacity import OVER_CAPACITY
        return (db.query(Lead)
                .filter(Lead.organization_id == org.id)
                .filter((Lead.capacity_state.is_(None))
                        | (Lead.capacity_state != OVER_CAPACITY))
                .count())
    return 0


def check(db: Session, org: Optional[Organization], key: str,
          adding: int = 1) -> dict:
    """Would adding `adding` more exceed this plan's ceiling?

    Returns a dict rather than a bool so a caller can report the real numbers.
    "You have reached your plan's limit of 2 users" is actionable; "forbidden"
    is not.
    """
    limit = limit_for(db, org, key)
    if limit is None or org is None:
        return {"allowed": True, "limit": None, "used": None, "unlimited": True}

    used = usage_for(db, org, key)
    return {
        "allowed": (used + adding) <= limit,
        "limit": limit,
        "used": used,
        "adding": adding,
        "unlimited": False,
    }


def _authorize_bypass(db: Session, org: Optional[Organization], key: str,
                      adding: int, bypass: str, actor) -> None:
    """A bypass is a privilege, not a keyword. Check it, then record it.

    Refusing an unknown reason matters as much as refusing an unprivileged
    caller: a typo'd bypass string that silently disabled the limit would be
    exactly the accidental hole this design exists to prevent.
    """
    spec = BYPASS_REASONS.get(bypass)
    if spec is None:
        raise LimitBypassDenied(
            "Unknown plan-limit bypass reason %r. Bypasses must be one of: %s."
            % (bypass, ", ".join(sorted(BYPASS_REASONS))))

    role = getattr(actor, "role", None)
    if role not in spec["roles"]:
        raise LimitBypassDenied(
            "Bypassing the plan limit for %r requires one of: %s."
            % (bypass, ", ".join(spec["roles"])))

    # AUDITED. A bypass that left no trace would be indistinguishable from a
    # path that never enforced anything, which is the whole point of naming it.
    try:
        from app.routers.audit_log_router import log_action
        log_action(
            db,
            organization_id=getattr(org, "id", None),
            actor_user_id=getattr(actor, "id", None),
            action="plan_limit.bypass",
            target_type="organization",
            target_id=getattr(org, "id", None),
            details={"limit": key, "adding": adding, "reason": bypass,
                     "why": spec["why"], "actor_role": role,
                     "plan": getattr(effective_plan(db, org), "key", None)},
            platform_id=getattr(org, "platform_id", None),
            # NOT commit=True. This runs inside the caller's transaction, which
            # is still holding the organization row lock and has not yet
            # written the thing being audited. Committing here would publish a
            # half-finished provision and drop the lock early.
            commit=False,
        )
    except Exception:                                    # pragma: no cover
        # An audit failure must not become a data-loss failure mid-provision,
        # but it must be loud.
        log.exception("plan_limits: FAILED TO AUDIT bypass %r on org %s",
                      bypass, getattr(org, "id", None))


def require_capacity(db: Session, org: Optional[Organization], key: str,
                     adding: int = 1, *, bypass: Optional[str] = None,
                     actor=None) -> None:
    """THE enforcement point. Refuse the addition if it exceeds the plan.

    402 Payment Required rather than 403 Forbidden, matching the entitlement
    gate this sits beside: the caller is not unauthorized, their PLAN does not
    include this. The two are different problems with different fixes, and
    telling somebody they lack permission when they actually need a bigger plan
    sends them to the wrong person.

    `bypass` names a declared exception from BYPASS_REASONS and requires
    `actor` to hold one of that reason's roles. It is checked and audited
    BEFORE the limit is consulted, so an unprivileged caller cannot learn
    anything by passing one, and a legitimate bypass is recorded whether or not
    the organization was actually near its ceiling.
    """
    if bypass is not None:
        _authorize_bypass(db, org, key, adding, bypass, actor)
        return

    # Lock FIRST. A count taken before the lock is a count that can go stale
    # between the check and the caller's INSERT.
    _lock_org(db, org)

    result = check(db, org, key, adding=adding)
    if result["allowed"]:
        return

    noun = "users" if key == LIMIT_USERS else "leads"
    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail=("This plan includes up to %d %s and %d are already in use. "
                "Upgrade the plan to add more."
                % (result["limit"], noun, result["used"])))


class CapacityCounter:
    """Exact per-row enforcement for a batch, at the cost of ONE count.

    Imports are the awkward case. Calling `require_capacity` per row would
    re-COUNT the whole lead table for every line of a ten-thousand-row
    spreadsheet. Calling it once with the row count would refuse imports that
    are mostly duplicates and create nothing.

    So the count is taken once, up front, under the same organization row lock,
    and then decremented as rows are actually created. The result is the same
    number the per-row check would have produced, and the partial import that
    precedes the refusal is real work the caller keeps - consistent with the
    "no retroactive enforcement" rule: the limit stops the NEXT addition, it
    does not roll back what fit.
    """

    def __init__(self, db: Session, org: Optional[Organization], key: str):
        self.db = db
        self.org = org
        self.key = key
        _lock_org(db, org)
        self.limit = limit_for(db, org, key)
        self.used = usage_for(db, org, key) if (self.limit is not None and org) else 0

    @property
    def unlimited(self) -> bool:
        return self.limit is None

    @property
    def remaining(self) -> Optional[int]:
        if self.unlimited:
            return None
        return max(0, self.limit - self.used)

    def has_room(self, n: int = 1) -> bool:
        return self.unlimited or (self.used + n) <= self.limit

    def take(self, n: int = 1) -> None:
        """Claim capacity for `n` rows about to be created, or raise 402."""
        if self.unlimited:
            return
        if (self.used + n) > self.limit:
            noun = "users" if self.key == LIMIT_USERS else "leads"
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=("This plan includes up to %d %s and %d are in use. "
                        "The remaining records were not imported. Upgrade the "
                        "plan to add more."
                        % (self.limit, noun, self.used)))
        self.used += n


def counter_for_org_id(db: Session, org_id: Optional[str], key: str) -> CapacityCounter:
    """A CapacityCounter for the many batch paths that hold an id, not the row."""
    org = None
    if org_id:
        org = db.query(Organization).filter(Organization.id == org_id).first()
    return CapacityCounter(db, org, key)


def require_capacity_for_org_id(db: Session, org_id: Optional[str], key: str,
                                adding: int = 1, *, bypass: Optional[str] = None,
                                actor=None) -> None:
    """Same guard, for the many service paths that hold an id, not the row.

    Exists so those paths do not each write their own `db.query(Organization)`
    lookup - and, more importantly, so none of them decide that a missing
    organization means "no limit, carry on" by accident. A NULL org_id is a
    platform-scoped record and genuinely has no customer plan; that is the one
    case this returns quietly.
    """
    if not org_id:
        return
    org = db.query(Organization).filter(Organization.id == org_id).first()
    require_capacity(db, org, key, adding=adding, bypass=bypass, actor=actor)


def report(db: Session, org: Optional[Organization]) -> dict:
    """Every limit and its usage, for the Billing screen and God Mode.

    Also reports the PENDING plan's limits where one is scheduled, so a
    customer can see what they will drop to before it happens rather than
    discovering it when an action starts failing.
    """
    plan = effective_plan(db, org)
    out = {
        "plan": getattr(plan, "key", None),
        "limits": {},
        "pending_plan": getattr(org, "billing_pending_plan_key", None) if org else None,
        "pending_effective_at": getattr(org, "billing_pending_effective_at", None) if org else None,
        "pending_limits": {},
    }
    for key in (LIMIT_USERS, LIMIT_LEADS):
        out["limits"][key] = check(db, org, key, adding=0)

    # Held inbound prospects, reported SEPARATELY from usage. They are not
    # consuming the plan, but the customer must be able to see that real
    # business is waiting on an upgrade - a held lead nobody is told about is
    # barely better than a dropped one.
    try:
        from app.services import lead_capacity
        out["capacity_hold"] = lead_capacity.report(db, org)
    except Exception:                                    # pragma: no cover
        out["capacity_hold"] = {"held": 0, "oldest_held_at": None}

    pending_key = out["pending_plan"]
    if pending_key and org is not None:
        from app.services import billing_catalog
        pending = billing_catalog.resolve_plan(
            db, getattr(org, "platform_id", None), pending_key)
        if pending is not None:
            for key in (LIMIT_USERS, LIMIT_LEADS):
                value = getattr(pending, key, None)
                out["pending_limits"][key] = {
                    "limit": int(value) if value else None,
                    "used": usage_for(db, org, key),
                    "unlimited": value is None,
                }
    return out
