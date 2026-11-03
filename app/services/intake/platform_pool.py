"""UNIVERSAL INTAKE DESTINATION: platform_acquisition_pool (#470 Stage B).

Commercial businesses that might BUY cleaning (offices, medical/dental,
apartments, property managers, short-term rental managers) go into EvoSys's
own master service-prospect pool, `lead_intel_prospects`. From there the owner
assigns them, one client at a time, to cleaning-company tenants (#452).

    UPLOAD -> MAP -> NORMALIZE/VALIDATE -> DUPLICATES -> REVIEW -> COMMIT -> QUALIFY

It is the SAME intake ledger every import uses (import_batches /
import_staged_rows, `read_table` for CSV/XLSX). What it adds is the destination
adapter, because these rows are businesses rather than people and land in the
platform pool rather than a tenant CRM.

HARD RULES
  * God only. The batch belongs to the platform organization, never to a
    customer tenant; nothing is routed to Divine Shine (or anyone) by importing.
  * Nothing is sent, assigned, enrolled or marked outreach-ready. Committed
    rows are qualified by the existing `lead_routing` qualification and wait in
    the pool for a deliberate assignment.
  * Every original column is kept on the staged row and the prospect (raw_json).
  * Idempotent: the same source record (source system + record id), website
    domain or business phone is never added twice - a re-upload commits zero.
  * California, Alaska, Hawaii, territories and foreign rows are refused at
    review (geo.py); an unreadable location is kept for review, never guessed.
  * Rollback removes only this batch's prospects that have NOT been assigned.

EvoSys's own cleaning-company RECRUITMENT leads are a different population and
belong in EvoSys Sales (`evosys_sales`), never here.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.import_models import (ImportBatch, ImportBatchStatus, ImportDuplicateStatus,
                                      ImportRowReviewStatus, ImportStagedRow, ImportValidationStatus)
from app.models.lead_intel_models import LeadIntelProspect

PIPELINE = "platform_pool"
DESTINATION = "platform_acquisition_pool"
PLATFORM_ORG_ID = "org-god-platform"
MAX_ROWS = 5000

# Canonical business fields, in the order the mapping screen shows them.
FIELDS: List[Tuple[str, str]] = [
    ("business_name", "Business name"), ("source_record_id", "Source record ID"),
    ("category", "Business type / category"), ("website", "Website"), ("email", "Email"),
    ("email_verified", "Email verified (yes/no)"), ("phone", "Business phone"),
    ("contact_name", "Contact name"), ("contact_title", "Contact title"),
    ("address", "Street address"), ("city", "City"), ("state", "State"), ("zip_code", "ZIP"),
    ("country", "Country"), ("source_url", "Source URL"), ("researched_at", "Researched on"),
    ("evidence", "Qualification evidence / notes"), ("recurring_potential", "Recurring-cleaning potential"),
    ("confidence", "Confidence"),
]
FIELD_KEYS = [k for k, _ in FIELDS]
ALIASES: Dict[str, Tuple[str, ...]] = {
    "business_name": ("business name", "business", "company", "company name", "name", "organization",
                      "property name", "practice name", "account name"),
    "source_record_id": ("id", "record id", "source id", "place id", "place_id", "external id", "source record id"),
    "category": ("category", "type", "business type", "industry", "segment", "vertical"),
    "website": ("website", "url", "site", "web", "domain", "homepage"),
    "email": ("email", "e-mail", "email address", "contact email"),
    "email_verified": ("email verified", "verified", "email status"),
    "phone": ("phone", "phone number", "telephone", "business phone", "main phone"),
    "contact_name": ("contact", "contact name", "decision maker", "owner name", "manager"),
    "contact_title": ("title", "contact title", "job title", "role"),
    "address": ("address", "street", "street address", "address 1", "full address"),
    "city": ("city", "town"),
    "state": ("state", "st", "province", "region"),
    "zip_code": ("zip", "zip code", "zipcode", "postal code", "postcode"),
    "country": ("country",),
    "source_url": ("source url", "source", "listing url", "google maps url", "link", "maps url"),
    "researched_at": ("researched", "researched at", "researched on", "date", "found on", "date found"),
    "evidence": ("evidence", "notes", "qualification", "why", "reason", "research notes"),
    "recurring_potential": ("recurring", "recurring potential", "frequency"),
    "confidence": ("confidence", "score"),
}
SEGMENT_HINTS = (
    ("medical_dental", ("dental", "dentist", "medical", "clinic", "orthodont", "pediatric", "physician",
                        "chiropract", "veterinar", "urgent care", "dermatolog", "optometr")),
    ("apartments", ("apartment", "multifamily", "multi-family", "residences", "lofts")),
    ("property_management", ("property management", "property manager", "realty management", "hoa")),
    ("short_term_rental", ("short-term rental", "short term rental", "vacation rental", "airbnb")),
    ("office", ("office", "law firm", "accounting", "cpa", "insurance", "financial", "coworking",
                "professional", "agency", "consult")),
)


class PoolImportError(ValueError):
    pass


def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (h or "").lower()).strip()


def suggest_mapping(headers: List[str]) -> Dict[str, Optional[str]]:
    """{canonical_field: header or None}. Exact alias matches only - a guess
    the owner has to undo is worse than a blank they fill in."""
    out: Dict[str, Optional[str]] = {k: None for k in FIELD_KEYS}
    used = set()
    for key in FIELD_KEYS:
        for h in headers:
            if h in used:
                continue
            if _norm(h) in ALIASES.get(key, ()) or _norm(h) == _norm(key):
                out[key] = h
                used.add(h)
                break
    return out


def segment_for(category: Optional[str], name: Optional[str] = None) -> Optional[str]:
    """Which acquisition segment a category points at, from words in it. None
    when nothing fits - never a default."""
    text = " ".join(x for x in (category, name) if x).lower()
    for seg, words in SEGMENT_HINTS:
        if any(w in text for w in words):
            return seg
    return None


def domain_of(website: Optional[str]) -> str:
    w = (website or "").strip().lower()
    if not w:
        return ""
    w = re.sub(r"^[a-z]+://", "", w)
    w = w.split("/")[0].split("?")[0].split("#")[0]
    if w.startswith("www."):
        w = w[4:]
    return w if "." in w else ""


_TRUE = {"yes", "y", "true", "1", "verified", "valid", "deliverable"}
_FALSE = {"no", "n", "false", "0", "unverified", "invalid", "undeliverable", "unknown", "risky"}


def _bool(v: Optional[str]) -> Optional[bool]:
    s = (v or "").strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return None


def _date(v: Optional[str]) -> Optional[str]:
    s = (v or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:19], fmt).isoformat()
        except ValueError:
            continue
    return None


# ── batches ─────────────────────────────────────────────────────────────────
def create_batch(db: Session, user, *, content: bytes, filename: str, source_system: str,
                 display_name: Optional[str] = None) -> ImportBatch:
    from app.services.intake.engine import IntakeError, read_table
    src = re.sub(r"[^a-z0-9_.-]+", "_", (source_system or "").strip().lower())[:60]
    if not src:
        raise PoolImportError("Say where these records came from (source system)")
    try:
        headers, rows, stats = read_table(content, filename or "upload.csv")
    except IntakeError as exc:
        raise PoolImportError(str(exc))
    if not headers or not any(h.strip() for h in headers):
        raise PoolImportError("The file has no header row")
    if not rows:
        raise PoolImportError("The file has no data rows")
    if len(rows) > MAX_ROWS:
        raise PoolImportError("This file has %d rows; the master pool takes up to %d per import." % (len(rows), MAX_ROWS))
    name = (filename or "").lower()
    b = ImportBatch(organization_id=PLATFORM_ORG_ID, created_by_id=user.id,
                    created_by_name=user.full_name or user.email,
                    display_name=(display_name or filename or "Master pool import")[:200],
                    source_type="xlsx" if name.endswith((".xlsx", ".xlsm")) else "csv",
                    source_filename=filename, status=ImportBatchStatus.PROCESSING, pipeline=PIPELINE,
                    stage="mapping", source_system=src, source_label=src, source_detail=DESTINATION,
                    original_row_count=len(rows), headers_json=json.dumps(headers),
                    mapping_json=json.dumps(suggest_mapping(headers)),
                    analysis_json=json.dumps({"file": stats}), acting_user_id=user.id,
                    acting_user_name=user.full_name or user.email, acting_role=user.role,
                    acted_as_platform_owner=True, total_rows=len(rows))
    b.batch_code = "POOL-%s" % datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    db.add(b)
    db.flush()
    for i, cells in enumerate(rows, start=1):
        raw = {h: (cells[j] if j < len(cells) else "") for j, h in enumerate(headers)}
        if len(cells) > len(headers):
            raw["__extra_cells__"] = cells[len(headers):]
        db.add(ImportStagedRow(batch_id=b.id, organization_id=PLATFORM_ORG_ID, row_number=i,
                               raw_data=json.dumps(raw, default=str)))
    db.commit()
    return b


def load(db: Session, batch_id: str) -> ImportBatch:
    b = (db.query(ImportBatch).filter(ImportBatch.id == batch_id, ImportBatch.pipeline == PIPELINE,
                                      ImportBatch.organization_id == PLATFORM_ORG_ID).first())
    if b is None:
        raise PoolImportError("Import not found")
    return b


def save_mapping(db: Session, b: ImportBatch, mapping: Dict[str, Optional[str]]) -> ImportBatch:
    if b.status in (ImportBatchStatus.COMMITTED, ImportBatchStatus.COMMITTING):
        raise PoolImportError("This import is already committed")
    headers = json.loads(b.headers_json or "[]")
    clean: Dict[str, Optional[str]] = {}
    for k in FIELD_KEYS:
        h = (mapping or {}).get(k)
        if h and h not in headers:
            raise PoolImportError("Column %r isn't in this file" % h)
        clean[k] = h or None
    if not clean["business_name"]:
        raise PoolImportError("Map the business name column")
    b.mapping_json = json.dumps(clean)
    b.stage = "mapping"
    db.commit()
    return b


def _existing(db: Session, n: Dict[str, Any], source_system: str) -> Optional[Tuple[str, str]]:
    """(prospect id, why) when the pool already holds this business."""
    q = db.query(LeadIntelProspect.id).filter(LeadIntelProspect.stage != "duplicate")
    if n.get("source_record_id"):
        hit = q.filter(LeadIntelProspect.source == source_system,
                       LeadIntelProspect.source_record_id == n["source_record_id"]).first()
        if hit:
            return hit[0], "same source record"
    if n.get("domain"):
        for pid, site in (db.query(LeadIntelProspect.id, LeadIntelProspect.website)
                          .filter(LeadIntelProspect.stage != "duplicate",
                                  LeadIntelProspect.website.ilike("%" + n["domain"] + "%")).limit(50).all()):
            if domain_of(site) == n["domain"]:
                return pid, "same website"
    if n.get("phone_e164"):
        hit = q.filter(LeadIntelProspect.phone_e164 == n["phone_e164"]).first()
        if hit:
            return hit[0], "same business phone"
    return None


def normalize_row(raw: Dict[str, Any], mapping: Dict[str, Optional[str]]) -> Dict[str, Any]:
    from app.services.acquisition import geo as G
    from app.services.lead_routing import split_address, to_e164
    v = {k: (str(raw.get(h) or "").strip() if h else "") for k, h in mapping.items()}
    address = v.get("address") or ""
    a_city, a_state, a_zip = split_address(address) if address else (None, None, None)
    state = G.normalize_state(v.get("state")) or (v.get("state") or None) or a_state
    country = v.get("country") or None
    if not state and address:
        st, z, ctry = G.from_address(address)
        state, country = st or state, country or ctry
    geo_status, geo_note = G.status(state, country)
    email = (v.get("email") or "").lower() or None
    website = v.get("website") or None
    n = {
        "business_name": v.get("business_name") or None,
        "source_record_id": v.get("source_record_id") or None,
        "category": v.get("category") or None,
        "segment": segment_for(v.get("category"), v.get("business_name")),
        "website": website, "domain": domain_of(website),
        "email": email, "email_verified": _bool(v.get("email_verified")) if email else None,
        "phone_raw": v.get("phone") or None, "phone_e164": to_e164(v.get("phone")),
        "contact_name": v.get("contact_name") or None, "contact_title": v.get("contact_title") or None,
        "address": address or None, "city": v.get("city") or a_city or None, "state": state,
        "zip_code": v.get("zip_code") or a_zip or None, "country": country,
        "source_url": v.get("source_url") or None, "researched_at": _date(v.get("researched_at")),
        "evidence": v.get("evidence") or None, "recurring_potential": v.get("recurring_potential") or None,
        "confidence": v.get("confidence") or None,
        "geo_status": geo_status, "geo_note": geo_note,
    }
    errors, warnings = [], []
    if not n["business_name"]:
        errors.append("No business name")
    if not (n["phone_e164"] or n["email"] or n["domain"]):
        errors.append("No way to identify or reach the business (phone, email or website)")
    if n["email"] and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", n["email"]):
        errors.append("Email isn't a valid address")
    if geo_status == G.EXCLUDED:
        errors.append(geo_note)
    elif geo_status == G.REVIEW:
        warnings.append(geo_note)
    if n["source_url"] and not n["source_url"].lower().startswith(("http://", "https://")):
        warnings.append("Source URL isn't a web address")
    if not n["source_url"]:
        warnings.append("No source URL - provenance not recorded")
    if n["email"] and n["email_verified"] is not True:
        warnings.append("Email not verified")
    if not n["segment"]:
        warnings.append("Business type not recognized - choose a segment at assignment")
    if v.get("researched_at") and not n["researched_at"]:
        warnings.append("Researched date not understood")
    n["errors"], n["warnings"] = errors, warnings
    return n


def analyze(db: Session, b: ImportBatch) -> Dict[str, Any]:
    """Normalize, validate and duplicate-check every row. Writes nothing to
    the pool. Re-runnable; a re-run keeps the owner's reject decisions."""
    if b.status in (ImportBatchStatus.COMMITTED, ImportBatchStatus.COMMITTING):
        raise PoolImportError("This import is already committed")
    mapping = json.loads(b.mapping_json or "{}")
    if not mapping.get("business_name"):
        raise PoolImportError("Map the business name column first")
    seen: Dict[str, str] = {}
    counts = {"rows": 0, "valid": 0, "warning": 0, "invalid": 0, "duplicate_in_pool": 0,
              "duplicate_in_file": 0, "new": 0, "geo_review": 0, "geo_excluded": 0}
    rows = (db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == b.id)
            .order_by(ImportStagedRow.row_number).all())
    for r in rows:
        counts["rows"] += 1
        n = normalize_row(json.loads(r.raw_data or "{}"), mapping)
        r.street_address, r.city, r.state, r.zip_code = n["address"], n["city"], n["state"], n["zip_code"]
        r.email_raw = r.email_normalized = n["email"]
        r.phone_raw, r.phone_normalized = n["phone_raw"], n["phone_e164"]
        r.source_category = n["segment"]
        dup_reason = None
        r.duplicate_of_staged_row_id = None
        keys = [k for k in (("src:" + n["source_record_id"]) if n["source_record_id"] else None,
                            ("dom:" + n["domain"]) if n["domain"] else None,
                            ("tel:" + n["phone_e164"]) if n["phone_e164"] else None) if k]
        first = next((seen[k] for k in keys if k in seen), None)
        existing = _existing(db, n, b.source_system) if not n["errors"] else None
        if n["errors"]:
            r.duplicate_status = ImportDuplicateStatus.NEW
        elif existing:
            r.duplicate_status = ImportDuplicateStatus.MATCHED_EXISTING
            n["duplicate_of_prospect_id"], dup_reason = existing
            counts["duplicate_in_pool"] += 1
        elif first:
            r.duplicate_status = ImportDuplicateStatus.WITHIN_BATCH_DUPLICATE
            r.duplicate_of_staged_row_id = first
            dup_reason = "repeats an earlier row in this file"
            counts["duplicate_in_file"] += 1
        else:
            r.duplicate_status = ImportDuplicateStatus.NEW
            for k in keys:
                seen[k] = r.id
        r.match_reason = dup_reason
        if n["errors"]:
            r.validation_status = ImportValidationStatus.INVALID
            counts["invalid"] += 1
        elif n["warnings"]:
            r.validation_status = ImportValidationStatus.WARNING
            counts["warning"] += 1
        else:
            r.validation_status = ImportValidationStatus.VALID
            counts["valid"] += 1
        if n["geo_status"] == "review":
            counts["geo_review"] += 1
        if n["geo_status"] == "excluded":
            counts["geo_excluded"] += 1
        r.validation_errors = json.dumps(n["errors"] + n["warnings"])
        r.extra_fields = json.dumps({"business": n}, default=str)
        if r.review_status != ImportRowReviewStatus.REJECTED:
            r.review_status = ImportRowReviewStatus.PENDING
    counts["new"] = sum(1 for r in rows if committable(r))
    b.analysis_json = json.dumps(dict(json.loads(b.analysis_json or "{}"), counts=counts))
    b.status, b.stage = ImportBatchStatus.READY_FOR_REVIEW, "ready_for_review"
    b.new_rows, b.invalid_rows = counts["new"], counts["invalid"]
    b.matched_rows = counts["duplicate_in_pool"] + counts["duplicate_in_file"]
    b.warning_rows = counts["warning"]
    db.commit()
    return counts


