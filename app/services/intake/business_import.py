"""Shared core for importing BUSINESSES into a destination other than a tenant
CRM (#470 Stage B): EvoSys Sales and a customer's Acquisition pipeline.

    UPLOAD -> MAP -> NORMALIZE/VALIDATE -> GEOGRAPHY -> DUPLICATES -> REVIEW -> COMMIT -> (ROLLBACK)

Same ledger as every other import (import_batches / import_staged_rows, and
`engine.read_table` for CSV/XLSX). A destination supplies an `Adapter`:
who may write there, how to find an existing record, and how to write one.

HARD RULES (all destinations)
  * The destination is explicit and stored on the batch when it is created;
    every later call re-checks that the actor may still write there. Nothing is
    taken from whichever customer was last open in the browser.
  * Every original column is kept on the staged row (raw_data).
  * Geography comes from `acquisition/geo.py` (contiguous US minus
    California). A row the file marks Pending/Excluded, or whose location
    can't be read, is HELD or EXCLUDED - never committed by default.
  * A public phone number is never called a mobile line and never consent.
    "Unknown" stays unknown.
  * Importing sends nothing, schedules nothing and enrolls no one. Intro
    messages in the file are kept as DRAFTS on the record.
  * Idempotent: an existing record (same source record id, the id quoted in the
    record's source text, same phone, same website or same company name) is
    shown as a match and never created twice or overwritten.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.import_models import (ImportBatch, ImportBatchStatus, ImportDuplicateStatus,
                                      ImportRowReviewStatus, ImportStagedRow, ImportValidationStatus)

MAX_ROWS = 5000
PLATFORM_ORG_ID = "org-god-platform"

FIELDS: List[Tuple[str, str]] = [
    ("business_name", "Company / business name"), ("source_record_id", "Source record ID"),
    ("contact_name", "Contact name"), ("phone", "Phone"), ("email", "Email"),
    ("email_status", "Email status (verified / unverified)"), ("website", "Website"),
    ("address", "Street address"), ("city", "City"), ("state", "State"), ("zip_code", "ZIP"),
    ("country", "Country"), ("industry", "Industry / category"), ("source", "Source (text)"),
    ("source_url", "Source URL"), ("notes", "Notes"), ("tags", "Tags"),
    ("contact_preference", "Contact preference"), ("phone_line_type", "Phone line type"),
    ("location_status", "Location status from the file"), ("intro_draft", "Intro message (kept as a draft)"),
]
FIELD_KEYS = [k for k, _ in FIELDS]
ALIASES: Dict[str, Tuple[str, ...]] = {
    "business_name": ("company", "company name", "business", "business name", "organization", "account name",
                      "practice name", "property name"),
    "source_record_id": ("source record id", "record id", "source id", "external id", "id"),
    "contact_name": ("full name", "contact", "contact name", "owner name", "name", "decision maker"),
    "phone": ("phone", "phone number", "telephone", "business phone", "main phone"),
    "email": ("email", "e mail", "email address", "contact email"),
    "email_status": ("email status", "email verified", "verified"),
    "website": ("website", "url", "site", "domain", "homepage"),
    "address": ("address", "street", "street address", "full address"),
    "city": ("city", "town"),
    "state": ("state", "st", "province", "region"),
    "zip_code": ("zip", "zip code", "zipcode", "postal code"),
    "country": ("country",),
    "industry": ("industry", "category", "business type", "type", "segment", "vertical"),
    "source": ("source", "lead source"),
    "source_url": ("source url", "listing url", "link", "maps url"),
    "notes": ("notes", "note", "research notes", "evidence"),
    "tags": ("tags", "tag", "labels"),
    "contact_preference": ("contact preference", "preferred contact", "preference"),
    "phone_line_type": ("phone line type", "line type", "phone type"),
    "location_status": ("location status", "geo status", "territory status"),
    "intro_draft": ("intro message", "intro", "introduction", "intro draft", "message draft", "draft message"),
}

HELD = "held_for_review"
EXCLUDED = "excluded"
ELIGIBLE = "eligible"

_PREFS = {"text_only": "Text only (stated by the source)", "prefers_text": "Prefers text (stated by the source)",
          "email_only": "Email only (stated by the source)", "call_only": "Call only (stated by the source)",
          "not_stated": None, "": None}


class BusinessImportError(ValueError):
    pass


@dataclass
class Adapter:
    """What one destination plugs in.

    find_existing(db, batch, n) -> (record_id, why) | None   - inside THIS destination only
    write(db, batch, user, n, row) -> record_id         (raises BusinessImportError to refuse a row)
    related(db, batch, n) -> [notes]                    the same business in ANOTHER area, for information
    undo(db, batch, record_id) -> True if removed, False if kept (worked on since import)

    A business can legitimately be an EvoSys Sales prospect and, separately, a
    prospect in a cleaning customer's pipeline - for different reasons. So a
    record elsewhere never blocks a row, is never copied or moved, and is only
    shown to the platform owner (who may see every area); a sales manager or an
    Acquisition operator is never told what another area or customer holds.
    """
    pipeline: str
    destination: str
    label: str
    find_existing: Callable[..., Optional[Tuple[str, str]]]
    write: Callable[..., str]
    related: Callable[..., List[str]] = field(default=lambda db, b, n: [])
    undo: Optional[Callable[..., bool]] = None
    commits_per_row: bool = False   # the writer commits itself (Acquisition's add_prospect does)


def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (h or "").lower()).strip()


def suggest_mapping(headers: List[str]) -> Dict[str, Optional[str]]:
    """Exact alias matches only - a guess the user has to undo is worse than a blank."""
    out: Dict[str, Optional[str]] = {k: None for k in FIELD_KEYS}
    used = set()
    for key in FIELD_KEYS:
        for h in headers:
            if h in used:
                continue
            if _norm(h) == _norm(key) or _norm(h) in ALIASES.get(key, ()):
                out[key] = h
                used.add(h)
                break
    return out


def digits10(phone: Optional[str]) -> str:
    d = re.sub(r"\D", "", phone or "")
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d if len(d) == 10 else ""


def domain_of(website: Optional[str]) -> str:
    w = (website or "").strip().lower()
    if not w:
        return ""
    w = re.sub(r"^[a-z]+://", "", w).split("/")[0].split("?")[0].split("#")[0]
    if w.startswith("www."):
        w = w[4:]
    return w if "." in w else ""


def company_key(name: Optional[str]) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    s = re.sub(r"\b(llc|inc|co|corp|company|ltd|the)\b", " ", s)
    return re.sub(r"\s+", "", s)


def id_in_text(record_id: str, text: Optional[str]) -> bool:
    """FB-CLEAN-004 inside 'Facebook ad | FB-CLEAN-004 | Anytown' - and NOT inside FB-CLEAN-0041."""
    if not record_id or not text:
        return False
    return re.search(r"(?<![A-Za-z0-9-])%s(?![A-Za-z0-9])" % re.escape(record_id), text) is not None


# ── batches ─────────────────────────────────────────────────────────────────
def create_batch(db: Session, user, adapter: Adapter, *, target_id: str, target_name: str,
                 content: bytes, filename: str, source_system: str, batch_org_id: str,
                 display_name: Optional[str] = None, profile: Optional[str] = None) -> ImportBatch:
    from app.services.intake.engine import IntakeError, read_table
    src = re.sub(r"[^a-z0-9_.-]+", "_", (source_system or "").strip().lower()).strip("_")[:60]
    if not src:
        raise BusinessImportError("Say where these records came from (source system)")
    try:
        headers, rows, stats = read_table(content, filename or "upload.csv")
    except IntakeError as exc:
        raise BusinessImportError(str(exc))
    if not headers or not any(h.strip() for h in headers):
        raise BusinessImportError("The file has no header row")
    if not rows:
        raise BusinessImportError("The file has no data rows")
    if len(rows) > MAX_ROWS:
        raise BusinessImportError("This file has %d rows; one import takes up to %d." % (len(rows), MAX_ROWS))
    name = (filename or "").lower()
    dest = {"key": adapter.destination, "label": adapter.label, "target_id": target_id,
            "target_name": target_name, "profile": profile}
    b = ImportBatch(organization_id=batch_org_id, created_by_id=user.id,
                    created_by_name=user.full_name or user.email,
                    display_name=(display_name or filename or adapter.label + " import")[:200],
                    source_type="xlsx" if name.endswith((".xlsx", ".xlsm")) else "csv",
                    source_filename=filename, status=ImportBatchStatus.PROCESSING, pipeline=adapter.pipeline,
                    stage="mapping", source_system=src, source_label=src,
                    source_detail="%s:%s" % (adapter.destination, target_id),
                    original_row_count=len(rows), headers_json=json.dumps(headers),
                    mapping_json=json.dumps(suggest_mapping(headers)),
                    analysis_json=json.dumps({"file": stats, "destination": dest}),
                    acting_user_id=user.id, acting_user_name=user.full_name or user.email,
                    acting_role=user.role, acted_as_platform_owner=(user.role == "god_admin"),
                    total_rows=len(rows))
    b.batch_code = "%s-%s" % (adapter.pipeline.upper().replace("_", "")[:8],
                              datetime.utcnow().strftime("%Y%m%d-%H%M%S"))
    db.add(b)
    db.flush()
    for i, cells in enumerate(rows, start=1):
        raw = {h: (cells[j] if j < len(cells) else "") for j, h in enumerate(headers)}
        if len(cells) > len(headers):
            raw["__extra_cells__"] = cells[len(headers):]
        db.add(ImportStagedRow(batch_id=b.id, organization_id=batch_org_id, row_number=i,
                               raw_data=json.dumps(raw, default=str)))
    db.commit()
    return b


def destination_of(b: ImportBatch) -> Dict[str, Any]:
    return (json.loads(b.analysis_json or "{}") or {}).get("destination") or {}


def target_id(b: ImportBatch) -> Optional[str]:
    return destination_of(b).get("target_id")


def load(db: Session, adapter: Adapter, batch_id: str, target: Optional[str] = None) -> ImportBatch:
    b = db.query(ImportBatch).filter(ImportBatch.id == batch_id, ImportBatch.pipeline == adapter.pipeline).first()
    if b is None or (target is not None and target_id(b) != target):
        raise BusinessImportError("Import not found")
    return b


def save_mapping(db: Session, b: ImportBatch, mapping: Dict[str, Optional[str]]) -> ImportBatch:
    if b.status in (ImportBatchStatus.COMMITTED, ImportBatchStatus.COMMITTING):
        raise BusinessImportError("This import is already committed")
    headers = json.loads(b.headers_json or "[]")
    clean: Dict[str, Optional[str]] = {}
    for k in FIELD_KEYS:
        h = (mapping or {}).get(k)
        if h and h not in headers:
            raise BusinessImportError("Column %r isn't in this file" % h)
        clean[k] = h or None
    if not clean["business_name"]:
        raise BusinessImportError("Map the company / business name column")
    b.mapping_json = json.dumps(clean)
    b.stage = "mapping"
    db.commit()
    return b


def normalize_row(raw: Dict[str, Any], mapping: Dict[str, Optional[str]]) -> Dict[str, Any]:
    from app.services.acquisition import geo as G
    v = {k: (str(raw.get(h) or "").strip() if h else "") for k, h in mapping.items()}
    for k in FIELD_KEYS:
        v.setdefault(k, "")
    state = G.normalize_state(v["state"]) or (v["state"] or None)
    country = v["country"] or None
    if not state and v["address"]:
        st, _z, ctry = G.from_address(v["address"])
        state, country = st or state, country or ctry
    geo_status, geo_note = G.status(state, country)
    file_loc = v["location_status"].lower()
    # The file can only make a row MORE restricted than geography says, never less.
    if geo_status == G.EXCLUDED or file_loc in ("excluded", "exclude", "do not import"):
        territory, why = EXCLUDED, (geo_note if geo_status == G.EXCLUDED else "Marked excluded in the file")
    elif geo_status == G.REVIEW or file_loc in ("pending", "review", "needs review", "unknown"):
        territory, why = HELD, (geo_note if geo_status == G.REVIEW else "Marked location-pending in the file")
    else:
        territory, why = ELIGIBLE, geo_note
    email = (v["email"] or "").lower() or None
    status = v["email_status"].lower()
    pref = v["contact_preference"].lower().replace(" ", "_")
    line = v["phone_line_type"].lower()
    n = {
        "business_name": v["business_name"] or None,
        "source_record_id": v["source_record_id"] or None,
        "contact_name": v["contact_name"] or None,
        "phone_raw": v["phone"] or None, "phone10": digits10(v["phone"]),
        "email": email,
        # Only an explicit "verified" counts; anything else is not verified.
        "email_verified": True if status in ("verified", "valid", "yes", "true") else False if email else None,
        "website": v["website"] or None, "domain": domain_of(v["website"]),
        "address": v["address"] or None, "city": v["city"] or None, "state": state, "zip_code": v["zip_code"] or None,
        "country": country, "industry": v["industry"] or None, "source": v["source"] or None,
        "source_url": v["source_url"] or None, "notes": v["notes"] or None,
        "tags": [t.strip() for t in re.split(r"[,;|]", v["tags"]) if t.strip()],
        "contact_preference": pref or None, "contact_preference_label": _PREFS.get(pref, pref or None),
        # Never upgraded: a number from an ad is not a known mobile line.
        "phone_line_type": line if line in ("landline", "voip") else "unknown",
        "file_location_status": v["location_status"] or None,
        "intro_draft": v["intro_draft"] or None,
        "geo_status": geo_status, "territory": territory, "territory_note": why,
        "sms_consent": False,
    }
    errors, warnings = [], []
    if not n["business_name"]:
        errors.append("No company name")
    if not (n["phone10"] or n["email"] or n["domain"]):
        errors.append("No way to identify or reach the business (phone, email or website)")
    if v["phone"] and not n["phone10"]:
        warnings.append("Phone isn't a 10-digit US number - kept as written")
    if n["email"] and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", n["email"]):
        errors.append("Email isn't a valid address")
    if n["email"] and not n["email_verified"]:
        warnings.append("Email not verified")
    if territory == HELD:
        warnings.append("Held for location review: " + why)
    if not n["source_record_id"]:
        warnings.append("No source record ID - matching relies on phone, website and name")
    n["errors"], n["warnings"] = errors, warnings
    return n


def analyze(db: Session, b: ImportBatch, adapter: Adapter, user=None) -> Dict[str, Any]:
    """Normalize, validate, apply geography and find matches. Writes nothing
    to the destination. Re-runnable; keeps the user's reject decisions."""
    if b.status in (ImportBatchStatus.COMMITTED, ImportBatchStatus.COMMITTING):
        raise BusinessImportError("This import is already committed")
    mapping = json.loads(b.mapping_json or "{}")
    if not mapping.get("business_name"):
        raise BusinessImportError("Map the company / business name column first")
    seen: Dict[str, str] = {}
    counts = {"rows": 0, "ready": 0, "invalid": 0, "matched_existing": 0, "duplicate_in_file": 0,
              "held_for_review": 0, "excluded": 0, "warnings": 0, "with_intro_draft": 0, "also_elsewhere": 0}
    owner_view = getattr(user, "role", None) == "god_admin"
    rows = (db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == b.id)
            .order_by(ImportStagedRow.row_number).all())
    for r in rows:
        counts["rows"] += 1
        n = normalize_row(json.loads(r.raw_data or "{}"), mapping)
        n["related"] = adapter.related(db, b, n) if (owner_view and not n["errors"]) else []
        if n["related"]:
            counts["also_elsewhere"] += 1
        r.street_address, r.city, r.state, r.zip_code = n["address"], n["city"], n["state"], n["zip_code"]
        r.email_raw = r.email_normalized = n["email"]
        r.phone_raw, r.phone_normalized = n["phone_raw"], n["phone10"] or None
        r.duplicate_of_staged_row_id = None
        r.match_reason = None
        keys = [k for k in (("src:" + n["source_record_id"].lower()) if n["source_record_id"] else None,
                            ("tel:" + n["phone10"]) if n["phone10"] else None,
                            ("dom:" + n["domain"]) if n["domain"] else None) if k]
        first = next((seen[k] for k in keys if k in seen), None)
        existing = adapter.find_existing(db, b, n) if not n["errors"] else None
        if n["errors"]:
            r.duplicate_status = ImportDuplicateStatus.NEW
        elif existing:
            r.duplicate_status = ImportDuplicateStatus.MATCHED_EXISTING
            n["existing_id"], r.match_reason = existing
            counts["matched_existing"] += 1
        elif first:
            r.duplicate_status = ImportDuplicateStatus.WITHIN_BATCH_DUPLICATE
            r.duplicate_of_staged_row_id = first
            r.match_reason = "repeats an earlier row in this file"
            counts["duplicate_in_file"] += 1
        else:
            r.duplicate_status = ImportDuplicateStatus.NEW
            for k in keys:
                seen[k] = r.id
        if n["errors"]:
            r.validation_status = ImportValidationStatus.INVALID
            counts["invalid"] += 1
        elif n["warnings"]:
            r.validation_status = ImportValidationStatus.WARNING
            counts["warnings"] += 1
        else:
            r.validation_status = ImportValidationStatus.VALID
        if n["territory"] == HELD:
            counts["held_for_review"] += 1
        elif n["territory"] == EXCLUDED:
            counts["excluded"] += 1
        if n["intro_draft"]:
            counts["with_intro_draft"] += 1
        r.validation_errors = json.dumps(n["errors"] + n["warnings"])
        r.extra_fields = json.dumps({"business": n}, default=str)
        if r.review_status not in (ImportRowReviewStatus.REJECTED, ImportRowReviewStatus.COMMITTED):
            r.review_status = ImportRowReviewStatus.PENDING
    counts["ready"] = sum(1 for r in rows if committable(r))
    b.analysis_json = json.dumps(dict(json.loads(b.analysis_json or "{}"), counts=counts))
    b.status, b.stage = ImportBatchStatus.READY_FOR_REVIEW, "ready_for_review"
    b.new_rows, b.invalid_rows = counts["ready"], counts["invalid"]
    b.matched_rows = counts["matched_existing"] + counts["duplicate_in_file"]
    b.warning_rows = counts["warnings"]
    db.commit()
    return counts


