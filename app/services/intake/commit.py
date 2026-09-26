"""SAFE COMMIT — the only step that writes to the CRM, and only on a decision.

THE DECISION SCREEN'S ACTIONS
  stage_only        (DEFAULT) nothing is written. The batch is parked as STAGED.
  ready_only        READY rows, reviewer-APPROVED rows and exact existing matches.
  ready_and_review  ...plus NEEDS_REVIEW rows, handled conservatively (below).
  include_enrichment: rows with no usable channel are preserved as contacts in
                      NEEDS_ENRICHMENT - searchable, matchable, reportable,
                      never leads.

WHAT A ROW BECOMES
  every committed row           -> an OrgContact (the organization's database)
  classification.creates_lead   -> ALSO a Lead, only if it has a usable direct
    AND a usable channel           channel, is not blocked, and is not an
    AND not review/blocked         unresolved review row. Everything else never
                                   touches a lead count, pipeline or queue.

WHAT IS NEVER DONE
  * An uncertain (POSSIBLE) match is never merged. In ready_and_review mode an
    undecided possible match is created SEPARATELY and never activated as a lead.
  * A DNC / blocked existing record is never reactivated.
  * An import never grants TCPA SMS consent (Lead.sms_consent stays False).
  * A field a person corrected by hand is never overwritten.
  * INVALID rows are never written.

ROLLBACK SUPPORT
  Every write is recorded in import_record_versions: created records by id,
  updated records with the exact prior value of every field changed.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.import_models import ImportBatch, ImportBatchStatus, ImportRowReviewStatus, ImportStagedRow
from app.models.intake_models import (CommitMode, ContactLifecycle, DuplicateResolution,
                                      EmailStatus, ImportRecordVersion, IntakeStatus,
                                      MatchType, OrgContact, OrgContactSourceId, RecordClass)
from app.services.intake import classification as C
from app.services.intake import engine as ENG

log = logging.getLogger(__name__)

CHUNK = 200

A_CREATE = "create"
A_UPDATE = "update"
A_MERGE_DUP = "merge_into_file_duplicate"
A_SKIP = "skip"

_BAD_EMAIL = (EmailStatus.INVALID, EmailStatus.HARD_BOUNCE)
_DENY_EMAIL = (EmailStatus.UNSUBSCRIBED, EmailStatus.SUPPRESSED)


def _j(s, d):
    try:
        return json.loads(s) if s else d
    except Exception:  # noqa: BLE001
        return d


# ══════════════════════════════════════════════════════════════════════════
# SELECTION
# ══════════════════════════════════════════════════════════════════════════

def planned_action(row: ImportStagedRow, mode: str, include_enrichment: bool) -> Optional[str]:
    """What the commit would do with this row, or None if it is not selected."""
    st = row.intake_status
    res = row.duplicate_resolution
    if mode == CommitMode.STAGE_ONLY:
        return None
    if st in (IntakeStatus.IMPORTED, IntakeStatus.SKIPPED, IntakeStatus.INVALID,
              IntakeStatus.FAILED, IntakeStatus.PARSED, None):
        return None
    if st == IntakeStatus.DUPLICATE:
        if res in (DuplicateResolution.MERGE, DuplicateResolution.UPDATE_EXISTING):
            return A_MERGE_DUP
        if res == DuplicateResolution.SKIP_INCOMING:
            return A_SKIP
        if res == DuplicateResolution.KEEP_SEPARATE:
            return A_CREATE
        return None
    if st == IntakeStatus.BLOCKED:
        return None                         # only a reviewer's APPROVAL moves it
    if st == IntakeStatus.EXISTING_MATCH:
        if res in (DuplicateResolution.UPDATE_EXISTING, DuplicateResolution.MERGE, None):
            return A_UPDATE
        if res == DuplicateResolution.SKIP_INCOMING:
            return A_SKIP
        if res == DuplicateResolution.KEEP_SEPARATE:
            return A_CREATE
        return None
    if st == IntakeStatus.APPROVED:
        if res in (DuplicateResolution.UPDATE_EXISTING, DuplicateResolution.MERGE) and (
                row.matched_contact_id or row.matched_lead_id):
            return A_UPDATE
        if res == DuplicateResolution.SKIP_INCOMING:
            return A_SKIP
        return A_CREATE
    if st == IntakeStatus.READY:
        return A_CREATE
    if st == IntakeStatus.NEEDS_ENRICHMENT:
        return A_CREATE if include_enrichment else None
    if st == IntakeStatus.NEEDS_REVIEW:
        if mode != CommitMode.READY_AND_REVIEW:
            return None
        if res in (DuplicateResolution.UPDATE_EXISTING, DuplicateResolution.MERGE) and (
                row.matched_contact_id or row.matched_lead_id):
            return A_UPDATE
        if res == DuplicateResolution.SKIP_INCOMING:
            return A_SKIP
        if row.needs_enrichment and not include_enrichment:
            return None
        return A_CREATE
    return None


def may_activate_lead(row: ImportStagedRow, contact: OrgContact) -> Tuple[bool, str]:
    if not row.creates_lead:
        return False, "classification_is_not_an_opportunity"
    if row.needs_enrichment:
        return False, "no_usable_channel"
    if row.intake_status not in (IntakeStatus.READY, IntakeStatus.APPROVED,
                                 IntakeStatus.EXISTING_MATCH):
        return False, "unresolved_review"
    if contact.lead_id:
        return False, "already_a_lead"
    if contact.sms_status == "dnc":
        return False, "dnc"
    return True, ""


def commit_preview(db: Session, batch: ImportBatch, mode: str, include_enrichment: bool) -> dict:
    rows = (db.query(ImportStagedRow)
            .filter(ImportStagedRow.batch_id == batch.id,
                    ImportStagedRow.organization_id == batch.organization_id).all())
    out = {"mode": mode, "include_enrichment": include_enrichment,
           "create_contacts": 0, "update_existing": 0, "merge_file_duplicates": 0,
           "skip": 0, "activate_leads": 0, "enrichment_contacts": 0,
           "not_selected": {}, "already_imported": 0}
    for r in rows:
        a = planned_action(r, mode, include_enrichment)
        if r.intake_status == IntakeStatus.IMPORTED:
            out["already_imported"] += 1
        if a is None:
            k = r.intake_status or "none"
            if r.intake_status != IntakeStatus.IMPORTED:
                out["not_selected"][k] = out["not_selected"].get(k, 0) + 1
            continue
        if a == A_CREATE:
            out["create_contacts"] += 1
            if r.needs_enrichment:
                out["enrichment_contacts"] += 1
            if (r.creates_lead and not r.needs_enrichment
                    and r.intake_status in (IntakeStatus.READY, IntakeStatus.APPROVED)):
                out["activate_leads"] += 1
        elif a == A_UPDATE:
            out["update_existing"] += 1
        elif a == A_MERGE_DUP:
            out["merge_file_duplicates"] += 1
        elif a == A_SKIP:
            out["skip"] += 1
    try:
        from app.models.models import Organization
        from app.services import plan_limits
        org = db.query(Organization).filter(Organization.id == batch.organization_id).first()
        limit = plan_limits.limit_for(db, org, plan_limits.LIMIT_LEADS)
        used = plan_limits.usage_for(db, org, plan_limits.LIMIT_LEADS) if limit is not None else None
        out["lead_capacity"] = {"limit": limit, "used": used,
                                "available": (None if limit is None or used is None
                                              else max(0, limit - used))}
    except Exception:  # noqa: BLE001
        out["lead_capacity"] = None
    return out


# ══════════════════════════════════════════════════════════════════════════
# WRITES
# ══════════════════════════════════════════════════════════════════════════

_CONTACT_SNAPSHOT_FIELDS = [
    "first_name", "last_name", "full_name", "company", "company_norm", "job_title", "email",
    "email_raw", "phone", "phone_raw", "mobile_phone", "mobile_phone_raw", "phone_line_type",
    "sms_status", "email_status", "street_address", "city", "state", "zip_code", "country",
    "owner_name", "last_activity_at", "classification", "record_class", "historical_customer",
    "lifecycle", "tags", "custom_fields", "vertical_fields", "source_fields", "lead_id",
]
_LEAD_SNAPSHOT_FIELDS = ["first_name", "last_name", "phone", "phone_raw", "email",
                         "street_address", "city", "state", "zip_code", "allow_email",
                         "allow_bulk_email", "allow_sms", "allow_voice", "sms_consent",
                         "custom_fields", "org_contact_id", "last_contact_date"]


def _snap(obj, fields) -> dict:
    out = {}
    for f in fields:
        v = getattr(obj, f, None)
        out[f] = v.isoformat() if isinstance(v, datetime) else v
    return out


def _version(db, batch, row, target_type, target_id, action, before=None, after=None):
    db.add(ImportRecordVersion(
        organization_id=batch.organization_id, batch_id=batch.id,
        staged_row_id=row.id if row is not None else None,
        target_type=target_type, target_id=target_id, action=action,
        before_json=json.dumps(before, default=str) if before is not None else None,
        after_json=json.dumps(after, default=str) if after is not None else None))


def _lifecycle(row) -> str:
    return ContactLifecycle.NEEDS_ENRICHMENT if row.needs_enrichment else ContactLifecycle.ACTIVE


def _contact_values(row: ImportStagedRow, batch: ImportBatch) -> dict:
    norm = _j(row.normalized_json, {})
    tags = list(_j(batch.tags_json, []))
    for t in _j(row.tags_json, []):
        if t not in tags:
            tags.append(t)
    return {
        "record_class": row.record_class or RecordClass.CONTACT,
        "classification": row.classification,
        "lifecycle": _lifecycle(row),
        "historical_customer": row.historical_customer,
        "first_name": row.first_name, "last_name": row.last_name, "full_name": row.full_name,
        "company": row.company, "company_norm": row.company_norm,
        "job_title": norm.get("job_title"),
        "email": row.email_normalized, "email_raw": row.email_raw,
        "phone": norm.get("phone_e164"),
        "phone_raw": row.phone_raw,
        "mobile_phone": row.mobile_phone_normalized, "mobile_phone_raw": row.mobile_phone_raw,
        "phone_line_type": row.phone_line_type,
        "sms_status": row.sms_status, "email_status": row.email_status,
        "outreach_reasons": json.dumps(norm.get("outreach_reasons") or []),
        "street_address": row.street_address, "city": row.city, "state": row.state,
        "zip_code": row.zip_code, "country": norm.get("country"),
        "source": batch.source_label, "source_detail": batch.source_detail or batch.source_filename,
        "source_system": row.source_system or (batch.source_system if row.source_record_id else None),
        "source_record_id": row.source_record_id,
        "import_batch_id": batch.id, "source_row_number": row.row_number,
        "last_activity_at": row.last_activity_date.replace(tzinfo=None)
        if row.last_activity_date else None,
        "owner_name": norm.get("owner_name"),
        "tags": json.dumps(tags) if tags else None,
        "custom_fields": row.custom_fields_json,
        "vertical_fields": row.vertical_fields_json,
        "source_fields": json.dumps(norm.get("source_meta")) if norm.get("source_meta") else None,
    }


def _register_source_id(db, org_id, contact_id, system, sid, batch_id, primary):
    if not sid:
        return
    exists = (db.query(OrgContactSourceId.id)
              .filter(OrgContactSourceId.organization_id == org_id,
                      OrgContactSourceId.org_contact_id == contact_id,
                      OrgContactSourceId.source_record_id == sid,
                      OrgContactSourceId.source_system == (system or "")).first())
    if not exists:
        db.add(OrgContactSourceId(organization_id=org_id, org_contact_id=contact_id,
                                  source_system=system or "", source_record_id=sid,
                                  import_batch_id=batch_id, is_primary=primary))


def create_contact(db, batch, row) -> OrgContact:
    vals = _contact_values(row, batch)
    c = OrgContact(organization_id=batch.organization_id, **vals)
    db.add(c)
    db.flush()
    _register_source_id(db, batch.organization_id, c.id, vals["source_system"],
                        row.source_record_id, batch.id, True)
    _version(db, batch, row, "org_contact", c.id, "created", None, {"id": c.id})
    return c


_JSON_FIELDS = ("tags", "custom_fields", "vertical_fields", "source_fields")


def update_contact(db, batch, row, contact: OrgContact, policy: dict) -> List[str]:
    """Apply the batch's update policy. Returns the fields changed."""
    vals = _contact_values(row, batch)
    protected = set(_j(contact.manually_edited_fields, []))
    overwrite = set(policy.get("fields") or []) if policy.get("mode") == "overwrite" else set()
    before, changed = {}, []
    candidates = ["first_name", "last_name", "full_name", "company", "company_norm", "job_title",
                  "email", "email_raw", "phone", "phone_raw", "mobile_phone", "mobile_phone_raw",
                  "street_address", "city", "state", "zip_code", "country", "owner_name",
                  "last_activity_at", "historical_customer", "classification"]
    for f in candidates:
        new = vals.get(f)
        if new in (None, ""):
            continue
        cur = getattr(contact, f)
        base_field = "company" if f == "company_norm" else (
            "email" if f == "email_raw" else "phone" if f == "phone_raw" else
            "mobile_phone" if f == "mobile_phone_raw" else f)
        if base_field in protected:
            continue
        if cur in (None, "") or (base_field in overwrite and cur != new):
            before[f] = cur.isoformat() if isinstance(cur, datetime) else cur
            setattr(contact, f, new)
            changed.append(f)
    for f in _JSON_FIELDS:
        new = _j(vals.get(f), None)
        if not new or f in protected:
            continue
        cur = _j(getattr(contact, f), None)
        if f == "tags":
            merged = list(cur or [])
            merged += [t for t in new if t not in merged]
        else:
            merged = dict(cur or {})
            for k, v in new.items():
                if k not in merged or merged[k] in (None, "") or f in overwrite:
                    merged[k] = v
        if merged != cur:
            before[f] = getattr(contact, f)
            setattr(contact, f, json.dumps(merged))
            changed.append(f)
    # Outreach status is re-derived evidence, and a more restrictive signal
    # always wins: an import can mark an address bounced; it cannot un-bounce it.
    for f, rank in (("email_status", _EMAIL_RANK), ("sms_status", _SMS_RANK)):
        new, cur = vals.get(f), getattr(contact, f)
        if new and (cur is None or rank.get(new, 0) > rank.get(cur, 0)):
            before[f] = cur
            setattr(contact, f, new)
            changed.append(f)
    if contact.lifecycle == ContactLifecycle.NEEDS_ENRICHMENT and not row.needs_enrichment:
        before["lifecycle"] = contact.lifecycle
        contact.lifecycle = ContactLifecycle.ACTIVE
        changed.append("lifecycle")
    _register_source_id(db, batch.organization_id, contact.id, vals["source_system"],
                        row.source_record_id, batch.id, False)
    if changed:
        _version(db, batch, row, "org_contact", contact.id, "updated", before,
                 _snap(contact, changed))
    return changed


