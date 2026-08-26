"""
Zoom, via Server-to-Server OAuth.

WHY SERVER-TO-SERVER AND NOT USER-LEVEL OAUTH
---------------------------------------------
Sales meetings are hosted by the BRAND, not by whichever rep happened to book
them. Three consequences decided this:

  · A rep leaving must not take their meetings with them. Under user OAuth,
    revoking their Zoom account orphans every future meeting they booked.
  · Nobody should have to complete a consent screen before the team can sell.
  · A prospect meeting BookaBoost must not receive a Zoom room branded EvoSys
    Pro. Credentials are therefore per brand (MeetingProviderConfig), not per
    person.

ZOOM JWT IS DEPRECATED AND IS NOT USED HERE. JWT app credentials were retired
by Zoom; this module uses the account_credentials grant, which is the current
supported server-side path.

CREDENTIALS NEVER LEAVE THE SERVER. They are read from an encrypted config row
or from environment variables, used to mint a short-lived access token, and are
never returned by any endpoint or written into any log line.
"""
import base64
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Tuple

import httpx

from app.services.meeting_providers.base import (
    MeetingProvider, MeetingRequest, MeetingResult,
)

log = logging.getLogger(__name__)

ZOOM_TOKEN_URL = "https://zoom.us/oauth/token"
ZOOM_API = "https://api.zoom.us/v2"
TIMEOUT = 20

# Env-var fallback — the single-brand path that works with no setup. A
# MeetingProviderConfig row for the brand always wins over these.
ZOOM_ACCOUNT_ID = os.environ.get("ZOOM_ACCOUNT_ID")
ZOOM_CLIENT_ID = os.environ.get("ZOOM_CLIENT_ID")
ZOOM_CLIENT_SECRET = os.environ.get("ZOOM_CLIENT_SECRET")
# Which Zoom user hosts. "me" is the account owner.
ZOOM_HOST_ID = os.environ.get("ZOOM_HOST_ID", "me")

# Access tokens last an hour. Minting one per meeting would trade a round-trip
# for nothing, so they are cached per credential set. Keyed by account id, and
# the cache holds TOKENS ONLY — never the client secret.
_TOKEN_CACHE = {}
_TOKEN_LOCK = threading.Lock()
# Refresh a minute early so a token cannot expire between the check and the call.
_TOKEN_SKEW_SECONDS = 60


def _zoom_time(dt: datetime) -> str:
    """Naive UTC -> the unambiguous GMT form Zoom accepts."""
    return dt.replace(microsecond=0).isoformat() + "Z"


