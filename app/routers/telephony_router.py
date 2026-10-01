"""Telephony endpoints (stream XC, 2026-09-28).

TWILIO WEBHOOKS (signature verified per account, fail closed - telephony_webhook_guard)
  POST /voice/inbound/dial-status           ring timed out / answered -> voicemail or hang up
  POST /voice/inbound/voicemail-recording   RecordingStatusCallback -> Voicemail + task
  POST /voice/inbound/voicemail-done        <Record action> -> hang up
  POST /voice/amd                           async answering-machine detection result
  POST /voice/human/bridge                  human dialer leg 1 answered -> <Dial> the lead
  POST /voice/human/dial-status             leg 2 outcome
  POST /voice/human/status                  leg 1 status callback
  (POST /voice/inbound itself lives in voice_router and delegates to telephony_service.)

USER (tenant workspace)
  GET  /telephony/me                        my callback phone
  PUT  /telephony/me/callback-phone         save it
  GET  /calls/human/readiness/{lead_id}     every precondition of click-to-call, with fixes
  POST /calls/human                         place the bridge (user's phone first, then the lead)
  GET  /calls/{call_id}                     poll a call
  POST /calls/{call_id}/disposition         outcome + notes + optional callback
  GET  /voicemails                          org-scoped, paged
  GET  /voicemails/{id}
  POST /voicemails/{id}/review
  GET  /voicemails/{id}/audio               authenticated proxy - the provider URL never leaves
  GET  /telephony/numbers                   org admin: read the org's number configuration
  GET  /dialer/identity                     public contact vs outreach numbers ("Not configured")
  GET  /dialer/queue                        next-call queue (same compliance gate as dialing)
  GET  /dialer/leads/{lead_id}/history      calls + voicemails for one lead
  POST /dialer/calls/manual                 log a tel: (own-phone) call so it can be dispositioned

GOD (platform owner only)
  GET   /god/telephony/orgs/{org_id}
  POST  /god/telephony/numbers
  PATCH /god/telephony/numbers/{number_id}
  POST  /god/telephony/orgs/{org_id}/voicemail-drop
  POST  /god/telephony/voicemail-drops/{drop_id}/revoke

No endpoint here purchases or provisions a number.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import (get_db, require_god, require_not_observation, require_tenant_or_observer,
                      require_tenant_user)
from app.models.models import Lead, Organization, Platform, User, VoiceCall
from app.models.telephony_models import (OrgVoicemailDrop, PhoneNumber, TelephonyUserSetting,
                                         Voicemail)
from app.services import lead_scope
from app.services import number_resolution as NR
from app.services import telephony_service as TS
from app.services import telephony_twilio as TT
from app.services.telephony_webhook_guard import assert_org_matches, verify_voice_webhook
from app.utils.time_fmt import iso_utc  # S17: explicit-UTC timestamps

router = APIRouter(tags=["telephony"])
log = logging.getLogger(__name__)

XML = "application/xml"


def _xml(body: str) -> Response:
    return Response(content=body, media_type=XML)


def _iso(dt) -> Optional[str]:
    return iso_utc(dt)


def _org_id(db: Session, user: User) -> str:
    org_id = lead_scope.active_workspace_org_id(user, db)
    if not org_id:
        raise HTTPException(status_code=403, detail="This route needs an active customer workspace.")
    return org_id


# ══ Twilio webhooks ═══════════════════════════════════════════════════════════

@router.post("/voice/inbound/dial-status")
async def inbound_dial_status(request: Request, log_id: str = "", db: Session = Depends(get_db)):
    v = await verify_voice_webhook(request, db)
    row = TS.inbound_row(db, log_id)
    if row is None:
        return _xml(TT.twiml_hangup())
    assert_org_matches(v, row.organization_id)
    return _xml(TS.handle_dial_status(db, row, v.get("DialCallStatus")))


@router.post("/voice/inbound/voicemail-recording")
async def inbound_voicemail_recording(request: Request, log_id: str = "",
                                      db: Session = Depends(get_db)):
    v = await verify_voice_webhook(request, db)
    row = TS.inbound_row(db, log_id)
    if row is None:
        return _xml("<?xml version='1.0'?><Response/>")
    assert_org_matches(v, row.organization_id)
    if (v.get("RecordingStatus") or "completed") == "completed" and v.get("RecordingSid"):
        TS.store_voicemail(db, row, recording_sid=v.get("RecordingSid"),
                           recording_url=v.get("RecordingUrl"),
                           duration=v.get("RecordingDuration"), call_sid=v.get("CallSid"))
    return _xml("<?xml version='1.0'?><Response/>")


@router.post("/voice/inbound/voicemail-done")
async def inbound_voicemail_done(request: Request, log_id: str = "", db: Session = Depends(get_db)):
    v = await verify_voice_webhook(request, db)
    row = TS.inbound_row(db, log_id)
    if row is None:
        return _xml(TT.twiml_hangup())
    assert_org_matches(v, row.organization_id)
    return _xml(TT.twiml_hangup("Thank you. Your message has been recorded. Goodbye."))


def _call_for_webhook(db, v, call_id: str) -> Optional[VoiceCall]:
    call = db.query(VoiceCall).filter(VoiceCall.id == call_id).first() if call_id else None
    if call is not None:
        assert_org_matches(v, call.organization_id)
    return call


@router.post("/voice/amd")
async def amd_callback(request: Request, call_id: str = "", db: Session = Depends(get_db)):
    v = await verify_voice_webhook(request, db)
    call = _call_for_webhook(db, v, call_id)
    if call is None:
        return _xml("<?xml version='1.0'?><Response/>")
    result = TS.handle_async_amd(db, call, v.get("AnsweredBy"), v.get("CallSid"))
    log.info("telephony: AMD for call %s -> %s", call.id, result)
    return _xml("<?xml version='1.0'?><Response/>")


@router.post("/voice/human/bridge")
async def human_bridge(request: Request, call_id: str = "", db: Session = Depends(get_db)):
    v = await verify_voice_webhook(request, db)
    call = _call_for_webhook(db, v, call_id)
    if call is None or not call.is_human_call:
        return _xml(TT.twiml_hangup("This call could not be connected. Goodbye."))
    return _xml(TS.bridge_twiml(db, call))


@router.post("/voice/human/dial-status")
async def human_dial_status(request: Request, call_id: str = "", db: Session = Depends(get_db)):
    v = await verify_voice_webhook(request, db)
    call = _call_for_webhook(db, v, call_id)
    if call is None:
        return _xml(TT.twiml_hangup())
    return _xml(TS.human_dial_status(db, call, v.get("DialCallStatus"), v.get("DialCallDuration")))


@router.post("/voice/human/status")
async def human_status(request: Request, call_id: str = "", db: Session = Depends(get_db)):
    v = await verify_voice_webhook(request, db)
    call = _call_for_webhook(db, v, call_id)
    if call is not None:
        TS.human_leg_status(db, call, v.get("CallStatus"))
    return _xml("<?xml version='1.0'?><Response/>")


# ══ the user's own callback phone ════════════════════════════════════════════

class CallbackPhoneIn(BaseModel):
    phone: Optional[str] = None
    ring_on_inbound: Optional[bool] = None


class VerifyIn(BaseModel):
    code: str = Field(..., min_length=4, max_length=12)


def _me_out(row: Optional[TelephonyUserSetting]) -> dict:
    return {"callback_phone": row.callback_e164 if row and row.verified_at else None,
            "verified_at": _iso(row.verified_at) if row else None,
            "pending_phone": row.pending_e164 if row and row.code_hash else None,
            "code_expires_at": _iso(row.code_expires_at) if row and row.code_hash else None,
            "ring_on_inbound": bool(row.ring_on_inbound) if row else True}


@router.get("/telephony/me")
def telephony_me(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    return _me_out(db.query(TelephonyUserSetting).filter(TelephonyUserSetting.user_id == user.id).first())


@router.put("/telephony/me/callback-phone")
def set_callback_phone(req: CallbackPhoneIn, db: Session = Depends(get_db),
                       user: User = Depends(require_tenant_user),
                       _w: User = Depends(require_not_observation)):
    """Start verifying a callback phone. The number is NOT usable until the
    code delivered to it is typed back (POST .../verify). `phone: null`
    removes the saved number."""
    row = db.query(TelephonyUserSetting).filter(TelephonyUserSetting.user_id == user.id).first()
    if req.ring_on_inbound is not None:
        if row is None:
            row = TelephonyUserSetting(user_id=user.id, code_attempts=0)
            db.add(row)
        row.ring_on_inbound = bool(req.ring_on_inbound)
        db.commit()
    if not req.phone:
        if req.ring_on_inbound is None and row is not None:
            row.callback_e164 = row.verified_at = None
            row.pending_e164 = row.code_hash = row.code_expires_at = None
            db.commit()
        return _me_out(row)
    try:
        row = TS.start_callback_verification(db, user, _org_id(db, user), req.phone)
    except TS.RateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:                                 # noqa: BLE001
        log.error("callback verification delivery failed for %s: %s", user.id, exc)
        raise HTTPException(status_code=502, detail="The verification call could not be placed.")
    return _me_out(row)


@router.post("/telephony/me/callback-phone/verify")
def verify_callback_phone(req: VerifyIn, db: Session = Depends(get_db),
                          user: User = Depends(require_tenant_user),
                          _w: User = Depends(require_not_observation)):
    try:
        row = TS.confirm_callback_verification(db, user, req.code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _me_out(row)


# ══ human dialer ═════════════════════════════════════════════════════════════

def _lead_in_scope(db, user, lead_id) -> Lead:
    lead = lead_scope.authorized_lead_query(db, user).filter(Lead.id == lead_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.get("/calls/human/readiness/{lead_id}")
def human_readiness(lead_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user)):
    lead = _lead_in_scope(db, user, lead_id)
    return TS.human_call_readiness(db, lead, user)


class HumanCallIn(BaseModel):
    lead_id: str


@router.post("/calls/human", status_code=201)
def place_human_call(req: HumanCallIn, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user),
                     _w: User = Depends(require_not_observation)):
    lead = _lead_in_scope(db, user, req.lead_id)
    try:
        call = TS.start_human_call(db, lead, user)
    except TS.RateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail="The voice provider could not place the call: %s" % exc)
    try:
        from app.routers.audit_log_router import log_action
        log_action(db, lead.organization_id, user.id, action="voice.human_call",
                   target_type="lead", target_id=lead.id)
    except Exception:                                        # noqa: BLE001
        log.exception("audit log failed for human call %s", call.id)
    return TS.call_json(call)


def _call_in_scope(db, user, call_id) -> VoiceCall:
    org_id = _org_id(db, user)
    q = db.query(VoiceCall).filter(VoiceCall.id == call_id, VoiceCall.organization_id == org_id)
    if not lead_scope.is_manager_here(user, db):
        q = q.filter(VoiceCall.advisor_id == user.id)
    call = q.first()
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    return call


@router.get("/calls/{call_id}")
def get_call(call_id: str, db: Session = Depends(get_db),
             user: User = Depends(require_tenant_user)):
    return TS.call_json(_call_in_scope(db, user, call_id))


class DispositionIn(BaseModel):
    outcome: str
    notes: Optional[str] = Field(None, max_length=5000)
    callback_at: Optional[str] = None
    # A follow-up task without a callback time (or with its own title).
    follow_up: Optional[bool] = False
    follow_up_title: Optional[str] = Field(None, max_length=300)


@router.post("/calls/{call_id}/disposition")
def disposition(call_id: str, req: DispositionIn, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user),
                _w: User = Depends(require_not_observation)):
    call = _call_in_scope(db, user, call_id)
    if req.outcome not in TS.DISPOSITIONS:
        raise HTTPException(status_code=422, detail="Unknown outcome. Use one of: %s"
                            % ", ".join(TS.DISPOSITIONS))
    before = {"disposition": call.disposition, "notes": call.disposition_notes}
    call.disposition = req.outcome
    call.disposition_notes = (req.notes or "").strip() or None
    call.disposition_at = datetime.utcnow()
    call.disposition_by_id = user.id
    callback = None
    if req.callback_at:
        from app.services import wholesale_ops, wholesale_sms
        due = wholesale_ops.parse_when(req.callback_at)
        lead = db.query(Lead).filter(Lead.id == call.lead_id,
                                     Lead.organization_id == call.organization_id).first()
        if lead is None:
            raise HTTPException(status_code=404, detail="Lead not found")
        if wholesale_sms.is_program_lead(db, lead):
            cb = wholesale_ops.create_callback(
                db, call.organization_id, user, lead_id=lead.id, due_at=due,
                notes=call.disposition_notes, source="manual", source_ref=call.id)
            callback = {"kind": "wholesale_callback", "id": cb.id, "due_at": _iso(cb.due_at)}
        else:
            from app.models.work_models import LeadTask
            t = LeadTask(organization_id=call.organization_id, lead_id=lead.id,
                         title=("Call back %s" % (TS.lead_label(lead) or "contact"))[:300],
                         details=call.disposition_notes, due_at=due, status="open",
                         assigned_to_id=user.id, created_by_id=user.id, source="callback")
            db.add(t)
            db.flush()
            callback = {"kind": "task", "id": t.id, "due_at": _iso(t.due_at)}
    if req.follow_up and callback is None:
        lead = db.query(Lead).filter(Lead.id == call.lead_id,
                                     Lead.organization_id == call.organization_id).first()
        if lead is None:
            raise HTTPException(status_code=404, detail="Lead not found")
        from app.models.work_models import LeadTask
        title = (req.follow_up_title or "").strip() or (
            "Follow up with %s" % (TS.lead_label(lead) or "contact"))
        t = LeadTask(organization_id=call.organization_id, lead_id=lead.id, title=title[:300],
                     details=call.disposition_notes, due_at=None, status="open",
                     assigned_to_id=user.id, created_by_id=user.id, source="call_follow_up")
        db.add(t)
        db.flush()
        callback = {"kind": "task", "id": t.id, "due_at": None}
    db.commit()
    try:
        from app.routers.audit_log_router import log_action
        log_action(db, call.organization_id, user.id, action="voice.call_disposition",
                   target_type="voice_call", target_id=call.id,
                   details={"lead_id": call.lead_id, "outcome": call.disposition,
                            "has_notes": bool(call.disposition_notes),
                            "task_id": (callback or {}).get("id")},
                   before=before,
                   after={"disposition": call.disposition, "notes": call.disposition_notes})
    except Exception:                                        # noqa: BLE001
        log.exception("audit log failed for disposition on %s", call.id)
    return {"call": TS.call_json(call), "callback": callback}


# ══ human dialer screen (2026-10-01) ══════════════════════════════════════════

@router.get("/dialer/identity")
def dialer_identity(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Who a call/text from this workspace comes from - public contact number
    and outreach numbers kept separate; "Not configured" when missing."""
    org_id = _org_id(db, user)
    ident = NR.communication_identity(db, org_id, user=user)
    ident["calling_mode"] = ("provider_bridge" if ident["voice_outbound"]["configured"]
                             and ident["voice_outbound"]["provider_ready"] else "device_tel_fallback")
    return ident


