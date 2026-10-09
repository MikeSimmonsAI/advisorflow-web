"""SMS Consent Center - read-only operator view. God-only. Sends nothing.

    GET /god/sms-consent/overview   programs (sender, wording version, open or
                                    pending), campaign registry (what each
                                    campaign may carry right now)
    GET /god/sms-consent/records    the consent ledger: program, sender,
                                    wording + version, server timestamp, source
                                    page, IP, STOP status. Filter by program,
                                    phone, organization.
    GET /god/sms-consent/sci-check  why an SCI text to a number would or would
                                    not be allowed (the real gate, dry)
    POST /god/sms-consent/sci/reconcile
                                    owner-attested consent for the EXISTING SCI
                                    contacts (dry run unless apply). Skips
                                    suppressed / DNC / ever-opted-out numbers.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import User
from app.models.sms_consent_models import SmsConsentRecord
from app.services import sms_campaigns, sms_programs, wholesale_sms

router = APIRouter(prefix="/god/sms-consent", tags=["god-sms-consent"])


@router.get("/overview")
def overview(_god: User = Depends(require_god)):
    return {"programs": sms_programs.programs_view(),
            "campaigns": sms_campaigns.public_view(),
            "sci_send_enabled": sms_programs._truthy(sms_programs.SCI_SEND_ENV)}


@router.get("/records")
def records(program: Optional[str] = Query(None, description="general | wholesale | sci"),
            phone: Optional[str] = None, organization_id: Optional[str] = None,
            limit: int = Query(100, ge=1, le=500),
            db: Session = Depends(get_db), _god: User = Depends(require_god)):
    q = db.query(SmsConsentRecord)
    if program:
        p = sms_programs.get(program)
        q = q.filter(SmsConsentRecord.program == (p.consent_program if p else program))
    if phone:
        q = q.filter(SmsConsentRecord.phone_normalized == (wholesale_sms.normalize_e164(phone) or "-"))
    if organization_id:
        q = q.filter(SmsConsentRecord.organization_id == organization_id)
    rows = q.order_by(SmsConsentRecord.consented_at.desc()).limit(limit).all()
    return {"count": len(rows), "records": [sms_programs.record_view(r) for r in rows]}


@router.get("/sci-check")
def sci_check(phone: str, from_number: Optional[str] = None,
              db: Session = Depends(get_db), _god: User = Depends(require_god)):
    org_id = sms_programs.sci_org_id(db)
    if not org_id:
        return {"eligible": False, "reasons": ["program_org_not_configured"]}
    return sms_programs.sci_check(db, org_id, phone,
                                  from_number=from_number or sms_programs.sci_sender_number())


class ReconcileIn(BaseModel):
    attested_by: str
    evidence_reference: str
    apply: bool = False


@router.post("/sci/reconcile")
def sci_reconcile(body: ReconcileIn, db: Session = Depends(get_db),
                  god: User = Depends(require_god)):
    org_id = sms_programs.sci_org_id(db)
    if not org_id or not sms_programs.is_sci_org(db, org_id):
        raise HTTPException(status_code=409, detail="The SCI organization could not be resolved.")
    try:
        out = sms_programs.reconcile_existing(db, org_id, attested_by=body.attested_by,
                                              evidence_reference=body.evidence_reference,
                                              apply=body.apply)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    out["requested_by"] = god.email
    return out


# ── SCI telephony readiness (read-only) ─────────────────────────────────────

def _env_set(name: str) -> bool:
    import os
    return bool((os.environ.get(name) or "").strip())


@router.get("/sci/telephony")
def sci_telephony(live: bool = Query(False, description="also read the number's Twilio config (GET only)"),
                  db: Session = Depends(get_db), _god: User = Depends(require_god)):
    """Everything the SCI toll-free line needs, as this deployment sees it.
    Never returns a secret: env vars are reported as set / not set. With
    `live=true` and the platform account configured, it READS (never changes)
    the number's webhooks and its Toll-Free Verification from Twilio."""
    import os
    from app.models.telephony_models import PhoneNumber
    from app.services import number_resolution as NR
    from app.services.programs import regional_pools as rp
    tf = sms_programs.sci_sender_number()
    row = db.query(PhoneNumber).filter(PhoneNumber.e164 == tf).first()
    org_id = sms_programs.sci_org_id(db)
    base = (os.environ.get("API_BASE_URL") or "").rstrip("/")
    expected = {"sms_url": base + "/sms/webhook/inbound", "voice_url": base + "/voice/inbound"}
    entry = sms_programs.sci_sender_entry()
    out = {
        "toll_free": tf,
        "env": {k: _env_set(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "API_BASE_URL",
                                          "SMS_PROGRAM_ORG_SCI", "SMS_CAMPAIGN_REGISTRY_JSON",
                                          "SCI_TOLL_FREE_NUMBER")},
        "sci_send_enabled": sms_programs._truthy(sms_programs.SCI_SEND_ENV),
        "sci_org_resolved": bool(org_id),
        "number_row": None if row is None else {
            "organization_is_sci": row.organization_id == org_id,
            "label": row.label, "is_toll_free_line": rp.pool_for_phone_number(row) == rp.TOLL_FREE_POOL,
            "route_mode": NR.parse_route(row.default_inbound_route)["mode"],
            "cap_sms": row.cap_sms, "cap_voice_inbound": row.cap_voice_inbound,
            "cap_voicemail": row.cap_voicemail, "cap_voice_outbound": row.cap_voice_outbound,
            "active": row.is_active},
        "sender_registry": entry and {k: entry.get(k) for k in ("key", "kind", "verification_status",
                                                                 "approved_scope", "programs")},
        "sender_approved": sms_campaigns.is_approved(entry),
        "expected_webhooks": expected,
        "twilio_live": None,
    }
    if live:
        out["twilio_live"] = _twilio_number_readout(tf, expected)
    return out


