"""
External calendar sync + invitations regression suite — Checkpoint 3.

Layers, same discipline as smoke_scheduling.py:

  · FORMAT tests call ics_builder directly. RFC 5545 is a specification with
    exact answers, so the assertions are exact.
  · REGISTRY tests prove provider resolution and the .ics fallback, using a
    fake provider injected through register_provider — production code carries
    no test branch.
  · SYNC tests drive the orchestrator against the fake provider so every
    outcome (success, transient failure, reauth, partial success) is reachable
    without a network or a real Microsoft/Google account.
  · API tests drive real in-process HTTP.

NO TEST EVER CONTACTS MICROSOFT OR GOOGLE. A suite that needs live vendor
credentials is a suite that gets skipped, and a skipped test protects nothing.

Temp SQLite. Never touches production.

    python scripts/smoke_calendar_sync.py
"""
import os
import sys
import base64
import shutil
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="calsync_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "smoke" + "0" * 59
os.environ["SECRET_KEY"] = "smoke" + "0" * 59

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILURES = []


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("\n         -> " + str(detail)[:400]) if not ok else ""))
    if not ok:
        FAILURES.append(label)


def U(y, mo, d, h, mi=0):
    """Naive UTC, the only instant format this system stores."""
    return datetime(y, mo, d, h, mi)


def unfold(text):
    """Reverse RFC 5545 line folding before asserting on content.

    Without this a test asserting on a long ATTENDEE line fails against a
    perfectly valid file, because the property was split across a continuation
    — which is exactly the bug this helper was written in response to.
    """
    return text.replace("\r\n ", "")


# ═══════════════════════════════════════════════════════════════════════════
# 1. ICS FORMAT
# ═══════════════════════════════════════════════════════════════════════════

def test_ics_format():
    print("\n[1] iCalendar generation (RFC 5545)")
    from app.services.ics_builder import (
        build_ics, ics_uid, escape_text, fold, METHOD_REQUEST, METHOD_CANCEL,
    )

    text = build_ics(
        uid="af-appt1@advisorflow.app",
        starts_at=U(2026, 9, 15, 14, 0),
        ends_at=U(2026, 9, 15, 14, 30),
        summary="Discovery call",
        description="Intro conversation",
        location="Phone",
        organizer_email="support@evosyspro.live",
        organizer_name="EvoSys Pro",
        attendees=[("blake@example.com", "Blake Kolb")],
        method=METHOD_REQUEST,
        sequence=0,
    )

    check("VCALENDAR is well-formed",
          text.startswith("BEGIN:VCALENDAR") and text.rstrip().endswith("END:VCALENDAR"),
          text[:120])
    check("CRLF line endings (Outlook rejects LF-only)",
          "\r\n" in text and "\n" not in text.replace("\r\n", ""))
    check("DTSTART is a UTC instant", "DTSTART:20260915T140000Z" in text, text)
    check("DTEND is a UTC instant", "DTEND:20260915T143000Z" in text, text)
    check("no VTIMEZONE block (UTC needs none)", "BEGIN:VTIMEZONE" not in text)
    check("METHOD:REQUEST present", "METHOD:REQUEST" in text)
    check("STATUS:CONFIRMED on an invitation", "STATUS:CONFIRMED" in text)
    check("SEQUENCE present", "SEQUENCE:0" in text)
    # Long properties are folded across continuation lines, so content
    # assertions run against the UNFOLDED document.
    flat = unfold(text)
    check("ORGANIZER carries mailto",
          "ORGANIZER" in flat and "mailto:support@evosyspro.live" in flat, flat[:400])
    check("ATTENDEE carries mailto", "mailto:blake@example.com" in flat, flat[:400])
    check("ATTENDEE asks for a reply",
          "RSVP=TRUE" in flat and "PARTSTAT=NEEDS-ACTION" in flat, flat[:400])

    cancel = build_ics(
        uid="af-appt1@advisorflow.app",
        starts_at=U(2026, 9, 15, 14, 0), ends_at=U(2026, 9, 15, 14, 30),
        summary="Discovery call", method=METHOD_CANCEL, sequence=2,
        organizer_email="support@evosyspro.live",
        attendees=[("blake@example.com", "Blake Kolb")],
    )
    check("cancellation uses METHOD:CANCEL", "METHOD:CANCEL" in cancel)
    check("cancellation uses STATUS:CANCELLED", "STATUS:CANCELLED" in cancel)
    check("cancellation is not marked CONFIRMED", "STATUS:CONFIRMED" not in cancel)
    check("cancellation reuses the SAME UID",
          "UID:af-appt1@advisorflow.app" in cancel,
          "a fresh UID leaves the meeting on the recipient's calendar")
    check("cancellation raises SEQUENCE", "SEQUENCE:2" in cancel)


def test_ics_escaping_and_folding():
    print("\n[2] iCalendar escaping and line folding")
    from app.services.ics_builder import build_ics, escape_text, fold, ics_uid

    check("comma escaped", escape_text("Smith, John") == "Smith\\, John", escape_text("Smith, John"))
    check("semicolon escaped", escape_text("a;b") == "a\\;b")
    check("newline escaped to literal \\n", escape_text("a\nb") == "a\\nb")
    check("CRLF collapses to one \\n", escape_text("a\r\nb") == "a\\nb", escape_text("a\r\nb"))
    # Backslash must be escaped FIRST or the escapes added afterwards get
    # re-escaped and the file is corrupt.
    check("backslash escaped once, not twice",
          escape_text("a\\b") == "a\\\\b", escape_text("a\\b"))
    check("None becomes empty, not the word None", escape_text(None) == "")

    long_line = "DESCRIPTION:" + ("x" * 300)
    folded = fold(long_line)
    parts = folded.split("\r\n")
    check("long line is folded", len(parts) > 1, len(parts))
    check("every folded line is <= 75 octets",
          all(len(p.encode("utf-8")) <= 75 for p in parts),
          [len(p.encode("utf-8")) for p in parts])
    check("continuations begin with one space",
          all(p.startswith(" ") for p in parts[1:]))
    check("unfolding restores the original",
          parts[0] + "".join(p[1:] for p in parts[1:]) == long_line)

    # Multi-byte safety: splitting mid-character produces a file strict
    # parsers reject outright.
    uni = fold("SUMMARY:" + ("é" * 120))
    ok = True
    for p in uni.split("\r\n"):
        try:
            p.encode("utf-8").decode("utf-8")
        except Exception:
            ok = False
    check("folding never splits a multi-byte character", ok)

    body = build_ics(uid="u@x", starts_at=U(2026, 9, 1, 15), ends_at=U(2026, 9, 1, 16),
                     summary="Call with Smith, John; re: pricing")
    check("escaped summary survives into the document",
          "Smith\\, John\\; re: pricing" in body.replace("\r\n ", ""), body[:400])

    a = ics_uid("appt-123", "blake@example.com")
    b = ics_uid("appt-123", "blake@example.com")
    c = ics_uid("appt-123", "michael@example.com")
    check("UID is stable across calls", a == b, (a, b))
    check("UID differs per recipient", a != c, (a, c))
    check("UID has no characters needing escaping",
          all(ch.isalnum() or ch in "-._@" for ch in a), a)


