"""Serialize a stored inbound Reply for the lead conversation timeline.

Dependency-free on purpose (no ORM import): it only reads attributes, so the
channel/identity/preview contract can be proven without a database.

`replies.source` is "sms" | "email". The lead-detail `/timeline` feed used to
hardcode channel "sms" for every inbound row, so an email reply stored by the
mailbox poller was in the Reply table (and in the notification) but showed in
the EvoSys conversation as a text message. /history already honoured `source`;
this makes /timeline agree with it.
"""

PREVIEW_CHARS = 120


def reply_channel(reply) -> str:
    src = (getattr(reply, "source", None) or "sms").strip().lower()
    return src if src in ("sms", "email") else "sms"


def inbound_reply_event(reply) -> dict:
    body = getattr(reply, "body", None) or ""
    return {
        "id": getattr(reply, "id", None),
        "type": "inbound",
        "channel": reply_channel(reply),
        "body": body,
        "body_preview": body[:PREVIEW_CHARS],
        "timestamp": getattr(reply, "received_at", None),
        "is_hot": getattr(reply, "is_hot", None),
    }