def committable(r: ImportStagedRow) -> bool:
    return (r.validation_status != ImportValidationStatus.INVALID
            and r.duplicate_status == ImportDuplicateStatus.NEW
            and r.review_status not in (ImportRowReviewStatus.REJECTED, ImportRowReviewStatus.COMMITTED)
            and bool(r.extra_fields))


def review(db: Session, b: ImportBatch, user, row_ids: List[str], decision: str, note: Optional[str]) -> int:
    if decision not in ("reject", "restore"):
        raise PoolImportError("Decision must be reject or restore")
    if b.status in (ImportBatchStatus.COMMITTED, ImportBatchStatus.COMMITTING):
        raise PoolImportError("This import is already committed")
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


def row_payload(r: ImportStagedRow) -> Dict[str, Any]:
    biz = (json.loads(r.extra_fields or "{}") or {}).get("business") or {}
    return {"id": r.id, "row": r.row_number, "business": biz, "raw": json.loads(r.raw_data or "{}"),
            "validation": r.validation_status, "duplicate": r.duplicate_status, "duplicate_reason": r.match_reason,
            "review": r.review_status, "review_note": r.review_note, "committable": committable(r),
            "pool_prospect_id": biz.get("pool_prospect_id")}


def commit(db: Session, b: ImportBatch, user) -> Dict[str, Any]:
    """Write the reviewed rows into the master pool and qualify them. Each row
    is checked against the pool again at this moment (another import may have
    added the business since analysis). No assignment, no message, no consent."""
    from app.services import lead_routing as LR
    if b.status == ImportBatchStatus.COMMITTED:
        return json.loads(b.commit_report_json or "{}")
    if b.status != ImportBatchStatus.READY_FOR_REVIEW:
        raise PoolImportError("Analyze the import before committing")
    b.status, b.stage = ImportBatchStatus.COMMITTING, "committing"
    db.flush()
    report = {"destination": DESTINATION, "target_org_id": None, "committed": 0, "skipped_duplicate": 0,
              "skipped_invalid": 0, "skipped_rejected": 0, "prospect_ids": [], "assigned": 0, "sent": 0}
    rows = (db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == b.id)
            .order_by(ImportStagedRow.row_number).all())
    now = datetime.utcnow()
    for r in rows:
        if r.review_status == ImportRowReviewStatus.REJECTED:
            report["skipped_rejected"] += 1
            continue
        if r.validation_status == ImportValidationStatus.INVALID:
            report["skipped_invalid"] += 1
            continue
        if not committable(r):
            report["skipped_duplicate"] += 1
            continue
        extra = json.loads(r.extra_fields or "{}")
        n = extra.get("business") or {}
        again = _existing(db, n, b.source_system)
        if again:
            r.duplicate_status = ImportDuplicateStatus.MATCHED_EXISTING
            r.match_reason = again[1]
            n["duplicate_of_prospect_id"] = again[0]
            r.extra_fields = json.dumps({"business": n}, default=str)
            report["skipped_duplicate"] += 1
            continue
        qn = {"name": n.get("business_name"), "phone_e164": n.get("phone_e164"), "email": n.get("email"),
              "zip_code": n.get("zip_code")}
        decision = LR.qualify_normalized(qn, r.id)
        evidence = {k: n.get(k) for k in ("evidence", "recurring_potential", "confidence", "category", "segment",
                                          "geo_status", "geo_note", "warnings")}
        p = LeadIntelProspect(
            source=b.source_system, external_id=None, industry=n.get("segment") or n.get("category"),
            name=n.get("business_name"), phone_raw=n.get("phone_raw"), phone_e164=n.get("phone_e164"),
            email=n.get("email"), website=n.get("website"), address=n.get("address"), city=n.get("city"),
            state=n.get("state"), zip_code=n.get("zip_code"), stage="qualified", bucket=decision["bucket"],
            priority=decision["priority"], score=decision["score"], best_channel=decision["best_channel"],
            decision_json=json.dumps(decision["channels"], default=str), import_batch_id=b.id,
            source_record_id=n.get("source_record_id"), source_url=n.get("source_url"),
            researched_at=datetime.fromisoformat(n["researched_at"]) if n.get("researched_at") else None,
            contact_name=n.get("contact_name"), contact_title=n.get("contact_title"),
            email_verified=n.get("email_verified"), evidence_json=json.dumps(evidence, default=str),
            raw_json=r.raw_data)
        db.add(p)
        db.flush()
        n["pool_prospect_id"] = p.id
        r.extra_fields = json.dumps({"business": n}, default=str)
        r.review_status = ImportRowReviewStatus.COMMITTED
        r.committed_by_id, r.committed_at = user.id, now
        report["committed"] += 1
        report["prospect_ids"].append(p.id)
    b.status, b.stage = ImportBatchStatus.COMMITTED, "committed"
    b.committed_rows = report["committed"]
    b.committed_at, b.committed_by_id = now, user.id
    b.committed_by_name = user.full_name or user.email
    b.completed_at = now
    b.commit_report_json = json.dumps(report)
    db.commit()
    return report