# ═══════════════════════════════════════════════════════════════════════════
# 3. THE FAKE PROVIDER
# ═══════════════════════════════════════════════════════════════════════════
#
# Injected via register_provider. Everything the orchestrator can encounter
# from a real vendor is reachable here — success, transport failure, expired
# token, missing scope, rate limit — with no network and no credentials.

class FakeProvider(object):
    key = "fake"
    calls = []            # class-level: every call, in order, across instances
    outcome = "ok"        # ok | transport | reauth | scope | rate_limit | notready
    busy = []             # BusyIntervals get_busy should return
    busy_error = None
    seq = {"n": 0}

    def __init__(self, user, connection=None, org=None):
        self.user = user
        self.connection = connection
        self.org = org

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.outcome = "ok"
        cls.busy = []
        cls.busy_error = None
        cls.seq = {"n": 0}

    def _record(self, op, **kw):
        FakeProvider.calls.append(dict(op=op, user=getattr(self.user, "id", None), **kw))

    def _result(self, event_id=None):
        from app.services.calendar_providers.base import SyncResult
        o = FakeProvider.outcome
        if o == "ok":
            if not event_id:
                FakeProvider.seq["n"] += 1
                event_id = "fake-evt-%d" % FakeProvider.seq["n"]
            return SyncResult(ok=True, external_event_id=event_id)
        return SyncResult.failure(o, "fake %s" % o)

    def is_ready(self):
        if FakeProvider.outcome == "notready":
            return False, "fake not ready"
        return True, None

    def create_event(self, payload):
        self._record("create", payload=payload)
        return self._result()

    def update_event(self, external_event_id, payload):
        self._record("update", event_id=external_event_id, payload=payload)
        return self._result(external_event_id)

    def cancel_event(self, external_event_id, payload=None):
        self._record("cancel", event_id=external_event_id, payload=payload)
        return self._result(external_event_id)

    def get_busy(self, start_utc, end_utc):
        self._record("get_busy", start=start_utc, end=end_utc)
        if FakeProvider.busy_error:
            from app.services.calendar_providers.base import SyncResult
            return [], SyncResult.failure(FakeProvider.busy_error, "fake busy error")
        return list(FakeProvider.busy), None


class FakeUser(object):
    """Just enough user for the pure-registry tests. The DB-backed sections
    below use real User rows."""
    def __init__(self, uid="u1", ms=None, google=None, email="u@example.com"):
        self.id = uid
        self.email = email
        self.full_name = "Test User"
        self.microsoft_oauth_refresh_token_encrypted = ms
        self.google_oauth_refresh_token_encrypted = google
        self.google_calendar_id = "primary"


# ═══════════════════════════════════════════════════════════════════════════
# 4. PROVIDER RESOLUTION AND THE .ICS FALLBACK
# ═══════════════════════════════════════════════════════════════════════════

def test_registry():
    print("\n[4] Provider resolution")
    from app.services import calendar_providers as reg

    check("no connection and no token resolves to .ics",
          reg.resolve_provider_key(None, FakeUser()) == reg.PROVIDER_ICS)
    check("a Microsoft token resolves to microsoft",
          reg.resolve_provider_key(None, FakeUser(ms="tok")) == reg.PROVIDER_MICROSOFT)
    check("a Google token resolves to google",
          reg.resolve_provider_key(None, FakeUser(google="tok")) == reg.PROVIDER_GOOGLE)
    check("both tokens prefer Microsoft (first production provider)",
          reg.resolve_provider_key(None, FakeUser(ms="a", google="b")) == reg.PROVIDER_MICROSOFT)
    check("an explicit preference wins",
          reg.resolve_provider_key(None, FakeUser(ms="a"), prefer=reg.PROVIDER_GOOGLE)
          == reg.PROVIDER_GOOGLE)
    check("a nonsense preference is ignored, not obeyed",
          reg.resolve_provider_key(None, FakeUser(ms="a"), prefer="pigeon")
          == reg.PROVIDER_MICROSOFT)

    check(".ics is not treated as a readable calendar",
          reg.is_external_calendar(reg.PROVIDER_ICS) is False)
    check("microsoft is a readable calendar",
          reg.is_external_calendar(reg.PROVIDER_MICROSOFT) is True)
    check("google is a readable calendar",
          reg.is_external_calendar(reg.PROVIDER_GOOGLE) is True)

    # get_provider must ALWAYS return something. This is the promise that keeps
    # a participant without a calendar from being dropped from a meeting.
    p = reg.get_provider(None, FakeUser())
    check("get_provider never returns None for an unconnected user", p is not None)
    check("an unconnected user gets the .ics provider",
          getattr(p, "key", None) == reg.PROVIDER_ICS, getattr(p, "key", None))

    # A user with a token but a provider that cannot be built must still get a
    # working object, not None and not an exception.
    reg.register_provider(reg.PROVIDER_MICROSOFT, lambda u, c, o: None)
    try:
        p2 = reg.get_provider(None, FakeUser(ms="tok"))
        check("an unbuildable provider falls back rather than returning None",
              p2 is not None and getattr(p2, "key", None) == reg.PROVIDER_ICS,
              getattr(p2, "key", None))
    finally:
        reg.reset_providers()

    # And the injection seam itself.
    FakeProvider.reset()
    reg.register_provider(reg.PROVIDER_MICROSOFT, FakeProvider)
    try:
        p3 = reg.get_provider(None, FakeUser(ms="tok"))
        check("register_provider injects a fake at the real call site",
              getattr(p3, "key", None) == "fake", getattr(p3, "key", None))
        r = p3.create_event(object())
        check("the fake records calls", len(FakeProvider.calls) == 1)
        check("the fake returns a SyncResult", r.ok and bool(r.external_event_id))
    finally:
        reg.reset_providers()
        FakeProvider.reset()

    check("reset_providers restores the real resolution",
          getattr(reg.get_provider(None, FakeUser()), "key", None) == reg.PROVIDER_ICS)


# ═══════════════════════════════════════════════════════════════════════════
# 5. THE .ICS EMAIL PROVIDER
# ═══════════════════════════════════════════════════════════════════════════

SENT = []


def _fake_send(to_email, subject, body_html, attachments=None, org=None):
    SENT.append(dict(to=to_email, subject=subject, html=body_html,
                     attachments=attachments or [], org=org))
    return {"success": True, "provider_message_id": "msg-%d" % len(SENT), "error": None}


def _failing_send(to_email, subject, body_html, attachments=None, org=None):
    SENT.append(dict(to=to_email, subject=subject, failed=True))
    return {"success": False, "provider_message_id": None, "error": "domain not verified"}


