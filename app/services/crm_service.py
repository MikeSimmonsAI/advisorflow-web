"""
CRM Integration Service — BookaBoost

Handles outbound webhook pushes and inbound lead imports for connected CRMs.
Every payload is annotated with BookaBoost source metadata so clients have
a clear audit trail of what the platform did.

Supported modes:
  push_only  — BookaBoost → CRM (events fire when things happen here)
  pull_only  — CRM → BookaBoost (CRM posts contacts to our inbound endpoint)
  two_way    — both directions

Supported push targets:
  webhook    — generic HTTP POST, works with any CRM via Zapier/Make/native webhooks
  gohighlevel — direct GHL API (creates/updates contact + adds BookaBoost tag + note)
  hubspot    — direct HubSpot API (creates/updates contact + adds note)
"""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session
from sqlalchemy import text

logger = logging.getLogger(__name__)

BB_SIG_HEADER = "X-BookaBoost-Signature"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_connections(db: Session, org_id: str, active_only: bool = True) -> list[dict]:
    query = "SELECT * FROM crm_connections WHERE organization_id = :org_id"
    if active_only:
        query += " AND active = TRUE"
    rows = db.execute(text(query), {"org_id": org_id}).mappings().all()
    return [dict(row) for row in rows]


def _sign(secret: str, payload: str) -> str:
    if not secret:
        return ""
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _lead_dict(lead: Any) -> dict:
    return {
        "id": str(lead.id),
        "first_name": lead.first_name or "",
        "last_name": lead.last_name or "",
        "full_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
        "email": lead.email or "",
        "phone": lead.phone or "",
        "status": lead.status or "",
        "tier": lead.tier or "",
        "source_year": str(lead.source_year or ""),
    }


def _build_payload(event_type: str, lead: Any, extra: dict, tag: str) -> dict:
    return {
        "source": tag or "BookaBoost",
        "source_platform": "BookaBoost",
        "bookaboost_event": event_type,
        "bookaboost_timestamp": datetime.utcnow().isoformat() + "Z",
        "lead": _lead_dict(lead),
        **extra,
    }


# ── Generic webhook push ──────────────────────────────────────────────────────

def _push_webhook(conn: dict, payload_dict: dict) -> dict:
    payload_json = json.dumps(payload_dict)
    sig = _sign(conn.get("webhook_secret") or "", payload_json)
    headers = {
        "Content-Type": "application/json",
        BB_SIG_HEADER: sig,
        "User-Agent": "BookaBoost-Webhook/1.0",
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(conn["webhook_url"], content=payload_json, headers=headers)
        return {"success": resp.status_code < 300, "status_code": resp.status_code}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── GoHighLevel direct API ────────────────────────────────────────────────────

def _push_gohighlevel(conn: dict, event_type: str, lead: Any, extra: dict, tag: str) -> dict:
    api_key = conn.get("api_key_encrypted") or ""  # stored as plaintext for now
    if not api_key:
        return {"success": False, "error": "No GHL API key configured"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Version": "2021-07-28",
    }

    contact_data = {
        "firstName": lead.first_name or "",
        "lastName": lead.last_name or "",
        "email": lead.email or "",
        "phone": lead.phone or "",
        "tags": [tag or "BookaBoost"],
        "source": "BookaBoost",
        "customFields": [
            {"key": "bookaboost_last_event", "field_value": event_type},
            {"key": "bookaboost_tier", "field_value": lead.tier or ""},
        ],
    }

    note_text = (
        f"[BookaBoost] Event: {event_type}\n"
        f"Timestamp: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"
        + (f"Booking URL: {extra.get('booking_url')}\n" if extra.get("booking_url") else "")
        + (f"Notes: {extra.get('notes')}\n" if extra.get("notes") else "")
    )

    try:
        with httpx.Client(timeout=10.0) as client:
            # Create or update contact
            resp = client.post(
                "https://rest.gohighlevel.com/v1/contacts/",
                json=contact_data, headers=headers,
            )
            contact_id = None
            if resp.status_code in (200, 201):
                contact_id = resp.json().get("contact", {}).get("id")
            elif resp.status_code == 422:
                # Contact exists — try to find by phone/email
                search_resp = client.get(
                    "https://rest.gohighlevel.com/v1/contacts/",
                    params={"query": lead.phone or lead.email or ""},
                    headers=headers,
                )
                contacts = search_resp.json().get("contacts", [])
                if contacts:
                    contact_id = contacts[0].get("id")

            # Add a note if we have a contact ID
            if contact_id:
                client.post(
                    f"https://rest.gohighlevel.com/v1/contacts/{contact_id}/notes/",
                    json={"body": note_text},
                    headers=headers,
                )

        return {"success": resp.status_code < 300 or resp.status_code == 422, "contact_id": contact_id}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── HubSpot direct API ────────────────────────────────────────────────────────

def _push_hubspot(conn: dict, event_type: str, lead: Any, extra: dict, tag: str) -> dict:
    api_key = conn.get("api_key_encrypted") or ""
    if not api_key:
        return {"success": False, "error": "No HubSpot API key configured"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    contact_data = {
        "properties": {
            "firstname": lead.first_name or "",
            "lastname": lead.last_name or "",
            "email": lead.email or "",
            "phone": lead.phone or "",
            "bookaboost_source": "true",
            "bookaboost_last_event": event_type,
        }
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                "https://api.hubapi.com/crm/v3/objects/contacts",
                json=contact_data, headers=headers,
            )
            contact_id = None
            if resp.status_code in (200, 201):
                contact_id = resp.json().get("id")
            elif resp.status_code == 409:
                # Already exists — update by email
                if lead.email:
                    update_resp = client.patch(
                        f"https://api.hubapi.com/crm/v3/objects/contacts/{lead.email}?idProperty=email",
                        json=contact_data, headers=headers,
                    )
                    contact_id = update_resp.json().get("id")

            # Add engagement note
            if contact_id:
                note_body = (
                    f"BookaBoost event: {event_type}\n"
                    + (f"Booking URL: {extra.get('booking_url')}\n" if extra.get("booking_url") else "")
                )
                client.post(
                    "https://api.hubapi.com/crm/v3/objects/notes",
                    json={
                        "properties": {
                            "hs_note_body": note_body,
                            "hs_timestamp": str(int(datetime.utcnow().timestamp() * 1000)),
                        },
                        "associations": [{"to": {"id": contact_id}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}]}],
                    },
                    headers=headers,
                )

        return {"success": resp.status_code < 300 or resp.status_code == 409, "contact_id": contact_id}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Main push_event ───────────────────────────────────────────────────────────

