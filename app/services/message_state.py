"""
ONE vocabulary for the outcome of an outbound message.

Why this module exists
----------------------
Before it, an outbound SMS carried three overlapping status fields
(`twilio_status`, `delivery_status`, and the implicit "there is a row, so it
was sent") and the conversation transcript rendered NONE of them. A message
that Twilio accepted and then failed to deliver looked, in the family's case
file, exactly like a message that arrived. An operator sent a text, saw it in
the history, and reasonably concluded the lead had received it. That is the
bug this module closes.

The five states are the only ones any UI should ever show:

  blocked    - never handed to the provider. A pre-send gate stopped it
               (DNC, suppression list, no sender configured). There is no
               provider SID because no provider request was ever made.
  queued     - accepted by the provider, not yet handed to a carrier.
  sent       - handed to the carrier. NOT a delivery confirmation.
  delivered  - the carrier confirmed handset delivery.
  failed     - the provider or carrier rejected it, or delivery failed.
               `undelivered` and `canceled` both land here; the distinction
               lives in the error code, not in a fourth failure word.

The load-bearing rule is the last branch of `send_state_for`: a row with no
provider SID can never read as `sent` or better, whatever its other columns
say. That makes the guarantee structural rather than a matter of every call
site remembering to set the right value.
"""

BLOCKED   = "blocked"
QUEUED    = "queued"
SENT      = "sent"
DELIVERED = "delivered"
FAILED    = "failed"

ALL_STATES = (BLOCKED, QUEUED, SENT, DELIVERED, FAILED)

# Twilio's message resource statuses -> our vocabulary.
# https://www.twilio.com/docs/messaging/api/message-resource#message-status-values
_TWILIO_MAP = {
    "accepted":    QUEUED,
    "scheduled":   QUEUED,
    "queued":      QUEUED,
    "sending":     SENT,
    "sent":        SENT,
    "receiving":   SENT,
    "received":    DELIVERED,
    "delivered":   DELIVERED,
    "read":        DELIVERED,
    "undelivered": FAILED,
    "failed":      FAILED,
    "canceled":    FAILED,
    "cancelled":   FAILED,
}


def normalize_provider_status(raw: str | None) -> str:
    """Map one provider status string onto the five-state vocabulary.

    An unknown or empty status is QUEUED, not SENT: the safe reading of
    "we don't recognise this" is "it has not got anywhere yet", never
    "it arrived".
    """
    key = (raw or "").strip().lower()
    return _TWILIO_MAP.get(key, QUEUED)


def send_state_for(message) -> str:
    """The state to display for a Message row.

    Order matters. The stored `send_state` is trusted first because the
    status callback writes it from the provider's own receipt. Everything
    below is reconstruction for rows written before that column existed.

    The final guard is the point of the whole module: no provider SID means
    no provider request, which means the message was blocked before
    submission and must never render as sent.
    """
    sid = (getattr(message, "twilio_sid", None) or "").strip()

    stored = (getattr(message, "send_state", None) or "").strip().lower()
    if stored in ALL_STATES:
        # A stored non-blocked state is only believable with a SID behind it.
        if stored == BLOCKED or sid:
            return stored

    if not sid:
        return BLOCKED

    raw = (getattr(message, "delivery_status", None)
           or getattr(message, "twilio_status", None))
    # "pending" is this codebase's own placeholder, not a Twilio status; it
    # means "submitted, no receipt yet", which is QUEUED.
    if (raw or "").strip().lower() == "pending":
        return QUEUED
    return normalize_provider_status(raw)


# Presentation is defined once, here, so the transcript, the activity feed and
# any future surface cannot drift into describing the same row differently.
_LABELS = {
    BLOCKED:   ("Blocked",   "Never sent to the carrier"),
    QUEUED:    ("Queued",    "Accepted by the carrier, not yet sent"),
    SENT:      ("Sent",      "Handed to the carrier - delivery not yet confirmed"),
    DELIVERED: ("Delivered", "Confirmed delivered to the handset"),
    FAILED:    ("Failed",    "The carrier did not deliver this message"),
}


def state_label(state: str) -> str:
    return _LABELS.get(state, _LABELS[QUEUED])[0]


def state_description(state: str) -> str:
    return _LABELS.get(state, _LABELS[QUEUED])[1]


def is_terminal_failure(state: str) -> bool:
    return state == FAILED


def describe(message) -> dict:
    """The delivery block every API response should embed for a message row."""
    state = send_state_for(message)
    code = getattr(message, "error_code", None)
    return {
        "state": state,
        "label": state_label(state),
        "description": state_description(state),
        "error_code": code,
        "error_message": getattr(message, "error_message", None),
        "provider_sid": getattr(message, "twilio_sid", None),
    }