def _biz(r: ImportStagedRow) -> Dict[str, Any]:
    return (json.loads(r.extra_fields or "{}") or {}).get("business") or {}


def committable(r: ImportStagedRow) -> bool:
    n = _biz(r)
    return (bool(n) and r.validation_status != ImportValidationStatus.INVALID
            and r.duplicate_status == ImportDuplicateStatus.NEW
            and n.get("territory") == ELIGIBLE
            and r.review_status not in (ImportRowReviewStatus.REJECTED, ImportRowReviewStatus.COMMITTED))


def review(db: Session, b: ImportBatch, user, row_ids: List[str], decision: str, note: Optional[str]) -> int:
    if decision not in ("reject", "restore"):
        raise BusinessImportError("Decision must be reject or restore")
    if b.status in (ImportBatchStatus.COMMITTED, ImportBatchStatus.COMMITTING):
        raise BusinessImportError("This import is already committed")
    rows = (db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == b.id,
                                             ImportStagedRow.id.in_(list(row_ids or []))).all())
    for r in rows:
        if r.review_status == ImportRowReviewStatus.COMMITTED:
            continue
        r.review_status = ImportRowReviewStatus.REJECTED if decision == "reject" else ImportRowReviewStatus.PENDING
        r.review_note = (note or "").strip()[:500] or None
        r.reviewed_by_id, r.reviewed_at = user.id, datetime.utcnow()
    b.rejected_rows = (db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == b.id,
                                                        ImportStagedRow.review_status == ImportRowReviewStatus.REJECTED)
                       .count())
    db.commit()
    return len(rows)


