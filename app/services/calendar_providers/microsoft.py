"""
Microsoft 365 / Outlook calendar, via Microsoft Graph.

The FIRST production provider, per the checkpoint order.

REUSES the existing OAuth plumbing rather than building a second one:
`microsoft_email_service._get_fresh_access_token(user)` already exchanges the
encrypted refresh token on `users.microsoft_oauth_refresh_token_encrypted` for
an access token. This module adds no new OAuth flow, no new token storage and
no new secrets.

SCOPE WARNING
-------------
That service originally requested `offline_access Mail.Send User.Read` only —
no calendar permission. A user connected under the old consent has a live token
that email works with and calendar does not. Graph answers that with 403
ErrorAccessDenied, which this module maps to error_code 'scope' so the UI can
say "reconnect to grant calendar access" instead of the useless "sync failed".
Calendars.ReadWrite has been added to the requested scopes; anyone connected
before that must reconnect.
"""
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import httpx

from app.services.calendar_providers.base import (
    CalendarProvider, EventPayload, SyncResult, BusyInterval,
)

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
TIMEOUT = 20


def _iso(dt: datetime) -> str:
    """Graph wants a naive local-to-the-given-timezone wall clock alongside an
    explicit timeZone. We hold naive UTC, so we send the UTC instant and say
    UTC — Graph then renders it in each attendee's own zone correctly."""
    return dt.replace(microsecond=0).isoformat()


