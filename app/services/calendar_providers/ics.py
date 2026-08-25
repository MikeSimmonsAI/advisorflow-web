"""
The .ics email fallback.

REQUIRED, not optional. An internal participant who has connected neither
Microsoft nor Google still belongs in the meeting: they are still a real
participant on the AdvisorFlow appointment, they still hold the slot, they
still block double-booking. What they do not get is a calendar we can write to
— so they get a standards-compliant invitation by email instead, and their own
mail client puts it on their calendar.

ONE PARTICIPANT'S MISSING CONNECTION NEVER BLOCKS THE MEETING. That rule is
what this class exists to enforce; it is the branch that turns "cannot sync"
into "sent by email" rather than into a failed booking.

The UI shows this state honestly as CALENDAR NOT CONNECTED / INVITE SENT BY
EMAIL. It is not dressed up as a calendar connection, because it is not one:
nothing here can read the person's availability, and pretending otherwise would
be the exact "fake sync" this project forbids.

WHAT THIS PROVIDER CANNOT DO
----------------------------
get_busy() returns no intervals and NO error. That distinction matters. An
empty list with an error means "we could not read your calendar" and should
raise an alert; an empty list with no error means "there is no external
calendar to read", which is simply the truth for this user and must not put
their appointment into a failed state.
"""
import base64
import logging
from datetime import datetime
from typing import List, Optional, Tuple

from app.services.calendar_providers.base import (
    CalendarProvider, EventPayload, SyncResult, BusyInterval,
)
from app.services.ics_builder import (
    build_ics, ics_uid, METHOD_REQUEST, METHOD_CANCEL,
)

log = logging.getLogger(__name__)

ICS_CONTENT_TYPE = "text/calendar; charset=UTF-8; method=REQUEST"
ICS_CANCEL_CONTENT_TYPE = "text/calendar; charset=UTF-8; method=CANCEL"


def _fmt_local(dt: datetime, tzname: str) -> str:
    """Render a naive-UTC instant as a readable wall clock in the meeting's own
    timezone. The email body is read by a human, so it must not show UTC.

    Zero-padding is stripped by hand rather than with strftime's %-d / %-I:
    those are glibc extensions that raise on Windows, and this code is edited
    and tested on Windows before it is deployed to Linux.
    """
    try:
        from datetime import timezone as _tz
        from zoneinfo import ZoneInfo
        aware = dt.replace(tzinfo=_tz.utc).astimezone(ZoneInfo(tzname or "UTC"))
    except Exception:
        return dt.strftime("%Y-%m-%d %H:%M") + " UTC"
    day = str(int(aware.strftime("%d")))
    hour = str(int(aware.strftime("%I")))
    zone = aware.strftime("%Z") or (tzname or "UTC")
    return "%s, %s %s, %s at %s:%s %s %s" % (
        aware.strftime("%A"), aware.strftime("%B"), day, aware.strftime("%Y"),
        hour, aware.strftime("%M"), aware.strftime("%p"), zone,
    )


