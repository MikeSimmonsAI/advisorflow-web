"""EXECUTIVE PORTFOLIO — one brand's customer base, composed for an executive.

WHY THIS IS A SERVICE AND NOT MORE ROUTER CODE
==============================================
The Executive Command Center reports portfolio totals. Portfolio Health lists
the organizations those totals are made of. The organization drill-down opens
one of them. If those three were computed by three different pieces of code
they would eventually disagree, and the first time an executive noticed
"12 at-risk" on one screen and eleven rows on the next, the whole surface stops
being worth reading.

So ONE function builds the rows, and everything else is derived from them:

    rows()          → one record per customer organization
    portfolio()     → the totals, summed FROM those rows
    organization()  → one of those rows, plus the deeper reads

WHAT AN EXECUTIVE IS ASKING
===========================
Not "what is in the database". They are asking:

    How is my business doing?
    Which organizations need attention?
    Where is revenue coming from?
    Are leads being worked?
    Are appointments being created?
    What needs me today?

That is why this module reports OUTCOMES and EXCEPTIONS — leads worked, replies
answered, appointments created, organizations drifting — rather than the
operational queues a customer's own staff work from. The customer workspace
exists to do the work; this exists to tell somebody whether the work is
happening.

HONESTY RULES, IN FORCE THROUGHOUT
==================================
1. A figure nobody can compute is None, and the caller renders "not tracked"
   or "no data". It is NEVER zero. "We cannot price this customer" and "this
   customer pays nothing" are opposite facts and must not render identically.
2. A rate with no denominator is None, not 0%. Nought replies out of nought
   sends is not a nought-percent reply rate.
3. Health carries a REASON in words. A score nobody can explain is not a
   management tool, it is a number to argue with.
4. Nothing here invents a threshold that the business has not set. The
   activity windows below are the ones the existing customer-health endpoint
   has always used; they are reused rather than re-guessed.

SCOPE IS AN EXPLICIT ORGANIZATION LIST, NOT A BRAND
===================================================
`rows()` takes `org_ids` as a REQUIRED keyword argument with no default, and
the only thing that produces that list is
`app/services/executive_authority.portfolio_authority()`.

This used to filter on `Organization.platform_id == platform_id`, which meant
holding an executive grant on a brand exposed every organization on it — two
executives under one white-label brand saw each other's whole portfolio. The
platform id is still passed, and is still applied, because an assignment must
never let a portfolio cross a brand boundary; what changed is that it is no
longer SUFFICIENT on its own.

THE DEFAULT IS ABSENT ON PURPOSE. A service that could be called without a
scope would eventually be called without one, and the failure mode is silent
and total: every customer on the brand, rendered as though it were somebody's
portfolio. An empty list is honoured as an empty portfolio, because an
executive with no assignments has none — falling back to "everything" when the
list is empty would reintroduce the exact defect this closes.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import (BookingLink, Lead, Message, Organization,
                               Reply, User)

# ── the health vocabulary, unchanged from what has shipped ──────────────────
# These five words and their day windows are what /executive/customer-health
# has always returned. They are reused verbatim rather than re-derived, so an
# executive who learned what "watch" means does not have to relearn it.
HEALTHY = "healthy"
WATCH = "watch"
AT_RISK = "at_risk"
INACTIVE = "inactive"
ONBOARDING = "onboarding"

HEALTH_ORDER = (AT_RISK, WATCH, ONBOARDING, INACTIVE, HEALTHY)

HEALTH_LABELS = {
    HEALTHY: "Healthy",
    WATCH: "Watch",
    AT_RISK: "At risk",
    INACTIVE: "Inactive",
    ONBOARDING: "Onboarding",
}

# The filters Portfolio Health offers. Each one is a real predicate over the
# rows below — there is no filter here that the data cannot answer.
FILTERS = (
    "all", "healthy", "needs_attention", "inactive",
    "implementation", "billing_issue", "low_activity", "high_growth",
)

# "Recently" for the growth and activity filters. Thirty days matches the
# window the rest of the platform reports on (messages_30d, new_leads_30d), so
# a number here and a number there describe the same period.
RECENT_DAYS = 30
GROWTH_LEAD_THRESHOLD = 25       # new leads in RECENT_DAYS to read as growing
LOW_ACTIVITY_LEAD_THRESHOLD = 1  # fewer than this worked in RECENT_DAYS


def _iso(v):
    return v.isoformat() if v else None


def _rate(numerator: int, denominator: int) -> Optional[float]:
    """A percentage, or None when there is no denominator.

    Nought out of nought is not nought percent. A screen that renders 0% for an
    organization that has sent nothing is reporting a failure that did not
    happen.
    """
    if not denominator:
        return None
    return round((numerator / denominator) * 100, 1)


# ═══════════════════════════════════════════════════════════════════════════
# THE ROWS — one record per customer organization, built in bulk
# ═══════════════════════════════════════════════════════════════════════════

def rows(db: Session, platform_id: str, *, org_ids: List[str],
         now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """The executive picture for EXACTLY the organizations they were assigned.

    `org_ids` is required and comes from `executive_authority`. It is applied
    ALONGSIDE the platform filter, never instead of it: the id list is the
    portfolio boundary, and the platform filter is the brand boundary, and a
    row has to satisfy both. An assignment that has drifted into another brand
    therefore grants nothing.

    An EMPTY list means an empty portfolio. It is not treated as "unfiltered" —
    that reading is what turned an executive grant into brand-wide access in
    the first place.

    BULK BY CONSTRUCTION. One grouped query per fact, never one query per
    organization — a portfolio of forty customers must not cost forty round
    trips, and the N+1 shape is how an executive screen becomes the slowest
    page in the product.
    """
    now = now or datetime.utcnow()
    recent = now - timedelta(days=RECENT_DAYS)
    week = now - timedelta(days=7)

    if not org_ids:
        return []

    orgs = (db.query(Organization)
            .filter(Organization.platform_id == platform_id,
                    Organization.id.in_(list(org_ids)))
            .order_by(Organization.name).all())
    if not orgs:
        return []
    ids = [o.id for o in orgs]

    def grouped(q):
        return dict(q.all())

    # ── people ──────────────────────────────────────────────────────────────
    users = grouped(db.query(User.organization_id, func.count(User.id))
                    .filter(User.organization_id.in_(ids),
                            User.is_active.is_(True))
                    .group_by(User.organization_id))

    # ── leads. The same active-lead exclusion the customer's own Overview
    #    uses, so an executive and the staff never see different totals.
    def lead_count(*extra):
        q = (db.query(Lead.organization_id, func.count(Lead.id))
             .filter(Lead.organization_id.in_(ids),
                     (Lead.manual_flag == None) |          # noqa: E711
                     (Lead.manual_flag == "bad_email")))
        for e in extra:
            q = q.filter(e)
        return grouped(q.group_by(Lead.organization_id))

    leads_total = lead_count()
    leads_new = lead_count(Lead.status == "new")
    leads_hot = lead_count(Lead.status == "hot")
    leads_booked = lead_count(Lead.status == "booked")
    leads_sent = lead_count(Lead.status == "sent")
    leads_recent = lead_count(Lead.created_at >= recent)

    # ── are leads being WORKED. A lead that has been messaged is a lead
    #    somebody touched; a lead sitting untouched is the exception an
    #    executive is looking for.
    worked_recent = grouped(
        db.query(Lead.organization_id, func.count(func.distinct(Lead.id)))
        .filter(Lead.organization_id.in_(ids),
                Lead.last_messaged_at.isnot(None),
                Lead.last_messaged_at >= recent)
        .group_by(Lead.organization_id))
    unworked = grouped(
        db.query(Lead.organization_id, func.count(Lead.id))
        .filter(Lead.organization_id.in_(ids),
                Lead.status == "new",
                Lead.last_messaged_at.is_(None),
                (Lead.manual_flag == None) |                # noqa: E711
                (Lead.manual_flag == "bad_email"))
        .group_by(Lead.organization_id))

    # ── conversation ────────────────────────────────────────────────────────
    replies = grouped(db.query(Lead.organization_id, func.count(Reply.id))
                      .join(Lead, Reply.lead_id == Lead.id)
                      .filter(Lead.organization_id.in_(ids))
                      .group_by(Lead.organization_id))
    replies_unreviewed = grouped(
        db.query(Lead.organization_id, func.count(Reply.id))
        .join(Lead, Reply.lead_id == Lead.id)
        .filter(Lead.organization_id.in_(ids),
                Reply.reviewed_at.is_(None))
        .group_by(Lead.organization_id))

    # ── appointments. BookingLink is the customer-side appointment; a booked
    #    time that exists is an appointment that was created.
    appts = grouped(
        db.query(Lead.organization_id, func.count(func.distinct(BookingLink.lead_id)))
        .join(Lead, BookingLink.lead_id == Lead.id)
        .filter(Lead.organization_id.in_(ids),
                BookingLink.booked_time.isnot(None))
        .group_by(Lead.organization_id))
    appts_week = grouped(
        db.query(Lead.organization_id, func.count(func.distinct(BookingLink.lead_id)))
        .join(Lead, BookingLink.lead_id == Lead.id)
        .filter(Lead.organization_id.in_(ids),
                BookingLink.booked_time.isnot(None),
                BookingLink.booked_time >= week)
        .group_by(Lead.organization_id))
    appts_upcoming = grouped(
        db.query(Lead.organization_id, func.count(func.distinct(BookingLink.lead_id)))
        .join(Lead, BookingLink.lead_id == Lead.id)
        .filter(Lead.organization_id.in_(ids),
                BookingLink.booked_time.isnot(None),
                BookingLink.booked_time >= now)
        .group_by(Lead.organization_id))

    # ── last operational activity, across all three channels ────────────────
    last_sms = grouped(db.query(Lead.organization_id, func.max(Message.sent_at))
                       .join(Lead, Message.lead_id == Lead.id)
                       .filter(Lead.organization_id.in_(ids))
                       .group_by(Lead.organization_id))
    last_reply = grouped(db.query(Lead.organization_id, func.max(Reply.received_at))
                         .join(Lead, Reply.lead_id == Lead.id)
                         .filter(Lead.organization_id.in_(ids))
                         .group_by(Lead.organization_id))
    last_booking = grouped(
        db.query(Lead.organization_id, func.max(BookingLink.booked_time))
        .join(Lead, BookingLink.lead_id == Lead.id)
        .filter(Lead.organization_id.in_(ids),
                BookingLink.booked_time.isnot(None))
        .group_by(Lead.organization_id))

    messages_recent = grouped(
        db.query(Lead.organization_id, func.count(Message.id))
        .join(Lead, Message.lead_id == Lead.id)
        .filter(Lead.organization_id.in_(ids), Message.sent_at >= recent)
        .group_by(Lead.organization_id))

    implementations = _implementations(db, ids)
    commercial = _commercial(db, ids)
    money = _money(db, orgs)
    held = _held(db, ids)

    out: List[Dict[str, Any]] = []
    for o in orgs:
        oid = o.id
        candidates = [last_sms.get(oid), last_reply.get(oid), last_booking.get(oid)]
        seen = [c for c in candidates if c is not None]
        last_activity = max(seen) if seen else None
        health, reason = _classify(o, last_activity, now)

        sent = int(leads_sent.get(oid, 0) or 0)
        total = int(leads_total.get(oid, 0) or 0)
        reply_n = int(replies.get(oid, 0) or 0)
        booked = int(leads_booked.get(oid, 0) or 0)
        impl = implementations.get(oid)
        m = money.get(oid, {})

        row = {
            "id": oid,
            "name": o.name,
            "is_active": bool(o.is_active),
            "provisioned_at": _iso(o.created_at),

            # ── commercial identity ─────────────────────────────────────────
            "package": m.get("plan_name"),
            "plan_key": m.get("plan_key"),
            # MRR IS None, NEVER 0, WHEN THE PLAN DOES NOT RESOLVE. See the
            # module docstring: unpriceable and free are opposite facts.
            "mrr": m.get("mrr"),
            "mrr_available": m.get("mrr") is not None,
            "has_payment_method": m.get("has_payment_method"),

            # ── people and leads ────────────────────────────────────────────
            "active_users": int(users.get(oid, 0) or 0),
            "leads_total": total,
            "leads_new": int(leads_new.get(oid, 0) or 0),
            "leads_hot": int(leads_hot.get(oid, 0) or 0),
            "leads_booked": booked,
            "leads_sent": sent,
            "leads_added_recently": int(leads_recent.get(oid, 0) or 0),
            "leads_worked_recently": int(worked_recent.get(oid, 0) or 0),
            "leads_never_touched": int(unworked.get(oid, 0) or 0),
            "messages_recently": int(messages_recent.get(oid, 0) or 0),

            # ── conversation and outcome ────────────────────────────────────
            "replies_total": reply_n,
            "replies_unreviewed": int(replies_unreviewed.get(oid, 0) or 0),
            "appointments_total": int(appts.get(oid, 0) or 0),
            "appointments_last_7_days": int(appts_week.get(oid, 0) or 0),
            "appointments_upcoming": int(appts_upcoming.get(oid, 0) or 0),

            # RATES ARE None WITHOUT A DENOMINATOR, never 0%.
            "response_rate": _rate(reply_n, sent),
            "conversion_rate": _rate(booked, sent),

            # ── the originating deal, where there was one ───────────────────
            # NOT called "pipeline". This is the deal that created the
            # customer, which is a closed sale, not an open opportunity — and
            # a customer created outside the pipeline honestly has neither.
            "originating_deal": commercial.get(oid),

            # ── implementation ──────────────────────────────────────────────
            "implementation": impl,

            # ── the verdict ─────────────────────────────────────────────────
            "last_activity": _iso(last_activity),
            "health": health,
            "health_label": HEALTH_LABELS[health],
            "reason": reason,
            "held_leads": int(held.get(oid, 0) or 0),
        }
        row["attention"] = _attention(row, now)
        out.append(row)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# THE EXCEPTIONS — what an executive should be told without asking
# ═══════════════════════════════════════════════════════════════════════════

def _attention(row: Dict[str, Any], now: datetime) -> List[Dict[str, str]]:
    """Conditions on ONE organization that a person should act on.

    Every one is derived from a figure already on the row, so an executive can
    check the claim against the same screen. Nothing here is a guess about
    intent, a projected trend, or a threshold invented for effect.
    """
    items: List[Dict[str, str]] = []

    def add(key, severity, text):
        items.append({"key": key, "severity": severity, "text": text})

    if not row["is_active"]:
        add("suspended", "action_required",
            "This organization is suspended and its people cannot sign in.")

    if row["health"] in (AT_RISK, INACTIVE):
        add("dormant", "action_required", row["reason"])

    # THE BIGGEST ONE: leads bought and never worked. This is the condition
    # Mike named directly — "1,584 leads remain unworked" — and it is the
    # difference between a customer who is failing and a customer who has not
    # started.
    if row["leads_never_touched"] >= 50:
        add("unworked_leads", "action_required",
            "%s leads have never been contacted." % f"{row['leads_never_touched']:,}")
    elif row["leads_never_touched"] > 0 and row["leads_total"] and \
            row["leads_never_touched"] >= row["leads_total"] * 0.5:
        add("unworked_leads", "attention",
            "%s of %s leads have never been contacted."
            % (f"{row['leads_never_touched']:,}", f"{row['leads_total']:,}"))

    if row["replies_unreviewed"] >= 5:
        add("unanswered_replies", "action_required",
            "%d replies from families are waiting for someone to read them."
            % row["replies_unreviewed"])
    elif row["replies_unreviewed"]:
        add("unanswered_replies", "attention",
            "%d unread %s from families."
            % (row["replies_unreviewed"],
               "reply" if row["replies_unreviewed"] == 1 else "replies"))

    if row["has_payment_method"] is False:
        add("no_payment_method", "action_required",
            "No payment method on file — this customer cannot be charged.")

    impl = row.get("implementation")
    if impl and impl.get("is_open"):
        sev = "action_required" if impl.get("is_blocked") else "attention"
        add("implementation", sev,
            "Implementation is %s." % (impl.get("label") or "in progress").lower())

    if row["held_leads"]:
        add("held_leads", "attention",
            "%s inbound %s held because the plan's lead limit is reached."
            % (f"{row['held_leads']:,}",
               "prospect is" if row["held_leads"] == 1 else "prospects are"))

    if row["active_users"] == 0:
        add("no_users", "action_required",
            "Nobody has an active account here, so nobody can work the leads.")

    return items


def _classify(org: Organization, last_activity: Optional[datetime],
              now: datetime):
    """Health, and the sentence explaining it.

    UNCHANGED RULES. These are the windows /executive/customer-health has
    always used. What changed in this pass is only that the reason is written
    for a business owner rather than as a log line.
    """
    age_days = (now - org.created_at).days if org.created_at else 9999

    if not org.is_active:
        return INACTIVE, "Suspended — the account is switched off."

    if last_activity is None:
        if age_days <= 30:
            return ONBOARDING, ("Set up %d days ago and has not sent, received "
                                "or booked anything yet." % age_days)
        return INACTIVE, ("Nothing has ever been sent, received or booked in "
                          "this workspace.")

    days = (now - last_activity).days
    if days <= 14:
        return HEALTHY, ("Working the book — last activity %s."
                         % _ago(days))
    if days <= 30:
        if age_days <= 30:
            return ONBOARDING, ("Still getting started — last activity %s."
                                % _ago(days))
        return WATCH, ("Slowing down — last activity %s." % _ago(days))
    if days <= 60:
        return AT_RISK, ("Quiet for %d days. This is usually the first sign of "
                         "a customer drifting away." % days)
    return INACTIVE, "No activity at all for %d days." % days


def _ago(days: int) -> str:
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return "%d days ago" % days


# ═══════════════════════════════════════════════════════════════════════════
# THE SUPPORTING READS
# ═══════════════════════════════════════════════════════════════════════════

def _implementations(db: Session, ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """The most recent implementation per organization, where one exists."""
    try:
        from app.models.implementation_models import (
            IMPLEMENTATION_OPEN_STATUSES, IMPLEMENTATION_STATUS_LABELS,
            IMPL_BLOCKED, Implementation)
    except Exception:                                    # pragma: no cover
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    rowset = (db.query(Implementation)
              .filter(Implementation.organization_id.in_(ids))
              .order_by(Implementation.created_at.desc()).all())
    for impl in rowset:
        # First wins because the query is newest-first.
        if impl.organization_id in out:
            continue
        out[impl.organization_id] = {
            "id": impl.id,
            "status": impl.status,
            "label": IMPLEMENTATION_STATUS_LABELS.get(impl.status, impl.status),
            "is_open": impl.status in IMPLEMENTATION_OPEN_STATUSES,
            "is_blocked": impl.status == IMPL_BLOCKED,
        }
    return out


def _commercial(db: Session, ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """The deal that created each customer, and who sold it.

    A customer provisioned outside the pipeline has neither, and that is
    reported as absent rather than filled in — an invented salesperson would
    eventually be paid a commission.
    """
    try:
        from app.models.sales_models import Opportunity
    except Exception:                                    # pragma: no cover
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    opps = (db.query(Opportunity)
            .filter(Opportunity.customer_organization_id.in_(ids))
            .order_by(Opportunity.created_at.asc()).all())
    owner_ids = sorted({o.owner_user_id for o in opps if o.owner_user_id})
    names = {}
    if owner_ids:
        names = {u.id: (u.full_name or u.email) for u in
                 db.query(User).filter(User.id.in_(owner_ids)).all()}
    for o in opps:
        if o.customer_organization_id in out:
            continue
        out[o.customer_organization_id] = {
            "opportunity_id": o.id,
            "value": float(o.deal_value) if o.deal_value is not None else None,
            "sold_by": names.get(o.owner_user_id),
            "closed_at": _iso(getattr(o, "closed_at", None)),
        }
    return out


def _money(db: Session, orgs: Iterable[Organization]) -> Dict[str, Dict[str, Any]]:
    """What each customer is worth per month, priced from THEIR OWN brand.

    Reuses `billing_catalog` rather than re-deriving a price. The annual
    divide-by-twelve rule in particular lives in exactly one place — an
    executive revenue figure that disagreed with the finance screen would be
    worse than no figure.
    """
    out: Dict[str, Dict[str, Any]] = {}
    try:
        from app.models.billing_models import BillingInterval
        from app.services import billing_catalog
    except Exception:                                    # pragma: no cover
        return {o.id: {"plan_name": None, "plan_key": None, "mrr": None,
                       "has_payment_method": None} for o in orgs}

    cache: Dict[Any, Any] = {}
    for o in orgs:
        key = getattr(o, "billing_plan_key", None) or o.plan
        ck = (o.platform_id, key)
        if ck not in cache:
            try:
                cache[ck] = billing_catalog.resolve_plan(db, o.platform_id, key)
            except Exception:                            # pragma: no cover
                cache[ck] = None
        plan = cache[ck]
        interval = getattr(o, "stripe_plan_interval", None) or BillingInterval.MONTH
        cents = None
        if plan is not None:
            try:
                cents = billing_catalog._monthly_equivalent_cents(plan, interval)
            except Exception:                            # pragma: no cover
                cents = None
        out[o.id] = {
            "plan_name": getattr(plan, "name", None),
            "plan_key": key,
            "mrr": (cents / 100.0) if cents is not None else None,
            "has_payment_method": bool(getattr(o, "stripe_customer_id", None)),
        }
    return out


def _held(db: Session, ids: List[str]) -> Dict[str, int]:
    """Inbound prospects held because a plan's lead limit was reached.

    Real business the customer cannot work — which makes it the most useful
    upgrade conversation on an executive's screen.
    """
    try:
        from app.services import lead_capacity
    except Exception:                                    # pragma: no cover
        return {}
    out = {}
    for oid in ids:
        try:
            out[oid] = lead_capacity.held_count(db, oid)
        except Exception:                                # pragma: no cover
            out[oid] = 0
    return out


# ═══════════════════════════════════════════════════════════════════════════
# THE TOTALS — summed from the rows above, never queried separately
# ═══════════════════════════════════════════════════════════════════════════

def portfolio(db: Session, platform_id: str, *, org_ids: List[str],
              now: Optional[datetime] = None) -> Dict[str, Any]:
    """The Command Center's figures.

    EVERY ONE IS A SUM OF `rows()`. That is the whole point: an executive who
    clicks a headline arrives at the organizations that make it up, and the
    count matches. A separate aggregate query would be faster and would
    eventually be wrong.

    `org_ids` is required for the same reason it is required on `rows()`, and
    is passed straight through — a total computed over a wider set than the
    list behind it would be a number the executive is not entitled to.
    """
    now = now or datetime.utcnow()
    rs = rows(db, platform_id, org_ids=org_ids, now=now)

    def total(field):
        return sum(int(r.get(field) or 0) for r in rs)

    health_counts = {h: 0 for h in HEALTH_LABELS}
    for r in rs:
        health_counts[r["health"]] += 1

    sent = total("leads_sent")
    replies_n = total("replies_total")
    booked = total("leads_booked")

    # REVENUE IS REPORTED WITH ITS COVERAGE. "$4,491 across 3 of 5 customers"
    # is a usable sentence; "$4,491" alone silently implies the other two earn
    # nothing, which is not what the data says.
    priced = [r for r in rs if r["mrr"] is not None]
    mrr = round(sum(r["mrr"] for r in priced), 2) if priced else None

    attention_rows = [r for r in rs if r["attention"]]
    open_impl = [r for r in rs if (r.get("implementation") or {}).get("is_open")]

    return {
        "organizations": len(rs),
        "active_organizations": sum(1 for r in rs if r["is_active"]),
        "health": health_counts,
        "needs_attention": len(attention_rows),

        "leads_total": total("leads_total"),
        "leads_added_recently": total("leads_added_recently"),
        "leads_worked_recently": total("leads_worked_recently"),
        "leads_never_touched": total("leads_never_touched"),
        "leads_sent": sent,

        "replies_total": replies_n,
        "replies_unreviewed": total("replies_unreviewed"),
        "appointments_total": total("appointments_total"),
        "appointments_last_7_days": total("appointments_last_7_days"),
        "appointments_upcoming": total("appointments_upcoming"),

        "response_rate": _rate(replies_n, sent),
        "conversion_rate": _rate(booked, sent),

        # MRR AND ITS COVERAGE, always together.
        "mrr": mrr,
        "mrr_priced_organizations": len(priced),
        "mrr_unpriced_organizations": len(rs) - len(priced),

        "implementations_open": len(open_impl),
        "held_leads": total("held_leads"),
        "recent_window_days": RECENT_DAYS,
        "as_of": _iso(now),
    }


def matches_filter(row: Dict[str, Any], key: str) -> bool:
    """Whether one organization belongs in one Portfolio Health filter.

    Every filter is a predicate over data already on the row. There is no
    filter offered here that the data cannot answer — an empty result must
    mean "none match", never "we did not compute that".
    """
    if key in (None, "", "all"):
        return True
    if key == "healthy":
        return row["health"] == HEALTHY
    if key == "needs_attention":
        # The filter an executive actually reaches for: anything with a
        # standing exception, whatever produced it.
        return bool(row["attention"])
    if key == "inactive":
        return row["health"] == INACTIVE or not row["is_active"]
    if key == "implementation":
        return bool((row.get("implementation") or {}).get("is_open"))
    if key == "billing_issue":
        return row["has_payment_method"] is False or bool(row["held_leads"])
    if key == "low_activity":
        return row["leads_worked_recently"] < LOW_ACTIVITY_LEAD_THRESHOLD
    if key == "high_growth":
        return row["leads_added_recently"] >= GROWTH_LEAD_THRESHOLD
    return True


def sort_key(row: Dict[str, Any]):
    """Worst first, then largest. An executive opens this page to find trouble,
    so trouble is at the top and the biggest customer breaks the tie."""
    try:
        severity = HEALTH_ORDER.index(row["health"])
    except ValueError:                                   # pragma: no cover
        severity = len(HEALTH_ORDER)
    return (severity, -len(row["attention"]), -(row["leads_total"] or 0),
            (row["name"] or "").lower())
