"""
iCalendar (.ics) generation — RFC 5545.

Hand-rolled on purpose. Nothing in this codebase generated .ics before, and
`icalendar` is not a dependency; adding a package to emit a few dozen lines of
well-specified text is not a trade worth making, and a vendored dependency in
the invitation path is one more thing that can break a booking.

Used by BOTH the .ics calendar-provider fallback (internal participants with no
connected calendar) and the prospect invitation email, so there is exactly one
implementation of the format.

WHY EVERYTHING IS EMITTED IN UTC
--------------------------------
DTSTART/DTEND are written as UTC instants with a trailing Z. That is always
unambiguous and needs no VTIMEZONE block. A VTIMEZONE with hand-written DST
rules is the classic way an invitation lands an hour off twice a year, and the
recipient's mail client already renders a UTC instant in their own zone.
"""
import re
from datetime import datetime

PRODID = "-//AdvisorFlow//Scheduling//EN"

METHOD_REQUEST = "REQUEST"
METHOD_CANCEL = "CANCEL"


def _stamp(dt: datetime) -> str:
    """Naive UTC -> iCalendar UTC form."""
    return dt.strftime("%Y%m%dT%H%M%SZ")


def escape_text(value) -> str:
    """RFC 5545 §3.3.11. Backslash MUST be escaped first, or every escape this
    function then adds would itself be re-escaped."""
    if value is None:
        return ""
    s = str(value)
    s = s.replace("\\", "\\\\")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\n", "\\n")
    s = s.replace(";", "\\;").replace(",", "\\,")
    return s


def fold(line: str) -> str:
    """RFC 5545 §3.1: content lines are folded at 75 octets, continuations
    beginning with a single space.

    Folded on OCTETS, not characters. A name with a non-ASCII character is
    multiple bytes in UTF-8, and splitting mid-character produces a file that
    strict parsers reject outright.
    """
    raw = line.encode("utf-8")
    if len(raw) <= 73:
        return line
    out = []
    chunk = raw[:73]
    # Never split a multi-byte sequence: back off to a character boundary.
    while len(chunk) > 1 and (raw[len(chunk)] & 0xC0) == 0x80:
        chunk = chunk[:-1]
    out.append(chunk.decode("utf-8"))
    rest = raw[len(chunk):]
    while rest:
        chunk = rest[:72]
        while len(chunk) > 1 and len(chunk) < len(rest) and (rest[len(chunk)] & 0xC0) == 0x80:
            chunk = chunk[:-1]
        out.append(" " + chunk.decode("utf-8"))
        rest = rest[len(chunk):]
    return "\r\n".join(out)


_UID_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def ics_uid(appointment_id: str, recipient_key: str = "", domain: str = "advisorflow.app") -> str:
    """A STABLE UID for one appointment/recipient pair.

    Stability is the whole contract. A reschedule or cancellation is recognised
    by the recipient's mail client only if it carries the SAME UID as the
    original invitation — a fresh random UID creates a second event next to the
    first instead of moving it, which is how a "cancelled" meeting stays on
    somebody's calendar.

    Derived from ids we already store, so it survives a restart, a redeploy and
    a lost row in a log table.
    """
    base = str(appointment_id or "unknown")
    if recipient_key:
        base = "%s-%s" % (base, _UID_SAFE.sub("", str(recipient_key))[:64])
    return "af-%s@%s" % (base, domain)


def build_ics(
    uid: str,
    starts_at: datetime,          # naive UTC
    ends_at: datetime,            # naive UTC
    summary: str,
    description: str = "",
    location: str = "",
    organizer_email: str = "",
    organizer_name: str = "",
    attendees=None,               # [(email, name)]
    method: str = METHOD_REQUEST,
    sequence: int = 0,
    dtstamp: datetime = None,
    url: str = "",
) -> str:
    """Return a complete VCALENDAR document as text.

    CRLF line endings throughout — RFC 5545 requires them, and Outlook in
    particular rejects LF-only files rather than tolerating them.
    """
    attendees = attendees or []
    stamp = _stamp(dtstamp or datetime.utcnow())
    cancelling = (method or "").upper() == METHOD_CANCEL

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:" + PRODID,
        "CALSCALE:GREGORIAN",
        "METHOD:" + (method or METHOD_REQUEST).upper(),
        "BEGIN:VEVENT",
        "UID:" + uid,
        "DTSTAMP:" + stamp,
        "DTSTART:" + _stamp(starts_at),
        "DTEND:" + _stamp(ends_at),
        "SEQUENCE:" + str(int(sequence or 0)),
        "SUMMARY:" + escape_text(summary),
    ]

    if description:
        lines.append("DESCRIPTION:" + escape_text(description))
    if location:
        lines.append("LOCATION:" + escape_text(location))
    if url:
        # URL is not escaped as TEXT — it is a URI value type, and escaping the
        # commas in a query string would corrupt the link.
        lines.append("URL:" + str(url))
    if organizer_email:
        lines.append('ORGANIZER;CN="%s":mailto:%s'
                     % (escape_text(organizer_name or organizer_email).replace('"', ""),
                        organizer_email))
    for em, nm in attendees:
        if not em:
            continue
        lines.append(
            'ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;'
            'RSVP=TRUE;CN="%s":mailto:%s'
            % (escape_text(nm or em).replace('"', ""), em))

    if cancelling:
        # Both are required for a cancellation to actually remove the event.
        # STATUS alone leaves it on the calendar greyed out in some clients.
        lines.append("STATUS:CANCELLED")
    else:
        lines.append("STATUS:CONFIRMED")
        lines.append("TRANSP:OPAQUE")

    lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(ln) for ln in lines) + "\r\n"
