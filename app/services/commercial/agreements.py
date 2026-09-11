"""THE AGREEMENT ITSELF: create it incomplete, and keep it honest.

WHAT THIS MODULE IS FOR
-----------------------
A customer whose commercial structure is still being negotiated has to be
creatable TODAY, with the parts that are known recorded as known and the parts
that are not recorded as not known. Everything else in this package hangs off
that: the questions ask for what is missing, the blocking says what the missing
parts cost, and onboarding carries on regardless.

STATUS IS DERIVED, NOT TYPED
----------------------------
Nobody sets an agreement to TERMS_REQUIRED. An agreement is in TERMS_REQUIRED
because a term that activation needs has not been answered, and it leaves that
state when the last one is answered. A status somebody can type is a status
that will eventually disagree with the terms underneath it.

The three human decisions — APPROVE, ACTIVATE, SUSPEND/END — are the exception,
and each is an explicit authorised act with its own audit entry, because each
means something different:

  APPROVED   an authorised approver accepts these terms.
  ACTIVE     the arrangement is in force. Separate from approval on purpose:
             terms can be agreed in March for an April start.
  SUSPENDED  in force but halted. Settlement stops; nothing else does.
  ENDED      finished. History stays readable forever.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.commercial_models import (
    AG_ACTIVE, AG_APPROVED, AG_DRAFT, AG_ENDED, AG_READY_FOR_APPROVAL,
    AG_SUSPENDED, AG_TERMS_REQUIRED, AGREEMENT_STATUS_LABELS,
    AGREEMENT_TYPE_LABELS, AGREEMENT_TYPES, CommercialAgreement,
    CommercialAllocation, CommercialParty, PARTY_TYPES, SHARE_BEARING_TYPES,
)
from app.models.models import User
from app.services.commercial import audit as caudit
from app.services.commercial import authority as auth
from app.services.commercial import revenue_share as rs
from app.services.commercial import terms as t


def get_or_404(db: Session, agreement_id: str) -> CommercialAgreement:
    row = (db.query(CommercialAgreement)
           .filter(CommercialAgreement.id == agreement_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="Agreement not found.")
    return row


def for_organization(db: Session, organization_id: str) -> List[CommercialAgreement]:
    return (db.query(CommercialAgreement)
            .filter(CommercialAgreement.organization_id == organization_id)
            .order_by(CommercialAgreement.created_at.desc())
            .all())


def current_for_organization(db: Session,
                             organization_id: str) -> Optional[CommercialAgreement]:
    """The agreement that governs this customer right now.

    An ENDED agreement never governs, and among the rest the most recently
    created wins — a replacement term sheet supersedes the one it replaced. The
    old one stays readable; it just stops being the answer to "what are we on".
    """
    rows = [r for r in for_organization(db, organization_id)
            if r.status != AG_ENDED]
    return rows[0] if rows else None


def for_opportunity(db: Session, opportunity_id: str) -> List[CommercialAgreement]:
    return (db.query(CommercialAgreement)
            .filter(CommercialAgreement.opportunity_id == opportunity_id)
            .order_by(CommercialAgreement.created_at.desc())
            .all())


# ════════════════════════════════════════════════════════════════════════════
# CREATE
# ════════════════════════════════════════════════════════════════════════════


def create(db: Session, actor: User, *,
           platform_id: str,
           agreement_type: str,
           brand_sales_org_id: Optional[str] = None,
           organization_id: Optional[str] = None,
           opportunity_id: Optional[str] = None,
           implementation_id: Optional[str] = None,
           name: Optional[str] = None,
           reference: Optional[str] = None,
           currency: str = "usd",
           effective_date: Optional[date] = None,
           notes: Optional[str] = None,
           references_t2_subscription: bool = False,
           t2_note: Optional[str] = None,
           as_draft: bool = False) -> CommercialAgreement:
    """Create an arrangement whose terms may be entirely unknown.

    Nothing here requires a price, a percentage, a party or a date. That is the
    point: the alternative is that a customer cannot exist in the system until
    a negotiation finishes, and the work of onboarding them cannot start.
    """
    if agreement_type not in AGREEMENT_TYPES:
        raise HTTPException(status_code=400,
                            detail="Unknown commercial model '%s'." % agreement_type)

    row = CommercialAgreement(
        platform_id=platform_id,
        brand_sales_org_id=brand_sales_org_id,
        organization_id=organization_id,
        opportunity_id=opportunity_id,
        implementation_id=implementation_id,
        agreement_type=agreement_type,
        status=AG_DRAFT,
        name=(name or None),
        reference=(reference or None),
        currency=(currency or "usd").lower(),
        effective_date=effective_date,
        notes=(notes or None),
        references_t2_subscription=bool(references_t2_subscription),
        t2_note=(t2_note or None),
        created_by_user_id=getattr(actor, "id", None),
        version=1,
    )
    db.add(row)
    db.flush()

    caudit.record(db, row, actor, caudit.A_AGREEMENT_CREATED,
                  after={"agreement_type": agreement_type,
                         "status": row.status,
                         "organization_id": organization_id,
                         "opportunity_id": opportunity_id},
                  details={"name": row.name, "currency": row.currency})

    if not as_draft:
        refresh_status(db, row, actor)

    db.flush()
    return row


# ════════════════════════════════════════════════════════════════════════════
# DERIVED STATUS
# ════════════════════════════════════════════════════════════════════════════

_DERIVED_STATUSES = (AG_DRAFT, AG_TERMS_REQUIRED, AG_READY_FOR_APPROVAL)


def activation_blockers(db: Session,
                        agreement: CommercialAgreement) -> List[str]:
    """Everything except the approval itself.

    Approval is excluded deliberately: an approver has to be able to see an
    agreement that is ready and approve it, and "not approved yet" as a blocker
    on approval would be a loop.
    """
    return [r for r in t._activation_reasons(db, agreement)
            if r != "The agreement has not been approved."]


def refresh_status(db: Session, agreement: CommercialAgreement,
                   actor: Optional[User] = None) -> CommercialAgreement:
    """Move the agreement between the three DERIVED states. Never past them.

    An APPROVED, ACTIVE, SUSPENDED or ENDED agreement is not touched here — a
    term edited after approval does not silently un-approve the deal, it shows
    up as an outstanding item on a live agreement and a human decides what that
    means.
    """
    if agreement.status not in _DERIVED_STATUSES:
        return agreement

    before = agreement.status
    ready = not activation_blockers(db, agreement)
    agreement.status = AG_READY_FOR_APPROVAL if ready else AG_TERMS_REQUIRED

    if agreement.status != before:
        caudit.record(db, agreement, actor, caudit.A_STATUS_CHANGED,
                      before={"status": before},
                      after={"status": agreement.status},
                      details={"derived": True})
    db.flush()
    return agreement


# ════════════════════════════════════════════════════════════════════════════
# AMENDMENTS
# ════════════════════════════════════════════════════════════════════════════

_EDITABLE_FIELDS = ("name", "reference", "notes", "document_reference",
                    "document_note", "t2_note")


def update(db: Session, agreement: CommercialAgreement, actor: User, *,
           expected_version: Optional[int] = None,
           **fields) -> CommercialAgreement:
    """Change the descriptive parts of an agreement.

    Concurrency is checked against the agreement's own version, which every
    term write bumps too — so a stale editor who reloaded before the last term
    was answered is stopped here rather than overwriting the sheet.
    """
    if expected_version is not None and int(agreement.version or 1) != int(expected_version):
        raise HTTPException(
            status_code=409,
            detail="This agreement was changed by somebody else while you were "
                   "editing it. Reload it and try again.")

    before: Dict[str, Any] = {}
    after: Dict[str, Any] = {}

    for key in _EDITABLE_FIELDS:
        if key not in fields:
            continue
        new = fields[key]
        new = (new.strip() or None) if isinstance(new, str) else new
        if getattr(agreement, key) != new:
            before[key] = getattr(agreement, key)
            after[key] = new
            setattr(agreement, key, new)

    if "currency" in fields and fields["currency"]:
        new_cur = str(fields["currency"]).lower()
        if new_cur != agreement.currency:
            before["currency"] = agreement.currency
            after["currency"] = new_cur
            agreement.currency = new_cur

    if "effective_date" in fields:
        new_eff = fields["effective_date"]
        if new_eff != agreement.effective_date:
            before["effective_date"] = agreement.effective_date
            after["effective_date"] = new_eff
            agreement.effective_date = new_eff

    if "end_date" in fields:
        new_end = fields["end_date"]
        if new_end != agreement.end_date:
            before["end_date"] = agreement.end_date
            after["end_date"] = new_end
            agreement.end_date = new_end

    if "references_t2_subscription" in fields:
        new_t2 = bool(fields["references_t2_subscription"])
        if new_t2 != bool(agreement.references_t2_subscription):
            before["references_t2_subscription"] = agreement.references_t2_subscription
            after["references_t2_subscription"] = new_t2
            agreement.references_t2_subscription = new_t2

    if not after:
        return agreement

    agreement.version = int(agreement.version or 1) + 1
    caudit.record(db, agreement, actor, caudit.A_AGREEMENT_UPDATED,
                  before=before, after=after)
    refresh_status(db, agreement, actor)
    db.flush()
    return agreement


def set_type(db: Session, agreement: CommercialAgreement, actor: User,
             agreement_type: str) -> CommercialAgreement:
    """Change the SHAPE of the arrangement.

    Answers already given are kept, not deleted. A term that no longer applies
    to the new type stops being asked and stops counting toward activation, but
    it stays in the record — somebody answered it, and a type corrected from
    hybrid to revenue-share should not silently destroy the fee they stated.
    """
    if agreement_type not in AGREEMENT_TYPES:
        raise HTTPException(status_code=400,
                            detail="Unknown commercial model '%s'." % agreement_type)
    if agreement_type == agreement.agreement_type:
        return agreement
    if agreement.status in (AG_ACTIVE, AG_SUSPENDED, AG_ENDED):
        raise HTTPException(
            status_code=409,
            detail="The commercial model of an agreement that is already in "
                   "force cannot be changed. End it and record the new "
                   "arrangement, so the history of what was in force stays true.")

    before = agreement.agreement_type
    agreement.agreement_type = agreement_type
    agreement.version = int(agreement.version or 1) + 1
    caudit.record(db, agreement, actor, caudit.A_TYPE_CHANGED,
                  before={"agreement_type": before},
                  after={"agreement_type": agreement_type})
    refresh_status(db, agreement, actor)
    db.flush()
    return agreement


def bind_to_customer(db: Session, agreement: CommercialAgreement, actor: User, *,
                     organization_id: str,
                     implementation_id: Optional[str] = None) -> CommercialAgreement:
    """Attach an agreement negotiated pre-sale to the tenant it now governs.

    One-way. An agreement already bound to a different organization is refused
    rather than re-pointed: moving a commercial arrangement between tenants is
    not an edit, it is two different deals.
    """
    if agreement.organization_id and agreement.organization_id != organization_id:
        raise HTTPException(
            status_code=409,
            detail="This agreement already belongs to another customer.")

    before = {"organization_id": agreement.organization_id,
              "implementation_id": agreement.implementation_id}
    agreement.organization_id = organization_id
    if implementation_id:
        agreement.implementation_id = implementation_id
    agreement.version = int(agreement.version or 1) + 1
    caudit.record(db, agreement, actor, caudit.A_AGREEMENT_UPDATED,
                  before=before,
                  after={"organization_id": agreement.organization_id,
                         "implementation_id": agreement.implementation_id},
                  details={"bound_to_customer": True})
    db.flush()
    return agreement


# ════════════════════════════════════════════════════════════════════════════
# THE THREE HUMAN DECISIONS
# ════════════════════════════════════════════════════════════════════════════


def approve(db: Session, agreement: CommercialAgreement, actor: User,
            note: Optional[str] = None) -> CommercialAgreement:
    if agreement.status in (AG_ACTIVE, AG_SUSPENDED):
        raise HTTPException(status_code=409,
                            detail="This agreement is already in force.")
    if agreement.status == AG_ENDED:
        raise HTTPException(status_code=409,
                            detail="This agreement has ended.")

    blockers = activation_blockers(db, agreement)
    if blockers:
        raise HTTPException(
            status_code=409,
            detail="This agreement cannot be approved yet: " + " ".join(blockers))

    before = {"status": agreement.status}
    agreement.status = AG_APPROVED
    agreement.approved_by_user_id = getattr(actor, "id", None)
    agreement.approved_at = datetime.utcnow()
    agreement.approval_note = (note or None)
    agreement.version = int(agreement.version or 1) + 1

    caudit.record(db, agreement, actor, caudit.A_APPROVED,
                  before=before, after={"status": AG_APPROVED},
                  note=note)
    db.flush()
    return agreement


def activate(db: Session, agreement: CommercialAgreement, actor: User,
             note: Optional[str] = None) -> CommercialAgreement:
    """Put an approved arrangement in force.

    Deliberately separate from approval, and deliberately re-checks the
    blockers: an agreement approved last week whose parties have changed since
    is not activatable on the strength of the old approval.
    """
    if agreement.status == AG_ACTIVE:
        return agreement
    if agreement.status != AG_APPROVED:
        raise HTTPException(
            status_code=409,
            detail="Only an approved agreement can be activated. This one is %s."
                   % AGREEMENT_STATUS_LABELS.get(agreement.status, agreement.status))

    blockers = activation_blockers(db, agreement)
    if blockers:
        raise HTTPException(
            status_code=409,
            detail="This agreement cannot be activated: " + " ".join(blockers))

    before = {"status": agreement.status}
    agreement.status = AG_ACTIVE
    agreement.activated_by_user_id = getattr(actor, "id", None)
    agreement.activated_at = datetime.utcnow()
    agreement.version = int(agreement.version or 1) + 1

    caudit.record(db, agreement, actor, caudit.A_ACTIVATED,
                  before=before, after={"status": AG_ACTIVE},
                  details={"effective_date": str(agreement.effective_date)
                           if agreement.effective_date else None},
                  note=note)
    db.flush()
    return agreement


def suspend(db: Session, agreement: CommercialAgreement, actor: User,
            reason: str) -> CommercialAgreement:
    if agreement.status != AG_ACTIVE:
        raise HTTPException(status_code=409,
                            detail="Only an active agreement can be suspended.")
    if not (reason or "").strip():
        raise HTTPException(status_code=400,
                            detail="Suspending an agreement requires a reason.")

    before = {"status": agreement.status}
    agreement.status = AG_SUSPENDED
    agreement.suspended_by_user_id = getattr(actor, "id", None)
    agreement.suspended_at = datetime.utcnow()
    agreement.suspension_reason = reason.strip()
    agreement.version = int(agreement.version or 1) + 1

    caudit.record(db, agreement, actor, caudit.A_SUSPENDED,
                  before=before, after={"status": AG_SUSPENDED},
                  note=reason.strip())
    db.flush()
    return agreement


def resume(db: Session, agreement: CommercialAgreement, actor: User,
           note: Optional[str] = None) -> CommercialAgreement:
    if agreement.status != AG_SUSPENDED:
        raise HTTPException(status_code=409,
                            detail="Only a suspended agreement can be resumed.")
    before = {"status": agreement.status,
              "suspension_reason": agreement.suspension_reason}
    agreement.status = AG_ACTIVE
    agreement.suspended_at = None
    agreement.suspension_reason = None
    agreement.version = int(agreement.version or 1) + 1
    caudit.record(db, agreement, actor, caudit.A_STATUS_CHANGED,
                  before=before, after={"status": AG_ACTIVE}, note=note)
    db.flush()
    return agreement


def end(db: Session, agreement: CommercialAgreement, actor: User,
        reason: str, end_date: Optional[date] = None) -> CommercialAgreement:
    if agreement.status == AG_ENDED:
        return agreement
    if not (reason or "").strip():
        raise HTTPException(status_code=400,
                            detail="Ending an agreement requires a reason.")

    before = {"status": agreement.status, "end_date": agreement.end_date}
    agreement.status = AG_ENDED
    agreement.ended_by_user_id = getattr(actor, "id", None)
    agreement.ended_at = datetime.utcnow()
    agreement.end_reason = reason.strip()
    if end_date:
        agreement.end_date = end_date
    agreement.version = int(agreement.version or 1) + 1

    caudit.record(db, agreement, actor, caudit.A_ENDED,
                  before=before,
                  after={"status": AG_ENDED, "end_date": agreement.end_date},
                  note=reason.strip())
    db.flush()
    return agreement


# ════════════════════════════════════════════════════════════════════════════
# PARTIES AND ALLOCATIONS
# ════════════════════════════════════════════════════════════════════════════


def _party_or_404(db: Session, agreement: CommercialAgreement,
                  party_id: str) -> CommercialParty:
    row = (db.query(CommercialParty)
           .filter(CommercialParty.id == party_id,
                   CommercialParty.agreement_id == agreement.id)
           .first())
    if row is None:
        raise HTTPException(status_code=404,
                            detail="That party is not on this agreement.")
    return row


def add_party(db: Session, agreement: CommercialAgreement, actor: User, *,
              party_key: str, display_name: str, party_type: str,
              organization_id: Optional[str] = None,
              brand_sales_org_id: Optional[str] = None,
              external_reference: Optional[str] = None,
              is_payee: bool = True,
              note: Optional[str] = None) -> CommercialParty:
    if party_type not in PARTY_TYPES:
        raise HTTPException(status_code=400,
                            detail="Unknown party type '%s'." % party_type)
    key = (party_key or "").strip().lower()
    if not key:
        raise HTTPException(status_code=400, detail="A party needs a key.")
    if not (display_name or "").strip():
        raise HTTPException(status_code=400, detail="A party needs a name.")

    clash = (db.query(CommercialParty)
             .filter(CommercialParty.agreement_id == agreement.id,
                     CommercialParty.party_key == key).first())
    if clash is not None:
        raise HTTPException(status_code=409,
                            detail="This agreement already has a party '%s'." % key)

    last = (db.query(CommercialParty)
            .filter(CommercialParty.agreement_id == agreement.id)
            .order_by(CommercialParty.position.desc()).first())

    row = CommercialParty(
        agreement_id=agreement.id, party_key=key,
        display_name=display_name.strip(), party_type=party_type,
        organization_id=organization_id,
        brand_sales_org_id=brand_sales_org_id,
        external_reference=(external_reference or None),
        is_payee=bool(is_payee),
        position=((last.position + 1) if last else 0),
        note=(note or None),
    )
    db.add(row)
    agreement.version = int(agreement.version or 1) + 1
    db.flush()

    caudit.record(db, agreement, actor, caudit.A_PARTY_ADDED,
                  target_type="commercial_party", target_id=row.id,
                  after={"party_key": key, "display_name": row.display_name,
                         "party_type": party_type, "is_payee": row.is_payee})
    refresh_status(db, agreement, actor)
    return row


def update_party(db: Session, agreement: CommercialAgreement, actor: User,
                 party_id: str, **fields) -> CommercialParty:
    row = _party_or_404(db, agreement, party_id)
    before, after = {}, {}
    for key in ("display_name", "party_type", "organization_id",
                "brand_sales_org_id", "external_reference", "note"):
        if key not in fields:
            continue
        new = fields[key]
        new = (new.strip() or None) if isinstance(new, str) else new
        if key == "party_type" and new not in PARTY_TYPES:
            raise HTTPException(status_code=400,
                                detail="Unknown party type '%s'." % new)
        if key == "display_name" and not new:
            raise HTTPException(status_code=400, detail="A party needs a name.")
        if getattr(row, key) != new:
            before[key] = getattr(row, key)
            after[key] = new
            setattr(row, key, new)
    if "is_payee" in fields and bool(fields["is_payee"]) != bool(row.is_payee):
        before["is_payee"] = row.is_payee
        after["is_payee"] = bool(fields["is_payee"])
        row.is_payee = bool(fields["is_payee"])

    if not after:
        return row

    agreement.version = int(agreement.version or 1) + 1
    caudit.record(db, agreement, actor, caudit.A_PARTY_CHANGED,
                  target_type="commercial_party", target_id=row.id,
                  before=before, after=after)
    refresh_status(db, agreement, actor)
    db.flush()
    return row


def remove_party(db: Session, agreement: CommercialAgreement, actor: User,
                 party_id: str) -> None:
    """Remove a party, and its allocation with it.

    Refused once the agreement is in force. A live arrangement's parties are
    what its settlements were calculated against; removing one retroactively
    would make past statements unreadable.
    """
    if agreement.status in (AG_ACTIVE, AG_SUSPENDED, AG_ENDED):
        raise HTTPException(
            status_code=409,
            detail="A party cannot be removed from an agreement that is "
                   "already in force.")
    row = _party_or_404(db, agreement, party_id)
    before = {"party_key": row.party_key, "display_name": row.display_name}

    (db.query(CommercialAllocation)
     .filter(CommercialAllocation.agreement_id == agreement.id,
             CommercialAllocation.party_id == row.id)
     .delete(synchronize_session=False))
    db.delete(row)
    agreement.version = int(agreement.version or 1) + 1

    caudit.record(db, agreement, actor, caudit.A_PARTY_REMOVED,
                  target_type="commercial_party", target_id=party_id,
                  before=before)
    db.flush()
    refresh_status(db, agreement, actor)


def set_allocation(db: Session, agreement: CommercialAgreement, actor: User,
                   party_id: str, *,
                   percent: Any = None,
                   fixed_amount_cents: Optional[int] = None,
                   tier_json: Any = None,
                   note: Optional[str] = None,
                   clear_percent: bool = False) -> CommercialAllocation:
    """State (or unstate) one party's share.

    `clear_percent` exists because setting a percentage back to UNKNOWN is a
    real act — a figure was recorded in error and the truth is that nobody has
    agreed one. Passing percent=None without it leaves the stored percentage
    alone, so a caller updating only the note cannot erase the split by
    omission.
    """
    party = _party_or_404(db, agreement, party_id)

    row = (db.query(CommercialAllocation)
           .filter(CommercialAllocation.agreement_id == agreement.id,
                   CommercialAllocation.party_id == party.id).first())
    if row is None:
        row = CommercialAllocation(agreement_id=agreement.id, party_id=party.id,
                                   position=int(party.position or 0))
        db.add(row)
        db.flush()

    before = {"percent": (str(row.percent) if row.percent is not None else None),
              "fixed_amount_cents": row.fixed_amount_cents,
              "tier_json": row.tier_json}

    if clear_percent:
        row.percent = None
    elif percent is not None:
        try:
            value = float(percent)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400,
                                detail="A share percentage must be a number.")
        if value < 0 or value > 100:
            raise HTTPException(status_code=400,
                                detail="A share percentage must be between 0 and 100.")
        row.percent = value

    if fixed_amount_cents is not None:
        try:
            cents = int(fixed_amount_cents)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400,
                                detail="A fixed amount must be a whole number of cents.")
        if cents < 0:
            raise HTTPException(status_code=400,
                                detail="A fixed amount cannot be negative.")
        row.fixed_amount_cents = cents

    if tier_json is not None:
        row.tier_json = tier_json
    if note is not None:
        row.note = note.strip() or None

    agreement.version = int(agreement.version or 1) + 1

    caudit.record(db, agreement, actor, caudit.A_ALLOCATION_CHANGED,
                  target_type="commercial_allocation", target_id=row.id,
                  before=before,
                  after={"percent": (str(row.percent)
                                     if row.percent is not None else None),
                         "fixed_amount_cents": row.fixed_amount_cents,
                         "tier_json": row.tier_json},
                  details={"party_key": party.party_key,
                           "display_name": party.display_name})
    db.flush()
    refresh_status(db, agreement, actor)
    return row


def link_document(db: Session, agreement: CommercialAgreement, actor: User, *,
                  file_id: Optional[str] = None,
                  reference: Optional[str] = None,
                  note: Optional[str] = None) -> CommercialAgreement:
    """Point the agreement at the document that states it.

    A DOCUMENT DOES NOT APPROVE ANYTHING. Linking a signed PDF records where the
    paper is; the agreement's status still only moves when an authorised person
    moves it. The two are separated because "a file exists" is the single most
    tempting proxy for "this was agreed", and it is not one.
    """
    before = {"document_file_id": agreement.document_file_id,
              "document_reference": agreement.document_reference}
    if file_id is not None:
        agreement.document_file_id = file_id or None
    if reference is not None:
        agreement.document_reference = reference.strip() or None
    if note is not None:
        agreement.document_note = note.strip() or None
    agreement.version = int(agreement.version or 1) + 1

    caudit.record(db, agreement, actor, caudit.A_DOCUMENT_LINKED,
                  before=before,
                  after={"document_file_id": agreement.document_file_id,
                         "document_reference": agreement.document_reference},
                  details={"approves_nothing": True})
    db.flush()
    return agreement


# ════════════════════════════════════════════════════════════════════════════
# VIEWS
# ════════════════════════════════════════════════════════════════════════════
#
# Two, and they are not the same payload with a flag. The customer's view
# contains no percentages, no party economics, no internal notes and no
# vocabulary from this codebase; the internal view contains everything,
# including WHY each gated action is unavailable.


def _identity(agreement: CommercialAgreement) -> Dict[str, Any]:
    return {
        "id": agreement.id,
        "agreement_type": agreement.agreement_type,
        "agreement_type_label": AGREEMENT_TYPE_LABELS.get(
            agreement.agreement_type, agreement.agreement_type),
        "status": agreement.status,
        "status_label": AGREEMENT_STATUS_LABELS.get(agreement.status,
                                                    agreement.status),
        "name": agreement.name,
        "reference": agreement.reference,
        "currency": agreement.currency,
        "effective_date": agreement.effective_date,
        "end_date": agreement.end_date,
        "version": int(agreement.version or 1),
    }


def internal_view(db: Session, agreement: CommercialAgreement) -> Dict[str, Any]:
    rule = t.value(db, agreement, "allocation_rule")
    verdict = rs.validate(db, agreement, rule)
    block = t.blocking(db, agreement)
    completeness = t.completeness(db, agreement)

    return {
        **_identity(agreement),
        "platform_id": agreement.platform_id,
        "brand_sales_org_id": agreement.brand_sales_org_id,
        "organization_id": agreement.organization_id,
        "opportunity_id": agreement.opportunity_id,
        "implementation_id": agreement.implementation_id,
        "notes": agreement.notes,
        "references_t2_subscription": bool(agreement.references_t2_subscription),
        "t2_note": agreement.t2_note,
        "document": {
            "file_id": agreement.document_file_id,
            "reference": agreement.document_reference,
            "note": agreement.document_note,
            "approves_nothing": True,
        },
        "terms": t.ordered_terms(db, agreement),
        "missing_terms": [
            {"key": r["key"], "label": r["label"], "audience": r["audience"]}
            for r in t.missing_required(db, agreement)
        ],
        "completeness": completeness,
        "allocation": {
            "rule": rule,
            "valid": verdict["valid"],
            "reasons": verdict["reasons"],
            "total_percent": (str(verdict["total_percent"])
                              if verdict["total_percent"] is not None else None),
            "residual_percent": (str(verdict["residual_percent"])
                                 if verdict["residual_percent"] is not None else None),
            "parties": [
                {**{k: v for k, v in row.items() if k != "percent"},
                 "percent": (str(row["percent"]) if row["percent"] is not None
                             else None),
                 "percent_known": row["percent"] is not None}
                for row in verdict["parties"]
            ],
        },
        "blocking": block,
        "approval": {
            "approved_by_user_id": agreement.approved_by_user_id,
            "approved_at": agreement.approved_at,
            "note": agreement.approval_note,
            "activated_by_user_id": agreement.activated_by_user_id,
            "activated_at": agreement.activated_at,
            "suspended_at": agreement.suspended_at,
            "suspension_reason": agreement.suspension_reason,
            "ended_at": agreement.ended_at,
            "end_reason": agreement.end_reason,
        },
        "created_at": agreement.created_at,
        "updated_at": agreement.updated_at,
    }


# Plain-English status lines for the customer. The internal vocabulary —
# terms_required, ready_for_approval — is not a thing to put in front of the
# person whose company this is.
_CUSTOMER_STATUS_LINES = {
    AG_DRAFT: "We are still preparing your agreement.",
    AG_TERMS_REQUIRED: "Some terms still need to be completed.",
    AG_READY_FOR_APPROVAL: "Your terms are complete and with us for sign-off.",
    AG_APPROVED: "Your terms have been agreed.",
    AG_ACTIVE: "Your agreement is in place.",
    AG_SUSPENDED: "Your agreement is paused. Your account manager can explain "
                  "where things stand.",
    AG_ENDED: "This agreement has ended.",
}


def customer_view(db: Session, agreement: CommercialAgreement) -> Dict[str, Any]:
    """What the customer's own onboarding screen shows.

    No percentages. No party allocation. No internal notes, no blocking
    vocabulary, no enum names. Just: this is the kind of arrangement we have,
    here is where it stands, and here are the business questions still open.
    """
    rows = t.ordered_terms(db, agreement, audience="customer")
    open_questions = [r for r in rows
                      if r["state"] in ("required", "unknown")]
    answered = [r for r in rows if r["state"] == "answered"]

    return {
        **_identity(agreement),
        "headline": AGREEMENT_TYPE_LABELS.get(agreement.agreement_type,
                                              agreement.agreement_type),
        "status_line": _CUSTOMER_STATUS_LINES.get(
            agreement.status, "Your agreement is being prepared."),
        "questions": [
            {
                "key": r["key"], "label": r["label"],
                "description": r["description"], "help_text": r["help_text"],
                "kind": r["kind"], "allowed_values": r["allowed_values"],
                "validation": r["validation"],
                "value": r["value"], "value_label": r["value_label"],
                "answered": r["state"] == "answered",
                "note": r["note"],
                "revision": r["revision"],
            }
            for r in rows
        ],
        "open_question_count": len(open_questions),
        "answered_question_count": len(answered),
        "question_total": len(rows),
        "document": {
            "reference": agreement.document_reference,
            "has_document": bool(agreement.document_file_id
                                 or agreement.document_reference),
        },
    }
