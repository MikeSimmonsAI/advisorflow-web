import logging
import os
import shutil
import tempfile
import json as _json
from fastapi import (
    APIRouter, Depends, UploadFile, File, Form, Query, HTTPException, Request, Response,
)
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta, time, timezone

from app.deps import get_db, require_tenant_user, require_tenant_or_observer
from app.limiter import limiter
from app.services.platform_owner import require_tenant_context
from app.models.models import User, Lead, Reply, ReplyClassification, CadenceState, BookingLink, EngagementTemperature, CRMContact, VoiceCall
from app.services.import_service import import_leads_from_excel
from app.services.import_permissions import require_import_stage, require_import_commit
from app.services.import_staging_service import stage_batch as _stage_batch
from app.services.import_commit_service import commit_batch as _commit_batch_svc
from app.models.import_models import (
    ImportBatch, ImportBatchStatus, ImportStagedRow,
    ImportRowReviewStatus, ImportDuplicateStatus, ImportValidationStatus,
)
from app.models.models import gen_uuid
from app.services.dedup_service import normalize_phone
from app.routers.audit_log_router import log_action
# THE ONE AUTHORIZED LEAD SCOPE. Every list, count, search, export and
# single-record fetch in this file goes through it, so the advisor boundary is
# stated once instead of re-derived per route.
from app.services import lead_scope
from app.services.lead_scope import (authorized_lead_query, load_lead_in_scope, assert_leads_in_scope, reject_ownership_fields)

router = APIRouter()


def _is_suppressed(db: Session, lead: Lead) -> bool:
    """Lazy import to avoid a circular import (compliance_service -> compliance_router -> ... )."""
    from app.services.compliance_service import is_phone_suppressed
    return is_phone_suppressed(db, lead.organization_id, lead.phone)


@router.post("/{lead_id}/not-duplicate")
def keep_lead_separate(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_context),
):
    """KEEP SEPARATE. This lead is its own person - resolve the flag, keep both.

    Until this existed the only endpoint that touched `is_duplicate` DELETED
    the row. A lead wrongly flagged - and the flag is applied inconsistently
    enough that "wrongly" is common - could be removed from the Duplicates tab
    only by destroying it. That is not a choice anyone should have to make
    about a real family's record.

    Nothing is deleted and nothing is merged. The flag is resolved, the
    resolution is stamped with who and when, and the row returns to normal
    outreach. `duplicate_of_lead_id` is deliberately KEPT: it is the evidence
    of what was matched, and a resolved pair should stay explainable.

    A resolved lead is not re-flagged. `duplicate_resolved_at` is what the
    importer and the maintenance passes check, so re-running an import over
    the same identifying data will not undo a human's decision. Materially
    changing the identifying data is a different lead and gets re-evaluated.
    """
    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    was = {
        "is_duplicate": bool(lead.is_duplicate),
        "status": lead.status,
        "duplicate_of_lead_id": getattr(lead, "duplicate_of_lead_id", None),
        "duplicate_reason": getattr(lead, "duplicate_reason", None),
    }

    lead.is_duplicate = False
    lead.duplicate_resolved_at = datetime.utcnow()
    lead.duplicate_resolved_by = current_user.id

    # A lead pushed to DNC by the duplicate-import bug comes back. A lead that
    # is DNC for a REAL reason - a STOP, a suppression, an admin decision -
    # stays exactly where it is: resolving a data-quality flag must never
    # silently re-open contact with someone who asked not to be contacted.
    restored_status = None
    if lead.status == "dnc" and was["is_duplicate"] and not _has_real_dnc_reason(db, lead):
        # An unclassified lead goes back to the review queue, NOT to "new".
        # `tier == "partial"` is truthy, so a naive check would have released
        # it straight into outreach unreviewed.
        lead.status = "new" if _has_real_tier(lead) else "needs_tier_review"
        restored_status = lead.status

    db.commit()

    log_action(
        db,
        organization_id=current_user.organization_id,
        actor_user_id=current_user.id,
        action="lead.duplicate_resolved_keep_separate",
        target_type="lead",
        target_id=lead.id,
        details={"before": was, "restored_status": restored_status,
                 "name": ((lead.first_name or "") + " " + (lead.last_name or "")).strip(),
                 "phone": lead.phone, "email": lead.email},
    )

    return {
        "lead_id": lead.id,
        "is_duplicate": False,
        "status": lead.status,
        "restored_status": restored_status,
        "resolved_at": lead.duplicate_resolved_at.isoformat(),
        "message": "Kept as a separate record. Nothing was deleted.",
    }


