"""WHOLESALE TEST-DATA CLEANUP - the same preview / confirm / receipt discipline
as data_cleanup.py (leads), extended to the Wholesale and EvoSense tables.

WHAT IS SYNTHETIC. Only rows a person or the system already flagged as test:
`is_test = TRUE` on a Wholesale property, deal, buyer, seller profile, an
EvoSense property / owner / person / contact point / event, and a lead that
only such rows point at. Nothing is inferred from a name, an address or a
phone pattern - those are reported, never used to delete.

KEPT ON PURPOSE, even though flagged test:
  * a row whose note says it is kept as production evidence
    ("... kept as production verification evidence"), and
  * a property whose deal carries a SIGNED document (an e-signature record is
    an audit trail).
They stay flagged test, so every normal screen keeps hiding them.

DEPENDENT ROWS are found from the database's own foreign keys (every model in
the registry), children before parents, so nothing is deleted that the
preview did not count. Rows with an ON DELETE SET NULL link are not deleted
(the database clears the link). If any dependent row is a REAL record - a
Wholesale / EvoSense / lead row with is_test = FALSE, or any organization or
user - the whole plan is refused. Accounts, permissions, organizations and
the audit log are never in a plan.

BACKUP. Preview stores, in the cleanup receipt (cleanup_executions), the full
content of every row the plan would delete - every column, as JSON. Execute
re-checks that the set is unchanged, saves the receipt BEFORE the first delete,
then deletes. A failure rolls the delete back and the receipt says so.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

RULE = "wholesale_is_test"
KEEP_NOTE = "kept as production verification evidence"

# Roots: (table, extra SQL condition). Every root row must be is_test = TRUE.
ROOTS = (
    "wholesale_properties", "wholesale_deals", "wholesale_buyers", "wholesale_seller_profiles",
    "evosense_properties", "evosense_owners", "evosense_persons", "evosense_contact_points",
    "evosense_events",
)
# A dependent row in one of these tables must itself be flagged test, or the plan is refused.
GUARDED = {"wholesale_properties", "wholesale_deals", "wholesale_buyers", "wholesale_seller_profiles",
           "leads", "evosense_properties", "evosense_strategies", "evosense_owners", "evosense_persons"}
NEVER = {"organizations", "users", "audit_log_entries", "audit_logs", "cleanup_executions", "platforms",
         "memberships", "org_memberships", "user_organizations"}


def _metadata():
    import app.models.registry  # noqa: F401 - every model module, so every foreign key is known
    from app.models.models import Base
    return Base.metadata


def _jsonable(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        return None
    return v


def _rows(db: Session, table: str, col: str, ids: List[Any]) -> List[Dict[str, Any]]:
    if not ids:
        return []
    out = []
    for i in range(0, len(ids), 500):
        q = text("SELECT * FROM %s WHERE %s IN :ids" % (table, col)).bindparams(bindparam("ids", expanding=True))
        out += [{k: _jsonable(v) for k, v in dict(r._mapping).items()} for r in db.execute(q, {"ids": ids[i:i + 500]})]
    return out


def _has_col(md, table: str, col: str) -> bool:
    t = md.tables.get(table)
    return t is not None and col in t.c


def _kept_property_ids(db: Session) -> Set[str]:
    kept = {r[0] for r in db.execute(text(
        "SELECT id FROM wholesale_properties WHERE is_test = :t AND lower(coalesce(test_note,'')) LIKE :k"),
        {"t": True, "k": "%" + KEEP_NOTE + "%"})}
    kept |= {r[0] for r in db.execute(text(
        "SELECT d.property_id FROM wholesale_deals d JOIN wholesale_documents doc ON doc.deal_id = d.id "
        "WHERE lower(coalesce(doc.status,'')) = 'signed'"))}
    return kept


def _root_ids(db: Session, md, org_ids: Optional[List[str]]) -> Dict[str, List[Any]]:
    kept_props = _kept_property_ids(db)
    out: Dict[str, List[Any]] = {}
    for t in ROOTS:
        if t not in md.tables or not _has_col(md, t, "is_test"):
            continue
        sql = "SELECT id FROM %s WHERE is_test = :t" % t
        params: Dict[str, Any] = {"t": True}
        if org_ids and _has_col(md, t, "organization_id"):
            sql += " AND organization_id IN :orgs"
        q = text(sql)
        if org_ids and _has_col(md, t, "organization_id"):
            q = q.bindparams(bindparam("orgs", expanding=True))
            params["orgs"] = org_ids
        ids = [r[0] for r in db.execute(q, params)]
        if t == "wholesale_properties":
            ids = [i for i in ids if i not in kept_props]
        elif t == "wholesale_deals":
            kept_deals = {r[0] for r in db.execute(text("SELECT id FROM wholesale_deals WHERE property_id IN :p")
                                                    .bindparams(bindparam("p", expanding=True)),
                                                    {"p": list(kept_props) or ["-"]})}
            ids = [i for i in ids if i not in kept_deals]
        elif t == "wholesale_seller_profiles":
            ids = [i for i in ids if i not in {r[0] for r in db.execute(
                text("SELECT id FROM wholesale_seller_profiles WHERE property_id IN :p")
                .bindparams(bindparam("p", expanding=True)), {"p": list(kept_props) or ["-"]})}]
        out[t] = ids
    return out


def _lead_ids(db: Session, md, plan: Dict[str, Set[Any]]) -> List[Any]:
    """Test leads that only test rows point at (a deal's seller, a seller
    profile, an EvoSense-promoted owner). A lead any surviving row still
    points at stays."""
    cand: Set[Any] = set()
    deals = list(plan.get("wholesale_deals") or [])
    profs = list(plan.get("wholesale_seller_profiles") or [])
    for t, col, ids in (("wholesale_deals", "seller_lead_id", deals), ("wholesale_seller_profiles", "lead_id", profs)):
        for r in _rows(db, t, "id", ids):
            if r.get(col):
                cand.add(r[col])
    if not cand:
        return []
    test = {r["id"] for r in _rows(db, "leads", "id", list(cand)) if r.get("is_test")}
    # still used by a surviving deal / profile?
    used = set()
    for t, col in (("wholesale_deals", "seller_lead_id"), ("wholesale_seller_profiles", "lead_id")):
        q = text("SELECT id, %s FROM %s WHERE %s IN :l" % (col, t, col)).bindparams(bindparam("l", expanding=True))
        for rid, lid in db.execute(q, {"l": list(test)}):
            if rid not in plan.get(t, set()):
                used.add(lid)
    return sorted(test - used)


def _children(md) -> Dict[str, List[Tuple[str, str, str]]]:
    """parent table -> [(child table, child column, ondelete)]"""
    out: Dict[str, List[Tuple[str, str, str]]] = {}
    for t in md.tables.values():
        for fk in t.foreign_keys:
            parent = fk.column.table.name
            if fk.column.name != "id":
                continue
            out.setdefault(parent, []).append((t.name, fk.parent.name, (fk.ondelete or "NO ACTION").upper()))
    return out


def build_plan(db: Session, org_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    md = _metadata()
    kids = _children(md)
    plan: Dict[str, Set[Any]] = {t: set(ids) for t, ids in _root_ids(db, md, org_ids).items()}
    leads = _lead_ids(db, md, plan)
    if leads:
        plan["leads"] = set(leads)
    refused: List[str] = []
    # closure over foreign keys, breadth first
    frontier = [(t, set(ids)) for t, ids in plan.items() if ids]
    while frontier:
        nxt = []
        for parent, ids in frontier:
            for child, col, ondelete in kids.get(parent, []):
                if child in NEVER:
                    rows = _rows(db, child, col, list(ids))
                    if rows:
                        refused.append("%d %s row(s) point at %s being deleted" % (len(rows), child, parent))
                    continue
                if ondelete == "SET NULL":
                    continue
                rows = _rows(db, child, col, list(ids))
                if not rows:
                    continue
                new = {r["id"] for r in rows if "id" in r} - plan.get(child, set())
                if child in GUARDED:
                    real = [r for r in rows if r.get("id") in new and not r.get("is_test")]
                    if real:
                        refused.append("%d real %s row(s) depend on %s: %s" % (
                            len(real), child, parent, ", ".join(str(r["id"]) for r in real[:5])))
                        continue
                if new:
                    plan.setdefault(child, set()).update(new)
                    nxt.append((child, new))
        frontier = nxt
    # deletion order: a table before any table it references (children first)
    order = _delete_order(md, [t for t, ids in plan.items() if ids])
    manifest = {t: sorted((r for r in _rows(db, t, "id", sorted(plan[t], key=str))), key=lambda r: str(r.get("id")))
                for t in order}
    counts = {t: len(manifest[t]) for t in order}
    total = sum(counts.values())
    digest = hashlib.sha256(json.dumps({t: sorted(map(str, plan[t])) for t in order}, sort_keys=True)
                            .encode()).hexdigest()[:16]
    return {"rule": RULE, "order": order, "counts": counts, "total": total, "ids": {t: sorted(map(str, plan[t]))
                                                                                   for t in order},
            "manifest": manifest, "digest": digest, "refused": refused,
            "kept": _kept_report(db),
            "confirmation_phrase": "DELETE %d WHOLESALE TEST RECORDS" % total}


def _delete_order(md, tables: List[str]) -> List[str]:
    sorted_all = [t.name for t in md.sorted_tables]          # parents before children
    rank = {n: i for i, n in enumerate(sorted_all)}
    return sorted(tables, key=lambda t: -rank.get(t, 0))     # children first


def _kept_report(db: Session) -> List[Dict[str, Any]]:
    out = []
    for pid in sorted(_kept_property_ids(db)):
        r = _rows(db, "wholesale_properties", "id", [pid])
        if r and r[0].get("is_test"):
            out.append({"id": pid, "street_address": r[0].get("street_address"), "test_note": r[0].get("test_note"),
                        "why": "production evidence note" if KEEP_NOTE in (r[0].get("test_note") or "").lower()
                        else "a signed document (e-signature audit trail)"})
    return out


def preview(db: Session, actor, *, org_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """What WOULD go, saved as a receipt with the full backup. Deletes nothing."""
    from app.models.cleanup_models import CleanupExecution, STATUS_PREVIEWED
    plan = build_plan(db, org_ids)
    row = CleanupExecution(
        actor_user_id=actor.id, actor_email=getattr(actor, "email", None), status=STATUS_PREVIEWED,
        rules=json.dumps([RULE]), org_ids=json.dumps(org_ids or []), import_batches=json.dumps([]),
        confirmation_phrase=plan["confirmation_phrase"],
        target_lead_ids=json.dumps({"digest": plan["digest"], "ids": plan["ids"]}),
        manifest=json.dumps({"backup": plan["manifest"], "kept": plan["kept"], "refused": plan["refused"]}),
        expected_counts=json.dumps(plan["counts"]), expected_total=plan["total"],
        excluded=json.dumps(sorted(NEVER)))
    db.add(row)
    db.commit()
    return {"execution_id": row.id, "counts": plan["counts"], "total": plan["total"], "refused": plan["refused"],
            "kept": plan["kept"], "confirmation_phrase": plan["confirmation_phrase"], "ids": plan["ids"],
            "backup_rows": sum(len(v) for v in plan["manifest"].values())}


def execute(db: Session, actor, *, execution_id: str, confirmation: str) -> Dict[str, Any]:
    from app.models.cleanup_models import CleanupExecution, STATUS_FAILED, STATUS_SUCCEEDED, STATUS_SUPERSEDED
    from app.routers.audit_log_router import log_action
    row = db.query(CleanupExecution).filter(CleanupExecution.id == execution_id).first()
    if row is None or row.actor_user_id != actor.id or json.loads(row.rules or "[]") != [RULE]:
        raise HTTPException(status_code=404, detail="Cleanup plan not found")
    if row.status != "previewed":
        raise HTTPException(status_code=409, detail="This cleanup plan is already %s." % row.status)
    org_ids = json.loads(row.org_ids or "[]") or None
    plan = build_plan(db, org_ids)
    if plan["refused"]:
        raise HTTPException(status_code=409, detail={"message": "Refused: real records would be touched.",
                                                     "refused": plan["refused"]})
    if plan["digest"] != json.loads(row.target_lead_ids).get("digest"):
        row.status = STATUS_SUPERSEDED
        row.error = "The matching records changed after the preview. Nothing was deleted; preview again."
        db.commit()
        raise HTTPException(status_code=409, detail=row.error)
    if (confirmation or "").strip() != row.confirmation_phrase:
        raise HTTPException(status_code=400, detail="Confirmation does not match. Type exactly: %s"
                            % row.confirmation_phrase)
    row.confirmation_received = confirmation
    db.commit()                                     # the receipt and its backup are on disk first
    deleted: Dict[str, int] = {}
    try:
        for t in plan["order"]:
            ids = plan["ids"][t]
            n = 0
            for i in range(0, len(ids), 500):
                q = text("DELETE FROM %s WHERE id IN :ids" % t).bindparams(bindparam("ids", expanding=True))
                n += db.execute(q, {"ids": ids[i:i + 500]}).rowcount or 0
            deleted[t] = n
        log_action(db, None, actor.id, action="data_cleanup.executed", target_type="cleanup", target_id=row.id,
                   details={"execution_id": row.id, "rules": [RULE], "deleted": deleted,
                            "confirmation": row.confirmation_phrase},
                   note="Wholesale / EvoSense test-data cleanup. Full rows saved in the receipt before deletion. "
                        "Organizations, users, permissions and the audit log were not touched.", commit=False)
        row.status = STATUS_SUCCEEDED
        row.executed_at = datetime.utcnow()
        row.actual_counts = json.dumps(deleted)
        row.actual_total = sum(deleted.values())
        db.commit()
    except Exception as exc:  # noqa: BLE001 - recorded, rolled back
        db.rollback()
        fresh = db.query(CleanupExecution).filter(CleanupExecution.id == row.id).first()
        fresh.status = STATUS_FAILED
        fresh.executed_at = datetime.utcnow()
        fresh.actual_counts = json.dumps({})
        fresh.actual_total = 0
        fresh.error = "%s: %s" % (type(exc).__name__, str(exc)[:900])
        db.commit()
        raise HTTPException(status_code=500, detail={"message": "The cleanup failed and was rolled back. Nothing "
                                                                "was deleted.", "execution_id": row.id,
                                                     "error": fresh.error[:300]})
    return {"execution_id": row.id, "status": row.status, "deleted": deleted, "total_deleted": row.actual_total,
            "expected_total": row.expected_total, "counts_match": row.actual_total == row.expected_total}


def backup(db: Session, actor, execution_id: str) -> Dict[str, Any]:
    from app.models.cleanup_models import CleanupExecution
    row = db.query(CleanupExecution).filter(CleanupExecution.id == execution_id).first()
    if row is None or json.loads(row.rules or "[]") != [RULE]:
        raise HTTPException(status_code=404, detail="Cleanup plan not found")
    return {"execution_id": row.id, "status": row.status, "created_at": row.created_at.isoformat() + "Z",
            "expected_counts": json.loads(row.expected_counts or "{}"), "manifest": json.loads(row.manifest or "{}")}