def test_ics_provider():
    print("\n[5] The .ics email fallback provider")
    from app.services import email_service
    from app.services.calendar_providers.ics import IcsEmailProvider
    from app.services.calendar_providers.base import EventPayload

    original = email_service.send_email_via_provider
    email_service.send_email_via_provider = _fake_send
    del SENT[:]
    try:
        user = FakeUser(uid="u-blake", email="blake@example.com")
        prov = IcsEmailProvider(user, None, None)

        ready, reason = prov.is_ready()
        check("ready when the participant has an email address", ready, reason)

        no_email = IcsEmailProvider(FakeUser(email=None), None, None)
        ok2, reason2 = no_email.is_ready()
        check("not ready without an email address", ok2 is False and bool(reason2), reason2)

        payload = EventPayload(
            subject="Discovery call",
            starts_at=U(2026, 9, 15, 14, 0),
            ends_at=U(2026, 9, 15, 14, 30),
            timezone="America/Chicago",
            body_text="Intro conversation",
            advisorflow_appointment_id="appt-77",
            organizer_email="support@evosyspro.live",
            organizer_name="EvoSys Pro",
        )
        res = prov.create_event(payload)
        check("create_event succeeds", res.ok, res.error_message)
        check("exactly one email sent", len(SENT) == 1, len(SENT))
        check("addressed to the participant", SENT[0]["to"] == "blake@example.com")
        att = SENT[0]["attachments"][0]
        check("an attachment is present", bool(att))
        check("attachment is text/calendar", att["content_type"].startswith("text/calendar"),
              att["content_type"])
        check("attachment declares method=REQUEST", "method=REQUEST" in att["content_type"])
        check("attachment filename ends .ics", att["filename"].endswith(".ics"))
        decoded = base64.b64decode(att["content"]).decode("utf-8")
        check("attachment decodes to a VCALENDAR", decoded.startswith("BEGIN:VCALENDAR"))
        check("event id returned is the UID", res.external_event_id in decoded,
              res.external_event_id)

        first_uid = res.external_event_id

        # Reschedule: same UID, higher SEQUENCE. Both are required, and a
        # SEQUENCE that does not increase is silently dropped by Outlook.
        payload.starts_at = U(2026, 9, 16, 15, 0)
        payload.ends_at = U(2026, 9, 16, 15, 30)
        payload.sequence = 0
        upd = prov.update_event(first_uid, payload)
        check("update_event succeeds", upd.ok, upd.error_message)
        check("update keeps the SAME event id", upd.external_event_id == first_uid)
        body2 = base64.b64decode(SENT[1]["attachments"][0]["content"]).decode("utf-8")
        check("update reuses the same UID", ("UID:" + first_uid) in body2)
        check("update raises SEQUENCE above zero", "SEQUENCE:0" not in body2, body2[:300])
        check("update carries the new time", "DTSTART:20260916T150000Z" in body2, body2[:300])

        payload.sequence = 3
        can = prov.cancel_event(first_uid, payload)
        check("cancel_event succeeds", can.ok, can.error_message)
        body3 = base64.b64decode(SENT[2]["attachments"][0]["content"]).decode("utf-8")
        check("cancellation reuses the same UID", ("UID:" + first_uid) in body3)
        check("cancellation is METHOD:CANCEL", "METHOD:CANCEL" in body3)
        check("cancellation attachment declares method=CANCEL",
              "method=CANCEL" in SENT[2]["attachments"][0]["content_type"])
        check("cancellation SEQUENCE is above the update's", "SEQUENCE:4" in body3, body3[:300])
        check("cancellation subject says cancelled",
              SENT[2]["subject"].lower().startswith("cancelled"), SENT[2]["subject"])

        # Cancelling without the meeting details must FAIL LOUDLY rather than
        # emit an .ics the recipient's client will ignore while we log success.
        bad = prov.cancel_event(first_uid, None)
        check("cancel without a payload is refused, not faked",
              bad.ok is False and bad.error_code == "needs_payload", bad.error_code)

        # No external calendar exists. Empty AND no error — a known-absent
        # calendar is not a failed read and must not raise an alert.
        busy, err = prov.get_busy(U(2026, 9, 1, 0), U(2026, 9, 30, 0))
        check("get_busy returns no intervals", busy == [])
        check("get_busy returns NO error for an absent calendar", err is None, err)
    finally:
        email_service.send_email_via_provider = original

    email_service.send_email_via_provider = _failing_send
    del SENT[:]
    try:
        prov = IcsEmailProvider(FakeUser(email="x@example.com"), None, None)
        from app.services.calendar_providers.base import EventPayload
        p = EventPayload(subject="X", starts_at=U(2026, 9, 15, 14),
                         ends_at=U(2026, 9, 15, 15), timezone="UTC",
                         advisorflow_appointment_id="appt-9")
        r = prov.create_event(p)
        check("a rejected email is reported as a failure", r.ok is False, r)
        check("email failure is not misreported as reauth",
              r.needs_reauth is False and r.error_code == "email_failed", r.error_code)
        check("the provider error message survives", "domain not verified" in (r.error_message or ""),
              r.error_message)
    finally:
        email_service.send_email_via_provider = original
        del SENT[:]


# ═══════════════════════════════════════════════════════════════════════════
# 6. VENDOR PROVIDER ERROR MAPPING (no network)
# ═══════════════════════════════════════════════════════════════════════════

def test_error_mapping():
    print("\n[6] Vendor error mapping")
    from app.services.calendar_providers.microsoft import MicrosoftCalendarProvider
    from app.services.calendar_providers.google import GoogleCalendarProvider

    class Resp(object):
        def __init__(self, code, text=""):
            self.status_code = code
            self.text = text

    ms = MicrosoftCalendarProvider(FakeUser(ms="tok"))
    check("MS 401 -> reauth", ms._classify(Resp(401)).error_code == "reauth")
    check("MS 403 -> scope", ms._classify(Resp(403)).error_code == "scope")
    check("MS 403 message tells the user to reconnect",
          "reconnect" in (ms._classify(Resp(403)).error_message or "").lower())
    check("MS 404 -> http_404", ms._classify(Resp(404)).error_code == "http_404")
    check("MS 429 -> rate_limit", ms._classify(Resp(429)).error_code == "rate_limit")
    check("MS 500 is neither reauth nor scope",
          ms._classify(Resp(500)).needs_reauth is False)
    check("MS reauth/scope both flag needs_reauth",
          ms._classify(Resp(401)).needs_reauth and ms._classify(Resp(403)).needs_reauth)

    ms_unconnected = MicrosoftCalendarProvider(FakeUser())
    ok, why = ms_unconnected.is_ready()
    check("MS not ready without a token", ok is False and bool(why), why)

    g = GoogleCalendarProvider(FakeUser(google="tok"))

    class GResp(dict):
        # HttpError reads .status AND .reason off the response during
        # construction, and googleapiclient treats it dict-like. Anything less
        # than this and the test harness fails before the code under test runs.
        def __init__(self, status):
            super(GResp, self).__init__(status=status)
            self.status = status
            self.reason = "Error"

    try:
        from googleapiclient.errors import HttpError
        have_google = True
    except Exception as e:
        have_google = False
        print("       (googleapiclient not importable here: %s)" % e)

    if have_google:
        def http_err(code, content=b""):
            return HttpError(GResp(code), content)
        check("Google 401 -> reauth", g._classify(http_err(401)).error_code == "reauth")
        check("Google 403 insufficient -> scope",
              g._classify(http_err(403, b'{"error":{"message":"Insufficient Permission"}}')
                          ).error_code == "scope")
        # Google overloads 403 for quota. Those need opposite handling: one
        # needs the user, the other needs only patience.
        check("Google 403 rateLimitExceeded -> rate_limit, NOT scope",
              g._classify(http_err(403, b'{"error":{"errors":[{"reason":"rateLimitExceeded"}]}}')
                          ).error_code == "rate_limit")
        check("Google 404 -> http_404", g._classify(http_err(404)).error_code == "http_404")
        check("Google 410 Gone -> http_404 (already deleted)",
              g._classify(http_err(410)).error_code == "http_404")
        check("Google 429 -> rate_limit", g._classify(http_err(429)).error_code == "rate_limit")
    check("Google non-HTTP exception -> transport",
          g._classify(RuntimeError("boom")).error_code == "transport")

    g_unconnected = GoogleCalendarProvider(FakeUser())
    ok, why = g_unconnected.is_ready()
    check("Google not ready without a token", ok is False and bool(why), why)