def _has_real_tier(lead: Lead) -> bool:
    """Has a human actually classified this lead?

    `partial` is the IMPORTER'S PLACEHOLDER, not a tier. It means the upload
    could not work out what kind of lead this is, so a person must. It is a
    non-empty string, which is the trap: a truthiness check reads it as "tier
    present" and would release thousands of unclassified leads straight into
    outreach - the exact opposite of what `needs_tier_review` is for.
    """
    tier = (str(lead.tier).strip().lower() if lead.tier else "")
    return bool(tier) and tier not in ("partial", "none", "unknown")


def _has_real_dnc_reason(db: Session, lead: Lead) -> bool:
    """Is this lead DNC for a reason OTHER than the duplicate-import bug?

    Checked before any repair restores a status. A STOP reply, a suppression
    entry an admin added, or a manual flag are all real and must survive.
    Fails CLOSED: anything unexpected counts as a real reason, because leaving
    a lead suppressed is recoverable and texting someone who opted out is not.
    """
    try:
        if getattr(lead, "manual_flag", None):
            return True
        # A DNC classification on any inbound reply is a legal opt-out.
        from app.models.models import Reply, ReplyClassification
        stopped = (db.query(Reply)
                   .filter(Reply.lead_id == lead.id,
                           Reply.classification == ReplyClassification.DNC)
                   .first())
        if stopped is not None:
            return True
        # The org-wide suppression list is the other source of truth.
        if _is_suppressed(db, lead):
            return True
    except Exception:
        return True
    return False


@router.get("/{lead_id}/duplicate-explain")
def explain_duplicate(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_context),
):
    """WHY is this lead flagged, and what did it match?

    Rows flagged before traceability existed carry no reason and, on the
    in-file path, no parent either - they say "duplicate" and nothing more.
    This reconstructs the answer live from the same two sources the dedup
    engine consults, so an old flag can still be explained rather than being
    an unaccountable mark on somebody's record.
    """
    from app.services.dedup_service import (normalize_phone, normalize_last_name,
                                            PLACEHOLDER_LAST_NAME)
    from app.models.models import ContactRegistry

    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    norm_phone = normalize_phone(lead.phone) if lead.phone else None
    norm_last = normalize_last_name(lead.last_name) if lead.last_name else None

    stored = {
        "is_duplicate": bool(lead.is_duplicate),
        "reason": getattr(lead, "duplicate_reason", None),
        "match_field": getattr(lead, "duplicate_match_field", None),
        "match_value": getattr(lead, "duplicate_match_value", None),
        "duplicate_of_lead_id": getattr(lead, "duplicate_of_lead_id", None),
        "resolved_at": (lead.duplicate_resolved_at.isoformat()
                        if getattr(lead, "duplicate_resolved_at", None) else None),
    }

    # What the registry holds for this phone, and which rule it would fire.
    registry = []
    if norm_phone:
        for e in (db.query(ContactRegistry)
                  .filter(ContactRegistry.organization_id == lead.organization_id,
                          ContactRegistry.normalized_phone == norm_phone).all()):
            is_placeholder = e.normalized_last_name == PLACEHOLDER_LAST_NAME
            registry.append({
                "registry_last_name": e.normalized_last_name,
                "is_placeholder_from_historical_sent_log": is_placeholder,
                "matches_this_lead": (e.normalized_last_name == norm_last) or is_placeholder,
                "first_seen_lead_id": e.first_seen_lead_id,
                "owning_user_id": e.owning_user_id,
            })

    # Other LEADS sharing the identifying data, so the UI can name the sibling.
    siblings = []
    if norm_phone:
        for other in (db.query(Lead)
                      .filter(Lead.organization_id == lead.organization_id,
                              Lead.id != lead.id).all()):
            if other.phone and normalize_phone(other.phone) == norm_phone:
                siblings.append({
                    "id": other.id,
                    "name": ((other.first_name or "") + " " + (other.last_name or "")).strip(),
                    "email": other.email, "status": other.status,
                    "is_duplicate": bool(other.is_duplicate),
                    "same_last_name": normalize_last_name(other.last_name or "") == norm_last,
                    "created_at": str(other.created_at or ""),
                })

    parent = None
    if stored["duplicate_of_lead_id"]:
        p = db.query(Lead).filter(Lead.id == stored["duplicate_of_lead_id"]).first()
        if p:
            parent = {"id": p.id,
                      "name": ((p.first_name or "") + " " + (p.last_name or "")).strip(),
                      "phone": p.phone, "email": p.email}

    return {
        "lead": {"id": lead.id,
                 "name": ((lead.first_name or "") + " " + (lead.last_name or "")).strip(),
                 "phone": lead.phone, "email": lead.email, "status": lead.status,
                 "normalized_phone": norm_phone, "normalized_last_name": norm_last},
        "stored_flag": stored,
        "registry_entries_for_this_phone": registry,
        "other_leads_sharing_this_phone": siblings,
        "parent_lead": parent,
    }


