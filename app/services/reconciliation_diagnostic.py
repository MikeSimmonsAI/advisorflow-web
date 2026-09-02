"""GOD-ONLY, READ-ONLY: bind operational Leads to staged historical evidence.

WHAT THIS ANSWERS

The qualification diagnostic can say how many of a subject's leads qualify. It
cannot say WHICH historical record each of those leads corresponds to, and
without that the reconciliation findings and the operational rows are two lists
that cannot be joined - a report about families nobody can act on, because no
row in it names a Lead.

This closes that gap by exposing, for every lead in the subject's ALREADY
AUTHORIZED set:

    current_lead_id           the operational Lead id
    historical_contact_guid   the source system's own contact identifier
    match_status              MATCHED_HIGH_CONFIDENCE / MATCHED_REVIEW /
                              NO_MATCH / MULTIPLE_MATCHES
    match_confidence          the fixed number attached to the rule that fired

WHAT IT DELIBERATELY DOES NOT DO

  - It does not choose the population. It reconciles the list it is handed,
    which is the subject's own authorized lead query. It cannot widen that set,
    because it never queries Leads at all.
  - It names no customer, no advisor and no file. The historical side is
    whatever SourceRecord rows exist for the workspace being examined.
  - It invents no match. If no historical source has been staged for that
    workspace, every row reports NO_SOURCE_LAYER and says so - an empty
    reconciliation layer is a fact to report, not a reason to guess.
  - It writes nothing and sends nothing. SourceRecord is historical evidence,
    never an operational lead, and nothing here promotes one to the other.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.models.source_records import SourceRecord
from app.services import source_reconciliation as sr
from app.services.source_adapters import leads_to_records

# A book can be large. The caller gets every row up to this, and is TOLD when
# it was cut rather than being handed a short list that looks complete.
MAX_ROWS = 2000

NO_SOURCE_LAYER = "NO_SOURCE_LAYER"


def _source_record_to_record(s: SourceRecord) -> sr.Record:
    """A staged historical row in the reconciliation engine's own shape."""
    emails = [e for e in (s.email, s.email_alt) if e]
    phones = [p for p in (s.phone, s.mobile_phone) if p]
    return sr.Record(
        key=s.id,
        source_key=(s.source_key or ""),
        first_name=(s.first_name or ""),
        last_name=(s.last_name or ""),
        emails=tuple(emails),
        phones=tuple(phones),
        street_address=(s.street_address or ""),
        city=(s.city or ""),
        state=(s.state or ""),
        zip_code=(s.zip_code or ""),
        last_contact_date=s.last_activity_at,
        last_action=(s.last_action or ""),
        status_reason=(s.status_reason or ""),
        sale_made=("yes" if s.sale_made else ""),
        last_sold_date=s.last_sold_at,
        owner=(s.owner_name or ""),
        created_on=s.source_created_at,
        allow_email=s.allow_email,
        allow_bulk_email=s.allow_bulk_email,
        allow_sms=s.allow_sms,
        allow_voice=s.allow_voice,
    )


def run(db: Session, leads: Iterable[Any],
        organization_id: Optional[str]) -> Dict[str, Any]:
    """Reconcile an already-authorized lead set against its workspace's
    staged historical records. READ-ONLY."""
    leads = list(leads)
    out: Dict[str, Any] = {
        "organization_id": organization_id,
        "leads_reconciled": len(leads),
        "source_records_available": 0,
        "rows": [],
        "truncated": False,
    }

    if not organization_id:
        out["note"] = ("No workspace resolved for this run, so there is no "
                       "tenant whose historical records could be loaded.")
        return out

    # TENANT SCOPE IS NOT OPTIONAL. The historical side is filtered by the same
    # organization the subject resolved into, so a diagnostic can never match a
    # lead against another customer's history.
    sources = (db.query(SourceRecord)
               .filter(SourceRecord.organization_id == organization_id)
               .all())
    out["source_records_available"] = len(sources)

    if not sources:
        out["note"] = ("No historical source records are staged for this "
                       "workspace, so no lead can be bound to one. This is "
                       "reported as NO_SOURCE_LAYER rather than guessed.")
        out["rows"] = [
            {"current_lead_id": getattr(l, "id", None),
             "historical_contact_guid": None,
             "match_status": NO_SOURCE_LAYER,
             "match_confidence": 0}
            for l in leads[:MAX_ROWS]
        ]
        out["truncated"] = len(leads) > MAX_ROWS
        return out

    index = sr.SourceIndex([_source_record_to_record(s) for s in sources])
    targets = leads_to_records(leads)

    rows: List[Dict[str, Any]] = []
    for lead, t in zip(leads, targets):
        if len(rows) >= MAX_ROWS:
            out["truncated"] = True
            break
        finding = sr.match_record(t, index)
        matched = finding["matched"]
        rows.append({
            "current_lead_id": getattr(lead, "id", None),
            "historical_contact_guid": (matched.source_key if matched else None),
            "match_status": finding["match_status"],
            "match_confidence": finding["match_confidence"],
            "match_rule": finding["match_rule"],
            "alternate_count": len(finding["alternates"]),
        })
    out["rows"] = rows

    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["match_status"]] = counts.get(r["match_status"], 0) + 1
    out["match_status_counts"] = counts
    out["bound_to_history"] = sum(
        1 for r in rows if r["historical_contact_guid"])
    return out