@router.get("/dialer/queue")
def dialer_queue(limit: int = Query(25, ge=1, le=100), scope: str = Query("mine"),
                 db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    org_id = _org_id(db, user)
    return TS.dialer_queue(db, user, org_id, limit=limit, everyone=(scope == "team"))


@router.get("/dialer/leads/{lead_id}/history")
def dialer_lead_history(lead_id: str, db: Session = Depends(get_db),
                        user: User = Depends(require_tenant_user)):
    lead = _lead_in_scope(db, user, lead_id)
    return TS.lead_call_history(db, lead)


class ManualCallIn(BaseModel):
    lead_id: str


@router.post("/dialer/calls/manual", status_code=201)
def dialer_manual_call(req: ManualCallIn, db: Session = Depends(get_db),
                       user: User = Depends(require_tenant_user),
                       _w: User = Depends(require_not_observation)):
    """Record a call the user placed from their own phone (tel: fallback) so
    notes and a disposition can be saved. No provider call is made."""
    lead = _lead_in_scope(db, user, req.lead_id)
    try:
        call = TS.start_manual_call_log(db, lead, user)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    try:
        from app.routers.audit_log_router import log_action
        log_action(db, lead.organization_id, user.id, action="voice.manual_call_logged",
                   target_type="lead", target_id=lead.id, details={"call_id": call.id})
    except Exception:                                        # noqa: BLE001
        log.exception("audit log failed for manual call %s", call.id)
    return TS.call_json(call)


# ══ voicemails ═══════════════════════════════════════════════════════════════

def _vm_scope(db, user):
    org_id = _org_id(db, user)
    q = db.query(Voicemail).filter(Voicemail.organization_id == org_id)
    if not lead_scope.is_manager_here(user, db):
        mine = db.query(Lead.id).filter(Lead.organization_id == org_id,
                                        Lead.assigned_to_id == user.id)
        q = q.filter((Voicemail.lead_id.is_(None)) | (Voicemail.lead_id.in_(mine)))
    return q


def _vm_out(db, vm: Voicemail, leads: Dict[str, Lead]) -> dict:
    lead = leads.get(vm.lead_id) if vm.lead_id else None
    return {
        "id": vm.id, "status": vm.status, "received_at": _iso(vm.received_at),
        "from": vm.from_e164, "to": vm.to_e164, "caller_state": vm.caller_state,
        "lead_id": vm.lead_id, "lead_name": TS.lead_label(lead) or None,
        "org_contact_id": vm.org_contact_id, "duration_seconds": vm.duration_seconds,
        "has_recording": bool(vm.recording_sid or vm.recording_url),
        "audio_path": "/voicemails/%s/audio" % vm.id if (vm.recording_sid or vm.recording_url) else None,
        "transcript": vm.transcript,
        "transcript_status": vm.transcript_status,
        "transcript_note": TS.TRANSCRIPT_REASON if vm.transcript_status == TS.TRANSCRIPT_NOT_ENABLED else None,
        "task_id": vm.task_id, "reviewed_at": _iso(vm.reviewed_at),
        "organization_id": vm.organization_id,
    }


@router.get("/voicemails")
def list_voicemails(status: Optional[str] = None, page: int = Query(1, ge=1),
                    page_size: int = Query(25, ge=1, le=100),
                    db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    q = _vm_scope(db, user)
    if status in ("new", "reviewed"):
        q = q.filter(Voicemail.status == status)
    total = q.count()
    rows = (q.order_by(Voicemail.received_at.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    ids = [r.lead_id for r in rows if r.lead_id]
    leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_(ids)).all()} if ids else {}
    return {"items": [_vm_out(db, r, leads) for r in rows], "total": total,
            "page": page, "page_size": page_size}


def _vm_or_404(db, user, vm_id) -> Voicemail:
    vm = _vm_scope(db, user).filter(Voicemail.id == vm_id).first()
    if vm is None:
        raise HTTPException(status_code=404, detail="Voicemail not found")
    return vm


@router.get("/voicemails/{vm_id}")
def get_voicemail(vm_id: str, db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_or_observer)):
    vm = _vm_or_404(db, user, vm_id)
    leads = {}
    if vm.lead_id:
        l = db.query(Lead).filter(Lead.id == vm.lead_id).first()
        if l is not None:
            leads[l.id] = l
    return _vm_out(db, vm, leads)


class ReviewIn(BaseModel):
    reviewed: bool = True


@router.post("/voicemails/{vm_id}/review")
def review_voicemail(vm_id: str, req: ReviewIn = ReviewIn(), db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user),
                     _w: User = Depends(require_not_observation)):
    vm = _vm_or_404(db, user, vm_id)
    if req.reviewed:
        vm.status, vm.reviewed_at, vm.reviewed_by_id = "reviewed", datetime.utcnow(), user.id
    else:
        vm.status, vm.reviewed_at, vm.reviewed_by_id = "new", None, None
    db.commit()
    return _vm_out(db, vm, {})


