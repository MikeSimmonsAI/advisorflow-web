"""probe_public_identity.py — deploy gate for customer-facing identity.

The bug this exists to prevent: a Greenland family receiving mail from
support@bookaboost.live pointing at advisorflow-booking.vercel.app. Greenland
is an EvoSys Pro customer. Neither name is theirs, and one belongs to a
competitor brand inside the same codebase.

Every assertion here is paired: a positive control proving the right brand
resolves, and a negative proving a DIFFERENT brand in the same database does
not leak into it. A suite that only checked "EvoSys resolves to EvoSys" would
pass just as happily against a function that returns EvoSys unconditionally.

No network. No production database. Run: python scripts/probe_public_identity.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///./.probe_public_identity.db"
os.environ["JWT_SECRET"] = "probe" * 16
from cryptography.fernet import Fernet                            # noqa: E402
os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()

# Deliberately NOT set, so level 4 cannot mask a level 2 failure.
os.environ.pop("EMAIL_FROM_ADDRESS", None)
os.environ.pop("BOOKING_BASE_URL", None)
os.environ.pop("PUBLIC_BASE_URL", None)
os.environ.pop("TRACKING_BASE_URL", None)

DB_FILE = os.path.join(ROOT, ".probe_public_identity.db")
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

from app.main import app as _app  # noqa: E402,F401  (registers every model)
from app.deps import SessionLocal, engine                          # noqa: E402
from app.models.models import Base, Organization, Platform          # noqa: E402
from app.services import public_identity as pi                      # noqa: E402

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

# ── three brands, exactly as production stores them ─────────────────────────

db.add(Platform(id="plt-evosyspro", name="EvoSys Pro", slug="evosyspro",
                domain="app.evosyspro.live", support_email="support@evosyspro.live"))
db.add(Platform(id="plt-bookaboost", name="BookaBoost", slug="bookaboost",
                domain="app.bookaboost.live", support_email="support@bookaboost.live"))
# A brand whose row predates the columns - proves the code registry still answers.
db.add(Platform(id="plt-bare", name="Bare Brand", slug="evosyspro-bare",
                domain=None, support_email=None))
db.commit()

GREENLAND = "org-greenland"
RIVAL = "org-rival"
OWN_DOMAIN = "org-own-domain"
BARE = "org-bare"
ORPHAN = "org-orphan"

db.add(Organization(id=GREENLAND, name="Greenland Cemetery and Funeral Home",
                    slug="restland", platform_id="plt-evosyspro"))
db.add(Organization(id=RIVAL, name="A BookaBoost Customer",
                    slug="rival", platform_id="plt-bookaboost"))
db.add(Organization(id=OWN_DOMAIN, name="Has Its Own Domain", slug="own",
                    platform_id="plt-evosyspro",
                    from_email="hello@theirown.com",
                    resend_api_key="re_org_key_not_real"))
db.add(Organization(id=BARE, name="On A Bare Platform", slug="bare",
                    platform_id="plt-bare"))
db.add(Organization(id=ORPHAN, name="No Platform At All", slug="orphan",
                    platform_id=None))
db.commit()


# ── 1. the platform answers, and answers per brand ──────────────────────────

print("\n[1] THE BRAND A CUSTOMER BELONGS TO IS THE BRAND THEY SEE")

g = pi.identity_for_org(db, GREENLAND)
check("1. Greenland sends from its own brand",
      g.from_email == "support@evosyspro.live", g.from_email)
check("   and the platform row is what answered",
      g.source.get("from_email") == "platform", g.source)
check("1. Greenland links point at its own brand",
      g.public_base_url == "https://app.evosyspro.live", g.public_base_url)
check("   a bare stored host is turned into a real URL",
      g.public_base_url.startswith("https://"), g.public_base_url)
check("1. the brand name is the platform's, not AdvisorFlow's",
      g.brand_name == "EvoSys Pro", g.brand_name)

r = pi.identity_for_org(db, RIVAL)
check("1. A DIFFERENT BRAND IN THE SAME DATABASE DOES NOT LEAK",
      r.from_email == "support@bookaboost.live", r.from_email)
check("   and its links are its own too",
      r.public_base_url == "https://app.bookaboost.live", r.public_base_url)
check("   the two brands resolved differently",
      g.from_email != r.from_email and g.public_base_url != r.public_base_url)

# The whole point. Before this module Greenland got the BookaBoost default.
check("1. NO BOOKABOOST ADDRESS REACHES AN EVOSYS CUSTOMER",
      "bookaboost" not in (g.from_email or "").lower())
check("   AND NO RENDER OR VERCEL HOST REACHES ONE EITHER",
      "onrender" not in (g.public_base_url or "").lower()
      and "vercel" not in (g.public_base_url or "").lower(), g.public_base_url)


# ── 2. precedence ───────────────────────────────────────────────────────────

print("\n[2] THE MOST SPECIFIC LEVEL WINS")

o = pi.identity_for_org(db, OWN_DOMAIN)
check("2. an org's own verified address beats its platform's",
      o.from_email == "hello@theirown.com", o.from_email)
check("   and the organization row is what answered",
      o.source.get("from_email") == "organization", o.source)
check("2. its own Resend key travels with its own address",
      o.resend_api_key == "re_org_key_not_real")
check("   an org WITHOUT its own key does not borrow one",
      pi.identity_for_org(db, GREENLAND).resend_api_key is None)
check("2. but it still uses its brand's public host",
      o.public_base_url == "https://app.evosyspro.live", o.public_base_url)

b = pi.identity_for_org(db, BARE)
check("2. a platform row with no columns set falls to the code registry "
      "rather than to nothing",
      b.source.get("from_email") in ("registry", "unresolved"), b.source)


# ── 3. it fails closed, it does not invent ──────────────────────────────────

print("\n[3] AN UNKNOWN BRAND GETS NOTHING, NOT A PLAUSIBLE GUESS")

orp = pi.identity_for_org(db, ORPHAN)
check("3. no platform means no from address",
      orp.from_email is None, orp.from_email)
check("   and it says so rather than silently defaulting",
      orp.source.get("from_email") == "unresolved", orp.source)
check("3. no platform means no public host",
      orp.public_base_url is None, orp.public_base_url)
check("3. a missing organization does not raise",
      pi.identity_for_org(db, "does-not-exist").from_email is None)
check("3. a None organization_id does not raise",
      pi.identity_for_org(db, None).from_email is None)

# The failure mode that matters most: inventing an address. If the registry or
# the environment ever starts answering for an unknown brand, this fails.
check("3. NOTHING FABRICATES AN ADDRESS FOR AN UNKNOWN BRAND",
      orp.from_email is None and orp.public_base_url is None)


# ── 4. the link builders ────────────────────────────────────────────────────

print("\n[4] LINKS ARE BUILT IN ONE PLACE, NOT CONCATENATED AT CALL SITES")

bu = pi.booking_url(db, GREENLAND, "tok123")
check("4. a booking link is branded",
      bu == "https://app.evosyspro.live/book/tok123", bu)
su = pi.survey_url(db, GREENLAND, "tok456")
check("4. a survey link is branded",
      su == "https://app.evosyspro.live/survey/tok456", su)
check("4. a rival brand's booking link is its own",
      pi.booking_url(db, RIVAL, "t") == "https://app.bookaboost.live/book/t")
check("4. NO INFRASTRUCTURE HOST APPEARS IN EITHER LINK",
      all(h not in (bu + su) for h in ("onrender", "vercel", "localhost")))

# A positive control for the sending adapter: it must duck-type what
# send_email_via_provider already reads, or the wiring silently does nothing.
si = pi.sending_identity_for_org(db, GREENLAND)
check("4. the sending adapter exposes what the mailer reads",
      hasattr(si, "from_email") and hasattr(si, "resend_api_key"))
check("   and carries the RESOLVED address, not the raw org's empty one",
      si.from_email == "support@evosyspro.live", si.from_email)


# ── 5. no secret ever leaves ────────────────────────────────────────────────

print("\n[5] A DIAGNOSTIC MAY REPORT PRESENCE, NEVER A VALUE")

d = pi.identity_for_org(db, OWN_DOMAIN).as_dict()
check("5. the dict reports that a key exists",
      d.get("resend_api_key_set") is True)
check("5. AND NEVER CONTAINS THE KEY ITSELF",
      "re_org_key_not_real" not in str(d), d)

# ── 6. the business a family sees is not the platform ───────────────────────

print("\n[6] THE FAMILY SEES THEIR FUNERAL HOME, NOT THE PLATFORM")

check("6. the customer-facing name is the ORGANIZATION's",
      g.customer_facing_name == "Greenland Cemetery and Funeral Home",
      g.customer_facing_name)
check("6. and the platform's name is kept separately, not shown to them",
      g.brand_name == "EvoSys Pro" and g.brand_name != g.customer_facing_name)
check("6. a rival brand's customer sees ITS OWN business name",
      r.customer_facing_name == "A BookaBoost Customer", r.customer_facing_name)

# The bug this prevents: the booking confirmation used get_brand_name(), which
# returns the PLATFORM, in both the From name and the signature.
check("6. THE PLATFORM NAME IS NEVER THE CUSTOMER-FACING NAME",
      g.customer_facing_name != g.brand_name
      and r.customer_facing_name != r.brand_name)


# ── 7. reply-to and cc are opt-in, never inherited ──────────────────────────

print("\n[7] NOTHING IS COPIED ANYWHERE UNLESS SOMEBODY SET IT")

check("7. an org with no reply-to gets none",
      g.reply_to_email is None, g.reply_to_email)
check("7. AN ORG WITH NO CC GETS NONE - blank means blank",
      g.cc_email is None, g.cc_email)
check("   and the identity says they are unset rather than guessing",
      g.source.get("reply_to_email") == "unset"
      and g.source.get("cc_email") == "unset", g.source)

org_row = db.query(Organization).filter(Organization.id == GREENLAND).first()
org_row.reply_to_email = "michael.simmons@nsmg.com"
db.commit()
g2 = pi.identity_for_org(db, GREENLAND)
check("7. a set reply-to is carried",
      g2.reply_to_email == "michael.simmons@nsmg.com", g2.reply_to_email)
check("   it does NOT become the from address",
      g2.from_email == "support@evosyspro.live", g2.from_email)
check("   and setting a reply-to does not invent a cc",
      g2.cc_email is None, g2.cc_email)
si2 = pi.sending_identity_for_org(db, GREENLAND)
check("7. the mailer adapter carries reply-to and cc through",
      si2.reply_to_email == "michael.simmons@nsmg.com" and si2.cc_email is None)

# A different brand's org must not pick up the reply-to just set above.
check("7. ONE ORG'S REPLY-TO DOES NOT LEAK TO ANOTHER",
      pi.identity_for_org(db, RIVAL).reply_to_email is None)

db.close()

print("\n%d checks, %d failure(s)" % (checks, len(failures)))
if failures:
    print("FAILED:")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("ALL PUBLIC IDENTITY CHECKS PASSED")