class IcsEmailProvider(CalendarProvider):
    """Not a calendar connection. An email sender that speaks iCalendar."""

    key = "ics"

    def is_ready(self) -> Tuple[bool, Optional[str]]:
        """Ready whenever we have somewhere to send. This provider is the
        fallback OF LAST RESORT, so it is deliberately hard to make unready —
        the only true blocker is not knowing the person's email address."""
        if not getattr(self.user, "email", None):
            return False, "No email address on file for this user"
        return True, None

    def _recipient(self, payload: EventPayload) -> Tuple[Optional[str], Optional[str]]:
        email = payload.recipient_email or getattr(self.user, "email", None)
        name = (payload.recipient_name
                or getattr(self.user, "full_name", None)
                or email)
        return email, name

    # ── message assembly ────────────────────────────────────────────────────

    def _html(self, p: EventPayload, name: str, cancelled: bool) -> str:
        when = _fmt_local(p.starts_at, p.timezone)
        if cancelled:
            headline = "This meeting has been cancelled"
            lead = "The meeting below has been cancelled. No action is needed."
        else:
            headline = p.subject or "Meeting invitation"
            lead = ("You are invited to the meeting below. Open the attached "
                    "invitation to add it to your calendar.")
        rows = ["<p><strong>When:</strong> %s</p>" % when]
        if p.location:
            rows.append("<p><strong>Where:</strong> %s</p>" % p.location)
        if p.meeting_url and not cancelled:
            rows.append('<p><strong>Join:</strong> <a href="%s">%s</a></p>'
                        % (p.meeting_url, p.meeting_url))
        if p.body_text and not cancelled:
            rows.append("<p>%s</p>" % p.body_text.replace("\n", "<br>"))
        return (
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
            'color:#1f2937;line-height:1.5">'
            "<p>Hi %s,</p><p>%s</p><h3 style=\"margin:18px 0 6px\">%s</h3>%s"
            '<p style="color:#6b7280;font-size:13px;margin-top:22px">'
            "Sent by AdvisorFlow scheduling.</p></div>"
        ) % (name or "there", lead, headline, "".join(rows))

    def _send(self, p: EventPayload, uid: str, method: str) -> SyncResult:
        """Build the .ics, attach it, send it. The only place this class does
        anything with the outside world."""
        to_email, to_name = self._recipient(p)
        if not to_email:
            return SyncResult.failure("no_email", "No email address for this participant")

        cancelling = method == METHOD_CANCEL
        try:
            text = build_ics(
                uid=uid,
                starts_at=p.starts_at,
                ends_at=p.ends_at,
                summary=p.subject,
                description=p.body_text or "",
                location=p.location or (p.meeting_url or ""),
                organizer_email=p.organizer_email or "",
                organizer_name=p.organizer_name or "",
                attendees=[(to_email, to_name)],
                method=method,
                sequence=p.sequence or 0,
                url=p.meeting_url or "",
            )
        except Exception as e:
            return SyncResult.failure("ics_build", e)

        subject = ("Cancelled: " if cancelling else "Invitation: ") + (p.subject or "Meeting")
        attachment = {
            "filename": "invite.ics",
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "content_type": ICS_CANCEL_CONTENT_TYPE if cancelling else ICS_CONTENT_TYPE,
        }
        try:
            from app.services.email_service import send_email_via_provider
            result = send_email_via_provider(
                to_email=to_email,
                subject=subject,
                body_html=self._html(p, to_name, cancelling),
                attachments=[attachment],
                org=self.org,
            )
        except Exception as e:
            return SyncResult.failure("transport", e)

        if not result.get("success"):
            err = result.get("error") or "Email provider rejected the message"
            # Not reauth: nothing the participant can reconnect. This is our
            # sending infrastructure, and it is the operator's problem.
            return SyncResult.failure("email_failed", err)

        # The UID is this provider's event id. It is what a later reschedule or
        # cancellation must reuse, so it is what gets persisted.
        return SyncResult(ok=True, external_event_id=uid)

    # ── operations ──────────────────────────────────────────────────────────

    def create_event(self, payload: EventPayload) -> SyncResult:
        to_email, _ = self._recipient(payload)
        uid = ics_uid(payload.advisorflow_appointment_id, to_email or "")
        return self._send(payload, uid, METHOD_REQUEST)

    def update_event(self, external_event_id: str, payload: EventPayload) -> SyncResult:
        # Reuses the stored UID so the recipient's client MOVES the existing
        # event instead of adding a second one. Falls back to deriving the UID
        # only if we somehow never stored it.
        to_email, _ = self._recipient(payload)
        uid = external_event_id or ics_uid(payload.advisorflow_appointment_id, to_email or "")
        if not (payload.sequence or 0):
            # A REQUEST with an unchanged SEQUENCE is treated as a duplicate and
            # silently dropped by Outlook. Refusing to send is worse than
            # bumping it, so guarantee a minimum of 1 on any update.
            payload.sequence = 1
        return self._send(payload, uid, METHOD_REQUEST)

    def cancel_event(self, external_event_id: str, payload: EventPayload = None) -> SyncResult:
        if payload is None:
            # Documented on the base class: a CANCEL needs the event's details.
            # Reporting that honestly beats emitting an invalid .ics that the
            # recipient's client ignores while we record a success.
            return SyncResult.failure(
                "needs_payload", "Cancellation by email requires the meeting details")
        to_email, _ = self._recipient(payload)
        uid = external_event_id or ics_uid(payload.advisorflow_appointment_id, to_email or "")
        payload.sequence = max(int(payload.sequence or 0) + 1, 1)
        return self._send(payload, uid, METHOD_CANCEL)

    def get_busy(self, start_utc: datetime, end_utc: datetime):
        """No external calendar exists to read. Empty AND no error — see the
        module docstring: this is a known-absent calendar, not a failed read."""
        return [], None
