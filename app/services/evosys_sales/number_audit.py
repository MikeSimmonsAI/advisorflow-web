"""EvoSys BookIt number audit (owner, 2026-11-20): what the PROVIDER says about
the team's number, not what our settings say.

Reads (never changes) the platform Twilio account:
  * the number itself: owning account, voice / SMS / MMS capability, where
    inbound texts and calls are sent;
  * toll-free verification status for it;
  * the last messages and calls from / to it, by status and error code;
  * for recent EvoSys BookIt texts in our database, the number Twilio actually
    sent each one from, grouped by kind (campaign, AI answer, person, booking
    confirmation, reminder).
No credential is returned. Phone numbers of leads are masked to the last 4.
"""
from __future__ import annotations

from collections import Counter
from typing import Dict

from sqlalchemy.orm import Session


def _mask(n) -> str:
    n = str(n or "")
    return ("•••" + n[-4:]) if len(n) > 4 else n


def _kind(m) -> str:
    lab = m.label or ""
    if m.step_no is not None:
        return "campaign"
    if lab == "ai_answer":
        return "ai_answer"
    if lab == "booking_confirmation":
        return "booking_confirmation"
    if lab.startswith(("rd:", "r1:", "rm:")):
        return "reminder"
    return "person" if m.actor_user_id else "other"


def run(db: Session, s) -> Dict:
    from app.services.evosys_sales import sms_setup
    from app.services.evosys_sales.outreach import e164
    from app.models.evosys_sales_models import EvoSysOutreachMessage as M
    out: Dict = {"configured_number": e164(s.sms_from_number)}
    client = sms_setup._client()
    if client is None:
        out["error"] = "The platform Twilio account is not configured."
        return out
    num, err = sms_setup._number(client, s)
    if err:
        out["error"] = err
        return out
    caps = num.capabilities or {}
    out["number"] = {
        "e164": num.phone_number, "sid": num.sid, "account_sid": num.account_sid,
        "friendly_name": num.friendly_name,
        "voice": bool(caps.get("voice")), "sms": bool(caps.get("sms")), "mms": bool(caps.get("mms")),
        "sms_url": num.sms_url or None, "voice_url": num.voice_url or None,
        "voice_application_sid": getattr(num, "voice_application_sid", None) or None,
        "sms_application_sid": getattr(num, "sms_application_sid", None) or None,
        "status_callback": num.status_callback or None,
        "trunk_sid": getattr(num, "trunk_sid", None) or None,
    }
    try:
        acct = client.api.v2010.accounts(num.account_sid).fetch()
        out["account"] = {"sid": acct.sid, "name": acct.friendly_name, "type": acct.type, "status": acct.status,
                          "is_subaccount": bool(acct.owner_account_sid and acct.owner_account_sid != acct.sid)}
    except Exception as e:                                           # noqa: BLE001
        out["account"] = {"error": str(e)[:200]}
    try:
        vs = [v for v in client.messaging.v1.tollfree_verifications.list(limit=50)
              if getattr(v, "tollfree_phone_number_sid", None) == num.sid]
        out["tollfree_verification"] = ([{"status": v.status, "rejection_reason": getattr(v, "rejection_reason", None),
                                          "error_code": getattr(v, "error_code", None),
                                          "updated": str(getattr(v, "date_updated", "") or "")} for v in vs]
                                        or "none found for this number")
    except Exception as e:                                           # noqa: BLE001
        out["tollfree_verification"] = {"error": str(e)[:200]}
    try:
        sent = client.messages.list(from_=num.phone_number, limit=300)
        got = client.messages.list(to=num.phone_number, limit=100)
        out["messages_from_number"] = {"count": len(sent), "by_status": dict(Counter(m.status for m in sent)),
                                       "error_codes": dict(Counter(str(m.error_code) for m in sent if m.error_code)),
                                       "newest": str(sent[0].date_sent or sent[0].date_created) if sent else None}
        out["messages_to_number"] = {"count": len(got), "newest": str(got[0].date_created) if got else None}
    except Exception as e:                                           # noqa: BLE001
        out["messages_from_number"] = {"error": str(e)[:200]}
    try:
        calls_out = client.calls.list(from_=num.phone_number, limit=50)
        calls_in = client.calls.list(to=num.phone_number, limit=50)
        out["calls"] = {"from_number": {"count": len(calls_out), "by_status": dict(Counter(c.status for c in calls_out))},
                        "to_number": {"count": len(calls_in), "by_status": dict(Counter(c.status for c in calls_in)),
                                      "newest": str(calls_in[0].start_time) if calls_in else None}}
    except Exception as e:                                           # noqa: BLE001
        out["calls"] = {"error": str(e)[:200]}
    # What Twilio says each kind of EvoSys BookIt text was really sent from.
    rows = (db.query(M).filter(M.brand_sales_org_id == s.brand_sales_org_id, M.channel == "text",
                               M.direction == "out", M.provider_message_id.isnot(None))
            .order_by(M.created_at.desc()).limit(400).all())
    by_kind, seen = {}, Counter()
    for m in rows:
        k = _kind(m)
        if seen[k] >= 5:
            continue
        seen[k] += 1
        try:
            tm = client.messages(m.provider_message_id).fetch()
            by_kind.setdefault(k, []).append({"from": tm.from_, "to": _mask(tm.to), "status": tm.status,
                                              "error_code": tm.error_code, "at": str(tm.date_created)})
        except Exception as e:                                       # noqa: BLE001
            by_kind.setdefault(k, []).append({"error": str(e)[:120]})
    out["evosys_texts_sent_from"] = by_kind
    froms = {x.get("from") for v in by_kind.values() for x in v if x.get("from")}
    out["all_customer_texts_from_this_number"] = froms <= {num.phone_number} if froms else None
    out["staff_alerts_sent_from"] = e164(s.sms_from_number)
    return out
