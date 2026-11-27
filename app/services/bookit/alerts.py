"""Restland BookIT: a family's own incoming message -> the assigned advisor's
bell (owner, Nov 26: "CUSTOMER MESSAGE NOTIFICATIONS").

Source: the family turns the conversation engine records when a genuine
inbound text or email arrives (ProgramConversationTurn, role "family"). Our
own outgoing messages, delivery receipts and sync retries never create a
family turn, so they never alert. One alert per inbound message (dedupe on
the received message id), to the advisor the follow-up on the board is assigned to. Several messages
before the advisor acts share one alert. Reading the alert does not touch the follow-up.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.services.bookit.core import notify

log = logging.getLogger(__name__)

LOOKBACK = timedelta(days=2)       # alerts are for replies that arrive now, not history
EVERY_SECONDS = 30                 # the bell polls; the sweep runs at most this often per org
_last: Dict[str, float] = {}


def sweep(db: Session, prog, now: Optional[datetime] = None, force: bool = False) -> int:
    """Create the missing incoming-message alerts. Returns how many were added."""
    import json
    from app.models.models import Lead
    from app.models.program_models import HandoffSheetRow as R
    from app.models.program_models import ProgramConversation as C
    from app.models.program_models import ProgramConversationTurn as T
    from app.services.programs import restland_sheet as RS
    org = prog.organization_id
    if not force and time.monotonic() - _last.get(org, 0.0) < EVERY_SECONDS:
        return 0
    _last[org] = time.monotonic()
    now = now or datetime.utcnow()
    turns = (db.query(T, C).join(C, C.id == T.conversation_id)
             .filter(T.organization_id == org, T.role == "family", T.created_at >= now - LOOKBACK)
             .order_by(T.created_at).all())
    if not turns:
        return 0
    leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_({c.lead_id for _, c in turns})).all()}
    rows = {r.row_id: r for r in db.query(R).filter(R.organization_id == org,
                                                   R.row_id.in_({"FU-" + c.id for _, c in turns})).all()}
    try:
        ev = RS.followup_events(db, list({c.id: c for _, c in turns}.values()))
    except Exception:                                    # noqa: BLE001
        log.exception("alerts: follow-up events lookup failed")
        ev = {}
    from app.models.bookit_models import BookitNotification as N
    last_alert = {}
    for ref, at in (db.query(N.ref_id, N.created_at).filter(N.organization_id == org, N.kind == "family_reply",
                                                            N.ref_id.in_(list(rows) or ["-"])).all()):
        if at is not None and (ref not in last_alert or at > last_alert[ref]):
            last_alert[ref] = at
    added = 0
    for t, c in turns:
        lead = leads.get(c.lead_id)
        if RS._skip(lead) or c.is_test:
            continue
        rid = "FU-" + c.id
        rec = rows.get(rid)
        if rec is None:
            continue                                     # not a follow-up on the board: nobody to tell
        who = rec.assigned_to or (json.loads(rec.staff_json or "{}").get("assigned") or None)
        if not who:
            continue                                     # unassigned: the round-robin assigns it first
        e = ev.get(c.id) or {}
        team = [x for x in (e.get("human_at"), e.get("note_at"), rec.staff_changed_at) if x is not None]
        if team and t.created_at is not None and max(team) > t.created_at:
            continue                                     # the team already acted after it
        if t.created_at is not None and last_alert.get(rid) and last_alert[rid] >= t.created_at:
            continue                                     # already covered by the alert raised after it
        name = ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip() or "A family"
        text = " ".join((t.text or "").split())
        if notify(db, org, who, "family_reply", "New reply from %s" % name,
                  body=(text[:140] + "…") if len(text) > 140 else text, ref_type="follow_up", ref_id=rid,
                  dedupe="inbound:%s" % (t.response_id or t.message_id or t.id)):
            added += 1
            last_alert[rid] = now
    return added


def safe_sweep(db: Session, prog) -> None:
    """Never let the alert sweep break the page that triggered it."""
    sp = db.begin_nested()
    try:
        n = sweep(db, prog)
        sp.commit()
        if n:
            db.commit()
    except Exception:                                    # noqa: BLE001
        log.exception("alerts: sweep failed")
        if sp.is_active:
            sp.rollback()