def push_event(db: Session, org_id: str, event_type: str, lead: Any, extra: dict = {}) -> list[dict]:
    """
    Fire event to all active CRM connections for this org.
    event_type: 'booking' | 'status_change' | 'new_reply' | 'pipeline_started'
    Called from booking flows, status updates, etc.
    """
    connections = _get_connections(db, org_id)
    results = []

    for conn in connections:
        push_events = json.loads(conn.get("push_events") or '["booking"]')
        if event_type not in push_events:
            continue

        crm_type = conn.get("crm_type") or "webhook"
        tag = conn.get("annotation_tag") or "BookaBoost"

        if crm_type == "gohighlevel":
            result = _push_gohighlevel(conn, event_type, lead, extra, tag)
        elif crm_type == "hubspot":
            result = _push_hubspot(conn, event_type, lead, extra, tag)
        else:
            payload = _build_payload(event_type, lead, extra, tag)
            result = _push_webhook(conn, payload)

        result["connection_id"] = conn["id"]
        result["connection_name"] = conn.get("name", "")
        results.append(result)

        if result.get("success"):
            db.execute(text(
                "UPDATE crm_connections SET last_push_at = NOW(), "
                "total_pushed = total_pushed + 1 WHERE id = :id"
            ), {"id": conn["id"]})

    try:
        db.commit()
    except Exception:
        db.rollback()

    return results


# ── Inbound (pull-in) ─────────────────────────────────────────────────────────

def import_inbound_leads(db: Session, org_id: str, records: list[dict]) -> dict:
    """
    Process leads pushed FROM a CRM into BookaBoost's inbound webhook endpoint.
    Deduplicates by phone + email. Creates Lead records for new contacts.
    """
    from app.models.models import Lead

    created = 0
    skipped = 0

    for rec in records:
        phone = (rec.get("phone") or rec.get("mobile") or "").strip()
        email = (rec.get("email") or "").strip()

        existing = None
        if phone:
            existing = db.query(Lead).filter_by(organization_id=org_id, phone=phone).first()
        if not existing and email:
            existing = db.query(Lead).filter_by(organization_id=org_id, email=email).first()

        if existing:
            skipped += 1
            continue

        lead = Lead(
            id=str(uuid.uuid4()),
            organization_id=org_id,
            first_name=rec.get("first_name") or rec.get("firstName") or "",
            last_name=rec.get("last_name") or rec.get("lastName") or "",
            email=email or None,
            phone=phone or None,
            tier=rec.get("tier") or rec.get("tag") or "",
            source_year=str(rec.get("source_year") or datetime.utcnow().year),
            status="new",
            created_at=datetime.utcnow(),
        )
        db.add(lead)
        created += 1

    db.commit()

    if created > 0:
        db.execute(text(
            "UPDATE crm_connections SET last_pull_at = NOW(), "
            "total_pulled = total_pulled + :count "
            "WHERE organization_id = :org_id AND active = TRUE"
        ), {"count": created, "org_id": org_id})
        db.commit()

    return {"created": created, "skipped": skipped, "total": len(records)}
