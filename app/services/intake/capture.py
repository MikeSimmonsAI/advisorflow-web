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
           "Zip Code", "Notes", "Company"]
_KEYS = ["first_name", "last_name", "email", "phone", "street_address", "city", "state",
         "zip_code", "notes", "company"]


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


def user_context(org, user) -> IntakeContext:
    """A capture made by a signed-in person (an operator adding one owner).

    The ORGANIZATION is the one the caller already resolved and authorized for
    this request; only the actor comes from the user, so the batch, its audit
    rows and its record versions say who did it and as whom."""
    from app.services.platform_owner import is_platform_owner
    name = getattr(user, "full_name", None) or getattr(user, "email", None) or user.id
    return IntakeContext(org_id=org.id, org_name=org.name or getattr(org, "brand_name", "") or "",
                         org_slug=getattr(org, "slug", None), actor_id=user.id, actor_name=name,
                         actor_email=getattr(user, "email", None),
                         role=getattr(user, "role", None) or "user",
                         acting_as_platform_owner=is_platform_owner(user))


def _csv(record: Dict[str, Any]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADERS)
    w.writerow([("" if record.get(k) is None else str(record.get(k))) for k in _KEYS])
    return buf.getvalue().encode("utf-8")


def capture_one(db: Session, org, record: Dict[str, Any], *, source: str = "website",
                source_detail: Optional[str] = None, list_name: Optional[str] = None,
                classification: str = "new_inquiry", actor_label: str = "Public web form",
                external: bool = True, explicit: Optional[bool] = None,
                user=None) -> CaptureResult:
    """Capture one person. COMMITS (the engine commits as it goes).

    `external`  - the person reached out on their own (a web form, a webhook):
                  at the plan's lead limit the Lead is created HELD.
    `explicit`  - somebody deliberately asked for THIS person to be worked (the
                  person themselves, or an operator adding one owner by hand):
                  a POSSIBLE match still gets its own Lead, kept separate and
                  marked for review. Defaults to `external`. A present operator
                  is subject to the plan limit (no hold); the caller checks
                  capacity before capturing so a refusal writes nothing.
    `user`      - the signed-in actor, when there is one."""
    from app.models.models import Lead
    if explicit is None:
        explicit = external
    ctx = user_context(org, user) if user is not None else system_context(org, actor_label)
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

    if lead is None and contact is not None and explicit and row.creates_lead:
        # The review row a person explicitly submitted: activate its Lead
        # (held at the limit), still marked for review on the contact.
        try:
            from app.services import plan_limits
            capacity = plan_limits.counter_for_org_id(db, org.id, plan_limits.LIMIT_LEADS)
        except Exception:  # noqa: BLE001
            capacity = None
        lead = COMMIT.activate_lead(db, _batch(db, batch.id, org.id), row, contact,
                                    ENG.org_catalog(db, org.id), ctx.actor_name, capacity,
                                    hold_when_full=external)
        if lead is not None:
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


@dataclass
class CapturedRow:
    index: int                       # position in the caller's list
    contact_id: Optional[str]
    lead: Any
    match: str                       # new | existing | possible | blocked_existing | duplicate | not_imported
    lead_created: bool = False


@dataclass
class ManyResult:
    batch_id: str
    rows: List[CapturedRow]


