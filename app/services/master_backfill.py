"""BACKFILL — bringing the leads that already exist into the master database.

Ingestion only knows about people who arrive from now on. Every lead already
in the estate — four thousand in WUPA, two hundred in Fiber Cartel, whatever
Atlantis imports on go-live day — has to be walked once.

===========================================================================
WHAT THIS IS ALLOWED TO DO, WRITTEN DOWN SO IT STAYS TRUE
===========================================================================

It READS `leads` and WRITES `master_contacts` and `lead_occurrences`. That is
the entire blast radius, and it is worth being explicit about the things this
module deliberately cannot do, because a bulk pass over every lead in the
estate is precisely where a mistake would be catastrophic:

  * IT DOES NOT MODIFY A CUSTOMER'S LEAD. No column on `leads` is written.
  * IT SENDS NOTHING. No email, no SMS, no notification, no webhook.
  * IT TRIGGERS NOTHING. No campaign, no cadence, no AI workforce action, no
    scoring pass, no enrichment. It never calls a service that could.
  * IT DELETES NOTHING, EVER. There is no DELETE in this file.

===========================================================================
SAFE TO RUN AGAIN, AND AGAIN
===========================================================================

Idempotent twice over. It only SELECTS leads with no occurrence yet, so a
second run has almost nothing to look at; and `record_lead` is itself
idempotent, so even a lead that slipped through both guards is recognised
rather than duplicated. Interrupting it mid-run is safe — every batch commits
on its own and the next run resumes from what is missing, not from a cursor
somebody has to remember.

`limit` exists so the first production run can be a hundred rows that somebody
looks at before the rest follow.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.master_contact_models import LeadOccurrence, MasterContact
from app.models.models import Lead
from app.services import master_contacts

logger = logging.getLogger(__name__)

DEFAULT_BATCH = 500


def pending_count(db: Session, organization_id: Optional[str] = None) -> int:
    """How many leads have no master occurrence yet."""
    q = db.query(func.count(Lead.id)).filter(~_has_occurrence())
    if organization_id:
        q = q.filter(Lead.organization_id == organization_id)
    return int(q.scalar() or 0)


def _has_occurrence():
    return (
        select(LeadOccurrence.id)
        .where(LeadOccurrence.organization_id == Lead.organization_id)
        .where(LeadOccurrence.lead_id == Lead.id)
        .exists()
    )


def stats(db: Session) -> dict:
    """What the master database currently holds. Read-only."""
    total_leads = int(db.query(func.count(Lead.id)).scalar() or 0)
    return {
        "leads_total": total_leads,
        "leads_without_occurrence": pending_count(db),
        "master_contacts": int(db.query(func.count(MasterContact.id)).scalar() or 0),
        "master_contacts_synthetic": int(
            db.query(func.count(MasterContact.id))
            .filter(MasterContact.is_synthetic.is_(True)).scalar() or 0),
        "master_contacts_needing_review": int(
            db.query(func.count(MasterContact.id))
            .filter(MasterContact.needs_review.is_(True)).scalar() or 0),
        "lead_occurrences": int(db.query(func.count(LeadOccurrence.id)).scalar() or 0),
        "organizations_represented": int(
            db.query(func.count(func.distinct(LeadOccurrence.organization_id)))
            .scalar() or 0),
    }


def backfill(
    db: Session,
    *,
    organization_id: Optional[str] = None,
    limit: Optional[int] = None,
    batch_size: int = DEFAULT_BATCH,
    dry_run: bool = False,
) -> dict:
    """Walk leads with no occurrence and record them. Returns counts.

    `dry_run` counts what WOULD be written and writes nothing — the mode to
    run first against production, because a number nobody expected is the
    cheapest possible place to discover a wrong assumption.
    """
    scanned = 0
    recorded = 0
    failed = 0
    cursor: Optional[str] = None

    contacts_before = int(db.query(func.count(MasterContact.id)).scalar() or 0)

    if dry_run:
        pending = pending_count(db, organization_id)
        return {
            "dry_run": True,
            "organization_id": organization_id,
            "would_record": min(pending, limit) if limit else pending,
            "leads_without_occurrence": pending,
            "recorded": 0,
            "contacts_created": 0,
            "failed": 0,
        }

    while True:
        remaining = None if limit is None else max(0, limit - scanned)
        if remaining == 0:
            break
        take = batch_size if remaining is None else min(batch_size, remaining)

        # Keyset pagination on the primary key rather than OFFSET: every batch
        # commits, so rows shift under an offset and a page would be skipped.
        q = db.query(Lead).filter(~_has_occurrence())
        if organization_id:
            q = q.filter(Lead.organization_id == organization_id)
        if cursor is not None:
            q = q.filter(Lead.id > cursor)
        rows = q.order_by(Lead.id.asc()).limit(take).all()
        if not rows:
            break

        for lead in rows:
            scanned += 1
            cursor = lead.id
            occurrence = master_contacts.record_lead(
                db, lead,
                source=getattr(lead, "source", None) or _source_from_legacy(lead),
                source_detail=getattr(lead, "source_detail", None),
                ingestion_path="master_backfill",
            )
            if occurrence is None:
                failed += 1
            else:
                recorded += 1

        db.commit()
        logger.info("master_backfill: %d scanned, %d recorded, %d failed",
                    scanned, recorded, failed)

    contacts_after = int(db.query(func.count(MasterContact.id)).scalar() or 0)
    return {
        "dry_run": False,
        "organization_id": organization_id,
        "scanned": scanned,
        "recorded": recorded,
        "failed": failed,
        "contacts_created": max(0, contacts_after - contacts_before),
        "leads_without_occurrence_remaining": pending_count(db, organization_id),
    }


def _source_from_legacy(lead: Lead) -> Optional[str]:
    """Best-effort provenance for leads that predate the `source` column.

    `source_file` is the older field and holds a filename for imports and a
    marker like "manual" or "voice:..." for everything else. Reading it is how
    a lead from 2023 gets an origin at all — and reading it is all this does:
    the lead itself is never written.
    """
    legacy = (getattr(lead, "source_file", None) or "").strip()
    if not legacy:
        return None
    if legacy == "manual":
        return "manual"
    if legacy.startswith("voice:"):
        return "voice"
    return "import"
