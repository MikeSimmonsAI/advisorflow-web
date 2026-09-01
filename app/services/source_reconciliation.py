"""
SOURCE RECONCILIATION - platform capability.

    CURRENT OPERATIONAL RECORDS
            |
    HISTORICAL / IMPORTED SOURCE DATA
            |
          MATCH
            |
          ENRICH
            |
    CONFLICT / REVIEW
            |
        VIABILITY
            |
      QUALIFICATION            (app/services/qualification.py)
            |
        OUTREACH

WHAT THIS MODULE IS FOR
-----------------------
An operational lead row is usually a THIN PROJECTION of a richer historical
record. A CRM export carries dispositions, activity dates, compliance flags,
addresses and sale history that an import may never have mapped onto a column.
Judging a lead's worth from the operational row alone therefore judges it from
an accident of the import mapping, not from what the business actually knows.

This module compares operational records against a historical source, decides
how confident the correspondence is, proposes ENRICHMENT for blanks, reports
CONFLICTS rather than resolving them, and carries compliance in one direction
only: toward the more restrictive state.

WHAT IT MUST NEVER DO
---------------------
  * Never merge on weak evidence. Anything below high confidence is a proposal
    for a human, not a decision.
  * Never overwrite a populated operational value. A disagreement is a
    CONFLICT; silently picking a winner destroys the evidence that there was
    a disagreement at all.
  * Never loosen compliance. If either side says do-not-contact, the result is
    do-not-contact. Enrichment can restrict; it can never reactivate anybody.
  * Never widen authorization. This module receives the records it is given.
    Scoping is the caller's responsibility and is performed BEFORE this runs,
    exactly as qualification does:  AUTHORIZED SCOPE -> RECONCILIATION.

TENANT-AGNOSTIC BY CONSTRUCTION
-------------------------------
No customer, advisor, file name, population size or industry appears anywhere
in this module. It takes two iterables of normalized records and returns
findings. A gate asserts this.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from app.services.dedup_service import normalize_phone, normalize_last_name

# The platform already has ONE definition of "this string is not a usable
# email address". Reimplementing it here would create a second one that drifts.
from app.services.import_service import _check_email_quality


# ---------------------------------------------------------------------------
# Vocabulary - all machine-readable, all published
# ---------------------------------------------------------------------------

MATCHED_HIGH_CONFIDENCE = "MATCHED_HIGH_CONFIDENCE"
MATCHED_REVIEW = "MATCHED_REVIEW"
NO_MATCH = "NO_MATCH"
MULTIPLE_MATCHES = "MULTIPLE_MATCHES"

MATCH_STATUSES = (
    MATCHED_HIGH_CONFIDENCE, MATCHED_REVIEW, NO_MATCH, MULTIPLE_MATCHES,
)

VIABLE_READY = "VIABLE_READY"
VIABLE_LOWER_PRIORITY = "VIABLE_LOWER_PRIORITY"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
DO_NOT_CONTACT = "DO_NOT_CONTACT"
BAD_CONTACT_DATA = "BAD_CONTACT_DATA"
DUPLICATE = "DUPLICATE"
ALREADY_RESOLVED = "ALREADY_RESOLVED"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

VIABILITY_CLASSES = (
    VIABLE_READY, VIABLE_LOWER_PRIORITY, REVIEW_REQUIRED, DO_NOT_CONTACT,
    BAD_CONTACT_DATA, DUPLICATE, ALREADY_RESOLVED, INSUFFICIENT_DATA,
)

# Match rules, strongest first. Each is (code, confidence, status).
#
# Confidence is NOT a probability and is NOT model output. It is a fixed
# number attached to a named rule, so that two records matched the same way
# always carry the same number and a reader can look up what the number means.
RULE_SOURCE_ID = ("source_id_exact", 100, MATCHED_HIGH_CONFIDENCE)
RULE_EMAIL_LAST = ("email_exact_plus_last_name", 96, MATCHED_HIGH_CONFIDENCE)
RULE_PHONE_LAST = ("phone_exact_plus_last_name", 94, MATCHED_HIGH_CONFIDENCE)
RULE_EMAIL_ONLY = ("email_exact_name_absent", 88, MATCHED_HIGH_CONFIDENCE)
RULE_PHONE_FIRST = ("phone_exact_plus_first_name", 84, MATCHED_REVIEW)
RULE_NAME_ZIP = ("full_name_plus_zip", 78, MATCHED_REVIEW)
RULE_NAME_ADDRESS = ("full_name_plus_street", 76, MATCHED_REVIEW)
RULE_PHONE_ONLY = ("phone_exact_name_disagrees", 55, MATCHED_REVIEW)
RULE_NAME_ONLY = ("full_name_only", 40, MATCHED_REVIEW)

MATCH_RULES = (
    RULE_SOURCE_ID, RULE_EMAIL_LAST, RULE_PHONE_LAST, RULE_EMAIL_ONLY,
    RULE_PHONE_FIRST, RULE_NAME_ZIP, RULE_NAME_ADDRESS, RULE_PHONE_ONLY,
    RULE_NAME_ONLY,
)

# A rule at or above this confidence may be treated as the same person without
# a human looking. Everything else is a proposal.
AUTO_CONFIDENCE_FLOOR = 88

# Fields enrichment may fill when the operational value is BLANK.
# Deliberately excludes anything operational, owned, or consent-bearing.
ENRICHABLE_FIELDS = (
    "email", "phone", "street_address", "city", "state", "zip_code",
    "last_contact_date", "last_action", "status_reason",
)

# Fields whose disagreement is reported and never resolved automatically.
CONFLICT_FIELDS = (
    "email", "phone", "street_address", "city", "state", "zip_code",
)

# Never written by reconciliation, at any confidence, for any reason.
# Present as data so a gate can assert it rather than trusting a comment.
PROTECTED_FIELDS = (
    "id", "organization_id", "assigned_to_id", "created_at",
    "status", "sms_consent", "sms_consent_text", "sms_consent_source",
    "sms_consent_timestamp", "sms_consent_ip",
    "manual_flag", "manual_flag_reason", "is_test",
    "case_status", "last_messaged_at",
)

# `bulk_email` is a real, separate permission in most CRMs: a person can be
# emailable while having opted out of campaigns. It is reported and enforced on
# its own, and it is deliberately NOT part of the all-channels-restricted test
# below, because opting out of marketing is not a do-not-contact.
CHANNELS = ("email", "bulk_email", "sms", "voice")
DNC_CHANNELS = ("email", "sms", "voice")


# ---------------------------------------------------------------------------
# Normalized record
# ---------------------------------------------------------------------------

@dataclass
class Record:
    """
    One record from either side, in one shape.

    `raw` keeps everything the adapter saw, so a finding can always be traced
    back to the column it came from.
    """
    key: str = ""                       # caller's own identifier for this row
    source_key: str = ""                # external/system identifier, if any
    first_name: str = ""
    last_name: str = ""
    emails: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()
    street_address: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    last_contact_date: datetime | None = None
    last_action: str = ""
    status_reason: str = ""
    disposition: str = ""
    sale_made: str = ""
    last_sold_date: datetime | None = None
    owner: str = ""
    created_on: datetime | None = None
    # Compliance as the SOURCE states it. None means "the source said nothing",
    # which is not the same as "the source said yes".
    allow_email: bool | None = None
    allow_bulk_email: bool | None = None
    allow_sms: bool | None = None
    allow_voice: bool | None = None
    suppressed: bool = False
    raw: dict = field(default_factory=dict)

    # -- normalized views -------------------------------------------------
    @property
    def norm_emails(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            e for e in (normalize_email(x) for x in self.emails) if e))

    @property
    def norm_phones(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            p for p in (normalize_phone(x) for x in self.phones) if p))

    @property
    def norm_last(self) -> str:
        return normalize_last_name(self.last_name)

    @property
    def norm_first(self) -> str:
        return normalize_last_name(self.first_name)

    @property
    def norm_zip(self) -> str:
        d = re.sub(r"\D", "", self.zip_code or "")
        return d[:5] if len(d) >= 5 else ""

    @property
    def norm_street(self) -> str:
        s = re.sub(r"[^a-z0-9 ]", " ", (self.street_address or "").lower())
        return re.sub(r"\s+", " ", s).strip()

    @property
    def email(self) -> str:
        e = self.norm_emails
        return e[0] if e else ""

    @property
    def phone(self) -> str:
        p = self.norm_phones
        return p[0] if p else ""


def normalize_email(raw: str) -> str:
    """Lowercased and trimmed, or empty when the platform calls it unusable."""
    if not raw:
        return ""
    low = str(raw).strip().lower()
    if not low or "@" not in low:
        return ""
    if _check_email_quality(low):
        return ""
    return low


# ---------------------------------------------------------------------------
# Index over the historical source
# ---------------------------------------------------------------------------

class SourceIndex:
    """
    Lookup structures over a historical source.

    Built once, queried per operational record. Every index is a
    key -> list-of-records mapping, because a real historical file contains
    the same phone on several rows and pretending otherwise is how a match
    lands on the wrong family.
    """

    def __init__(self, records: Iterable[Record]):
        self.records: list[Record] = []
        self.by_source_key: dict[str, list[Record]] = {}
        self.by_email: dict[str, list[Record]] = {}
        self.by_phone: dict[str, list[Record]] = {}
        self.by_name: dict[tuple[str, str], list[Record]] = {}
        for r in records:
            i = len(self.records)
            self.records.append(r)
            if r.source_key:
                self.by_source_key.setdefault(str(r.source_key).strip(), []).append(r)
            for e in r.norm_emails:
                self.by_email.setdefault(e, []).append(r)
            for p in r.norm_phones:
                self.by_phone.setdefault(p, []).append(r)
            if r.norm_last:
                self.by_name.setdefault((r.norm_last, r.norm_first), []).append(r)
            del i

    def __len__(self) -> int:
        return len(self.records)

    def candidates(self, t: Record) -> list[Record]:
        seen: dict[int, Record] = {}
        buckets: list[list[Record]] = []
        if t.source_key:
            buckets.append(self.by_source_key.get(str(t.source_key).strip(), []))
        for e in t.norm_emails:
            buckets.append(self.by_email.get(e, []))
        for p in t.norm_phones:
            buckets.append(self.by_phone.get(p, []))
        if t.norm_last:
            buckets.append(self.by_name.get((t.norm_last, t.norm_first), []))
        for b in buckets:
            for r in b:
                seen[id(r)] = r
        return list(seen.values())


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def score_pair(t: Record, s: Record) -> tuple[str, int, str] | None:
    """
    Returns the STRONGEST rule that fires for this pair, or None.

    Order matters and is the hierarchy: strongest identifier first, name and
    address evidence next, and a bare identifier with disagreeing names last -
    where it is a review item, never an automatic merge.
    """
    if t.source_key and s.source_key and \
            str(t.source_key).strip() == str(s.source_key).strip():
        return RULE_SOURCE_ID

    shared_email = set(t.norm_emails) & set(s.norm_emails)
    shared_phone = set(t.norm_phones) & set(s.norm_phones)

    last_known = bool(t.norm_last) and bool(s.norm_last)
    last_agrees = last_known and t.norm_last == s.norm_last
    first_agrees = bool(t.norm_first) and bool(s.norm_first) and \
        t.norm_first == s.norm_first

    if shared_email and last_agrees:
        return RULE_EMAIL_LAST
    if shared_phone and last_agrees:
        return RULE_PHONE_LAST
    if shared_email and not last_known:
        # An address matched but one side has no surname to corroborate it.
        # Still strong: a personal mailbox is a person.
        return RULE_EMAIL_ONLY
    if shared_phone and first_agrees:
        return RULE_PHONE_FIRST
    if last_agrees and first_agrees:
        if t.norm_zip and t.norm_zip == s.norm_zip:
            return RULE_NAME_ZIP
        if t.norm_street and t.norm_street == s.norm_street:
            return RULE_NAME_ADDRESS
    if shared_phone:
        # A shared household or recycled number. Never automatic.
        return RULE_PHONE_ONLY
    if last_agrees and first_agrees:
        return RULE_NAME_ONLY
    return None


def match_record(t: Record, index: SourceIndex) -> dict:
    """
    Match ONE operational record against the source index.

    Returns a finding with a status, the winning rule, its confidence, and
    every other candidate that fired at the same strength - because
    'two records matched equally well' is an answer, not a tie to break.
    """
    scored: list[tuple[int, str, str, Record]] = []
    for s in index.candidates(t):
        rule = score_pair(t, s)
        if rule:
            code, conf, status = rule
            scored.append((conf, code, status, s))

    if not scored:
        return {
            "match_status": NO_MATCH,
            "match_confidence": 0,
            "match_rule": None,
            "reason_codes": ["no_candidate_in_source"],
            "matched": None,
            "alternates": [],
        }

    scored.sort(key=lambda x: -x[0])
    best_conf = scored[0][0]
    top = [x for x in scored if x[0] == best_conf]
    # Distinct people, not distinct rows: the same person appearing twice in a
    # historical export is a duplicate in the SOURCE, not an ambiguous match.
    distinct = {_identity_key(x[3]) for x in top}

    conf, code, status, best = top[0]
    reasons = [code]

    if len(distinct) > 1:
        return {
            "match_status": MULTIPLE_MATCHES,
            "match_confidence": conf,
            "match_rule": code,
            "reason_codes": [code, "multiple_distinct_source_records"],
            "matched": best,
            "alternates": [x[3] for x in top[1:]],
        }

    if len(top) > 1:
        reasons.append("duplicate_rows_in_source")

    return {
        "match_status": status,
        "match_confidence": conf,
        "match_rule": code,
        "reason_codes": reasons,
        "matched": best,
        "alternates": [x[3] for x in top[1:]],
    }


def _identity_key(r: Record) -> str:
    if r.source_key:
        return f"src:{str(r.source_key).strip()}"
    e = r.email
    p = r.phone
    return f"id:{r.norm_last}|{r.norm_first}|{e}|{p}"


# ---------------------------------------------------------------------------
# Enrichment and conflict
# ---------------------------------------------------------------------------

def _value(rec: Record, field_name: str):
    if field_name == "email":
        return rec.email
    if field_name == "phone":
        return rec.phone
    return getattr(rec, field_name, None)


def _blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == ""
    return False


def propose_enrichment(t: Record, s: Record, confidence: int) -> dict:
    """
    BLANK-FILL ONLY.

    A blank operational value plus a present historical value is a candidate.
    A populated operational value that disagrees is a CONFLICT and is returned
    as one. Nothing here writes anything; every item is a proposal carrying the
    field, both values, and the confidence of the match that produced it.
    """
    fills: list[dict] = []
    conflicts: list[dict] = []

    for f in ENRICHABLE_FIELDS:
        cur = _value(t, f)
        hist = _value(s, f)
        if _blank(hist):
            continue
        if _blank(cur):
            fills.append({
                "field": f,
                "current": None,
                "proposed": hist,
                "confidence": confidence,
                "auto_applicable": confidence >= AUTO_CONFIDENCE_FLOOR,
                "reason_code": "blank_fill",
            })
        elif f in CONFLICT_FIELDS and _differs(f, cur, hist):
            conflicts.append({
                "field": f,
                "current": cur,
                "historical": hist,
                "confidence": confidence,
                "reason_code": "value_conflict",
            })

    return {"fills": fills, "conflicts": conflicts}


def _differs(field_name: str, a, b) -> bool:
    if field_name == "phone":
        return normalize_phone(str(a)) != normalize_phone(str(b))
    if field_name == "email":
        return normalize_email(str(a)) != normalize_email(str(b))
    if field_name == "zip_code":
        da = re.sub(r"\D", "", str(a))[:5]
        db = re.sub(r"\D", "", str(b))[:5]
        return bool(da) and bool(db) and da != db
    ca = re.sub(r"\s+", " ", str(a).strip().lower())
    cb = re.sub(r"\s+", " ", str(b).strip().lower())
    return ca != cb


# ---------------------------------------------------------------------------
# Compliance - one direction only
# ---------------------------------------------------------------------------

def reconcile_compliance(t: Record, s: Record | None) -> dict:
    """
    The MORE RESTRICTIVE state wins, per channel.

    A source that says nothing about a channel (None) cannot grant permission
    and cannot remove it. A source that says 'do not allow' removes it even
    when the operational record currently allows it. There is no path through
    this function that turns a restricted channel back on - that is the whole
    point of it, and a gate asserts it.
    """
    out: dict[str, Any] = {"channels": {}, "findings": [], "discovered_restriction": False}
    for ch in CHANNELS:
        cur = getattr(t, f"allow_{ch}", None)
        hist = getattr(s, f"allow_{ch}", None) if s else None
        # False beats None beats True: restriction is sticky, permission is not.
        if cur is False or hist is False:
            resolved = False
        elif cur is True or hist is True:
            resolved = True
        else:
            resolved = None
        out["channels"][ch] = resolved
        if hist is False and cur is not False:
            out["discovered_restriction"] = True
            out["findings"].append({
                "channel": ch,
                "reason_code": "historical_restriction_discovered",
                "current": cur,
                "historical": hist,
                "resolved": False,
            })
        if cur is False and hist is True:
            out["findings"].append({
                "channel": ch,
                "reason_code": "historical_permission_ignored_current_is_restrictive",
                "current": cur,
                "historical": hist,
                "resolved": False,
            })
    if (s and s.suppressed) or t.suppressed:
        out["suppressed"] = True
        if s and s.suppressed and not t.suppressed:
            out["discovered_restriction"] = True
            out["findings"].append({
                "channel": "all",
                "reason_code": "historical_suppression_discovered",
                "resolved": False,
            })
    else:
        out["suppressed"] = False
    return out


# ---------------------------------------------------------------------------
# Viability
# ---------------------------------------------------------------------------

RESOLVED_MARKERS = ("contract sold", "sold", "closed won", "purchased")
DEAD_MARKERS = ("non viable", "non-viable", "not viable", "deceased",
                "do not contact", "do not call", "disqualified", "lost")


def _has_marker(text: str, markers: tuple[str, ...]) -> str | None:
    low = (text or "").strip().lower()
    if not low:
        return None
    for m in markers:
        if m in low:
            return m
    return None


def classify_viability(t: Record, finding: dict, compliance: dict,
                       enrichment: dict, now: datetime | None = None) -> dict:
    """
    Classify AFTER reconciliation, from evidence on both sides.

    Order is deliberate: the states that forbid or invalidate outreach are
    decided before the states that rank it, so nothing can be promoted past a
    restriction by scoring well on something else.
    """
    now = now or datetime.now(timezone.utc)
    s: Record | None = finding.get("matched")
    reasons: list[str] = []

    def enriched(field_name):
        cur = _value(t, field_name)
        if not _blank(cur):
            return cur
        for f in enrichment.get("fills", []):
            if f["field"] == field_name:
                return f["proposed"]
        return None

    # 1. Forbidden
    if compliance.get("suppressed"):
        return _v(DO_NOT_CONTACT, ["suppressed"])
    if all(compliance["channels"].get(c) is False for c in DNC_CHANNELS):
        return _v(DO_NOT_CONTACT, ["all_channels_restricted"])
    marker = _has_marker(t.status_reason, DEAD_MARKERS) or \
        _has_marker(t.last_action, DEAD_MARKERS) or \
        (_has_marker(s.status_reason, DEAD_MARKERS) if s else None) or \
        (_has_marker(s.disposition, DEAD_MARKERS) if s else None)
    if marker:
        return _v(DO_NOT_CONTACT, [f"marked_{marker.replace(' ', '_')}"])

    # 2. Ambiguous identity
    if finding["match_status"] == MULTIPLE_MATCHES:
        return _v(REVIEW_REQUIRED, ["multiple_distinct_source_records"])
    if enrichment.get("conflicts"):
        return _v(REVIEW_REQUIRED,
                  ["conflict_" + c["field"] for c in enrichment["conflicts"]])
    if finding["match_status"] == MATCHED_REVIEW:
        return _v(REVIEW_REQUIRED, ["match_below_auto_confidence",
                                    finding.get("match_rule") or ""])

    # 3. Unusable
    if not enriched("email") and not enriched("phone"):
        return _v(BAD_CONTACT_DATA, ["no_usable_email_or_phone"])

    # 4. Already dealt with
    sold = _has_marker(t.status_reason, RESOLVED_MARKERS) or \
        (_has_marker(s.status_reason, RESOLVED_MARKERS) if s else None) or \
        (_has_marker(s.sale_made, ("yes", "true")) if s else None)
    if sold:
        return _v(ALREADY_RESOLVED, ["prior_sale_on_record"])

    # 5. Ranked
    if finding["match_status"] == NO_MATCH:
        thin = _blank(t.last_action) and t.last_contact_date is None and \
            _blank(t.status_reason)
        if thin:
            return _v(INSUFFICIENT_DATA, ["no_match_and_no_local_history"])
        reasons.append("no_match_local_history_only")

    evidence = _engagement_evidence(t, s, enriched)
    if evidence:
        return _v(VIABLE_READY, reasons + evidence)
    return _v(VIABLE_LOWER_PRIORITY, reasons + ["reachable_no_engagement_evidence"])


def _engagement_evidence(t: Record, s: Record | None, enriched) -> list[str]:
    """
    Real, per-person evidence only.

    Being present in a file is not evidence. Belonging to an organization is
    not evidence. A disposition, a logged action or a dated activity is.
    """
    ev: list[str] = []
    action = t.last_action or (s.last_action if s else "")
    if action and action.strip():
        ev.append("historical_action_on_record")
    date = enriched("last_contact_date") or (s.last_contact_date if s else None)
    if isinstance(date, datetime):
        ev.append("historical_activity_date")
    reason = (t.status_reason or (s.status_reason if s else "") or "").strip().lower()
    if reason and reason not in ("new", "open", "none"):
        ev.append(f"disposition_{re.sub(r'[^a-z0-9]+', '_', reason).strip('_')}")
    return ev


def _v(cls: str, reasons: list[str]) -> dict:
    return {"viability": cls, "viability_reasons": [r for r in reasons if r]}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def reconcile(targets: Iterable[Record], index: SourceIndex,
              now: datetime | None = None) -> list[dict]:
    """
    Reconcile a population of operational records against a source index.

    READ-ONLY. Returns findings. Writes nothing, sends nothing, and has no
    database handle to do either with.
    """
    out = []
    for t in targets:
        finding = match_record(t, index)
        s = finding["matched"]
        conf = finding["match_confidence"]
        enrichment = (propose_enrichment(t, s, conf)
                      if s and finding["match_status"] != MULTIPLE_MATCHES
                      else {"fills": [], "conflicts": []})
        compliance = reconcile_compliance(t, s)
        viability = classify_viability(t, finding, compliance, enrichment, now)
        out.append({
            "key": t.key,
            "match_status": finding["match_status"],
            "match_confidence": conf,
            "match_rule": finding["match_rule"],
            "reason_codes": finding["reason_codes"],
            "source_key": (s.source_key if s else None),
            "alternate_count": len(finding["alternates"]),
            "enrichment": enrichment,
            "compliance": compliance,
            **viability,
            "target": t,
            "source": s,
        })
    return out


def summarize(findings: list[dict]) -> dict:
    """Counts only. Every number here is a count of findings, never an estimate."""
    from collections import Counter
    match_counts = Counter(f["match_status"] for f in findings)
    via_counts = Counter(f["viability"] for f in findings)
    fills = Counter()
    for f in findings:
        for item in f["enrichment"]["fills"]:
            fills[item["field"]] += 1
    conflicts = Counter()
    for f in findings:
        for item in f["enrichment"]["conflicts"]:
            conflicts[item["field"]] += 1
    restrictions = sum(1 for f in findings
                       if f["compliance"].get("discovered_restriction"))
    rules = Counter(f["match_rule"] for f in findings if f["match_rule"])
    return {
        "total": len(findings),
        "match_status": {k: match_counts.get(k, 0) for k in MATCH_STATUSES},
        "match_rules": dict(rules),
        "viability": {k: via_counts.get(k, 0) for k in VIABILITY_CLASSES},
        "enrichment_fills": dict(fills),
        "conflicts": dict(conflicts),
        "compliance_restrictions_discovered": restrictions,
    }


def vocabulary() -> dict:
    """Published so a UI renders what the engine returns, never a hardcoded list."""
    return {
        "match_statuses": list(MATCH_STATUSES),
        "viability_classes": list(VIABILITY_CLASSES),
        "match_rules": [
            {"code": c, "confidence": n, "status": s} for c, n, s in MATCH_RULES
        ],
        "auto_confidence_floor": AUTO_CONFIDENCE_FLOOR,
        "dnc_channels": list(DNC_CHANNELS),
        "enrichable_fields": list(ENRICHABLE_FIELDS),
        "conflict_fields": list(CONFLICT_FIELDS),
        "protected_fields": list(PROTECTED_FIELDS),
        "channels": list(CHANNELS),
    }
