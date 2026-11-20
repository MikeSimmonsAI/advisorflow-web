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
    english = None
    try:
        from app.services.evosys_sales import ai as _AI
        if _AI.looks_spanish(text):
            new = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id == opp.id,
                                                          EvoSysOutreachMessage.provider_message_id == message_sid).first())
            if new is not None:
                _AI.fill_translations(db, [new], bso, opp.owner_user_id or "system", background=True)
                english = new.translation
                db.commit()
    except Exception:                                       # noqa: BLE001
        db.rollback()
    if not opted_out:
        s = O.get_settings(db, bso)
        answered = None
        try:
            answered = _auto_answer(db, s, opp, message_sid)
        except Exception:                                   # noqa: BLE001 - the reply is safe on the lead
            db.rollback()
            log.exception("[evosys-sales sms] AI answer failed for lead %s", opp.id)
        _alert_owner(db, s, opp, text + (" [English: %s]" % english if english else ""), answered)
    return True


AUTO_MAX_PER_LEAD = 3


def conversation(db: Session, opp_id: str) -> list:
    rows = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id == opp_id,
                                                   EvoSysOutreachMessage.channel == "text",
                                                   EvoSysOutreachMessage.status.in_(("sent", "received")))
            .order_by(EvoSysOutreachMessage.created_at).all())
    return [{"from": "them" if m.direction == "in" else "us", "text": (m.body or "")[:500]} for m in rows]


def _auto_answer(db: Session, s, opp: Opportunity, message_sid: str) -> Optional[str]:
    """When the team turned it on, let AI answer a SIMPLE text reply by itself
    (yes / is it free / how does it work). Anything else waits for a person
    with the draft ready on the lead. Returns the text sent, or None."""
    from app.models.evosys_sales_models import EvoSysCampaign, EvoSysOutreachState
    from app.services import sales_suppression as SDNC
    from app.services.evosys_sales import ai as AI
    from app.services.evosys_sales import outreach as O
    reply = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id == opp.id,
                                                    EvoSysOutreachMessage.provider_message_id == message_sid).first())
    if reply is None:
        return None
    st = db.get(EvoSysOutreachState, opp.id)
    camp = db.get(EvoSysCampaign, st.campaign_id) if st is not None and st.campaign_id else None
    if not s.text_auto_reply:
        return None                       # off: a person drafts it from the lead
    from app.services.evosys_sales import inbox as IB
    from app.services.evosys_sales import booking_page as BP
    if (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id == opp.id,
                                               EvoSysOutreachMessage.direction == "in",
                                               EvoSysOutreachMessage.created_at > reply.created_at).first()) is not None:
        return None                       # they sent more since: answer the newest one, once
    if IB.recent_out(db, opp.id) is not None:
        return None                       # something just went to them: never fire a second text on top
    if BP.current(db, opp) is not None:
        return None                       # already booked: Mike answers these himself
    background = True
    t = AI.text_reply(db, s, camp, opp, reply, conversation(db, opp.id), opp.owner_user_id or "system",
                      background=background)
    out = t.output or {}
    if out.get("category"):
        reply.label = reply.label or out["category"]
    db.commit()
    if not out.get("auto_ok"):
        return None
    auto_sent = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id == opp.id,
                                                        EvoSysOutreachMessage.direction == "out",
                                                        EvoSysOutreachMessage.channel == "text",
                                                        EvoSysOutreachMessage.label == "ai_answer").count())
    if auto_sent >= AUTO_MAX_PER_LEAD:
        return None
    if (st is not None and st.opted_out_at) or SDNC.for_opportunity(db, opp) or SDNC.find(db, s.brand_sales_org_id, opp.phone, opp.email):
        return None
    to = O.e164(opp.phone)
    body = out["draft"]
    if IB.duplicate_of(db, opp.id, body) is not None:
        return None                       # they already got these exact words
    r = O._send_text(to, body, s)
    ok = bool(r.get("success"))
    db.add(EvoSysOutreachMessage(brand_sales_org_id=s.brand_sales_org_id, opportunity_id=opp.id, direction="out",
                                 channel="text", to_address=to, body=body, label="ai_answer",
                                 status="sent" if ok else "failed", error=None if ok else str(r.get("error") or "")[:1000],
                                 provider_message_id=r.get("provider_message_id"),
                                 campaign_id=st.campaign_id if st else None, created_at=datetime.utcnow()))
    if ok:
        AI.resolve(db, t, "auto_sent")
        db.add(OpportunityEvent(opportunity_id=opp.id, event_type="outreach_reply_sent",
                                summary="AI answered their text", detail=body[:500], occurred_at=datetime.utcnow()))
    db.commit()
    return body if ok else None


