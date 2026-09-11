"""WHAT A CUSTOMER'S SUPPORT ACTUALLY INCLUDES.

THE RULE THIS MODULE EXISTS TO ENFORCE
--------------------------------------
There are three commercial things and only one of them is free:

    TECHNICAL PRODUCT SUPPORT   our software is not doing what we sold. Every
                                paying package includes it. The customer is
                                not charged, and — the part that is easy to
                                get wrong in code — it must NEVER draw down
                                their paid consulting minutes.

    CUSTOMER ASSISTANCE         help using a working product. Some is
                                included by package; beyond that, billable.

    PROFESSIONAL SERVICES       our people doing work for their business.
                                Billable.

`consumption_for(category)` is where that lives, in one function, so no caller
has to remember it.

WHY THE NUMBERS BELOW ARE A FALLBACK AND NOT A POLICY
-----------------------------------------------------
`INITIAL_EVOSYSPRO_RULES` is the shape `brand_config.FROZEN_BRAND_DEFAULTS`
already established: a frozen literal that answers only where the database has
not, field by field. A `support_entitlement_configs` row wins on every field it
fills. The rules are EvoSys Pro's opening position, written down so a fresh
deployment behaves sensibly on day one — not a global constant every brand
inherits forever. BookaBoost may sell entirely different support, and it does
that by writing rows, not by editing this file.

A PLAN NOBODY CONFIGURED IS NOT A PLAN WITH NO SUPPORT
-------------------------------------------------------
`resolve()` never returns "nothing included". A customer whose brand has not
configured support at all falls back to the Starter shape and is marked
`source="default"`, so the God console can list exactly which brands are
running on an assumption. Returning zero minutes and no queue would look
identical to a deliberate decision, and a customer would be refused help
because of a missing config row.
"""

from __future__ import annotations

import calendar
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Organization
from app.models.support_models import (
    Queue, Severity, SupportAssistanceEntry, SupportEntitlementConfig,
    TicketCategory,
)

log = logging.getLogger(__name__)


# The support features a package can include. Registered here so a typo in a
# config row cannot silently grant or withhold a surface.
SUPPORT_FEATURES: Dict[str, str] = {
    "ask_ai": "24/7 AI support assistant",
    "knowledge_base": "Help centre and knowledge base",
    "tickets": "Trouble tickets with SLA",
    "technical_support": "Technical product support",
    "system_status": "Live system status",
    "scheduled_assistance": "Scheduled live assistance",
    "email_updates": "Email and in-app ticket updates",
}

ALL_SUPPORT_FEATURE_KEYS = tuple(sorted(SUPPORT_FEATURES))

_BASE_FEATURES = ["ask_ai", "knowledge_base", "tickets", "technical_support",
                  "system_status", "email_updates"]


# ── EvoSys Pro's opening position, in business minutes ──────────────────────
#
# BUSINESS MINUTES, NOT CLOCK MINUTES. "1 business day" is 8 business hours =
# 480 minutes of a support day as `support_sla` defines it, so a ticket raised
# at 16:45 is not late at 09:00 the next morning.
#
# `critical` is present on every tier and identical on every tier. A Starter
# customer whose service is down does not queue behind a Professional
# customer's question — that is the emergency rule, and encoding it as a
# per-package number would let a future edit quietly sell it.
INITIAL_EVOSYSPRO_RULES: Dict[str, Dict[str, Any]] = {
    "starter": {
        "display_name": "Starter Support",
        "queue": Queue.STANDARD,
        "first_response_normal_minutes": 480,    # 1 business day
        "first_response_high_minutes": 480,      # 8 business hours
        "first_response_critical_minutes": 60,   # emergency queue, 24/7
        "included_assistance_minutes": 0,
        "emergency_override": True,
        "features": list(_BASE_FEATURES),
    },
    "growth": {
        "display_name": "Growth Support",
        "queue": Queue.PRIORITY,
        "first_response_normal_minutes": 480,    # 8 business hours
        "first_response_high_minutes": 240,      # 4 business hours
        "first_response_critical_minutes": 60,
        "included_assistance_minutes": 30,
        "emergency_override": True,
        "features": list(_BASE_FEATURES) + ["scheduled_assistance"],
    },
    "professional": {
        "display_name": "Professional Support",
        "queue": Queue.PRIORITY_PLUS,
        "first_response_normal_minutes": 240,    # 4 business hours
        "first_response_high_minutes": 120,      # 2 business hours
        "first_response_critical_minutes": 60,
        "included_assistance_minutes": 60,
        "emergency_override": True,
        "features": list(_BASE_FEATURES) + ["scheduled_assistance"],
    },
}