# Higher = more restrictive. An import may only move a status UP this list.
_EMAIL_RANK = {"ready": 1, "pending": 1, "no_email": 0, "review": 2, "invalid": 3,
               "suppressed": 4, "unsubscribed": 4, "hard_bounce": 4}
_SMS_RANK = {"ready": 1, "pending_validation": 1, "no_phone": 0, "review": 2, "landline": 3,
             "invalid": 3, "opted_out": 4, "suppressed": 4, "dnc": 5}


def _lead_phone(e164: Optional[str]) -> Optional[str]:
    """A Lead's phone in the PLATFORM's format, not intake's E.164.

    Every lead reader - the inbound SMS webhook's sender lookup, suppression,
    dedupe - compares against `dedup_service.normalize_phone` ("12145550123").
    A lead written as "+12145550123" is invisible to all of them: a reply from
    that person would not find their record. The org contact keeps E.164."""
    if not e164:
        return None
    from app.services.dedup_service import normalize_phone
    return normalize_phone(e164) or e164


def _update_matched_lead(db, batch, row, lead) -> List[str]:
    """Blank-fill a matched lead and apply more-restrictive consent. Versioned."""
    before, changed = {}, []
    fill = {"first_name": row.first_name, "last_name": row.last_name,
            "phone": _lead_phone(row.phone_normalized), "phone_raw": row.phone_raw,
            "email": row.email_normalized if row.email_status not in _BAD_EMAIL else None,
            "street_address": row.street_address, "city": row.city, "state": row.state,
            "zip_code": row.zip_code}
    for f, v in fill.items():
        if v and not getattr(lead, f, None):
            before[f] = getattr(lead, f, None)
            setattr(lead, f, v)
            changed.append(f)
    # Restrictive-only permission merge: a denial is applied, a grant never is.
    denials = {"allow_email": row.consent_email is False or row.email_status in _DENY_EMAIL,
               "allow_bulk_email": row.consent_bulk_email is False,
               "allow_sms": row.consent_sms is False,
               "allow_voice": row.consent_voice is False}
    for f, deny in denials.items():
        if deny and getattr(lead, f, None) is not False:
            before[f] = getattr(lead, f, None)
            setattr(lead, f, False)
            changed.append(f)
    if changed:
        _version(db, batch, row, "lead", lead.id, "updated", before, _snap(lead, changed))
    return changed