def outcome(r: ImportStagedRow) -> str:
    n = _biz(r)
    if r.review_status == ImportRowReviewStatus.COMMITTED:
        return "created"
    if r.review_status == ImportRowReviewStatus.REJECTED:
        return "rejected"
    if r.validation_status == ImportValidationStatus.INVALID:
        return "invalid"
    if n.get("territory") == EXCLUDED:
        return "excluded"
    if r.duplicate_status == ImportDuplicateStatus.MATCHED_EXISTING:
        return "matched_existing"
    if r.duplicate_status == ImportDuplicateStatus.WITHIN_BATCH_DUPLICATE:
        return "duplicate_in_file"
    if n.get("territory") == HELD:
        return "held_for_review"
    if n.get("write_error"):
        return "failed"
    return "ready" if committable(r) else "pending"


NEXT_STEP = {
    "ready": "Will be created when you press Create.",
    "created": "Created. Nothing was sent.",
    "matched_existing": "Already in this destination - nothing created. Open the existing record.",
    "duplicate_in_file": "Repeats an earlier row in this file - not created.",
    "held_for_review": "Confirm where this business is (a state in the contiguous US, not California), fix it in your file and upload again.",
    "excluded": "Outside the supported territory (California, Alaska, Hawaii, territories or another country). Not imported.",
    "invalid": "Fix the problem shown and upload again.",
    "rejected": "You rejected this row. Restore it to include it.",
    "failed": "Didn't save. Press Create again - only the missing rows are retried.",
    "pending": "Check rows to see what will happen.",
}


