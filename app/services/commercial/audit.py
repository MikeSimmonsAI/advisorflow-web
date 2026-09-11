"""Audit for commercial acts.

One helper, on top of the platform's existing `log_action`, so that every
commercial event lands in `audit_log_entries` beside every other control-plane
event rather than in a private log nobody queries.

WHY EVERY CALL PASSES commit=False
----------------------------------
An audit entry that survives a rolled-back change is a lie in the record, and
one that is written in its own transaction can do exactly that. These entries
are flushed inside the same transaction as the rows they describe; the caller
commits both or neither.

THE ACTIONS, SPELLED OUT
------------------------
Named constants rather than string literals at call sites, because the audit
screen filters on them and a typo produces an event nobody can find again.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.commercial_models import CommercialAgreement
from app.models.models import User
from app.routers.audit_log_router import log_action

# ── agreement lifecycle ─────────────────────────────────────────────────────
A_AGREEMENT_CREATED   = "commercial_agreement_created"
A_TYPE_CHANGED        = "commercial_agreement_type_changed"
A_STATUS_CHANGED      = "commercial_agreement_status_changed"
A_APPROVED            = "commercial_agreement_approved"
A_ACTIVATED           = "commercial_agreement_activated"
A_SUSPENDED           = "commercial_agreement_suspended"
A_ENDED               = "commercial_agreement_ended"
A_AGREEMENT_UPDATED   = "commercial_agreement_updated"
A_DOCUMENT_LINKED     = "commercial_agreement_document_linked"

# ── parties and allocations ─────────────────────────────────────────────────
A_PARTY_ADDED         = "commercial_party_added"
A_PARTY_CHANGED       = "commercial_party_changed"
A_PARTY_REMOVED       = "commercial_party_removed"
A_ALLOCATION_CHANGED  = "commercial_allocation_changed"

# ── terms and questions ─────────────────────────────────────────────────────
A_TERM_ANSWERED       = "commercial_term_answered"
A_TERM_CHANGED        = "commercial_term_changed"
A_TERM_CLEARED        = "commercial_term_cleared"
A_QUESTION_DEFINED    = "commercial_question_defined"

# ── onboarding overrides ────────────────────────────────────────────────────
A_MILESTONE_OVERRIDDEN = "onboarding_milestone_overridden"
A_MILESTONE_RESTORED   = "onboarding_milestone_override_removed"

# ── collections and settlement ──────────────────────────────────────────────
A_COLLECTION_RECORDED  = "commercial_collection_recorded"
A_COLLECTION_APPROVED  = "commercial_collection_approved"
A_COLLECTION_REJECTED  = "commercial_collection_rejected"
A_SETTLEMENT_CALCULATED = "commercial_settlement_calculated"
A_SETTLEMENT_BLOCKED    = "commercial_settlement_refused"
A_SETTLEMENT_APPROVED   = "commercial_settlement_approved"
A_SETTLEMENT_DISTRIBUTION_REFUSED = "commercial_settlement_distribution_refused"


def record(db: Session, agreement: Optional[CommercialAgreement],
           actor: Optional[User], action: str, *,
           target_type: str = "commercial_agreement",
           target_id: Optional[str] = None,
           organization_id: Optional[str] = None,
           platform_id: Optional[str] = None,
           brand_sales_org_id: Optional[str] = None,
           before: Any = None, after: Any = None,
           details: Any = None, note: Optional[str] = None) -> None:
    """Write one commercial audit entry inside the caller's transaction."""
    org_id = organization_id
    plat_id = platform_id
    brand_id = brand_sales_org_id
    tgt = target_id

    if agreement is not None:
        org_id = org_id or agreement.organization_id
        plat_id = plat_id or agreement.platform_id
        brand_id = brand_id or agreement.brand_sales_org_id
        tgt = tgt or agreement.id

    log_action(
        db, org_id, getattr(actor, "id", None),
        action=action,
        target_type=target_type,
        target_id=tgt or "unknown",
        platform_id=plat_id,
        brand_sales_org_id=brand_id,
        before=before, after=after, details=details, note=note,
        commit=False,
    )
