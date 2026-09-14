"""THE MASTER LEAD DATABASE API — god_admin only, and only ever god_admin.

===========================================================================
THE ONE RULE THIS FILE EXISTS TO ENFORCE
===========================================================================

Everything here reads ACROSS TENANTS. That is the entire point of the master
database and it is also the only genuinely dangerous thing about it: one
customer seeing another customer's people would be the worst defect this
platform could ship.

So the isolation is structural rather than careful:

  * Every route is `Depends(require_god)`. Not require_admin, not
    require_super_admin — god_admin, which no customer account can hold.
  * `X-Org-Override` is IGNORED here. An operator standing inside a customer
    (customer-view) still reads the whole master database, because this router
    is not a customer surface and never renders inside one. Nothing in this
    file reads `user.organization_id` or the active workspace at all.
  * No customer-facing router imports these models. `/leads`, `/crm`,
    `/imports` and every other tenant API continue to query `leads` with an
    organization filter and know nothing about this layer.

A customer cannot reach cross-tenant data through this router because the
router refuses them at the door, and cannot reach it through their own
routers because their own routers do not query these tables.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.master_contact_models import LeadOccurrence, MasterContact
from app.models.models import Organization, Platform, User
from app.services import master_backfill
from app.services.dedup_service import normalize_phone
# The SAME normalizer the writer used. A search that normalizes differently
# from the write is a search that cannot find what it stored.
from app.services.master_contacts import normalize_email

log = logging.getLogger(__name__)

router = APIRouter(prefix="/god/master", tags=["AdvisorFlow Master Lead Database"])


# ── search ──────────────────────────────────────────────────────────────────

@router.get("/contacts")
def master_contacts_search(
    search: Optional[str] = Query(None, description="name, email or phone"),
    platform_id: Optional[str] = Query(None),
    org_id: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    include_synthetic: bool = Query(False),
    needs_review: bool = Query(False),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    god: User = Depends(require_god),
    db: Session = Depends(get_db),
):
    """One row per APPEARANCE, which is what a lineage question actually asks.

    A contact-per-row table cannot answer "which organizations has this person
    been through" without expanding something; an occurrence-per-row table
    answers it by existing. The person's identity and total occurrence count
    ride along on each row, so the same table still reads as a people list.

    Synthetic and QA records are EXCLUDED BY DEFAULT. Demo orgs, acceptance
    tests and proof scenarios all write real Lead rows, and a master database
    where a quarter of the people are props is a master database nobody trusts.
    They are hidden, never deleted: `include_synthetic=true` shows them.
    """
    q = (db.query(LeadOccurrence, MasterContact)
           .join(MasterContact, MasterContact.id == LeadOccurrence.master_contact_id))

    if not include_synthetic:
        q = q.filter(LeadOccurrence.is_synthetic.is_(False),
                     MasterContact.is_synthetic.is_(False))
    if needs_review:
        q = q.filter(MasterContact.needs_review.is_(True))
    if org_id:
        q = q.filter(LeadOccurrence.organization_id == org_id)
    if platform_id:
        q = q.filter(LeadOccurrence.platform_id == platform_id)
    if source:
        q = q.filter(LeadOccurrence.source == source)

    if search:
        term = search.strip()
        like = f"%{term.lower()}%"
        clauses = [
            func.lower(func.coalesce(MasterContact.display_first_name, "")).like(like),
            func.lower(func.coalesce(MasterContact.display_last_name, "")).like(like),
            func.lower(func.coalesce(MasterContact.normalized_email, "")).like(like),
        ]
        # A searched phone number is normalized the same way a stored one was,
        # so "(555) 010-1234" finds 15550101234. Without this, the only people
        # findable by phone would be the ones typed in exactly the stored form.
        digits = normalize_phone(term)
        if digits:
            clauses.append(MasterContact.normalized_phone == digits)
        else:
            stripped = "".join(ch for ch in term if ch.isdigit())
            if len(stripped) >= 4:
                clauses.append(MasterContact.normalized_phone.like(f"%{stripped}%"))
        exact_email = normalize_email(term)
        if exact_email:
            clauses.append(MasterContact.normalized_email == exact_email)
        q = q.filter(or_(*clauses))

    total = q.count()
    rows = (q.order_by(LeadOccurrence.last_seen_at.desc().nullslast(),
                       LeadOccurrence.id.desc())
             .offset(skip).limit(limit).all())

    # Names for the ids, resolved in two queries rather than per row.
    org_ids = {o.organization_id for o, _ in rows if o.organization_id}
    plat_ids = {o.platform_id for o, _ in rows if o.platform_id}
    orgs = {}
    plats = {}
    if org_ids:
        orgs = {o.id: o.name for o in
                db.query(Organization).filter(Organization.id.in_(org_ids)).all()}
    if plat_ids:
        plats = {p.id: p.name for p in
                 db.query(Platform).filter(Platform.id.in_(plat_ids)).all()}

    def _row(occ: LeadOccurrence, contact: MasterContact) -> dict:
        name = " ".join(x for x in [contact.display_first_name,
                                    contact.display_last_name] if x).strip()
        return {
            "occurrence_id": occ.id,
            "master_contact_id": contact.id,
            "platform_id": occ.platform_id,
            "platform_name": plats.get(occ.platform_id),
            "organization_id": occ.organization_id,
            "organization_name": orgs.get(occ.organization_id),
            "lead_id": occ.lead_id,
            "name": name or None,
            "email": contact.normalized_email,
            "phone": contact.normalized_phone,
            "source": occ.source,
            "source_detail": occ.source_detail,
            "import_batch_id": occ.import_batch_id,
            "tenant_lead_status": occ.tenant_lead_status,
            "first_seen_at": occ.first_seen_at.isoformat() if occ.first_seen_at else None,
            "last_seen_at": occ.last_seen_at.isoformat() if occ.last_seen_at else None,
            "occurrence_count": contact.occurrence_count,
            "is_synthetic": bool(occ.is_synthetic or contact.is_synthetic),
            "needs_review": bool(contact.needs_review),
            "review_reason": contact.review_reason,
        }

    return {"total": total, "rows": [_row(o, c) for o, c in rows]}


@router.get("/contacts/{contact_id}")
def master_contact_detail(
    contact_id: str,
    god: User = Depends(require_god),
    db: Session = Depends(get_db),
):
    """One human and every organization they have ever appeared in."""
    contact = db.query(MasterContact).filter(MasterContact.id == contact_id).first()
    if contact is None:
        raise HTTPException(status_code=404, detail="Not found")

    occurrences = (db.query(LeadOccurrence)
                     .filter(LeadOccurrence.master_contact_id == contact_id)
                     .order_by(LeadOccurrence.first_seen_at.asc()).all())
    org_ids = {o.organization_id for o in occurrences if o.organization_id}
    orgs = {}
    if org_ids:
        orgs = {o.id: o.name for o in
                db.query(Organization).filter(Organization.id.in_(org_ids)).all()}

    return {
        "id": contact.id,
        "name": " ".join(x for x in [contact.display_first_name,
                                     contact.display_last_name] if x).strip() or None,
        "email": contact.normalized_email,
        "phone": contact.normalized_phone,
        "first_seen_at": contact.first_seen_at.isoformat() if contact.first_seen_at else None,
        "last_seen_at": contact.last_seen_at.isoformat() if contact.last_seen_at else None,
        "occurrence_count": contact.occurrence_count,
        "is_synthetic": contact.is_synthetic,
        "synthetic_reason": contact.synthetic_reason,
        "needs_review": contact.needs_review,
        "review_reason": contact.review_reason,
        "occurrences": [{
            "id": o.id,
            "organization_id": o.organization_id,
            "organization_name": orgs.get(o.organization_id),
            "platform_id": o.platform_id,
            "lead_id": o.lead_id,
            "source": o.source,
            "source_detail": o.source_detail,
            "import_batch_id": o.import_batch_id,
            "tenant_lead_status": o.tenant_lead_status,
            "first_seen_at": o.first_seen_at.isoformat() if o.first_seen_at else None,
            "last_seen_at": o.last_seen_at.isoformat() if o.last_seen_at else None,
            "is_synthetic": o.is_synthetic,
        } for o in occurrences],
    }


# ── operations ──────────────────────────────────────────────────────────────

@router.get("/stats")
def master_stats(
    god: User = Depends(require_god),
    db: Session = Depends(get_db),
):
    """Coverage, in numbers. `leads_without_occurrence` is the honest one."""
    return master_backfill.stats(db)


@router.post("/backfill")
def run_backfill(
    organization_id: Optional[str] = Query(None),
    limit: Optional[int] = Query(None, ge=1, le=100000),
    batch_size: int = Query(master_backfill.DEFAULT_BATCH, ge=1, le=2000),
    dry_run: bool = Query(True),
    refresh: bool = Query(False),
    after_lead_id: Optional[str] = Query(None),
    god: User = Depends(require_god),
    db: Session = Depends(get_db),
):
    """Bring existing leads into the master database.

    `dry_run` DEFAULTS TO TRUE. Running the real thing takes an explicit
    `dry_run=false`, because the default behaviour of a bulk endpoint should
    be the one that cannot surprise anybody.

    `refresh=true` also revisits leads that already have an occurrence, so
    derived fields are recomputed under the current rules. It creates nothing.

    Writes only `master_contacts` and `lead_occurrences`; see
    `app/services/master_backfill.py` for what it structurally cannot do.
    """
    result = master_backfill.backfill(
        db,
        organization_id=organization_id,
        limit=limit,
        batch_size=batch_size,
        dry_run=dry_run,
        refresh=refresh,
        after_lead_id=after_lead_id,
    )
    log.info("god master backfill by %s: %s", god.id, result)
    return result
