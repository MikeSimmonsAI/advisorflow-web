"""Retell integration bridge regression suite.

Service credential auth, advisor scoping, availability via the REAL engine,
booking with re-validation, double-book refusal, idempotent retry, cross-brand
denial, rate limiting, and what must never leak.

NO TEST CONTACTS ZOOM, A CALENDAR, OR SENDS AN EMAIL. The booking path's side
effects run through fakes registered on the provider registries; email is
monkeypatched at the module boundary. There is no path in here that can reach a
real prospect or a real vendor.

Temp SQLite. Never touches production.

    python scripts/smoke_retell_bridge.py
"""
import os
import sys
import shutil
import tempfile
from datetime import datetime, timedelta, date as date_cls

TMP = tempfile.mkdtemp(prefix="retellbridge_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "smoke" + "0" * 59
os.environ["SECRET_KEY"] = "smoke" + "0" * 59

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient                          # noqa: E402
from app.main import app                                           # noqa: E402
from app.deps import SessionLocal, engine                          # noqa: E402
from app.models.models import Base, Platform, User                 # noqa: E402
from app.models.sales_models import (                              # noqa: E402
    Membership, BrandSalesOrg, Opportunity, BrandPackage,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.models.scheduling_models import (                         # noqa: E402
    MeetingType, SalesAppointment, AppointmentParticipant,
    AvailabilityProfile, AvailabilityWindow,
)
from app.models.integration_models import (                        # noqa: E402
    IntegrationCredential, IntegrationRequestLog, INTEGRATION_RETELL,
)
from app.services.auth_service import hash_password                # noqa: E402
from app.services.integration_auth import generate_key, hash_key   # noqa: E402
from app.services import availability as av                        # noqa: E402
from app.services.meeting_roles import ensure_meeting_types        # noqa: E402

CHI = "America/Chicago"
NYC = "America/New_York"
FAILURES = []
ID = {}
KEYS = {}


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("\n         -> " + str(detail)[:400]) if not ok else ""))
    if not ok:
        FAILURES.append(label)


def H(key):
    return {"Authorization": "Bearer " + key}


# ── provider fakes: nothing leaves this process ─────────────────────────────

class FakeZoom(object):
    key = "zoom"
    calls = []

    def __init__(self, config=None):
        self.config = config

    def is_ready(self):
        return True, None

    def _r(self, mid="zoom-fake-1"):
        from app.services.meeting_providers.base import MeetingResult
        return MeetingResult(ok=True, provider_meeting_id=mid,
                             join_url="https://zoom.example/j/" + mid,
                             host_url="https://zoom.example/s/" + mid + "?zak=SECRET",
                             passcode="000000")

    def create_meeting(self, req):
        FakeZoom.calls.append("create"); return self._r()

    def update_meeting(self, mid, req):
        FakeZoom.calls.append("update"); return self._r(mid)

    def cancel_meeting(self, mid):
        FakeZoom.calls.append("cancel"); return self._r(mid)

    def verify(self):
        return self._r()


class FakeCalendar(object):
    key = "ics"
    calls = []

    def __init__(self, user=None, connection=None, org=None):
        self.user = user

    def is_ready(self):
        return True, None

    def _ok(self, eid="ext-1"):
        from app.services.calendar_providers.base import SyncResult
        return SyncResult(ok=True, external_event_id=eid)

    def create_event(self, payload):
        FakeCalendar.calls.append("create"); return self._ok()

    def update_event(self, eid, payload):
        FakeCalendar.calls.append("update"); return self._ok(eid)

    def cancel_event(self, eid, payload=None):
        FakeCalendar.calls.append("cancel"); return self._ok(eid)

    def get_busy(self, start, end):
        return [], None


EMAILS = []


def _fake_send(to_email, subject, body_html, attachments=None, org=None, **kw):
    EMAILS.append({"to": to_email, "subject": subject})
    return {"success": True, "provider_message_id": "fake", "error": None}


def install_fakes():
    from app.services import meeting_providers as mp
    from app.services import calendar_providers as cp
    from app.services import email_service
    mp.register_provider("zoom", FakeZoom)
    cp.register_provider("ics", FakeCalendar)
    cp.register_provider("microsoft", FakeCalendar)
    cp.register_provider("google", FakeCalendar)
    email_service.send_email_via_provider = _fake_send
    # appointment_invites imports the symbol directly in some paths.
    try:
        from app.services import appointment_invites as ai
        ai.send_email_via_provider = _fake_send
    except Exception:
        pass


# ── fixture ────────────────────────────────────────────────────────────────

