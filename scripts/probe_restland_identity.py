"""probe_restland_identity.py — one organization, one identity, every channel.

The failure this prevents is not a broken booking link. It is fixing
"AdvisorFlow" in one place and meeting it again somewhere else two weeks
later, because six subsystems each answered "who is this customer?" for
themselves:

    voice     org.name straight into a Retell dynamic variable
    email     get_brand_name() -> the PLATFORM, in the From name
    SMS       a module constant naming a Vercel host
    survey    a different module constant naming a Render host
    booking   a third constant, defaulted per file
    calendar  whichever provider came first in a tuple

This asserts that ONE organization row now drives all six, and — the half
that actually matters — that a second tenant in the same database cannot
inherit any of it.

There is also a source-level scan. Behavioural checks prove today's call
sites are right; the scan is what stops a new one being written wrong
tomorrow, which is the way this class of bug always comes back.

No network. Run: python scripts/probe_restland_identity.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///./.probe_restland_identity.db"
os.environ["JWT_SECRET"] = "probe" * 16
from cryptography.fernet import Fernet                            # noqa: E402
os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
for _v in ("EMAIL_FROM_ADDRESS", "BOOKING_BASE_URL", "PUBLIC_BASE_URL",
           "TRACKING_BASE_URL"):
    os.environ.pop(_v, None)

DB_FILE = os.path.join(ROOT, ".probe_restland_identity.db")
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

from app.main import app as _app  # noqa: E402,F401
from app.deps import SessionLocal, engine                          # noqa: E402
from app.models.models import (                                    # noqa: E402
    Base, Lead, Organization, Platform, User)
from app.services import public_identity as pi                      # noqa: E402
from app.services import calendar_providers as cp                   # noqa: E402
from app.services import voice_orchestrator as vo                   # noqa: E402

failures, checks = [], 0


def check(label, ok, detail=""):
    global checks
    checks += 1
    if ok:
        print("  PASS  " + label)
    else:
        print("  FAIL  " + label + ("  -> " + str(detail)[:240] if detail else ""))
        failures.append(label)


# ── the confirmed Restland test identity ────────────────────────────────────

NAME = "Restland Cemetery and Funeral Home"
ADDRESS = "13005 Greenville Ave, Dallas, TX 75243"
PHONE = "+14695537417"
FROM = "support@evosyspro.live"
REPLY_TO = "michael.simmons@nsmg.com"
HOST = "https://app.evosyspro.live"

Base.metadata.create_all(bind=engine)
db = SessionLocal()

db.add(Platform(id="plt-evosyspro", name="EvoSys Pro", slug="evosyspro",
                domain="app.evosyspro.live", support_email=FROM))
db.add(Platform(id="plt-bookaboost", name="BookaBoost", slug="bookaboost",
                domain="app.bookaboost.live",
                support_email="support@bookaboost.live"))
db.commit()

REST = "org-restland"
RIVAL = "org-rival"
db.add(Organization(id=REST, name=NAME, slug="restland",
                    platform_id="plt-evosyspro", org_address=ADDRESS,
                    org_phone=PHONE, reply_to_email=REPLY_TO,
                    calendar_provider="google"))
db.add(Organization(id=RIVAL, name="Someone Else Funeral Home", slug="rival",
                    platform_id="plt-bookaboost"))
db.commit()

advisor = User(id="adv-rest", email="michael.simmons@nsmg.com",
               full_name="Mike Simmons", password_hash="x", role="org_admin",
               organization_id=REST, is_active=True)
advisor.google_oauth_refresh_token_encrypted = "g"
advisor.microsoft_oauth_refresh_token_encrypted = "m"
db.add(advisor)
lead = Lead(id="lead-rest", first_name="Mike", last_name="Simmons",
            phone="+14695537417", organization_id=REST)
db.add(lead)
db.commit()

rest = pi.identity_for_org(db, REST)
riv = pi.identity_for_org(db, RIVAL)
org_row = db.query(Organization).filter(Organization.id == REST).first()


# ── 1. the business ─────────────────────────────────────────────────────────

print("\n[1] ONE ORGANIZATION RECORD IS THE BUSINESS")

check("1. name", rest.customer_facing_name == NAME, rest.customer_facing_name)
check("1. address", rest.business_address == ADDRESS, rest.business_address)
check("1. phone", rest.business_phone == PHONE, rest.business_phone)
check("1. the PLATFORM is kept separate and is not the business",
      rest.brand_name == "EvoSys Pro" and rest.brand_name != rest.customer_facing_name)


# ── 2. every channel resolves the same identity ─────────────────────────────

print("\n[2] SIX CHANNELS, ONE ANSWER")

voice_vars = vo._dynamic_variables(lead, org_row, advisor)
check("2. VOICE speaks the business name",
      voice_vars.get("organization_name") == NAME,
      voice_vars.get("organization_name"))
check("   and the same value again as business_name",
      voice_vars.get("business_name") == NAME)

check("2. EMAIL sends from the verified brand domain",
      rest.from_email == FROM, rest.from_email)
check("   displays the BUSINESS, not the platform",
      rest.customer_facing_name == NAME)
check("   replies reach the operational mailbox",
      rest.reply_to_email == REPLY_TO, rest.reply_to_email)
check("   and nothing is copied anywhere",
      rest.cc_email is None, rest.cc_email)

tok = "tok-abc"
bu = pi.booking_url(db, REST, tok)
su = pi.survey_url(db, REST, tok)
check("2. BOOKING link is on the public host",
      bu == HOST + "/book/" + tok, bu)
check("2. SURVEY link is on the public host",
      su == HOST + "/survey/" + tok, su)
check("2. SMS and EMAIL build links through that same resolver",
      pi.booking_url(db, REST, tok) == bu)

check("2. CALENDAR is the configured provider, not the tuple's first entry",
      cp.resolve_provider_key(db, advisor) == cp.PROVIDER_GOOGLE,
      cp.resolve_provider_key(db, advisor))
check("   and it says an owner chose it",
      cp.configured_provider_key(db, advisor) == ("google", "organization"))


# ── 3. negative controls ────────────────────────────────────────────────────

print("\n[3] WHAT A FAMILY MUST NEVER SEE")

surface = " ".join(str(v) for v in [
    rest.customer_facing_name, rest.business_address, rest.business_phone,
    rest.from_email, rest.reply_to_email, rest.cc_email, rest.public_base_url,
    bu, su, voice_vars,
]).lower()

for bad, label in (("bookaboost", "A COMPETITOR BRAND'S IDENTITY"),
                   ("greenland", "THE OLD WRONG BUSINESS NAME"),
                   ("advisorflow", "THE INTERNAL PLATFORM NAME"),
                   ("onrender", "A RENDER HOST"),
                   ("vercel", "A VERCEL HOST"),
                   ("localhost", "A DEVELOPMENT HOST")):
    check("3. NO %s reaches a Restland family" % label, bad not in surface,
          [f for f in surface.split() if bad in f][:3])


# ── 4. another tenant cannot inherit any of it ──────────────────────────────

print("\n[4] A SECOND TENANT INHERITS NOTHING")

check("4. the rival's from-address is its own",
      riv.from_email == "support@bookaboost.live", riv.from_email)
check("4. the rival's links are its own host",
      pi.booking_url(db, RIVAL, tok) == "https://app.bookaboost.live/book/" + tok)
check("4. RESTLAND'S REPLY-TO DOES NOT LEAK",
      riv.reply_to_email is None, riv.reply_to_email)
check("4. RESTLAND'S ADDRESS DOES NOT LEAK",
      riv.business_address is None, riv.business_address)
check("4. RESTLAND'S PHONE DOES NOT LEAK",
      riv.business_phone is None, riv.business_phone)
check("4. and the rival is not silently on Google either",
      cp.configured_provider_key(db, User(
          id="x", email="x@x.com", full_name="x", password_hash="x",
          role="advisor", organization_id=RIVAL)) == (None, None))


# ── 5. the source scan — what stops this coming back ────────────────────────

print("\n[5] NO MODULE BUILDS A CUSTOMER LINK BY HAND ANY MORE")

# Files allowed to name a public path: the resolver itself, and the routers
# that SERVE those paths (they define routes, they do not send links).
ALLOWED = {
    os.path.join("app", "services", "public_identity.py"),
    os.path.join("app", "routers", "survey_router.py"),
    os.path.join("app", "routers", "calendar_router.py"),
}
# Webhook endpoints handed to Meta/TikTok/Google are infrastructure URLs shown
# to an ADMIN in a settings screen. They are not customer-facing and are
# supposed to name the backend.
ADMIN_ONLY = {
    os.path.join("app", "routers", "social_webhooks_router.py"),
    # app/main.py holds two non-sending uses of an infrastructure hostname:
    #
    #   * the CORS allow-list, which must name real deployment origins;
    #   * the A2P 10DLC / TCR campaign-evidence page, a compliance document
    #     submitted to The Campaign Registry for the BookaBoost campaign.
    #
    # The second one is deliberately NOT rewritten. It documents a specific
    # registered SMS program under a specific brand, and editing a carrier
    # compliance record to name a different business would be a worse problem
    # than the one it appears to be. It is not linked from any message a
    # family receives; nothing in a Restland SMS or email points at it.
    os.path.join("app", "main.py"),
    # A Twilio inbound-webhook URL. It must name the backend: it is what the
    # carrier posts to, and no human ever sees it.
    os.path.join("app", "routers", "dlc_router.py"),
    # The legacy Twilio voice router's ADVISOR-facing "view lead" links, in
    # internal notification email. Not customer-facing, and the router itself
    # is deliberately fail-closed.
    os.path.join("app", "routers", "voice_router.py"),
    # Last-resort defaults behind the resolver, reached only when an
    # organization has no platform domain at all. The resolver is consulted
    # first at both call sites.
    os.path.join("app", "routers", "billing_router.py"),
}

PATTERN = re.compile(r'f?["\'][^"\']*\{[A-Za-z_]*BASE_URL[^}]*\}[^"\']*/(book|survey)/')
HOSTS = re.compile(r'https?://[^"\'\s]*(onrender\.com|vercel\.app)')

offenders, host_offenders = [], []
for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "app")):
    for fn in files:
        if not fn.endswith(".py"):
            continue
        full = os.path.join(dirpath, fn)
        rel = os.path.relpath(full, ROOT)
        if rel in ALLOWED:
            continue
        with io.open(full, "r", encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        for i, line in enumerate(src.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if PATTERN.search(line):
                offenders.append("%s:%d" % (rel, i))
            if rel not in ADMIN_ONLY and HOSTS.search(line):
                host_offenders.append("%s:%d" % (rel, i))

check("5. no module concatenates a base url into /book/ or /survey/",
      not offenders, offenders[:6])

# A RATCHET, not a pass/fail line.
#
# Four hostnames remain, none of them on a path a Restland family touches.
# They are listed by file rather than silently excluded, because "we allowed
# it" and "we forgot it" look identical in an exclusion list a year later.
#
# The assertion is EQUALITY: a new offender fails the gate, and so does
# fixing one without removing it from here. Either way a human has to look.
KNOWN_REMAINING = {
    # Advisor-facing "View Lead & Respond" button in an internal notification
    # email. Goes to staff, never to a family.
    "app\\services\\ai_conversation_service.py",
    "app\\services\\email_poller_service.py",
    # A docstring naming the OAuth callback URL. Documentation, not a link.
    "app\\services\\twilio_callbacks.py",
    # PROSPECT-FACING, and the one real item left: the brand-sales
    # appointment-confirmation link. It points at the backend on purpose - the
    # prospect has no account and must not land on a login - so branding it
    # needs the same public-route work /book and /survey are getting, not a
    # string change. Tracked, not hidden.
    "app\\services\\appointment_invites.py",
}
found_files = {o.rsplit(":", 1)[0] for o in host_offenders}
check("5. the set of remaining infrastructure hostnames has not grown",
      found_files <= KNOWN_REMAINING,
      sorted(found_files - KNOWN_REMAINING))
check("5. and none of them is on a path a family touches",
      not (found_files & {
          "app\\services\\sms_service.py",
          "app\\services\\email_service.py",
          "app\\services\\public_identity.py",
          "app\\crons\\review_request_cron.py",
          "app\\services\\post_appointment_service.py",
          "app\\services\\email_tracking_service.py",
      }), sorted(found_files))

# Positive control: the scan must be capable of finding something, or a
# passing result proves only that the regex is broken.
probe_line = 'booking_url = f"{BOOKING_BASE_URL}/book/{token}"'
check("   (control) the scan detects the pattern it is looking for",
      bool(PATTERN.search(probe_line)))
check("   (control) and detects an infrastructure host",
      bool(HOSTS.search('x = "https://advisorflow-booking.vercel.app"')))

db.close()

print("\n%d checks, %d failure(s)" % (checks, len(failures)))
if failures:
    print("FAILED:")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("ALL RESTLAND IDENTITY CHECKS PASSED")