# What an unrecognised or absent package falls back to. Starter's shape,
# because the alternative — no support — is never the right answer to a
# missing configuration row.
_FALLBACK_PLAN_KEY = "starter"


def _frozen_rule(plan_key: Optional[str]) -> Dict[str, Any]:
    key = (plan_key or "").strip().lower()
    rule = INITIAL_EVOSYSPRO_RULES.get(key)
    if rule is None:
        rule = INITIAL_EVOSYSPRO_RULES[_FALLBACK_PLAN_KEY]
    return {k: (list(v) if isinstance(v, list) else v) for k, v in rule.items()}


def _load_features(raw: Optional[str], fallback: List[str]) -> List[str]:
    if not raw:
        return list(fallback)
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("support_entitlements: unreadable included_features_json; "
                    "using the package default")
        return list(fallback)
    if not isinstance(parsed, list):
        return list(fallback)
    return [k for k in parsed if isinstance(k, str) and k in SUPPORT_FEATURES]


def plan_key_for(org: Optional[Organization]) -> Optional[str]:
    """Which package this organization is entitled to TODAY.

    `billing_plan_key` and never `billing_pending_plan_key`, for the same
    reason `plan_limits.effective_plan` gives: a scheduled downgrade has not
    happened, and reading it would withdraw a response target the customer has
    already paid for. Falls back to the legacy coarse `plan` column so an
    organization that predates the brand catalogue still resolves.
    """
    if org is None:
        return None
    return (getattr(org, "billing_plan_key", None)
            or getattr(org, "plan", None) or None)


def resolve(db: Optional[Session], org: Optional[Organization]) -> Dict[str, Any]:
    """The support entitlement in force for this organization, right now.

    Never raises and never returns None. The `source` field says where the
    answer came from so an operator can tell a decision from an assumption:

        "config"        a support_entitlement_configs row for this brand+plan
        "default"       the frozen rule above; nobody has configured this
        "no_customer"   there is no organization (platform-level caller)
    """
    if org is None:
        rule = _frozen_rule(_FALLBACK_PLAN_KEY)
        return _shape(rule, plan_key=None, platform_id=None, source="no_customer")

    plan_key = plan_key_for(org)
    platform_id = getattr(org, "platform_id", None)
    rule = _frozen_rule(plan_key)
    source = "default"

    row = None
    if db is not None and platform_id and plan_key:
        try:
            row = (db.query(SupportEntitlementConfig)
                   .filter(SupportEntitlementConfig.platform_id == platform_id,
                           SupportEntitlementConfig.plan_key == plan_key,
                           SupportEntitlementConfig.is_active.is_(True))
                   .first())
        except Exception:                                     # noqa: BLE001
            # A support page must not 500 because a config table is briefly
            # unreadable. The frozen rule is a correct, conservative answer.
            log.exception("support_entitlements: config lookup failed for "
                          "platform=%s plan=%s", platform_id, plan_key)
            row = None

    if row is not None:
        source = "config"
        # FIELD BY FIELD, exactly like brand_config: a row that sets a queue
        # and nothing else keeps the package's response targets rather than
        # zeroing them.
        if row.display_name:
            rule["display_name"] = row.display_name
        if row.queue:
            rule["queue"] = row.queue
        for column, key in (
            ("first_response_normal_minutes", "first_response_normal_minutes"),
            ("first_response_high_minutes", "first_response_high_minutes"),
            ("first_response_critical_minutes", "first_response_critical_minutes"),
        ):
            value = getattr(row, column, None)
            if value is not None:
                rule[key] = int(value)
        if row.included_assistance_minutes is not None:
            rule["included_assistance_minutes"] = int(row.included_assistance_minutes)
        if row.emergency_override is not None:
            rule["emergency_override"] = bool(row.emergency_override)
        rule["features"] = _load_features(row.included_features_json, rule["features"])

    return _shape(rule, plan_key=plan_key, platform_id=platform_id, source=source)