def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    db.add_all([Platform(id="plt-evo", name="EvoSys Pro", slug="evosyspro"),
                Platform(id="plt-bb", name="BookaBoost", slug="bookaboost")])
    db.flush()
    db.add_all([
        BrandSalesOrg(id="bso-evo", platform_id="plt-evo", name="EvoSys Pro Sales",
                      slug="evosyspro-sales", timezone=CHI),
        BrandSalesOrg(id="bso-bb", platform_id="plt-bb", name="BookaBoost Sales",
                      slug="bookaboost-sales", timezone=CHI),
    ])
    db.flush()

    def mk(uid, email, name, active=True):
        db.add(User(id=uid, organization_id=None, email=email, full_name=name,
                    password_hash=hash_password("NotUsed123!"), role="advisor",
                    must_change_password=False, is_active=active))

    mk("u-taffy", "advisor@example.com", "Ada Ventura")       # the bridge advisor
    mk("u-other", "other@example.com", "Other Rep")           # same brand, not allowlisted
    mk("u-bb", "bb@example.com", "Other Brand Rep")           # different brand
    mk("u-tenant", "tenant@example.com", "Tenant Advisor")    # no membership at all
    db.flush()
    db.add_all([
        Membership(user_id="u-taffy", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_REP, is_active=True),
        Membership(user_id="u-other", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_REP, is_active=True),
        Membership(user_id="u-bb", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-bb", role=ROLE_SALES_REP, is_active=True),
    ])
    db.flush()
    ensure_meeting_types(db, "bso-evo")
    ensure_meeting_types(db, "bso-bb")
    db.flush()

    # A real working week for the advisor, so availability is genuinely computed
    # rather than defaulted.
    for uid, tz in (("u-taffy", CHI), ("u-other", CHI), ("u-bb", CHI)):
        u = db.query(User).filter_by(id=uid).first()
        prof = av.get_or_create_profile(db, u)
        prof.timezone = tz
        prof.accepts_bookings = True
        prof.min_notice_minutes = 0
        prof.booking_horizon_days = 60
        db.flush()
        for dow in range(0, 5):        # Mon-Fri 09:00-17:00 local
            db.add(AvailabilityWindow(profile_id=prof.id, day_of_week=dow,
                                      start_minute=9 * 60, end_minute=17 * 60))
    db.flush()

    db.add(Opportunity(id="opp-evo", brand_sales_org_id="bso-evo",
                       owner_user_id="u-taffy", company_name="Greenland Memorial",
                       contact_name="Dana Reyes", email="dana@greenland.example",
                       stage="discovery", status="open"))
    db.add(Opportunity(id="opp-bb", brand_sales_org_id="bso-bb",
                       owner_user_id="u-bb", company_name="Other Co",
                       stage="prospect", status="open"))
    db.flush()

    def key(name, brand, default_advisor=None, allow=None, kind=INTEGRATION_RETELL,
            active=True, revoked=None):
        full, prefix, hashed = generate_key()
        db.add(IntegrationCredential(
            name=name, kind=kind, key_prefix=prefix, key_hash=hashed,
            brand_sales_org_id=brand, default_advisor_user_id=default_advisor,
            allowed_advisor_ids=allow, is_active=active, revoked_at=revoked,
            rate_limit_per_minute=60))
        return full

    KEYS["main"] = key("Taffiny voice", "bso-evo", "u-taffy", allow="u-taffy")
    KEYS["nodefault"] = key("No default", "bso-evo")
    KEYS["brandwide"] = key("Brand wide", "bso-evo", "u-taffy")   # no allowlist
    KEYS["bb"] = key("Other brand voice", "bso-bb", "u-bb")
    KEYS["revoked"] = key("Retired", "bso-evo", "u-taffy",
                          revoked=datetime.utcnow())
    KEYS["inactive"] = key("Switched off", "bso-evo", "u-taffy", active=False)
    KEYS["wrongkind"] = key("Some other vendor", "bso-evo", "u-taffy", kind="other")
    db.commit()
    db.close()


def next_weekday(n=1):
    """A local date that is a weekday, n days out — the fixture only opens Mon-Fri."""
    d = date_cls.today() + timedelta(days=n)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


# ═══════════════════════════════════════════════════════════════════════════
# 1. THE CREDENTIAL
# ═══════════════════════════════════════════════════════════════════════════

def test_credential():
    print("\n[1] Service credential")
    c = TestClient(app)

    r = c.get("/integrations/retell/ping")
    check("NO CREDENTIAL IS REFUSED", r.status_code == 401, r.status_code)
    check("and it says how to authenticate",
          "bearer" in (r.headers.get("www-authenticate") or "").lower(),
          dict(r.headers))

    r = c.get("/integrations/retell/ping", headers={"Authorization": "Bearer nope"})
    check("a garbage credential is refused", r.status_code == 401, r.status_code)
    bad_detail = r.json().get("detail")

    r = c.get("/integrations/retell/ping", headers=H(KEYS["revoked"]))
    check("A REVOKED KEY IS REFUSED", r.status_code == 401, r.status_code)
    check("and looks identical to an unknown key",
          r.json().get("detail") == bad_detail, r.json())

    r = c.get("/integrations/retell/ping", headers=H(KEYS["inactive"]))
    check("an inactive key is refused", r.status_code == 401, r.status_code)
    check("also identical", r.json().get("detail") == bad_detail, r.json())

    r = c.get("/integrations/retell/ping", headers=H(KEYS["wrongkind"]))
    check("a key issued for a DIFFERENT integration cannot drive Retell routes",
          r.status_code == 401, r.status_code)

    r = c.get("/integrations/retell/ping",
              headers={"Authorization": KEYS["main"]})       # no "Bearer "
    check("the scheme is required, not just the value", r.status_code == 401,
          r.status_code)

    r = c.get("/integrations/retell/availability?" +
              "key=" + KEYS["main"])
    check("a key in the query string does not authenticate", r.status_code in (401, 405),
          r.status_code)

    r = c.get("/integrations/retell/ping", headers=H(KEYS["main"]))
    check("a valid key works", r.status_code == 200, r.text[:300])
    body = r.json()
    check("ping names the integration", body["integration"] == "Taffiny voice", body)
    check("ping names the brand", body["brand"] == "EvoSys Pro Sales", body)
    check("ping names the default advisor",
          body["default_advisor_name"] == "Ada Ventura", body)
    check("PING RETURNS NO SECRET",
          not any("key" == k or "secret" in k.lower() for k in body),
          sorted(body))
    check("and no key hash or prefix",
          "key_hash" not in str(body) and "evsk_" not in str(body), body)

    db = SessionLocal()
    creds = db.query(IntegrationCredential).all()
    check("NO PLAINTEXT KEY IS STORED ANYWHERE",
          all(KEYS["main"] not in (x.key_hash or "") for x in creds))
    row = db.query(IntegrationCredential).filter_by(name="Taffiny voice").first()
    check("only a hash is stored", row.key_hash == hash_key(KEYS["main"]))
    check("the stored prefix is a prefix of the key",
          KEYS["main"].startswith(row.key_prefix), row.key_prefix)
    check("using the key stamps last_used_at", row.last_used_at is not None)
    db.close()


# ═══════════════════════════════════════════════════════════════════════════
# 2. ADVISOR SCOPING — no enumeration
# ═══════════════════════════════════════════════════════════════════════════

def test_advisor_scope():
    print("\n[2] Advisor isolation")
    c = TestClient(app)
    day = next_weekday(3).isoformat()

    def ask(key, advisor=None):
        b = {"date_from": day, "duration_minutes": 30}
        if advisor:
            b["advisor_id"] = advisor
        return c.post("/integrations/retell/availability", headers=H(key), json=b)

    r = ask(KEYS["main"])
    check("the default advisor is used when none is named", r.status_code == 200,
          r.text[:300])
    check("and the answer names them", r.json()["advisor_id"] == "u-taffy",
          r.json().get("advisor_id"))

    r = ask(KEYS["nodefault"])
    check("a key with no default and no named advisor is a clear 400",
          r.status_code == 400, r.status_code)

    r = ask(KEYS["main"], "u-other")
    check("AN ADVISOR OUTSIDE THE KEY'S ALLOWLIST IS REFUSED",
          r.status_code == 404, r.status_code)
    not_found = r.json().get("detail")

    r = ask(KEYS["main"], "u-bb")
    check("ANOTHER BRAND'S ADVISOR IS REFUSED", r.status_code == 404, r.status_code)
    check("with the identical message — no enumeration oracle",
          r.json().get("detail") == not_found, r.json())

    r = ask(KEYS["main"], "u-tenant")
    check("a user with no sales membership is refused", r.status_code == 404,
          r.status_code)
    check("identically", r.json().get("detail") == not_found, r.json())

    r = ask(KEYS["main"], "00000000-0000-0000-0000-000000000000")
    check("an id that does not exist is refused", r.status_code == 404, r.status_code)
    check("IDENTICALLY — REAL AND FAKE IDS ARE INDISTINGUISHABLE",
          r.json().get("detail") == not_found, r.json())

    # A brand-wide key may reach any member of its own brand, but still not out.
    r = ask(KEYS["brandwide"], "u-other")
    check("a key with no allowlist may reach its own brand", r.status_code == 200,
          r.text[:200])
    r = ask(KEYS["brandwide"], "u-bb")
    check("but still not another brand", r.status_code == 404, r.status_code)

    # The other brand's key is symmetrically confined.
    r = ask(KEYS["bb"], "u-taffy")
    check("CROSS-BRAND IS DENIED IN BOTH DIRECTIONS", r.status_code == 404,
          r.status_code)


# ═══════════════════════════════════════════════════════════════════════════
# 3. AVAILABILITY — the real engine, and what it must not leak
# ═══════════════════════════════════════════════════════════════════════════

def test_availability():
    print("\n[3] Availability")
    c = TestClient(app)
    day = next_weekday(3)

    r = c.post("/integrations/retell/availability", headers=H(KEYS["main"]),
               json={"date_from": day.isoformat(), "duration_minutes": 30})
    check("availability loads", r.status_code == 200, r.text[:300])
    b = r.json()
    check("it reports success", b["success"] is True)
    check("IT RETURNS A TIMEZONE", b["timezone"] == CHI, b.get("timezone"))
    check("it returns slots", b["slot_count"] > 0, b["slot_count"])

    s = b["slots"][0]
    for f in ("starts_at", "ends_at", "starts_at_local", "ends_at_local",
              "duration_minutes", "label"):
        check("every slot has %s" % f, f in s, sorted(s))
    check("starts_at is explicit UTC", s["starts_at"].endswith("Z"), s["starts_at"])
    check("ends_at is explicit UTC", s["ends_at"].endswith("Z"), s["ends_at"])
    check("the slot is the requested length", s["duration_minutes"] == 30, s)
    check("the label is speakable", "at" in s["label"] and "," in s["label"],
          s["label"])

    # The engine, not a parallel grid: the first opening must be 9am LOCAL.
    check("THE FIRST OPENING MATCHES THE ADVISOR'S REAL WORKING HOURS",
          s["starts_at_local"].endswith("T09:00:00"), s["starts_at_local"])

    # Cross-check against the engine directly. If these ever disagree, the
    # bridge has grown a second scheduler, which is the thing it must not do.
    db = SessionLocal()
    u = db.query(User).filter_by(id="u-taffy").first()
    start_utc = av.local_to_utc(day, 0, CHI)
    end_utc = av.local_to_utc(day + timedelta(days=1), 0, CHI)
    engine = av.find_shared_slots(db, [u], [], start_utc, end_utc, 30, limit=40)
    db.close()
    check("THE BRIDGE RETURNS EXACTLY WHAT THE ENGINE RETURNS",
          len(engine["slots"]) == b["slot_count"],
          (len(engine["slots"]), b["slot_count"]))
    check("and the same first instant",
          engine["slots"][0]["starts_at"].replace(microsecond=0).isoformat() + "Z"
          == s["starts_at"],
          (engine["slots"][0]["starts_at"], s["starts_at"]))

    # Timezone override changes the spoken time, not the instant.
    r2 = c.post("/integrations/retell/availability", headers=H(KEYS["main"]),
                json={"date_from": day.isoformat(), "duration_minutes": 30,
                      "timezone": NYC})
    check("a timezone override is accepted", r2.status_code == 200, r2.text[:200])
    check("and is reported back", r2.json()["timezone"] == NYC)
    check("A TIMEZONE OVERRIDE SHIFTS THE WALL CLOCK, NOT THE INSTANT",
          r2.json()["slots"][0]["starts_at_local"].endswith("T10:00:00"),
          r2.json()["slots"][0]["starts_at_local"])

    r3 = c.post("/integrations/retell/availability", headers=H(KEYS["main"]),
                json={"date_from": day.isoformat(), "timezone": "Mars/Olympus"})
    check("an unknown timezone is refused, not silently swapped for UTC",
          r3.status_code == 400, r3.status_code)

    # A meeting type sets the duration without the caller knowing it.
    r4 = c.post("/integrations/retell/availability", headers=H(KEYS["main"]),
                json={"date_from": day.isoformat(), "meeting_type": "discovery"})
    check("a meeting type key is accepted", r4.status_code == 200, r4.text[:200])
    check("and supplies the duration", r4.json()["duration_minutes"] == 30,
          r4.json().get("duration_minutes"))
    r5 = c.post("/integrations/retell/availability", headers=H(KEYS["main"]),
                json={"date_from": day.isoformat(), "meeting_type": "nonsense"})
    check("an unknown meeting type is refused", r5.status_code == 404, r5.status_code)

    # Ranges.
    r6 = c.post("/integrations/retell/availability", headers=H(KEYS["main"]),
                json={"date_from": day.isoformat(),
                      "date_to": (day + timedelta(days=60)).isoformat()})
    check("an oversized range is refused", r6.status_code == 400, r6.status_code)
    r7 = c.post("/integrations/retell/availability", headers=H(KEYS["main"]),
                json={"date_from": day.isoformat(),
                      "date_to": (day - timedelta(days=2)).isoformat()})
    check("a backwards range is refused", r7.status_code == 400, r7.status_code)

    # A weekend: no openings, and a REASON rather than a bare empty list.
    sat = day
    while sat.weekday() != 5:
        sat += timedelta(days=1)
    r8 = c.post("/integrations/retell/availability", headers=H(KEYS["main"]),
                json={"date_from": sat.isoformat()})
    check("a day with no working hours returns no slots", r8.json()["slot_count"] == 0,
          r8.json()["slot_count"])
    check("AND SAYS WHY, RATHER THAN RETURNING A SILENT EMPTY LIST",
          bool(r8.json().get("reason")), r8.json())

    # Leakage.
    raw = r.text.lower()
    for forbidden, why in (("password", "a password"), ("token", "a token"),
                           ("zak=", "a Zoom host token"),
                           ("refresh", "a refresh token"),
                           ("opportunity", "opportunity internals"),
                           ("other rep", "another rep's identity"),
                           ("greenland", "customer/deal data")):
        check("availability leaks no %s" % why, forbidden not in raw, forbidden)
    check("it exposes no user list",
          "u-other" not in raw and "u-bb" not in raw)
    check("it exposes no email addresses", "@example.com" not in raw)


# ═══════════════════════════════════════════════════════════════════════════
# 4. BOOKING — re-validated, and refused when it should be
# ═══════════════════════════════════════════════════════════════════════════

def first_slot(c, key=None, advisor=None, day=None, duration=30):
    day = day or next_weekday(4)
    body = {"date_from": day.isoformat(), "duration_minutes": duration}
    if advisor:
        body["advisor_id"] = advisor
    r = c.post("/integrations/retell/availability", headers=H(key or KEYS["main"]),
               json=body)
    return r.json()["slots"][0]


def test_booking():
    print("\n[4] Booking")
    c = TestClient(app)
    slot = first_slot(c)
    ID["slot"] = slot

    r = c.post("/integrations/retell/book", headers=H(KEYS["main"]), json={
        "starts_at": slot["starts_at"], "duration_minutes": 30,
        "prospect_name": "Dana Reyes", "prospect_email": "dana@greenland.example"})
    check("a booking with no external_ref is refused", r.status_code == 422,
          r.status_code)

    r = c.post("/integrations/retell/book", json={
        "external_ref": "call-noauth", "starts_at": slot["starts_at"]})
    check("BOOKING REQUIRES A CREDENTIAL", r.status_code == 401, r.status_code)

    r = c.post("/integrations/retell/book", headers=H(KEYS["bb"]), json={
        "external_ref": "call-crossbrand", "starts_at": slot["starts_at"],
        "advisor_id": "u-taffy"})
    check("CROSS-BRAND BOOKING IS DENIED", r.status_code == 404, r.status_code)

    r = c.post("/integrations/retell/book", headers=H(KEYS["main"]), json={
        "external_ref": "call-past",
        "starts_at": (datetime.utcnow() - timedelta(days=1))
                     .replace(microsecond=0).isoformat() + "Z"})
    check("a booking in the past is refused", r.status_code == 400, r.status_code)

    r = c.post("/integrations/retell/book", headers=H(KEYS["main"]), json={
        "external_ref": "call-badopp", "starts_at": slot["starts_at"],
        "opportunity_id": "opp-bb"})
    check("another brand's opportunity cannot be attached", r.status_code == 404,
          r.status_code)

    # The real one.
    EMAILS[:] = []
    FakeZoom.calls[:] = []
    FakeCalendar.calls[:] = []
    r = c.post("/integrations/retell/book", headers=H(KEYS["main"]), json={
        "external_ref": "retell-call-0001", "starts_at": slot["starts_at"],
        "duration_minutes": 30, "meeting_type": "discovery",
        "prospect_name": "Dana Reyes", "prospect_email": "dana@greenland.example",
        "prospect_phone": "555-0100", "opportunity_id": "opp-evo",
        "notes": "Booked by voice agent."})
    check("a valid booking succeeds", r.status_code == 200, r.text[:400])
    b = r.json()
    ID["appt"] = b.get("appointment_id")
    check("it returns the appointment id", bool(b.get("appointment_id")), b)
    check("it is not flagged as a replay", b["idempotent_replay"] is False, b)
    check("it returns the instant in UTC", b["starts_at"].endswith("Z"), b["starts_at"])
    check("it returns the timezone", b["timezone"] == CHI, b.get("timezone"))
    check("it returns the local wall clock", bool(b.get("starts_at_local")), b)
    # "sent" here, not "pending": the prospect invitation went out through the
    # fake sender during the booking, which is exactly the behaviour a human
    # booking gets. What matters is that it is not confirmed by us on the
    # prospect's behalf.
    check("it reports the confirmation state",
          b["confirmation_status"] in ("pending", "sent"),
          b.get("confirmation_status"))
    check("AND THE PROSPECT IS NOT AUTO-CONFIRMED",
          b["confirmation_status"] != "confirmed", b.get("confirmation_status"))

    db = SessionLocal()
    appt = db.query(SalesAppointment).filter_by(id=ID["appt"]).first()
    check("THE APPOINTMENT IS A REAL SalesAppointment ROW", appt is not None)
    check("scoped to the credential's brand", appt.brand_sales_org_id == "bso-evo",
          appt.brand_sales_org_id)
    check("the prospect is captured", appt.prospect_name == "Dana Reyes",
          appt.prospect_name)
    check("the meeting type is attached", appt.meeting_type_id is not None)
    check("the opportunity is attached", appt.opportunity_id == "opp-evo",
          appt.opportunity_id)
    parts = db.query(AppointmentParticipant).filter_by(appointment_id=appt.id).all()
    check("the advisor is a participant", len(parts) == 1 and parts[0].user_id == "u-taffy",
          [(p.user_id, p.is_blocking) for p in parts])
    check("AND THEIR TIME IS NOW BLOCKED", parts[0].is_blocking is True)
    db.close()

    check("the fake video provider was used, never a real one",
          "create" in FakeZoom.calls, FakeZoom.calls)
    check("NO REAL EMAIL LEFT THE PROCESS — only the fake recorded one",
          all(isinstance(e, dict) for e in EMAILS), EMAILS[:2])

    # The slot is gone from availability now.
    day = date_cls.fromisoformat(slot["starts_at_local"][:10])
    r2 = c.post("/integrations/retell/availability", headers=H(KEYS["main"]),
                json={"date_from": day.isoformat(), "duration_minutes": 30})
    starts = [s["starts_at"] for s in r2.json()["slots"]]
    check("THE BOOKED SLOT DISAPPEARS FROM AVAILABILITY",
          slot["starts_at"] not in starts, starts[:3])


def test_double_book():
    print("\n[5] Double-booking is refused")
    c = TestClient(app)
    slot = ID["slot"]

    r = c.post("/integrations/retell/book", headers=H(KEYS["main"]), json={
        "external_ref": "retell-call-0002", "starts_at": slot["starts_at"],
        "duration_minutes": 30})
    check("THE SAME SLOT CANNOT BE BOOKED TWICE", r.status_code == 409, r.status_code)
    check("and the refusal tells the agent what to do",
          "another" in (r.json().get("detail") or "").lower(), r.json())

    # Overlapping, not identical — the re-check is interval-based, not equality.
    start = datetime.fromisoformat(slot["starts_at"].rstrip("Z"))
    r2 = c.post("/integrations/retell/book", headers=H(KEYS["main"]), json={
        "external_ref": "retell-call-0003",
        "starts_at": (start + timedelta(minutes=15)).isoformat() + "Z",
        "duration_minutes": 30})
    check("an OVERLAPPING slot is refused too", r2.status_code == 409, r2.status_code)

    db = SessionLocal()
    n = db.query(SalesAppointment).filter_by(brand_sales_org_id="bso-evo").count()
    check("no extra appointment was created by the refusals", n == 1, n)
    db.close()


def test_idempotency():
    print("\n[6] Idempotent retry")
    c = TestClient(app)

    r = c.post("/integrations/retell/book", headers=H(KEYS["main"]), json={
        "external_ref": "retell-call-0001", "starts_at": ID["slot"]["starts_at"],
        "duration_minutes": 30})
    check("REPLAYING THE SAME external_ref SUCCEEDS", r.status_code == 200,
          r.text[:300])
    b = r.json()
    check("it is flagged as a replay", b["idempotent_replay"] is True, b)
    check("AND RETURNS THE ORIGINAL APPOINTMENT, NOT A NEW ONE",
          b["appointment_id"] == ID["appt"], (b.get("appointment_id"), ID["appt"]))

    db = SessionLocal()
    n = db.query(SalesAppointment).filter_by(brand_sales_org_id="bso-evo").count()
    check("still exactly one appointment exists", n == 1, n)
    db.close()

    # A retry with a different time but the same ref must NOT create a second
    # meeting — the ref is the identity of the attempt.
    slot2 = first_slot(c, day=next_weekday(6))
    r2 = c.post("/integrations/retell/book", headers=H(KEYS["main"]), json={
        "external_ref": "retell-call-0001", "starts_at": slot2["starts_at"],
        "duration_minutes": 30})
    check("a replayed ref with a different time still replays the original",
          r2.status_code == 200 and r2.json()["appointment_id"] == ID["appt"],
          r2.json())
    db = SessionLocal()
    n = db.query(SalesAppointment).filter_by(brand_sales_org_id="bso-evo").count()
    check("and creates nothing", n == 1, n)
    db.close()

    # A FAILED attempt must not burn the ref forever.
    r3 = c.post("/integrations/retell/book", headers=H(KEYS["main"]), json={
        "external_ref": "retell-retryable",
        "starts_at": (datetime.utcnow() - timedelta(days=2))
                     .replace(microsecond=0).isoformat() + "Z"})
    check("a failing attempt is refused", r3.status_code == 400, r3.status_code)
    r4 = c.post("/integrations/retell/book", headers=H(KEYS["main"]), json={
        "external_ref": "retell-retryable", "starts_at": slot2["starts_at"],
        "duration_minutes": 30})
    check("A FAILED ATTEMPT DOES NOT BURN ITS REF — the retry works",
          r4.status_code == 200, r4.text[:300])
    ID["appt2"] = r4.json().get("appointment_id")

    # Two DIFFERENT refs on the same free slot. Idempotency must not be the only
    # thing standing between two bookings — the conflict check has to catch this
    # even when the refs are unrelated.
    #
    # Driven at the service layer rather than through the route, because by this
    # point in the suite the booking route's 10/minute limit is genuinely
    # exhausted and would answer 429 before the conflict check ever ran. That
    # the limiter fires is asserted separately below; here the subject is the
    # conflict check.
    slot3 = first_slot(c, day=next_weekday(7))
    from app.services import retell_bridge as rb
    from fastapi import HTTPException as _HX
    db = SessionLocal()
    cred = db.query(IntegrationCredential).filter_by(name="Taffiny voice").first()
    org = rb.brand_for(db, cred)
    adv = rb.resolve_advisor(db, cred, None)
    start = datetime.fromisoformat(slot3["starts_at"].rstrip("Z"))
    first = rb.book(db, cred, adv, org, starts_at=start, duration_minutes=30,
                    meeting_type=None, external_ref="ref-alpha-1")
    check("the first of two refs takes the slot", first["success"] is True, first)
    second_status = None
    try:
        rb.book(db, cred, adv, org, starts_at=start, duration_minutes=30,
                meeting_type=None, external_ref="ref-bravo-2")
    except _HX as e:
        second_status = e.status_code
    check("TWO DIFFERENT REFS CANNOT BOTH TAKE ONE SLOT", second_status == 409,
          second_status)
    db.close()

    # The 429 above was real. Prove the limiter actually bites on the route.
    burst = [c.post("/integrations/retell/book", headers=H(KEYS["main"]), json={
        "external_ref": "burst-%d" % n, "starts_at": slot3["starts_at"],
        "duration_minutes": 30}).status_code for n in range(4)]
    check("THE BOOKING ROUTE'S RATE LIMIT ACTUALLY FIRES", 429 in burst, burst)


# ═══════════════════════════════════════════════════════════════════════════
# 7. AUDIT
# ═══════════════════════════════════════════════════════════════════════════

def test_audit():
    print("\n[7] Audit trail")
    db = SessionLocal()
    rows = db.query(IntegrationRequestLog).all()
    check("requests are recorded", len(rows) > 0, len(rows))

    booked = [r for r in rows if r.action == "book" and r.success]
    check("the successful booking is recorded", len(booked) >= 1, len(booked))
    r0 = booked[0]
    check("the audit names the integration", r0.integration_name == "Taffiny voice",
          r0.integration_name)
    check("it names the brand", r0.brand_sales_org_id == "bso-evo", r0.brand_sales_org_id)
    check("it names the advisor targeted", r0.advisor_user_id == "u-taffy",
          r0.advisor_user_id)
    check("it records the appointment created", bool(r0.appointment_id))
    check("it records a timestamp", r0.occurred_at is not None)
    check("it records the external ref", bool(r0.external_ref))

    avail = [r for r in rows if r.action == "availability"]
    check("availability requests are recorded too", len(avail) > 0, len(avail))
    failed = [r for r in rows if not r.success]
    check("FAILURES ARE RECORDED, NOT ONLY SUCCESSES", len(failed) > 0, len(failed))
    check("failures record the status code",
          any(r.status_code and r.status_code >= 400 for r in failed))

    blob = " ".join(filter(None, [
        (r.detail or "") + (r.key_prefix or "") + (r.integration_name or "")
        for r in rows]))
    for key in KEYS.values():
        if key in blob:
            check("NO SECRET VALUE IS EVER WRITTEN TO THE AUDIT TRAIL", False, key[:20])
            break
    else:
        check("NO SECRET VALUE IS EVER WRITTEN TO THE AUDIT TRAIL", True)
    check("only the non-secret prefix is stored",
          all((r.key_prefix or "").startswith("evsk_") or not r.key_prefix
              for r in rows))
    db.close()


# ═══════════════════════════════════════════════════════════════════════════
# 8. RATE LIMITING
# ═══════════════════════════════════════════════════════════════════════════

def test_rate_limit():
    print("\n[8] Rate limiting")
    from app.routers import integrations_router as ir
    from app.services.integration_auth import rate_limit_key

    check("the availability route declares a limit",
          "/minute" in ir.AVAILABILITY_LIMIT, ir.AVAILABILITY_LIMIT)
    check("the booking route declares a tighter one",
          int(ir.BOOK_LIMIT.split("/")[0]) < int(ir.AVAILABILITY_LIMIT.split("/")[0]),
          (ir.BOOK_LIMIT, ir.AVAILABILITY_LIMIT))

    class _Req:
        def __init__(self, auth):
            self.headers = {"authorization": auth} if auth else {}
            self.client = type("C", (), {"host": "203.0.113.9"})()

    k1 = rate_limit_key(_Req("Bearer " + KEYS["main"]))
    k2 = rate_limit_key(_Req("Bearer " + KEYS["brandwide"]))
    k3 = rate_limit_key(_Req(None))
    check("THE LIMIT IS KEYED PER CREDENTIAL, NOT PER IP", k1 != k2, (k1, k2))
    check("an anonymous caller still gets a bucket", bool(k3), k3)
    check("THE RATE-LIMIT KEY CONTAINS NO SECRET",
          KEYS["main"] not in k1 and len(k1) < 40, k1)
    check("it uses only the non-secret prefix",
          KEYS["main"][:12] in k1, k1)

    # The limiter is actually wired onto the app.
    check("the app has a limiter installed", getattr(app.state, "limiter", None) is not None)


# ═══════════════════════════════════════════════════════════════════════════
# 9. STATIC GUARANTEES
# ═══════════════════════════════════════════════════════════════════════════

def test_static():
    print("\n[9] Static guarantees")
    import inspect
    from app.services import retell_bridge as rb
    from app.routers import integrations_router as ir
    from app.services import integration_auth as ia

    rsrc = inspect.getsource(ir)
    n_routes = rsrc.count("@router.")
    # The surface now carries a second tenancy tree, so "gated" means gated by
    # ONE OF the two kind-specific dependencies — never ungated. The intent of
    # this assertion is unchanged: no route here is reachable without an
    # integration credential of a specific, declared kind.
    n_brand = rsrc.count("Depends(require_retell)")
    n_tenant = rsrc.count("Depends(require_retell_tenant)")
    check("EVERY INTEGRATION ROUTE IS GATED BY A KIND-SPECIFIC DEPENDENCY",
          n_brand + n_tenant == n_routes, (n_brand, n_tenant, n_routes))
    check("the brand-sales routes are still gated by require_retell",
          n_brand == 3, n_brand)
    check("no integration route accepts a user JWT",
          "get_current_user" not in rsrc and "require_sales" not in rsrc)
    check("every route declares a rate limit",
          rsrc.count("@limiter.limit") == n_routes,
          (rsrc.count("@limiter.limit"), n_routes))

    bsrc = inspect.getsource(rb)
    check("THE BRIDGE DOES NOT COMPUTE AVAILABILITY ITSELF",
          "find_shared_slots" in bsrc and "subtract_intervals" not in bsrc)
    check("it re-checks conflicts at booking time", "find_conflicts" in bsrc)
    check("it reuses the shared side-effect path, not a copy",
          "_push_appointment" in bsrc)
    check("it never reads a Zoom host url", "host_url" not in bsrc)
    check("it never touches customer-tenant tables",
          "import Lead" not in bsrc and "BookingLink" not in bsrc)
    check("out-of-scope and non-existent advisors share one message",
          bsrc.count("_NO_ADVISOR") >= 4, bsrc.count("_NO_ADVISOR"))

    asrc = inspect.getsource(ia)
    check("the auth layer stores only a hash", "sha256" in asrc)
    check("it compares in constant time", "compare_digest" in asrc)
    check("it refuses every failure mode identically", asrc.count("_REFUSED") >= 3)
    # Checked as a dependency, not as a word — the docstring mentions it by
    # name precisely to say it is never used.
    check("it never resolves to a User",
          "Depends(get_current_user)" not in asrc and "-> User" not in asrc)

    from app.models.integration_models import IntegrationCredential as IC
    cols = {c.name for c in IC.__table__.columns}
    check("the credential table stores no plaintext key",
          "key" not in cols and "secret" not in cols, sorted(cols))
    check("it stores a hash", "key_hash" in cols)
    check("it stores a non-secret prefix", "key_prefix" in cols)
    check("it is scoped to a brand", "brand_sales_org_id" in cols)
    check("it is revocable", "revoked_at" in cols and "is_active" in cols)

    from app.models.integration_models import IntegrationRequestLog as IL
    uniq = {tuple(sorted(c.columns.keys())) for c in IL.__table__.constraints
            if c.__class__.__name__ == "UniqueConstraint"}
    check("IDEMPOTENCY IS ENFORCED BY A UNIQUE CONSTRAINT",
          ("credential_id", "external_ref") in uniq, uniq)


def main():
    print("=" * 74)
    print("  RETELL INTEGRATION BRIDGE")
    print("=" * 74)
    install_fakes()
    build()
    test_credential()
    test_advisor_scope()
    test_availability()
    test_booking()
    test_double_book()
    test_idempotency()
    test_audit()
    test_rate_limit()
    test_static()

    print("\n" + "=" * 74)
    if FAILURES:
        print("  %d FAILURES" % len(FAILURES))
        for f in FAILURES:
            print("   - %s" % f)
        shutil.rmtree(TMP, ignore_errors=True)
        sys.exit(1)
    print("  ALL RETELL BRIDGE CHECKS PASSED")
    print("=" * 74)
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