def _twilio_number_readout(tf: str, expected: dict) -> dict:
    """READ-ONLY Twilio lookup. Errors are reported, never raised; no secret
    is returned or logged."""
    import os
    sid = (os.environ.get("TWILIO_ACCOUNT_SID") or "").strip()
    token = (os.environ.get("TWILIO_AUTH_TOKEN") or "").strip()
    if not (sid and token):
        return {"checked": False, "reason": "platform Twilio account not configured on this service"}
    try:
        from twilio.rest import Client
        c = Client(sid, token)
        nums = c.incoming_phone_numbers.list(phone_number=tf, limit=1)
        if not nums:
            return {"checked": True, "found_on_account": False}
        n = nums[0]
        res = {"checked": True, "found_on_account": True,
               "sms_url": n.sms_url, "sms_method": n.sms_method,
               "voice_url": n.voice_url, "voice_method": n.voice_method,
               "sms_webhook_matches": (n.sms_url or "").rstrip("/") == expected["sms_url"],
               "voice_webhook_matches": (n.voice_url or "").rstrip("/") == expected["voice_url"],
               "messaging_service_sid": getattr(n, "messaging_service_sid", None)}
        try:
            v = c.messaging.v1.tollfree_verifications.list(tollfree_phone_number_sid=n.sid, limit=5)
            res["toll_free_verification"] = [{"status": x.status,
                                              "use_case_categories": list(x.use_case_categories or []),
                                              "message_volume": x.message_volume,
                                              "date_updated": str(x.date_updated)} for x in v]
        except Exception as exc:                        # noqa: BLE001
            res["toll_free_verification"] = {"error": type(exc).__name__}
        return res
    except Exception as exc:                            # noqa: BLE001
        return {"checked": False, "reason": "Twilio read failed: %s" % type(exc).__name__}


# ── SCI locations audit (read-only) ─────────────────────────────────────────