def recipients(s) -> list:
    """(phone, email) for the owner and everyone on alert_also."""
    out = [(s.alert_phone, s.alert_email)]
    for r in (s.alert_also or []):
        if isinstance(r, dict):
            out.append((r.get("phone"), r.get("email")))
    return out


def notify_owner(s, line: str, subject: str, html_body: str) -> None:
    """Text + email everyone who gets EvoSys Sales alerts (same path as SCI
    and Restland alerts). Never raises: a failed alert loses nothing."""
    from app.services.evosys_sales.outreach import e164
    phones, emails = [], []
    for ph, em in recipients(s):
        if e164(ph) and e164(ph) not in phones:
            phones.append(e164(ph))
        if em and em.strip().lower() not in emails:
            emails.append(em.strip().lower())
    if phones:
        try:
            from app.services import sms_programs as _sci
            client, from_ = _sci.sci_sender()
            from app.services.twilio_callbacks import apply_status_callback
            for ph in phones:
                try:
                    client.messages.create(**apply_status_callback(dict(body=line[:320], from_=from_, to=ph)))
                except Exception:                           # noqa: BLE001
                    log.exception("[evosys-sales] alert text to one recipient failed")
        except Exception:                                   # noqa: BLE001
            log.exception("[evosys-sales] owner alert text failed")
    if emails:
        try:
            from app.services.email_service import send_email_via_provider
            from app.services.evosys_sales.outreach import _Sender
            for em in emails:
                try:
                    send_email_via_provider(em, subject, html_body,
                                            org=_Sender(s), message_type="notification", sensitivity="none")
                except Exception:                           # noqa: BLE001
                    log.exception("[evosys-sales] alert email to one recipient failed")
        except Exception:                                   # noqa: BLE001
            log.exception("[evosys-sales] owner alert email failed")


def _alert_owner(db: Session, s, opp: Opportunity, text: str, answered: Optional[str] = None) -> None:
    """Tell the owner a prospect texted back - by text and email, the same way
    SCI and Restland replies reach him."""
    import html as _html
    who = opp.company_name or opp.contact_name or "A prospect"
    phone = opp.phone or ""
    if answered:
        line = "EvoSys Sales - %s (%s) texted: \"%s\" AI answered: \"%s\"" % (who, phone, (text or "")[:110], answered[:120])
    else:
        line = "EvoSys Sales - NEEDS A PERSON: %s (%s) texted back: \"%s\" (draft ready on the lead)" % (
            who, phone, (text or "")[:150])
    body = ("<p><b>%s</b> (%s) texted back to the EvoSys Sales number:</p><blockquote>%s</blockquote>"
            % (_html.escape(who), _html.escape(phone), _html.escape(text or "")))
    body += (("<p>AI answered for you:</p><blockquote>%s</blockquote>" % _html.escape(answered)) if answered else
             "<p>The automatic follow-up has stopped. A draft answer is ready on the lead in EvoSys Sales.</p>")
    subj = ("EvoSys Sales - %s texted back (AI answered)" if answered
            else "EvoSys Sales - NEEDS A PERSON: %s texted back") % who
    notify_owner(s, line, subj, body)


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