def next_actions(b: ImportBatch) -> List[Dict[str, Any]]:
    """What the person should do now, in plain words, from the counts."""
    c = (json.loads(b.analysis_json or "{}") or {}).get("counts") or {}
    rep = json.loads(b.commit_report_json or "null") or {}
    dest = destination_of(b)
    where = dest.get("target_name") or dest.get("label") or "this destination"
    out: List[Dict[str, Any]] = []
    if not c:
        return [{"key": "analyze", "text": "Match the columns, then press Check rows. Nothing is added before you press Create."}]
    committed = b.status == ImportBatchStatus.COMMITTED
    if b.stage == "partially_committed":
        out.append({"key": "retry", "text": "%d rows didn't save. Press Create again to retry only those." % rep.get("failed", 0),
                    "download": "failed"})
    if not committed and c.get("ready"):
        out.append({"key": "create", "text": "%d ready to create in %s. Nothing is sent and nobody is put in a campaign." % (c["ready"], where)})
    if committed:
        out.append({"key": "done", "text": "%d created in %s. Messages sent: 0." % (rep.get("created", 0) + rep.get("already_created", 0), where)})
    if c.get("held_for_review"):
        out.append({"key": "held", "text": "%d held because their location isn't confirmed. Download the list, confirm each state, and upload those rows again." % c["held_for_review"],
                    "download": "held_for_review"})
    if c.get("invalid"):
        out.append({"key": "invalid", "text": "%d can't be imported as they are. Download the list to see why." % c["invalid"], "download": "invalid"})
    if c.get("excluded"):
        out.append({"key": "excluded", "text": "%d are outside the supported territory and won't be imported. The list is kept for reference." % c["excluded"],
                    "download": "excluded"})
    if c.get("matched_existing"):
        out.append({"key": "matched", "text": "%d are already in %s - nothing will be created for them." % (c["matched_existing"], where),
                    "download": "matched_existing"})
    return out


