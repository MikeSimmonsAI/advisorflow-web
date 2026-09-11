"""THE COMMERCIAL QUESTION ENGINE.

WHY THIS IS NOT FOUR QUESTIONS IN A JSX FILE
--------------------------------------------
The first revenue-share deal needs four things nobody has decided yet: what the
percentage applies against, which collections are eligible, who receives the
customer's money first, and how often settlement happens. It would take ten
minutes to hard-code those four into an onboarding screen, and the second
structure that needs a fifth question would then need a deploy, a migration and
a release to ask it.

So questions are DEFINITIONS. The defaults below are the platform's, shipped in
code because a fresh install must be able to ask a revenue-share customer the
right things on day one without an operator seeding anything. A brand may add
its own, or override a default by the same key, by writing a row — and those
rows win, per key, for that brand only.

WHAT A DEFINITION CONTROLS
--------------------------
  applies_to_types          which commercial structures ask it at all
  audience                  whether the CUSTOMER is asked, or only staff
  required_for_activation   whether leaving it unanswered blocks ACTIVATION —
                            and activation only, never the rest of onboarding
  allowed_values            what the answer may be
  validation                the bounds an answer must satisfy
  version                   snapshotted onto the answer, so an answered term
                            keeps saying what was actually asked

NOTHING HERE DEFAULTS A COMMERCIAL TERM SILENTLY. `default_value` is populated
on exactly one definition — the rounding policy, which is a mechanical
convention rather than a negotiated figure — and it is displayed as a default
rather than presented as an agreed term.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.commercial_models import (
    ATTRIBUTION_RULES, COLLECTION_SOURCES, CommercialQuestionDefinition,
    TYPE_CUSTOM_FIXED, TYPE_CUSTOM_QUOTED, TYPE_HYBRID, TYPE_REVENUE_SHARE,
)

# Who is asked.
AUDIENCE_CUSTOMER = "customer"
AUDIENCE_INTERNAL = "internal"
AUDIENCE_BOTH     = "both"
AUDIENCES = (AUDIENCE_CUSTOMER, AUDIENCE_INTERNAL, AUDIENCE_BOTH)

# Answer shapes this engine knows how to validate. A definition carrying a kind
# that is not in here is refused at write time rather than accepted and then
# silently unvalidated.
KIND_SELECT      = "select"
KIND_MULTISELECT = "multiselect"
KIND_TEXT        = "text"
KIND_TEXTAREA    = "textarea"
KIND_MONEY       = "money"        # stored as integer cents
KIND_PERCENT     = "percent"      # stored as a number, 0..100
KIND_INTEGER     = "integer"
KIND_BOOLEAN     = "boolean"
KIND_DATE        = "date"         # ISO yyyy-mm-dd string

KINDS = (KIND_SELECT, KIND_MULTISELECT, KIND_TEXT, KIND_TEXTAREA, KIND_MONEY,
         KIND_PERCENT, KIND_INTEGER, KIND_BOOLEAN, KIND_DATE)

_SHARE_TYPES = [TYPE_REVENUE_SHARE, TYPE_HYBRID]
_CUSTOM_MONEY_TYPES = [TYPE_CUSTOM_FIXED, TYPE_HYBRID, TYPE_REVENUE_SHARE,
                       TYPE_CUSTOM_QUOTED]


def _opt(value: str, label: str, description: Optional[str] = None) -> Dict[str, Any]:
    row = {"value": value, "label": label}
    if description:
        row["description"] = description
    return row


_OTHER = _opt("other", "Something else",
              "Describe it in the notes; an operator will record the detail.")

# ════════════════════════════════════════════════════════════════════════════
# PLATFORM DEFAULT DEFINITIONS
# ════════════════════════════════════════════════════════════════════════════
#
# Order matters: display_order is what the customer's screen and the internal
# console both sort by.

DEFAULT_DEFINITIONS: List[Dict[str, Any]] = [

    # ── the money that is fixed, if any ─────────────────────────────────────
    #
    # REQUIRED even for a revenue-share deal, and answerable as zero. "There is
    # no upfront fee" and "nobody has discussed the upfront fee" are different
    # commercial positions, and only the first of them can be activated.
    {
        "key": "setup_fee",
        "label": "One-time setup or implementation fee",
        "description": "What the customer pays once, at the start. Enter 0 if "
                       "the arrangement has no upfront fee.",
        "kind": KIND_MONEY,
        "applies_to_types": _CUSTOM_MONEY_TYPES,
        "audience": AUDIENCE_BOTH,
        "required_for_activation": True,
        "validation": {"min": 0},
        "display_order": 10,
    },
    {
        "key": "recurring_base_fee",
        "label": "Recurring base fee",
        "description": "What the customer pays every billing period regardless "
                       "of performance. Enter 0 if there is no base fee.",
        "kind": KIND_MONEY,
        "applies_to_types": _CUSTOM_MONEY_TYPES,
        "audience": AUDIENCE_BOTH,
        "required_for_activation": True,
        "validation": {"min": 0},
        "display_order": 20,
    },
    {
        "key": "recurring_interval",
        "label": "How often the base fee recurs",
        "kind": KIND_SELECT,
        "applies_to_types": _CUSTOM_MONEY_TYPES,
        "audience": AUDIENCE_BOTH,
        "required_for_activation": False,
        "allowed_values": [_opt("month", "Monthly"), _opt("year", "Annually"),
                           _opt("not_applicable", "No recurring base fee")],
        "display_order": 30,
    },

    # ── the four a revenue share cannot be settled without ──────────────────
    {
        "key": "share_basis",
        "label": "What is the percentage applied against?",
        "description": "The figure the split is calculated from.",
        "kind": KIND_SELECT,
        "applies_to_types": _SHARE_TYPES,
        "audience": AUDIENCE_BOTH,
        "required_for_activation": True,
        "allowed_values": [
            _opt("gross_collections", "Gross collections",
                 "Everything collected, before any deduction."),
            _opt("net_collections", "Net collections",
                 "What is left after the deductions your agreement names."),
            _opt("collections_after_defined_adjustments",
                 "Collections after defined adjustments",
                 "Gross, less the specific adjustments listed in the agreement."),
            _OTHER,
        ],
        "display_order": 100,
    },
    {
        "key": "eligible_collections",
        "label": "Which collections are eligible?",
        "description": "Which of the money you collect the share applies to.",
        "kind": KIND_SELECT,
        "applies_to_types": _SHARE_TYPES,
        "audience": AUDIENCE_BOTH,
        "required_for_activation": True,
        "allowed_values": [
            _opt("all_collections", "All collections"),
            _opt("platform_attributed_only", "Only revenue from work the "
                 "platform generated"),
            _opt("specific_products_services", "Only specific products or "
                 "services"),
            _OTHER,
        ],
        "display_order": 110,
    },
    {
        "key": "payment_recipient",
        "label": "Who receives the customer payment first?",
        "description": "Whose account the money lands in before it is split.",
        "kind": KIND_SELECT,
        "applies_to_types": _SHARE_TYPES,
        "audience": AUDIENCE_BOTH,
        "required_for_activation": True,
        "allowed_values": [
            _opt("customer_company", "The customer's own company"),
            _opt("provider_entity", "The provider's entity"),
            _opt("processor_or_escrow", "A payment processor or escrow account"),
            _OTHER,
        ],
        "display_order": 120,
    },
    {
        "key": "settlement_frequency",
        "label": "How often is settlement calculated and paid?",
        "kind": KIND_SELECT,
        "applies_to_types": _SHARE_TYPES,
        "audience": AUDIENCE_BOTH,
        "required_for_activation": True,
        "allowed_values": [
            _opt("weekly", "Weekly"),
            _opt("biweekly", "Every two weeks"),
            _opt("monthly", "Monthly"),
            _opt("quarterly", "Quarterly"),
            _opt("custom", "Something else"),
        ],
        "display_order": 130,
    },

    # ── how the platform knows what was collected ───────────────────────────
    {
        "key": "collections_source",
        "label": "Authoritative source of collection figures",
        "description": "Where the numbers a settlement is calculated from come "
                       "from. A settlement can only be built from records this "
                       "platform can verify.",
        "kind": KIND_SELECT,
        "applies_to_types": _SHARE_TYPES,
        "audience": AUDIENCE_INTERNAL,
        "required_for_activation": True,
        "allowed_values": [
            _opt(COLLECTION_SOURCES[0], "Stripe",
                 "Payments this platform already records."),
            _opt(COLLECTION_SOURCES[1], "Manual approved entry",
                 "Typed by a named person and approved by another."),
            _opt(COLLECTION_SOURCES[2], "Connected accounting system",
                 "Named here; no feed is implemented yet."),
            _opt(COLLECTION_SOURCES[3], "External billing provider",
                 "Named here; no feed is implemented yet."),
            _opt(COLLECTION_SOURCES[4], "Import"),
            _opt(COLLECTION_SOURCES[5], "A future integration",
                 "Recorded as the intention. Produces no records today."),
        ],
        "display_order": 200,
    },
    {
        "key": "attribution_rule",
        "label": "Attribution rule",
        "description": "How a collection is judged to be inside the share.",
        "kind": KIND_SELECT,
        "applies_to_types": _SHARE_TYPES,
        "audience": AUDIENCE_INTERNAL,
        "required_for_activation": True,
        "allowed_values": [
            _opt(ATTRIBUTION_RULES[0], "All eligible collections"),
            _opt(ATTRIBUTION_RULES[1], "Platform-attributed only"),
            _opt(ATTRIBUTION_RULES[2], "A specific service"),
            _opt(ATTRIBUTION_RULES[3], "A custom rule stated in the agreement"),
        ],
        "display_order": 210,
    },
    {
        "key": "allocation_rule",
        "label": "How the allocation must reconcile",
        "kind": KIND_SELECT,
        "applies_to_types": _SHARE_TYPES,
        "audience": AUDIENCE_INTERNAL,
        "required_for_activation": True,
        "allowed_values": [
            _opt("full_allocation", "Every party's percentage, totalling 100%"),
            _opt("residual_to_party", "Named percentages, remainder to one party"),
            _opt("custom", "A structure stated in the agreement"),
        ],
        "display_order": 220,
    },
    {
        "key": "rounding_policy",
        "label": "Rounding policy",
        "description": "How a split that does not divide evenly is resolved.",
        "kind": KIND_SELECT,
        "applies_to_types": _SHARE_TYPES,
        "audience": AUDIENCE_INTERNAL,
        "required_for_activation": False,
        "allowed_values": [
            _opt("largest_remainder", "Largest remainder",
                 "Every cent of the basis is allocated; the odd cents go to the "
                 "largest remainders."),
            _opt("round_half_up", "Round each party half up",
                 "Each party is rounded independently; the total may differ "
                 "from the basis by a cent or two."),
        ],
        "default_value": {"value": "largest_remainder"},
        "display_order": 230,
    },

    # ── adjustments: stored as policy, never assumed ────────────────────────
    {
        "key": "adjustment_refunds",
        "label": "Refunds",
        "description": "Whether a refunded collection reduces the share.",
        "kind": KIND_SELECT,
        "applies_to_types": _SHARE_TYPES,
        "audience": AUDIENCE_INTERNAL,
        "required_for_activation": True,
        "allowed_values": [
            _opt("reduce_share", "Reduce the share"),
            _opt("do_not_reduce_share", "Do not reduce the share"),
            _OTHER,
        ],
        "display_order": 300,
    },
    {
        "key": "adjustment_chargebacks",
        "label": "Chargebacks",
        "kind": KIND_SELECT,
        "applies_to_types": _SHARE_TYPES,
        "audience": AUDIENCE_INTERNAL,
        "required_for_activation": True,
        "allowed_values": [
            _opt("reduce_share", "Reduce the share"),
            _opt("do_not_reduce_share", "Do not reduce the share"),
            _OTHER,
        ],
        "display_order": 310,
    },
    {
        "key": "adjustment_processing_fees",
        "label": "Payment processing fees",
        "kind": KIND_SELECT,
        "applies_to_types": _SHARE_TYPES,
        "audience": AUDIENCE_INTERNAL,
        "required_for_activation": True,
        "allowed_values": [
            _opt("deducted_before_share", "Deducted before the split"),
            _opt("not_deducted", "Not deducted"),
            _OTHER,
        ],
        "display_order": 320,
    },
    {
        "key": "adjustment_taxes",
        "label": "Taxes",
        "kind": KIND_SELECT,
        "applies_to_types": _SHARE_TYPES,
        "audience": AUDIENCE_INTERNAL,
        "required_for_activation": True,
        "allowed_values": [
            _opt("excluded_from_basis", "Excluded from the basis"),
            _opt("included_in_basis", "Included in the basis"),
            _OTHER,
        ],
        "display_order": 330,
    },
    {
        "key": "adjustment_credits",
        "label": "Credits",
        "kind": KIND_SELECT,
        "applies_to_types": _SHARE_TYPES,
        "audience": AUDIENCE_INTERNAL,
        "required_for_activation": True,
        "allowed_values": [
            _opt("reduce_share", "Reduce the share"),
            _opt("do_not_reduce_share", "Do not reduce the share"),
            _OTHER,
        ],
        "display_order": 340,
    },
    {
        "key": "adjustment_write_offs",
        "label": "Write-offs and uncollectable amounts",
        "kind": KIND_SELECT,
        "applies_to_types": _SHARE_TYPES,
        "audience": AUDIENCE_INTERNAL,
        "required_for_activation": True,
        "allowed_values": [
            _opt("reduce_share", "Reduce the share"),
            _opt("do_not_reduce_share", "Do not reduce the share"),
            _OTHER,
        ],
        "display_order": 350,
    },

    # ── a quoted deal states its own terms in words ─────────────────────────
    {
        "key": "quoted_terms_summary",
        "label": "Quoted terms",
        "description": "What was quoted, in the words of the agreement.",
        "kind": KIND_TEXTAREA,
        "applies_to_types": [TYPE_CUSTOM_QUOTED],
        "audience": AUDIENCE_INTERNAL,
        "required_for_activation": True,
        "validation": {"max_length": 4000},
        "display_order": 400,
    },
]

DEFAULT_BY_KEY = {d["key"]: d for d in DEFAULT_DEFINITIONS}


# ════════════════════════════════════════════════════════════════════════════
# RESOLUTION
# ════════════════════════════════════════════════════════════════════════════
#
# Precedence, highest first:
#
#   1. a row for THIS brand with this key      — the brand's own question, or
#                                                its override of a default
#   2. a row with platform_id NULL             — a platform-wide edit made
#                                                without a deploy
#   3. the code default above                  — what a fresh install asks
#
# A brand row with is_active = False REMOVES the question for that brand,
# including a code default. That is the supported way to stop asking something
# without deleting the answers already given.


def _from_row(row: CommercialQuestionDefinition) -> Dict[str, Any]:
    return {
        "key": row.key,
        "label": row.label,
        "description": row.description,
        "help_text": row.help_text,
        "kind": row.kind,
        "applies_to_types": list(row.applies_to_types or []) or None,
        "audience": row.audience,
        "required_for_activation": bool(row.required_for_activation),
        "allowed_values": list(row.allowed_values or []) or None,
        "validation": dict(row.validation or {}) or None,
        "default_value": row.default_value,
        "display_order": int(row.display_order or 0),
        "version": int(row.version or 1),
        "is_active": bool(row.is_active),
        "origin": "brand" if row.platform_id else "platform_row",
        "definition_id": row.id,
    }


def _from_code(d: Dict[str, Any]) -> Dict[str, Any]:
    out = {
        "key": d["key"],
        "label": d["label"],
        "description": d.get("description"),
        "help_text": d.get("help_text"),
        "kind": d.get("kind", KIND_SELECT),
        "applies_to_types": d.get("applies_to_types"),
        "audience": d.get("audience", AUDIENCE_BOTH),
        "required_for_activation": bool(d.get("required_for_activation")),
        "allowed_values": d.get("allowed_values"),
        "validation": d.get("validation"),
        "default_value": d.get("default_value"),
        "display_order": int(d.get("display_order", 0)),
        "version": int(d.get("version", 1)),
        "is_active": True,
        "origin": "code_default",
        "definition_id": None,
    }
    return out


def _applies(defn: Dict[str, Any], agreement_type: Optional[str]) -> bool:
    types = defn.get("applies_to_types")
    if not types:
        return True
    if agreement_type is None:
        return True
    return agreement_type in types


def _audience_ok(defn: Dict[str, Any], audience: Optional[str]) -> bool:
    if audience is None:
        return True
    have = defn.get("audience") or AUDIENCE_BOTH
    if have == AUDIENCE_BOTH:
        return True
    return have == audience


def applies_to(defn: Dict[str, Any], agreement_type: Optional[str]) -> bool:
    """Public form of the applicability test, for callers outside this module."""
    return _applies(defn, agreement_type)


def catalogue(db: Session, platform_id: Optional[str]) -> Dict[str, Dict[str, Any]]:
    """Every question this brand knows, resolved by precedence, keyed by key.

    Inactive questions are RETAINED in the map with is_active False so a caller
    that is rendering an already-answered term can still label it. Callers that
    are asking questions filter on is_active themselves — `for_agreement` does.
    """
    resolved: Dict[str, Dict[str, Any]] = {k: _from_code(v)
                                           for k, v in DEFAULT_BY_KEY.items()}

    rows = (db.query(CommercialQuestionDefinition)
            .filter(CommercialQuestionDefinition.platform_id.is_(None))
            .all())
    for r in rows:
        resolved[r.key] = _from_row(r)

    if platform_id:
        brand_rows = (db.query(CommercialQuestionDefinition)
                      .filter(CommercialQuestionDefinition.platform_id == platform_id)
                      .all())
        for r in brand_rows:
            resolved[r.key] = _from_row(r)

    return resolved


def for_agreement(db: Session, platform_id: Optional[str],
                  agreement_type: Optional[str],
                  audience: Optional[str] = None) -> List[Dict[str, Any]]:
    """The questions this agreement should be asked, in display order."""
    out = [d for d in catalogue(db, platform_id).values()
           if d["is_active"] and _applies(d, agreement_type)
           and _audience_ok(d, audience)]
    out.sort(key=lambda d: (d["display_order"], d["key"]))
    return out


def definition(db: Session, platform_id: Optional[str],
               key: str) -> Optional[Dict[str, Any]]:
    return catalogue(db, platform_id).get(key)


def required_keys(db: Session, platform_id: Optional[str],
                  agreement_type: Optional[str]) -> List[str]:
    """Keys whose absence blocks ACTIVATION. Nothing else."""
    return [d["key"] for d in for_agreement(db, platform_id, agreement_type)
            if d["required_for_activation"]]


# ════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ════════════════════════════════════════════════════════════════════════════
#
# Every answer passes through here before it is stored, and a rejection writes
# nothing. A commercial term accepted in a shape the settlement engine cannot
# read is worse than an unanswered one: the first looks finished.


def _bad(msg: str) -> HTTPException:
    return HTTPException(status_code=400, detail=msg)


def validate_answer(defn: Dict[str, Any], raw: Any) -> Any:
    """Normalise one answer, or raise 400.

    `None` is allowed and means UNANSWERED — clearing an answer is a legitimate
    act (somebody recorded the wrong thing) and is audited like any other
    change. It is NOT the same as answering zero, and the caller records the
    term's state accordingly.
    """
    if raw is None:
        return None

    kind = defn.get("kind") or KIND_SELECT
    if kind not in KINDS:
        raise _bad("Question '%s' has an unsupported answer type '%s'."
                   % (defn.get("key"), kind))

    rules = defn.get("validation") or {}

    if kind in (KIND_SELECT, KIND_MULTISELECT):
        allowed = {o["value"] for o in (defn.get("allowed_values") or [])}
        values = raw if kind == KIND_MULTISELECT else [raw]
        if not isinstance(values, list):
            raise _bad("'%s' expects a list of values." % defn.get("key"))
        cleaned = []
        for v in values:
            s = str(v).strip()
            if allowed and s not in allowed:
                raise _bad("'%s' is not an accepted answer to \"%s\"."
                           % (s, defn.get("label") or defn.get("key")))
            cleaned.append(s)
        if kind == KIND_SELECT:
            return cleaned[0] if cleaned else None
        return cleaned

    if kind in (KIND_TEXT, KIND_TEXTAREA):
        s = str(raw).strip()
        if not s:
            return None
        limit = int(rules.get("max_length") or (240 if kind == KIND_TEXT else 4000))
        if len(s) > limit:
            raise _bad("The answer to \"%s\" is longer than %d characters."
                       % (defn.get("label") or defn.get("key"), limit))
        return s

    if kind == KIND_MONEY:
        # Cents. Integers only — a commercial figure that arrives as 19.999 is
        # a data error, not a rounding opportunity.
        try:
            cents = int(raw)
        except (TypeError, ValueError):
            raise _bad("\"%s\" must be a whole number of cents."
                       % (defn.get("label") or defn.get("key")))
        if "min" in rules and cents < int(rules["min"]):
            raise _bad("\"%s\" cannot be less than %d."
                       % (defn.get("label") or defn.get("key"), int(rules["min"])))
        if "max" in rules and cents > int(rules["max"]):
            raise _bad("\"%s\" cannot be more than %d."
                       % (defn.get("label") or defn.get("key"), int(rules["max"])))
        return cents

    if kind == KIND_PERCENT:
        try:
            pct = float(raw)
        except (TypeError, ValueError):
            raise _bad("\"%s\" must be a number."
                       % (defn.get("label") or defn.get("key")))
        lo = float(rules.get("min", 0))
        hi = float(rules.get("max", 100))
        if pct < lo or pct > hi:
            raise _bad("\"%s\" must be between %s and %s."
                       % (defn.get("label") or defn.get("key"), lo, hi))
        return pct

    if kind == KIND_INTEGER:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise _bad("\"%s\" must be a whole number."
                       % (defn.get("label") or defn.get("key")))
        if "min" in rules and n < int(rules["min"]):
            raise _bad("\"%s\" cannot be less than %d."
                       % (defn.get("label") or defn.get("key"), int(rules["min"])))
        if "max" in rules and n > int(rules["max"]):
            raise _bad("\"%s\" cannot be more than %d."
                       % (defn.get("label") or defn.get("key"), int(rules["max"])))
        return n

    if kind == KIND_BOOLEAN:
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        if s in ("true", "yes", "1"):
            return True
        if s in ("false", "no", "0"):
            return False
        raise _bad("\"%s\" must be yes or no."
                   % (defn.get("label") or defn.get("key")))

    # KIND_DATE
    s = str(raw).strip()
    if not s:
        return None
    from datetime import date as _date
    try:
        _date.fromisoformat(s)
    except ValueError:
        raise _bad("\"%s\" must be a date in yyyy-mm-dd form."
                   % (defn.get("label") or defn.get("key")))
    return s


def answer_label(defn: Dict[str, Any], value: Any) -> Optional[str]:
    """The human wording of a stored answer, for a screen that must not show
    `collections_after_defined_adjustments` to a customer."""
    if value is None:
        return None
    kind = defn.get("kind") or KIND_SELECT
    if kind in (KIND_SELECT, KIND_MULTISELECT):
        by_value = {o["value"]: o.get("label") or o["value"]
                    for o in (defn.get("allowed_values") or [])}
        if kind == KIND_SELECT:
            return by_value.get(str(value), str(value))
        return ", ".join(by_value.get(str(v), str(v)) for v in (value or []))
    if kind == KIND_MONEY:
        try:
            return "${:,.2f}".format(int(value) / 100.0)
        except Exception:
            return str(value)
    if kind == KIND_BOOLEAN:
        return "Yes" if value else "No"
    return str(value)