def test_google_parsing():
    print("\n[7] Google response parsing")
    from app.services.calendar_providers.google import _parse_google_dt, _rfc3339

    check("UTC Z suffix parses to naive UTC",
          _parse_google_dt({"dateTime": "2026-09-15T14:00:00Z"}) == U(2026, 9, 15, 14),
          _parse_google_dt({"dateTime": "2026-09-15T14:00:00Z"}))
    # This is the one that matters: an offset time must be CONVERTED, not
    # truncated. Truncating it books the meeting five hours wrong.
    check("offset time converts to UTC",
          _parse_google_dt({"dateTime": "2026-09-15T09:00:00-05:00"}) == U(2026, 9, 15, 14),
          _parse_google_dt({"dateTime": "2026-09-15T09:00:00-05:00"}))
    check("positive offset converts to UTC",
          _parse_google_dt({"dateTime": "2026-09-15T18:30:00+02:00"}) == U(2026, 9, 15, 16, 30),
          _parse_google_dt({"dateTime": "2026-09-15T18:30:00+02:00"}))
    check("all-day date parses to midnight",
          _parse_google_dt({"date": "2026-09-15"}) == U(2026, 9, 15, 0))
    check("garbage returns None rather than a wrong time",
          _parse_google_dt({"dateTime": "not-a-time"}) is None)
    check("empty node returns None", _parse_google_dt({}) is None)
    check("rfc3339 emits an explicit Z",
          _rfc3339(U(2026, 9, 15, 14)) == "2026-09-15T14:00:00Z", _rfc3339(U(2026, 9, 15, 14)))


def test_payload_privacy():
    print("\n[8] What the types refuse to carry")
    from app.services.calendar_providers.base import BusyInterval, EventPayload

    b = BusyInterval(starts_at=U(2026, 9, 1, 14), ends_at=U(2026, 9, 1, 15))
    fields = set(getattr(b, "__dataclass_fields__", {}).keys())
    # Structural, not a promise: there is nowhere to put a subject, so a
    # colleague's meeting titles cannot reach our database even by accident.
    check("BusyInterval has no subject field", "subject" not in fields, fields)
    check("BusyInterval has no attendees field", "attendees" not in fields, fields)
    check("BusyInterval has no body/notes field",
          not (fields & {"body", "body_text", "notes", "description"}), fields)

    p = EventPayload(subject="x", starts_at=U(2026, 9, 1, 14), ends_at=U(2026, 9, 1, 15),
                     timezone="UTC")
    pf = set(getattr(p, "__dataclass_fields__", {}).keys())
    check("EventPayload has no internal-notes field",
          not (pf & {"internal_notes", "private_notes"}), pf)
    check("EventPayload defaults sequence to 0", p.sequence == 0)
    check("EventPayload defaults attendees to empty, not None", p.attendees == [])


# ═══════════════════════════════════════════════════════════════════════════
# 9. EXTERNAL BUSY INGESTION (database-backed)
# ═══════════════════════════════════════════════════════════════════════════

DB = {}