def _vm_via_membership(db, user, vm_id) -> Optional[Voicemail]:
    from app.services import workspace_access
    vm = db.query(Voicemail).filter(Voicemail.id == vm_id).first()
    if vm is None or not workspace_access.has_workspace(user, db, vm.organization_id):
        return None
    role = workspace_access.workspace_role(user, db, vm.organization_id) or ""
    if role in ("org_admin", "super_admin", "manager", "owner", "admin"):
        return vm
    if vm.lead_id is None:
        return vm
    lead = db.query(Lead).filter(Lead.id == vm.lead_id,
                                 Lead.organization_id == vm.organization_id).first()
    return vm if lead is not None and lead.assigned_to_id == user.id else None


@router.get("/voicemails/{vm_id}/audio")
def voicemail_audio(vm_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_or_observer)):
    vm = _vm_scope(db, user).filter(Voicemail.id == vm_id).first()
    if vm is None:
        # The player fetches bytes without the workspace header, so a person
        # standing in a workspace they hold a MEMBERSHIP in (not their home
        # column) is checked against that membership directly. Same rule as
        # the list: managers there see all; others only their own leads' or
        # unmatched voicemails. Anyone else: 404.
        vm = _vm_via_membership(db, user, vm_id)
    if vm is None:
        raise HTTPException(status_code=404, detail="Voicemail not found")
    if not TT.valid_recording_sid(vm.recording_sid):
        raise HTTPException(status_code=404, detail="No recording is stored for this voicemail.")
    org = db.query(Organization).filter(Organization.id == vm.organization_id).first()
    rec = (db.query(PhoneNumber).filter(PhoneNumber.id == vm.phone_number_id).first()
           if vm.phone_number_id else None)
    level = "organization" if rec is None or rec.organization_id else (
        "brand" if rec.platform_id else "platform")
    creds = NR.twilio_credentials(db, org, NR.ResolvedNumber(ok=True, level=level))
    if not creds:
        raise HTTPException(status_code=503, detail="The provider account for this recording is not configured.")
    try:
        # Built server-side from the RecordingSid + the owning account SID;
        # the URL Twilio posted is never fetched.
        content, ctype = TT.fetch_recording(creds, vm.recording_sid)
    except Exception as exc:                                 # noqa: BLE001
        log.error("voicemail audio fetch failed for %s: %s", vm.id, exc)
        raise HTTPException(status_code=502, detail="The recording could not be retrieved from the provider.")
    return Response(content=content, media_type=ctype or "audio/mpeg",
                    headers={"Cache-Control": "private, no-store"})