def _shape(rule: Dict[str, Any], *, plan_key, platform_id, source) -> Dict[str, Any]:
    return {
        "plan_key": plan_key,
        "platform_id": platform_id,
        "display_name": rule["display_name"],
        "queue": rule["queue"],
        "queue_label": Queue.LABELS.get(rule["queue"], rule["queue"]),
        "first_response_minutes": {
            Severity.P1: rule["first_response_critical_minutes"],
            Severity.P2: rule["first_response_high_minutes"],
            Severity.P3: rule["first_response_normal_minutes"],
            # A question has no first-response commitment, and saying so is
            # more honest than quoting the normal target and missing it.
            Severity.P4: None,
        },
        "included_assistance_minutes": rule["included_assistance_minutes"],
        "emergency_override": rule["emergency_override"],
        "features": list(rule["features"]),
        "source": source,
    }


def has_support_feature(entitlement: Dict[str, Any], key: str) -> bool:
    return key in (entitlement or {}).get("features", ())


def first_response_minutes(entitlement: Dict[str, Any], severity: str) -> Optional[int]:
    """Business minutes promised for a first response, or None if none is."""
    return (entitlement or {}).get("first_response_minutes", {}).get(severity)


def queue_for(entitlement: Dict[str, Any], severity: str,
              *, platform_emergency: bool = False) -> str:
    """Which queue this ticket joins.

    PACKAGE INFLUENCES PRIORITY. A VERIFIED EMERGENCY OVERRIDES IT.

    A Starter customer with a genuine P1 outage does not sit behind a
    Professional customer's how-to question, and that is not a courtesy — it
    is what "critical" means. `platform_emergency` is set by the platform's
    own evidence (a confirmed incident, a failed health check), never by the
    customer ticking a box, because a severity a customer can assert is a
    severity every customer asserts.
    """
    ent = entitlement or {}
    if severity == Severity.P1 and platform_emergency and ent.get("emergency_override", True):
        return Queue.EMERGENCY
    return ent.get("queue", Queue.STANDARD)


# ══════════════════════════════════════════════════════════════════════════
# ASSISTANCE ALLOWANCE
# ══════════════════════════════════════════════════════════════════════════

def consumption_for(category: str) -> Dict[str, bool]:
    """Does work in this category eat the customer's included minutes?

    THE ONE PLACE THIS IS DECIDED. Technical product support answers False to
    both, always: a defect in our software is not the customer spending their
    consulting allowance, and it is not billable either. Every caller that
    records time asks here rather than deciding for itself, because the day
    one of them decides differently is the day a customer is billed for our
    bug.
    """
    if category == TicketCategory.TECHNICAL_PRODUCT_SUPPORT:
        return {"counts_against_allowance": False, "billable": False}
    if category == TicketCategory.CUSTOMER_ASSISTANCE:
        return {"counts_against_allowance": True, "billable": False}
    # Professional services are work for their business. Never included.
    return {"counts_against_allowance": False, "billable": True}


def _add_months(when: datetime, months: int) -> datetime:
    month = when.month - 1 + months
    year = when.year + month // 12
    month = month % 12 + 1
    day = min(when.day, calendar.monthrange(year, month)[1])
    return when.replace(year=year, month=month, day=day)


def billing_period(org: Optional[Organization],
                   now: Optional[datetime] = None) -> Dict[str, datetime]:
    """The window an allowance is measured over.

    Anchored to the SUBSCRIPTION period when Stripe has told us one, because
    that is the month the customer is actually paying for. A calendar month
    would hand a customer who signed up on the 20th nine days of allowance in
    their first "month" and then a full one nine days later.

    Falls back to the calendar month for an organization with no subscription
    — mid-implementation, migrated, or manually onboarded — which is a real
    and common state and must not mean "no allowance at all".
    """
    now = now or datetime.utcnow()
    end = getattr(org, "billing_current_period_end", None) if org is not None else None
    if isinstance(end, datetime):
        # Walk the anchor backwards/forwards in whole months until it brackets
        # `now`, so a stale period end from a webhook we have not seen since
        # still yields the right window rather than a period in the past.
        guard = 0
        while end <= now and guard < 240:
            end = _add_months(end, 1)
            guard += 1
        while _add_months(end, -1) > now and guard < 480:
            end = _add_months(end, -1)
            guard += 1
        return {"start": _add_months(end, -1), "end": end}

    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {"start": start, "end": _add_months(start, 1)}