def rollback(db: Session, b: ImportBatch, user) -> Dict[str, Any]:
    """Remove this import's prospects from the pool - except any already
    assigned to a client, which stay (and are reported) so no tenant loses a
    record it is working."""
    if b.status != ImportBatchStatus.COMMITTED:
        raise PoolImportError("Only a committed import can be rolled back")
    rows = db.query(LeadIntelProspect).filter(LeadIntelProspect.import_batch_id == b.id).all()
    kept = [p.id for p in rows if p.routed_org_id]
    removed = 0
    for p in rows:
        if p.routed_org_id:
            continue
        db.delete(p)
        removed += 1
    for r in db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == b.id,
                                              ImportStagedRow.review_status == ImportRowReviewStatus.COMMITTED).all():
        biz = (json.loads(r.extra_fields or "{}") or {}).get("business") or {}
        if biz.get("pool_prospect_id") not in kept:
            r.review_status = ImportRowReviewStatus.PENDING
    report = {"removed": removed, "kept_assigned": len(kept), "kept_ids": kept}
    b.rolled_back_at, b.rolled_back_by_id = datetime.utcnow(), user.id
    b.rollback_report_json = json.dumps(report)
    b.status, b.stage = ImportBatchStatus.READY_FOR_REVIEW, "rolled_back"
    db.commit()
    return report


def batch_payload(db: Session, b: ImportBatch) -> Dict[str, Any]:
    return {"id": b.id, "code": b.batch_code, "name": b.display_name, "filename": b.source_filename,
            "source_system": b.source_system, "destination": DESTINATION, "status": b.status, "stage": b.stage,
            "rows": b.total_rows, "headers": json.loads(b.headers_json or "[]"),
            "mapping": json.loads(b.mapping_json or "{}"), "fields": [{"key": k, "label": l} for k, l in FIELDS],
            "analysis": json.loads(b.analysis_json or "{}"), "commit_report": json.loads(b.commit_report_json or "null"),
            "rollback_report": json.loads(b.rollback_report_json or "null"), "created_by": b.created_by_name,
            "created_at": b.created_at.isoformat() if b.created_at else None,
            "committed_at": b.committed_at.isoformat() if b.committed_at else None}