# ══ number configuration ═════════════════════════════════════════════════════

def _number_out(rec: PhoneNumber) -> dict:
    if rec.workspace_id:
        scope = "workspace"
    elif rec.organization_id:
        scope = "organization"
    elif rec.platform_id:
        scope = "brand"
    else:
        scope = "platform"
    return {"id": rec.id, "e164": rec.e164, "provider": rec.provider, "provider_sid": rec.provider_sid,
            "organization_id": rec.organization_id, "platform_id": rec.platform_id,
            "workspace_id": rec.workspace_id, "scope": scope,
            "capabilities": {"sms": bool(rec.cap_sms), "voice_outbound": bool(rec.cap_voice_outbound),
                             "voice_inbound": bool(rec.cap_voice_inbound),
                             "voicemail": bool(rec.cap_voicemail)},
            "inbound_route": NR.parse_route(rec.default_inbound_route),
            "is_active": bool(rec.is_active), "label": rec.label,
            "created_at": _iso(rec.created_at), "updated_at": _iso(rec.updated_at)}


def _drop_out(d: Optional[OrgVoicemailDrop]) -> Optional[dict]:
    if d is None:
        return None
    return {"id": d.id, "status": d.status, "message_text": d.message_text,
            "recording_url": d.recording_url, "approved_by_id": d.approved_by_id,
            "approved_at": _iso(d.approved_at), "created_at": _iso(d.created_at)}


