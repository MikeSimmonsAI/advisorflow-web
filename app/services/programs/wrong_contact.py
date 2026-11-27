"""A wrong number is about the PHONE, not the family (owner, Nov 27).

When someone texts back "wrong number" (or "wrong person" / "this isn't her")
the number we have doesn't reach the family - but their email may. Before
this, that reply stopped everything: the texts, the emails and the record
went to Data Review. Now, when the family has an email we can use:

  * texting that number stops for good (the number stays on the record,
    marked as not to be texted - nothing is deleted),
  * the reply no longer counts as "the family replied, a person has it", so
    their email follow-ups carry on as scheduled,
  * a note on the family's record says what happened and when.

The same thing can be done by hand from the Inbox ("Wrong number - email
only"), and undone (`undo=True`) if the number turns out to be right.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Dict, Optional

from sqlalchemy.orm import Session

# What a text that says the NUMBER is wrong looks like (not "wrong email").
_WRONG_PHONE = re.compile(r"\b(wrong (number|person|#)|not (him|her|me|my (mom|dad|husband|wife))|"
                          r"this is not (him|her|me)|no one (here )?by that name|nobody (here )?by that name|"
                          r"don'?t know (who|anyone|any)|you have the wrong|not the right (person|number))", re.I)
_WRONG_OTHER = re.compile(r"\bwrong (e-?mail|address)\b", re.I)
FLAG = "WRONG NUMBER (text) - texts stopped, emails continue"
_OLD_FLAGS = ("WRONG NUMBER / ADDRESS (reply)", "WRONG PERSON (reply)")


def says_wrong_phone(body: str) -> bool:
    t = body or ""
    return bool(_WRONG_PHONE.search(t)) and not _WRONG_OTHER.search(t)


def email_usable(lead) -> bool:
    return bool((lead.email or "").strip()) and lead.allow_email is not False \
        and (lead.manual_flag or "") != "bad_email" and (lead.status or "").lower() != "dnc"


def is_wrong_number_only(db: Session, lead) -> bool:
    """Every real reply from this family is a text saying the number is wrong,
    and the family is now email-only: they haven't actually answered us."""
    from app.models.program_models import ProgramResponse
    if (lead.contact_channel or "") != "email_only" or (lead.phone_line_type or "") != "invalid":
        return False
    rows = (db.query(ProgramResponse.response_class, ProgramResponse.channel)
            .filter(ProgramResponse.organization_id == lead.organization_id,
                    ProgramResponse.lead_id == lead.id,
                    ProgramResponse.response_class.notin_(("auto_reply", "opt_out"))).all())
    return bool(rows) and all(c in ("bad_data", "wrong_person") and (ch or "") != "email" for c, ch in rows)


def mark_wrong_number(db: Session, prog, lead, *, by: str, now: Optional[datetime] = None,
                      undo: bool = False) -> Dict:
    """Stop texting this family's number and keep emailing them (or undo it)."""
    from app.models.models import CadenceState
    from app.models.program_models import ProgramSourceRecord
    from app.services.programs import restland_sheet as RS
    now = now or datetime.utcnow()
    recs = (db.query(ProgramSourceRecord)
            .filter(ProgramSourceRecord.organization_id == lead.organization_id,
                    ProgramSourceRecord.lead_id == lead.id).all())
    if undo:
        lead.contact_channel = "sms"
        lead.phone_line_type = None
        lead.phone_line_checked_at = None
        for rec in recs:
            flags = [f for f in json.loads(rec.data_note_flags or "[]") if f != FLAG]
            rec.data_note_flags = json.dumps(flags)
        RS._note(db, lead.organization_id, lead.id,
                 "%s: the phone number is right after all - texting allowed again." % by)
        return {"ok": True, "texts": "allowed", "emails": "unchanged"}
    lead.contact_channel = "email_only"
    lead.phone_line_type = "invalid"                  # never texted (send_approval.textable)
    lead.phone_line_checked_at = now
    st = db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
    if st is not None and str(getattr(st.status, "value", st.status)) in ("active", "paused"):
        st.status = "stopped_replied"                 # the TEXT sequence ends here
    if (lead.status or "").lower() == "replied":
        lead.status = "sent"                          # not a real answer: the email sequence carries on
    for rec in recs:
        flags = [f for f in json.loads(rec.data_note_flags or "[]") if f not in _OLD_FLAGS]
        if FLAG not in flags:
            flags.append(FLAG)
        rec.data_note_flags = json.dumps(flags)
        if not [f for f in flags if f != FLAG]:
            rec.needs_data_review = False             # nothing left for a person to fix
    emails = email_usable(lead)
    RS._note(db, lead.organization_id, lead.id,
             "%s: %s says it's a wrong number - texts to that number stopped%s." % (
                 by, lead.phone or "the phone", "; emails continue" if emails else "; no usable email on file"))
    return {"ok": True, "texts": "stopped", "emails": "continue" if emails else "no usable email"}
