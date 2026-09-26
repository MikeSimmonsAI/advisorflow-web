"""SAFE ROLLBACK of a committed import batch.

Rollback reads `import_record_versions` - the exact list of writes the batch
made - and decides, record by record, what can be undone WITHOUT destroying
anything anyone did after the import:

  CREATED record, untouched since, no downstream activity  -> REMOVED
  CREATED record with downstream activity or later edits   -> ARCHIVED (contact)
                                                              or KEPT (lead),
                                                              and the batch is
                                                              detached from it
  UPDATED record, field still holds the imported value     -> RESTORED to the
                                                              prior value
  UPDATED record, field changed again after the import     -> KEPT, reported

"Downstream activity" is discovered, not listed: every table in the schema
with a foreign key to `leads.id` is checked (messages, replies, booking links,
calls, emails, appointments, opportunities, tasks, notes...). A table added
next year is covered without anyone remembering to add it here.

`plan()` never writes. `execute()` applies exactly what `plan()` reports.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, List

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.import_models import ImportBatch, ImportBatchStatus, ImportStagedRow
from app.models.intake_models import (ContactLifecycle, ImportRecordVersion, OrgContact,
                                      OrgContactSourceId)

log = logging.getLogger(__name__)

# Tables whose reference to a lead is the import's own bookkeeping, not activity.
_OWN_TABLES = {"import_staged_rows", "import_record_versions", "org_contacts",
               "org_contact_source_ids"}

REMOVE, RESTORE, ARCHIVE, KEEP = "remove", "restore", "archive", "keep"


def _j(s, d):
    try:
        return json.loads(s) if s else d
    except Exception:  # noqa: BLE001
        return d


def _lead_reference_columns():
    from app.models.models import Base
    out = []
    for table in Base.metadata.tables.values():
        if table.name in _OWN_TABLES:
            continue
        for col in table.columns:
            for fk in col.foreign_keys:
                if fk.target_fullname == "leads.id":
                    out.append((table, col))
    return out


def downstream_activity(db: Session, lead_ids: List[str]) -> Dict[str, Dict[str, int]]:
    """{lead_id: {table: count}} for every lead with ANY referencing row."""
    out: Dict[str, Dict[str, int]] = defaultdict(dict)
    ids = [i for i in lead_ids if i]
    if not ids:
        return {}
    for table, col in _lead_reference_columns():
        for i in range(0, len(ids), 900):
            chunk = ids[i:i + 900]
            try:
                rows = db.execute(
                    table.select().with_only_columns(col, func.count())
                    .where(col.in_(chunk)).group_by(col)).all()
            except Exception:  # noqa: BLE001 - a table missing in this DB has no rows
                db.rollback()
                continue
            for lid, n in rows:
                if n:
                    out[lid][table.name] = out[lid].get(table.name, 0) + int(n)
    return dict(out)


def _differs(current, recorded) -> bool:
    if isinstance(current, datetime):
        current = current.isoformat()
    if current in ("",):
        current = None
    if recorded in ("",):
        recorded = None
    return current != recorded


_LEAD_TOUCH_FIELDS = ("status", "assigned_to_id", "notes", "tier", "manual_flag",
                      "case_status", "engagement_temperature")


def plan(db: Session, batch: ImportBatch) -> dict:
    from app.models.models import Lead
    versions = (db.query(ImportRecordVersion)
                .filter(ImportRecordVersion.batch_id == batch.id,
                        ImportRecordVersion.organization_id == batch.organization_id,
                        ImportRecordVersion.rolled_back_at.is_(None))
                .order_by(ImportRecordVersion.applied_at).all())
    created_leads = [v.target_id for v in versions
                     if v.target_type == "lead" and v.action == "created"]
    activity = downstream_activity(db, created_leads)

    # Later writes by OTHER batches to the same records make them "touched".
    targets = {v.target_id for v in versions}
    later = set()
    if targets:
        tl = list(targets)
        for i in range(0, len(tl), 900):
            for (tid,) in (db.query(ImportRecordVersion.target_id)
                           .filter(ImportRecordVersion.organization_id == batch.organization_id,
                                   ImportRecordVersion.batch_id != batch.id,
                                   ImportRecordVersion.target_id.in_(tl[i:i + 900]),
                                   ImportRecordVersion.rolled_back_at.is_(None)).all()):
                later.add(tid)

    items = []
    lead_removable = {}
    for lid in created_leads:
        lead = db.query(Lead).filter(Lead.id == lid,
                                     Lead.organization_id == batch.organization_id).first()
        if lead is None:
            lead_removable[lid] = (False, "already gone")
            continue
        reasons = []
        if activity.get(lid):
            reasons.append("activity: " + ", ".join(f"{t} ({n})" for t, n in
                                                    sorted(activity[lid].items())))
        if (lead.status or "new") != "new":
            reasons.append(f"status changed to '{lead.status}'")
        if lead.assigned_to_id:
            reasons.append("assigned to a user")
        if lead.notes:
            reasons.append("has notes")
        if lead.tier:
            reasons.append("tier set after import")
        if lid in later:
            reasons.append("updated by a later import")
        lead_removable[lid] = (not reasons, "; ".join(reasons))

    for v in versions:
        before = _j(v.before_json, {})
        after = _j(v.after_json, {})
        if v.target_type == "lead":
            if v.action == "created":
                ok, why = lead_removable.get(v.target_id, (False, "unknown"))
                items.append({"version_id": v.id, "target_type": "lead",
                              "target_id": v.target_id, "action": v.action,
                              "outcome": REMOVE if ok else KEEP,
                              "reason": "untouched since import" if ok else why})
            else:
                lead = db.query(Lead).filter(Lead.id == v.target_id,
                                             Lead.organization_id == batch.organization_id).first()
                items.append(_restore_item(v, lead, before, after))
        else:
            contact = db.query(OrgContact).filter(
                OrgContact.id == v.target_id,
                OrgContact.organization_id == batch.organization_id).first()
            if v.action == "created":
                if contact is None:
                    items.append({"version_id": v.id, "target_type": "org_contact",
                                  "target_id": v.target_id, "action": v.action,
                                  "outcome": KEEP, "reason": "already gone"})
                    continue
                reasons = []
                if _j(contact.manually_edited_fields, []):
                    reasons.append("edited by a person")
                if contact.id in later:
                    reasons.append("updated by a later import")
                if contact.lead_id and contact.lead_id in lead_removable:
                    ok, why = lead_removable[contact.lead_id]
                    if not ok:
                        reasons.append("its lead is being kept" + (f" ({why})" if why else ""))
                # A contact this batch created for a lead that PREDATES the
                # batch is removable: the lead stays, and its link back to the
                # contact is restored by that lead's own "updated" version.
                items.append({"version_id": v.id, "target_type": "org_contact",
                              "target_id": v.target_id, "action": v.action,
                              "outcome": ARCHIVE if reasons else REMOVE,
                              "reason": "; ".join(reasons) or "untouched since import"})
            else:
                items.append(_restore_item(v, contact, before, after))

    summary = defaultdict(int)
    for it in items:
        summary[f"{it['target_type']}:{it['outcome']}"] += 1
    safe = all(it["outcome"] in (REMOVE, RESTORE) for it in items)
    return {"batch_id": batch.id, "batch_code": batch.batch_code,
            "fully_reversible": safe, "summary": dict(summary),
            "items": items, "item_count": len(items)}


def _restore_item(v, obj, before, after) -> dict:
    base = {"version_id": v.id, "target_type": v.target_type, "target_id": v.target_id,
            "action": v.action}
    if obj is None:
        return base | {"outcome": KEEP, "reason": "record no longer exists"}
    restorable, conflicts = [], []
    for f, old in before.items():
        cur = getattr(obj, f, None)
        if f in after and _differs(cur, after.get(f)):
            conflicts.append(f)
        else:
            restorable.append(f)
    if conflicts and not restorable:
        return base | {"outcome": KEEP, "fields": [],
                       "reason": "changed again after import: " + ", ".join(conflicts)}
    return base | {"outcome": RESTORE, "fields": restorable,
                   "reason": ("restores " + ", ".join(restorable)
                              + (f"; keeps {', '.join(conflicts)} (changed since)"
                                 if conflicts else ""))}


def execute(db: Session, batch: ImportBatch, ctx) -> dict:
    from app.models.models import Lead
    from app.services.intake import audit
    p = plan(db, batch)
    now = datetime.utcnow()
    counts = defaultdict(int)
    versions = {v.id: v for v in (db.query(ImportRecordVersion)
                                  .filter(ImportRecordVersion.batch_id == batch.id,
                                          ImportRecordVersion.organization_id
                                          == batch.organization_id).all())}
    # Order: restores first, then contacts, then leads (contacts reference leads).
    order = {RESTORE: 0, ARCHIVE: 1, KEEP: 1, REMOVE: 2}
    items = sorted(p["items"], key=lambda it: (order[it["outcome"]],
                                               0 if it["target_type"] == "org_contact" else 1))
    for it in items:
        v = versions.get(it["version_id"])
        if v is None:
            continue
        model = Lead if it["target_type"] == "lead" else OrgContact
        obj = db.query(model).filter(model.id == it["target_id"],
                                     model.organization_id == batch.organization_id).first()
        outcome = it["outcome"]
        if outcome == RESTORE and obj is not None:
            before = _j(v.before_json, {})
            for f in it.get("fields", []):
                val = before.get(f)
                col = getattr(model, f, None)
                if val is not None and col is not None and "DateTime" in type(
                        col.property.columns[0].type).__name__:
                    try:
                        val = datetime.fromisoformat(val)
                    except Exception:  # noqa: BLE001
                        pass
                setattr(obj, f, val)
        elif outcome == REMOVE and obj is not None:
            if it["target_type"] == "org_contact":
                db.query(OrgContactSourceId).filter(
                    OrgContactSourceId.org_contact_id == obj.id).delete(synchronize_session=False)
                db.delete(obj)
            else:
                db.query(OrgContact).filter(OrgContact.lead_id == obj.id,
                                            OrgContact.organization_id == batch.organization_id
                                            ).update({"lead_id": None}, synchronize_session=False)
                try:
                    from app.models.master_contact_models import LeadOccurrence
                    db.query(LeadOccurrence).filter(
                        LeadOccurrence.organization_id == batch.organization_id,
                        LeadOccurrence.lead_id == obj.id).delete(synchronize_session=False)
                except Exception:  # noqa: BLE001
                    log.exception("could not remove master occurrence for lead %s", obj.id)
                db.delete(obj)
        elif outcome == ARCHIVE and obj is not None:
            obj.lifecycle = ContactLifecycle.ARCHIVED
            obj.archived_at = now
            obj.archived_reason = f"rollback of import {batch.batch_code}: {it['reason']}"[:250]
        elif outcome == KEEP and obj is not None and it["target_type"] == "lead":
            # Detach: the lead stays, and says it outlived its batch's rollback.
            pass
        v.rolled_back_at = now
        v.rollback_outcome = outcome
        v.rollback_note = it["reason"][:500]
        counts[f"{it['target_type']}:{outcome}"] += 1
        db.flush()

    (db.query(ImportStagedRow)
     .filter(ImportStagedRow.batch_id == batch.id,
             ImportStagedRow.organization_id == batch.organization_id,
             ImportStagedRow.commit_action.isnot(None))
     .update({"commit_action": ImportStagedRow.commit_action + ":rolled_back"},
             synchronize_session=False))
    fully = all(it["outcome"] in (REMOVE, RESTORE) for it in p["items"])
    batch.status = (ImportBatchStatus.ROLLED_BACK if fully
                    else ImportBatchStatus.PARTIALLY_ROLLED_BACK)
    batch.rolled_back_at = now
    batch.rolled_back_by_id = ctx.actor_id
    report = {"summary": dict(counts), "fully_reversible": fully,
              "kept_or_archived": [it for it in p["items"]
                                   if it["outcome"] in (KEEP, ARCHIVE)][:500]}
    batch.rollback_report_json = json.dumps(report, default=str)
    audit.record(db, ctx, "intake.batch_rolled_back", batch.id,
                 {"summary": dict(counts), "fully_reversible": fully})
    db.commit()
    return report
