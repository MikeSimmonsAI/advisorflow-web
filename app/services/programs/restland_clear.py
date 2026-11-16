"""RESTLAND WORKSPACE RESET - CLEAR THE OLD CUSTOMER DATA (Issue #507, Nov 15).

Owner-authorized reset of the Restland workspace (org 01fed629-...). Runs only
after the do-not-contact preservation (restland_reset.preserve) and its proof
(restland_reset.verify) pass in the same run - otherwise nothing is deleted.

    WORKSPACE_RESET="<restland org>:clear:<run key>"

  * Every row removed is first copied, whole, into workspace_reset_items
    (entity = table, before_json = the row) under this run: the private,
    scoped restore point, kept in the database, never in the repository.
  * Table by table, children before parents, each step in its own transaction
    with its copy, so a failure stops the run where it is and a re-run with the
    same key carries on with only what is left (nothing broader, nothing sent).
  * Scope: rows tied to Restland's own contacts (lead_id / their links), plus
    Restland's duplicate registry. Nothing of any other organization.

KEPT: the workspace and its program record, staff and logins, sender and
inbox connections, the calendar connection and booking settings, campaign
templates (all switched off), cadence templates, and every do-not-contact
record (suppression_entries, contact_suppressions). No message is sent, no
calendar event is touched (bookings are removed only from this database).
"""
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.models import Lead, Organization
from app.models.reset_models import WorkspaceResetRun
from app.services.programs import restland_reset as RR

MODE = "clear"
L = "(select id from leads where organization_id = :org)"