@router.post("/maintenance/duplicate-dnc-repair")
def repair_duplicate_dnc(
    apply: bool = Query(False, description="False (default) reports only."),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_context),
):
    """Undo the duplicate->DNC coupling on rows the importer already wrote.

    DRY RUN BY DEFAULT. `apply=false` counts and returns a sample, changes
    nothing. Nothing here deletes, merges, or clears a duplicate flag - it only
    lifts a `status = "dnc"` that the import bug applied for a bookkeeping
    reason, and only where no REAL suppression exists.

    A row is repaired only when ALL of these hold:
      - status == "dnc"
      - is_duplicate is true            (the bug's signature)
      - `_has_real_dnc_reason` is false (no STOP, no suppression, no manual flag)

    Everything else is left alone. The lead stays flagged as a duplicate - that
    is a separate, honest fact, and resolving it is a human decision made
    through /not-duplicate.
    """
    if current_user.role not in ("org_admin", "super_admin", "god_admin"):
        raise HTTPException(status_code=403, detail="Admin role required.")

    candidates = db.query(Lead).filter(
        Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db),
        Lead.status == "dnc",
        Lead.is_duplicate == True,
    ).all()

    repairable, protected = [], []
    for lead in candidates:
        (protected if _has_real_dnc_reason(db, lead) else repairable).append(lead)

    if apply:
        for lead in repairable:
            # Unclassified leads land in the review queue, not in outreach.
            lead.status = "new" if _has_real_tier(lead) else "needs_tier_review"
        db.commit()
        log_action(
            db,
            organization_id=current_user.organization_id,
            actor_user_id=current_user.id,
            action="lead.duplicate_dnc_repaired",
            target_type="organization",
            target_id=current_user.organization_id,
            details={"repaired": len(repairable), "protected": len(protected)},
        )

    def _brief(l):
        return {"id": l.id,
                "name": ((l.first_name or "") + " " + (l.last_name or "")).strip(),
                "phone": l.phone, "reason": getattr(l, "duplicate_reason", None)}

    # What this repair would actually add to SMS READY. A repaired lead only
    # becomes sendable if it has a REAL tier and a phone; the rest return to
    # the review queue, which is where they belong.
    to_outreach = [l for l in repairable if _has_real_tier(l) and l.phone]
    to_review = [l for l in repairable if l not in to_outreach]

    return {
        "dry_run": not apply,
        "dnc_and_duplicate": len(candidates),
        "repairable": len(repairable),
        "protected_real_dnc": len(protected),
        "would_become_sendable": len(to_outreach),
        "would_return_to_tier_review": len(to_review),
        "sample_repairable": [_brief(l) for l in repairable[:10]],
        "sample_protected": [_brief(l) for l in protected[:10]],
    }


@router.post("/maintenance/tier-status-repair")
def repair_stale_tier_review(
    apply: bool = Query(False, description="False (default) reports only."),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_context),
):
    """Release leads stuck in `needs_tier_review` that already HAVE a tier.

    DRY RUN BY DEFAULT.

    `needs_tier_review` means "somebody must classify this lead before we
    contact them". Once a tier is set that is answered, but nothing moved the
    status on, so the leads stayed parked - which is why SMS READY reads 0
    against ten thousand leads with phone numbers.

    Only rows whose question has actually been answered are released:
      - status == "needs_tier_review"
      - a tier is present and is not the "partial" placeholder
      - not flagged duplicate, not suppressed, has a phone

    A lead with no tier still needs a human. It is reported, not touched.
    """
    if current_user.role not in ("org_admin", "super_admin", "god_admin"):
        raise HTTPException(status_code=403, detail="Admin role required.")

    parked = db.query(Lead).filter(
        Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db),
        Lead.status == "needs_tier_review",
    ).all()

    releasable, no_tier, blocked = [], [], []
    for lead in parked:
        if not _has_real_tier(lead):
            no_tier.append(lead)
        elif lead.is_duplicate or not lead.phone or _has_real_dnc_reason(db, lead):
            blocked.append(lead)
        else:
            releasable.append(lead)

    if apply:
        for lead in releasable:
            lead.status = "new"
        db.commit()
        log_action(
            db,
            organization_id=current_user.organization_id,
            actor_user_id=current_user.id,
            action="lead.tier_status_repaired",
            target_type="organization",
            target_id=current_user.organization_id,
            details={"released": len(releasable), "still_need_a_tier": len(no_tier),
                     "blocked_for_another_reason": len(blocked)},
        )

    return {
        "dry_run": not apply,
        "total_needs_tier_review": len(parked),
        "releasable_have_a_valid_tier": len(releasable),
        "still_need_a_tier": len(no_tier),
        "blocked_for_another_reason": len(blocked),
        "blocked_breakdown": {
            "duplicate": sum(1 for l in blocked if l.is_duplicate),
            "no_phone": sum(1 for l in blocked if not l.phone),
        },
    }


