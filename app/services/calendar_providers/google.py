"""
Google Calendar, via the Google Calendar API.

The SECOND production provider. Same architecture as Microsoft on purpose:
same base class, same SyncResult vocabulary, same "never raise into the
orchestrator" rule. The scheduling model is not forked for Google — the only
thing that differs is what happens inside these methods.

REUSES the existing OAuth plumbing: `calendar_service._get_calendar_service(user)`
already builds a credentialed calendar/v3 client from the encrypted refresh
token on `users.google_oauth_refresh_token_encrypted`. No second OAuth flow, no
second token store, no new secrets.

SCOPE NOTE — this is where Google differs from Microsoft
--------------------------------------------------------
Microsoft needed Calendars.ReadWrite added to its requested scopes, so anyone
connected under the old consent must reconnect. Google needs NO scope change:
the existing consent already requests `calendar.events`, which covers
create/update/delete AND reading events in a window.

That is why busy time is read with events().list and not freeBusy().query.
freeBusy requires `calendar.readonly` or `calendar.freebusy`, neither of which
the existing consent grants — using it would silently 403 for every already-
connected user and force a reconnect for no functional gain. events().list in
a time window returns the same information we actually keep (start and end).

IMPORTS ARE LAZY. The google client libraries are imported inside methods so
that a missing/broken google dependency degrades this one provider instead of
breaking the import of the provider registry, and with it the whole app.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from app.services.calendar_providers.base import (
    CalendarProvider, EventPayload, SyncResult, BusyInterval,
)

log = logging.getLogger(__name__)


def _rfc3339(dt: datetime) -> str:
    """We hold naive UTC everywhere. Google wants an explicit offset, so say Z."""
    return dt.replace(microsecond=0).isoformat() + "Z"


def _parse_google_dt(node: dict) -> Optional[datetime]:
    """Google returns either {'dateTime': '...±hh:mm'} or {'date': 'YYYY-MM-DD'}
    for all-day events. Returns naive UTC, which is what the rest of the system
    stores. Anything unparseable returns None and the caller drops the event —
    a busy block we cannot place in time is worse than no busy block, because
    it would silently remove real availability.
    """
    raw = node.get("dateTime") or node.get("date")
    if not raw:
        return None
    try:
        # fromisoformat only learned to accept a 'Z' suffix in 3.11; the
        # backend does not pin that, so normalise it here.
        txt = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(txt)
    except Exception:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class GoogleCalendarProvider(CalendarProvider):
    key = "google"

    # ── connection ──────────────────────────────────────────────────────────

    def is_ready(self) -> Tuple[bool, Optional[str]]:
        if not getattr(self.user, "google_oauth_refresh_token_encrypted", None):
            return False, "Google Calendar is not connected"
        return True, None

    def _calendar_id(self) -> str:
        # The connection row wins if it names a specific calendar; otherwise the
        # value stored at connect time; otherwise Google's own default.
        if self.connection is not None and getattr(self.connection, "calendar_id", None):
            return self.connection.calendar_id
        return getattr(self.user, "google_calendar_id", None) or "primary"

    def _service(self):
        """(service, error). Never raises."""
        try:
            from app.services.calendar_service import _get_calendar_service
            return _get_calendar_service(self.user), None
        except ValueError as e:
            # No stored refresh token at all.
            return None, SyncResult.failure("reauth", e)
        except ImportError as e:
            # google client libraries missing from the environment. Not the
            # user's fault and not fixable by reconnecting, so it is not 'reauth'.
            return None, SyncResult.failure("unavailable", e)
        except Exception as e:
            return None, SyncResult.failure("transport", e)

    # ── error mapping ───────────────────────────────────────────────────────

    def _classify(self, exc) -> SyncResult:
        """Turn any google-client exception into a result the UI can act on.

        The codes produced here are deliberately the SAME vocabulary Microsoft
        produces ('reauth', 'scope', 'http_404', 'rate_limit', 'transport'), so
        the orchestrator and the UI need no per-provider branching.
        """
        # A refresh that fails means the user revoked access or changed their
        # password. Retrying on their behalf can never fix it.
        try:
            from google.auth.exceptions import RefreshError
            if isinstance(exc, RefreshError):
                return SyncResult.failure("reauth", "Google sign-in expired")
        except Exception:
            pass

        status = None
        reason = ""
        try:
            from googleapiclient.errors import HttpError
            if isinstance(exc, HttpError):
                status = getattr(getattr(exc, "resp", None), "status", None)
                try:
                    reason = (exc.content or b"").decode("utf-8", "replace")[:400]
                except Exception:
                    reason = str(exc)[:400]
        except Exception:
            pass

        if status is None:
            return SyncResult.failure("transport", exc)
        if status == 401:
            return SyncResult.failure("reauth", "Google sign-in expired")
        if status == 403:
            low = reason.lower()
            # Google overloads 403: it is both "you lack permission" and
            # "you are going too fast". Those need opposite handling — one
            # requires the user, the other requires only patience.
            if "ratelimit" in low or "quota" in low or "userratelimit" in low:
                return SyncResult.failure("rate_limit", "Google rate limit — retry later")
            return SyncResult.failure(
                "scope", "Google denied calendar access — reconnect to grant "
                         "calendar permission")
        if status in (404, 410):
            # 410 Gone is what Google returns for an already-deleted event.
            return SyncResult.failure("http_404", "Event no longer exists in Google Calendar")
        if status == 429:
            return SyncResult.failure("rate_limit", "Google rate limit — retry later")
        return SyncResult.failure("http_%s" % status, reason or ("Google error %s" % status))

    # ── event shaping ───────────────────────────────────────────────────────

    def _event_body(self, p: EventPayload) -> dict:
        lines = [p.body_text or ""]
        if p.meeting_url:
            lines.append("")
            lines.append("Join: %s" % p.meeting_url)
        if p.advisorflow_appointment_id:
            lines.append("")
            lines.append("AdvisorFlow reference: %s" % p.advisorflow_appointment_id)

        body = {
            "summary": p.subject,
            "description": "\n".join(lines).strip(),
            # Naive-UTC instants sent with an explicit Z, plus the timezone the
            # meeting was actually agreed in so Google renders the right wall
            # clock (and moves it correctly across a DST boundary).
            "start": {"dateTime": _rfc3339(p.starts_at), "timeZone": p.timezone or "UTC"},
            "end":   {"dateTime": _rfc3339(p.ends_at),   "timeZone": p.timezone or "UTC"},
        }
        if p.location or p.meeting_url:
            body["location"] = p.location or (p.meeting_url or "Online")
        if p.attendees:
            body["attendees"] = [
                {"email": em, "displayName": nm or em}
                for em, nm in p.attendees if em
            ]
        if p.advisorflow_appointment_id:
            # Structured, machine-readable reconciliation key. Survives a user
            # editing the description, which the body line does not.
            body["extendedProperties"] = {
                "private": {"advisorflow_appointment_id": p.advisorflow_appointment_id}
            }
        return body

    # ── operations ──────────────────────────────────────────────────────────
    #
    # sendUpdates="none" on every write, deliberately.
    #
    # AdvisorFlow owns all attendee communication: prospects get the branded
    # invitation with the secure confirmation link, and each internal
    # participant gets an event on their OWN calendar through their OWN
    # connection. Letting Google also email the attendee list would send a
    # second, competing invitation with a different accept/decline mechanism.

    def create_event(self, payload: EventPayload) -> SyncResult:
        service, err = self._service()
        if err:
            return err
        try:
            created = service.events().insert(
                calendarId=self._calendar_id(),
                body=self._event_body(payload),
                sendUpdates="none",
            ).execute()
        except Exception as e:
            return self._classify(e)
        return SyncResult(ok=True, external_event_id=created.get("id"))

    def update_event(self, external_event_id: str, payload: EventPayload) -> SyncResult:
        service, err = self._service()
        if err:
            return err
        try:
            service.events().update(
                calendarId=self._calendar_id(),
                eventId=external_event_id,
                body=self._event_body(payload),
                sendUpdates="none",
            ).execute()
        except Exception as e:
            result = self._classify(e)
            if result.error_code == "http_404":
                # Deleted in Google. The AdvisorFlow appointment is the truth,
                # so recreate rather than fail — same behaviour as Microsoft.
                created = self.create_event(payload)
                if created.ok:
                    return created
            return result
        return SyncResult(ok=True, external_event_id=external_event_id)

    def cancel_event(self, external_event_id: str, payload: EventPayload = None) -> SyncResult:
        # payload is accepted for interface parity and ignored: Google cancels
        # by id. Only the .ics fallback needs the event's details to cancel.
        service, err = self._service()
        if err:
            return err
        try:
            service.events().delete(
                calendarId=self._calendar_id(),
                eventId=external_event_id,
                sendUpdates="none",
            ).execute()
        except Exception as e:
            result = self._classify(e)
            # Already gone is the state we were trying to reach.
            if result.error_code == "http_404":
                return SyncResult(ok=True, external_event_id=external_event_id)
            return result
        return SyncResult(ok=True, external_event_id=external_event_id)

    # ── busy reads ──────────────────────────────────────────────────────────

    def get_busy(self, start_utc: datetime, end_utc: datetime):
        """Busy periods in the window, via events().list.

        See the module docstring for why this is not freeBusy().query.

        Only start and end survive this method. Summary, description, attendees
        and conferencing links are all present in the response and are all
        discarded here — a colleague needs to know you are busy, not what you
        are doing, and what we never store cannot leak.
        """
        service, err = self._service()
        if err:
            return [], err
        try:
            resp = service.events().list(
                calendarId=self._calendar_id(),
                timeMin=_rfc3339(start_utc),
                timeMax=_rfc3339(end_utc),
                singleEvents=True,      # expand recurrence into real instances
                orderBy="startTime",
                maxResults=250,
                # Ask for only the fields we keep. Smaller payload, and it makes
                # the privacy claim above structural rather than a promise.
                fields="items(id,start,end,status,transparency,eventType)",
            ).execute()
        except Exception as e:
            return [], self._classify(e)

        out: List[BusyInterval] = []
        for ev in (resp.get("items") or []):
            if (ev.get("status") or "").lower() == "cancelled":
                continue
            # 'transparent' is Google's "free" — shown on the calendar but does
            # not block. Same meaning as Graph's showAs=free.
            if (ev.get("transparency") or "opaque").lower() == "transparent":
                continue
            start_node = ev.get("start") or {}
            end_node = ev.get("end") or {}
            s = _parse_google_dt(start_node)
            e = _parse_google_dt(end_node)
            if s is None or e is None:
                continue
            is_all_day = bool(start_node.get("date") and not start_node.get("dateTime"))
            if is_all_day:
                # Google's all-day end date is EXCLUSIVE, and both ends are bare
                # dates with no time. Treating the span as-is is correct for
                # blocking purposes; the half-open end needs no adjustment.
                if e <= s:
                    e = s + timedelta(days=1)
            if e <= s:
                continue
            out.append(BusyInterval(starts_at=s, ends_at=e,
                                    provider_event_id=ev.get("id"),
                                    is_all_day=is_all_day))
        return out, None
