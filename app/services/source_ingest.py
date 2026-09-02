"""
STAGE A HISTORICAL SOURCE - without creating a single operational lead.

    classify_columns(header)     -> what every column is, and where it lands
    build_source_record(row, n)  -> one normalized SourceRecord (unsaved)
    build_opportunity(row, n)    -> one normalized SourceOpportunity (unsaved)
    stage_records(db, ...)       -> persist a batch, tenant-scoped

There is no function in this module that constructs a `Lead`, and none that
sends anything. Staging a hundred thousand historical rows must not create a
hundred thousand sendable records, and the way to guarantee that is for the
code that does the staging to have no way to make one.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.import_models import (ImportBatch, SourceKind, SourceOpportunity,
                                      SourceRecord)
from app.services import permission_values as pv
from app.services.dedup_service import normalize_phone, normalize_last_name

# ---------------------------------------------------------------------------
# Column classification vocabulary - the audit answer, as data
# ---------------------------------------------------------------------------

# Destination
MAPPED = "MAPPED"                          # lands in a real column
CUSTOM_FIELDS_ONLY = "CUSTOM_FIELDS_ONLY"  # kept, but only inside the JSON blob
IGNORED = "IGNORED"                        # not carried at all

# Purpose
COMPLIANCE = "COMPLIANCE"
HISTORICAL_ACTIVITY = "HISTORICAL_ACTIVITY"
IDENTITY = "IDENTITY"
PROVENANCE = "PROVENANCE"
COMMERCIAL = "COMMERCIAL"
OTHER = "OTHER"

DESTINATIONS = (MAPPED, CUSTOM_FIELDS_ONLY, IGNORED)
PURPOSES = (COMPLIANCE, HISTORICAL_ACTIVITY, IDENTITY, PROVENANCE, COMMERCIAL, OTHER)

IDENTITY_COLUMNS = {
    "full name", "first name", "middle name", "last name", "phone", "email",
    "mobile phone", "alt phone", "home phone", "home phone 2", "assistant phone",
    "email address 2", "email address 3", "street address", "city", "state",
    "zip code", "phone e164", "address 1: phone", "address 1: telephone 2",
    "address 1: telephone 3", "address 2: telephone 1", "address 2: telephone 2",
    "address 2: telephone 3", "address 3: telephone1", "address 3: telephone2",
    "address 3: telephone3", "purchaser", "beneficiary", "contact",
}

HISTORICAL_ACTIVITY_COLUMNS = {
    "last activity date", "last activity/note", "last activity", "last action",
    "open activity date", "last logged activity", "last contact date",
    "open activity count", "next activity date", "days since last completed activity",
    "last marketing email", "status reason", "last assigned date",
    "last assigned by", "last activity type", "days since last activity",
}

COMMERCIAL_COLUMNS = {
    "sale made?", "last sale type", "last sold date", "contract #", "contract date",
    "contract total", "contract type", "contract need", "contract cancelled",
    "contract close status", "actual revenue", "actual close date", "date signed",
    "heritage $", "funeral $", "service & merchandise $", "status",
}

PROVENANCE_COLUMNS = {
    "(do not modify) contact", "(do not modify) opportunity",
    "(do not modify) row checksum", "(do not modify) modified on",
    "created on", "created by", "modified on", "modified by", "owner",
    "current owner", "original owner", "record created on", "lead date",
    "lead source", "local lead source", "location", "changed fields",
    "opportunity id", "lead id", "leadid", "lead type", "seminar lead?",
    "sales advisor", "sales advisor(text)", "sales advisor (non-crm user)",
    "sales type (hmis)", "originating lead qualification", "originating prospect",
    "current modified on", "topic",
}


def purpose_of(header_lower: str) -> str:
    """What KIND of fact a column carries. Compliance is checked first."""
    if header_lower in pv.ALL_PERMISSION_COLUMNS:
        return COMPLIANCE
    if header_lower in HISTORICAL_ACTIVITY_COLUMNS:
        return HISTORICAL_ACTIVITY
    if header_lower in IDENTITY_COLUMNS:
        return IDENTITY
    if header_lower in COMMERCIAL_COLUMNS:
        return COMMERCIAL
    if header_lower in PROVENANCE_COLUMNS:
        return PROVENANCE
    return OTHER


# Columns this module writes into a real SourceRecord/SourceOpportunity field.
STAGED_COLUMNS = (
    set(pv.ALL_PERMISSION_COLUMNS)
    | HISTORICAL_ACTIVITY_COLUMNS
    | IDENTITY_COLUMNS
    | COMMERCIAL_COLUMNS
    | PROVENANCE_COLUMNS
)


def classify_columns(header: Iterable[str], operational_lookup: dict | None = None) -> list[dict]:
    """
    Classify every column of a source file.

    `operational_lookup` is `import_service._build_column_lookup(header)` when
    the caller wants the answer for the OPERATIONAL importer as well - which is
    the interesting comparison, because a column can be MAPPED here and
    CUSTOM_FIELDS_ONLY there.

    Returns one row per column: name, purpose, where it lands in each pipeline.
    Nothing here guesses; a column is classified by exact membership in the
    tables above, and anything unrecognised is honestly reported as OTHER
    rather than filed somewhere plausible.
    """
    operational_cols = set()
    if operational_lookup:
        operational_cols = {str(c).strip().lower() for c in operational_lookup.values()}

    out = []
    for col in header:
        name = str(col).strip()
        low = name.lower()
        if not name:
            continue
        purpose = purpose_of(low)
        staged = MAPPED if low in STAGED_COLUMNS else CUSTOM_FIELDS_ONLY
        if operational_lookup is None:
            operational = None
        elif low in operational_cols:
            operational = MAPPED
        elif low in pv.ALL_PERMISSION_COLUMNS:
            # After the compliance fix these are read by the permission table
            # rather than by the header map, and are excluded from the parked
            # set - so they are mapped, just not through `lookup`.
            operational = MAPPED
        else:
            operational = CUSTOM_FIELDS_ONLY
        out.append({
            "column": name,
            "purpose": purpose,
            "staged_destination": staged,
            "operational_destination": operational,
        })
    return out


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------

def _s(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in pv.BLANK_VALUES else s


def _date(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    s = _s(v)
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
                "%d/%m/%Y", "%m-%d-%Y", "%b %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(s[:len(fmt) + 8], fmt)
        except ValueError:
            continue
    return None


def _money(v) -> float | None:
    s = _s(v).replace("$", "").replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _yes(v) -> bool | None:
    """Yes/No as a tri-state. Not a permission - use permission_values for those."""
    s = _s(v).lower()
    if not s:
        return None
    if s in pv.BOOL_TRUE:
        return True
    if s in pv.BOOL_FALSE:
        return False
    return None


def _first(low: dict, names: Iterable[str]):
    for n in names:
        if n in low and _s(low[n]):
            return low[n]
    return None


def _norm_zip(v) -> str:
    d = re.sub(r"\D", "", _s(v))
    return d[:5] if len(d) >= 5 else ""


def split_full_name(full: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"\s+", (full or "").strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return "", parts[0]
    return parts[0], parts[-1]


# ---------------------------------------------------------------------------
# Builders. They return UNSAVED objects; persisting is the caller's decision.
# ---------------------------------------------------------------------------

CONTACT_KEY_COLUMNS = ("(do not modify) contact", "contact id", "contactid",
                       "crm id", "external id", "source id", "record id")

PHONE_COLUMNS = ("phone e164", "phone", "mobile phone", "alt phone", "home phone",
                 "home phone 2", "assistant phone", "address 1: phone",
                 "address 1: telephone 2", "address 1: telephone 3",
                 "address 2: telephone 1", "address 2: telephone 2",
                 "address 2: telephone 3", "address 3: telephone1",
                 "address 3: telephone2", "address 3: telephone3")

ACTIVITY_DATE_COLUMNS = ("last activity date", "last activity/note", "last activity",
                         "last contact date", "last logged activity",
                         "last completed activity")


def build_source_record(row: dict, organization_id: str, row_number: int = 0,
                        batch: ImportBatch | None = None) -> SourceRecord:
    """One raw row -> one normalized, auditable SourceRecord. Nothing is sent."""
    low = {str(k).strip().lower(): v for k, v in row.items() if k is not None}

    first = _s(_first(low, ("first name", "firstname", "fname")))
    last = _s(_first(low, ("last name", "lastname", "lname", "surname")))
    full = _s(_first(low, ("full name", "fullname", "name")))
    if not last and full:
        f2, l2 = split_full_name(full)
        last, first = l2, (first or f2)

    email = _s(_first(low, ("email", "email address", "e-mail")))
    email_alt = _s(_first(low, ("email address 2", "email address 3")))
    phone = _s(_first(low, PHONE_COLUMNS))
    mobile = _s(low.get("mobile phone"))
    all_phones = [_s(low[c]) for c in PHONE_COLUMNS if c in low and _s(low[c])]

    # EVERY date column is consulted and the most recent wins. Preferring one
    # column silently is how a record with three dates reports the oldest.
    dates = [d for d in (_date(low.get(c)) for c in ACTIVITY_DATE_COLUMNS) if d]

    perms = pv.read_all(low)
    p = perms["permissions"]

    return SourceRecord(
        organization_id=organization_id,
        import_batch_id=(batch.id if batch else None),
        source_system=(batch.source_system if batch else None),
        source_entity="contact",
        source_key=_s(_first(low, CONTACT_KEY_COLUMNS)) or None,
        source_row_number=row_number,
        row_checksum=_s(low.get("(do not modify) row checksum")) or None,
        raw_json=json.dumps({k: (v.isoformat() if isinstance(v, datetime) else _s(v))
                             for k, v in low.items() if _s(v)}),

        first_name=first or None,
        last_name=last or None,
        full_name=full or None,
        norm_first_name=normalize_last_name(first) or None,
        norm_last_name=normalize_last_name(last) or None,

        email=email or None,
        norm_email=(email.lower() if email else None),
        email_alt=email_alt or None,

        phone=phone or None,
        norm_phone=normalize_phone(phone) or None,
        mobile_phone=mobile or None,
        norm_mobile_phone=normalize_phone(mobile) or None,
        phones_json=json.dumps(all_phones) if all_phones else None,

        street_address=_s(_first(low, ("street address", "address", "address 1"))) or None,
        city=_s(low.get("city")) or None,
        state=_s(low.get("state")) or None,
        zip_code=_s(_first(low, ("zip code", "zip", "postal code"))) or None,
        norm_zip=_norm_zip(_first(low, ("zip code", "zip", "postal code"))) or None,

        allow_email=pv.to_bool(p.get(pv.EMAIL, pv.UNKNOWN)),
        allow_bulk_email=pv.to_bool(p.get(pv.BULK_EMAIL, pv.UNKNOWN)),
        allow_sms=pv.to_bool(p.get(pv.SMS, pv.UNKNOWN)),
        allow_voice=pv.to_bool(p.get(pv.VOICE, pv.UNKNOWN)),
        permission_review=bool(perms["needs_review"]),
        permission_raw=json.dumps(perms["evidence"]),

        last_activity_at=(max(dates) if dates else None),
        # AN ACTION, NEVER A DATE. "last logged activity" holds a timestamp in
        # these exports and is deliberately absent from this list.
        last_action=_s(_first(low, ("last action", "last activity type",
                                    "last call result"))) or None,
        open_activity_at=_date(low.get("open activity date")),
        last_assigned_at=_date(low.get("last assigned date")),
        activity_count=(int(_s(low["open activity count"]))
                        if _s(low.get("open activity count", "")).isdigit() else None),

        status_reason=_s(low.get("status reason")) or None,
        lead_type=_s(low.get("lead type")) or None,
        lead_source=_s(_first(low, ("lead source", "local lead source"))) or None,
        owner_name=_s(_first(low, ("owner", "current owner"))) or None,
        original_owner_name=_s(low.get("original owner")) or None,

        sale_made=_yes(low.get("sale made?")),
        last_sold_at=_date(low.get("last sold date")),
        last_sale_type=_s(low.get("last sale type")) or None,

        source_created_at=_date(_first(low, ("created on", "record created on",
                                             "lead date"))),
        source_modified_at=_date(_first(low, ("(do not modify) modified on",
                                              "modified on", "current modified on"))),
    )


OPPORTUNITY_CONTACT_KEYS = ("leadid", "lead id", "contact")


def build_opportunity(row: dict, organization_id: str, row_number: int = 0,
                      batch: ImportBatch | None = None,
                      contact_key_column: str = "leadid") -> SourceOpportunity:
    """
    One opportunity row -> one SourceOpportunity.

    `contact_key_column` is EXPLICIT rather than guessed. An export can carry
    three columns that all look like a contact reference and only one of them
    actually holds the key the contact master uses; picking by name similarity
    attaches contracts to the wrong people. The caller establishes which column
    joins - by measuring it - and says so here.
    """
    low = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    return SourceOpportunity(
        organization_id=organization_id,
        import_batch_id=(batch.id if batch else None),
        source_key=_s(_first(low, ("(do not modify) opportunity", "opportunity id"))) or None,
        contact_source_key=_s(low.get(contact_key_column)) or None,
        source_row_number=row_number,
        raw_json=json.dumps({k: (v.isoformat() if isinstance(v, datetime) else _s(v))
                             for k, v in low.items() if _s(v)}),
        status=_s(low.get("status")) or None,
        status_reason=_s(low.get("status reason")) or None,
        close_status=_s(low.get("contract close status")) or None,
        cancelled=_yes(low.get("contract cancelled")),
        contract_number=_s(low.get("contract #")) or None,
        contract_type=_s(low.get("contract type")) or None,
        contract_need=_s(low.get("contract need")) or None,
        contract_total=_money(low.get("contract total")),
        contract_at=_date(low.get("contract date")),
        actual_close_at=_date(low.get("actual close date")),
        location=_s(low.get("location")) or None,
        advisor_name=_s(_first(low, ("sales advisor", "sales advisor(text)"))) or None,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def open_batch(db: Session, organization_id: str, *, source_filename: str,
               source_system: str = "", name: str = "", uploaded_by_id: str = None,
               uploaded_by_name: str = "", header: list[str] | None = None,
               mapping: Any = None, source_year: int = None) -> ImportBatch:
    batch = ImportBatch(
        organization_id=organization_id,
        name=name or source_filename,
        source_filename=source_filename,
        source_system=source_system or None,
        kind=SourceKind.HISTORICAL.value,
        column_count=(len(header) if header else None),
        header_json=json.dumps(list(header)) if header else None,
        mapping_json=json.dumps(mapping) if mapping is not None else None,
        uploaded_by_id=uploaded_by_id,
        uploaded_by_name=uploaded_by_name or None,
        source_year=source_year,
        status="staged",
    )
    db.add(batch)
    db.flush()
    return batch


def stage_records(db: Session, organization_id: str, batch: ImportBatch,
                  rows: Iterable[dict], *, commit_every: int = 1000) -> dict:
    """
    Persist staged contact records for ONE organization.

    `organization_id` is applied to every row from the argument, never from the
    file: a source file cannot name a tenant it should land in.
    """
    n = 0
    denials = {p: 0 for p in pv.PERMISSIONS}
    review = 0
    with_key = 0
    for i, row in enumerate(rows, start=1):
        rec = build_source_record(row, organization_id, row_number=i, batch=batch)
        db.add(rec)
        n += 1
        if rec.source_key:
            with_key += 1
        if rec.permission_review:
            review += 1
        for field, p in (("allow_email", pv.EMAIL), ("allow_bulk_email", pv.BULK_EMAIL),
                         ("allow_sms", pv.SMS), ("allow_voice", pv.VOICE)):
            if getattr(rec, field) is False:
                denials[p] += 1
        if commit_every and n % commit_every == 0:
            db.flush()
    batch.row_count = n
    batch.status = "loaded"
    batch.completed_at = datetime.utcnow()
    return {"staged": n, "with_source_key": with_key,
            "permission_denials": denials, "permission_needs_review": review}


def stage_opportunities(db: Session, organization_id: str, batch: ImportBatch,
                        rows: Iterable[dict], *, contact_key_column: str,
                        commit_every: int = 1000) -> dict:
    n = 0
    joinable = 0
    for i, row in enumerate(rows, start=1):
        opp = build_opportunity(row, organization_id, row_number=i, batch=batch,
                                contact_key_column=contact_key_column)
        db.add(opp)
        n += 1
        if opp.contact_source_key:
            joinable += 1
        if commit_every and n % commit_every == 0:
            db.flush()
    batch.row_count = n
    batch.status = "loaded"
    batch.completed_at = datetime.utcnow()
    return {"staged": n, "with_contact_key": joinable,
            "unjoinable": n - joinable}
