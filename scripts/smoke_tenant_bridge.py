"""Tenant-side Retell bridge regression suite.

The CUSTOMER TENANT half: a funeral home's own advisors, their own settings,
their own leads, their own external calendars. Covers tenant-scoped service
auth, cross-tenant denial, Google-calendar availability, fail-closed behaviour
when a calendar cannot be read, booking re-validation, double-book refusal,
idempotent retry, lead association, and what must never leak.

NO TEST CONTACTS GOOGLE, MICROSOFT, TWILIO OR AN EMAIL PROVIDER. Every external
edge is a fake registered through the existing provider registry or
monkeypatched at the module boundary. There is no path in here that can reach a
real family or a real vendor.

Temp SQLite. Never touches production.

    python scripts/smoke_tenant_bridge.py
"""
import os
import sys
import json
import shutil
import tempfile
from datetime import datetime, timedelta, date as date_cls, timezone as _tz

TMP = tempfile.mkdtemp(prefix="tenantbridge_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "smoke" + "0" * 59
os.environ["SECRET_KEY"] = "smoke" + "0" * 59

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient                          # noqa: E402
from sqlalchemy import text                                        # noqa: E402
from app.main import app                                           # noqa: E402
from app.deps import SessionLocal, engine                          # noqa: E402
from app.models.models import (                                    # noqa: E402
    Base, Platform, Organization, User, Lead, BookingLink,
    AdvisorAvailabilityBlock, BlockType,
)
from app.models.sales_models import BrandSalesOrg                  # noqa: E402
from app.models.integration_models import (                        # noqa: E402
    IntegrationCredential, IntegrationRequestLog,
    INTEGRATION_RETELL, INTEGRATION_RETELL_TENANT,
)
from app.services.auth_service import hash_password                # noqa: E402
from app.services.integration_auth import generate_key             # noqa: E402
from app.services import tenant_scheduling as ts                   # noqa: E402

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


def code_only(src):
    """Source with docstrings and comments removed.

    A static assertion like "this module never touches X" must be about what
    the code does, not about whether the word appears in a paragraph explaining
    why it deliberately does not.
    """
    import ast
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc and node.body and isinstance(node.body[0], ast.Expr):
                node.body[0].value = ast.Constant(value="")
    # A bare string expression that is not a docstring (the router has one)
    # is blanked too; nothing in these modules relies on such a value.
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            node.value = ast.Constant(value="")
    return ast.unparse(ast.fix_missing_locations(tree))


def utc_for(local_naive, tz_name=CHI):
    """Local wall time -> the naive-UTC 'Z' string the book route expects."""
    return ts._to_utc(local_naive, tz_name).replace(microsecond=0).isoformat() + "Z"


# ── fakes: nothing leaves this process ──────────────────────────────────────

class FakeCal(object):
    """One fake standing in for every external calendar.

    `busy` is a list of (start_utc, end_utc). `error` makes every read fail,
    which is how the fail-closed assertions are driven.
    """
    key = "ics"
    busy = []
    error = None
    created = []

    def __init__(self, user=None, connection=None, org=None):
        self.user = user

    def is_ready(self):
        return True, None

    def create_event(self, payload):
        from app.services.calendar_providers.base import SyncResult
        FakeCal.created.append({
            "subject": payload.subject,
            "location": payload.location,
            "starts_at": payload.starts_at,
            "timezone": payload.timezone,
        })
        return SyncResult(ok=True, external_event_id="ext-tenant-1")

    def update_event(self, eid, payload):
        from app.services.calendar_providers.base import SyncResult
        return SyncResult(ok=True, external_event_id=eid)

    def cancel_event(self, eid, payload=None):
        from app.services.calendar_providers.base import SyncResult
        return SyncResult(ok=True, external_event_id=eid)

    def get_busy(self, start, end):
        from app.services.calendar_providers.base import SyncResult, BusyInterval
        if FakeCal.error:
            return [], SyncResult.failure(FakeCal.error, "fake calendar is down")
        out = []
        for s, e in FakeCal.busy:
            if s < end and e > start:
                out.append(BusyInterval(starts_at=s, ends_at=e))
        return out, None


SMS = []
MAIL = []


def install_fakes():
    from app.services import calendar_providers as cp
    cp.register_provider("google", FakeCal)
    cp.register_provider("microsoft", FakeCal)
    cp.register_provider("ics", FakeCal)

    # The confirmation flow. Patched at the module boundary so no Twilio client
    # is constructed and no email provider is called, while still proving the
    # real `on_booking_confirmed` ran.
    from app.services import appointment_flow_service as afs
    afs._send_sms_safe = lambda advisor, to, body: SMS.append({"to": to, "body": body})
    afs._send_email_safe = lambda to, subj, html: MAIL.append({"to": to, "subject": subj})


# ── fixture ────────────────────────────────────────────────────────────────

def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    # The advisor-visible Client Record table is raw SQL in production (created
    # outside the models). Create a compatible one here so the happy path can be
    # asserted rather than assumed.
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS appointment_case_files (
            id VARCHAR PRIMARY KEY, lead_id VARCHAR, organization_id VARCHAR,
            recorded_by_id VARCHAR, booking_link_id VARCHAR,
            appointment_date TIMESTAMP, appointment_type VARCHAR,
            outcome_type VARCHAR, products_discussed TEXT, products_sold TEXT,
            chk_id_verified BOOLEAN, chk_beneficiary_named BOOLEAN,
            chk_app_signed BOOLEAN, chk_payment_collected BOOLEAN,
            chk_illustrations_reviewed BOOLEAN, chk_medical_history BOOLEAN,
            chk_hipaa_signed BOOLEAN, chk_replacement_form BOOLEAN,
            chk_beneficiary_reviewed BOOLEAN, chk_riders_explained BOOLEAN,
            referral_potential BOOLEAN, case_status VARCHAR,
            created_at TIMESTAMP, updated_at TIMESTAMP
        )
    """))

    db.add(Platform(id="plt-bb", name="BookaBoost", slug="bookaboost"))
    db.flush()

    # TWO funeral homes. Everything cross-tenant is asserted against the second.
    db.add_all([
        Organization(id="org-rest", name="Restland Memorial", slug="restland",
                     platform_id="plt-bb", is_active=True,
                     org_address="9800 Restland Rd, Dallas, TX 75243",
                     appointment_types=json.dumps(
                         ["Family File Review", "Grave Marker Consultation"])),
        Organization(id="org-other", name="Elsewhere Funeral Home", slug="elsewhere",
                     platform_id="plt-bb", is_active=True,
                     org_address="1 Somewhere St"),
        # A tenant that has configured NO appointment types, to prove a new
        # customer is not blocked by an empty settings page.
        Organization(id="org-blank", name="Blank Chapel", slug="blank",
                     platform_id="plt-bb", is_active=True),
    ])
    # A brand-sales org, so the "a tenant key cannot reach brand sales" and
    # "a brand key cannot reach a tenant" assertions have both sides present.
    db.add(BrandSalesOrg(id="bso-evo", platform_id="plt-bb",
                         name="EvoSys Pro Sales", slug="evosyspro-sales",
                         timezone=CHI))
    db.flush()

    def mk(uid, org, email, name, active=True, google=False, microsoft=False,
           start="09:00", end="17:00", days="0,1,2,3,4", buf=0, cap=8, tz=CHI):
        u = User(id=uid, organization_id=org, email=email, full_name=name,
                 password_hash=hash_password("x" * 12), role="advisor",
                 is_active=active,
                 available_start_time=start, available_end_time=end,
                 available_days=days, buffer_minutes=buf,
                 max_bookings_per_day=cap, booking_timezone=tz)
        if google:
            u.google_oauth_refresh_token_encrypted = "enc-google-refresh-token"
            u.google_calendar_connected = True
            u.google_calendar_id = "primary"
        if microsoft:
            u.microsoft_oauth_refresh_token_encrypted = "enc-ms-refresh-token"
            u.microsoft_365_connected = True
        db.add(u)
        return u

    # THE TAFFINY ADVISOR: Restland, Google Calendar, 10-4, Mon-Fri, 15m buffer.
    mk("u-google", "org-rest", "gadvisor@restland.test", "Grace Alvarez",
       google=True, start="10:00", end="16:00", buf=15, cap=3)
    mk("u-nocal", "org-rest", "nocal@restland.test", "Noel Cortez")
    mk("u-inactive", "org-rest", "gone@restland.test", "Gone Away", active=False)
    # Same platform, DIFFERENT funeral home. Must be unreachable.
    mk("u-other", "org-other", "other@elsewhere.test", "Otto Herrera", google=True)
    mk("u-blank", "org-blank", "blank@blank.test", "Blanche Kim")
    # Brand-sales staff: organization_id IS NULL by design.
    mk("u-sales", None, "sales@evosyspro.test", "Sam Ellis")
    db.flush()

    def key(name, kind, org=None, brand=None, advisor=None, allow=None, rate=60):
        full, prefix, hashed = generate_key()
        db.add(IntegrationCredential(
            name=name, kind=kind, key_prefix=prefix, key_hash=hashed,
            brand_sales_org_id=brand, organization_id=org,
            default_advisor_user_id=advisor, allowed_advisor_ids=allow,
            rate_limit_per_minute=rate, is_active=True,
            created_at=datetime.utcnow()))
        KEYS[name] = full
        return full

    key("taffiny", INTEGRATION_RETELL_TENANT, org="org-rest", advisor="u-google")
    # One key per section. The rate limit is per credential and is real — a
    # single key would exhaust its own bucket partway through the suite and
    # report 429s as if they were logic failures. Sharing the same tenant and
    # advisor keeps every section testing the same thing.
    for n in range(4, 13):
        key("s%d" % n, INTEGRATION_RETELL_TENANT, org="org-rest",
            advisor="u-google")
    key("restland-nocal", INTEGRATION_RETELL_TENANT, org="org-rest", advisor="u-nocal")
    key("blank", INTEGRATION_RETELL_TENANT, org="org-blank", advisor="u-blank")
    key("elsewhere", INTEGRATION_RETELL_TENANT, org="org-other", advisor="u-other")
    key("restricted", INTEGRATION_RETELL_TENANT, org="org-rest",
        advisor="u-google", allow="u-google")
    key("brandkey", INTEGRATION_RETELL, brand="bso-evo", advisor="u-sales")

    # A deliberately broken row: scoped to BOTH trees. Must be refused, not
    # resolved to whichever column is read first.
    full, prefix, hashed = generate_key()
    db.add(IntegrationCredential(
        name="both-scopes", kind=INTEGRATION_RETELL_TENANT,
        key_prefix=prefix, key_hash=hashed,
        brand_sales_org_id="bso-evo", organization_id="org-rest",
        rate_limit_per_minute=60, is_active=True, created_at=datetime.utcnow()))
    KEYS["both-scopes"] = full

    # Revoked and inactive keys.
    full, prefix, hashed = generate_key()
    db.add(IntegrationCredential(
        name="revoked", kind=INTEGRATION_RETELL_TENANT,
        key_prefix=prefix, key_hash=hashed, organization_id="org-rest",
        default_advisor_user_id="u-google", rate_limit_per_minute=60,
        is_active=False, revoked_at=datetime.utcnow(),
        created_at=datetime.utcnow()))
    KEYS["revoked"] = full

    # A family already known to Restland, for the lead-reuse assertion.
    db.add(Lead(id="lead-known", organization_id="org-rest",
                first_name="Dana", last_name="Whitfield",
                phone="+15555550123", email="dana@example.test", status="new"))
    # A family belonging to the OTHER funeral home.
    db.add(Lead(id="lead-foreign", organization_id="org-other",
                first_name="Foreign", last_name="Family",
                phone="+15555559999", status="new"))

    db.commit()
    db.close()


def next_weekday(offset=3):
    """The Nth BUSINESS day from today — distinct for every distinct N.

    Counting calendar days and then skipping the weekend is not enough: +11 and
    +13 both land on the same Monday roughly two days in seven, and two sections
    sharing a day share the advisor's daily booking cap. That produced a
    capacity refusal masquerading as a logic failure, which is exactly the kind
    of flake that gets a real assertion deleted later.
    """
    d = date_cls.today()
    for _ in range(max(1, offset)):
        d += timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
    return d


# ── 1. service credential ───────────────────────────────────────────────────

def s1_auth(c):
    print("\n[1] Tenant service credential")
    PING = "/integrations/retell/tenant/ping"

    r = c.get(PING)
    check("no credential is refused", r.status_code == 401, r.text)
    check("the refusal names no reason a prober could use",
          r.json().get("detail") == "Invalid or missing integration credential.",
          r.text)

    codes = set()
    for bad in ("", "not-a-key", "evsk_" + "z" * 40, "Bearer", "evsk_short"):
        codes.add(c.get(PING, headers={"Authorization": "Bearer " + bad}).status_code)
    codes.add(c.get(PING, headers={"Authorization": "Basic abc"}).status_code)
    codes.add(c.get(PING, headers={"Authorization": KEYS["taffiny"]}).status_code)  # no scheme
    check("every malformed credential fails identically", codes == {401}, codes)

    r = c.get(PING, headers=H(KEYS["revoked"]))
    check("a REVOKED key is refused", r.status_code == 401, r.text)
    check("revoked is indistinguishable from unknown",
          r.json().get("detail") == "Invalid or missing integration credential.")

    r = c.get(PING, headers=H(KEYS["both-scopes"]))
    check("A KEY SCOPED TO BOTH TREES IS REFUSED, NOT RESOLVED",
          r.status_code == 401, r.text)

    r = c.get(PING, headers=H(KEYS["taffiny"]))
    check("a valid tenant key works", r.status_code == 200, r.text)
    d = r.json()
    check("ping names the tenant", d.get("organization") == "Restland Memorial", d)
    check("ping names the default advisor",
          d.get("default_advisor_name") == "Grace Alvarez", d)
    check("PING REPORTS WHICH CALENDAR AVAILABILITY IS READ FROM",
          d.get("advisor_calendar") == "google", d)
    check("ping reports the advisor's own timezone",
          d.get("advisor_timezone") == CHI, d)
    check("ping lists the tenant's configured appointment types",
          d.get("appointment_types") == ["Family File Review",
                                         "Grave Marker Consultation"], d)

    r = c.get(PING, headers=H(KEYS["restland-nocal"]))
    check("an advisor with no external calendar is reported as such",
          r.json().get("advisor_calendar") == "none", r.text)

    check("ping exposes no secret",
          "evsk_" not in r.text and "refresh" not in r.text.lower(), r.text[:200])


# ── 2. the two trees do not meet ────────────────────────────────────────────

def s2_trees(c):
    print("\n[2] Tenant and brand keys cannot cross")
    day = next_weekday().isoformat()

    r = c.get("/integrations/retell/tenant/ping", headers=H(KEYS["brandkey"]))
    check("A BRAND KEY CANNOT OPEN A TENANT ROUTE", r.status_code == 401, r.text)
    r = c.post("/integrations/retell/tenant/availability",
               headers=H(KEYS["brandkey"]), json={"date_from": day})
    check("a brand key cannot read tenant availability", r.status_code == 401, r.text)

    r = c.get("/integrations/retell/ping", headers=H(KEYS["taffiny"]))
    check("A TENANT KEY CANNOT OPEN A BRAND ROUTE", r.status_code == 401, r.text)
    r = c.post("/integrations/retell/availability",
               headers=H(KEYS["taffiny"]), json={"date_from": day})
    check("A TENANT KEY CANNOT REACH BRAND-SALES SCHEDULING",
          r.status_code == 401, r.text)

    check("both refusals are the same opaque message",
          r.json().get("detail") == "Invalid or missing integration credential.")


# ── 3. advisor scoping ──────────────────────────────────────────────────────

def s3_advisor(c):
    print("\n[3] Advisor resolution and the tenant boundary")
    AV = "/integrations/retell/tenant/availability"
    day = next_weekday().isoformat()

    r = c.post(AV, headers=H(KEYS["taffiny"]),
               json={"date_from": day, "advisor_id": "u-google"})
    check("an advisor in this tenant resolves", r.status_code == 200, r.text)

    r = c.post(AV, headers=H(KEYS["taffiny"]),
               json={"date_from": day, "advisor_id": "u-other"})
    check("AN ADVISOR IN ANOTHER FUNERAL HOME IS REFUSED",
          r.status_code == 404, r.text)
    other = r.json().get("detail")

    r = c.post(AV, headers=H(KEYS["taffiny"]),
               json={"date_from": day, "advisor_id": "u-sales"})
    check("a brand-sales user is not reachable as a tenant advisor",
          r.status_code == 404, r.text)

    r = c.post(AV, headers=H(KEYS["taffiny"]),
               json={"date_from": day, "advisor_id": "u-inactive"})
    check("an inactive advisor is refused", r.status_code == 404, r.text)
    inactive = r.json().get("detail")

    r = c.post(AV, headers=H(KEYS["taffiny"]),
               json={"date_from": day, "advisor_id": "does-not-exist"})
    absent = r.json().get("detail")

    r = c.post(AV, headers=H(KEYS["restricted"]),
               json={"date_from": day, "advisor_id": "u-nocal"})
    check("an advisor off this key's allowlist is refused", r.status_code == 404, r.text)
    off_list = r.json().get("detail")

    check("CROSS-TENANT ENUMERATION IS IMPOSSIBLE — every refusal is identical",
          other == inactive == absent == off_list == "Advisor not found.",
          [other, inactive, absent, off_list])

    r = c.post(AV, headers=H(KEYS["taffiny"]), json={"date_from": day})
    check("omitting advisor_id uses the credential's own default",
          r.status_code == 200 and r.json().get("advisor_id") == "u-google", r.text)


# ── 4. availability honours the advisor's real settings ─────────────────────

def s4_settings(c):
    print("\n[4] The advisor's own settings are what is honoured")
    AV = "/integrations/retell/tenant/availability"
    FakeCal.busy = []
    FakeCal.error = None
    day = next_weekday(4)

    r = c.post(AV, headers=H(KEYS["s4"]),
               json={"date_from": day.isoformat(), "duration_minutes": 60})
    check("availability returns 200", r.status_code == 200, r.text)
    d = r.json()
    check("slots are returned", d["slot_count"] > 0, d)

    hours = sorted({s["starts_at_local"][11:16] for s in d["slots"]})
    check("THE ADVISOR'S 10:00 START IS HONOURED, NOT A HARDCODED 09:00",
          hours[0] == "10:00", hours)
    check("the advisor's 16:00 end is honoured — no slot starts at or after it",
          all(h < "16:00" for h in hours), hours)

    check("every slot carries an explicit UTC instant",
          all(s["starts_at"].endswith("Z") for s in d["slots"]), d["slots"][:2])
    check("every slot carries a spoken label",
          all(s["label"] and ":" in s["label"] for s in d["slots"]), d["slots"][:2])
    check("the response states the timezone", d.get("timezone") == CHI, d)
    check("the response states the tenant's location",
          d.get("location") == "9800 Restland Rd, Dallas, TX 75243", d)

    # Weekend: the advisor works Mon-Fri.
    sat = date_cls.today() + timedelta(days=1)
    while sat.weekday() != 5:
        sat += timedelta(days=1)
    r = c.post(AV, headers=H(KEYS["s4"]), json={"date_from": sat.isoformat()})
    check("a non-working day returns nothing", r.json()["slot_count"] == 0, r.text)
    check("AND SAYS WHY, IN WORDS AN AGENT CAN SAY", r.json().get("reason"), r.text)

    r = c.post(AV, headers=H(KEYS["s4"]),
               json={"date_from": day.isoformat(), "timezone": NYC})
    d2 = r.json()
    check("a requested timezone changes the SPOKEN time",
          d2["slots"][0]["starts_at_local"][11:16] == "11:00", d2["slots"][:1])
    check("but not the underlying instant",
          d2["slots"][0]["starts_at"] == d["slots"][0]["starts_at"],
          [d2["slots"][0], d["slots"][0]])

    r = c.post(AV, headers=H(KEYS["s4"]),
               json={"date_from": day.isoformat(), "timezone": "Mars/Olympus"})
    check("AN UNKNOWN TIMEZONE IS REFUSED, NOT SWAPPED FOR A DEFAULT",
          r.status_code == 400, r.text)

    r = c.post(AV, headers=H(KEYS["s4"]),
               json={"date_from": day.isoformat(),
                     "date_to": (day + timedelta(days=60)).isoformat()})
    check("an oversized range is refused", r.status_code == 400, r.text)

    r = c.post(AV, headers=H(KEYS["s4"]),
               json={"date_from": day.isoformat(),
                     "date_to": (day - timedelta(days=1)).isoformat()})
    check("a backwards range is refused", r.status_code == 400, r.text)

    r = c.post(AV, headers=H(KEYS["s4"]),
               json={"date_from": date_cls.today().isoformat()})
    todays = [s for s in r.json()["slots"]
              if s["starts_at_local"][:10] == date_cls.today().isoformat()]
    now_local = ts._to_local(datetime.utcnow(), CHI)
    check("today's slots respect the notice window",
          all(datetime.fromisoformat(s["starts_at_local"]) >= now_local
              for s in todays), todays[:2])


# ── 5. the appointment type belongs to the tenant ───────────────────────────

def s5_appointment_type(c):
    print("\n[5] Appointment type comes from the tenant, not from this codebase")
    AV = "/integrations/retell/tenant/availability"
    day = next_weekday(4).isoformat()

    r = c.post(AV, headers=H(KEYS["s5"]),
               json={"date_from": day, "appointment_type": "Family File Review"})
    check("the tenant's configured type is accepted",
          r.json().get("appointment_type") == "Family File Review", r.text)

    r = c.post(AV, headers=H(KEYS["s5"]),
               json={"date_from": day, "appointment_type": "family file review"})
    check("matching is case-insensitive, as a transcript will be",
          r.json().get("appointment_type") == "Family File Review", r.text)

    r = c.post(AV, headers=H(KEYS["s5"]),
               json={"date_from": day, "appointment_type": "Bouncy Castle Hire"})
    check("a type this location does not offer is refused", r.status_code == 404, r.text)
    check("and the refusal lists what IS offered",
          "Family File Review" in r.json().get("detail", ""), r.text)

    r = c.post(AV, headers=H(KEYS["s5"]), json={"date_from": day})
    check("omitting it uses the tenant's first configured type",
          r.json().get("appointment_type") == "Family File Review", r.text)

    r = c.post(AV, headers=H(KEYS["blank"]),
               json={"date_from": day, "appointment_type": "Whatever They Call It"})
    check("A TENANT THAT CONFIGURED NOTHING IS NOT BLOCKED FROM BOOKING",
          r.status_code == 200, r.text)
    check("and its own wording is kept",
          r.json().get("appointment_type") == "Whatever They Call It", r.text)

    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "app", "services", "tenant_scheduling.py"),
               encoding="utf-8").read()
    check("NO CUSTOMER'S NAME IS HARDCODED IN THE SERVICE",
          "Garden of Freedom" not in src and "Restland" not in src)
    check("no customer address is hardcoded either",
          "Greenville Ave" not in src)


# ── 6. Google Calendar, and failing closed ──────────────────────────────────

def s6_google(c):
    print("\n[6] Google Calendar is actually consulted — and failure is not 'free'")
    AV = "/integrations/retell/tenant/availability"
    day = next_weekday(5)

    FakeCal.error = None
    FakeCal.busy = []
    base = c.post(AV, headers=H(KEYS["s6"]),
                  json={"date_from": day.isoformat(), "duration_minutes": 60}).json()
    check("baseline: the Google advisor has openings", base["slot_count"] > 0, base)
    first = base["slots"][0]

    # Put a Google event exactly over the first opening.
    busy_start = datetime.fromisoformat(first["starts_at"].replace("Z", ""))
    FakeCal.busy = [(busy_start, busy_start + timedelta(minutes=60))]
    after = c.post(AV, headers=H(KEYS["s6"]),
                   json={"date_from": day.isoformat(), "duration_minutes": 60}).json()
    taken = {s["starts_at"] for s in after["slots"]}
    check("A BUSY GOOGLE EVENT REMOVES THAT OPENING",
          first["starts_at"] not in taken, after["slots"][:3])
    check("and only that one — the rest survive", after["slot_count"] > 0, after)

    # The whole point: a calendar we cannot read is not a calendar that is free.
    FakeCal.busy = []
    FakeCal.error = "transport"
    r = c.post(AV, headers=H(KEYS["s6"]),
               json={"date_from": day.isoformat()})
    d = r.json()
    check("AN UNREADABLE CALENDAR RETURNS NO SLOTS — IT FAILS CLOSED",
          r.status_code == 200 and d["slot_count"] == 0, d)
    check("and says so, rather than implying a busy week",
          "calendar" in (d.get("reason") or "").lower(), d)

    FakeCal.error = "reauth"
    d = c.post(AV, headers=H(KEYS["s6"]),
               json={"date_from": day.isoformat()}).json()
    check("an expired Google grant also fails closed", d["slot_count"] == 0, d)

    FakeCal.error = None
    d = c.post(AV, headers=H(KEYS["restland-nocal"]),
               json={"date_from": day.isoformat()}).json()
    check("AN ADVISOR WITH NO CALENDAR IS NOT THE SAME AS AN UNREADABLE ONE",
          d["slot_count"] > 0, d)

    check("no provider token appears in any response",
          "refresh" not in r.text.lower() and "enc-google" not in r.text, r.text[:200])


# ── 7. blocks, buffers and existing bookings ────────────────────────────────

def s7_subtraction(c):
    print("\n[7] Blocks, buffers and existing bookings are subtracted")
    AV = "/integrations/retell/tenant/availability"
    FakeCal.busy = []
    FakeCal.error = None
    day = next_weekday(6)

    before = c.post(AV, headers=H(KEYS["s7"]),
                    json={"date_from": day.isoformat(),
                          "duration_minutes": 60}).json()
    n_before = before["slot_count"]
    check("baseline slots exist", n_before > 0, before)

    db = SessionLocal()
    db.add(AdvisorAvailabilityBlock(
        advisor_id="u-google", organization_id="org-rest",
        block_type=BlockType.SLOT, block_date=day, block_time="11:00"))
    db.commit()
    db.close()

    after = c.post(AV, headers=H(KEYS["s7"]),
                   json={"date_from": day.isoformat(),
                         "duration_minutes": 60}).json()
    hours = {s["starts_at_local"][11:16] for s in after["slots"]}
    check("A SLOT BLOCK REMOVES THAT TIME", "11:00" not in hours, sorted(hours))

    # An existing booking at 13:00, with a 15-minute buffer either side.
    db = SessionLocal()
    db.add(BookingLink(id="bk-existing", lead_id="lead-known", user_id="u-google",
                       status="booked", token="tok-existing",
                       booked_time=datetime(day.year, day.month, day.day, 13, 0)))
    db.commit()
    db.close()

    after2 = c.post(AV, headers=H(KEYS["s7"]),
                    json={"date_from": day.isoformat(),
                          "duration_minutes": 60}).json()
    hours2 = {s["starts_at_local"][11:16] for s in after2["slots"]}
    check("AN EXISTING BOOKING REMOVES ITS TIME", "13:00" not in hours2, sorted(hours2))
    check("the advisor's 15-minute buffer removes the abutting slot too",
          "12:00" not in hours2, sorted(hours2))

    db = SessionLocal()
    db.add(AdvisorAvailabilityBlock(
        advisor_id="u-google", organization_id="org-rest",
        block_type=BlockType.DATE_RANGE, start_date=day, end_date=day,
        reason="vacation"))
    db.commit()
    db.close()

    d = c.post(AV, headers=H(KEYS["s7"]),
               json={"date_from": day.isoformat()}).json()
    check("A DATE-RANGE BLOCK CLEARS THE WHOLE DAY", d["slot_count"] == 0, d)
    check("and explains itself", d.get("reason"), d)


# ── 8. booking ──────────────────────────────────────────────────────────────

def s8_book(c):
    print("\n[8] Booking: re-validation, the family, and the records created")
    AV = "/integrations/retell/tenant/availability"
    BOOK = "/integrations/retell/tenant/book"
    FakeCal.busy = []
    FakeCal.error = None
    FakeCal.created = []
    del SMS[:]
    del MAIL[:]
    day = next_weekday(8)

    slots = c.post(AV, headers=H(KEYS["s8"]),
                   json={"date_from": day.isoformat(),
                         "duration_minutes": 60}).json()["slots"]
    check("openings to book into", len(slots) >= 3, len(slots))
    pick = slots[0]

    r = c.post(BOOK, headers=H(KEYS["s8"]), json={
        "external_ref": "retell-call-alpha-1",
        "starts_at": pick["starts_at"],
        "duration_minutes": 60,
        "appointment_type": "Family File Review",
        "family_name": "Marta Delgado",
        "family_phone": "+15555551234",
        "family_email": "marta@example.test",
        "notes": "Asked about plots near the oak.",
    })
    check("a booking succeeds", r.status_code == 200, r.text)
    d = r.json()
    ID["booking"] = d.get("booking_id")
    ID["lead"] = d.get("lead_id")
    check("it is not flagged as a replay", d.get("idempotent_replay") is False, d)
    check("it returns a spoken label", d.get("label"), d)
    check("it names the appointment type",
          d.get("appointment_type") == "Family File Review", d)
    check("it names the in-person location",
          d.get("location") == "9800 Restland Rd, Dallas, TX 75243", d)
    check("the calendar write is reported", d.get("calendar_synced") is True, d)

    db = SessionLocal()
    bk = db.query(BookingLink).filter(BookingLink.id == ID["booking"]).first()
    lead = db.query(Lead).filter(Lead.id == ID["lead"]).first()
    check("A REAL BookingLink EXISTS", bk is not None, d)
    check("its status is booked", bk and bk.status == "booked", bk.status if bk else None)
    check("booked_time is stored as LOCAL wall time, matching the Vercel flow",
          bk and bk.booked_time.strftime("%H:%M") == pick["starts_at_local"][11:16],
          [bk.booked_time.isoformat() if bk else None, pick["starts_at_local"]])
    check("the calendar event id is stored for later cancellation",
          bk and bk.calendar_event_id == "ext-tenant-1", bk.calendar_event_id if bk else None)
    check("A LEAD WAS CREATED FOR THE FAMILY", lead is not None, d)
    check("the lead belongs to the RIGHT tenant",
          lead and lead.organization_id == "org-rest", lead.organization_id if lead else None)
    check("the lead carries only what the caller supplied — no invented email",
          lead and lead.email == "marta@example.test" and lead.first_name == "Marta",
          [lead.email, lead.first_name] if lead else None)
    check("the lead's provenance is recorded",
          lead and (lead.source_file or "").startswith("voice:"),
          lead.source_file if lead else None)
    check("the lead is marked booked", lead and lead.status == "booked")

    cf = db.execute(text("SELECT COUNT(*) FROM appointment_case_files "
                         "WHERE booking_link_id = :b"),
                    {"b": ID["booking"]}).scalar()
    check("THE ADVISOR'S CLIENT RECORD WAS CREATED, AS THE VERCEL FLOW DOES",
          cf == 1, cf)
    db.close()

    check("the calendar event carries the tenant's address, not a hardcoded one",
          FakeCal.created and FakeCal.created[-1]["location"]
          == "9800 Restland Rd, Dallas, TX 75243", FakeCal.created[-1:])
    check("the event subject names the appointment type and the family",
          FakeCal.created and "Family File Review" in FakeCal.created[-1]["subject"]
          and "Marta" in FakeCal.created[-1]["subject"], FakeCal.created[-1:])
    check("THE EXISTING CONFIRMATION FLOW RAN — the family was contacted",
          d.get("confirmation_sent") is True and (len(SMS) + len(MAIL)) > 0,
          {"sms": SMS, "mail": MAIL})

    # The booked time is now gone from availability.
    again = c.post(AV, headers=H(KEYS["s8"]),
                   json={"date_from": day.isoformat(),
                         "duration_minutes": 60}).json()
    check("THE BOOKED TIME NO LONGER APPEARS AS AVAILABLE",
          pick["starts_at"] not in {s["starts_at"] for s in again["slots"]},
          again["slots"][:3])

    ID["day"] = day.isoformat()
    ID["free"] = slots[2]["starts_at"]


# ── 9. re-validation and double-booking ─────────────────────────────────────

def s9_revalidate(c):
    print("\n[9] Nothing is trusted because it was free a moment ago")
    BOOK = "/integrations/retell/tenant/book"
    AV = "/integrations/retell/tenant/availability"
    day = next_weekday(9)
    FakeCal.busy = []
    FakeCal.error = None

    slots = c.post(AV, headers=H(KEYS["s9"]),
                   json={"date_from": day.isoformat(),
                         "duration_minutes": 60}).json()["slots"]
    target = slots[0]["starts_at"]

    r = c.post(BOOK, headers=H(KEYS["s9"]), json={
        "external_ref": "revalidate-first-1", "starts_at": target,
        "duration_minutes": 60, "family_name": "First Family",
        "family_phone": "+15555552001"})
    check("the first caller gets the slot", r.status_code == 200, r.text)

    r = c.post(BOOK, headers=H(KEYS["s9"]), json={
        "external_ref": "revalidate-second-2", "starts_at": target,
        "duration_minutes": 60, "family_name": "Second Family",
        "family_phone": "+15555552002"})
    check("A SECOND CALLER FOR THE SAME TIME IS REFUSED", r.status_code == 409, r.text)
    check("with wording a voice agent can say",
          "another" in r.json().get("detail", "").lower(), r.text)

    # A Google event that appeared AFTER availability was read.
    free = slots[3]["starts_at"]
    gs = datetime.fromisoformat(free.replace("Z", ""))
    FakeCal.busy = [(gs, gs + timedelta(minutes=60))]
    r = c.post(BOOK, headers=H(KEYS["s9"]), json={
        "external_ref": "revalidate-google-3", "starts_at": free,
        "duration_minutes": 60, "family_name": "Late Clash",
        "family_phone": "+15555552003"})
    check("A CALENDAR EVENT THAT APPEARED MID-CALL BLOCKS THE BOOKING",
          r.status_code == 409, r.text)

    FakeCal.error = "transport"
    r = c.post(BOOK, headers=H(KEYS["s9"]), json={
        "external_ref": "revalidate-blind-4", "starts_at": slots[4]["starts_at"],
        "duration_minutes": 60, "family_name": "Blind Booking",
        "family_phone": "+15555552004"})
    check("IF THE CALENDAR CANNOT BE READ, NOTHING IS WRITTEN",
          r.status_code == 503, r.text)
    FakeCal.error = None
    FakeCal.busy = []

    db = SessionLocal()
    n = db.query(BookingLink).filter(BookingLink.user_id == "u-google").count()
    db.close()

    # Outside the advisor's hours, on a day they do not work, and in the past.
    for label, payload in [
        ("outside the advisor's hours", {
            "external_ref": "outside-hours-5",
            "starts_at": utc_for(datetime(day.year, day.month, day.day, 7, 0))}),
        ("in the past", {
            "external_ref": "in-the-past-6",
            "starts_at": utc_for(datetime.now().replace(microsecond=0)
                                 - timedelta(days=2))}),
    ]:
        payload.update({"duration_minutes": 60, "family_name": "Nope",
                        "family_phone": "+15555552009"})
        r = c.post(BOOK, headers=H(KEYS["s9"]), json=payload)
        check("a booking %s is refused" % label,
              r.status_code in (400, 409), (label, r.status_code, r.text[:150]))

    db = SessionLocal()
    n2 = db.query(BookingLink).filter(BookingLink.user_id == "u-google").count()
    db.close()
    check("NO REFUSED BOOKING CREATED A RECORD", n2 == n, (n, n2))


# ── 10. idempotency ─────────────────────────────────────────────────────────

def s10_idempotent(c):
    print("\n[10] Retell retries do not double-book")
    BOOK = "/integrations/retell/tenant/book"
    AV = "/integrations/retell/tenant/availability"
    day = next_weekday(11)
    FakeCal.busy = []
    FakeCal.error = None
    del SMS[:]
    del MAIL[:]

    slots = c.post(AV, headers=H(KEYS["s10"]),
                   json={"date_from": day.isoformat(),
                         "duration_minutes": 60}).json()["slots"]
    body = {"external_ref": "retell-retry-xyz", "starts_at": slots[0]["starts_at"],
            "duration_minutes": 60, "family_name": "Retry Family",
            "family_phone": "+15555553001"}

    first = c.post(BOOK, headers=H(KEYS["s10"]), json=body)
    check("the first attempt books", first.status_code == 200, first.text)
    sent_after_first = len(SMS) + len(MAIL)

    second = c.post(BOOK, headers=H(KEYS["s10"]), json=body)
    check("the retry succeeds", second.status_code == 200, second.text)
    check("THE RETRY IS FLAGGED AS A REPLAY",
          second.json().get("idempotent_replay") is True, second.text)
    check("AND RETURNS THE ORIGINAL BOOKING, NOT A NEW ONE",
          second.json().get("booking_id") == first.json().get("booking_id"),
          [first.json().get("booking_id"), second.json().get("booking_id")])
    check("THE FAMILY IS NOT TEXTED TWICE",
          len(SMS) + len(MAIL) == sent_after_first,
          {"before": sent_after_first, "after": len(SMS) + len(MAIL)})

    db = SessionLocal()
    n = db.query(BookingLink).filter(
        BookingLink.user_id == "u-google",
        BookingLink.booked_time == datetime.fromisoformat(
            slots[0]["starts_at_local"])).count()
    db.close()
    check("exactly one booking exists for that time", n == 1, n)

    # A FAILED attempt must not burn its reference.
    FakeCal.error = "transport"
    ref = "retry-after-failure-1"
    r = c.post(BOOK, headers=H(KEYS["s10"]), json={
        "external_ref": ref, "starts_at": slots[2]["starts_at"],
        "duration_minutes": 60, "family_name": "Recover",
        "family_phone": "+15555553002"})
    check("a failing attempt is refused", r.status_code == 503, r.text)
    FakeCal.error = None
    r = c.post(BOOK, headers=H(KEYS["s10"]), json={
        "external_ref": ref, "starts_at": slots[2]["starts_at"],
        "duration_minutes": 60, "family_name": "Recover",
        "family_phone": "+15555553002"})
    check("A FAILED ATTEMPT DOES NOT BURN ITS REF — the retry works",
          r.status_code == 200, r.text)

    r = c.post(BOOK, headers=H(KEYS["s10"]), json={
        "external_ref": "short", "starts_at": slots[3]["starts_at"]})
    check("an unusably short external_ref is refused", r.status_code == 422, r.status_code)


# ── 11. the family, and cross-tenant lead access ────────────────────────────

def s11_lead(c):
    print("\n[11] Lead association")
    BOOK = "/integrations/retell/tenant/book"
    AV = "/integrations/retell/tenant/availability"
    day = next_weekday(13)
    FakeCal.busy = []
    FakeCal.error = None

    slots = c.post(AV, headers=H(KEYS["s11"]),
                   json={"date_from": day.isoformat(),
                         "duration_minutes": 60}).json()["slots"]

    r = c.post(BOOK, headers=H(KEYS["s11"]), json={
        "external_ref": "known-lead-book-1", "starts_at": slots[0]["starts_at"],
        "duration_minutes": 60, "lead_id": "lead-known"})
    check("an existing family can be booked by lead_id", r.status_code == 200, r.text)
    check("AND IS LINKED TO THAT EXACT LEAD",
          r.json().get("lead_id") == "lead-known", r.text)

    r = c.post(BOOK, headers=H(KEYS["s11"]), json={
        "external_ref": "foreign-lead-book-2", "starts_at": slots[1]["starts_at"],
        "duration_minutes": 60, "lead_id": "lead-foreign"})
    check("A LEAD FROM ANOTHER FUNERAL HOME IS NOT REACHABLE",
          r.status_code == 404, r.text)
    check("and the refusal reveals nothing about it",
          r.json().get("detail") == "Lead not found.", r.text)

    # A returning family, recognised by phone rather than duplicated.
    r = c.post(BOOK, headers=H(KEYS["s11"]), json={
        "external_ref": "returning-family-3", "starts_at": slots[2]["starts_at"],
        "duration_minutes": 60, "family_name": "Dana Whitfield",
        "family_phone": "+15555550123"})
    check("a returning family is matched on phone, not duplicated",
          r.status_code == 200 and r.json().get("lead_id") == "lead-known", r.text)

    r = c.post(BOOK, headers=H(KEYS["s11"]), json={
        "external_ref": "anonymous-family-4", "starts_at": slots[3]["starts_at"],
        "duration_minutes": 60})
    check("a booking with no family details at all is refused",
          r.status_code == 400, r.text)


# ── 12. audit, rate limiting, and leakage ───────────────────────────────────

def s12_audit(c):
    print("\n[12] Audit, rate limiting, and what must never appear")
    db = SessionLocal()
    rows = db.query(IntegrationRequestLog).all()
    tenant_rows = [r for r in rows if r.organization_id == "org-rest"]
    check("requests are recorded against the tenant", len(tenant_rows) > 0, len(rows))
    check("FAILURES ARE RECORDED, NOT ONLY SUCCESSES",
          any(not r.success for r in rows), [r.success for r in rows[:10]])
    check("failures record the status code",
          any((not r.success) and r.status_code for r in rows))
    check("bookings record the lead and the booking",
          any(r.booking_link_id and r.lead_id for r in rows))
    check("NO AUDIT ROW CONTAINS A SECRET",
          all("evsk_" not in ((r.detail or "") + (r.key_prefix or "")[12:])
              for r in rows))
    check("no audit row leaks across trees",
          all(not (r.organization_id and r.brand_sales_org_id) for r in rows))

    creds = db.query(IntegrationCredential).all()
    check("NO FULL KEY IS STORED ANYWHERE",
          all(KEYS["s12"] != cr.key_hash and KEYS["s12"] not in (cr.note or "")
              for cr in creds))
    check("only a hash is stored",
          all(len(cr.key_hash) == 64 for cr in creds))
    db.close()

    AV = "/integrations/retell/tenant/availability"
    day = next_weekday(4).isoformat()
    codes = []
    for _ in range(40):
        codes.append(c.post(AV, headers=H(KEYS["s12"]),
                            json={"date_from": day}).status_code)
    check("THE RATE LIMIT ACTUALLY FIRES", 429 in codes, sorted(set(codes)))
    check("the limit is per credential — another key is unaffected",
          c.post(AV, headers=H(KEYS["blank"]),
                 json={"date_from": day}).status_code != 429)


# ── 13. static guarantees ───────────────────────────────────────────────────

def s13_static():
    print("\n[13] Guarantees that must hold in the source, not just at runtime")
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.join(here, "..")

    def read(*p):
        return open(os.path.join(root, *p), encoding="utf-8").read()

    router = read("app", "routers", "integrations_router.py")
    body = router.split('"""', 2)[-1]
    check("no tenant route accepts a user JWT",
          "get_current_user" not in body, "get_current_user found")
    check("EVERY tenant route is gated by the tenant dependency",
          body.count("Depends(require_retell_tenant)") == 3,
          body.count("Depends(require_retell_tenant)"))
    check("the brand routes still exist and are still gated",
          body.count("Depends(require_retell)") == 3,
          body.count("Depends(require_retell)"))
    check("no tenant request model accepts an organization_id",
          "organization_id" not in body.split("class TenantAvailabilityIn")[-1]
          .split("# ── tenant routes")[0])

    # These assertions are about CODE, not prose. The module documents the
    # separation at length and names the things it deliberately avoids, so the
    # docstrings and comments are stripped before looking.
    svc = code_only(read("app", "services", "tenant_scheduling.py"))
    check("the service never imports the brand-sales bridge",
          "retell_bridge" not in svc)
    check("it never writes a SalesAppointment", "SalesAppointment" not in svc)
    check("THE DEAD GOOGLE HELPER IS NOT REUSED",
          "_get_google_credentials" not in svc)
    check("the unauthenticated booking webhook is not proxied",
          "booking_confirmed_webhook" not in svc and "calendar_router" not in svc)

    auth = read("app", "services", "integration_auth.py")
    check("the tenant dependency checks the SCOPE, not just the kind string",
          "scope_kind()" in auth)

    mig = read("app", "auto_migrate.py")
    for col in ("organization_id", "booking_link_id"):
        check("the new column %s is in the migration list" % col,
              '"%s"' % col in mig or "'%s'" % col in mig)
    check("THE NOT-NULL RELAX FOR BRAND SCOPE IS REGISTERED",
          '("integration_credentials", "brand_sales_org_id")' in mig)

    # The brand-sales bridge must be untouched by this work.
    brand = read("app", "services", "retell_bridge.py")
    check("THE BRAND-SALES BRIDGE STILL BOOKS SalesAppointment",
          "SalesAppointment(" in brand)
    check("and still uses the sales availability engine",
          "find_shared_slots" in brand)


def main():
    install_fakes()
    build()
    c = TestClient(app)
    try:
        _run(c)
    except Exception:
        # Printed ASCII-safe on purpose. Source lines in this repo contain box
        # characters, and a console codepage that cannot encode them truncates
        # the traceback at exactly the frame you need to read.
        import traceback
        txt = traceback.format_exc()
        print(txt.encode("ascii", "replace").decode("ascii"))
        FAILURES.append("UNHANDLED EXCEPTION")
    finally:
        try:
            from app.services import calendar_providers as cp
            cp.reset_providers()
        except Exception:
            pass

    print()
    if FAILURES:
        print("  %d FAILURE(S): %s" % (len(FAILURES), ", ".join(FAILURES[:10])))
        shutil.rmtree(TMP, ignore_errors=True)
        sys.exit(1)
    print("  ALL TENANT BRIDGE CHECKS PASSED")
    shutil.rmtree(TMP, ignore_errors=True)


def _run(c):
    if True:
        s1_auth(c)
        s2_trees(c)
        s3_advisor(c)
        s4_settings(c)
        s5_appointment_type(c)
        s6_google(c)
        s7_subtraction(c)
        s8_book(c)
        s9_revalidate(c)
        s10_idempotent(c)
        s11_lead(c)
        s12_audit(c)
        s13_static()
        s14_show()


def s14_show():
    """`integration_key.py show` is the key-free way to verify a credential.

    It exists so nobody has to put a secret on a command line to answer "is
    this pointed at the right person?". If it ever started printing something
    secret, that reason would evaporate — hence the assertions at the end.
    """
    print("\n[14] Verifying a credential without handling the key")
    import io
    import contextlib
    import importlib.util

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "integration_key.py")
    # The script refuses to import without DATABASE_URL, which this suite set
    # at module load, so it picks up the same temp SQLite.
    spec = importlib.util.spec_from_file_location("integration_key_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class Args(object):
        def __init__(self, prefix, recent=5):
            self.prefix = prefix
            self.recent = recent

    db = SessionLocal()
    cred = (db.query(IntegrationCredential)
            .filter(IntegrationCredential.name == "taffiny").first())
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod.cmd_show(db, Args(cred.key_prefix))
    out = buf.getvalue()
    db.close()

    check("show reports the tenant", "Restland Memorial" in out, out[:300])
    check("show reports the default advisor", "Grace Alvarez" in out, out[:300])
    check("SHOW REPORTS WHICH CALENDAR IS ACTUALLY CONNECTED",
          "Calendar    : google" in out, out[:400])
    check("show reports the advisor's real hours",
          "10:00-16:00" in out, out[:400])
    check("show reports the tenant's appointment types",
          "Family File Review" in out, out[:400])
    check("show reports the allowlist and rate limit",
          "Allowlist" in out and "60/min" in out, out[:400])
    check("show reports whether Retell has called yet",
          "Last used" in out, out[:400])
    check("THE FULL KEY NEVER APPEARS IN show's OUTPUT",
          KEYS["taffiny"] not in out, "secret leaked")
    check("nor does the stored hash", cred.key_hash not in out, "hash leaked")

    # A misconfigured credential must be reported as broken, not rendered as
    # if it were usable.
    db = SessionLocal()
    broken = (db.query(IntegrationCredential)
              .filter(IntegrationCredential.name == "both-scopes").first())
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod.cmd_show(db, Args(broken.key_prefix))
    out2 = buf.getvalue()
    db.close()
    check("A KEY SCOPED TO BOTH TREES IS REPORTED AS BROKEN",
          "BROKEN" in out2, out2[:300])
    check("and says to reissue it", "reissue" in out2.lower(), out2[:300])


if __name__ == "__main__":
    main()
