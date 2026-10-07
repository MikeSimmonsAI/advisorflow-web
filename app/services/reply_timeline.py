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


_ACTIVITY_TYPE = {"sms": "sms_reply", "email": "email_reply"}
_ACTIVITY_LABEL = {"sms": "Reply received", "email": "Email reply received"}
_ACTIVITY_HOT_LABEL = {"sms": "\U0001F525 Hot reply received",
                       "email": "\U0001F525 Hot email reply received"}


def reply_activity_event(reply, fmt=lambda v: v) -> dict:
    """Event for the `/leads/{id}/activity` log ({id,type,ts,label,body,meta}).

    Channel comes from the same `reply_channel` as /timeline and /history, so
    the three read paths cannot drift. Unknown/null source keeps the legacy
    SMS shape (type sms_reply, label "Reply received"); no provider is
    invented. `fmt` formats datetimes (the router passes its UTC formatter).
    New field meta.channel; meta.source is unchanged (raw stored value).
    """
    channel = reply_channel(reply)
    label = (_ACTIVITY_HOT_LABEL if getattr(reply, "is_hot", None) else _ACTIVITY_LABEL)[channel]
    classification = getattr(reply, "classification", None)
    return {
        "id": f"reply-{getattr(reply, 'id', None)}",
        "type": _ACTIVITY_TYPE[channel],
        "ts": fmt(getattr(reply, "received_at", None)),
        "label": label,
        "body": getattr(reply, "body", None),
        "meta": {
            "is_hot": getattr(reply, "is_hot", None),
            "hot_reason": getattr(reply, "hot_reason", None),
            "classification": str(classification) if classification else None,
            "reviewed_at": fmt(getattr(reply, "reviewed_at", None)),
            "source": getattr(reply, "source", None),
            "channel": channel,
        },
    }
