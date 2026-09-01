"""
import_staging_service.py — Parse uploaded file → ImportStagedRow records.
NO live Lead is created here. DNC blocks are authoritative and not reversible.
"""
from __future__ import annotations
import json, logging, re, unicodedata
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.models.import_models import (
    ImportBatch, ImportBatchStatus, ImportDuplicateStatus,
    ImportMatchConfidence, ImportRowReviewStatus, ImportStagedRow,
    ImportValidationStatus,
)
from app.models.models import Lead
from app.services.import_service import (
    HEADER_MAP, TIER_TO_TRACK, _infer_tier, _check_email_quality,
    split_full_name, _build_column_lookup,
)
log = logging.getLogger(__name__)

_DIGIT_RE = re.compile(r"\D")
_DNC_STATUSES = {"dnc", "do_not_contact", "deceased"}

def _norm_phone(raw: Optional[str]) -> Optional[str]:
    if not raw: return None
    d = _DIGIT_RE.sub("", str(raw))
    if len(d) == 11 and d.startswith("1"): d = d[1:]
    return f"+1{d}" if len(d) == 10 else None

def _norm_email(raw: Optional[str]) -> Optional[str]:
    return (raw or "").strip().lower() or None

def _norm_name(name: Optional[str]) -> Optional[str]:
    if not name: return None
    n = unicodedata.normalize("NFKD", name)
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in n if not unicodedata.combining(c)).lower()) or None

def _is_dnc(lead: Lead) -> bool:
    return bool(lead and lead.status and lead.status.lower() in _DNC_STATUSES)

def _match_live(db: Session, org_id: str, phone: Optional[str],
                ln_norm: Optional[str], email: Optional[str]):
    """Returns (lead|None, dup_status, confidence)."""
    if phone:
        rows = db.query(Lead).filter(Lead.organization_id == org_id, Lead.phone == phone).all()
        if rows:
            for r in rows:
                if ln_norm and _norm_name(r.last_name) == ln_norm:
                    return r, ImportDuplicateStatus.MATCHED_EXISTING, ImportMatchConfidence.HIGH
            return rows[0], ImportDuplicateStatus.POSSIBLE_DUPLICATE, ImportMatchConfidence.MEDIUM
    if email and ln_norm:
        r = db.query(Lead).filter(Lead.organization_id == org_id, Lead.email == email).first()
        if r and _norm_name(r.last_name) == ln_norm:
            return r, ImportDuplicateStatus.MATCHED_EXISTING, ImportMatchConfidence.LOW
    return None, ImportDuplicateStatus.NEW, ImportMatchConfidence.NONE


def _parse_df_rows(df, org_id: str, batch_id: str, db: Session) -> list:
    lookup = _build_column_lookup(list(df.columns))
    rows = []
    for idx, raw_row in df.iterrows():
        def g(key, _r=raw_row, _l=lookup):
            return str(_r[_l[key]]).strip() if key in _l and str(_r[_l[key]]).strip() not in ("", "nan") else None
        phone_raw = g("phone")
        phone = _norm_phone(phone_raw)
        email_raw = g("email")
        email = _norm_email(email_raw)
        first = g("first_name")
        last = g("last_name")
        if not first and not last:
            full = g("full_name")
            if full:
                first, last = split_full_name(full)
        ln_norm = _norm_name(last)
        live, dup_status, confidence = _match_live(db, org_id, phone, ln_norm, email)
        errors = []
        val_status = ImportValidationStatus.VALID
        if not phone and not email:
            errors.append("Missing both phone and email")
            val_status = ImportValidationStatus.INVALID
        email_err = _check_email_quality(email) if email else None
        if email_err:
            errors.append(f"Email: {email_err}")
            if val_status == ImportValidationStatus.VALID:
                val_status = ImportValidationStatus.WARNING
        if live and _is_dnc(live):
            dup_status = ImportDuplicateStatus.DNC_BLOCKED
        tier_raw = g("tier") or g("status_reason")
        status_reason = g("status_reason")
        tier = _infer_tier(tier_raw, status_reason) if (tier_raw or status_reason) else None
        staged = ImportStagedRow(
            batch_id=batch_id, organization_id=org_id,
            row_number=int(idx) + 2,
            first_name=first, last_name=last,
            phone_raw=phone_raw, phone_normalized=phone,
            email_normalized=email,
            street_address=g("street_address"), city=g("city"),
            state=g("state"), zip_code=g("zip_code"),
            source_category=g("source_category"), tier=tier,
            raw_data=json.dumps({k: str(v) for k, v in dict(raw_row).items()}),
            validation_status=val_status,
            validation_errors=json.dumps(errors) if errors else None,
            duplicate_status=dup_status, match_confidence=confidence,
            matched_lead_id=live.id if live else None,
            review_status=ImportRowReviewStatus.PENDING,
        )
        rows.append(staged)
    return rows