def row_payload(r: ImportStagedRow) -> Dict[str, Any]:
    n = _biz(r)
    return {"id": r.id, "row": r.row_number, "business": n, "validation": r.validation_status,
            "duplicate": r.duplicate_status, "match_reason": r.match_reason, "review": r.review_status,
            "review_note": r.review_note, "committable": committable(r), "outcome": outcome(r),
            "record_id": n.get("record_id"), "existing_id": n.get("existing_id"),
            "next_step": NEXT_STEP.get(outcome(r)), "related": n.get("related") or []}


def commit(db: Session, b: ImportBatch, user, adapter: Adapter) -> Dict[str, Any]:
    """Create the ready rows in the destination. Each row is checked against
    the destination again at this moment. A failed row is reported and the
    rest continue. Safe to call again: committed rows are skipped, so a retry
    after an interruption finishes the job without duplicates."""
    if b.status == ImportBatchStatus.COMMITTED:
        return json.loads(b.commit_report_json or "{}")
    if b.status not in (ImportBatchStatus.READY_FOR_REVIEW, ImportBatchStatus.COMMITTING):
        raise BusinessImportError("Analyze the import before committing")
    b.status, b.stage = ImportBatchStatus.COMMITTING, "committing"
    db.commit()
    dest = destination_of(b)
    report = {"destination": dest.get("key"), "target_id": dest.get("target_id"),
              "target_name": dest.get("target_name"), "created": 0, "matched_existing": 0,
              "duplicate_in_file": 0, "held_for_review": 0, "excluded": 0, "invalid": 0, "rejected": 0,
              "failed": 0, "already_created": 0, "record_ids": [], "messages_sent": 0, "enrolled": 0}
    rows = (db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == b.id)
            .order_by(ImportStagedRow.row_number).all())
    for r in rows:
        if r.review_status == ImportRowReviewStatus.COMMITTED:
            report["already_created"] += 1
            report["record_ids"].append(_biz(r).get("record_id"))
            continue
        if not committable(r):
            o = outcome(r)
            key = o if o in report else "failed"
            report[key] += 1
            continue
        n = _biz(r)
        again = adapter.find_existing(db, b, n)
        if again:
            r.duplicate_status = ImportDuplicateStatus.MATCHED_EXISTING
            n["existing_id"], r.match_reason = again
            r.extra_fields = json.dumps({"business": n}, default=str)
            report["matched_existing"] += 1
            db.commit()
            continue
        try:
            if adapter.commits_per_row:
                rid = adapter.write(db, b, user, n, r)
            else:
                with db.begin_nested():
                    rid = adapter.write(db, b, user, n, r)
        except Exception as exc:  # noqa: BLE001 - one bad row never stops the rest
            db.rollback()
            b = db.query(ImportBatch).filter(ImportBatch.id == b.id).first()
            r = db.query(ImportStagedRow).filter(ImportStagedRow.id == r.id).first()
            n = _biz(r)
            n["write_error"] = str(getattr(exc, "detail", None) or exc)[:300]
            r.extra_fields = json.dumps({"business": n}, default=str)
            report["failed"] += 1
            db.commit()
            continue
        n.pop("write_error", None)
        n["record_id"] = rid
        r.extra_fields = json.dumps({"business": n}, default=str)
        r.review_status = ImportRowReviewStatus.COMMITTED
        r.committed_by_id, r.committed_at = user.id, datetime.utcnow()
        report["created"] += 1
        report["record_ids"].append(rid)
        db.commit()
    now = datetime.utcnow()
    if report["failed"]:
        # Some rows didn't go in: leave the import open so a retry creates
        # only those (rows already created are skipped).
        b.status, b.stage = ImportBatchStatus.READY_FOR_REVIEW, "partially_committed"
    else:
        b.status, b.stage = ImportBatchStatus.COMMITTED, "committed"
    b.committed_rows = report["created"] + report["already_created"]
    b.committed_at, b.committed_by_id = now, user.id
    b.committed_by_name = user.full_name or user.email
    b.completed_at = now
    b.commit_report_json = json.dumps(report)
    db.commit()
    return report