def capture_many(db: Session, org, records: List[Dict[str, Any]], *, source: str,
                 source_detail: Optional[str] = None, list_name: Optional[str] = None,
                 classification: str = "cold_prospect", actor_label: str = "Import",
                 user=None) -> ManyResult:
    """Capture MANY people deliberately added by a present operator (a module's
    own file import, e.g. Wholesale's property CSV) as ONE intake batch.

    Same engine and same rules as any import - one matcher, org contacts,
    fill-blanks, a single batch the organization can roll back - plus what a
    deliberate add means: an existing do-not-contact person is kept on their
    existing record (every block still applies), and a POSSIBLE match is kept
    separate, marked for review, and still gets its Lead. The plan limit is the
    normal bulk rule: past it the contact is kept and no Lead is made, and the
    caller reports that row. COMMITS.

    Returns one CapturedRow per input record, in order (row N of the file is
    records[N-2]); an in-file duplicate resolves to the row it duplicates."""
    from app.models.models import Lead
    ctx = user_context(org, user) if user is not None else system_context(org, actor_label)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADERS)
    for r in records:
        w.writerow([("" if r.get(k) is None else str(r.get(k))) for k in _KEYS])
    batch = ENG.create_batch(db, ctx, content=buf.getvalue().encode("utf-8"),
                             filename="capture-%s.csv" % stamp, source=source,
                             source_detail=source_detail, list_name=list_name,
                             display_name="%s %s" % (actor_label, stamp))
    cfg = json.loads(batch.classification_json or "{}")
    cfg["fallback"] = classification
    batch.classification_json = json.dumps(cfg)
    db.commit()
    ENG.run_analysis(db, batch.id, org.id)

    staged = (db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == batch.id,
                                              ImportStagedRow.organization_id == org.id).all())
    pre = {}
    for row in staged:
        pre[row.id] = {IntakeStatus.EXISTING_MATCH: "existing", IntakeStatus.NEEDS_REVIEW: "possible",
                       IntakeStatus.BLOCKED: "blocked_existing",
                       IntakeStatus.DUPLICATE: "duplicate"}.get(row.intake_status, "new")
        if row.intake_status == IntakeStatus.BLOCKED:
            row.intake_status = IntakeStatus.APPROVED
            row.duplicate_resolution = DuplicateResolution.UPDATE_EXISTING
            row.review_note = ("Added by an operator for an existing do-not-contact record: "
                               "kept on that record; every outreach block still applies.")
        elif row.intake_status == IntakeStatus.NEEDS_REVIEW:
            row.duplicate_resolution = DuplicateResolution.KEEP_SEPARATE
    db.commit()

    COMMIT.run_commit(db, batch.id, org.id, ctx, CommitMode.READY_AND_REVIEW,
                      include_enrichment=True, hold_when_full=False)
    db.expire_all()
    staged = (db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == batch.id,
                                              ImportStagedRow.organization_id == org.id).all())
    by_id = {r.id: r for r in staged}
    catalog = None
    b = None
    try:
        from app.services import plan_limits
        capacity = plan_limits.counter_for_org_id(db, org.id, plan_limits.LIMIT_LEADS)
    except Exception:  # noqa: BLE001
        capacity = None

    out: List[CapturedRow] = []
    for row in sorted(staged, key=lambda r: r.row_number):
        src = row
        if row.duplicate_of_staged_row_id and row.duplicate_of_staged_row_id in by_id:
            src = by_id[row.duplicate_of_staged_row_id]
        contact = (db.query(OrgContact).filter(OrgContact.id == src.committed_contact_id,
                                               OrgContact.organization_id == org.id).first()
                   if src.committed_contact_id else None)
        lead_id = src.committed_lead_id or getattr(contact, "lead_id", None)
        lead = (db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == org.id).first()
                if lead_id else None)
        created = bool(lead is not None and lead.import_batch_id == batch.id and src is row)
        if lead is None and contact is not None and src is row and row.creates_lead:
            # A review row an operator deliberately added: its own Lead, still
            # marked for review on the contact. Plan limit applies (no hold).
            if catalog is None:
                catalog, b = ENG.org_catalog(db, org.id), _batch(db, batch.id, org.id)
            lead = COMMIT.activate_lead(db, b, row, contact, catalog, ctx.actor_name, capacity,
                                        hold_when_full=False)
            if lead is not None:
                row.committed_lead_id = lead.id
                created = True
        match = pre.get(row.id, "new") if (contact is not None or lead is not None) else "not_imported"
        out.append(CapturedRow(index=row.row_number - 2, contact_id=getattr(contact, "id", None),
                               lead=lead, match=match, lead_created=created))
    db.commit()
    return ManyResult(batch_id=batch.id, rows=out)