def db_setup():
    from app.main import app                                       # noqa: F401
    from app.deps import SessionLocal, engine
    from app.models.models import Base, User
    from app.services.auth_service import hash_password
    import app.models.calendar_models                              # noqa: F401
    import app.models.scheduling_models                            # noqa: F401

    # create_all only. run_auto_migrations() emits Postgres-only DDL and is
    # exercised against the real database at deploy time; running it here would
    # bury the results in expected SQLite syntax errors. That the new columns
    # are REGISTERED for migration is asserted statically in [12] instead.
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    u = User(email="ext@example.com", full_name="External Test",
             password_hash=hash_password("x"), role="advisor",
             must_change_password=False, is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    DB["session"] = db
    DB["user"] = u
    return db, u


def test_external_busy():
    print("\n[9] External busy ingestion")
    from app.services import calendar_providers as reg
    from app.services import external_busy as eb
    from app.services import availability as av
    from app.services.calendar_providers.base import BusyInterval
    from app.models.calendar_models import CalendarConnection, ExternalBusyBlock

    db, user = DB["session"], DB["user"]
    NOW = U(2026, 9, 14, 6, 0)              # Monday 06:00 UTC = 01:00 Chicago
    WSTART, WEND = U(2026, 9, 14, 0), U(2026, 9, 19, 0)

    prof = av.get_or_create_profile(db, user, "America/Chicago")
    prof.min_notice_minutes = 0
    prof.buffer_before_minutes = 0
    prof.buffer_after_minutes = 0
    db.commit()

    # Baseline: Tuesday, no external calendar at all.
    day_s, day_e = U(2026, 9, 15, 0), U(2026, 9, 16, 0)
    base = av.free_intervals_for_user(db, user, day_s, day_e, now_utc=NOW)
    check("a user with no external calendar has ordinary availability",
          len(base) == 2, base)

    # An unconnected user must not be refreshed as if they had failed.
    rep = eb.refresh_external_busy(db, user, WSTART, WEND, now_utc=NOW)
    check("refreshing an unconnected user is not an error",
          rep["refreshed"] is False and rep["reason"] == "no_external_calendar"
          and rep["error"] is None, rep)

    # Connect Microsoft, with the fake standing in for Graph.
    user.microsoft_oauth_refresh_token_encrypted = "enc-token"
    conn = CalendarConnection(user_id=user.id, provider=reg.PROVIDER_MICROSOFT,
                              is_connected=True, calendar_scope_ok=True,
                              account_email="ext@example.com")
    db.add(conn)
    db.commit()

    FakeProvider.reset()
    FakeProvider.busy = [
        # 10:00-11:00 Chicago on Tuesday = 15:00-16:00 UTC
        BusyInterval(starts_at=U(2026, 9, 15, 15), ends_at=U(2026, 9, 15, 16),
                     provider_event_id="ext-1"),
    ]
    reg.register_provider(reg.PROVIDER_MICROSOFT, FakeProvider)
    try:
        rep = eb.refresh_external_busy(db, user, WSTART, WEND, now_utc=NOW)
        db.commit()
        check("a connected user refreshes", rep["refreshed"] is True, rep)
        check("the busy block was cached", rep["count"] == 1, rep)
        rows = db.query(ExternalBusyBlock).filter(
            ExternalBusyBlock.user_id == user.id).all()
        check("exactly one cache row exists", len(rows) == 1, len(rows))
        check("the cache stores the interval", rows[0].starts_at == U(2026, 9, 15, 15))
        check("the cache marks the block private (no subject kept)",
              rows[0].is_private is True)
        check("the cache records the fetched window",
              rows[0].window_start == WSTART and rows[0].window_end == WEND)

        after = av.free_intervals_for_user(db, user, day_s, day_e, now_utc=NOW)
        covered_before = sum((e - s).total_seconds() for s, e in base) / 60
        covered_after = sum((e - s).total_seconds() for s, e in after) / 60
        check("external busy time is removed from availability",
              covered_after == covered_before - 60,
              (covered_before, covered_after))
        check("the external block genuinely split the morning",
              any(e == U(2026, 9, 15, 15) for s, e in after), after)
        check("availability resumes after the external block",
              any(s == U(2026, 9, 15, 16) for s, e in after), after)

        # The escape hatch must actually escape.
        ignored = av.free_intervals_for_user(db, user, day_s, day_e, now_utc=NOW,
                                             include_external=False)
        check("include_external=False ignores the external calendar",
              ignored == base, ignored)

        # The engine reads the CACHE. It must not call the provider.
        calls_before = len(FakeProvider.calls)
        av.free_intervals_for_user(db, user, day_s, day_e, now_utc=NOW)
        check("the availability engine never calls a provider",
              len(FakeProvider.calls) == calls_before,
              FakeProvider.calls[calls_before:])
    finally:
        reg.reset_providers()


def test_external_busy_failure_modes():
    print("\n[10] External busy — failure and freshness")
    from app.services import calendar_providers as reg
    from app.services import external_busy as eb
    from app.services import availability as av
    from app.models.calendar_models import CalendarConnection, ExternalBusyBlock

    db, user = DB["session"], DB["user"]
    NOW = U(2026, 9, 14, 6, 0)
    WSTART, WEND = U(2026, 9, 14, 0), U(2026, 9, 19, 0)
    day_s, day_e = U(2026, 9, 15, 0), U(2026, 9, 16, 0)
    conn = (db.query(CalendarConnection)
            .filter(CalendarConnection.user_id == user.id).first())

    FakeProvider.reset()
    reg.register_provider(reg.PROVIDER_MICROSOFT, FakeProvider)
    try:
        check("a just-refreshed window is fresh",
              eb.cache_is_fresh(db, user.id, reg.PROVIDER_MICROSOFT,
                                WSTART, WEND, now_utc=NOW) is True)
        check("a WIDER window than was fetched is NOT fresh",
              eb.cache_is_fresh(db, user.id, reg.PROVIDER_MICROSOFT,
                                WSTART, U(2026, 9, 30, 0), now_utc=NOW) is False)
        check("the same window later is stale",
              eb.cache_is_fresh(db, user.id, reg.PROVIDER_MICROSOFT, WSTART, WEND,
                                now_utc=NOW + timedelta(hours=2)) is False)

        calls = len(FakeProvider.calls)
        rep = eb.refresh_external_busy(db, user, WSTART, WEND, now_utc=NOW)
        check("a fresh cache is not re-fetched",
              rep["reason"] == "cache_fresh" and len(FakeProvider.calls) == calls, rep)
        rep = eb.refresh_external_busy(db, user, WSTART, WEND, now_utc=NOW, force=True)
        check("force=True re-fetches anyway",
              rep["refreshed"] is True and len(FakeProvider.calls) > calls, rep)
        db.commit()

        # A provider failure must NOT wipe what we already knew: stale busy
        # time is closer to the truth than pretending the person is free.
        before = len(db.query(ExternalBusyBlock).filter(
            ExternalBusyBlock.user_id == user.id).all())
        FakeProvider.busy_error = "transport"
        rep = eb.refresh_external_busy(db, user, WSTART, WEND,
                                       now_utc=NOW + timedelta(hours=3))
        db.commit()
        check("a provider error is reported, not raised",
              rep["refreshed"] is False and rep["reason"] == "provider_error", rep)
        after = len(db.query(ExternalBusyBlock).filter(
            ExternalBusyBlock.user_id == user.id).all())
        check("a failed refresh keeps the previous cache", after == before,
              (before, after))
        still = av.free_intervals_for_user(db, user, day_s, day_e, now_utc=NOW)
        check("availability still works during a provider outage",
              len(still) > 0, still)
        db.refresh(conn)
        check("the failure is recorded on the connection",
              conn.failure_count >= 1 and bool(conn.last_error), conn.failure_count)
        check("a transport failure does NOT disconnect the user",
              conn.is_connected is True)

        # An expired grant is different: only the user can fix it, and the UI
        # has to be able to say so.
        FakeProvider.busy_error = "reauth"
        rep = eb.refresh_external_busy(db, user, WSTART, WEND,
                                       now_utc=NOW + timedelta(hours=4))
        db.commit()
        db.refresh(conn)
        check("a reauth failure is flagged as needing the user",
              rep.get("needs_reauth") is True, rep)
        check("a reauth failure marks the connection disconnected",
              conn.is_connected is False)
        check("a reauth failure clears the calendar scope flag",
              conn.calendar_scope_ok is False)
        check("the stored error never contains a token",
              "enc-token" not in (conn.last_error or ""), conn.last_error)
    finally:
        FakeProvider.reset()
        reg.reset_providers()


def test_scopes():
    print("\n[11] OAuth scopes")
    from app.services import microsoft_email_service as mes
    from app.services import calendar_service as cs

    check("Microsoft requests calendar write",
          "Calendars.ReadWrite" in mes.SCOPES, mes.SCOPES)
    check("Microsoft still requests Mail.Send (email must not break)",
          "Mail.Send" in mes.SCOPES, mes.SCOPES)
    check("Microsoft still requests offline_access (no refresh token without it)",
          "offline_access" in mes.SCOPES, mes.SCOPES)
    check("Google already grants calendar.events — no reconnect needed",
          any("calendar.events" in s for s in cs.SCOPES), cs.SCOPES)


# ═══════════════════════════════════════════════════════════════════════════
# 12. SYNC ORCHESTRATION
# ═══════════════════════════════════════════════════════════════════════════

def sync_fixture():
    """Three participants, three different situations — which is the normal
    case, not an edge case: Blake on Microsoft, Michael on Google, Mike on
    nothing at all."""
    from app.deps import SessionLocal
    from app.models.models import Platform, User
    from app.models.sales_models import BrandSalesOrg
    from app.models.scheduling_models import SalesAppointment, AppointmentParticipant
    from app.models.calendar_models import CalendarConnection
    from app.services.auth_service import hash_password
    from app.services import calendar_providers as reg

    db = SessionLocal()
    db.add(Platform(id="plt-evo", name="EvoSys Pro", slug="evosyspro"))
    db.flush()
    db.add(BrandSalesOrg(id="bso-evo", platform_id="plt-evo", name="EvoSys Pro Sales",
                         slug="evosyspro-sales", timezone="America/Chicago"))
    db.flush()

    def mk(uid, email, name, ms=None, google=None):
        u = User(id=uid, email=email, full_name=name, password_hash=hash_password("x"),
                 role="advisor", must_change_password=False, is_active=True,
                 microsoft_oauth_refresh_token_encrypted=ms,
                 google_oauth_refresh_token_encrypted=google)
        db.add(u)
        return u

    mk("s-blake", "blake@evosyspro.live", "Blake Rehani", ms="enc-ms")
    mk("s-michael", "michael@evosyspro.live", "Michael Schlueter", google="enc-g")
    mk("s-mike", "mike@evosyspro.live", "Mike Simmons")
    db.flush()

    for uid, provider in (("s-blake", reg.PROVIDER_MICROSOFT),
                          ("s-michael", reg.PROVIDER_GOOGLE)):
        db.add(CalendarConnection(user_id=uid, provider=provider, is_connected=True,
                                  calendar_scope_ok=True))

    appt = SalesAppointment(
        id="appt-1", brand_sales_org_id="bso-evo", title="Discovery + Demo",
        starts_at=U(2026, 9, 15, 15), ends_at=U(2026, 9, 15, 16),
        timezone="America/Chicago",
        prospect_name="Dana Reyes", prospect_company="Greenland Memorial",
        prospect_email="dana@greenland.example", prospect_phone="555-0100",
        notes="INTERNAL: budget is soft, do not mention the discount.",
    )
    db.add(appt)
    db.flush()
    for uid in ("s-blake", "s-michael", "s-mike"):
        db.add(AppointmentParticipant(
            appointment_id="appt-1", user_id=uid, is_required=True,
            busy_start_at=appt.starts_at, busy_end_at=appt.ends_at))
    db.commit()
    DB["sync_db"] = db
    DB["appt"] = appt
    return db, appt


def test_sync_orchestration():
    print("\n[12] Sync orchestration")
    from app.services import calendar_providers as reg
    from app.services import appointment_sync as sync
    from app.models.scheduling_models import AppointmentParticipant
    from app.models.calendar_models import (
        AppointmentSyncLog, SYNC_SYNCED, SYNC_ICS_SENT, SYNC_NOT_CONNECTED,
        SYNC_FAILED, SYNC_RETRYING, SYNC_REAUTH,
    )

    db, appt = sync_fixture()
    NOW = U(2026, 9, 14, 12)

    def part(uid):
        return (db.query(AppointmentParticipant)
                .filter(AppointmentParticipant.appointment_id == appt.id,
                        AppointmentParticipant.user_id == uid).first())

    del SENT[:]
    from app.services import email_service
    original_send = email_service.send_email_via_provider
    email_service.send_email_via_provider = _fake_send

    FakeProvider.reset()
    reg.register_provider(reg.PROVIDER_MICROSOFT, FakeProvider)
    reg.register_provider(reg.PROVIDER_GOOGLE, FakeProvider)
    try:
        rep = sync.sync_appointment(db, appt, now=NOW)
        check("every participant is accounted for", rep["total"] == 3, rep)
        check("the two connected calendars synced", rep["synced"] == 2, rep)
        check("the unconnected participant got an email invite",
              rep["ics_sent"] == 1, rep)
        check("nobody needs a human", rep["needs_attention"] == 0, rep)
        check("a mixed-provider meeting counts as fully OK",
              rep["all_ok"] is True, rep)

        check("Blake synced via Microsoft",
              part("s-blake").sync_status == SYNC_SYNCED
              and part("s-blake").external_calendar_provider == reg.PROVIDER_MICROSOFT,
              part("s-blake").sync_status)
        check("Michael synced via Google",
              part("s-michael").external_calendar_provider == reg.PROVIDER_GOOGLE,
              part("s-michael").external_calendar_provider)
        check("Mike is labelled ics_sent, NOT synced",
              part("s-mike").sync_status == SYNC_ICS_SENT,
              part("s-mike").sync_status)
        check("Mike's ics_sent_at is stamped", part("s-mike").ics_sent_at is not None)
        check("Mike still holds an event id to cancel later",
              bool(part("s-mike").external_event_id))
        check("exactly one .ics email went out", len(SENT) == 1, len(SENT))
        check("the email went to the unconnected participant",
              SENT[0]["to"] == "mike@evosyspro.live", SENT[0]["to"])

        # The prospect must not be mailed by a provider, and internal notes
        # must not reach any calendar.
        payloads = [c["payload"] for c in FakeProvider.calls if c["op"] == "create"]
        check("no attendees are put on internal events (no provider invites)",
              all(p.attendees == [] for p in payloads), [p.attendees for p in payloads])
        check("internal notes never reach a calendar body",
              all("budget is soft" not in (p.body_text or "") for p in payloads),
              [p.body_text for p in payloads])
        check("the prospect's name DOES reach the internal event",
              all("Dana Reyes" in (p.body_text or "") for p in payloads))
        check("the event carries the appointment id for reconciliation",
              all(p.advisorflow_appointment_id == appt.id for p in payloads))

        logs = db.query(AppointmentSyncLog).filter(
            AppointmentSyncLog.appointment_id == appt.id).all()
        check("every attempt is logged", len(logs) == 3, len(logs))
        check("no log row contains a token",
              all("enc-" not in (l.error_message or "") for l in logs))

        # Idempotency: syncing twice must MOVE events, not duplicate them.
        blake_evt = part("s-blake").external_event_id
        creates_before = len([c for c in FakeProvider.calls if c["op"] == "create"])
        sync.sync_appointment(db, appt, now=NOW)
        creates_after = len([c for c in FakeProvider.calls if c["op"] == "create"])
        check("a second sync updates rather than creating a duplicate",
              creates_after == creates_before,
              (creates_before, creates_after))
        check("the external event id is unchanged",
              part("s-blake").external_event_id == blake_evt)
        check("the second pass was an update",
              any(c["op"] == "update" for c in FakeProvider.calls))
    finally:
        reg.reset_providers()
        email_service.send_email_via_provider = original_send
        FakeProvider.reset()


def test_sync_failures_and_cancel():
    print("\n[13] Sync failure, retry, cancellation")
    from app.services import calendar_providers as reg
    from app.services import appointment_sync as sync
    from app.services import email_service
    from app.models.scheduling_models import AppointmentParticipant
    from app.models.calendar_models import (
        SYNC_SYNCED, SYNC_ICS_SENT, SYNC_NOT_CONNECTED,
        SYNC_FAILED, SYNC_RETRYING, SYNC_REAUTH,
    )

    db, appt = DB["sync_db"], DB["appt"]
    NOW = U(2026, 9, 14, 13)

    def part(uid):
        return (db.query(AppointmentParticipant)
                .filter(AppointmentParticipant.appointment_id == appt.id,
                        AppointmentParticipant.user_id == uid).first())

    original_send = email_service.send_email_via_provider
    email_service.send_email_via_provider = _fake_send
    FakeProvider.reset()
    reg.register_provider(reg.PROVIDER_MICROSOFT, FakeProvider)
    reg.register_provider(reg.PROVIDER_GOOGLE, FakeProvider)
    try:
        # A transient failure should ask to be retried, not give up.
        for uid in ("s-blake", "s-michael"):
            p = part(uid)
            p.sync_attempts = 0
            p.external_event_id = None
        db.commit()
        FakeProvider.outcome = "transport"
        rep = sync.sync_appointment(db, appt, now=NOW)
        check("a transient failure does not raise", isinstance(rep, dict))
        check("the appointment itself is untouched by a sync failure",
              appt.status != "cancelled" and appt.starts_at == U(2026, 9, 15, 15))
        check("a first transient failure is marked retrying",
              part("s-blake").sync_status == SYNC_RETRYING,
              part("s-blake").sync_status)
        check("partial success is reported as partial",
              rep["partial"] is True and rep["needs_attention"] == 2, rep)
        check("the unconnected participant still succeeded by email",
              rep["ics_sent"] == 1, rep)

        # Repeated failure must eventually stop hiding behind a spinner.
        sync.sync_appointment(db, appt, now=NOW)
        sync.sync_appointment(db, appt, now=NOW)
        check("repeated failure becomes a hard failure a human can see",
              part("s-blake").sync_status == SYNC_FAILED,
              (part("s-blake").sync_status, part("s-blake").sync_attempts))

        # A dead grant is different from a flaky network.
        FakeProvider.outcome = "reauth"
        sync.sync_appointment(db, appt, now=NOW)
        check("an expired grant is marked reauth, not failed",
              part("s-blake").sync_status == SYNC_REAUTH,
              part("s-blake").sync_status)
        check("the stored sync error never contains a token",
              "enc-ms" not in (part("s-blake").sync_error or ""),
              part("s-blake").sync_error)

        # Retry clears the counter and succeeds once the provider recovers.
        FakeProvider.outcome = "ok"
        rep = sync.retry_failed_sync(db, appt, now=NOW)
        check("retry only touches participants that needed it",
              rep["total"] == 2, rep)
        check("retry succeeds once the provider recovers",
              part("s-blake").sync_status == SYNC_SYNCED,
              part("s-blake").sync_status)
        check("retry reset the attempt counter",
              part("s-blake").sync_attempts == 1, part("s-blake").sync_attempts)

        # Reschedule: an update, not a second event.
        appt.starts_at = U(2026, 9, 16, 16)
        appt.ends_at = U(2026, 9, 16, 17)
        appt.rescheduled_count = 1
        db.commit()
        creates_before = len([c for c in FakeProvider.calls if c["op"] == "create"])
        sync.resync_appointment(db, appt, now=NOW)
        check("a reschedule creates no new events",
              len([c for c in FakeProvider.calls if c["op"] == "create"]) == creates_before)
        upd = [c for c in FakeProvider.calls if c["op"] == "update"][-1]
        check("the reschedule carries the new time",
              upd["payload"].starts_at == U(2026, 9, 16, 16), upd["payload"].starts_at)
        check("the reschedule raises the iCalendar SEQUENCE",
              upd["payload"].sequence == 1, upd["payload"].sequence)

        # Cancellation must reach every calendar, or nobody knows it is off.
        del SENT[:]
        ids_before = [part(u).external_event_id for u in
                      ("s-blake", "s-michael", "s-mike")]
        check("everyone had an event before cancelling", all(ids_before), ids_before)
        rep = sync.cancel_appointment_sync(db, appt, now=NOW)
        cancels = [c for c in FakeProvider.calls if c["op"] == "cancel"]
        check("cancellation reached both connected calendars", len(cancels) == 2, len(cancels))
        check("cancellation emailed the unconnected participant",
              len(SENT) == 1 and SENT[0]["subject"].lower().startswith("cancelled"),
              SENT)
        check("event ids are cleared so a retry cannot resurrect them",
              all(part(u).external_event_id is None
                  for u in ("s-blake", "s-michael", "s-mike")))
        check("cancellation reports no outstanding attention",
              rep["needs_attention"] == 0, rep)

        # A failed cancellation is the worst silent failure there is.
        p = part("s-blake")
        p.external_event_id = "evt-zombie"
        db.commit()
        FakeProvider.outcome = "transport"
        rep = sync.cancel_appointment_sync(db, appt, now=NOW)
        check("a failed cancellation is surfaced, not swallowed",
              rep["needs_attention"] >= 1 and part("s-blake").sync_status == SYNC_FAILED,
              rep)
        check("a failed cancellation KEEPS the event id for retry",
              part("s-blake").external_event_id == "evt-zombie",
              part("s-blake").external_event_id)
    finally:
        reg.reset_providers()
        email_service.send_email_via_provider = original_send
        FakeProvider.reset()


# ═══════════════════════════════════════════════════════════════════════════
# 14. PROSPECT INVITATION + CONFIRMATION LINK
# ═══════════════════════════════════════════════════════════════════════════

def test_prospect_invitation():
    print("\n[14] Prospect invitation")
    from app.services import appointment_invites as inv
    from app.services import email_service

    db, appt = DB["sync_db"], DB["appt"]
    NOW = U(2026, 9, 14, 14)

    ident = inv.brand_identity(db, appt)
    check("the brand is resolved from the platform, not hardcoded",
          ident["name"] == "EvoSys Pro", ident)
    check("the brand's real from-address is used",
          ident["from_email"] == "support@evosyspro.live", ident)
    check("the brand's real support phone is used",
          ident["support_phone"] == "469-553-7417", ident)

    original = email_service.send_email_via_provider
    email_service.send_email_via_provider = _fake_send
    del SENT[:]
    try:
        rep = inv.send_prospect_invitation(db, appt, kind="invite", now=NOW)
        check("the invitation is sent", rep["ok"] is True, rep)
        check("addressed to the prospect",
              SENT[0]["to"] == "dana@greenland.example", SENT[0]["to"])
        check("sent from the brand's verified address",
              getattr(SENT[0]["org"], "from_email", None) == "support@evosyspro.live")
        html = SENT[0]["html"]
        # The link now points at the BRAND's own host, which serves
        # /appointments/confirm/:token and calls the same token endpoints the
        # backend HTML page uses. It used to carry the API hostname, so a
        # stranger who had never heard of AdvisorFlow was emailed a link to
        # `advisorflow-backend.onrender.com` - which reads as phishing and
        # outlives the deployment in their inbox.
        check("the email carries a confirmation link",
              "/appointments/confirm/" in html, html[:300])
        check("the confirmation link is on the brand's own host",
              "https://app.evosyspro.live/appointments/confirm/" in html,
              html[:300])
        check("and NEVER on an infrastructure hostname",
              "onrender.com" not in html and "vercel.app" not in html,
              html[:300])
        # The single most important assertion in this section.
        check("INTERNAL NOTES NEVER REACH THE PROSPECT",
              "budget is soft" not in html and "discount" not in html.lower(), html)
        check("the prospect sees the meeting title", "Discovery + Demo" in html)
        check("the prospect is greeted by name", "Dana" in html)
        check("the brand's support phone is offered", "469-553-7417" in html)

        att = SENT[0]["attachments"][0]
        ics = base64.b64decode(att["content"]).decode("utf-8")
        check("a calendar invitation is attached",
              ics.startswith("BEGIN:VCALENDAR"), att["filename"])
        check("the attached invite is a REQUEST", "METHOD:REQUEST" in ics)
        check("internal notes are absent from the .ics too",
              "budget is soft" not in ics, ics[:400])
        check("the organizer is the brand, not a personal mailbox",
              "mailto:support@evosyspro.live" in unfold(ics), unfold(ics)[:400])

        db.refresh(appt)
        check("the appointment records that the invite went out",
              appt.prospect_invite_sent_at is not None)
        check("confirmation moves from pending to sent",
              appt.confirmation_status == "sent", appt.confirmation_status)
        check("no invite error is recorded on success",
              appt.prospect_invite_error is None)

        # A token is issued once and REUSED, so an older email keeps working.
        tok1 = inv.get_or_create_token(db, appt, now=NOW)
        appt.rescheduled_count = 2
        db.commit()
        inv.send_prospect_invitation(db, appt, kind="reschedule", now=NOW)
        tok2 = inv.get_or_create_token(db, appt, now=NOW)
        check("a reschedule REUSES the confirmation token",
              tok1.token == tok2.token,
              "a new token would kill the link in the prospect's first email")
        ics2 = base64.b64decode(SENT[1]["attachments"][0]["content"]).decode("utf-8")
        check("the reschedule .ics reuses the same UID",
              ("UID:" + ics_uid_for(appt)) in unfold(ics2), unfold(ics2)[:200])
        check("the reschedule raises SEQUENCE", "SEQUENCE:2" in ics2, ics2[:400])

        # A failed send must be recorded, not swallowed.
        email_service.send_email_via_provider = _failing_send
        rep = inv.send_prospect_invitation(db, appt, kind="invite", now=NOW)
        db.refresh(appt)
        check("a failed invitation is reported", rep["ok"] is False, rep)
        check("the failure is recorded for a human to retry",
              bool(appt.prospect_invite_error), appt.prospect_invite_error)
    finally:
        email_service.send_email_via_provider = original
        del SENT[:]

    # No email address is a normal state, not a failure.
    appt.prospect_email = None
    db.commit()
    rep = inv.send_prospect_invitation(db, appt, kind="invite", now=NOW)
    check("a meeting with no prospect email is not an error",
          rep["ok"] is False and rep["reason"] == "no_prospect_email", rep)
    appt.prospect_email = "dana@greenland.example"
    db.commit()


def ics_uid_for(appt):
    from app.services.ics_builder import ics_uid
    return ics_uid(appt.id, appt.prospect_email)


def test_confirmation_link():
    print("\n[15] The confirmation link")
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import appointment_invites as inv
    from app.models.scheduling_models import CONF_CONFIRMED, CONF_DECLINED

    db, appt = DB["sync_db"], DB["appt"]
    tok = inv.get_or_create_token(db, appt)
    db.commit()
    token = tok.token
    client = TestClient(app)

    check("the token is long enough to resist guessing", len(token) >= 32, len(token))
    check("the token is URL-safe",
          all(c.isalnum() or c in "-_" for c in token), token)

    before = appt.confirmation_status
    r = client.get("/sales/appointments/confirm/%s" % token)
    check("the confirmation page loads without a login", r.status_code == 200, r.status_code)
    check("the page shows the meeting", "Discovery + Demo" in r.text, r.text[:300])
    check("the page offers both answers",
          "value='confirm'" in r.text and "value='decline'" in r.text)
    check("the page never shows internal notes",
          "budget is soft" not in r.text)

    db.expire_all()
    appt = db.query(type(appt)).filter_by(id="appt-1").first()
    # THE critical property: mail scanners prefetch every link in an inbound
    # message. A GET that confirmed would auto-confirm a large share of
    # invitations seconds after delivery.
    check("GET CHANGES NOTHING (link scanners must not auto-confirm)",
          appt.confirmation_status == before,
          (before, appt.confirmation_status))

    r = client.post("/sales/appointments/confirm/%s" % token, data={"action": "confirm"})
    check("POST confirms", r.status_code == 200, r.status_code)
    check("the prospect is told they are confirmed", "confirmed" in r.text.lower())
    db.expire_all()
    appt = db.query(type(appt)).filter_by(id="appt-1").first()
    check("the appointment is now confirmed",
          appt.confirmation_status == CONF_CONFIRMED, appt.confirmation_status)
    check("the confirmation is attributed to the prospect link, not a staff member",
          appt.confirmation_source == "prospect_link", appt.confirmation_source)
    check("no user id is attributed to a prospect's click",
          appt.confirmed_by is None, appt.confirmed_by)

    # The prospect can change their mind — the token is not burned.
    r = client.post("/sales/appointments/confirm/%s" % token, data={"action": "decline"})
    db.expire_all()
    appt = db.query(type(appt)).filter_by(id="appt-1").first()
    check("the prospect can change their answer",
          appt.confirmation_status == CONF_DECLINED, appt.confirmation_status)

    r = client.post("/sales/appointments/confirm/%s" % token, data={"action": "delete"})
    check("an unknown action is refused", r.status_code == 200 and "wrong" in r.text.lower(),
          r.text[:200])

    # Every rejection must look identical to a stranger, or the endpoint
    # becomes an oracle for which tokens exist.
    r = client.get("/sales/appointments/confirm/%s" % ("z" * 40))
    check("an unknown token is rejected", r.status_code == 200 and "not valid" in r.text)
    row = (db.query(type(tok)).filter_by(token=token).first())
    row.revoked_at = datetime(2026, 1, 1)
    db.commit()
    r = client.get("/sales/appointments/confirm/%s" % token)
    check("a revoked token is rejected", "no longer active" in r.text, r.text[:200])
    r = client.post("/sales/appointments/confirm/%s" % token, data={"action": "confirm"})
    db.expire_all()
    appt = db.query(type(appt)).filter_by(id="appt-1").first()
    check("a revoked token cannot still confirm",
          appt.confirmation_status == CONF_DECLINED, appt.confirmation_status)
    row.revoked_at = None
    row.expires_at = datetime(2020, 1, 1)
    db.commit()
    r = client.get("/sales/appointments/confirm/%s" % token)
    check("an expired token is rejected", "expired" in r.text, r.text[:200])
    row.expires_at = None
    db.commit()

    # Route ordering: the token path must not be swallowed by /{appt_id}.
    r = client.get("/sales/appointments/confirm/%s" % token)
    check("the public route still resolves ahead of /appointments/{appt_id}",
          r.status_code == 200 and "Discovery + Demo" in r.text, r.status_code)
    r = client.get("/sales/appointments/appt-1")
    check("the authenticated route still requires auth",
          r.status_code in (401, 403), r.status_code)


def test_migrations_registered():
    print("\n[16] Migration registration")
    from app import auto_migrate

    # create_all() adds TABLES, never COLUMNS. Every column added to a table
    # that already exists in production must be listed here or it simply never
    # appears — which is a runtime crash on the first query, not a startup one.
    listed = set((t, c) for t, c, _ in auto_migrate.COLUMNS_TO_ADD)
    required = [
        ("sales_appointment_participants", "sync_status"),
        ("sales_appointment_participants", "external_calendar_provider"),
        ("sales_appointment_participants", "external_event_id"),
        ("sales_appointment_participants", "external_synced_at"),
        ("sales_appointment_participants", "sync_attempts"),
        ("sales_appointment_participants", "sync_last_attempt"),
        ("sales_appointment_participants", "sync_error"),
        ("sales_appointment_participants", "ics_sent_at"),
        ("sales_appointments", "prospect_invite_sent_at"),
        ("sales_appointments", "prospect_invite_error"),
        ("sales_appointments", "rescheduled_count"),
        ("sales_appointments", "rescheduled_at"),
        ("sales_appointments", "previous_starts_at"),
        ("sales_appointments", "reschedule_reason"),
        ("calendar_connections", "busy_window_start"),
        ("calendar_connections", "busy_window_end"),
        ("calendar_connections", "busy_fetched_at"),
    ]
    for table, col in required:
        check("%s.%s is registered for migration" % (table, col),
              (table, col) in listed)


# ═══════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 74)
    print("CHECKPOINT 3 — EXTERNAL CALENDAR SYNC + INVITATIONS")
    print("=" * 74)

    test_ics_format()
    test_ics_escaping_and_folding()
    test_registry()
    test_ics_provider()
    test_error_mapping()
    test_google_parsing()
    test_payload_privacy()
    db_setup()
    test_external_busy()
    test_external_busy_failure_modes()
    test_scopes()
    test_sync_orchestration()
    test_sync_failures_and_cancel()
    test_prospect_invitation()
    test_confirmation_link()
    test_migrations_registered()

    print("\n" + "=" * 74)
    if FAILURES:
        print("FAILED (%d):" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
    else:
        print("ALL CHECKS PASSED")
    print("=" * 74)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