def rollback(db: Session, b: ImportBatch, user, adapter: Adapter) -> Dict[str, Any]:
    """Remove what this import created - except records worked on since, which
    are kept and listed. Matched existing records were never touched, so they
    are never removed."""
    if adapter.undo is None:
        raise BusinessImportError("This destination can't be rolled back")
    if b.status != ImportBatchStatus.COMMITTED and b.stage != "partially_committed":
        raise BusinessImportError("Only a committed import can be rolled back")
    removed, kept = 0, []
    for r in db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == b.id,
                                              ImportStagedRow.review_status == ImportRowReviewStatus.COMMITTED).all():
        n = _biz(r)
        rid = n.get("record_id")
        if rid and adapter.undo(db, b, rid):
            removed += 1
            n.pop("record_id", None)
            r.extra_fields = json.dumps({"business": n}, default=str)
            r.review_status = ImportRowReviewStatus.PENDING
        elif rid:
            kept.append(rid)
    report = {"removed": removed, "kept_worked_on": len(kept), "kept_ids": kept}
    b.rolled_back_at, b.rolled_back_by_id = datetime.utcnow(), user.id
    b.rollback_report_json = json.dumps(report)
    b.status, b.stage = ImportBatchStatus.READY_FOR_REVIEW, "rolled_back"
    db.commit()
    return report


