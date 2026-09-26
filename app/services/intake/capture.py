"""ONE record, through the SAME intake engine - for web forms and webhooks.

Universal Intake is batch-shaped (create_batch -> analysis -> commit) because
most sources are files. A web form is a source too, and it must not get its
own matcher, its own contact table or its own lead rules - that is how a
platform ends up with two answers to "is this the same person?". So a single
submission is captured as a one-row batch and run through the engine
unchanged:

    headers + one row  ->  create_batch  ->  run_analysis  ->  run_commit
                          import_batches     import_staged_rows   org_contacts
                          import_batch_files                      leads
                                                                  import_record_versions

which gives a form submission everything an import has: normalization, the
one matcher (source id / email / phone-with-identity / address), the contact
record, fill-blanks updates that never overwrite a person's hand edits, and a
batch that can be rolled back.

WHAT IS DIFFERENT FOR A PERSON WHO REACHED OUT (and only that)
  * classification defaults to the caller's (e.g. `new_inquiry`) - someone who
    submitted a form did ask to hear back;
  * an EXACT match updates the existing record (fill blanks) and reuses its
    Lead; a POSSIBLE match is never merged - it is kept as a separate contact,
    marked for review, and still gets a Lead because the person explicitly
    asked to be contacted;
  * an existing do-not-contact record is not blocked from capture: the
    submission is preserved on that record and its Lead stays exactly as it
    was (DNC, suppression and consent gates still refuse every send);
  * at the plan's lead limit the Lead is created HELD (lead_capacity), never
    dropped and never counted.

Nothing here sends anything, grants SMS consent or starts a cadence.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.import_models import ImportStagedRow
from app.models.intake_models import CommitMode, DuplicateResolution, IntakeStatus, OrgContact
from app.services.intake import commit as COMMIT
from app.services.intake import engine as ENG
from app.services.intake.context import IntakeContext

log = logging.getLogger(__name__)

HEADERS = ["First Name", "Last Name", "Email", "Phone", "Street Address", "City", "State",
           "Zip Code", "Notes"]
_KEYS = ["first_name", "last_name", "email", "phone", "street_address", "city", "state",
         "zip_code", "notes"]


@dataclass
class CaptureResult:
    batch_id: str
    contact_id: Optional[str]
    lead: Any
    match: str                       # new | existing | possible | blocked_existing
    held: bool = False
    lead_created: bool = False
    notes: List[str] = field(default_factory=list)


def system_context(org, actor_label: str) -> IntakeContext:
    return IntakeContext(org_id=org.id, org_name=org.name or getattr(org, "brand_name", "") or "",
                         org_slug=getattr(org, "slug", None), actor_id=None, actor_name=actor_label,
                         actor_email=None, role="system", acting_as_platform_owner=False)


def _csv(record: Dict[str, Any]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADERS)
    w.writerow([("" if record.get(k) is None else str(record.get(k))) for k in _KEYS])
    return buf.getvalue().encode("utf-8")


def capture_one(db: Session, org, record: Dict[str, Any], *, source: str = "website",
                source_detail: Optional[str] = None, list_name: Optional[str] = None,
                classification: str = "new_inquiry", actor_label: str = "Public web form",
                external: bool = True) -> CaptureResult:
    """Capture one person. COMMITS (the engine commits as it goes)."""
    from app.models.models import Lead
    ctx = system_context(org, actor_label)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    batch = ENG.create_batch(db, ctx, content=_csv(record), filename="capture-%s.csv" % stamp,
                             source=source, source_detail=source_detail, list_name=list_name,
                             display_name="%s %s" % (actor_label, stamp))
    cfg = json.loads(batch.classification_json or "{}")
    cfg["fallback"] = classification
    batch.classification_json = json.dumps(cfg)
    db.commit()
    ENG.run_analysis(db, batch.id, org.id)

    row = (db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == batch.id,
                                           ImportStagedRow.organization_id == org.id).one())
    match = {IntakeStatus.EXISTING_MATCH: "existing", IntakeStatus.NEEDS_REVIEW: "possible",
             IntakeStatus.BLOCKED: "blocked_existing"}.get(row.intake_status, "new")
    notes: List[str] = []
    if row.intake_status == IntakeStatus.BLOCKED:
        # An explicit inbound submission from a person whose existing record is
        # do-not-contact: preserved ON that record, which stays DNC.
        row.intake_status = IntakeStatus.APPROVED
        row.duplicate_resolution = DuplicateResolution.UPDATE_EXISTING
        row.review_note = ("Explicit inbound submission from an existing do-not-contact record: "
                           "preserved on that record; every outreach block still applies.")
        notes.append("existing_record_dnc")
    elif row.intake_status == IntakeStatus.NEEDS_REVIEW:
        # A POSSIBLE match is never merged: a separate contact, marked for review.
        row.duplicate_resolution = DuplicateResolution.KEEP_SEPARATE
        notes.append("possible_duplicate_kept_separate")
    db.commit()

    COMMIT.run_commit(db, batch.id, org.id, ctx, CommitMode.READY_AND_REVIEW,
                      include_enrichment=True, hold_when_full=external)
    db.expire_all()
    row = db.query(ImportStagedRow).filter(ImportStagedRow.id == row.id).one()
    contact = (db.query(OrgContact).filter(OrgContact.id == row.committed_contact_id,
                                           OrgContact.organization_id == org.id).first()
               if row.committed_contact_id else None)
    lead = None
    lead_id = row.committed_lead_id or getattr(contact, "lead_id", None)
    if lead_id:
        lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == org.id).first()
    created = bool(lead is not None and lead.import_batch_id == batch.id)

    if lead is None and contact is not None and external and row.creates_lead:
        # The review row a person explicitly submitted: activate its Lead
        # (held at the limit), still marked for review on the contact.
        try:
            from app.services import plan_limits
            capacity = plan_limits.counter_for_org_id(db, org.id, plan_limits.LIMIT_LEADS)
        except Exception:  # noqa: BLE001
            capacity = None
        lead = COMMIT.activate_lead(db, _batch(db, batch.id, org.id), row, contact,
                                    ENG.org_catalog(db, org.id), actor_label, capacity,
                                    hold_when_full=True)
        row.committed_lead_id = lead.id
        created = True
        db.commit()
    from app.services import lead_capacity
    held = bool(lead is not None and lead_capacity.is_held(lead))
    return CaptureResult(batch_id=batch.id, contact_id=getattr(contact, "id", None), lead=lead,
                         match=match, held=held, lead_created=created, notes=notes)


def _batch(db, batch_id, org_id):
    from app.models.import_models import ImportBatch
    return (db.query(ImportBatch).filter(ImportBatch.id == batch_id,
                                         ImportBatch.organization_id == org_id).one())
