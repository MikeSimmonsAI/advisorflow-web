"""THE SAME THING MUST NOT HAPPEN TWICE.

ASSUME EVERY WORKER RETRIES AND EVERY EVENT ARRIVES MORE THAN ONCE. That is
not pessimism, it is how the platform already behaves: Twilio redelivers a
webhook after a timeout, Render restarts a job mid-run, and two loops can
overlap when one is slow. A system that is correct only when each message
arrives exactly once is a system that is correct on quiet days.

THE GUARANTEE IS A UNIQUE INDEX, NOT A CHECK.

A "has this happened already?" query followed by an insert is two statements
with a gap between them, and two workers will both read "no" in that gap.
`ai_ops_actions` and `ai_communications` each carry a unique constraint on
(organization_id, idempotency_key); the second writer's INSERT fails at the
database and this module turns that failure into the RIGHT ANSWER — "already
done, here is the original" — rather than into an error the caller might
retry a third time.

WHAT GOES INTO A KEY. Enough to make two genuinely different actions
different, and nothing that varies between two attempts at the SAME action.
A timestamp is therefore never in a key: including one would make every retry
a new action, which is precisely the bug. Content IS in the key, as a digest,
because "send this exact text to this person on this thread" twice is a
duplicate while "send a different text" is a follow-up.
"""

import logging
from typing import Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ai_operations_models import (AICommunication, AIOpsAction,
                                             AIScheduledAction)
from app.services.ai_operations import audit
from app.services.ai_operations import constants as C

_log = logging.getLogger(__name__)


def key_for_send(*, thread_id: str, channel: str, body: str,
                 subject_line: Optional[str] = None,
                 attempt_group: Optional[str] = None) -> str:
    """The key for one outbound message.

    `attempt_group` exists for the legitimate case of deliberately sending
    the same words twice — a scheduling coordinator's reminder on day 1 and
    day 7 is the same text and is not a duplicate. The caller passes the
    scheduled action's id, which is stable across retries of THAT action and
    different between the two reminders.
    """
    parts = ["send", thread_id or "", channel or "",
             audit.digest(subject_line) or "", audit.digest(body) or "",
             attempt_group or ""]
    return audit.digest("|".join(parts)) or "send"


def key_for_booking(*, thread_id: str, subject_id: str, start_at: str) -> str:
    """The key for booking one slot for one person.

    Deliberately NOT including the employee: two employees booking the same
    person into the same slot is a duplicate appointment for that family
    whichever one of them asked first.
    """
    return audit.digest("|".join(["book", subject_id or "", start_at or "",
                                  thread_id or ""])) or "book"


def key_for_inbound(*, provider: str, provider_event_id: str) -> str:
    return audit.digest("|".join(["inbound", provider or "",
                                  provider_event_id or ""])) or "inbound"


def key_for_action(*, operation: str, thread_id: str, subject_id: str,
                   discriminator: Optional[str] = None) -> str:
    return audit.digest("|".join([operation or "", thread_id or "",
                                  subject_id or "",
                                  discriminator or ""])) or "action"


def key_for_followup(*, thread_id: str, operation: str,
                     scheduled_for: str) -> str:
    """The key for a scheduled action.

    The scheduled TIME is part of it, unlike everywhere else, and for a
    reason that is the mirror image of the rule above: two follow-ups at the
    same minute on the same thread are a double-booking of the same slot,
    while the same follow-up moved to tomorrow is a legitimately different
    intent that must be allowed to exist.
    """
    return audit.digest("|".join(["followup", thread_id or "", operation or "",
                                  scheduled_for or ""])) or "followup"


def find_action(db: Session, organization_id: str, key: str
                ) -> Optional[AIOpsAction]:
    if not key:
        return None
    return (db.query(AIOpsAction)
            .filter(AIOpsAction.organization_id == organization_id,
                    AIOpsAction.idempotency_key == key)
            .first())


def find_communication(db: Session, organization_id: str, key: str
                       ) -> Optional[AICommunication]:
    if not key:
        return None
    return (db.query(AICommunication)
            .filter(AICommunication.organization_id == organization_id,
                    AICommunication.idempotency_key == key)
            .first())


def find_scheduled(db: Session, organization_id: str, key: str
                   ) -> Optional[AIScheduledAction]:
    if not key:
        return None
    return (db.query(AIScheduledAction)
            .filter(AIScheduledAction.organization_id == organization_id,
                    AIScheduledAction.idempotency_key == key)
            .first())


def claim(db: Session, row, *, organization_id: str, key: str,
          finder) -> Tuple[bool, object]:
    """Insert `row` under `key`, or return the row that already holds it.

    Returns (is_new, row). The caller acts only when `is_new` is True; a
    False means somebody — another worker, an earlier retry, the same worker
    before a timeout it did not see resolve — already did this, and the
    correct behaviour is to report THAT result rather than to produce a
    second one.

    THE SAVEPOINT MATTERS. A failed INSERT poisons the surrounding
    transaction on Postgres: every subsequent statement errors with
    "current transaction is aborted" until a rollback. `begin_nested` gives
    the insert its own savepoint, so losing the race costs the savepoint and
    nothing else — the caller's transaction, including its audit rows,
    survives intact.
    """
    if not key:
        db.add(row)
        db.flush()
        return True, row
    existing = finder(db, organization_id, key)
    if existing is not None:
        return False, existing
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
        return True, row
    except IntegrityError:
        # Lost the race. The winner's row is the answer.
        existing = finder(db, organization_id, key)
        if existing is not None:
            _log.info("ai_operations: idempotency collision on %s — "
                      "returning the original", key)
            return False, existing
        # A unique violation with no visible winner means the constraint that
        # fired was a different one; re-raising is correct because this is a
        # real defect rather than a race.
        raise


def duplicate_refusal(kind: str, key: str) -> Tuple[str, str]:
    """The refusal a duplicate produces, in the standard shape."""
    return C.D_DUPLICATE, (
        "This %s has already been performed (idempotency key %s); the "
        "original result is returned rather than repeated." % (kind, key))