@router.get("/sci/locations")
def sci_locations(db: Session = Depends(get_db), _god: User = Depends(require_god)):
    """Per SCI location: assignment, greeting, local phone, booking link,
    planning-guide link, representative routing - and what is missing."""
    import re
    from app.models.models import Lead, User as U
    from app.models.program_models import LocationProfile, OutreachProgram, ProgramSourceRecord
    from app.services.programs import campuses, identity, program_voice
    org_id = sms_programs.sci_org_id(db)
    if not org_id:
        raise HTTPException(status_code=409, detail="The SCI organization could not be resolved.")
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org_id).first()
    rep_user = (db.query(U).filter(U.id == prog.primary_contact_user_id).first()
                if prog and prog.primary_contact_user_id else None)
    try:
        plan = {e: r for r in campuses.plan(db, org_id) for e in r["entities"]}
    except Exception:                                   # noqa: BLE001
        plan = {}
    rows = []
    for p in (db.query(LocationProfile).filter(LocationProfile.organization_id == org_id,
                                               LocationProfile.is_review_bucket.is_(False))
              .order_by(LocationProfile.official_name)):
        recs = (db.query(ProgramSourceRecord.lead_id).filter(
            ProgramSourceRecord.organization_id == org_id, ProgramSourceRecord.location_id == p.location_id,
            ProgramSourceRecord.lead_id.isnot(None)).distinct().all())
        lead_ids = [r[0] for r in recs]
        assigned = (db.query(Lead.id).filter(Lead.id.in_(lead_ids), Lead.assigned_to_id.isnot(None)).count()
                    if lead_ids else 0)
        bs = identity.brand_settings(p)
        pg = identity.planning_guide_link(p, db=db)
        pg_source = ("own" if bs.get("planning_guide_link") else
                     "default" if pg == identity.PLANNING_GUIDE_DEFAULT else "hosted_flyer")
        digits = re.sub(r"\D", "", p.facility_phone or "")
        phone_ok = len(digits) in (10, 11)
        link_ok = (p.appointment_link or "").startswith("https://")
        greeting_custom = bool(str(bs.get("voicemail_greeting") or "").strip())
        issues = []
        if not phone_ok:
            issues.append("no local phone")
        if not link_ok:
            issues.append("no https booking link")
        if pg_source == "default":
            issues.append("planning guide falls back to evosyspro.live/planning-guide")
        if not greeting_custom:
            issues.append("default greeting (names the cemetery)")
        if lead_ids and assigned < len(lead_ids) and rep_user is None:
            issues.append("unassigned contacts and no program primary contact: voicemail notifies nobody")
        c = plan.get(p.official_name) or {}
        rows.append({
            "location": p.official_name, "location_id": p.location_id, "campus": p.campus_label,
            "number_status": c.get("number_status"), "area_code": c.get("area_code"),
            "contacts": len(lead_ids), "contacts_with_rep": assigned,
            "voicemail_rep_fallback": rep_user.email if rep_user else None,
            "local_phone": p.facility_phone, "booking_link": p.appointment_link,
            "planning_guide_link": pg, "planning_guide_source": pg_source,
            "greeting": "custom" if greeting_custom else "default",
            "greeting_text": program_voice.location_greeting(prog, p) if prog else None,
            "issues": issues})
    totals = {"locations": len(rows),
              "with_local_phone": sum(1 for r in rows if "no local phone" not in r["issues"]),
              "with_booking_link": sum(1 for r in rows if "no https booking link" not in r["issues"]),
              "custom_greeting": sum(1 for r in rows if r["greeting"] == "custom"),
              "planning_guide_hosted_or_own": sum(1 for r in rows if r["planning_guide_source"] != "default"),
              "contacts": sum(r["contacts"] for r in rows),
              "contacts_with_rep": sum(r["contacts_with_rep"] for r in rows),
              "program_primary_contact": rep_user.email if rep_user else None}
    return {"totals": totals, "locations": rows}