def assistance_summary(db: Session, org: Optional[Organization],
                       entitlement: Optional[Dict[str, Any]] = None,
                       now: Optional[datetime] = None) -> Dict[str, Any]:
    """Allowance, used, remaining — computed, never carried.

    NO ROLLOVER, and it is enforced by arithmetic rather than by policy: the
    query only ever looks at the CURRENT period, so unused minutes cannot
    survive into the next one even if someone later adds a balance column.
    """
    entitlement = entitlement or resolve(db, org)
    period = billing_period(org, now=now)
    allowance = int(entitlement.get("included_assistance_minutes") or 0)

    used = 0
    billable = 0
    entries: List[SupportAssistanceEntry] = []
    if org is not None and db is not None:
        try:
            entries = (db.query(SupportAssistanceEntry)
                       .filter(SupportAssistanceEntry.organization_id == org.id,
                               SupportAssistanceEntry.status == "delivered",
                               SupportAssistanceEntry.delivered_at >= period["start"],
                               SupportAssistanceEntry.delivered_at < period["end"])
                       .all())
        except Exception:                                     # noqa: BLE001
            log.exception("support_entitlements: assistance ledger read failed "
                          "for org=%s", getattr(org, "id", None))
            entries = []

    for entry in entries:
        if entry.counts_against_allowance:
            used += int(entry.minutes or 0)
        elif entry.billable:
            billable += int(entry.minutes or 0)

    return {
        "allowance_minutes": allowance,
        "used_minutes": used,
        # Never negative on the page. Overage is a real thing and it is
        # reported as overage, not as a negative balance nobody can read.
        "remaining_minutes": max(allowance - used, 0),
        "overage_minutes": max(used - allowance, 0),
        "billable_minutes_this_period": billable,
        "period_start": period["start"],
        "period_end": period["end"],
        "rollover": False,
        "entry_count": len(entries),
    }


def record_assistance(db: Session, *, org: Organization, category: str,
                      minutes: int, ticket_id: Optional[str] = None,
                      status: str = "delivered",
                      offering_key: Optional[str] = None,
                      requested_by: Optional[str] = None,
                      topic: Optional[str] = None,
                      scheduled_for: Optional[datetime] = None,
                      note: Optional[str] = None,
                      now: Optional[datetime] = None) -> SupportAssistanceEntry:
    """Write one line of the assistance ledger, with the commercial fact set here.

    The caller does not get to say whether this is included or billable — that
    comes from `consumption_for(category)`. A caller that could pass it would
    eventually pass the wrong one for a technical support session.
    """
    now = now or datetime.utcnow()
    rules = consumption_for(category)
    period = billing_period(org, now=now)

    entry = SupportAssistanceEntry(
        organization_id=org.id,
        platform_id=getattr(org, "platform_id", None),
        ticket_id=ticket_id,
        status=status,
        category=category,
        requested_topic=topic,
        scheduled_for=scheduled_for,
        delivered_at=now if status == "delivered" else None,
        minutes=int(minutes or 0),
        counts_against_allowance=rules["counts_against_allowance"],
        billable=rules["billable"],
        offering_key=offering_key,
        period_start=period["start"],
        period_end=period["end"],
        requested_by=requested_by,
        note=note,
    )
    db.add(entry)
    db.flush()
    return entry


# ══════════════════════════════════════════════════════════════════════════
# CONFIGURATION WRITES — God only, audited by the caller
# ══════════════════════════════════════════════════════════════════════════

def normalize_feature_keys(keys: Optional[List[str]]) -> List[str]:
    """Reject unknown feature keys loudly.

    `entitlements.normalize_keys` does the same thing for product features and
    for the same reason: an allow-list that silently accepts typos fills with
    keys that grant nothing and nobody notices for months.
    """
    from fastapi import HTTPException
    if keys is None:
        return []
    cleaned, unknown = [], []
    for raw in keys:
        key = (raw or "").strip().lower()
        if not key:
            continue
        if key not in SUPPORT_FEATURES:
            unknown.append(key)
        elif key not in cleaned:
            cleaned.append(key)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail="Unknown support feature key(s): %s. Valid keys: %s"
                   % (", ".join(sorted(set(unknown))),
                      ", ".join(ALL_SUPPORT_FEATURE_KEYS)))
    return sorted(cleaned)


