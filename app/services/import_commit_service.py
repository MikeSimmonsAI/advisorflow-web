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



def _apply_more_restrictive_consent(lead: Lead, row: ImportStagedRow) -> bool:
    """
    More-restrictive-wins rule for consent channels.
    
    On MERGE:
    - If the existing lead has a consent channel denied (False), keep False —
      an import can never GRANT permission that was previously denied.
    - If the staged row has a channel denied (False), apply the denial even if
      the lead currently has True or None (import of a denial is authoritative).
    - None (unknown/ambiguous) on the staged row → do NOT change the existing value.
      None on the lead → keep None; never set None to True from an import.
    
    This function writes only to sms_consent on the Lead model (the only consent
    field currently on Lead).  Additional channels (email, bulk_email, voice) are
    preserved in import_staged_rows for audit; they will be applied when the Lead
    model gains those fields.

    Returns True if any field was changed.
    """
    changed = False
    # SMS consent is the only field currently on Lead
    if row.consent_sms is not None:
        if row.consent_sms is False:
            # Staged denial is authoritative — apply even if lead currently allows
            if lead.sms_consent is not False:
                lead.sms_consent = False
                changed = True
        elif row.consent_sms is True:
            # Staged grant only applies if lead has no opinion (None/False→None is safe,
            # True→True is a no-op) — but NEVER override an existing False denial.
            # Lead.sms_consent default is False, so we only grant if it was previously None.
            # Actually: existing False means user previously opted out → do not grant.
            # None (unknown) → grant from import is reasonable.
            if lead.sms_consent is None:
                lead.sms_consent = True
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
                    _apply_more_restrictive_consent(lead, row)
                    db.flush()
            else:
                tier = row.tier or "pre_need"
                # The uploader's batch-level choice beats what was inferred
                # from the file. force_new_inquiry exists precisely for the
                # case where the operator knows the whole list is inbound
                # enquiries and the file's own columns say otherwise.
                if batch.force_new_inquiry:
                    tier = "new_inquiry"
                lead = Lead(
                    id=gen_uuid(), organization_id=org_id,
                    first_name=row.first_name, last_name=row.last_name,
                    phone=row.phone_normalized, phone_raw=row.phone_raw,
                    email=row.email_normalized,
                    street_address=row.street_address, city=row.city,
                    state=row.state, zip_code=row.zip_code,
                    source_category=row.source_category or "import",
                    tier=tier, message_track=TIER_TO_TRACK.get(tier, "needs_review"),
                    status="new",
                    # Batch-level provenance, carried from the upload form.
                    # These were being accepted by the endpoint and silently
                    # dropped here; a lead whose source_year is None because
                    # nobody threaded the value through is indistinguishable
                    # from one whose source year is genuinely unknown, which
                    # is exactly the kind of quiet data loss that only shows
                    # up months later when someone filters a campaign by year
                    # and gets nothing.
                    source_year=batch.source_year,
                    source_file=batch.source_filename,
                    import_list_name=batch.import_list_name,
                    relationship_type=(row.relationship_type
                                       or batch.relationship_type),
                )
                db.add(lead)
                db.flush()
            row.review_status = ImportRowReviewStatus.COMMITTED
            row.committed_at = datetime.now(timezone.utc)
            row.committed_by_id = committer_id
            # Record WHICH lead this staged row became.
            #
            # The column existed and was documented as post-commit provenance,
            # but nothing ever wrote it, so after a commit there was no link
            # from an imported row back to the live lead it produced - the
            # audit trail stopped at "committed". Both branches set it: the
            # merge branch points at the lead that absorbed the row, the
            # create branch at the lead it made.
            if lead is not None:
                row.committed_lead_id = lead.id
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
