"""Families who said yes to coming in and still have no time (Henry, Oct 22).

Every pass (the program loop runs this every 30 minutes) finds a family whose
latest message says they want to come in, that is at least an hour old, with
no booking and no real times or booking link sent to them since. The team gets
ONE text + email per family per day, with a link straight to the family.
Read-only on the family: nothing is ever sent to them from here.
"""
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

WAIT = timedelta(hours=1)            # how long a yes may sit without a time
LOOKBACK = timedelta(days=2)         # older than this is history, not a live yes
_LINK = re.compile(r"https?://", re.I)
_TIME = re.compile(r"\b\d{1,2}(:\d{2})?\s?(am|pm)\b", re.I)


def _family_wants_in(text: str) -> bool:
    from app.services.programs import ai_setter as S
    today = datetime.utcnow().date()
    return S.wants_visit(text) or S.future_visit_day(text, today) is not None


def find(db: Session, org_id: str, now: Optional[datetime] = None) -> List[Dict]:
    """[{lead_id, name, said, at}] - families waiting on a time from us."""
    from app.models.models import Lead, Message
    from app.models.program_models import ProgramConversation, ProgramConversationTurn
    now = now or datetime.utcnow()
    out: List[Dict] = []
    convs = (db.query(ProgramConversation)
             .filter(ProgramConversation.organization_id == org_id,
                     ProgramConversation.booked_time.is_(None),
                     ProgramConversation.state.notin_(("booked", "stopped", "closed")),
                     ProgramConversation.is_test.isnot(True),
                     ProgramConversation.last_family_at >= now - LOOKBACK,
                     ProgramConversation.last_family_at <= now - WAIT)
             .all())
    for conv in convs:
        last = (db.query(ProgramConversationTurn)
                .filter(ProgramConversationTurn.conversation_id == conv.id,
                        ProgramConversationTurn.role == "family")
                .order_by(ProgramConversationTurn.created_at.desc()).first())
        if last is None or not _family_wants_in(last.text or ""):
            continue
        lead = db.query(Lead).filter(Lead.id == conv.lead_id).first()
        if lead is None or getattr(lead, "is_test", False):
            continue
        if (getattr(lead, "status", None) or "").lower() in ("dnc", "opted_out", "unsubscribed"):
            continue
        # Anything we sent AFTER their yes that carries real times or a link
        # (AI offer, staff text, booking confirmation) means they have a way in.
        sent = (db.query(Message.body).filter(Message.lead_id == lead.id,
                                              Message.sent_at >= last.created_at)
                .all())
        if any(_LINK.search(b or "") or _TIME.search(b or "") for (b,) in sent):
            continue
        if conv.offered_at is not None and conv.offered_at >= last.created_at:
            continue
        name = ("%s %s" % ((lead.first_name or "").title(), (lead.last_name or "").title())).strip()
        out.append({"lead_id": lead.id, "conversation_id": conv.id, "name": name or "A family",
                    "said": (last.text or "").strip(), "at": last.created_at})
    return out


def alert_all(db: Session, now: Optional[datetime] = None) -> int:
    """Page the team once per family per day. Returns pages raised."""
    from app.models.program_models import ProgramAlert
    from app.services.programs import alert_ledger as L
    from app.services.programs import identity
    from app.services.programs import responses as R
    from app.services import sms_programs
    import html as _html
    now = now or datetime.utcnow()
    raised = 0
    org_id = sms_programs.sci_org_id(db)
    prog = identity.program_for_org(db, org_id) if org_id else None
    for prog in ([prog] if prog is not None else []):
        for fam in find(db, prog.organization_id, now):
            key = "stuck_visit:%s:%s" % (fam["conversation_id"], now.strftime("%Y%m%d"))
            if db.query(ProgramAlert.id).filter(ProgramAlert.dedupe_key == key).first() is not None:
                continue
            mins = int((now - fam["at"]).total_seconds() // 60)
            sms = L.sms_line("sla_breach", fam["name"],
                             detail="wants to come in, no time sent yet (%d min). Send times" % mins,
                             link=L.family_link(fam["lead_id"]))
            subject = "%s - %s wants to come in, no time sent yet" % (R.alert_title(prog), fam["name"])
            body = ("<p><b>%s</b> said: &ldquo;%s&rdquo;</p><p>That was %d minutes ago. No booking, and no "
                    "times or booking link have gone to them since. Open the family and tap "
                    "<b>Text them times to pick</b> or book a time.</p>"
                    % (_html.escape(fam["name"]), _html.escape(fam["said"][:300]), mins))
            try:
                L.page(db, prog, lead_id=fam["lead_id"], key=key, kind="sla_breach", sms_text=sms,
                       subject=subject, html=body, management=True, coalesce=False,
                       audit_message="%s wants to come in, no time sent (%d min)" % (fam["name"], mins))
                db.commit()
                raised += 1
            except Exception:                            # noqa: BLE001
                db.rollback()
                log.exception("stuck-visit alert failed for lead %s", fam["lead_id"])
    return raised