# (table, WHERE clause selecting the Restland rows). Children before parents.
STEPS: List[Tuple[str, str]] = [
    ("reply_states", "reply_id in (select id from replies where lead_id in %s)" % L),
    ("auto_send_candidates", "lead_id in %s" % L),
    ("auto_sent_log", "lead_id in %s" % L),
    ("auto_send_queue", "lead_id in %s" % L),
    ("survey_responses", "lead_id in %s or booking_followup_id in (select id from booking_followups where lead_id in %s)" % (L, L)),
    ("booking_followups", "lead_id in %s or booking_link_id in (select id from booking_links where lead_id in %s)" % (L, L)),
    ("lead_outcomes", "lead_id in %s or booking_link_id in (select id from booking_links where lead_id in %s)" % (L, L)),
    ("appointment_notifications", "lead_id in %s" % L),
    ("appointment_surveys", "lead_id in %s" % L),
    ("booking_page_events", "lead_id in %s" % L),
    ("roofing_stage_events", "lead_id in %s" % L),
    ("roofing_inspection_events", "inspection_id in (select id from roofing_inspections where lead_id in %s)" % L),
    ("roofing_inspection_photos", "lead_id in %s" % L),
    ("roofing_inspections", "lead_id in %s" % L),
    ("roofing_opportunities", "lead_id in %s" % L),
    ("messages", "lead_id in %s or booking_link_id in (select id from booking_links where lead_id in %s)" % (L, L)),
    ("jewelry_appointment_details", "booking_link_id in (select id from booking_links where lead_id in %s)" % L),
    ("booking_links", "lead_id in %s" % L),
    ("email_provider_events", "lead_id in %s" % L),
    ("email_messages", "lead_id in %s" % L),
    ("replies", "lead_id in %s" % L),
    ("outreach_text_slots", "organization_id = :org"),
    ("cadence_touch_logs", "lead_id in %s" % L),
    ("cadence_states", "lead_id in %s" % L),
    ("program_email_touches", "lead_id in %s" % L),
    ("program_responses", "lead_id in %s" % L),
    ("program_alerts", "lead_id in %s" % L),
    ("program_conversation_turns", "lead_id in %s" % L),
    ("program_conversations", "lead_id in %s" % L),
    ("program_handoff_learnings", "lead_id in %s" % L),
    ("program_lead_files", "lead_id in %s" % L),
    ("contact_verifications", "source_record_id in (select id from program_source_records where organization_id = :org or lead_id in %s)" % L),
    ("program_source_records", "organization_id = :org or lead_id in %s" % L),
    ("program_import_runs", "organization_id = :org"),
    ("campaign_cycle_members", "organization_id = :org"),
    ("campaign_audience_members", "organization_id = :org"),
    ("lead_dispositions", "lead_id in %s" % L),
    ("pipeline_conversations", "lead_id in %s" % L),
    ("conversation_memory_items", "lead_id in %s" % L),
    ("conversation_states", "lead_id in %s" % L),
    ("crm_contact_notes", "contact_id in (select id from crm_contacts where organization_id = :org or lead_id in %s)" % L),
    ("crm_notes", "contact_id in (select id from crm_contacts where organization_id = :org or lead_id in %s)" % L),
    ("crm_contacts", "organization_id = :org or lead_id in %s" % L),
    ("voice_calls", "lead_id in %s" % L),
    ("voicemails", "lead_id in %s" % L),
    ("inbound_call_logs", "lead_id in %s" % L),
    ("notifications", "lead_id in %s" % L),
    ("integration_request_logs", "lead_id in %s" % L),
    ("inbound_mailbox_messages", "lead_id in %s" % L),
    ("provider_transactions", "lead_id in %s" % L),
    ("lead_notes", "lead_id in %s" % L),
    ("lead_tasks", "lead_id in %s" % L),
    ("lead_occurrences", "lead_id in %s" % L),
    ("appointment_case_files", "organization_id = :org or lead_id in %s" % L),
    ("sms_recovery_events", "lead_id in %s" % L),
    ("sms_recovery_items", "lead_id in %s" % L),
    ("lead_referrals", "source_lead_id in %s or referred_lead_id in %s" % (L, L)),
    ("contact_registry", "organization_id = :org"),
]
# Rows of other programs (or other tenants) that point at a Restland contact or
# booking. Deleting would cascade-remove or blank them with no copy, so any such
# row makes the run refuse rather than guess. All are expected to be zero.
B = "(select id from booking_links where lead_id in %s)" % L
FOREIGN_GUARDS: List[Tuple[str, str]] = [
    (t, c) for t in ("agency_applications", "agency_appointments", "agency_assignments", "agency_client_profiles",
                     "agency_copilot_events", "agency_policies", "agency_prospect_profiles", "agency_renewal_events",
                     "agency_renewals", "agency_tasks", "jewelry_item_interests", "jewelry_transactions",
                     "jewelry_ai_exchanges", "wholesale_conversation_controls", "wholesale_seller_callbacks",
                     "wholesale_seller_profiles", "wholesale_temperature_overrides", "wholesale_enrichment_requests",
                     "wholesale_field_appointments", "energy_move_requests", "energy_request_alerts",
                     "org_contacts", "sms_consent_records", "acquisition_opportunities") for c in ("lead_id",)
] + [("wholesale_deals", "seller_lead_id"), ("import_staged_rows", "matched_lead_id"),
     ("import_staged_rows", "committed_lead_id"), ("import_staged_rows", "merged_into_lead_id"),
     ("acquisition_opportunities", "booking_link_id")]


class ClearRefused(ValueError):
    pass


def _exists(db: Session, table: str, col: Optional[str] = None) -> bool:
    q = "select 1 from information_schema.columns where table_schema='public' and table_name=:t"
    p = {"t": table}
    if col:
        q += " and column_name=:c"
        p["c"] = col
    return db.execute(text(q + " limit 1"), p).first() is not None


def _top_level(where: str) -> str:
    """The clause with every parenthesised part removed: only the table's own columns remain."""
    out, depth = [], 0
    for ch in where:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _cols_ok(db: Session, table: str, where: str) -> bool:
    import re
    if not _exists(db, table):
        return False
    top = _top_level(where)
    cols = set(re.findall(r"\b([a-z_]+)\s+(?:in|=)\s", top))
    return all(_exists(db, table, c) for c in cols)


def inventory(db: Session, org_id: str) -> Dict[str, int]:
    out = {"leads": db.query(Lead).filter(Lead.organization_id == org_id).count()}
    for t, where in STEPS:
        if _cols_ok(db, t, where):
            out[t] = db.execute(text("select count(*) from %s where %s" % (t, where)), {"org": org_id}).scalar()
    return {k: v for k, v in out.items() if v}


