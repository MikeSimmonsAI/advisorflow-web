"""Checkpoint 6 §30 — legacy availability hardening.

TWO PRODUCTION FAIL-OPEN BUGS AND ONE ANONYMOUS ENDPOINT.

A. `GET /calendar/slots` "checked" Google by importing
   `calendar_service._get_google_credentials`, which has never existed. The
   ImportError was swallowed and the candidate slot list left UNTOUCHED, so a
   Google-connected advisor with a full calendar was offered to the public at
   every slot. The Microsoft branch had the identical shape and the identical
   consequence on a token-refresh failure.

B. `GET /availability/slots/{advisor_id}` was anonymous.

`test_google_fail_closed_regression` and `test_provider_error_fails_closed` are
written so they would FAIL against the old implementation: the old code returned
a full day of slots in exactly the situations they assert must return none.

NO TEST CONTACTS GOOGLE, MICROSOFT OR ANY OTHER VENDOR. Every calendar read goes
through a fake registered in the existing `calendar_providers` registry, which is
the seam the tenant bridge and sales scheduler already use.

Temp SQLite. Never touches production.

    python scripts/smoke_legacy_hardening.py
"""
import os
import sys
import shutil
import tempfile
from datetime import datetime, timedelta, date as date_cls

TMP = tempfile.mkdtemp(prefix="legacyhard_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "smoke" + "0" * 59
os.environ["SECRET_KEY"] = "smoke" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                          # noqa: E402
from app.main import app                                           # noqa: E402
from app.deps import SessionLocal, engine                          # noqa: E402
from app.models.models import (                                    # noqa: E402
    Base, Platform, Organization, User, BookingLink, Lead,
)
from app.services.auth_service import hash_password                # noqa: E402
from app.services import calendar_providers as reg                 # noqa: E402
from app.services.calendar_providers.base import (                 # noqa: E402
    CalendarProvider, BusyInterval,
)

PW = "SmokeTest!2026"
FAILURES = []


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("\n         -> " + str(detail)[:400]) if not ok else ""))
    if not ok:
        FAILURES.append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 64 - len(t)))


