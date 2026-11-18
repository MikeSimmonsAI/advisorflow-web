"""EvoSys Sales: texts that come back to the team's texting number.

Called from the authenticated Twilio inbound webhook (sms_router) BEFORE the
customer-side pipeline. Returns False for any number that is not the EvoSys
Sales texting number, so every other program's replies are untouched.

For a text to the EvoSys Sales number:
  * STOP / UNSUBSCRIBE / CANCEL / END / QUIT / STOPALL / "remove me" ...
        -> the lead's follow-up stops for good (opted out) and its phone and
           email go on the EvoSys Sales Do Not Contact list.
  * anything else
        -> stored on the lead, the automatic follow-up stops, and the lead is
           flagged "needs a person" on its timeline. Nothing is auto-answered.
A text from a number that matches no lead is logged and dropped.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.evosys_sales_models import EvoSysOutreachMessage
from app.models.sales_models import Opportunity, OpportunityEvent

log = logging.getLogger(__name__)


def _digits10(p) -> Optional[str]:
    from app.services.evosys_sales.outreach import e164
    e = e164(p)
    return e[2:] if e else None


def is_team_number(db: Session, to_number: str) -> bool:
    from app.services.evosys_sales import outreach as O
    bso = O.brand_id(db)
    if not bso:
        return False
    s = O.get_settings(db, bso)
    mine = O.e164(s.sms_from_number)
    return bool(mine) and mine == O.e164(to_number)


def _find_lead(db: Session, bso_id: str, from_number: str) -> Optional[Opportunity]:
    d = _digits10(from_number)
    if not d:
        return None
    # Stored phones are free text ("(214) 555-0101", "+1214..."): compare digits.
    cands = [o for o in (db.query(Opportunity)
                         .filter(Opportunity.brand_sales_org_id == bso_id, Opportunity.phone.isnot(None))
                         .all())
             if _digits10(o.phone) == d]
    if not cands:
        return None
    # The one we last texted wins; otherwise the most recently updated.
    texted = (db.query(EvoSysOutreachMessage.opportunity_id)
              .filter(EvoSysOutreachMessage.opportunity_id.in_([o.id for o in cands]),
                      EvoSysOutreachMessage.channel == "text",
                      EvoSysOutreachMessage.direction == "out")
              .order_by(EvoSysOutreachMessage.created_at.desc()).first())
    if texted:
        return next(o for o in cands if o.id == texted[0])
    return cands[0]


def handle(db: Session, *, from_number: str, to_number: str, body: str, message_sid: str) -> bool:
    """True when this text belonged to EvoSys Sales (handled here)."""
    if not is_team_number(db, to_number):
        return False
    from app.services.evosys_sales import outreach as O
    from app.services.optout_parser import contains_hard_stop_language
    bso = O.brand_id(db)
    now = datetime.utcnow()
    opp = _find_lead(db, bso, from_number)
    if opp is None:
        log.warning("[evosys-sales sms] text to the team number from a number on no lead (sid %s)", message_sid)
        return True
    if message_sid and db.query(EvoSysOutreachMessage.id).filter(
            EvoSysOutreachMessage.opportunity_id == opp.id,
            EvoSysOutreachMessage.provider_message_id == message_sid).first():
        return True                       # Twilio retry: already recorded
    text = (body or "").strip()
    opted_out = contains_hard_stop_language(text)
    db.add(EvoSysOutreachMessage(brand_sales_org_id=bso, opportunity_id=opp.id, direction="in",
                                 channel="text", to_address=O.e164(from_number), body=text[:5000] or "(empty text)",
                                 status="received", provider_message_id=message_sid, match_method="sender",
                                 label="unsubscribe" if opted_out else None, created_at=now))
    if opted_out:
        O.stop(db, opp.id, "opted_out", now)
        _do_not_contact(db, opp, text)
        db.add(OpportunityEvent(opportunity_id=opp.id, event_type="outreach_opt_out",
                                summary="Texted STOP - opted out, no more messages",
                                detail=text[:2000], occurred_at=now))
    else:
        O.stop(db, opp.id, "replied", now)
        db.add(OpportunityEvent(opportunity_id=opp.id, event_type="outreach_reply",
                                summary="NEEDS A PERSON - replied by text, automatic follow-up stopped",
                                detail=text[:2000], occurred_at=now))
    db.commit()
    if not opted_out:
        _alert_owner(db, O.get_settings(db, bso), opp, text)
    return True


def _alert_owner(db: Session, s, opp: Opportunity, text: str) -> None:
    """Tell the owner a prospect texted back - by text and email, the same way
    SCI and Restland replies reach him. A failed alert never loses the reply
    (it is already on the lead)."""
    who = opp.company_name or opp.contact_name or "A prospect"
    phone = opp.phone or ""
    line = "EvoSys Sales - NEEDS A PERSON: %s (%s) texted back: \"%s\"" % (who, phone, (text or "")[:160])
    if s.alert_phone:
        try:
            from app.services import sms_programs as _sci
            client, from_ = _sci.sci_sender()
            from app.services.twilio_callbacks import apply_status_callback
            client.messages.create(**apply_status_callback(dict(body=line[:320], from_=from_, to=s.alert_phone)))
        except Exception:                                   # noqa: BLE001
            log.exception("[evosys-sales sms] reply alert text failed for lead %s", opp.id)
    if s.alert_email:
        try:
            import html as _html
            from app.services.email_service import send_email_via_provider
            from app.services.evosys_sales.outreach import _Sender
            body = ("<p><b>%s</b> (%s) texted back to the EvoSys Sales number:</p><blockquote>%s</blockquote>"
                    "<p>The automatic follow-up for this lead has stopped. Open the lead in EvoSys Sales to answer.</p>"
                    % (_html.escape(who), _html.escape(phone), _html.escape(text or "")))
            send_email_via_provider(s.alert_email, "EvoSys Sales - NEEDS A PERSON: %s texted back" % who, body,
                                    org=_Sender(s), message_type="notification", sensitivity="none")
        except Exception:                                   # noqa: BLE001
            log.exception("[evosys-sales sms] reply alert email failed for lead %s", opp.id)


def _do_not_contact(db: Session, opp: Opportunity, text: str) -> None:
    from app.services import sales_suppression as SDNC
    from app.models.models import User
    uid = opp.owner_user_id
    user = db.get(User, uid) if uid else None
    if user is None:
        log.error("[evosys-sales sms] opt-out on lead %s has no owner to record it under; "
                  "follow-up is stopped but the Do Not Contact row was not written", opp.id)
        return
    try:
        SDNC.record(db, opp, user, "Texted STOP to the EvoSys Sales number: %s" % text[:200])
    except ValueError:
        log.exception("[evosys-sales sms] could not record Do Not Contact for lead %s", opp.id)