def _backup_and_delete(db: Session, run_id: str, org_id: str, table: str, where: str) -> int:
    has_id = _exists(db, table, "id")
    ident = "x.id::text" if has_id else "md5(row_to_json(x)::text)"
    db.execute(text(
        "insert into workspace_reset_items (id, run_id, organization_id, entity, entity_id, action, before_json, "
        "reason, created_at) select gen_random_uuid()::text, :run, :org, :ent, %s, 'delete', row_to_json(x)::text, "
        "'Restland reset (#507): restore point', now() at time zone 'utc' from %s x where %s"
        % (ident, table, where)), {"run": run_id, "org": org_id, "ent": table})
    n = db.execute(text("delete from %s where %s" % (table, where)), {"org": org_id}).rowcount
    return n or 0


def clear(db: Session, org: Organization, run_key: str, *, now: Optional[datetime] = None) -> Dict:
    RR._guard(db, org)
    now = now or datetime.utcnow()
    run = db.query(WorkspaceResetRun).filter_by(organization_id=org.id, run_key=run_key, mode=MODE).first()
    if run is not None and run.status == "applied":
        return {"run_id": run.id, "already": True, "summary": json.loads(run.summary_json or "{}")}
    if run is None:
        run = WorkspaceResetRun(organization_id=org.id, run_key=run_key, mode=MODE, status="started")
        db.add(run)
        db.commit()
    summ = json.loads(run.summary_json or "{}") or {"before": inventory(db, org.id)}
    # 0. Outreach must already be off; every Restland campaign stays off.
    from app.models.program_models import CampaignFamily, WorkspaceSwitch
    for f in db.query(CampaignFamily).filter_by(organization_id=org.id).all():
        f.is_active = False
    sw = db.query(WorkspaceSwitch).filter_by(organization_id=org.id).first()
    if sw is not None:
        sw.texts_on = False
    db.commit()
    # 1. Late opt-outs captured, then proved - or nothing is deleted.
    summ["dnc"] = RR.preserve(db, org)
    proof = RR.verify(db, org)
    summ["dnc_proof"] = proof
    if not proof["ok"]:
        run.status, run.summary_json = "refused", json.dumps(summ, default=str)
        db.commit()
        raise ClearRefused("Do-not-contact proof failed - nothing deleted: %s" % proof["failures"])
    for t, c in FOREIGN_GUARDS:
        if _exists(db, t, c):
            n = db.execute(text("select count(*) from %s where %s in %s" % (t, c, B if c == "booking_link_id" else L)),
                           {"org": org.id}).scalar()
            if n:
                run.status, run.summary_json = "refused", json.dumps(summ, default=str)
                db.commit()
                raise ClearRefused("%d %s rows belong to Restland contacts - refusing to guess." % (n, t))
    # 2. Table by table, copy then delete; each step its own transaction.
    done = summ.setdefault("deleted", {})
    for t, where in STEPS:
        if not _cols_ok(db, t, where):
            continue
        try:
            n = _backup_and_delete(db, run.id, org.id, t, where)
            done[t] = done.get(t, 0) + n
            run.summary_json = json.dumps(summ, default=str)
            db.commit()
        except Exception as e:                           # noqa: BLE001 - stop here; a re-run resumes
            db.rollback()
            summ["failed_at"] = {"table": t, "error": str(e)[:300]}
            run.status, run.summary_json = "partial", json.dumps(summ, default=str)
            db.commit()
            return {"run_id": run.id, "already": False, "status": "partial", "summary": summ}
    db.commit()
    # 3. The contacts themselves (their duplicate links first).
    try:
        db.execute(text("update leads set duplicate_of_lead_id = null where organization_id = :org"), {"org": org.id})
        done["leads"] = done.get("leads", 0) + _backup_and_delete(db, run.id, org.id, "leads", "organization_id = :org")
        db.commit()
    except Exception as e:                               # noqa: BLE001
        db.rollback()
        summ["failed_at"] = {"table": "leads", "error": str(e)[:300]}
        run.status, run.summary_json = "partial", json.dumps(summ, default=str)
        db.commit()
        return {"run_id": run.id, "already": False, "status": "partial", "summary": summ}
    summ["after"] = inventory(db, org.id)
    summ["dnc_after"] = RR.summary(db, org)
    summ["dnc_proof_after"] = RR.verify(db, org)
    run.status, run.finished_at = "applied", datetime.utcnow()
    run.summary_json = json.dumps(summ, default=str)
    db.commit()
    return {"run_id": run.id, "already": False, "status": "applied", "summary": summ}