def _parse_google_contacts_rows(contacts: list, org_id: str, batch_id: str, db: Session) -> list:
    rows = []
    for idx, c in enumerate(contacts):
        names = (c.get("names") or [{}])[0]
        first = names.get("givenName") or None
        last  = names.get("familyName") or None
        phones = c.get("phoneNumbers") or []
        phone_raw = phones[0].get("value") if phones else None
        phone = _norm_phone(phone_raw)
        emails = c.get("emailAddresses") or []
        email = _norm_email(emails[0].get("value")) if emails else None
        addrs = (c.get("addresses") or [{}])[0]
        ln_norm = _norm_name(last)
        live, dup_status, confidence = _match_live(db, org_id, phone, ln_norm, email)
        errors, val_status = [], ImportValidationStatus.VALID
        if not phone and not email:
            errors.append("Missing both phone and email")
            val_status = ImportValidationStatus.INVALID
        if live and _is_dnc(live):
            dup_status = ImportDuplicateStatus.DNC_BLOCKED
        staged = ImportStagedRow(
            batch_id=batch_id, organization_id=org_id, row_number=idx + 1,
            first_name=first, last_name=last,
            phone_raw=phone_raw, phone_normalized=phone, email_normalized=email,
            street_address=addrs.get("streetAddress"), city=addrs.get("city"),
            state=addrs.get("region"), zip_code=addrs.get("postalCode"),
            raw_data=json.dumps(c),
            validation_status=val_status,
            validation_errors=json.dumps(errors) if errors else None,
            duplicate_status=dup_status, match_confidence=confidence,
            matched_lead_id=live.id if live else None,
            review_status=ImportRowReviewStatus.PENDING,
        )
        rows.append(staged)
    return rows


def stage_batch(batch_id: str, org_id: str, file_path: Optional[str],
                source_type: str, db: Session,
                google_contacts: Optional[list] = None) -> ImportBatch:
    """Main entry: parse source → write ImportStagedRows → update batch status.
    source_type: 'csv' | 'xlsx' | 'google_contacts'
    """
    batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id,
                                         ImportBatch.organization_id == org_id).first()
    if not batch:
        raise ValueError(f"Batch {batch_id} not found")
    batch.status = ImportBatchStatus.PROCESSING
    db.commit()
    try:
        if source_type == "google_contacts":
            rows = _parse_google_contacts_rows(google_contacts or [], org_id, batch_id, db)
        else:
            import pandas as pd
            df = (pd.read_excel(file_path, dtype=str) if source_type == "xlsx"
                  else pd.read_csv(file_path, dtype=str)).fillna("")
            rows = _parse_df_rows(df, org_id, batch_id, db)
        for r in rows:
            db.add(r)
        db.flush()
        batch.recount(db)
        batch.status = ImportBatchStatus.READY_FOR_REVIEW
        db.commit()
        log.info("Staged %d rows for batch %s", len(rows), batch_id)
        return batch
    except Exception as exc:
        db.rollback()
        try:
            batch.status = ImportBatchStatus.FAILED
            batch.error_message = str(exc)[:500]
            db.commit()
        except Exception:
            pass
        log.exception("Staging failed for batch %s", batch_id)
        raise