class MicrosoftCalendarProvider(CalendarProvider):
    key = "microsoft"

    def _token(self) -> Tuple[Optional[str], Optional[SyncResult]]:
        """Access token, or the SyncResult explaining why not."""
        try:
            from app.services.microsoft_email_service import _get_fresh_access_token
            return _get_fresh_access_token(self.user), None
        except ValueError as e:
            # Raised when there is no stored refresh token at all.
            return None, SyncResult.failure("reauth", e)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else 0
            if code in (400, 401):
                # invalid_grant: the user revoked consent or changed password.
                # Only they can fix it, so never auto-retry this.
                return None, SyncResult.failure("reauth", "Microsoft sign-in expired")
            return None, SyncResult.failure("http_%s" % code, e)
        except Exception as e:
            return None, SyncResult.failure("transport", e)

    def is_ready(self) -> Tuple[bool, Optional[str]]:
        if not getattr(self.user, "microsoft_oauth_refresh_token_encrypted", None):
            return False, "Microsoft 365 is not connected"
        return True, None

    def _headers(self, token: str) -> dict:
        return {"Authorization": "Bearer " + token, "Content-Type": "application/json"}

    def _classify(self, resp) -> SyncResult:
        """Turn a non-2xx Graph response into a result the UI can act on."""
        code = resp.status_code
        body = ""
        try:
            body = resp.text[:400]
        except Exception:
            pass
        if code in (401,):
            return SyncResult.failure("reauth", "Microsoft sign-in expired")
        if code == 403:
            # Almost always a missing Calendars.ReadWrite grant.
            return SyncResult.failure(
                "scope", "Microsoft denied calendar access — reconnect to grant "
                         "calendar permission")
        if code == 404:
            return SyncResult.failure("http_404", "Event no longer exists in Outlook")
        if code == 429:
            return SyncResult.failure("rate_limit", "Microsoft rate limit — retry later")
        return SyncResult.failure("http_%s" % code, body or "Graph error %s" % code)

    def _event_body(self, p: EventPayload) -> dict:
        lines = [p.body_text or ""]
        if p.meeting_url:
            lines.append("")
            lines.append("Join: %s" % p.meeting_url)
        if p.advisorflow_appointment_id:
            # Lets a later reconciliation prove which appointment an event is,
            # even if our stored id were lost.
            lines.append("")
            lines.append("AdvisorFlow reference: %s" % p.advisorflow_appointment_id)
        body = {
            "subject": p.subject,
            "body": {"contentType": "text", "content": "\n".join(lines).strip()},
            "start": {"dateTime": _iso(p.starts_at), "timeZone": "UTC"},
            "end":   {"dateTime": _iso(p.ends_at),   "timeZone": "UTC"},
        }
        if p.location or p.meeting_url:
            body["location"] = {"displayName": p.location or "Online"}
        if p.attendees:
            body["attendees"] = [
                {"emailAddress": {"address": em, "name": nm or em}, "type": "required"}
                for em, nm in p.attendees if em
            ]
        return body

    def create_event(self, payload: EventPayload) -> SyncResult:
        token, err = self._token()
        if err:
            return err
        try:
            r = httpx.post(GRAPH + "/me/events", headers=self._headers(token),
                           json=self._event_body(payload), timeout=TIMEOUT)
        except Exception as e:
            return SyncResult.failure("transport", e)
        if r.status_code in (200, 201):
            return SyncResult(ok=True, external_event_id=r.json().get("id"))
        return self._classify(r)

    def update_event(self, external_event_id: str, payload: EventPayload) -> SyncResult:
        token, err = self._token()
        if err:
            return err
        try:
            r = httpx.patch("%s/me/events/%s" % (GRAPH, external_event_id),
                            headers=self._headers(token),
                            json=self._event_body(payload), timeout=TIMEOUT)
        except Exception as e:
            return SyncResult.failure("transport", e)
        if r.status_code in (200, 201):
            return SyncResult(ok=True, external_event_id=external_event_id)
        if r.status_code == 404:
            # Somebody deleted it in Outlook. Recreate rather than fail — the
            # AdvisorFlow appointment is the truth and the calendar should match.
            created = self.create_event(payload)
            if created.ok:
                return created
        return self._classify(r)

    def cancel_event(self, external_event_id: str, payload: EventPayload = None) -> SyncResult:
        # payload is accepted for interface parity and ignored: Graph cancels
        # by id. Only the .ics fallback needs the event's details to cancel.
        token, err = self._token()
        if err:
            return err
        try:
            r = httpx.delete("%s/me/events/%s" % (GRAPH, external_event_id),
                             headers=self._headers(token), timeout=TIMEOUT)
        except Exception as e:
            return SyncResult.failure("transport", e)
        # 404 means it is already gone, which is the state we wanted.
        if r.status_code in (200, 202, 204, 404):
            return SyncResult(ok=True, external_event_id=external_event_id)
        return self._classify(r)

    def get_busy(self, start_utc: datetime, end_utc: datetime):
        """Read busy periods via calendarView.

        Only start/end are kept. Subject, attendees and body are discarded at
        this boundary so private meeting details never enter our database.
        """
        token, err = self._token()
        if err:
            return [], err
        params = {
            "startDateTime": _iso(start_utc),
            "endDateTime": _iso(end_utc),
            "$select": "id,start,end,isAllDay,showAs",
            "$top": "200",
        }
        try:
            r = httpx.get(GRAPH + "/me/calendarView", headers=self._headers(token),
                          params=params, timeout=TIMEOUT)
        except Exception as e:
            return [], SyncResult.failure("transport", e)
        if r.status_code != 200:
            return [], self._classify(r)

        out: List[BusyInterval] = []
        for ev in (r.json().get("value") or []):
            # 'free' and 'workingElsewhere' do not block a meeting.
            if (ev.get("showAs") or "busy").lower() in ("free", "workingelsewhere"):
                continue
            try:
                s = datetime.fromisoformat((ev["start"]["dateTime"])[:19])
                e = datetime.fromisoformat((ev["end"]["dateTime"])[:19])
            except Exception:
                continue
            if e <= s:
                continue
            out.append(BusyInterval(starts_at=s, ends_at=e,
                                    provider_event_id=ev.get("id"),
                                    is_all_day=bool(ev.get("isAllDay"))))
        return out, None