def org_telephony_payload(db, org: Organization, include_pools: bool) -> dict:
    rows = (db.query(PhoneNumber).filter(PhoneNumber.organization_id == org.id)
            .order_by(PhoneNumber.created_at.asc()).all())
    legacy_org = NR.normalize_e164(org.org_twilio_phone_number)
    users = db.query(User).filter(User.organization_id == org.id).all()
    legacy_users = [{"user_id": u.id, "name": u.full_name, "e164": NR.normalize_e164(u.twilio_phone_number)}
                    for u in users if NR.normalize_e164(u.twilio_phone_number)]
    settings = {s.user_id: s for s in db.query(TelephonyUserSetting)
                .filter(TelephonyUserSetting.user_id.in_([u.id for u in users])).all()} if users else {}
    members = [{"user_id": u.id, "name": u.full_name, "role": u.role,
                "callback_phone": (settings[u.id].callback_e164
                                   if u.id in settings and settings[u.id].verified_at else None)}
               for u in users if u.is_active]
    out_res = NR.resolve_voice_number(db, org, purpose=NR.PURPOSE_OUTBOUND)
    in_res = NR.resolve_voice_number(db, org, purpose=NR.PURPOSE_INBOUND)
    drops = (db.query(OrgVoicemailDrop).filter(OrgVoicemailDrop.organization_id == org.id)
             .order_by(OrgVoicemailDrop.created_at.desc()).limit(10).all())
    payload = {
        "organization_id": org.id,
        "numbers": [_number_out(r) for r in rows],
        "legacy": {
            "org_number": legacy_org,
            "org_number_governed_by_record": bool(legacy_org and db.query(PhoneNumber)
                                                  .filter(PhoneNumber.e164 == legacy_org).first()),
            "org_account_stored": bool(org.org_twilio_account_sid and org.org_twilio_auth_token_encrypted),
            "user_numbers": legacy_users,
            "note": ("Legacy numbers stored on the organization/user records are used as implicit "
                     "organization-level numbers until a phone number record governs them."),
        },
        "resolved": {"outbound": out_res.as_dict(), "inbound": in_res.as_dict()},
        "voicemail_drop": {"approved": _drop_out(TS.approved_drop(db, org.id)),
                           "history": [_drop_out(d) for d in drops]},
        "members": members,
        "webhooks": {
            "voice_url": ("%s/voice/inbound" % TS.backend_base()) if TS.backend_base() else None,
            "method": "POST",
            "note": ("Configure this as the Voice 'A call comes in' webhook on each inbound number in "
                     "Twilio. All other callbacks are set per call by the platform."),
        },
        "transcription": {"enabled": False, "note": TS.TRANSCRIPT_REASON},
        "browser_calling": {"available": False,
                            "note": "No Twilio Voice SDK access-token endpoint exists; calls use the "
                                    "click-to-call bridge (your phone rings first)."},
    }
    if include_pools:
        pools = db.query(PhoneNumber).filter(PhoneNumber.organization_id.is_(None))
        pools = pools.filter((PhoneNumber.platform_id.is_(None)) |
                             (PhoneNumber.platform_id == org.platform_id)).all()
        payload["pool_numbers"] = [_number_out(r) for r in pools]
        try:
            from app.models.location_models import Location
            payload["workspaces"] = [{"id": l.id, "name": getattr(l, "name", None)} for l in
                                     db.query(Location).filter(Location.organization_id == org.id).all()]
        except Exception:                                    # noqa: BLE001
            payload["workspaces"] = []
    return payload


