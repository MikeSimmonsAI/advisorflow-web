"""EvoSys Sales calling from the team number (owner, 2026-11-20).

* Outbound: the browser places the call through Twilio Voice (no tel: link).
  Twilio asks /evosys-sales-public/voice/outbound what to do; we check the
  caller is an EvoSys Sales member, the lead belongs to EvoSys Sales, is not on
  Do Not Contact, and dial the lead's number WITH THE TEAM NUMBER AS CALLER ID.
  Never a personal number, never another program's line.
* Inbound: a call to the team number (someone calling back) rings the
  configured destination (voice_forward_to, default the owner's alert phone)
  showing the caller's number; a missed call alerts the owner.
* Every call is a row on the lead's timeline (channel "call"), with who,
  when, status, duration and the outcome the person picks.
* Nothing is recorded.

The browser needs an API key and a calling app on the platform Twilio
account. `ensure_setup` creates both once and keeps the key's secret
encrypted in the database; it is never returned or logged.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.models.evosys_sales_models import EvoSysOutreachMessage as M
from app.models.sales_models import Opportunity, OpportunityEvent

log = logging.getLogger(__name__)

PUBLIC = "/evosys-sales-public/voice"
IDENTITY_PREFIX = "evs_"
OUTCOMES = {"talked": "Talked", "voicemail": "Left voicemail", "no_answer": "No answer",
            "booked": "Booked a call", "not_interested": "Not interested", "wrong_number": "Wrong number"}
FINAL = {"completed", "busy", "no-answer", "failed", "canceled"}


def _client():
    from app.services.evosys_sales import sms_setup
    return sms_setup._client()


def _base() -> str:
    from app.services.twilio_callbacks import public_api_base
    return (public_api_base() or "").rstrip("/")


def team_number(s) -> Optional[str]:
    from app.services.evosys_sales.outreach import e164
    return e164(s.sms_from_number)


def status(s) -> Dict:
    """What the app may say about calling, without touching Twilio."""
    num = team_number(s)
    ready = bool(num and s.voice_app_sid and s.voice_key_sid and s.voice_key_secret_enc)
    return {"ready": ready, "number": num, "forward_to": s.voice_forward_to or s.alert_phone,
            "blocker": None if ready else ("No EvoSys Sales number is saved." if not num
                                           else "Calling isn't set up yet (owner: Set up calling).")}


def ensure_setup(db: Session, s) -> Dict:
    """Create (once) the API key + calling app, and point the team number's
    incoming calls at us. Safe to run again: it reuses what exists."""
    from app.utils.crypto import encrypt_value
    from app.services.evosys_sales import sms_setup
    client = _client()
    if client is None:
        return {"error": "The platform Twilio account is not configured."}
    base = _base()
    if not base:
        return {"error": "This server does not know its own public address."}
    num, err = sms_setup._number(client, s)
    if err:
        return {"error": err}
    if not (num.capabilities or {}).get("voice"):
        return {"error": "%s can't make or take calls (Twilio says voice is off for it)." % num.phone_number}
    done = []
    if not s.voice_app_sid:
        app = client.applications.create(friendly_name="EvoSys Sales calling", voice_method="POST",
                                          voice_url=base + PUBLIC + "/outbound",
                                          status_callback=base + PUBLIC + "/status", status_callback_method="POST")
        s.voice_app_sid = app.sid
        done.append("calling app created")
    else:
        client.applications(s.voice_app_sid).update(voice_url=base + PUBLIC + "/outbound", voice_method="POST",
                                                    status_callback=base + PUBLIC + "/status")
    if not (s.voice_key_sid and s.voice_key_secret_enc):
        key = client.new_keys.create(friendly_name="EvoSys Sales browser calling")
        s.voice_key_sid, s.voice_key_secret_enc = key.sid, encrypt_value(key.secret)
        done.append("browser key created")
    before = num.voice_url or None
    want = base + PUBLIC + "/inbound"
    if before != want:
        client.incoming_phone_numbers(num.sid).update(voice_url=want, voice_method="POST")
        done.append("incoming calls now ring %s" % (s.voice_forward_to or s.alert_phone or "nobody - set a number"))
    db.commit()
    return {"ok": True, "number": num.phone_number, "done": done, "incoming_calls_went_to": before,
            "incoming_calls_go_to": want}


def token(s, user) -> Dict:
    """A one-hour browser calling token for this person (outgoing only)."""
    from twilio.jwt.access_token import AccessToken
    from twilio.jwt.access_token.grants import VoiceGrant
    from app.utils.crypto import decrypt_value
    from app.utils.twilio_webhook_guard import platform_account
    st = status(s)
    acct = platform_account()
    if not st["ready"] or not acct:
        return {"ready": False, "blocker": st["blocker"] or "The platform Twilio account is not configured."}
    t = AccessToken(acct[0], s.voice_key_sid, decrypt_value(s.voice_key_secret_enc),
                    identity=IDENTITY_PREFIX + user.id, ttl=3600)
    t.add_grant(VoiceGrant(outgoing_application_sid=s.voice_app_sid, incoming_allow=False))
    return {"ready": True, "token": t.to_jwt(), "number": st["number"]}


def _twiml(xml) -> str:
    return str(xml)


def _say_and_hang(text: str) -> str:
    from twilio.twiml.voice_response import VoiceResponse
    r = VoiceResponse()
    r.say(text)
    r.hangup()
    return _twiml(r)


def outbound(db: Session, s, params: Dict) -> str:
    """TwiML for a browser call: dial the lead from the team number."""
    from twilio.twiml.voice_response import VoiceResponse
    from app.models.models import User
    from app.services import sales_suppression as SDNC
    from app.services.evosys_sales.outreach import e164
    caller = (params.get("From") or "")
    uid = caller.split(":", 1)[-1][len(IDENTITY_PREFIX):] if IDENTITY_PREFIX in caller else ""
    user = db.get(User, uid) if uid else None
    opp = db.get(Opportunity, params.get("lead_id") or "")
    frm = team_number(s)
    if user is None or opp is None or opp.brand_sales_org_id != s.brand_sales_org_id or not frm:
        log.warning("[evosys voice] refused outbound: user=%s lead=%s", bool(user), bool(opp))
        return _say_and_hang("This call can't be placed.")
    if SDNC.for_opportunity(db, opp) or SDNC.find(db, s.brand_sales_org_id, opp.phone, opp.email):
        return _say_and_hang("This business is on Do Not Contact.")
    to = e164(opp.phone)
    if not to:
        return _say_and_hang("This business has no phone number we can call.")
    now = datetime.utcnow()
    db.add(M(brand_sales_org_id=s.brand_sales_org_id, opportunity_id=opp.id, direction="out", channel="call",
             to_address=to, body="Call from %s" % (user.full_name or user.email), status="ringing",
             provider_message_id=params.get("CallSid"), actor_user_id=user.id, created_at=now))
    db.add(OpportunityEvent(opportunity_id=opp.id, event_type="call", actor_user_id=user.id,
                            summary="Called from the EvoSys Sales number", occurred_at=now))
    db.commit()
    r = VoiceResponse()
    d = r.dial(caller_id=frm, answer_on_bridge=True, timeout=30)
    d.number(to, status_callback=_base() + PUBLIC + "/status",
             status_callback_event="initiated ringing answered completed", status_callback_method="POST")
    return _twiml(r)


def _lead_by_phone(db: Session, s, number: str) -> Optional[Opportunity]:
    from app.services.evosys_sales import sms_inbound
    try:
        return sms_inbound._find_lead(db, s.brand_sales_org_id, number)
    except Exception:                                                # noqa: BLE001
        return None


def inbound(db: Session, s, params: Dict) -> str:
    """Someone calls the team number: ring the configured person."""
    from twilio.twiml.voice_response import VoiceResponse
    from app.services.evosys_sales.outreach import e164
    frm = params.get("From") or ""
    dest = e164(s.voice_forward_to or s.alert_phone)
    opp = _lead_by_phone(db, s, frm)
    now = datetime.utcnow()
    if opp is not None:
        db.add(M(brand_sales_org_id=s.brand_sales_org_id, opportunity_id=opp.id, direction="in", channel="call",
                 to_address=team_number(s), body="Called the EvoSys Sales number", status="ringing",
                 provider_message_id=params.get("CallSid"), created_at=now))
        db.add(OpportunityEvent(opportunity_id=opp.id, event_type="call",
                                summary="Called us back on the EvoSys Sales number", occurred_at=now))
        db.commit()
    r = VoiceResponse()
    if not dest:
        r.say("Thanks for calling EvoSys Pro. Please text us at this number and we will get right back to you.")
        return _twiml(r)
    d = r.dial(caller_id=frm or None, answer_on_bridge=True, timeout=25,
               action=_base() + PUBLIC + "/inbound-done", method="POST")
    d.number(dest)
    return _twiml(r)


def inbound_done(db: Session, s, params: Dict) -> str:
    """After the forwarded ring: if nobody answered, say so and alert Mike."""
    from twilio.twiml.voice_response import VoiceResponse
    st = params.get("DialCallStatus") or ""
    sid = params.get("CallSid")
    row = db.query(M).filter(M.provider_message_id == sid, M.channel == "call").first() if sid else None
    if row is not None:
        row.status = "completed" if st == "completed" else ("missed" if st else row.status)
        db.commit()
    r = VoiceResponse()
    if st != "completed":
        who = "A business"
        if row is not None:
            o = db.get(Opportunity, row.opportunity_id)
            who = (o.company_name if o else None) or who
        try:
            from app.services.evosys_sales import sms_inbound as SI
            SI.notify_owner(s, "EvoSys Sales - MISSED CALL: %s (%s) called the Sales number."
                            % (who, params.get("From") or "unknown"),
                            "EvoSys Sales - missed call from %s" % who,
                            "<p>%s (%s) called the EvoSys Sales number and nobody answered.</p>"
                            % (who, params.get("From") or "unknown"))
        except Exception:                                            # noqa: BLE001
            log.exception("[evosys voice] missed-call alert failed")
        r.say("Sorry we missed you. We'll call you right back.")
    r.hangup()
    return _twiml(r)


def status_update(db: Session, params: Dict) -> None:
    """Twilio reports how a call went; keep the timeline row current."""
    sid = params.get("ParentCallSid") or params.get("CallSid")
    row = db.query(M).filter(M.provider_message_id == sid, M.channel == "call").first() if sid else None
    if row is None:
        return
    st = params.get("CallStatus") or params.get("DialCallStatus")
    if st:
        row.status = st
    dur = params.get("CallDuration") or params.get("DialCallDuration")
    if dur and st in FINAL:
        row.error = None
        row.body = (row.body or "").split(" · ")[0] + " · %ss" % dur
    db.commit()


def record_outcome(db: Session, opp: Opportunity, call_id: str, outcome: str, note: str, user) -> M:
    row = db.get(M, call_id)
    if row is None or row.opportunity_id != opp.id or row.channel != "call":
        raise ValueError("That call isn't on this lead.")
    if outcome not in OUTCOMES:
        raise ValueError("Pick an outcome.")
    row.label = outcome
    if note:
        row.subject = note[:500]
    db.add(OpportunityEvent(opportunity_id=opp.id, event_type="call_outcome", actor_user_id=user.id,
                            summary="Call: %s" % OUTCOMES[outcome], detail=note or None, occurred_at=datetime.utcnow()))
    db.commit()
    return row
