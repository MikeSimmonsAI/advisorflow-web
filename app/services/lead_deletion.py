"""Deleting a Lead without breaking the database or the compliance record.

WHY THIS EXISTS. `DELETE /leads/{id}` used to be `db.delete(lead)`, relying on
the database to cascade. Several tables reference `leads.id` WITHOUT a cascade
(voice_calls and pipeline_conversations are NOT NULL; crm_contacts,
notifications, contact_registry, provider_transactions, wholesale deals and
leads.duplicate_of_lead_id are nullable) and older production tables may lack
the ON DELETE clauses the models declare. Any such row made the delete raise an
IntegrityError - a 500 the browser reported as "Unable to reach the server".

THE POLICY, table by table, is derived from the schema rather than a hand list,
so a table added later is handled by the same rule:
  * a nullable reference is DETACHED (set to NULL): the other record - a CRM
    contact, a consent record, a billing transaction, an org contact, an import
    row - is its own fact and outlives the lead;
  * a NOT NULL reference is lead-owned history (messages, replies, calls,
    pipeline conversations, cadence state, notes...) and is deleted with it,
    exactly as the declared cascades already did - recursively, so rows that
    point at those rows are handled by the same two rules;
  * a lead that is the SELLER on a wholesale deal is refused (409): removing it
    would silently orphan a deal, which is a business decision, not a delete.

COMPLIANCE SURVIVES THE DELETE. An opt-out must not disappear with the lead
(re-importing the number would make it contactable again), so a lead that is
DNC, opted out, or flagged remove_all has its phone written to the suppression
list before anything is removed. The audit entry is written in the same
transaction.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import Table
from sqlalchemy.orm import Session

from app.models.models import Base, Lead


class LeadDeleteRefused(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


# Referencing columns that must not be detached or deleted silently.
_REFUSE = {("wholesale_deals", "seller_lead_id"):
           "This lead is the seller on a wholesale deal. Close or reassign the deal first."}

_MAX_DEPTH = 4


def _referencing(table: Table) -> List[Tuple[Table, object]]:
    """(child_table, fk_column) for every FK in the registry pointing at `table`'s PK."""
    out = []
    pk_cols = {c for c in table.primary_key.columns}
    for t in Base.metadata.tables.values():
        for fk in t.foreign_keys:
            if fk.column in pk_cols and fk.column.table is table:
                out.append((t, fk.parent))
    return out


def _pk_col(table: Table):
    cols = list(table.primary_key.columns)
    return cols[0] if len(cols) == 1 else None


def _clear(db: Session, table: Table, ids: List, depth: int, counts: Dict[str, int],
           visiting: Set[str]) -> None:
    """Detach or delete every row that references rows `ids` of `table`."""
    if not ids or depth > _MAX_DEPTH:
        return
    for child, col in _referencing(table):
        key = (child.name, col.name)
        if child.name in visiting and child is not table:
            continue
        if key in _REFUSE:
            continue  # checked up front for the lead itself
        if col.nullable:
            res = db.execute(child.update().where(col.in_(ids)).values({col.name: None}))
            if res.rowcount:
                counts["detached:%s.%s" % key] = counts.get("detached:%s.%s" % key, 0) + res.rowcount
            continue
        # NOT NULL: lead-owned rows. Clear what points at THEM first.
        pk = _pk_col(child)
        if pk is not None:
            child_ids = [r[0] for r in db.execute(child.select().with_only_columns(pk).where(col.in_(ids)))]
            if child_ids:
                _clear(db, child, child_ids, depth + 1, counts, visiting | {child.name})
        if child is table:
            continue  # a self reference that is NOT NULL cannot exist for leads
        res = db.execute(child.delete().where(col.in_(ids)))
        if res.rowcount:
            counts["deleted:%s" % child.name] = counts.get("deleted:%s" % child.name, 0) + res.rowcount


def _preserve_opt_out(db: Session, lead: Lead) -> Optional[str]:
    opted_out = (lead.status == "dnc"
                 or getattr(lead, "manual_flag", None) == "remove_all")
    if not opted_out or not lead.phone:
        return None
    from app.services import compliance_service
    try:
        entry = compliance_service.add_suppression_entry(
            db, lead.organization_id, lead.phone,
            reason="Preserved when the lead was deleted (it was DNC / opted out).")
        return entry.phone
    except ValueError:
        return None  # not a usable US number - nothing a suppression could match


def delete_lead(db: Session, lead: Lead, actor_user_id: str) -> Dict:
    """Delete one lead safely. Raises LeadDeleteRefused for a business block."""
    for (tname, cname), why in _REFUSE.items():
        t = Base.metadata.tables.get(tname)
        if t is None:
            continue
        hit = db.execute(t.select().with_only_columns(t.c[cname]).where(t.c[cname] == lead.id).limit(1)).first()
        if hit:
            raise LeadDeleteRefused(why)

    lead_id, org_id = lead.id, lead.organization_id
    suppressed = _preserve_opt_out(db, lead)
    counts: Dict[str, int] = {}
    leads = Base.metadata.tables["leads"]
    _clear(db, leads, [lead_id], 0, counts, {"leads"})

    from app.routers.audit_log_router import log_action
    log_action(db, org_id, actor_user_id, action="lead.delete",
               target_type="lead", target_id=lead_id,
               details={"related": counts, "suppression_preserved": bool(suppressed)},
               commit=False)
    db.expunge(lead)
    db.execute(leads.delete().where(leads.c.id == lead_id))
    db.commit()
    return {"deleted": True, "id": lead_id, "related": counts,
            "suppression_preserved": bool(suppressed)}