@router.get("/telephony/numbers")
def org_numbers(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    if not lead_scope.is_manager_here(user, db):
        raise HTTPException(status_code=403, detail="Organization administrators only.")
    org = db.query(Organization).filter(Organization.id == _org_id(db, user)).first()
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org_telephony_payload(db, org, include_pools=False)


@router.get("/god/telephony/orgs/{org_id}")
def god_org_numbers(org_id: str, db: Session = Depends(get_db), user: User = Depends(require_god)):
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org_telephony_payload(db, org, include_pools=True)


class RouteIn(BaseModel):
    mode: Optional[str] = None
    ring_user_ids: Optional[List[str]] = None
    timeout_seconds: Optional[int] = None
    voicemail: Optional[bool] = None
    greeting_text: Optional[str] = Field(None, max_length=500)
    greeting_recording_url: Optional[str] = None


class NumberIn(BaseModel):
    e164: Optional[str] = None
    organization_id: Optional[str] = None
    platform_id: Optional[str] = None
    workspace_id: Optional[str] = None
    provider: Optional[str] = None
    provider_sid: Optional[str] = None
    label: Optional[str] = Field(None, max_length=120)
    cap_sms: Optional[bool] = None
    cap_voice_outbound: Optional[bool] = None
    cap_voice_inbound: Optional[bool] = None
    cap_voicemail: Optional[bool] = None
    is_active: Optional[bool] = None
    inbound_route: Optional[RouteIn] = None
    clear_organization: bool = False


def _validate_route(db, org_id: Optional[str], route: RouteIn) -> str:
    data = route.model_dump(exclude_none=True)
    if data.get("greeting_recording_url") and not data["greeting_recording_url"].startswith("https://"):
        raise HTTPException(status_code=422, detail="Greeting recording must be an https URL.")
    ids = data.get("ring_user_ids") or []
    if ids:
        if not org_id:
            raise HTTPException(status_code=422, detail="Only an organization number can ring people.")
        bad = [i for i in ids if not TS.is_org_member(db, org_id, i)]
        if bad:
            raise HTTPException(status_code=422, detail="Ring targets must be members of the organization.")
    return json.dumps(NR.parse_route(data))


def _apply(db, rec: PhoneNumber, req: NumberIn, user: User) -> None:
    if req.clear_organization:
        rec.organization_id = None
        rec.workspace_id = None
    if req.organization_id is not None:
        if not db.query(Organization).filter(Organization.id == req.organization_id).first():
            raise HTTPException(status_code=404, detail="Organization not found")
        rec.organization_id = req.organization_id
    if req.platform_id is not None:
        if req.platform_id and not db.query(Platform).filter(Platform.id == req.platform_id).first():
            raise HTTPException(status_code=404, detail="Brand not found")
        rec.platform_id = req.platform_id or None
    if req.workspace_id is not None:
        if req.workspace_id:
            if not rec.organization_id:
                raise HTTPException(status_code=422, detail="A workspace number must belong to an organization.")
            from app.models.location_models import Location
            if not db.query(Location).filter(Location.id == req.workspace_id,
                                             Location.organization_id == rec.organization_id).first():
                raise HTTPException(status_code=404, detail="Workspace not found in this organization")
        rec.workspace_id = req.workspace_id or None
    for f in ("provider", "provider_sid", "label"):
        val = getattr(req, f)
        if val is not None:
            cleaned = val.strip() or None
            setattr(rec, f, (cleaned or "twilio") if f == "provider" else cleaned)
    for f in ("cap_sms", "cap_voice_outbound", "cap_voice_inbound", "cap_voicemail", "is_active"):
        val = getattr(req, f)
        if val is not None:
            setattr(rec, f, bool(val))
    if req.inbound_route is not None:
        rec.default_inbound_route = _validate_route(db, rec.organization_id, req.inbound_route)
    rec.updated_at = datetime.utcnow()


@router.post("/god/telephony/numbers", status_code=201)
def god_create_number(req: NumberIn, db: Session = Depends(get_db), user: User = Depends(require_god)):
    e164 = NR.normalize_e164(req.e164)
    if not e164:
        raise HTTPException(status_code=422, detail="Enter a valid US number.")
    if db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).first():
        raise HTTPException(status_code=409, detail="That number already has a record - edit it instead.")
    rec = PhoneNumber(e164=e164, provider="twilio", created_by_id=user.id, is_active=True)
    db.add(rec)
    _apply(db, rec, req, user)
    db.commit()
    return _number_out(rec)


