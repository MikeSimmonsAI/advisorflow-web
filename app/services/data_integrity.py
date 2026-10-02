"""DATABASE INTEGRITY - read-only checks an owner can run against production.

Every check is a COUNT plus up to five example ids, never a fix. Each answers
one question whose answer should be zero, and says what a non-zero answer
means. A check whose table does not exist on this deployment reports
"not_applicable" instead of failing the report.

Cross-tenant checks come first because they are the ones that matter: a row
that points across organizations is either a leak waiting to be displayed or
evidence that one already was. Memberships are honoured - a person who holds
an active customer_org membership in an organization may legitimately be
assigned its leads.
"""
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import text
from sqlalchemy.orm import Session

_MEMBER = ("EXISTS (SELECT 1 FROM memberships m WHERE m.user_id = u.id AND m.is_active = :t "
           "AND m.scope_type = 'customer_org' AND m.scope_id = {org})")

CHECKS: List[Dict[str, str]] = [
    # ── cross-tenant ──
    {"key": "lead_assigned_across_tenants", "severity": "critical",
     "question": "Leads assigned to a person who belongs to a different organization",
     "meaning": "That person may see another customer's lead. Reassign it.",
     "sql": "SELECT l.id FROM leads l JOIN users u ON u.id = l.assigned_to_id "
            "WHERE u.organization_id IS NOT NULL AND u.organization_id <> l.organization_id "
            "AND NOT " + _MEMBER.format(org="l.organization_id")},
    {"key": "notification_across_tenants", "severity": "critical",
     "question": "Notifications about a lead sent to a person in a different organization",
     "meaning": "A lead's name reached another customer's bell.",
     "sql": "SELECT n.id FROM notifications n JOIN leads l ON l.id = n.lead_id JOIN users u ON u.id = n.user_id "
            "WHERE u.organization_id IS NOT NULL AND u.organization_id <> l.organization_id "
            "AND NOT " + _MEMBER.format(org="l.organization_id")},
    {"key": "conversation_memory_across_tenants", "severity": "critical",
     "question": "Conversation memory filed under a different organization than its lead",
     "meaning": "Memory would be read in the wrong workspace.",
     "sql": "SELECT c.id FROM conversation_memory_items c JOIN leads l ON l.id = c.lead_id "
            "WHERE c.organization_id <> l.organization_id"},
    {"key": "pipeline_advisor_across_tenants", "severity": "critical",
     "question": "AI pipeline conversations run by an advisor from another organization",
     "meaning": "Messages would go out under the wrong customer's advisor.",
     "sql": "SELECT p.id FROM pipeline_conversations p JOIN users u ON u.id = p.advisor_id "
            "WHERE u.organization_id IS NOT NULL AND u.organization_id <> p.organization_id "
            "AND NOT " + _MEMBER.format(org="p.organization_id")},
    {"key": "lead_note_across_tenants", "severity": "critical",
     "question": "Lead notes filed under a different organization than their lead",
     "meaning": "A note would show in the wrong workspace.",
     "sql": "SELECT n.id FROM lead_notes n JOIN leads l ON l.id = n.lead_id "
            "WHERE n.organization_id IS NOT NULL AND n.organization_id <> l.organization_id"},
    # ── orphans ──
    {"key": "lead_without_organization", "severity": "high",
     "question": "Leads whose organization no longer exists",
     "meaning": "Invisible to everyone; counts may still include them.",
     "sql": "SELECT l.id FROM leads l LEFT JOIN organizations o ON o.id = l.organization_id WHERE o.id IS NULL"},
    {"key": "user_without_organization_row", "severity": "high",
     "question": "Users pointing at an organization that no longer exists",
     "meaning": "They can sign in to nothing.",
     "sql": "SELECT u.id FROM users u LEFT JOIN organizations o ON o.id = u.organization_id "
            "WHERE u.organization_id IS NOT NULL AND o.id IS NULL"},
    {"key": "reply_without_lead", "severity": "medium",
     "question": "Inbound replies whose lead no longer exists",
     "meaning": "A reply no one can see or answer.",
     "sql": "SELECT r.id FROM replies r LEFT JOIN leads l ON l.id = r.lead_id WHERE l.id IS NULL"},
    {"key": "message_without_lead", "severity": "low",
     "question": "Sent texts whose lead no longer exists",
     "meaning": "History with nothing to attach to.",
     "sql": "SELECT m.id FROM messages m LEFT JOIN leads l ON l.id = m.lead_id WHERE l.id IS NULL"},
    # ── data quality ──
    {"key": "dnc_with_sms_consent", "severity": "high",
     "question": "Leads marked do-not-contact that still carry SMS consent",
     "meaning": "Every send path refuses DNC first, but the record contradicts itself.",
     "sql": "SELECT l.id FROM leads l WHERE l.status = 'dnc' AND l.sms_consent = :t"},
    {"key": "duplicate_phone_in_workspace", "severity": "low",
     "question": "Phone numbers held by more than one active lead in the same workspace",
     "meaning": "Two records for one person - replies may land on the other one.",
     "sql": "SELECT MIN(l.id) FROM leads l WHERE l.phone IS NOT NULL AND l.phone <> '' "
            "AND (l.is_duplicate IS NULL OR l.is_duplicate = :f) "
            "GROUP BY l.organization_id, l.phone HAVING COUNT(*) > 1"},
]


def run(db: Session) -> Dict[str, Any]:
    results = []
    for c in CHECKS:
        row = {k: c[k] for k in ("key", "severity", "question", "meaning")}
        try:
            p = {"t": True, "f": False}
            n = int(db.execute(text("SELECT COUNT(*) FROM (%s) q" % c["sql"]), p).scalar() or 0)
            ids = [r[0] for r in db.execute(text("%s LIMIT 5" % c["sql"]), p).fetchall()] if n else []
            row.update(count=n, examples=ids, status="ok" if not n else "found")
        except Exception as exc:                                 # noqa: BLE001
            db.rollback()
            row.update(count=None, examples=[], status="not_applicable",
                       note=type(exc).__name__)
        results.append(row)
    found = [r for r in results if r["status"] == "found"]
    worst = next((s for s in ("critical", "high", "medium", "low")
                  if any(r["severity"] == s for r in found)), None)
    return {"read_only": True, "checks": results, "found": len(found), "worst": worst,
            "note": "Counts and example ids only; nothing is changed. Zero is the expected answer to every check."}