def read_src(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


def token_for(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


# ── fakes ───────────────────────────────────────────────────────────────────

class FakeBusyProvider(CalendarProvider):
    """A calendar we CAN read. Returns whatever the test set."""
    key = "google"
    intervals = []

    def is_ready(self):
        return True, None

    def get_busy(self, start_utc, end_utc):
        return list(FakeBusyProvider.intervals), None


class FakeBrokenProvider(CalendarProvider):
    """A calendar we CANNOT read — the shape of an expired token or an outage.

    Returns an error, exactly as the real providers do. This is the case the old
    code turned into 'completely free'.
    """
    key = "google"

    def is_ready(self):
        return True, None

    def get_busy(self, start_utc, end_utc):
        class _Err:
            error_code = "auth_expired"
        return [], _Err()


class FakeRaisingProvider(CalendarProvider):
    """A provider that breaks its own contract and raises.

    The registry promises providers never raise. If one ever does, that is still
    a calendar we could not read, and it must not become an open day.
    """
    key = "google"

    def is_ready(self):
        return True, None

    def get_busy(self, start_utc, end_utc):
        raise RuntimeError("boom")


def _next_weekday(base=None, ahead=2):
    d = (base or date_cls.today()) + timedelta(days=ahead)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _utc_for_local(d, hour, minute=0, tzname="America/Chicago"):
    """The UTC instant for a local wall time on `d`.

    Computed rather than assumed: Chicago is UTC-5 or UTC-6 depending on the
    date, and a test that hardcodes one of them passes for half the year.
    """
    from zoneinfo import ZoneInfo
    from datetime import timezone
    local = datetime(d.year, d.month, d.day, hour, minute, tzinfo=ZoneInfo(tzname))
    return local.astimezone(timezone.utc).replace(tzinfo=None)


# ── fixture ─────────────────────────────────────────────────────────────────

def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add_all([
        Platform(id="plt-a", name="Brand A", slug="brand-a"),
        Platform(id="plt-b", name="Brand B", slug="brand-b"),
    ])
    db.flush()
    db.add_all([
        Organization(id="org-a", name="Chapel A", slug="chapel-a", platform_id="plt-a"),
        Organization(id="org-b", name="Chapel B", slug="chapel-b", platform_id="plt-b"),
    ])
    db.flush()

    def mk(uid, email, org, role="advisor", google=False):
        db.add(User(
            id=uid, organization_id=org, email=email, full_name=uid,
            password_hash=hash_password(PW), role=role,
            must_change_password=False, is_active=True,
            google_calendar_connected=google,
            google_oauth_refresh_token_encrypted=("enc-token" if google else None),
        ))

    mk("adv-a", "adva@example.test", "org-a", google=True)
    mk("adv-a2", "adva2@example.test", "org-a")
    mk("adv-b", "advb@example.test", "org-b")
    mk("admin-a", "admina@example.test", "org-a", role="org_admin")
    mk("god", "god@example.test", None, role="god_admin")
    mk("brandsales", "bs@example.test", None)      # organization_id IS NULL
    db.flush()

    # BookingLink.lead_id is NOT NULL, so the booking token needs a family
    # behind it — which is also the shape the real public flow has.
    db.add_all([
        Lead(id="lead-a", organization_id="org-a", first_name="Pat",
             last_name="Family", phone="+15125550101"),
        Lead(id="lead-a2", organization_id="org-a", first_name="Sam",
             last_name="Family", phone="+15125550102"),
    ])
    db.flush()

    d = _next_weekday()
    db.add(BookingLink(id="bl-1", token="tok-adv-a", user_id="adv-a",
                       status="pending", lead_id="lead-a"))
    db.commit()
    db.close()
    return d


# ── A. Google fail-closed ───────────────────────────────────────────────────

def test_google_busy_blocks_slots(c, day):
    section("Google busy blocks the slot (§30 A)")
    reg.reset_providers()
    FakeBusyProvider.intervals = [
        BusyInterval(starts_at=_utc_for_local(day, 10, 0),
                     ends_at=_utc_for_local(day, 11, 0)),
    ]
    reg.register_provider("google", lambda u, conn, org: FakeBusyProvider(u, conn, org))
    try:
        r = c.get("/calendar/slots", params={"advisor_id": "adv-a",
                                             "date": day.isoformat(),
                                             "token": "tok-adv-a"})
        check("the booking page answers", r.status_code == 200, r.text[:200])
        slots = r.json().get("slots", [])
        check("some slots are still offered", len(slots) > 0, slots[:3])
        check("10:00 is NOT offered — Google says busy",
              not any(s.endswith("T10:00:00") for s in slots), slots)
        check("10:30 is NOT offered — the 10-11 block covers it",
              not any(s.endswith("T10:30:00") for s in slots), slots)
        check("09:00 IS offered — genuinely free",
              any(s.endswith("T09:00:00") for s in slots), slots)
        check("13:00 IS offered — after the busy period",
              any(s.endswith("T13:00:00") for s in slots), slots)
    finally:
        reg.reset_providers()


def test_google_fail_closed_regression(c, day):
    """WOULD HAVE FAILED against the old implementation.

    Old behaviour: ImportError on `_get_google_credentials` → swallowed →
    `available` untouched → a full day of slots. This asserts zero.
    """
    section("Google UNREADABLE returns no slots (§30 D — the regression)")
    reg.reset_providers()
    reg.register_provider("google", lambda u, conn, org: FakeBrokenProvider(u, conn, org))
    try:
        r = c.get("/calendar/slots", params={"advisor_id": "adv-a",
                                             "date": day.isoformat(),
                                             "token": "tok-adv-a"})
        check("still a 200, not a 500", r.status_code == 200, r.text[:200])
        body = r.json()
        check("NO SLOTS ARE OFFERED", body.get("slots") == [], body.get("slots"))
        check("a human-readable reason is returned", bool(body.get("reason")), body)
        check("the machine-readable cause is carried",
              body.get("calendar_error") == "auth_expired", body.get("calendar_error"))
        check("the reason names no credential and no vendor internals",
              "token" not in (body.get("reason") or "").lower(), body.get("reason"))
    finally:
        reg.reset_providers()


def test_provider_error_fails_closed(c, day):
    section("A provider that RAISES still fails closed")
    reg.reset_providers()
    reg.register_provider("google", lambda u, conn, org: FakeRaisingProvider(u, conn, org))
    try:
        r = c.get("/calendar/slots", params={"advisor_id": "adv-a",
                                             "date": day.isoformat(),
                                             "token": "tok-adv-a"})
        check("still a 200", r.status_code == 200, r.text[:200])
        check("NO SLOTS ARE OFFERED", r.json().get("slots") == [], r.json())
    finally:
        reg.reset_providers()


def test_no_calendar_is_not_an_error(c, day):
    section("No calendar connected is a legitimate state, not a failure")
    reg.reset_providers()
    db = SessionLocal()
    db.add(BookingLink(id="bl-2", token="tok-adv-a2", user_id="adv-a2",
                       status="pending", lead_id="lead-a2"))
    db.commit()
    db.close()
    r = c.get("/calendar/slots", params={"advisor_id": "adv-a2",
                                         "date": day.isoformat(),
                                         "token": "tok-adv-a2"})
    check("the day is offered normally", r.status_code == 200 and
          len(r.json().get("slots", [])) > 0, r.text[:200])
    check("no calendar_error is reported",
          r.json().get("calendar_error") is None, r.json())


def test_static_dead_import_is_gone():
    section("The dead import is gone from the code (§30 A)")
    src = read_src("app/routers/calendar_router.py")
    code = src
    # Strip comments so the explanatory paragraph naming the old bug does not
    # look like the bug itself.
    code_lines = [l for l in code.splitlines() if not l.strip().startswith("#")]
    code = "\n".join(code_lines)
    check("no import of _get_google_credentials remains",
          "_get_google_credentials" not in code)
    check("no googleapiclient import remains in the booking route",
          "googleapiclient" not in code)
    check("the route no longer builds its own Google service",
          'build("calendar", "v3"' not in code)
    check("the read goes through the tested registry",
          "external_busy" in code and "_booking_window_busy" in code)
    check("the failure path returns early rather than falling through",
          "calendar_error" in code)


# ── B. authentication ───────────────────────────────────────────────────────

def test_availability_slots_auth(c):
    section("GET /availability/slots/{advisor_id} is authenticated (§30 B)")
    r = c.get("/availability/slots/adv-a")
    check("anonymous is refused", r.status_code == 401,
          "%s %s" % (r.status_code, r.text[:150]))
    check("the refusal leaks nothing about the advisor",
          "adv-a" not in r.text and "Chapel" not in r.text, r.text[:150])

    r = c.get("/availability/slots/adv-a",
              headers={"Authorization": "Bearer not-a-real-token"})
    check("a bogus token is refused", r.status_code == 401, r.status_code)

    own = token_for(c, "adva@example.test")
    r = c.get("/availability/slots/adv-a", headers=own)
    check("the advisor reads their own slots", r.status_code == 200, r.text[:200])
    check("the payload shape is unchanged",
          "slots" in r.json() and "advisor_name" in r.json(), list(r.json()))

    # This endpoint renders a human label, and until Checkpoint 6 nothing had
    # ever called it successfully in a test on Windows — because it was
    # anonymous, so no suite had a reason to. It used strftime's %-I, a glibc
    # extension that raises ValueError on Windows. It worked on Render and
    # crashed on the machine it is developed on.
    rows = r.json().get("slots", [])
    if rows:
        lab = rows[0].get("label", "")
        check("the slot label renders on this platform", bool(lab), rows[0])
        check("the label has no zero-padded hour",
              " 0" not in lab.split(" at ")[-1], lab)
        check("the label reads like a person wrote it",
              " at " in lab and (":" in lab) and lab.split()[-1] in ("AM", "PM"), lab)
    else:
        check("the advisor has slots to label", False, "no slots returned")

    import re as _re
    src = read_src("app/routers/availability_router.py")
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith(("#", '"""', "*")))
    check("no glibc-only strftime code remains in availability_router",
          not _re.search(r"strftime\([^)]*%-", code),
          _re.findall(r"strftime\([^)]*%-[^)]*\)", code)[:2])

    same_org = token_for(c, "adva2@example.test")
    r = c.get("/availability/slots/adv-a", headers=same_org)
    check("a colleague in the same organisation is allowed",
          r.status_code == 200, r.text[:200])

    admin = token_for(c, "admina@example.test")
    r = c.get("/availability/slots/adv-a", headers=admin)
    check("their org_admin is allowed", r.status_code == 200, r.text[:200])

    god = token_for(c, "god@example.test")
    r = c.get("/availability/slots/adv-a", headers=god)
    check("god is allowed", r.status_code == 200, r.text[:200])


def test_availability_cross_tenant(c):
    section("Cross-tenant probing fails closed with no leakage (§30 B)")
    other = token_for(c, "advb@example.test")
    r = c.get("/availability/slots/adv-a", headers=other)
    check("another tenant's advisor is refused", r.status_code == 404,
          "%s %s" % (r.status_code, r.text[:150]))

    r_missing = c.get("/availability/slots/does-not-exist-at-all", headers=other)
    check("a non-existent id gives the SAME status", r_missing.status_code == 404)
    check("and the SAME body — no existence oracle",
          r.text == r_missing.text, (r.text[:120], r_missing.text[:120]))

    check("no advisor name leaks in the refusal",
          "adv-a" not in r.text and "Chapel A" not in r.text, r.text[:200])

    bs = token_for(c, "bs@example.test")
    r = c.get("/availability/slots/adv-a", headers=bs)
    check("a brand-sales identity gets nothing from a tenant",
          r.status_code == 404, "%s %s" % (r.status_code, r.text[:150]))


# ── C. the public Vercel booking flow ───────────────────────────────────────

def test_vercel_booking_flow_unaffected(c, day):
    section("The public Vercel booking flow is untouched (§30 C)")
    paths = sorted({getattr(r, "path", "") for r in app.routes})
    for p in ("/calendar/booking/{token}", "/calendar/slots",
              "/calendar/booking-confirmed"):
        check("still mounted: %s" % p, p in paths, "MISSING")

    # Every one of these must work with NO Authorization header at all.
    r = c.get("/calendar/booking/tok-adv-a")
    check("booking token lookup is still anonymous",
          r.status_code in (200, 404, 410), "%s %s" % (r.status_code, r.text[:150]))

    reg.reset_providers()
    reg.register_provider("google", lambda u, conn, org: FakeBusyProvider(u, conn, org))
    FakeBusyProvider.intervals = []
    try:
        r = c.get("/calendar/slots", params={"advisor_id": "adv-a",
                                             "date": day.isoformat(),
                                             "token": "tok-adv-a"})
        check("slot lookup is still anonymous and still works",
              r.status_code == 200 and len(r.json().get("slots", [])) > 0,
              r.text[:200])
    finally:
        reg.reset_providers()

    # The token security model is unchanged.
    r = c.get("/calendar/slots", params={"advisor_id": "adv-a", "date": day.isoformat(),
                                         "token": "not-a-real-token"})
    check("a bad booking token is still rejected", r.status_code == 404, r.status_code)
    r = c.get("/calendar/slots", params={"advisor_id": "adv-b", "date": day.isoformat(),
                                         "token": "tok-adv-a"})
    check("a token for another advisor is still rejected",
          r.status_code == 403, r.status_code)

    src = read_src("app/routers/calendar_router.py")
    check("the slots route still takes no current_user dependency",
          "def get_available_slots(\n    advisor_id: str = Query(...)" in src
          or "advisor_id: str = Query(...)" in src)
    check("get_current_user was NOT added to the public booking route",
          "current_user" not in src.split('@router.get("/slots")')[1].split("@router.")[0])


def main():
    print("=" * 72)
    print("CHECKPOINT 6 §30 — LEGACY AVAILABILITY HARDENING")
    print("=" * 72)
    day = build()
    print("\nUsing booking date %s (a weekday)" % day.isoformat())
    with TestClient(app) as c:
        test_google_busy_blocks_slots(c, day)
        test_google_fail_closed_regression(c, day)
        test_provider_error_fails_closed(c, day)
        test_no_calendar_is_not_an_error(c, day)
        test_static_dead_import_is_gone()
        test_availability_slots_auth(c)
        test_availability_cross_tenant(c)
        test_vercel_booking_flow_unaffected(c, day)

    print("\n" + "=" * 72)
    if FAILURES:
        print("FAILED (%d):" % len(FAILURES))
        for f in FAILURES:
            print("  - %s" % f)
        shutil.rmtree(TMP, ignore_errors=True)
        sys.exit(1)
    print("ALL LEGACY HARDENING CHECKS PASSED")
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