@router.delete("/duplicates/bulk-delete")
def bulk_delete_duplicate_leads(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_context),
):
    """
    Permanently deletes all leads flagged as duplicates (is_duplicate=True)
    for this organization. These leads were already blocked from all
    outreach by the dedup engine - this just removes them from the
    database entirely for a clean list.

    Requires org_admin or super_admin role - advisors cannot bulk delete.
    """
    if current_user.role not in ("org_admin", "super_admin", "god_admin"):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Admin role required to bulk delete leads.")

    duplicates = db.query(Lead).filter(
        Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db),
        Lead.is_duplicate == True,
    ).all()

    count = len(duplicates)
    for lead in duplicates:
        db.delete(lead)

    db.commit()

    log_action(
        db,
        organization_id=current_user.organization_id,
        actor_user_id=current_user.id,
        action="lead.bulk_delete_duplicates",
        target_type="organization",
        target_id=current_user.organization_id,
        details={"deleted_count": count},
    )

    return {"deleted": count, "message": f"Permanently deleted {count} duplicate leads."}


# ── Deduplicate email-only leads that slipped past the original dedup ──────
@router.post("/deduplicate-email-leads")
def deduplicate_email_leads(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """
    One-time (and safe to re-run) cleanup: finds email-only leads in this
    org where the same (email + last_name) pair appears more than once, keeps
    the oldest record, and marks all later duplicates as is_duplicate=True
    with status=dnc — exactly what the importer now does on new uploads.

    Does NOT delete anything — just flags. After reviewing, the advisor can
    call DELETE /leads/duplicates/bulk-delete to permanently remove them.
    Requires org_admin or super_admin.
    """
    if current_user.role not in ("org_admin", "super_admin", "god_admin"):
        raise HTTPException(status_code=403, detail="Admin role required.")

    from sqlalchemy import func as sqlfunc

    # Pull all email-only leads for this org that have a real email and last name
    email_leads = (
        db.query(Lead)
        .filter(
            Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db),
            Lead.contact_channel == "email_only",
            Lead.email.isnot(None),
            Lead.last_name.isnot(None),
        )
        .order_by(Lead.created_at.asc())  # oldest first → we keep the first one
        .all()
    )

    # Group by (normalized_email, normalized_last_name)
    seen: dict = {}  # key -> first (oldest) lead id
    flagged_ids = []

    for lead in email_leads:
        norm_email = (lead.email or "").strip().lower()
        norm_last  = "".join(c for c in (lead.last_name or "").lower() if c.isalpha())
        key = (norm_email, norm_last)

        if not norm_email or not norm_last:
            continue

        if key in seen:
            # This is a duplicate of the first record we saw for this key
            if not lead.is_duplicate:
                lead.is_duplicate = True
                lead.duplicate_of_lead_id = seen[key]
                lead.duplicate_reason = "existing_email"
                lead.duplicate_match_field = "email+last_name"
                lead.duplicate_match_value = norm_email
                # NOT dnc. Same rule as the importer: a duplicate is a
                # data-quality flag. A cleanup sweep has no business moving
                # anybody into the do-not-contact population.
                flagged_ids.append(lead.id)
        else:
            seen[key] = lead.id

    db.commit()

    return {
        "scanned": len(email_leads),
        "newly_flagged": len(flagged_ids),
        "message": (
            f"Flagged {len(flagged_ids)} email-only duplicate leads. "
            "Call DELETE /leads/duplicates/bulk-delete to permanently remove them."
            if flagged_ids else
            "No new email-only duplicates found — list is already clean."
        ),
    }


# ── PUBLIC: Landing page demo request — see demo_request() further down ─────
#
# THERE WAS A SECOND HANDLER FOR THIS PATH AND IT COULD NEVER RUN.
#
# `POST /leads/demo-request` was declared twice in this file: once here, and
# once near the bottom with a different request schema, a different response
# shape, and the notification email to LEAD_NOTIFY_EMAILS. Starlette matches the
# FIRST registration, so this one always won and the notifying one was dead
# code that still read like the live path. Its OPTIONS preflight kept answering,
# so a browser preflight succeeded and the POST then landed on a handler with
# incompatible required fields.
#
# The two are now ONE handler, defined once, below `flag_lead`. It accepts the
# union of both schemas and answers with the union of both response shapes, so
# no caller of either version breaks. This comment is all that remains here on
# purpose: the next person to look for the landing-page endpoint in the obvious
# place should be told where it went, not find a third copy.


