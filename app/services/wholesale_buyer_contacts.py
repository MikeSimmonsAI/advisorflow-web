"""Cash buyers in the shared contact database (P3).

A cash buyer is a COUNTERPARTY, not a prospect: never a Lead, never in a
cadence (see the wholesale_models docstring). But who the buyer IS belongs to
the one tenant-scoped identity layer, Universal Intake's `org_contacts`, so a
buyer who is also somebody's seller, a funding partner, or an imported contact
is one person - not three copies in three modules.

So each buyer is captured through intake with the built-in PARTNER
classification (record class PARTNER, creates no Lead) and keeps the link in
`wholesale_buyers.org_contact_id`. Everything that is about the BUYER ROLE -
buy boxes, standing, matching, outreach, proof of funds, claims, deal history -
stays on the buyer row and is untouched by linking.

* Test buyers (`is_test`) stay out of the contact database, the same sandbox
  rule EvoSense applies to synthetic owners.
* Linking never fails a buyer write: an intake error leaves the buyer exactly
  as it was, unlinked, with an event saying why - and a later `link_all`
  picks it up.
* Re-running is safe: a linked buyer is skipped, and intake's own matcher
  reuses an existing contact (same email / phone) instead of duplicating it.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.intake_models import OrgContact
from app.models.wholesale_models import ACTOR_SYSTEM, ACTOR_USER, WholesaleBuyer
from app.services import wholesale_service as svc

log = logging.getLogger(__name__)

SOURCE = "manual"
SOURCE_DETAIL = "Wholesale cash buyer"
LIST_NAME = "Cash buyers"


def _record(b: WholesaleBuyer) -> Dict[str, Any]:
    first, _, last = (b.contact_name or "").strip().partition(" ")
    return {"first_name": first or None, "last_name": last.strip() or None,
            "company": b.company_name, "email": b.email, "phone": b.phone,
            "notes": SOURCE_DETAIL}


def linked_contact(db: Session, b: WholesaleBuyer) -> Optional[OrgContact]:
    """The buyer's contact, only if it is in the buyer's own organization."""
    if not b.org_contact_id:
        return None
    return (db.query(OrgContact).filter(OrgContact.id == b.org_contact_id,
                                        OrgContact.organization_id == b.organization_id).first())


def _org(db, org_id):
    from app.models.models import Organization
    return db.query(Organization).filter(Organization.id == org_id).one()


def link_buyer(db: Session, b: WholesaleBuyer, user=None) -> str:
    """Link one buyer. COMMITS (intake commits as it goes). Returns the outcome:
    already_linked | skipped_test | skipped_no_channel | linked_new |
    linked_existing | linked_possible | error."""
    if linked_contact(db, b) is not None:
        return "already_linked"
    if b.is_test:
        return "skipped_test"
    if not (b.email or b.phone):
        return "skipped_no_channel"
    buyer_id, org_id = b.id, b.organization_id
    try:
        from app.services.intake import capture as CAP
        res = CAP.capture_one(db, _org(db, org_id), _record(b), source=SOURCE,
                              source_detail=SOURCE_DETAIL, list_name=LIST_NAME,
                              classification="partner", actor_label="Cash buyer",
                              external=False, explicit=True, user=user)
    except Exception as exc:  # noqa: BLE001 - the buyer stays exactly as it was
        db.rollback()
        log.exception("buyer contact link failed: buyer=%s", buyer_id)
        svc.log_event(db, org_id, "buyer.contact_link_error",
                      actor_type=ACTOR_USER if user else ACTOR_SYSTEM,
                      actor_user_id=getattr(user, "id", None),
                      summary="Buyer kept unlinked from the contact database (intake error)",
                      details={"buyer_id": buyer_id, "error": type(exc).__name__})
        db.commit()
        return "error"
    b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == buyer_id,
                                        WholesaleBuyer.organization_id == org_id).one()
    if not res.contact_id:
        return "error"
    b.org_contact_id = res.contact_id
    outcome = {"existing": "linked_existing", "possible": "linked_possible",
               "blocked_existing": "linked_existing"}.get(res.match, "linked_new")
    svc.log_event(db, org_id, "buyer.contact_linked",
                  actor_type=ACTOR_USER if user else ACTOR_SYSTEM,
                  actor_user_id=getattr(user, "id", None),
                  summary="Buyer linked to the contact database: %s"
                          % (b.company_name or b.contact_name or b.id),
                  details={"buyer_id": b.id, "org_contact_id": res.contact_id,
                           "match": res.match, "intake_batch_id": res.batch_id})
    db.commit()
    return outcome


def link_many(db: Session, org_id: str, buyers: List[WholesaleBuyer], user=None) -> Dict[str, int]:
    """Link a batch of buyers (a buyer-list import) as ONE intake batch, so the
    whole list goes through one matcher. Same outcomes as `link_buyer`."""
    counts: Dict[str, int] = {}
    todo = []
    for b in buyers:
        if linked_contact(db, b) is not None:
            key = "already_linked"
        elif b.is_test:
            key = "skipped_test"
        elif not (b.email or b.phone):
            key = "skipped_no_channel"
        else:
            todo.append(b)
            continue
        counts[key] = counts.get(key, 0) + 1
    if not todo:
        return counts
    ids = [b.id for b in todo]
    try:
        from app.services.intake import capture as CAP
        res = CAP.capture_many(db, _org(db, org_id), [_record(b) for b in todo], source=SOURCE,
                               source_detail=SOURCE_DETAIL, list_name=LIST_NAME,
                               classification="partner", actor_label="Cash buyers", user=user)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log.exception("buyer contact link (batch) failed: org=%s", org_id)
        svc.log_event(db, org_id, "buyer.contact_link_error",
                      actor_type=ACTOR_USER if user else ACTOR_SYSTEM,
                      actor_user_id=getattr(user, "id", None),
                      summary="%d buyer(s) kept unlinked from the contact database (intake error)" % len(ids),
                      details={"buyer_ids": ids[:50], "error": type(exc).__name__})
        db.commit()
        counts["error"] = counts.get("error", 0) + len(ids)
        return counts
    fresh = {b.id: b for b in db.query(WholesaleBuyer).filter(
        WholesaleBuyer.organization_id == org_id, WholesaleBuyer.id.in_(ids)).all()}
    for row in res.rows:
        b = fresh.get(ids[row.index]) if 0 <= row.index < len(ids) else None
        if b is None or not row.contact_id:
            counts["error"] = counts.get("error", 0) + 1
            continue
        b.org_contact_id = row.contact_id
        key = {"existing": "linked_existing", "possible": "linked_possible",
               "blocked_existing": "linked_existing", "duplicate": "linked_existing"}.get(row.match, "linked_new")
        counts[key] = counts.get(key, 0) + 1
    svc.log_event(db, org_id, "buyer.contacts_linked",
                  actor_type=ACTOR_USER if user else ACTOR_SYSTEM,
                  actor_user_id=getattr(user, "id", None),
                  summary="%d buyer(s) linked to the contact database" % sum(
                      v for k, v in counts.items() if k.startswith("linked")),
                  details={"counts": counts, "intake_batch_id": res.batch_id})
    db.commit()
    return counts


def link_all(db: Session, org_id: str, user=None, *, dry_run: bool = False) -> Dict[str, Any]:
    """Link every unlinked buyer of ONE organization. Idempotent."""
    buyers = (db.query(WholesaleBuyer).filter(WholesaleBuyer.organization_id == org_id)
              .order_by(WholesaleBuyer.created_at).all())
    counts: Dict[str, int] = {}
    would: List[str] = []
    if not dry_run:
        counts = link_many(db, org_id, buyers, user)
        return {"buyers": len(buyers), "dry_run": False, "counts": counts, "would_link": None}
    for b in buyers:
        key = ("already_linked" if linked_contact(db, b) is not None else
               "skipped_test" if b.is_test else
               "skipped_no_channel" if not (b.email or b.phone) else "would_link")
        if key == "would_link":
            would.append(b.id)
        counts[key] = counts.get(key, 0) + 1
    return {"buyers": len(buyers), "dry_run": dry_run, "counts": counts,
            "would_link": would if dry_run else None}


def write_through(db: Session, b: WholesaleBuyer, changed: Dict[str, Any], user) -> List[str]:
    """Mirror an operator's correction of the buyer's identity onto the linked
    contact (marked as a manual edit, so a later import never reverts it)."""
    contact = linked_contact(db, b)
    if contact is None:
        return []
    edits: Dict[str, Any] = {}
    if "contact_name" in changed:
        first, _, last = (changed["contact_name"] or "").strip().partition(" ")
        edits["first_name"], edits["last_name"] = first or None, last.strip() or None
    if "company_name" in changed:
        edits["company"] = changed["company_name"] or None
    if "email" in changed:
        edits["email"] = (changed["email"] or "").strip().lower() or None
    if "phone" in changed:
        from app.services.wholesale_sms import normalize_e164
        edits["phone"] = normalize_e164(changed["phone"]) if changed["phone"] else None
    if not edits:
        return []
    from app.services.intake.contacts import apply_manual_edit_to_contact
    return apply_manual_edit_to_contact(
        db, b.organization_id, contact, edits, actor_id=getattr(user, "id", None),
        actor_name=getattr(user, "full_name", None), source="wholesale_buyer_edit",
        subject={"buyer_id": b.id})