@router.patch("/god/telephony/numbers/{number_id}")
def god_update_number(number_id: str, req: NumberIn, db: Session = Depends(get_db),
                      user: User = Depends(require_god)):
    rec = db.query(PhoneNumber).filter(PhoneNumber.id == number_id).first()
    if rec is None:
        raise HTTPException(status_code=404, detail="Number not found")
    if req.e164 and NR.normalize_e164(req.e164) != rec.e164:
        raise HTTPException(status_code=422, detail="A number's digits cannot be changed; create a new record.")
    _apply(db, rec, req, user)
    db.commit()
    return _number_out(rec)


class DropIn(BaseModel):
    message_text: Optional[str] = Field(None, max_length=1000)
    recording_url: Optional[str] = None
    approve: bool = False


@router.post("/god/telephony/orgs/{org_id}/voicemail-drop", status_code=201)
def god_set_drop(org_id: str, req: DropIn, db: Session = Depends(get_db),
                 user: User = Depends(require_god)):
    if not db.query(Organization).filter(Organization.id == org_id).first():
        raise HTTPException(status_code=404, detail="Organization not found")
    text = (req.message_text or "").strip() or None
    url = (req.recording_url or "").strip() or None
    if not text and not url:
        raise HTTPException(status_code=422, detail="Provide the message text or a recording URL.")
    if url and not url.startswith("https://"):
        raise HTTPException(status_code=422, detail="Recording must be an https URL.")
    now = datetime.utcnow()
    if req.approve:
        for old in db.query(OrgVoicemailDrop).filter(OrgVoicemailDrop.organization_id == org_id,
                                                     OrgVoicemailDrop.status == "approved").all():
            old.status = "revoked"
    d = OrgVoicemailDrop(organization_id=org_id, message_text=text, recording_url=url,
                         status="approved" if req.approve else "draft",
                         approved_by_id=user.id if req.approve else None,
                         approved_at=now if req.approve else None, created_by_id=user.id)
    db.add(d)
    db.commit()
    return _drop_out(d)


@router.post("/god/telephony/voicemail-drops/{drop_id}/revoke")
def god_revoke_drop(drop_id: str, db: Session = Depends(get_db), user: User = Depends(require_god)):
    d = db.query(OrgVoicemailDrop).filter(OrgVoicemailDrop.id == drop_id).first()
    if d is None:
        raise HTTPException(status_code=404, detail="Voicemail message not found")
    d.status = "revoked"
    db.commit()
    return _drop_out(d)