def activate_lead(db, batch, row, contact: OrgContact, cat, ctx_name: str, capacity=None,
                  hold_when_full: bool = False):
    """Create the Lead for an opportunity contact, or return None when the
    plan's lead ceiling is reached. The capacity claim lives HERE, beside the
    only line that creates the lead, so no caller can create one unchecked.

    `hold_when_full` is the EXTERNAL ARRIVAL rule (app/services/lead_capacity):
    a person who reached out on their own - a web form, a webhook - is never
    left without a lead because of a plan number. The lead is created HELD:
    kept, not counted toward the plan, and blocked from every send, cadence
    and AI path until capacity exists (lead_capacity.release_available). A
    bulk import (a person at the customer, present to decide) keeps the old
    rule: contact kept, no lead, reported as held_for_capacity."""
    from app.models.models import Lead
    from app.services import master_contacts
    held = False
    if capacity is not None:
        if not capacity.has_room(1):
            if not hold_when_full:
                return None
            held = True
        else:
            capacity.take(1)
    cd = cat.get(row.classification)
    norm = _j(row.normalized_json, {})
    custom = {}
    if contact.company:
        custom["company"] = contact.company
    if norm.get("job_title"):
        custom["job_title"] = norm["job_title"]
    custom["classification"] = row.classification
    custom["record_class"] = row.record_class
    custom.update(_j(row.custom_fields_json, {}))
    custom.update(_j(row.vertical_fields_json, {}))
    email = row.email_normalized
    manual_flag = manual_reason = None
    if row.email_status in _BAD_EMAIL and email:
        manual_flag, manual_reason = "bad_email", f"import: {row.email_status}"
    phone = _lead_phone(row.mobile_phone_normalized or row.phone_normalized)
    lead = Lead(
        organization_id=batch.organization_id,
        first_name=row.first_name, last_name=row.last_name,
        phone=phone, phone_raw=row.mobile_phone_raw or row.phone_raw,
        email=email,
        street_address=row.street_address, city=row.city, state=row.state,
        zip_code=row.zip_code,
        status="new", tier=None,
        contact_channel="sms" if phone else "email_only",
        relationship_type=(cd.relationship_type if cd and cd.relationship_type
                           else batch.relationship_type),
        source=batch.source_label, source_detail=batch.source_detail or batch.source_filename,
        source_file=batch.source_filename, source_year=batch.source_year,
        import_list_name=batch.import_list_name, imported_by_name=ctx_name,
        source_category=row.classification,
        last_contact_date=row.last_activity_date.replace(tzinfo=None)
        if row.last_activity_date else None,
        allow_email=(False if (row.consent_email is False or row.email_status in _DENY_EMAIL)
                     else row.consent_email),
        allow_bulk_email=row.consent_bulk_email,
        allow_sms=row.consent_sms, allow_voice=row.consent_voice,
        sms_consent=False,
        manual_flag=manual_flag, manual_flag_reason=manual_reason,
        custom_fields=json.dumps(custom) if custom else None,
        org_contact_id=contact.id, import_batch_id=batch.id,
    )
    if held:
        from app.services import lead_capacity
        lead.capacity_state = lead_capacity.OVER_CAPACITY
        lead.capacity_held_at = datetime.utcnow()
        lead.capacity_hold_reason = lead_capacity.REASON_MAX_LEADS
    db.add(lead)
    db.flush()
    contact.lead_id = lead.id
    _version(db, batch, row, "lead", lead.id, "created", None, {"id": lead.id})
    try:
        master_contacts.record_lead(db, lead, source="import",
                                    source_detail=batch.source_filename or None,
                                    import_batch_id=batch.id,
                                    ingestion_path="intake.commit.activate_lead")
    except Exception:  # noqa: BLE001 - best effort by construction
        log.exception("master contact retention failed for lead %s", lead.id)
    return lead


