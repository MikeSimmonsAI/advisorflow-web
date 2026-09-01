"""
import_commit_service.py — Commit reviewed ImportStagedRows to live Leads.

PER-ROW IDEMPOTENT: each row is committed individually with its own db.commit().
If a row fails, it is marked FAILED with an error; others continue.
Final batch state: COMMITTED (all ok), PARTIALLY_COMMITTED (some ok/some fail),
FAILED (all fail or batch-level error).

MERGE BLACKLIST — never overwrite on a MERGED row:
  Lead.id, organization_id, assigned_to_id, status=dnc, sms_consent,
  sms_consent_timestamp, all message/reply/cadence history.
BLANK-FILL only: update a field on the live Lead only if the existing value is
null/empty. Never overwrite data that already exists.
"""
from __future__ import annotations
import json, logging
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from app.models.import_models import (
    ImportBatch, ImportBatchStatus, ImportDuplicateStatus,
    ImportRowReviewStatus, ImportStagedRow,
)
from app.models.models import Lead, gen_uuid
log = logging.getLogger(__name__)

# Fields that must NEVER be overwritten when merging into an existing Lead
MERGE_BLACKLIST = {
    "id", "organization_id", "assigned_to_id", "status",
    "sms_consent", "sms_consent_timestamp", "sms_consent_ip",
    "sms_consent_text", "sms_consent_source",
    "created_at",
}

TIER_TO_TRACK = {
    "pre_need": "pre_need_lock_price", "at_need": "at_need_support",
    "imminent": "imminent_support", "contract_sold": "upsell_existing",
    "email_only": "email_only_nurture", "partial": "needs_review",
    "addr_only": "needs_review",
}

def _blank_fill(lead: Lead, row: ImportStagedRow) -> bool:
    """Apply blank-fill merge: set field on lead only if currently null/empty.
    Returns True if any field was changed."""
    changed = False
    FILL_MAP = {
        "first_name": row.first_name, "last_name": row.last_name,
        "phone_raw": row.phone_raw, "email": row.email_normalized,
        "street_address": row.street_address, "city": row.city,
        "state": row.state, "zip_code": row.zip_code,
        "source_category": row.source_category,
    }
    for field, val in FILL_MAP.items():
        if val and not getattr(lead, field, None):
            setattr(lead, field, val)
            changed = True
    return changed


def commit_batch(batch_id: str, org_id: str, db: Session, committer_id: str) -> ImportBatch:
    """Per-row idempotent commit. Rows already COMMITTED are skipped.
    Result: COMMITTED / PARTIALLY_COMMITTED / FAILED."""
    batch = db.query(ImportBatch).filter(
        ImportBatch.id == batch_id, ImportBatch.organization_id == org_id
    ).first()
    if not batch:
        raise ValueError(f"Batch {batch_id} not found")

    batch.status = ImportBatchStatus.COMMITTING
    db.commit()

    rows = db.query(ImportStagedRow).filter(
        ImportStagedRow.batch_id == batch_id,
        ImportStagedRow.review_status.in_([
            ImportRowReviewStatus.ACCEPTED,
            ImportRowReviewStatus.MERGED,
            ImportRowReviewStatus.COMMITTED,
        ]),
    ).all()

    ok_count = 0
    fail_count = 0

    for row in rows:
        if row.review_status == ImportRowReviewStatus.COMMITTED:
            ok_count += 1
            continue
        try:
            if (row.duplicate_status in (
                    ImportDuplicateStatus.MATCHED_EXISTING,
                    ImportDuplicateStatus.POSSIBLE_DUPLICATE)
                    and row.matched_lead_id
                    and row.review_status == ImportRowReviewStatus.MERGED):
                lead = db.query(Lead).filter(Lead.id == row.matched_lead_id).first()
                if lead:
                    _blank_fill(lead, row)
                    db.flush()
            else:
                tier = row.tier or "pre_need"
                lead = Lead(
                    id=gen_uuid(), organization_id=org_id,
                    first_name=row.first_name, last_name=row.last_name,
                    phone=row.phone_normalized, phone_raw=row.phone_raw,
                    email=row.email_normalized,
                    street_address=row.street_address, city=row.city,
                    state=row.state, zip_code=row.zip_code,
                    source_category=row.source_category or "import",
                    tier=tier, track=TIER_TO_TRACK.get(tier, "needs_review"),
                    status="new", created_by_id=committer_id,
                )
                db.add(lead)
                db.flush()
            row.review_status = ImportRowReviewStatus.COMMITTED
            row.committed_at = datetime.now(timezone.utc)
            row.committed_by_id = committer_id
            db.commit()
            ok_count += 1
        except Exception as exc:
            db.rollback()
            row.review_status = ImportRowReviewStatus.REJECTED
            row.review_note = f"Commit error: {str(exc)[:200]}"
            db.commit()
            fail_count += 1
            log.exception("Commit failed row %s batch %s", row.id, batch_id)

    batch.recount(db)
    if ok_count > 0 and fail_count == 0:
        batch.status = ImportBatchStatus.COMMITTED
    elif ok_count > 0:
        batch.status = ImportBatchStatus.PARTIALLY_COMMITTED
    else:
        batch.status = ImportBatchStatus.FAILED
        batch.error_message = f"All {fail_count} rows failed to commit"

    batch.committed_at = datetime.now(timezone.utc)
    batch.committed_by_id = committer_id
    db.commit()
    return batch