def upsert_config(db: Session, *, platform_id: str, plan_key: str,
                  values: Dict[str, Any]) -> SupportEntitlementConfig:
    """Create or update one brand+package support configuration.

    Only the keys present in `values` are written, so a partial edit from a
    form that renders four fields cannot blank the other three.
    """
    plan_key = (plan_key or "").strip().lower()
    row = (db.query(SupportEntitlementConfig)
           .filter(SupportEntitlementConfig.platform_id == platform_id,
                   SupportEntitlementConfig.plan_key == plan_key)
           .first())
    if row is None:
        row = SupportEntitlementConfig(platform_id=platform_id, plan_key=plan_key)
        db.add(row)

    for field in ("display_name", "queue", "first_response_normal_minutes",
                  "first_response_high_minutes", "first_response_critical_minutes",
                  "included_assistance_minutes", "emergency_override", "notes",
                  "is_active"):
        if field in values:
            setattr(row, field, values[field])

    if "features" in values:
        row.included_features_json = json.dumps(
            normalize_feature_keys(values["features"]))

    db.flush()
    return row


def config_report(db: Session, platform_id: Optional[str]) -> Dict[str, Any]:
    """What this brand has decided, and what it is still assuming.

    The `source` on each package is the useful column: it is how an operator
    finds the packages that are running on `INITIAL_EVOSYSPRO_RULES` because
    nobody ever configured them, which is invisible from the customer's SLA
    page by design — it looks like a decision from there.
    """
    rows = {}
    if platform_id:
        for row in (db.query(SupportEntitlementConfig)
                    .filter(SupportEntitlementConfig.platform_id == platform_id)
                    .all()):
            rows[row.plan_key] = row

    keys = sorted(set(list(INITIAL_EVOSYSPRO_RULES) + list(rows)))
    packages = []
    for key in keys:
        rule = _frozen_rule(key)
        row = rows.get(key)
        shaped = _shape(rule, plan_key=key, platform_id=platform_id,
                        source="config" if row is not None else "default")
        if row is not None:
            shaped = resolve_for_row(rule, row, platform_id, key)
        shaped["configured"] = row is not None
        shaped["is_active"] = bool(row.is_active) if row is not None else True
        packages.append(shaped)

    return {
        "platform_id": platform_id,
        "packages": packages,
        "available_features": [{"key": k, "label": SUPPORT_FEATURES[k]}
                               for k in ALL_SUPPORT_FEATURE_KEYS],
        "queues": [{"key": q, "label": Queue.LABELS[q]} for q in Queue.ALL],
        "unconfigured_count": sum(1 for p in packages if not p["configured"]),
    }


def resolve_for_row(rule: Dict[str, Any], row: SupportEntitlementConfig,
                    platform_id: Optional[str], plan_key: str) -> Dict[str, Any]:
    """The same field-by-field merge `resolve()` performs, for a known row.

    Shared rather than duplicated: the merge order IS the policy, and two
    copies of it would eventually disagree about whether a NULL means
    'inherit' or 'zero'.
    """
    merged = dict(rule)
    if row.display_name:
        merged["display_name"] = row.display_name
    if row.queue:
        merged["queue"] = row.queue
    if row.first_response_normal_minutes is not None:
        merged["first_response_normal_minutes"] = int(row.first_response_normal_minutes)
    if row.first_response_high_minutes is not None:
        merged["first_response_high_minutes"] = int(row.first_response_high_minutes)
    if row.first_response_critical_minutes is not None:
        merged["first_response_critical_minutes"] = int(row.first_response_critical_minutes)
    if row.included_assistance_minutes is not None:
        merged["included_assistance_minutes"] = int(row.included_assistance_minutes)
    if row.emergency_override is not None:
        merged["emergency_override"] = bool(row.emergency_override)
    merged["features"] = _load_features(row.included_features_json, merged["features"])
    return _shape(merged, plan_key=plan_key, platform_id=platform_id, source="config")