def _ensure_custom_field_defs(db, batch):
    """A column mapped as kind=custom IS the approval to create that custom
    field. Adds missing definitions to the organization's schema."""
    from app.models.models import Organization
    mapping = _j(batch.mapping_json, {})
    wanted = [(h, m["target"]) for h, m in mapping.items() if m.get("kind") == "custom"]
    if not wanted:
        return []
    org = db.query(Organization).filter(Organization.id == batch.organization_id).first()
    defs = _j(org.crm_custom_fields, []) if org else []
    if not isinstance(defs, list):
        return []
    have = {d.get("key") for d in defs if isinstance(d, dict)}
    added = []
    for header, key in wanted:
        if key not in have:
            defs.append({"key": key, "label": header, "type": "text",
                         "source": f"import:{batch.batch_code}"})
            added.append(key)
            have.add(key)
    if added and org is not None:
        org.crm_custom_fields = json.dumps(defs)
    return added


def run_commit(db: Session, batch_id: str, org_id: str, ctx, mode: str,
               include_enrichment: bool, hold_when_full: bool = False) -> ImportBatch:
    from app.services.intake import audit
    batch = (db.query(ImportBatch)
             .filter(ImportBatch.id == batch_id, ImportBatch.organization_id == org_id).first())
    if batch is None:
        raise ENG.IntakeError("Batch not found.")
    if mode not in CommitMode.ALL:
        raise ENG.IntakeError(f"Unknown commit mode '{mode}'.")

    if mode == CommitMode.STAGE_ONLY:
        batch.commit_mode = mode
        batch.status = ImportBatchStatus.STAGED
        batch.stage = "staged"
        audit.record(db, ctx, "intake.decision_stage_only", batch.id,
                     {"rows": batch.total_rows})
        db.commit()
        return batch

    started = datetime.utcnow()
    batch.status = ImportBatchStatus.COMMITTING
    batch.commit_mode = mode
    batch.stage = "committing"
    batch.progress_pct = 0
    batch.heartbeat_at = datetime.utcnow()
    db.commit()

    report = {"mode": mode, "include_enrichment": include_enrichment,
              "contacts_created": 0, "contacts_updated": 0, "contacts_unchanged": 0,
              "file_duplicates_merged": 0, "leads_created": 0, "leads_updated": 0,
              "skipped": 0, "failed": 0, "held_for_capacity": 0, "needs_enrichment_preserved": 0,
              "lead_not_activated": {}, "errors": []}
    try:
        from app.services import plan_limits
        capacity = plan_limits.counter_for_org_id(db, org_id, plan_limits.LIMIT_LEADS)
    except Exception:  # noqa: BLE001
        capacity = None
    cat = ENG.org_catalog(db, org_id)
    policy = _j(batch.update_policy_json, ENG.DEFAULT_UPDATE_POLICY)
    report["custom_fields_created"] = _ensure_custom_field_defs(db, batch)
    db.commit()

    from app.models.models import Lead
    ids = [rid for (rid,) in (db.query(ImportStagedRow.id)
                              .filter(ImportStagedRow.batch_id == batch.id,
                                      ImportStagedRow.organization_id == org_id)
                              .order_by(ImportStagedRow.row_number).all())]
    # row number -> contact id for rows already written (this run or earlier)
    written: Dict[int, str] = {
        rn: cid for rn, cid in (db.query(ImportStagedRow.row_number,
                                         ImportStagedRow.committed_contact_id)
                                .filter(ImportStagedRow.batch_id == batch.id,
                                        ImportStagedRow.committed_contact_id.isnot(None)).all())}
    total = len(ids)
    done = 0
    for i in range(0, total, CHUNK):
        rows = (db.query(ImportStagedRow).filter(ImportStagedRow.id.in_(ids[i:i + CHUNK]))
                .order_by(ImportStagedRow.row_number).all())
        for row in rows:
            action = planned_action(row, mode, include_enrichment)
            if action is None:
                continue
            sp = db.begin_nested()
            try:
                contact = None
                lead = None
                if action == A_SKIP:
                    row.intake_status = IntakeStatus.SKIPPED
                    row.commit_action = "skipped"
                    report["skipped"] += 1
                elif action == A_MERGE_DUP:
                    dup_of = _j(row.normalized_json, {}).get("dup_of_row")
                    target_id = written.get(dup_of)
                    if not target_id:
                        sp.rollback()
                        continue          # survivor not imported yet; stays staged
                    contact = db.query(OrgContact).filter(
                        OrgContact.id == target_id, OrgContact.organization_id == org_id).first()
                    update_contact(db, batch, row, contact, {"mode": "fill_blanks"})
                    row.commit_action = "merged_into_file_duplicate"
                    report["file_duplicates_merged"] += 1
                elif action == A_UPDATE:
                    contact = None
                    if row.matched_contact_id:
                        contact = db.query(OrgContact).filter(
                            OrgContact.id == row.matched_contact_id,
                            OrgContact.organization_id == org_id).first()
                    if contact is None and row.matched_lead_id:
                        lead = db.query(Lead).filter(Lead.id == row.matched_lead_id,
                                                     Lead.organization_id == org_id).first()
                        if lead is not None:
                            if _update_matched_lead(db, batch, row, lead):
                                report["leads_updated"] += 1
                            contact = (db.query(OrgContact)
                                       .filter(OrgContact.organization_id == org_id,
                                               OrgContact.lead_id == lead.id).first())
                            if contact is None:
                                contact = create_contact(db, batch, row)
                                contact.lead_id = lead.id
                                if not lead.org_contact_id:
                                    _version(db, batch, row, "lead", lead.id, "updated",
                                             {"org_contact_id": None},
                                             {"org_contact_id": contact.id})
                                    lead.org_contact_id = contact.id
                                report["contacts_created"] += 1
                            else:
                                update_contact(db, batch, row, contact, policy)
                    elif contact is not None:
                        changed = update_contact(db, batch, row, contact, policy)
                        report["contacts_updated" if changed else "contacts_unchanged"] += 1
                        if contact.lead_id:
                            ml = db.query(Lead).filter(Lead.id == contact.lead_id,
                                                       Lead.organization_id == org_id).first()
                            if ml is not None and _update_matched_lead(db, batch, row, ml):
                                report["leads_updated"] += 1
                    if contact is None:
                        raise ENG.IntakeError("matched record no longer exists")
                    row.commit_action = "updated_existing"
                else:
                    contact = create_contact(db, batch, row)
                    report["contacts_created"] += 1
                    if row.needs_enrichment:
                        report["needs_enrichment_preserved"] += 1
                    row.commit_action = "created"

                if contact is not None and action in (A_CREATE, A_UPDATE):
                    ok, why = may_activate_lead(row, contact)
                    if ok:
                        lead = activate_lead(db, batch, row, contact, cat, ctx.actor_name,
                                             capacity, hold_when_full=hold_when_full)
                        if lead is None:
                            ok, why = False, "plan_capacity_reached"
                            report["held_for_capacity"] += 1
                        else:
                            report["leads_created"] += 1
                            if getattr(lead, "capacity_state", None):
                                report["held_for_capacity"] += 1
                    if not ok and row.creates_lead:
                        report["lead_not_activated"][why] = \
                            report["lead_not_activated"].get(why, 0) + 1

                if action != A_SKIP:
                    row.intake_status = IntakeStatus.IMPORTED
                    row.committed_contact_id = contact.id if contact else None
                    written[row.row_number] = row.committed_contact_id
                row.committed_lead_id = (lead.id if lead is not None else
                                         (contact.lead_id if contact is not None else None))
                row.review_status = ImportRowReviewStatus.COMMITTED
                row.committed_at = datetime.utcnow()
                row.committed_by_id = ctx.actor_id
                sp.commit()
            except Exception as exc:  # noqa: BLE001 - one bad row never sinks the batch
                sp.rollback()
                report["failed"] += 1
                if len(report["errors"]) < 50:
                    report["errors"].append({"row": row.row_number,
                                             "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
                row.intake_status = IntakeStatus.FAILED
                row.review_note = f"Commit error: {str(exc)[:200]}"
                log.exception("intake commit failed row %s batch %s", row.row_number, batch.id)
            done += 1
        batch.progress_pct = int(100 * min(i + CHUNK, total) / max(total, 1))
        batch.heartbeat_at = datetime.utcnow()
        db.commit()

    remaining = (db.query(ImportStagedRow)
                 .filter(ImportStagedRow.batch_id == batch.id,
                         ImportStagedRow.intake_status.in_((
                             IntakeStatus.READY, IntakeStatus.NEEDS_REVIEW,
                             IntakeStatus.NEEDS_ENRICHMENT, IntakeStatus.EXISTING_MATCH,
                             IntakeStatus.DUPLICATE, IntakeStatus.BLOCKED,
                             IntakeStatus.APPROVED))).count())
    report["rows_still_staged"] = remaining
    report["seconds"] = round((datetime.utcnow() - started).total_seconds(), 2)
    batch.commit_report_json = json.dumps(report, default=str)
    written_any = (report["contacts_created"] + report["contacts_updated"]
                   + report["contacts_unchanged"] + report["file_duplicates_merged"]
                   + report["skipped"])
    if report["failed"] and not written_any:
        batch.status = ImportBatchStatus.FAILED
        batch.error_message = f"All {report['failed']} selected rows failed to commit."
    elif report["failed"] or remaining:
        batch.status = ImportBatchStatus.PARTIALLY_COMMITTED
    else:
        batch.status = ImportBatchStatus.COMMITTED
    batch.stage = "committed"
    batch.progress_pct = 100
    batch.committed_at = datetime.utcnow()
    batch.committed_by_id = ctx.actor_id
    batch.committed_by_name = ctx.actor_name
    batch.completed_at = datetime.utcnow()
    batch.committed_rows = report["contacts_created"]
    batch.merged_rows = report["contacts_updated"] + report["file_duplicates_merged"]
    audit.record(db, ctx, "intake.batch_committed", batch.id,
                 {k: v for k, v in report.items() if k != "errors"})
    db.commit()
    return batch
