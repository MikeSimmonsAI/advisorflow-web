"""THE OAUTH `state` PARAMETER IS NOT AN AUTHORIZATION.

This module is the only place that creates or resolves a provider `state`
value, and it is the answer to a confirmed defect: the Google and Microsoft
callbacks used to read the advisor's user_id straight out of `state` and store
the resulting refresh token against it. `state` travels through the user's own
browser, so that made "whose integration is this" a field the caller filled in.
A working authorization against an attacker's own Google account, redirected
back with somebody else's user_id in `state`, overwrote that person's stored
credentials.

WHAT `state` IS NOW

    "<transaction id>.<secret>"

The id selects a row written at INITIATION, when the caller's identity was
still known from their session or from a verified setup token. The secret is
proven against a stored SHA-256 digest. The row carries the identity, the
provider, the flow and the tenant; the browser carries nothing but a handle.

FOUR REFUSALS, ONE PER REPORTED ATTACK

  identity substitution  the user is read from the row, never from the URL
  state tampering        a changed id or secret fails the digest comparison
  replay                 consumption is a conditional UPDATE; the second
                         callback to present the same state loses the race
  expiry                 a transaction older than its TTL is refused

plus WRONG PROVIDER / WRONG CONTEXT: a Google transaction presented to the
Microsoft callback is refused, and vice versa.

FAIL CLOSED. Every failure raises `OAuthStateError`. There is no branch that
falls back to treating the raw state as a user id — that fallback IS the
vulnerability, and it is not kept for compatibility. A setup link issued before
this deploy therefore has to be re-started from the setup page (the link itself
still works; the OAuth hop is re-initiated by it), which is the intended
trade: no in-flight authorization is worth leaving an account-takeover open.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.oauth_models import (FLOW_SETTINGS, FLOW_SETUP, FLOWS,
                                     PROVIDERS, OAuthAuthorizationTransaction)

_log = logging.getLogger(__name__)

# The secret half of the state string. 32 bytes of urandom, urlsafe-encoded.
# Long enough that guessing is not a strategy and short enough that the whole
# state stays well inside every provider's state length limit.
_SECRET_BYTES = 32

# Consumed and expired rows are evidence for about a day and litter after that.
# Swept opportunistically on initiation rather than by a cron, because the
# table only grows when somebody starts a connect flow.
_SWEEP_AFTER_HOURS = 24

__all__ = [
    "OAuthStateError",
    "issue_state",
    "consume_state",
    "FLOW_SETTINGS",
    "FLOW_SETUP",
]


class OAuthStateError(Exception):
    """A state value that may not be honoured, for any reason.

    Deliberately ONE exception type with a short, non-specific message. The
    callback turns this into a generic `connection_failed` redirect: telling a
    caller whether a transaction was expired, already used, or belonged to
    someone else is a probing oracle, and none of those answers helps the
    legitimate advisor, who only ever needs "start again".
    """


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _sweep(db: Session) -> None:
    """Best effort housekeeping. Never allowed to fail an authorization."""
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=_SWEEP_AFTER_HOURS)
        (db.query(OAuthAuthorizationTransaction)
           .filter(or_(OAuthAuthorizationTransaction.consumed_at < cutoff,
                       OAuthAuthorizationTransaction.expires_at < cutoff))
           .delete(synchronize_session=False))
        db.commit()
    except Exception:                                       # noqa: BLE001
        _log.debug("oauth transaction sweep failed", exc_info=True)
        try:
            db.rollback()
        except Exception:                                   # noqa: BLE001
            pass


def issue_state(db: Session, *, provider: str, subject: User,
                flow: str = FLOW_SETTINGS,
                initiated_by_user_id: Optional[str] = None,
                client_ip: Optional[str] = None) -> str:
    """Start an authorization transaction and return the `state` to send.

    `subject` is the user whose provider grant this will become. The caller is
    responsible for having proven it may act for that subject — a session for
    the settings flow, a verified setup token for the setup flow. This function
    records that decision; it does not make it.
    """
    if provider not in PROVIDERS:
        raise OAuthStateError("Unsupported provider.")
    if flow not in FLOWS:
        raise OAuthStateError("Unsupported flow.")
    if subject is None or not getattr(subject, "id", None):
        raise OAuthStateError("No subject for authorization.")

    # ── THE TENANT COMES FROM THE ROW, NOT FROM THE REQUEST'S COPY ──────────
    #
    # `deps.get_current_user` DETACHES a god_admin and then mutates the copy:
    # it nulls organization_id for neutral God Mode and writes the
    # X-Org-Override value when a customer is selected. Those are request-scoped
    # fictions, correct for the request and wrong as a durable record. Recording
    # one here would make `consume_state` compare a fiction against the real
    # column minutes later and refuse the owner's own calendar connection —
    # a boundary check that fires on legitimate use is a bug, not a boundary.
    #
    # Re-reading also re-asserts, at initiation, that the subject exists and is
    # active. `consume_state` asks the same two questions again at the far end,
    # because the window between them is exactly where an account can change.
    persisted = db.query(User).filter(User.id == subject.id).first()
    if persisted is None or not persisted.is_active:
        raise OAuthStateError("No active subject for authorization.")

    _sweep(db)

    secret = secrets.token_urlsafe(_SECRET_BYTES)
    txn = OAuthAuthorizationTransaction(
        secret_hash=_digest(secret),
        provider=provider,
        flow=flow,
        user_id=persisted.id,
        organization_id=persisted.organization_id,
        initiated_by_user_id=initiated_by_user_id or persisted.id,
        expires_at=OAuthAuthorizationTransaction.default_expiry(),
        created_ip=(client_ip or None),
    )
    db.add(txn)
    db.commit()

    _log.info("AUDIT: oauth authorization started provider=%s flow=%s "
              "subject=%s initiated_by=%s txn=%s",
              provider, flow, txn.user_id, txn.initiated_by_user_id, txn.id)
    return "%s.%s" % (txn.id, secret)


def consume_state(db: Session, state: str, *, provider: str
                  ) -> OAuthAuthorizationTransaction:
    """Resolve and spend a state value, or raise `OAuthStateError`.

    Returns the transaction with `consumed_at` already set. The caller reads
    `user_id` and `flow` from the returned row and from nowhere else.

    CONSUMED BEFORE THE CODE IS EXCHANGED, deliberately. A transaction that is
    spent on a token exchange that then fails is gone, and the advisor starts
    over — one wasted click. The alternative, consuming only on success, leaves
    a live single-use credential in a browser's history for every abandoned or
    failed attempt, which is the replay window this exists to close.
    """
    if provider not in PROVIDERS:
        raise OAuthStateError("Unsupported provider.")
    if not state or not isinstance(state, str) or "." not in state:
        raise OAuthStateError("Malformed authorization state.")

    txn_id, _, secret = state.partition(".")
    if not txn_id or not secret:
        raise OAuthStateError("Malformed authorization state.")

    txn = (db.query(OAuthAuthorizationTransaction)
             .filter(OAuthAuthorizationTransaction.id == txn_id)
             .first())
    if txn is None:
        raise OAuthStateError("Unknown authorization state.")

    # CONSTANT-TIME, and against the digest rather than the secret, so the row
    # itself is not a usable credential if the database is ever read.
    if not hmac.compare_digest(txn.secret_hash or "", _digest(secret)):
        _log.warning("AUDIT: oauth state secret mismatch txn=%s provider=%s",
                     txn.id, provider)
        raise OAuthStateError("Authorization state does not verify.")

    if txn.provider != provider:
        _log.warning("AUDIT: oauth state presented to the wrong provider "
                     "txn=%s issued_for=%s presented_to=%s",
                     txn.id, txn.provider, provider)
        raise OAuthStateError("Authorization state is for another provider.")

    if txn.is_expired:
        raise OAuthStateError("Authorization state has expired.")

    # ── SINGLE USE, ENFORCED BY THE DATABASE ────────────────────────────────
    #
    # A conditional UPDATE, not a read-then-write. Two callbacks arriving at
    # once both pass the checks above; only the one whose UPDATE matches a row
    # with `consumed_at IS NULL` gets rowcount 1. Checking `txn.is_consumed`
    # in Python and then writing would let both through under concurrency,
    # which is exactly the replay this has to refuse.
    now = datetime.now(timezone.utc)
    updated = (db.query(OAuthAuthorizationTransaction)
                 .filter(OAuthAuthorizationTransaction.id == txn.id,
                         OAuthAuthorizationTransaction.consumed_at.is_(None))
                 .update({"consumed_at": now}, synchronize_session=False))
    db.commit()
    if not updated:
        _log.warning("AUDIT: oauth state replay refused txn=%s provider=%s",
                     txn.id, provider)
        raise OAuthStateError("Authorization state has already been used.")
    db.refresh(txn)

    # ── THE SUBJECT MUST STILL BE THE SUBJECT ───────────────────────────────
    #
    # Minutes pass between initiation and callback. In that window an account
    # can be deactivated, deleted, or moved to another organization. Honouring
    # the transaction anyway would attach a live provider grant to an account
    # that is no longer entitled to one, or write it across a tenant boundary
    # that did not exist when the flow started.
    subject = db.query(User).filter(User.id == txn.user_id).first()
    if subject is None or not subject.is_active:
        raise OAuthStateError("Authorization subject is no longer active.")
    if (getattr(subject, "organization_id", None) or None) != (
            txn.organization_id or None):
        _log.warning("AUDIT: oauth subject changed organization mid-flow "
                     "txn=%s user=%s", txn.id, txn.user_id)
        raise OAuthStateError("Authorization subject has moved organization.")

    _log.info("AUDIT: oauth authorization consumed provider=%s flow=%s "
              "subject=%s txn=%s", provider, txn.flow, txn.user_id, txn.id)
    return txn