def batch_payload(b: ImportBatch) -> Dict[str, Any]:
    return {"id": b.id, "code": b.batch_code, "name": b.display_name, "filename": b.source_filename,
            "source_system": b.source_system, "destination": destination_of(b), "status": b.status,
            "stage": b.stage, "rows": b.total_rows, "headers": json.loads(b.headers_json or "[]"),
            "mapping": json.loads(b.mapping_json or "{}"), "fields": [{"key": k, "label": l} for k, l in FIELDS],
            "counts": (json.loads(b.analysis_json or "{}") or {}).get("counts"),
            "next_actions": next_actions(b),
            "commit_report": json.loads(b.commit_report_json or "null"),
            "rollback_report": json.loads(b.rollback_report_json or "null"), "created_by": b.created_by_name,
            "created_at": b.created_at.isoformat() if b.created_at else None,
            "committed_at": b.committed_at.isoformat() if b.committed_at else None}


REPORT_COLUMNS = ["row", "outcome", "next_step", "company", "source_record_id", "state", "country",
                  "file_location_status", "territory_note", "match_reason", "existing_id", "record_id", "problems",
                  "also_elsewhere"]


def report_csv(db: Session, b: ImportBatch, outcome_filter: Optional[str] = None) -> str:
    """Spreadsheet-safe per-row results; `outcome_filter` gives just the held,
    excluded, invalid, failed (...) rows. Company and source id identify each
    row; no phone numbers or emails are written."""
    import csv
    import io
    from app.utils.csv_safe import row as safe
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(REPORT_COLUMNS)
    for r in (db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == b.id)
              .order_by(ImportStagedRow.row_number).all()):
        o = outcome(r)
        if outcome_filter and o != outcome_filter:
            continue
        n = _biz(r)
        probs = json.loads(r.validation_errors or "[]") + ([n["write_error"]] if n.get("write_error") else [])
        w.writerow(safe([r.row_number, o, NEXT_STEP.get(o), n.get("business_name"), n.get("source_record_id"),
                         n.get("state"), n.get("country"), n.get("file_location_status"), n.get("territory_note"),
                         r.match_reason, n.get("existing_id"), n.get("record_id"), "; ".join(probs),
                         "; ".join(n.get("related") or [])]))
    return buf.getvalue()
