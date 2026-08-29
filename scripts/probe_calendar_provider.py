"""probe_calendar_provider.py — deploy gate for the calendar system of record.

The bug this exists to prevent: an advisor connected to both Microsoft and
Google silently gets Microsoft, because Microsoft is written first in

    PREFERENCE = (PROVIDER_MICROSOFT, PROVIDER_GOOGLE)

That is not a decision anyone made about the customer's business, it appears in
no screen, and it means "we fixed the Google calendar" can be completed in full
and change nothing about where a booking is written.

The contract under test:

    advisor override -> organization default -> (only then) preference order
    a CONFIGURED provider that is unusable FAILS CLOSED
    it never silently resolves to the other external provider

The last line is the one that matters. Every positive check here is paired with
the negative that would have passed before this existed.

No network. Run: python scripts/probe_calendar_provider.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///./.probe_calendar_provider.db"
os.environ["JWT_SECRET"] = "probe" * 16
from cryptography.fernet import Fernet                            # noqa: E402
os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()

DB_FILE = os.path.join(ROOT, ".probe_calendar_provider.db")
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

from app.main import app as _app  # noqa: E402,F401
from app.deps import SessionLocal, engine                          # noqa: E402
from app.models.models import Base, Organization, Platform, User    # noqa: E402
from app.services import calendar_providers as cp                   # noqa: E402

failures, checks = [], 0


def check(label, ok, detail=""):
    global checks
    checks += 1
    if ok:
        print("  PASS  " + label)
    else:
        print("  FAIL  " + label + ("  -> " + str(detail)[:240] if detail else ""))
        failures.append(label)


Base.metadata.create_all(bind=engine)
db = SessionLocal()

db.add(Platform(id="plt-evosyspro", name="EvoSys Pro", slug="evosyspro",
                domain="app.evosyspro.live", support_email="support@evosyspro.live"))
db.commit()

ORG = "org-restland"
db.add(Organization(id=ORG, name="Restland Cemetery and Funeral Home",
                    slug="restland", platform_id="plt-evosyspro"))
db.commit()


def mkuser(uid, google_token=True, ms_token=True, advisor_pref=None):
    u = User(id=uid, email=uid + "@example.com", full_name=uid,
             password_hash="x", role="advisor", organization_id=ORG,
             is_active=True)
    if google_token:
        u.google_oauth_refresh_token_encrypted = "g-token"
        u.google_calendar_connected = True
    if ms_token:
        u.microsoft_oauth_refresh_token_encrypted = "m-token"
        u.microsoft_365_connected = True
    u.calendar_provider = advisor_pref
    db.add(u)
    db.commit()
    return u


def set_org_provider(value):
    org = db.query(Organization).filter(Organization.id == ORG).first()
    org.calendar_provider = value
    db.commit()


# ── 1. the bug, reproduced, then fixed ──────────────────────────────────────

print("\n[1] TUPLE ORDER STOPS DECIDING WHOSE CALENDAR THIS IS")

both = mkuser("both-connected")

set_org_provider(None)
check("1. with NOTHING configured, the old behaviour still stands",
      cp.resolve_provider_key(db, both) == cp.PROVIDER_MICROSOFT,
      cp.resolve_provider_key(db, both))
check("   which is Microsoft purely because it is first in PREFERENCE",
      cp.PREFERENCE[0] == cp.PROVIDER_MICROSOFT)

set_org_provider("google")
check("1. THE ORGANIZATION'S CHOICE BEATS THE TUPLE",
      cp.resolve_provider_key(db, both) == cp.PROVIDER_GOOGLE,
      cp.resolve_provider_key(db, both))
key, src = cp.configured_provider_key(db, both)
check("   and it reports WHERE the choice came from",
      (key, src) == ("google", "organization"), (key, src))

# The reverse, so this cannot pass by returning "google" unconditionally.
set_org_provider("microsoft")
check("1. an org that chooses Microsoft gets Microsoft",
      cp.resolve_provider_key(db, both) == cp.PROVIDER_MICROSOFT)


# ── 2. the advisor overrides their organization ─────────────────────────────

print("\n[2] AN ADVISOR MAY DIFFER FROM THEIR ORGANIZATION")

set_org_provider("microsoft")
odd = mkuser("advisor-on-google", advisor_pref="google")
check("2. the advisor's own setting wins",
      cp.resolve_provider_key(db, odd) == cp.PROVIDER_GOOGLE,
      cp.resolve_provider_key(db, odd))
check("   and says so",
      cp.configured_provider_key(db, odd) == ("google", "advisor"))
check("2. their colleague still follows the organization",
      cp.resolve_provider_key(db, both) == cp.PROVIDER_MICROSOFT)


# ── 3. FAIL CLOSED — the load-bearing section ───────────────────────────────

print("\n[3] A CHOSEN CALENDAR THAT CANNOT BE REACHED FAILS. IT DOES NOT "
      "SWAP.")

set_org_provider("google")
# Microsoft is live, Google is not. Before this change the answer was
# Microsoft. That is the exact silent-wrong-calendar failure.
ms_only = mkuser("google-chosen-but-only-ms-connected", google_token=False)

check("3. it still resolves to the CHOSEN provider, not the available one",
      cp.resolve_provider_key(db, ms_only) == cp.PROVIDER_GOOGLE,
      cp.resolve_provider_key(db, ms_only))
check("3. IT DOES NOT FALL BACK TO MICROSOFT",
      cp.resolve_provider_key(db, ms_only) != cp.PROVIDER_MICROSOFT)

prov = cp.get_provider(db, ms_only, db.query(Organization).filter(
    Organization.id == ORG).first())
resolved = getattr(prov, "resolved_key", None)
check("3. the built provider degrades to .ics, never to the other calendar",
      resolved == cp.PROVIDER_ICS, resolved)
check("3. AND .ics IS NOT TREATED AS A READABLE CALENDAR",
      cp.is_external_calendar(resolved) is False)

# The load-bearing assertion, made where it actually matters: the tenant
# availability read, not the raw provider.
#
# .ics answers "no busy intervals, no error", which is indistinguishable from
# a calendar that WAS read and found empty. Asserting against the provider
# alone would therefore have passed while the caller still fabricated a free
# day. This asserts the behaviour a family would experience.
from datetime import datetime as _dt                               # noqa: E402
from app.services import tenant_scheduling as ts                    # noqa: E402

_org = db.query(Organization).filter(Organization.id == ORG).first()
busy, err = ts.external_busy(db, ms_only, _org,
                             _dt(2026, 9, 1), _dt(2026, 9, 2))
check("3. THE AVAILABILITY READ REPORTS UNREADABLE, NOT FREE",
      err is not None, (busy, err))
check("   rather than an empty busy list, which would read as 'free all day'",
      not (err is None and busy == []))

# Positive control: an advisor whose CHOSEN provider is genuinely absent (no
# external calendar configured at all) is a legitimate non-error state and
# must stay one, or every unconnected advisor becomes permanently unbookable.
set_org_provider(None)
none_connected = mkuser("no-calendar-at-all", google_token=False, ms_token=False)
busy2, err2 = ts.external_busy(db, none_connected, _org,
                               _dt(2026, 9, 1), _dt(2026, 9, 2))
check("3. but NO calendar configured at all is still not an error",
      err2 is None and busy2 == [], (busy2, err2))
set_org_provider("google")


# ── 4. explicit prefer= still wins, for cancellation ────────────────────────

print("\n[4] AN EXPLICIT prefer= STILL WINS, SO CANCELLATION FINDS ITS EVENT")

set_org_provider("google")
check("4. cancelling an event written to Microsoft can still say so",
      cp.resolve_provider_key(db, both, prefer=cp.PROVIDER_MICROSOFT)
      == cp.PROVIDER_MICROSOFT)
check("   and .ics can be asked for by name",
      cp.resolve_provider_key(db, both, prefer=cp.PROVIDER_ICS)
      == cp.PROVIDER_ICS)


# ── 5. garbage in the column does not become a provider ─────────────────────

print("\n[5] A BAD VALUE IS IGNORED, NOT OBEYED")

set_org_provider("Gmail")          # plausible, wrong
check("5. an unrecognised org value is treated as unset",
      cp.configured_provider_key(db, both) == (None, None),
      cp.configured_provider_key(db, both))
set_org_provider("  GOOGLE  ")     # case and whitespace
check("5. a recognised value survives case and whitespace",
      cp.configured_provider_key(db, both) == ("google", "organization"))
set_org_provider(None)
check("5. clearing it returns to the preference order",
      cp.resolve_provider_key(db, both) == cp.PROVIDER_MICROSOFT)
check("5. a None user does not raise",
      cp.configured_provider_key(db, None) == (None, None))

db.close()

print("\n%d checks, %d failure(s)" % (checks, len(failures)))
if failures:
    print("FAILED:")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("ALL CALENDAR PROVIDER CHECKS PASSED")
