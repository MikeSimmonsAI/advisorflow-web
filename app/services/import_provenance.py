"""
IMPORT PROVENANCE - where did this advisor's book actually come from?

READ-ONLY. Answers, for one named subject's AUTHORIZED leads:

    which file produced them
    which import list / uploader / source year they carry
    which columns the import PARKED into custom_fields, and how often
    what the parked columns say (owner names, activity dates, buckets)
    what channel permission they hold, and how much of it is simply unknown
    how much history they carry, and how much of it arrived as parked data

That last pair is the point. A book that reads as "never contacted" may be a
book whose contact history was parked at import; this reports both numbers so
the difference is visible instead of inferred.

Authorization is the caller's job and happens BEFORE this runs. This module
receives a lead query that is already scoped and never widens it.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from typing import Any, Iterable

from app.services import permission_values as pv

# Parked keys worth reporting individually rather than as a count. These are
# the ones that decide whether two populations are the same people.
IDENTITY_PARKED_KEYS = ("current owner", "original owner", "owner",
                        "contact age bucket", "email status", "address status",
                        "lead source", "local lead source", "days since last activity",
                        "last activity date", "created on", "changed fields")

TOP_N = 12


def _parked(lead) -> dict:
    raw = getattr(lead, "custom_fields", None)
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(k).strip().lower(): v for k, v in loaded.items()}


def _top(counter: Counter, n: int = TOP_N) -> list[dict]:
    return [{"value": v, "count": c} for v, c in counter.most_common(n)]


def summarize(leads: Iterable[Any]) -> dict:
    """
    One provenance report for a set of leads. Counts only - no message bodies,
    no addresses, nothing beyond what identifies a BATCH.
    """
    leads = list(leads)
    total = len(leads)

    source_file = Counter()
    import_list = Counter()
    imported_by = Counter()
    source_year = Counter()
    source_category = Counter()
    relationship = Counter()
    permission_source = Counter()

    parked_keys = Counter()
    parked_values: dict[str, Counter] = {k: Counter() for k in IDENTITY_PARKED_KEYS}

    perms = {p: Counter() for p in pv.PERMISSIONS}
    review = 0

    with_contact_date = 0
    with_last_action = 0
    with_status_reason = 0
    with_platform_send = 0
    parked_activity_date_only = 0

    for lead in leads:
        source_file[getattr(lead, "source_file", None) or "(none)"] += 1
        import_list[getattr(lead, "import_list_name", None) or "(none)"] += 1
        imported_by[getattr(lead, "imported_by_name", None) or "(none)"] += 1
        source_year[str(getattr(lead, "source_year", None) or "(none)")] += 1
        source_category[getattr(lead, "source_category", None) or "(none)"] += 1
        relationship[getattr(lead, "relationship_type", None) or "(none)"] += 1
        permission_source[getattr(lead, "permission_source", None) or "(none)"] += 1

        for field, p in (("allow_email", pv.EMAIL), ("allow_bulk_email", pv.BULK_EMAIL),
                         ("allow_sms", pv.SMS), ("allow_voice", pv.VOICE)):
            perms[p][pv.from_bool(getattr(lead, field, None))] += 1
        if getattr(lead, "permission_review", False):
            review += 1

        has_date = isinstance(getattr(lead, "last_contact_date", None), datetime)
        if has_date:
            with_contact_date += 1
        if (getattr(lead, "last_action_raw", None) or "").strip():
            with_last_action += 1
        if (getattr(lead, "status_reason_raw", None) or "").strip():
            with_status_reason += 1
        if isinstance(getattr(lead, "last_messaged_at", None), datetime):
            with_platform_send += 1

        park = _parked(lead)
        for k in park:
            parked_keys[k] += 1
        for k in IDENTITY_PARKED_KEYS:
            if k in park:
                parked_values[k][str(park[k])[:60]] += 1

        # THE NUMBER THAT EXPLAINS A FALSE "NEVER CONTACTED": the lead has an
        # activity date in its PARKED data and none on the record itself.
        if not has_date and any(k in park for k in
                                ("last activity date", "last activity/note",
                                 "last activity", "open activity date")):
            parked_activity_date_only += 1

    return {
        "total": total,
        "batch": {
            "source_file": _top(source_file),
            "import_list_name": _top(import_list),
            "imported_by_name": _top(imported_by),
            "source_year": _top(source_year),
            "source_category": _top(source_category),
            "relationship_type": _top(relationship),
            "permission_source": _top(permission_source),
        },
        "parked_columns": {
            "distinct_keys": len(parked_keys),
            "keys": _top(parked_keys, 40),
            "identity_values": {k: _top(v) for k, v in parked_values.items() if v},
        },
        "permissions": {p: dict(perms[p]) for p in pv.PERMISSIONS},
        "permission_needs_review": review,
        "history": {
            "with_contact_date_on_record": with_contact_date,
            "with_last_action_on_record": with_last_action,
            "with_status_reason_on_record": with_status_reason,
            "with_platform_send": with_platform_send,
            # If this is large, the population is not untouched - the platform
            # simply never read what the file gave it.
            "activity_date_parked_only": parked_activity_date_only,
        },
    }


# ---------------------------------------------------------------------------
# Population comparison - the binding question
# ---------------------------------------------------------------------------

CONFIRMED_SAME = "CONFIRMED_SAME_POPULATION"
PARTIAL_OVERLAP = "PARTIAL_OVERLAP"
DIFFERENT = "DIFFERENT_POPULATION"

# A judgement, stated as a threshold rather than left to a feeling.
SAME_THRESHOLD = 0.95      # of the smaller population
PARTIAL_THRESHOLD = 0.05


def compare_populations(current_keys: set[str], candidate_keys: set[str]) -> dict:
    """
    Compare two identity-key sets and say which of the three answers it is.

    Keys are whatever the caller normalizes to - email, or phone, or a source
    id. The verdict is arithmetic on set overlap and carries the exact counts,
    so a reader can disagree with the threshold without having to redo the work.
    """
    both = current_keys & candidate_keys
    smaller = min(len(current_keys), len(candidate_keys)) or 1
    ratio = len(both) / smaller

    if not both:
        verdict = DIFFERENT
    elif ratio >= SAME_THRESHOLD and len(current_keys) == len(candidate_keys):
        verdict = CONFIRMED_SAME
    elif ratio >= PARTIAL_THRESHOLD:
        verdict = PARTIAL_OVERLAP
    else:
        verdict = DIFFERENT

    return {
        "verdict": verdict,
        "current_count": len(current_keys),
        "candidate_count": len(candidate_keys),
        "in_both": len(both),
        "only_current": len(current_keys - candidate_keys),
        "only_candidate": len(candidate_keys - current_keys),
        "overlap_ratio_of_smaller": round(ratio, 4),
        "thresholds": {"same": SAME_THRESHOLD, "partial": PARTIAL_THRESHOLD},
    }