class ZoomProvider(MeetingProvider):
    key = "zoom"

    # ── credentials ─────────────────────────────────────────────────────────

    def _credentials(self) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
        """(account_id, client_id, client_secret, host). Config row beats env.

        Decrypted only here, held only as locals, never stored on self — an
        object that carries a client secret as an attribute is an object that
        eventually gets logged or serialized by accident.
        """
        cfg = self.config
        if cfg is not None and getattr(cfg, "is_active", True):
            try:
                from app.utils.crypto import decrypt_value
                acc = decrypt_value(cfg.account_id_encrypted) if cfg.account_id_encrypted else None
                cid = decrypt_value(cfg.client_id_encrypted) if cfg.client_id_encrypted else None
                sec = decrypt_value(cfg.client_secret_encrypted) if cfg.client_secret_encrypted else None
                if acc and cid and sec:
                    return acc, cid, sec, (cfg.host_identifier or "me")
            except Exception:
                # A config row we cannot decrypt is a real problem, but it must
                # not take the env-var path down with it.
                log.exception("could not decrypt Zoom credentials for brand config")
        return ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, ZOOM_HOST_ID

    def is_ready(self) -> Tuple[bool, Optional[str]]:
        acc, cid, sec, _host = self._credentials()
        if not (acc and cid and sec):
            return False, ("Zoom is not configured. Add the account id, client id "
                           "and client secret from a Zoom Server-to-Server OAuth app.")
        return True, None

    def _access_token(self) -> Tuple[Optional[str], Optional[MeetingResult]]:
        """A cached, valid access token, or the result explaining why not."""
        acc, cid, sec, _host = self._credentials()
        if not (acc and cid and sec):
            return None, MeetingResult.failure("not_configured", "Zoom is not configured")

        now = time.time()
        with _TOKEN_LOCK:
            cached = _TOKEN_CACHE.get(acc)
            if cached and cached[1] - _TOKEN_SKEW_SECONDS > now:
                return cached[0], None

        basic = base64.b64encode(("%s:%s" % (cid, sec)).encode("utf-8")).decode("ascii")
        try:
            r = httpx.post(
                ZOOM_TOKEN_URL,
                params={"grant_type": "account_credentials", "account_id": acc},
                headers={"Authorization": "Basic " + basic},
                timeout=TIMEOUT,
            )
        except Exception as e:
            return None, MeetingResult.failure("transport", e)

        if r.status_code != 200:
            # 400/401 here means the credentials are wrong or the app was
            # deactivated. Only a human with Zoom access can fix that, so it is
            # 'auth' and never retried on the user's behalf.
            return None, MeetingResult.failure(
                "auth", "Zoom rejected the credentials — check the Server-to-Server "
                        "OAuth app's account id, client id and secret.")
        try:
            body = r.json()
            token = body["access_token"]
            ttl = int(body.get("expires_in") or 3600)
        except Exception as e:
            return None, MeetingResult.failure("transport", "Unreadable Zoom token response: %s" % e)

        with _TOKEN_LOCK:
            _TOKEN_CACHE[acc] = (token, now + ttl)
        return token, None


    # ── error mapping ───────────────────────────────────────────────────────

    def _classify(self, resp) -> MeetingResult:
        code = resp.status_code
        body = ""
        try:
            body = resp.text[:400]
        except Exception:
            pass
        if code == 401:
            _TOKEN_CACHE.clear()   # a stale cached token must not be reused
            return MeetingResult.failure("auth", "Zoom rejected the access token")
        if code == 403:
            # Almost always a missing scope on the Server-to-Server app.
            return MeetingResult.failure(
                "scope", "Zoom denied the request — the Server-to-Server OAuth app "
                         "needs the meeting:write scope.")
        if code == 404:
            return MeetingResult.failure("http_404", "That Zoom meeting no longer exists")
        if code == 429:
            return MeetingResult.failure("rate_limit", "Zoom rate limit — retry shortly")
        return MeetingResult.failure("http_%s" % code, body or ("Zoom error %s" % code))

    # ── request shaping ─────────────────────────────────────────────────────

    def _body(self, req: MeetingRequest) -> dict:
        agenda = req.agenda or ""
        if req.advisorflow_appointment_id:
            # Correlation marker so an orphaned Zoom meeting can be traced back.
            agenda = (agenda + "\n\nAdvisorFlow reference: %s"
                      % req.advisorflow_appointment_id).strip()
        return {
            "topic": (req.topic or "Meeting")[:200],
            "type": 2,                       # a scheduled meeting, not recurring
            "start_time": _zoom_time(req.starts_at),
            "duration": max(int(req.duration_minutes or 30), 1),
            # start_time carries an explicit Z, so it is unambiguous regardless
            # of this field; timezone is what Zoom renders the host's copy in.
            "timezone": req.timezone or "UTC",
            "agenda": agenda[:2000],
            "settings": {
                # The prospect must never sit in an empty room because the rep
                # is thirty seconds late.
                "join_before_host": True,
                "waiting_room": False,
                "approval_type": 2,          # no registration
                "audio": "both",
                "mute_upon_entry": False,
            },
        }

    def _result_from(self, data: dict) -> MeetingResult:
        return MeetingResult(
            ok=True,
            provider_meeting_id=str(data.get("id") or "") or None,
            join_url=data.get("join_url"),
            # HOST ONLY — the caller encrypts this and never serializes it.
            host_url=data.get("start_url"),
            passcode=data.get("password"),
        )


    # ── operations ──────────────────────────────────────────────────────────

    def _headers(self, token: str) -> dict:
        return {"Authorization": "Bearer " + token, "Content-Type": "application/json"}

    def create_meeting(self, req: MeetingRequest) -> MeetingResult:
        token, err = self._access_token()
        if err:
            return err
        _acc, _cid, _sec, default_host = self._credentials()
        host = req.host_identifier or default_host or "me"
        try:
            r = httpx.post("%s/users/%s/meetings" % (ZOOM_API, host),
                           headers=self._headers(token), json=self._body(req),
                           timeout=TIMEOUT)
        except Exception as e:
            return MeetingResult.failure("transport", e)
        if r.status_code in (200, 201):
            try:
                return self._result_from(r.json())
            except Exception as e:
                return MeetingResult.failure("transport", "Unreadable Zoom response: %s" % e)
        return self._classify(r)

    def update_meeting(self, provider_meeting_id: str, req: MeetingRequest) -> MeetingResult:
        token, err = self._access_token()
        if err:
            return err
        try:
            r = httpx.patch("%s/meetings/%s" % (ZOOM_API, provider_meeting_id),
                            headers=self._headers(token), json=self._body(req),
                            timeout=TIMEOUT)
        except Exception as e:
            return MeetingResult.failure("transport", e)

        if r.status_code in (200, 204):
            # PATCH returns no body. The join URL does not change when a meeting
            # moves, so the stored one stays valid — which is exactly why a
            # reschedule updates rather than recreating.
            return MeetingResult(ok=True, provider_meeting_id=provider_meeting_id)
        if r.status_code == 404:
            # Somebody deleted it in Zoom. The AdvisorFlow appointment is the
            # truth, so recreate rather than leave the meeting without a room.
            created = self.create_meeting(req)
            if created.ok:
                return created
        return self._classify(r)

    def cancel_meeting(self, provider_meeting_id: str) -> MeetingResult:
        token, err = self._access_token()
        if err:
            return err
        try:
            r = httpx.delete("%s/meetings/%s" % (ZOOM_API, provider_meeting_id),
                             headers=self._headers(token), timeout=TIMEOUT)
        except Exception as e:
            return MeetingResult.failure("transport", e)
        # 404 means it is already gone, which is the state we wanted.
        if r.status_code in (200, 204, 404):
            return MeetingResult(ok=True, provider_meeting_id=provider_meeting_id)
        return self._classify(r)

    def verify(self) -> MeetingResult:
        """Mint a token and read the host user. Proves the credentials AND the
        scope, which a token fetch alone does not."""
        token, err = self._access_token()
        if err:
            return err
        _acc, _cid, _sec, host = self._credentials()
        try:
            r = httpx.get("%s/users/%s" % (ZOOM_API, host or "me"),
                          headers=self._headers(token), timeout=TIMEOUT)
        except Exception as e:
            return MeetingResult.failure("transport", e)
        if r.status_code == 200:
            try:
                data = r.json()
                who = data.get("email") or data.get("id") or "the configured host"
            except Exception:
                who = "the configured host"
            return MeetingResult(ok=True, error_message="Connected as %s" % who)
        return self._classify(r)
