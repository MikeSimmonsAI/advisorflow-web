"""The Restland completion sweep, asserted.

Six defects were live at once and every one of them shared a shape: the page
and the backend each held their own opinion, and the advisor or the family
found out which was right by pressing a button.

    branded links     the resolver emitted /book/:token and the frontend
                      served no such route, so every link 404'd
    booking preview   "Include booking link" checked, no link in the preview,
                      and - for a message with no {booking_link} placeholder -
                      no link in the delivered text either
    channel matrix    a lead with a phone and no email was refused SMS,
                      because one missing field disabled every channel
    twilio sender     "no credentials configured" arrived as a failed send
                      rather than as a disabled button
    voice button      the lead page dialled a second, unused implementation
                      while the proven Retell path stayed god-only
    cold outreach     a flat 155-character cap truncated the one message that
                      has the most to say

No network, no live database. Run: python scripts/probe_sweep_public_and_compose.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///./.probe_sweep_compose.db"
os.environ["JWT_SECRET"] = "probe" * 16
from cryptography.fernet import Fernet                              # noqa: E402
os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
for _v in ("EMAIL_FROM_ADDRESS", "BOOKING_BASE_URL", "PUBLIC_BASE_URL",
           "TRACKING_BASE_URL"):
    os.environ.pop(_v, None)

DB_FILE = os.path.join(ROOT, ".probe_sweep_compose.db")
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

from app.main import app as _app                                     # noqa: E402
from app.deps import SessionLocal, engine                            # noqa: E402
from app.models.models import (                                      # noqa: E402
    Base, Lead, Organization, Platform, User)
from app.services import public_identity as pi                       # noqa: E402
from app.services import sms_service as sms                          # noqa: E402
from app.services import appointment_invites as apinv                # noqa: E402
from app.services import draft_reply_service as drs                  # noqa: E402

failures, checks = [], 0


def check(label, ok, detail=""):
    global checks
    checks += 1
    if ok:
        print("  PASS  " + label)
    else:
        print("  FAIL  " + label + ("  -> " + str(detail)[:240] if detail else ""))
        failures.append(label)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


NAME = "Restland Cemetery and Funeral Home"
PHONE = "+14695537417"
HOST = "https://app.evosyspro.live"

Base.metadata.create_all(bind=engine)
db = SessionLocal()

db.add(Platform(id="plt-evosyspro", name="EvoSys Pro", slug="evosyspro",
                domain="app.evosyspro.live", support_email="support@evosyspro.live"))
db.add(Platform(id="plt-nodomain", name="No Domain Brand", slug="nodomain"))
db.commit()

REST, BARE = "org-restland", "org-bare"
db.add(Organization(id=REST, name=NAME, slug="restland",
                    platform_id="plt-evosyspro", org_phone=PHONE))
db.add(Organization(id=BARE, name="Hostless Home", slug="bare",
                    platform_id="plt-nodomain"))
db.commit()

advisor = User(id="adv", email="michael.simmons@nsmg.com", full_name="Mike Simmons",
               password_hash="x", role="org_admin", organization_id=REST,
               is_active=True)
db.add(advisor)
phone_only = Lead(id="lead-phone", first_name="Mike", phone=PHONE,
                  organization_id=REST)
email_only = Lead(id="lead-email", first_name="Dana", email="dana@example.com",
                  organization_id=REST)
both = Lead(id="lead-both", first_name="Sam", phone="+14695550101",
            email="sam@example.com", organization_id=REST)
db.add_all([phone_only, email_only, both])
db.commit()


# ── 1. the routes the resolver has been emitting ────────────────────────────

print("\n[1] THE BRANDED PUBLIC ROUTES EXIST")

app_jsx = read("frontend/src/App.jsx")
for path, comp, file_rel in (
    ('/book/:token', 'BookingPage', 'frontend/src/pages/public/BookingPage.jsx'),
    ('/survey/:token', 'SurveyPage', 'frontend/src/pages/public/SurveyPage.jsx'),
    ('/appointments/confirm/:token', 'AppointmentConfirmPage',
     'frontend/src/pages/public/AppointmentConfirmPage.jsx'),
):
    check("1. %s is routed" % path, ('path="%s"' % path) in app_jsx)
    check("   to <%s />" % comp, ("<%s />" % comp) in app_jsx)
    check("   and the component exists",
          os.path.exists(os.path.join(ROOT, file_rel)), file_rel)

check("1. the routes are NOT behind ProtectedRoute (a family has no account)",
      not re.search(r'path="/book/:token"[^>]*ProtectedRoute', app_jsx))

booking_page = read("frontend/src/pages/public/BookingPage.jsx")
check("1. the booking page reuses the existing endpoints, not a new system",
      "/calendar/booking/" in booking_page
      and "/calendar/slots" in booking_page
      and "/calendar/booking-confirmed" in booking_page)
check("1. it sends the webhook's OWN field names",
      "booking_token" in booking_page and "slot_display" in booking_page)
check("1. it surfaces the fail-closed reason rather than showing a day as free",
      "data.reason" in booking_page)

survey_page = read("frontend/src/pages/public/SurveyPage.jsx")
check("1. the survey page reads the JSON context endpoint",
      "/context" in survey_page and "/survey/" in survey_page)


# ── 2. no infrastructure hostname can reach a customer ──────────────────────

print("\n[2] A FAMILY IS NEVER SENT TO THE PLUMBING")

check("2. booking link is the branded host",
      pi.booking_url(db, REST, "tok") == HOST + "/book/tok",
      pi.booking_url(db, REST, "tok"))
check("2. survey link is the branded host",
      pi.survey_url(db, REST, "tok") == HOST + "/survey/tok",
      pi.survey_url(db, REST, "tok"))

for host in ("https://advisorflow-booking.vercel.app",
             "https://advisorflow-backend.onrender.com",
             "http://localhost:5173"):
    check("2. %s is refused as a fallback" % host,
          pi._is_infrastructure_host(host))
check("2. a real branded host is NOT refused",
      not pi._is_infrastructure_host(HOST))

# The org with no platform domain: the env fallback is the only candidate, and
# when it is infrastructure the builder must return nothing rather than a leak.
_saved = pi.ENV_BOOKING_BASE_URL
try:
    pi.ENV_BOOKING_BASE_URL = "https://advisorflow-booking.vercel.app"
    leaked = pi.booking_url(db, BARE, "tok")
    check("2. an unbranded org gets NO link rather than a Vercel link",
          leaked == "", leaked)
    pi.ENV_BOOKING_BASE_URL = "https://book.example.com"
    ok = pi.booking_url(db, BARE, "tok")
    check("2. a legitimately configured fallback still works",
          ok == "https://book.example.com/book/tok", ok)
finally:
    pi.ENV_BOOKING_BASE_URL = _saved

check("2. a prospect's confirmation link uses the BRAND's host",
      apinv.confirm_url("tok", HOST) == HOST + "/appointments/confirm/tok",
      apinv.confirm_url("tok", HOST))
check("2. and never the API hostname",
      apinv.confirm_url("tok", "https://advisorflow-backend.onrender.com") == "",
      apinv.confirm_url("tok", "https://advisorflow-backend.onrender.com"))

sales_router = read("app/routers/sales_scheduling_router.py")
check("2. the branded confirm page has a JSON context endpoint",
      '"/appointments/confirm/{token}/context"' in sales_router)
check("2. and a separate POST to record the answer",
      '"/appointments/confirm/{token}/respond"' in sales_router)
check("2. the original HTML page is still served (live links point at it)",
      '@router.get("/appointments/confirm/{token}"' in sales_router)


# ── 3. the preview is the message ───────────────────────────────────────────

print("\n[3] WHAT THE PREVIEW SHOWS IS WHAT IS SENT")

url = pi.booking_url(db, REST, "tok")
placeholder = "Hi {first_name}, book here: {booking_link}"
typed = "Hi Mike, this is Mike Simmons with Restland."

body = sms.compose_body(placeholder, phone_only, advisor, url)
check("3. a {booking_link} placeholder is substituted",
      url in body and "{booking_link}" not in body, body)

body = sms.compose_body(typed, phone_only, advisor, url)
check("3. a message with NO placeholder still gets the link appended",
      body.endswith(url), body)
check("   and the advisor's own words are untouched",
      body.startswith(typed))

body = sms.compose_body(typed + " " + url, phone_only, advisor, url)
check("3. a link the advisor typed themselves is not duplicated",
      body.count(url) == 1, body)

body = sms.compose_body(typed, phone_only, advisor, "")
check("3. no link means no link",
      "http" not in body, body)

lead_detail = read("frontend/src/pages/LeadDetail.jsx")
check("3. the composer renders the resolved URL before Send",
      "composePreview" in lead_detail and "bookingUrl" in lead_detail)
check("3. and the page's rule matches the backend's",
      "{booking_link}" in lead_detail and "trimEnd" in lead_detail)

check("3. the preview endpoint calls the SAME compose function",
      "compose_body" in read("app/routers/compose_router.py"))
check("3. and the send path calls it too",
      read("app/services/sms_service.py").count("compose_body(") >= 3)

check("3. preview and send share one booking link, not two",
      "get_or_create_booking_link" in read("app/services/sms_service.py"))


# ── 4. one missing field disables one channel ───────────────────────────────

print("\n[4] CHANNEL CAPABILITY IS PER CHANNEL")

from fastapi.testclient import TestClient                            # noqa: E402
from app.routers import compose_router as cr                         # noqa: E402


def matrix(lead):
    """The capability logic, exercised directly - no HTTP, no auth."""
    has_phone = bool((lead.phone or "").strip())
    has_email = bool((getattr(lead, "email", None) or "").strip())
    sender = sms.describe_sms_sender(advisor, db)
    return has_phone, has_email, sender


hp, he, sender = matrix(phone_only)
check("4. phone-only lead: has phone", hp)
check("4. phone-only lead: has no email", not he)
check("4. a missing email does not appear in any SMS decision",
      "email" not in (sender.get("reason") or "").lower(),
      sender.get("reason"))

src = read("app/routers/compose_router.py")
check("4. SMS availability depends on phone + sender, never on email",
      "sms_ok = has_phone and bool(sender.get(\"ready\"))" in src)
check("4. voice availability depends on phone + orchestrator, never on email",
      "if has_phone:" in src and "check_call_eligibility" in src)
check("4. only EMAIL cites a missing email address",
      src.count('"This lead has no email address."') == 1)
check("4. 'both' needs both, and says so",
      'cap(sms_ok and email_ok' in src)

check("4. the lead page drives its pills from that matrix",
      "composeCtx?.channels" in lead_detail)
check("4. an unavailable AI channel is disabled, not merely unselected",
      "disabled={!available}" in lead_detail)
check("4. and the channel actually sent is the one in force",
      "effectiveAiChannel" in lead_detail)


# ── 5. the sender is known before Send ──────────────────────────────────────

print("\n[5] TWILIO RESOLUTION IS VISIBLE, AND TENANT-SCOPED")

s = sms.describe_sms_sender(advisor, db)
check("5. an advisor with nothing configured is reported not ready",
      s["ready"] is False and s["source"] is None, s)
check("5. the reason names both places to fix it",
      "Settings" in (s["reason"] or "") and "Org Settings" in (s["reason"] or ""),
      s["reason"])
check("5. no secret is in the payload",
      "auth_token" not in s and "twilio_auth_token_encrypted" not in s)

org = db.query(Organization).filter(Organization.id == REST).first()
org.org_twilio_account_sid = "ACorgsid0000000000000000000000000"
org.org_twilio_auth_token_encrypted = "enc"
org.org_twilio_phone_number = "+14692241155"
db.commit()
s = sms.describe_sms_sender(advisor, db)
check("5. the ORGANIZATION's shared sender is used when the advisor has none",
      s["ready"] and s["source"] == "organization"
      and s["from_number"] == "+14692241155", s)

advisor.twilio_account_sid = "ACadvsid00000000000000000000000000"
advisor.twilio_auth_token_encrypted = "enc"
advisor.twilio_phone_number = "+14695551234"
db.commit()
s = sms.describe_sms_sender(advisor, db)
check("5. an advisor's OWN number wins over the shared one",
      s["source"] == "advisor" and s["from_number"] == "+14695551234", s)

# THE PLATFORM OWNER IS NOT A MEMBER OF THE TENANT'S STAFF.
#
# A god_admin inside a customer org via X-Org-Override carries that org's id on
# the in-memory User, so every downstream reader treats them as staff. Twilio
# resolution read their PERSONAL credentials and reported the platform's own
# number as the customer's sender - green, ready, and wrong. Pressing Send
# would have texted a family from a number the funeral home does not own.
god = User(id="god-probe-1", email="owner@platform.test", full_name="Platform Owner",
           password_hash="x", role="god_admin", organization_id=REST, is_active=True,
           twilio_account_sid="ACgodsid00000000000000000000000000",
           twilio_auth_token_encrypted="enc",
           twilio_phone_number="+18449172171")
db.add(god)
db.commit()

s = sms.describe_sms_sender(god, db)
check("5. the PLATFORM OWNER's personal Twilio is never a tenant's sender",
      s["from_number"] != "+18449172171" and s["source"] != "advisor", s)
check("5. an impersonated read falls through to the ORGANIZATION's own sender",
      s["source"] == "organization" and s["from_number"] == "+14692241155", s)

_org_sid, _org_tok, _org_num = (org.org_twilio_account_sid,
                                org.org_twilio_auth_token_encrypted,
                                org.org_twilio_phone_number)
org.org_twilio_account_sid = None
org.org_twilio_auth_token_encrypted = None
org.org_twilio_phone_number = None
db.commit()
s = sms.describe_sms_sender(god, db)
check("5. an org with no sender is reported NOT ready, not papered over",
      s["ready"] is False and s["from_number"] is None, s)
check("5. and the fix is addressed to the organization, not to the platform admin",
      "This organization has no Twilio sender" in (s["reason"] or ""), s["reason"])
try:
    sms._resolve_twilio_creds(god, db)
    check("5. the SEND refuses too - it does not disagree with the screen", False,
          "no exception raised")
except ValueError:
    check("5. the SEND refuses too - it does not disagree with the screen", True)
except Exception as _exc:                                          # noqa: BLE001
    check("5. the SEND refuses too - it does not disagree with the screen", False,
          repr(_exc))
org.org_twilio_account_sid = _org_sid
org.org_twilio_auth_token_encrypted = _org_tok
org.org_twilio_phone_number = _org_num
db.commit()

# WHOSE LEAD IS IT - not who happens to be looking at it.
#
# The composer read the sender AND minted the booking link off the CALLER. A
# link minted while the platform owner had the lead open named the OWNER's
# calendar, so a family clicking it would have booked time with the platform
# rather than with the funeral home.
from app.routers.compose_router import acting_advisor                  # noqa: E402
from app.services import sms_service as _sms                           # noqa: E402

phone_only.assigned_to_id = advisor.id
db.commit()
check("5. the composer acts as the lead's ASSIGNED advisor, not the caller",
      acting_advisor(db, phone_only, god).id == advisor.id)
check("5. an unassigned lead still falls back to the caller",
      acting_advisor(db, email_only, advisor).id == advisor.id)

_link = _sms.get_or_create_booking_link(db, phone_only,
                                        acting_advisor(db, phone_only, god))
check("5. a booking link minted under impersonation names the ADVISOR's calendar",
      _link.user_id == advisor.id, _link.user_id)
check("5. and never the platform owner's", _link.user_id != god.id)

compose_src = read("app/routers/compose_router.py")
check("5. no call site in the composer still passes the raw caller",
      "describe_sms_sender(user, db)" not in compose_src
      and "get_or_create_booking_link(db, lead, user)" not in compose_src
      and "compose_body(req.template or \"\", lead, user, url)" not in compose_src)

svc = read("app/services/sms_service.py")
check("5. the screen and the send share ONE platform-owner predicate",
      svc.count("_is_platform_owner(advisor)") >= 2)
check("5. resolution order is advisor -> organization -> unavailable",
      svc.index("Advisor's personal Twilio credentials") <
      svc.index("Org-level shared credentials"))
check("5. NO cross-tenant fallback: only this advisor's own organization is read",
      "Organization.id == advisor.organization_id" in svc)
check("5. the warning is shown, not hidden",
      "smsSender && !smsSender.ready" in lead_detail)


# ── 6. one voice implementation ─────────────────────────────────────────────

print("\n[6] THE LEAD PAGE USES THE PROVEN VOICE PATH")

voice_router = read("app/routers/voice_router.py")
call_ep = voice_router[voice_router.index('@router.post("/call/{lead_id}")'):]
call_ep = call_ep[:call_ep.index('@router.post("/twiml/')]
check("6. the call endpoint delegates to the orchestrator",
      "start_file_check_call" in call_ep and "check_call_eligibility" in call_ep)
check("6. it no longer dials Twilio directly",
      "initiate_outbound_call" not in call_ep)
check("6. it no longer builds a TwiML/WebSocket call itself",
      "twiml_url" not in call_ep and "wss://" not in call_ep)
check("6. a refusal is a 409 carrying the orchestrator's own reason",
      "status_code=409" in call_ep and "elig.reason" in call_ep)
check("6. a provider failure is a 502 carrying the provider's message",
      "status_code=502" in call_ep)
check("6. readiness can be asked before the button is pressed",
      '@router.get("/readiness/{lead_id}")' in voice_router)
check("6. readiness uses the SAME eligibility function as the call",
      voice_router.count("check_call_eligibility") >= 2)

check("6. the page resets its loading state on every path",
      "setCalling(false)                        // resets on EVERY path" in lead_detail)
check("6. and shows the real failure instead of a network message",
      "callError" in lead_detail)


# ── 7. async controls behave ────────────────────────────────────────────────

print("\n[7] ASYNC CONTROLS")

for fn, guard in (("handleSend", "sending) return"),
                  ("handleSendEmail", "sendingEmail) return"),
                  ("handleSuggestReply", "if (suggestingReply) return"),
                  ("handleSuggestEmail", "if (suggestingReply) return"),
                  ("handleCall", "if (calling) return"),
                  ("handleStartAiConversation", "if (aiConvLoading) return")):
    body = lead_detail[lead_detail.index("function %s(" % fn):]
    body = body[:2000]
    check("7. %s guards double submit" % fn, guard in body)
    check("   and exits its loading state in a finally block", "finally {" in body)

# Comments are stripped first: a note explaining what alert() used to do is not
# a call to alert(), and a probe that cannot tell them apart teaches people to
# stop writing the note.
_ld_live = re.sub(r'/\*.*?\*/', '', lead_detail, flags=re.S)
_ld_live = "\n".join(l for l in _ld_live.splitlines()
                     if not l.strip().startswith("//"))
check("7. alert() is gone from the lead page",
      not re.search(r'(?<![\w.])alert\(', _ld_live),
      re.findall(r'.{0,40}(?<![\w.])alert\(.{0,40}', _ld_live))
check("7. the app has a real notice surface",
      os.path.exists(os.path.join(ROOT, "frontend/src/components/Toast.jsx")))
check("7. mounted above the router so every page can use it",
      "<ToastProvider>" in app_jsx)


# ── 8. one phone formatter ──────────────────────────────────────────────────

print("\n[8] PHONE DISPLAY, WITHOUT TOUCHING STORAGE")

check("8. the shared formatter exists",
      os.path.exists(os.path.join(ROOT, "frontend/src/utils/phone.js")))
phone_js = read("frontend/src/utils/phone.js")
check("8. E.164 renders as +1 (469) 553-7417",
      "+1 (${digits.slice(1, 4)}) ${digits.slice(4, 7)}-${digits.slice(7)}" in phone_js)
check("8. the backend's formatter agrees",
      cr._fmt_phone(PHONE) == "+1 (469) 553-7417", cr._fmt_phone(PHONE))
check("8. ten digits render without a country code",
      cr._fmt_phone("4695537417") == "(469) 553-7417", cr._fmt_phone("4695537417"))
check("8. an unparseable value is left alone rather than mangled",
      cr._fmt_phone("ext 4321") == "ext 4321", cr._fmt_phone("ext 4321"))
for rel in ("frontend/src/pages/Leads.jsx",
            "frontend/src/pages/LeadDetail.jsx",
            "frontend/src/pages/CaseFile.jsx"):
    check("8. %s uses it" % os.path.basename(rel),
          "formatPhone" in read(rel))
check("8. the STORED value is untouched - the formatter is display-only",
      db.query(Lead).filter(Lead.id == "lead-phone").first().phone == PHONE)


# ── 9. temperature changes strategy, not adjectives ─────────────────────────

print("\n[9] COLD OUTREACH SAYS WHO IS TEXTING")

check("9. each temperature carries a strategy, not just a tone",
      set(drs.TONE_STRATEGY) == {"cold", "warm", "hot", "urgent"})
cold = drs.TONE_STRATEGY["cold"]
check("9. cold must introduce the advisor and the business",
      "Introduce the advisor BY NAME" in cold["goal"]
      and "which business" in cold["goal"])
check("9. cold must state there is no obligation",
      "no obligation" in cold["goal"])
check("9. cold must NOT imply a prior relationship",
      "following up" in cold["goal"] and "must NOT" in cold["goal"])
check("9. cold has room to say all of it",
      cold["max_chars"] >= 300, cold["max_chars"])
check("9. hot stays short - it has less to say",
      drs.TONE_STRATEGY["hot"]["max_chars"] < cold["max_chars"])

fb = drs._fallback_reply(phone_only, advisor, "", "cold", NAME)
check("9. the cold fallback names the advisor", "Mike Simmons" in fb, fb)
check("9. and names the business", NAME in fb, fb)
check("9. and offers no-pressure help",
      "no pressure" in fb.lower() or "no obligation" in fb.lower(), fb)
check("9. and claims no prior contact",
      not re.search(r"following up|checking back|as we discussed|again", fb, re.I), fb)
check("9. it fits the cold budget", len(fb) <= cold["max_chars"], len(fb))

drs_src = read("app/services/draft_reply_service.py")
check("9. the advisor's free-text direction overrides the defaults",
      "It OVERRIDES the defaults above" in drs_src)
check("9. the flat 155-character truncation is gone",
      "[:155]" not in drs_src)
check("9. the cap now follows the strategy",
      "if len(suggested) > max_chars:" in drs_src)
check("9. the business name comes from the resolver, not the account name",
      "customer_facing_name" in drs_src)
check("9. no advisor is hard-coded into the prompt",
      "Mike" not in drs_src.replace("# ", ""), "a name is hard-coded")


# ── 10. no tenant's details in shared code ──────────────────────────────────

print("\n[10] SHARED CODE NAMES NO CUSTOMER AND NO PERSON")

SHARED = ("app/routers/calendar_router.py",
          "app/services/email_poller_service.py",
          "app/services/ai_conversation_service.py")
for rel in SHARED:
    body = read(rel)
    live = "\n".join(l for l in body.splitlines()
                     if not l.strip().startswith("#"))
    check("10. %s does not email one named operator" % os.path.basename(rel),
          "michael.simmons@nsmg.com" not in live, rel)
    check("     it notifies the advisor who owns the record",
          'getattr(advisor, "email", None)' in live
          or "getattr(advisor, 'email', None)" in live, rel)

cal = read("app/routers/calendar_router.py")
live_cal = "\n".join(l for l in cal.splitlines() if not l.strip().startswith("#"))
check("10. one customer's street address is not the fallback for every tenant",
      "13005 Greenville Ave" not in live_cal)
check("10. the family's confirmation SMS signs the BUSINESS, not the platform",
      "_fam_ident.customer_facing_name" in cal)


# ── 11. the booking webhook is replay-safe ──────────────────────────────────

print("\n[11] CONFIRMING TWICE DOES NOT BOOK TWICE")

check("11. a replay at the same time returns without side effects",
      "idempotent_replay" in cal)
check("11. a DIFFERENT time is still treated as a reschedule",
      "_same_slot" in cal and "reschedule" in cal)
check("11. the configured calendar provider decides where the event goes",
      "configured_provider_key" in cal and "_provider_allowed" in cal)
check("11. Microsoft cannot win silently when Google is configured",
      '_provider_allowed("microsoft")' in cal and '_provider_allowed("google")' in cal)


# ── 12. Twilio gets TwiML, not JSON ─────────────────────────────────────────

print("\n[12] TWILIO 12300: THE REPLY IS THE RIGHT SHAPE")

sms_router = read("app/routers/sms_router.py")
inbound = sms_router[sms_router.index('@router.post("/webhook/inbound")'):]
inbound = inbound[:inbound.index('@router.patch("/replies/')]
status_cb = sms_router[sms_router.index('@router.post("/webhook/status-callback")'):]
status_cb = status_cb[:status_cb.index('@router.post("/webhook/inbound")')]

check("12. the inbound webhook answers with TwiML on every path",
      "return {" not in inbound and inbound.count("_twiml_ack()") >= 3,
      inbound.count("_twiml_ack()"))
check("12. the status callback answers with TwiML on every path",
      "return {" not in status_cb and status_cb.count("_twiml_ack()") >= 2,
      status_cb.count("_twiml_ack()"))
check("12. the content type is XML, which is what 12300 was about",
      'media_type="application/xml"' in sms_router)
check("12. the body is a valid, EMPTY TwiML document",
      "<Response></Response>" in sms_router)

import xml.etree.ElementTree as _ET                                  # noqa: E402
from app.routers import sms_router as smsr                           # noqa: E402
_ack = smsr._twiml_ack()
check("12. it really parses as XML",
      _ET.fromstring(_ack.body.decode()).tag == "Response")
check("12. and sends no message back to the family",
      _ET.fromstring(_ack.body.decode()).find("Message") is None)
check("12. served as an XML content type",
      "xml" in (_ack.media_type or ""), _ack.media_type)

# The security and business behaviour this must NOT have disturbed.
check("12. per-account signature verification still runs first",
      "await guard_inbound(" in inbound or "guard_inbound(request" in inbound,
      "guard missing")
check("12. the status callback is still authenticated",
      "guard_status_callback(request, db)" in status_cb)
check("12. STOP still reaches the suppression list",
      "add_suppression_entry_from_reply" in inbound)
check("12. a reply still stops the cadence",
      "stop_cadence_for_lead" in inbound)
check("12. an unrecognised number still refuses a cross-org lookup",
      "no matching" in inbound.lower() and "cross-org" in inbound.lower())


# ── 13. cleanup is narrow, dry-run first, and silent ────────────────────────

print("\n[13] CLEANUP TOUCHES ONE RECORD AND TELLS NOBODY")

maint = read("app/routers/god_maintenance_router.py")

check("13. it is god-only",
      "Depends(require_god)" in maint and maint.count("require_god") >= 3)
check("13. the caller must name ONE booking by id",
      "booking_id: str" in maint and "apply: bool = False" in maint)
check("13. it DEFAULTS to a dry run",
      "apply: bool = False" in maint and 'if not req.apply:' in maint)
check("13. the caller must also state the organization",
      "organization_id: str" in maint)
check("13. and a booking from another organization is refused",
      "owning_org != req.organization_id" in maint and "Refusing" in maint)

# The whole point: none of the messaging helpers may be reachable from here.
#
# Tested against the module's actual IDENTIFIERS, not its text. The docstring
# has to name `on_booking_cancelled` in order to explain why this endpoint does
# NOT call it, and a probe that cannot tell an explanation from a call would
# force that explanation to be deleted - which is exactly the comment a future
# reader most needs.
import ast as _ast                                                   # noqa: E402
_maint_ast = _ast.parse(maint)
_maint_names = set()
for _n in _ast.walk(_maint_ast):
    if isinstance(_n, _ast.Name):
        _maint_names.add(_n.id)
    elif isinstance(_n, _ast.Attribute):
        _maint_names.add(_n.attr)
    elif isinstance(_n, _ast.alias):
        _maint_names.add((_n.asname or _n.name).split(".")[-1])
    elif isinstance(_n, _ast.ImportFrom):
        _maint_names.add(_n.module or "")

for forbidden in ("on_booking_cancelled", "send_sms", "send_mms",
                  "send_email_via_provider", "Emails", "TwilioClient", "Client",
                  "start_cadence", "restart_cadence", "notify_hot_reply"):
    check("13. it cannot call %s" % forbidden, forbidden not in _maint_names)
check("13. the one helper it does use is the communication-free one",
      "cancel_calendar_event" in _maint_names)

check("13. nothing is deleted - rows are marked",
      "DELETE FROM" not in maint.upper() and "db.delete(" not in maint)
check("13. the cadence is explicitly left alone",
      "no cadence restart" in maint)
check("13. an apply is audited under the god admin's own identity",
      "GOD_BOOKING_CLEANUP_APPLIED" in maint and "god.email" in maint)
check("13. a dry run is audited too",
      "GOD_BOOKING_CLEANUP_DRYRUN" in maint)

from app.routers import god_maintenance_router as gm                 # noqa: E402
check("13. +14695537417 and 4695537417 are recognised as one number",
      gm._same_number("+14695537417", "4695537417"))
check("13. and so is (469) 553-7417",
      gm._same_number("+14695537417", "(469) 553-7417"))
check("13. a different number is not matched",
      not gm._same_number("+14695537417", "+18435328405"))
check("13. an empty value never matches anything",
      not gm._same_number("", "+14695537417")
      and not gm._same_number("+14695537417", None))

check("13. the phone audit is read-only and says so",
      '"read_only": True' in maint)
check("13. it reports ownership rather than merging records",
      "never merged" in maint or "never inferred and never merged" in maint)


# ── 14. calendar management, and disconnect that fails closed ───────────────

print("\n[14] AN ADVISOR CAN SEE AND UNDO THEIR OWN CALENDAR")

conn_router = read("app/routers/calendar_connections_router.py")
main_py = read("app/main.py")

# Comments stripped for the same reason as elsewhere: the note above `_caller`
# has to name `require_sales_member` to record what the dependency used to be.
_conn_live = "\n".join(l for l in conn_router.splitlines()
                       if not l.strip().startswith("#"))
_conn_live = re.sub(r'"""(?:.|\n)*?"""', '', _conn_live)
check("14. the endpoints are reachable by an org advisor, not only sales",
      "_caller = get_current_user" in conn_router
      and "require_sales_member" not in _conn_live)
check("14. mounted for the Sales Workspace as before",
      'prefix="/sales/calendar"' in main_py)
check("14. and mounted once more for everyone else",
      'prefix="/me/calendar"' in main_py)
check("14. it is ONE router, not a second implementation",
      main_py.count("calendar_connections_router)") == 0
      and main_py.count("app.include_router(calendar_connections_router,") == 2)

check("14. every provider is listed, connected or not",
      "for p in PROVIDERS" in conn_router)
check("14. the account email is reported",
      '"account_email"' in conn_router)
check("14. 'has a token' is reported separately from 'can read the calendar'",
      '"has_token"' in conn_router and '"calendar_scope_ok"' in conn_router)
check("14. the CONFIGURED provider is reported next to the ACTIVE one",
      '"configured_provider"' in conn_router and '"active_provider"' in conn_router)

disc = conn_router[conn_router.index('@router.post("/connections/{provider}/disconnect")'):]
check("14. disconnect affects only the named provider",
      "_token_field(provider)" in disc
      and "CalendarConnection.provider == provider" in disc)
check("14. it clears that provider's credentials",
      "setattr(user, _token_field(provider), None)" in disc)
check("14. it marks the connection inactive",
      "conn.is_connected = False" in disc)
check("14. it only ever touches the CALLER's own rows",
      disc.count("user.id") >= 2 and "organization_id" not in disc)
check("14. FAILS CLOSED: disconnecting the configured provider warns rather "
      "than switching to the other one",
      "configured == provider" in disc and '"warning"' in disc)
check("14. and does not silently reassign the configured provider",
      "calendar_provider =" not in disc)
check("14. historical appointments keep their event ids",
      "external_event_id" in disc and "deliberately" in disc)

settings_jsx = read("frontend/src/pages/Settings.jsx")
check("14. the UI confirms before disconnecting",
      "window.confirm(" in settings_jsx and "Disconnect ${c.label}" in settings_jsx)
check("14. and warns when it is the calendar scheduling uses",
      "availability will be reported as unavailable" in settings_jsx)
check("14. the panel offers connect, reconnect, test and disconnect",
      "connectCalendarProvider" in settings_jsx and "testCalendar" in settings_jsx
      and "disconnectCalendar" in settings_jsx)
check("14. it reads state from the backend rather than guessing from a token",
      "/me/calendar/connections" in settings_jsx)


# ── 15. navigation says what things are ─────────────────────────────────────

print("\n[15] NAVIGATION")

layout = read("frontend/src/components/Layout.jsx")
# Comments stripped: the block above NAV_GROUPS has to quote both old labels in
# order to record WHY they were renamed, and a probe that reads prose as code
# would make that note impossible to write.
layout_live = re.sub(r'/\*.*?\*/', '', layout, flags=re.S)
layout_live = "\n".join(l for l in layout_live.splitlines()
                        if not l.strip().startswith("//"))

check("15. the sidebar is grouped",
      "NAV_GROUPS" in layout)
for group in ("Workspace", "Engagement", "Operations", "Administration"):
    check("15. group %s exists" % group, "label: '%s'" % group in layout)

check("15. 'Master Dashboard' is gone - it was never platform-wide",
      "Master Dashboard" not in layout_live)
check("15. renamed to what it actually shows, this org's team",
      "'Team Performance'" in layout)
check("15. it still points at the same org-scoped route",
      "to: '/admin', label: 'Team Performance'" in layout)

check("15. 'Branding & Settings' no longer wraps into its neighbour",
      "Branding & Settings" not in layout_live)
check("15. the two settings entries are distinguishable",
      "'My Settings'" in layout and "'Organization'" in layout)

layout_css = read("frontend/src/components/Layout.css")
check("15. and a long label can no longer wrap at all",
      "white-space: nowrap" in layout_css and "text-overflow: ellipsis" in layout_css)

check("15. visibility rules are unchanged - admin items still need admin",
      "item.adminOnly && !isOrgAdmin" in layout)
check("15. and feature flags are still honoured",
      "isFeatureEnabled(item.featureKey)" in layout)
check("15. an empty group renders nothing rather than a bare heading",
      "if (items.length === 0) return null" in layout)
check("15. platform functions stay in Platform Admin",
      "SUPER_ADMIN_NAV_ITEMS" in layout and "Platform Admin" in layout)

# Every route the sidebar points at must exist in the router, or the tidy-up
# has quietly created a dead menu item.
app_routes = set(re.findall(r'path="([^"]+)"', app_jsx))
nav_targets = set(re.findall(r"to: '([^']+)'", layout))
missing = sorted(t for t in nav_targets
                 if t not in app_routes and t != "/" )
check("15. every sidebar destination is a real route", not missing, missing)


# ── 16. a family sees the funeral home, never the platform ──────────────────

print("\n[16] THE PUBLIC PAGES ARE BRANDED AS THE CUSTOMER")

import json                                                             # noqa: E402
from app.services.public_identity import public_branding                # noqa: E402

org.brand_logo_url = "https://cdn.restland.example/logo.png"
org.org_address = "13005 Greenville Ave, Dallas, TX 75243"
org.org_phone = "+12143279201"
org.brand_color_primary = "#1f4e79"
db.commit()

b = public_branding(db, REST)
check("16. the branding block names the BUSINESS", b["name"] == NAME, b["name"])
check("16. it carries the customer's own logo, address and phone",
      b["logo_url"].endswith("logo.png") and "Greenville" in b["address"]
      and b["phone"], b)
check("16. it names a document title, so the tab is not the platform's",
      b["document_title"] == NAME, b["document_title"])
check("16. the PLATFORM's name is not in the payload at all",
      "brand_name" not in b and "EvoSys" not in json.dumps(b), b)
check("16. every field says which level answered it",
      b["source"].get("customer_facing_name", "").startswith("organization"),
      b["source"])

# An unbranded organization must yield emptiness, not a platform default -
# a blank header is a gap, a header saying "EvoSys Pro" is a leak.
bare = public_branding(db, BARE)
check("16. an unbranded org gets NO logo rather than the platform's",
      bare["logo_url"] == "")
check("16. and its title never falls back to the platform",
      "EvoSys" not in (bare["document_title"] or ""), bare["document_title"])

def code_only(src):
    """The file with comments and docstrings removed.

    An absence check against raw source is a check against the EXPLANATION as
    well as the code, so the only way to keep it passing is to delete the
    comment that says why the old approach was wrong. That trade is never worth
    making; strip the prose instead.
    """
    out = []
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        out.append(line.split("  # ")[0])
    return "\n".join(out)


cal_src = read("app/routers/calendar_router.py")
check("16. the booking endpoint resolves the org from the LEAD, not a column "
      "booking_links does not have",
      'getattr(lead, "organization_id", None)' in cal_src
      and 'hasattr(booking, "organization_id")' not in code_only(cal_src))
check("16. and returns the resolved branding block",
      '"branding": _branding' in cal_src)

srv_src = read("app/routers/survey_router.py")
check("16. the survey endpoint returns the same block",
      '"branding": branding' in srv_src)

for page, tag in (("frontend/src/pages/public/BookingPage.jsx", "booking"),
                  ("frontend/src/pages/public/SurveyPage.jsx", "survey"),
                  ("frontend/src/pages/public/AppointmentConfirmPage.jsx", "confirm")):
    src = read(page)
    check("16. the %s page sets document.title from the resolved name" % tag,
          "document.title = resolvedTitle" in src, page)
    check("16.    and restores it on unmount rather than leaking across pages",
          "document.title = previous" in src, page)

book_src = read("frontend/src/pages/public/BookingPage.jsx")
check("16. the booking page renders the customer's logo when there is one",
      "businessLogo" in book_src and "S.logo" in book_src)


# ── 17. the booking stays on the family we called ───────────────────────────

print("\n[17] A SPOKEN CALLBACK NUMBER DOES NOT REKEY THE APPOINTMENT")

from app.services.tenant_scheduling import (record_callback_phone,   # noqa: E402
                                            _same_number)

check("17. two spellings of one number are one number",
      _same_number("+18435328405", "18435328405")
      and _same_number("843-532-8405", "+18435328405"))
check("17. and two different numbers are not",
      not _same_number("+18435328405", "+14695537417"))

called = phone_only                      # the lead EvoSys dialled
called.callback_phone = None
db.commit()

stored = record_callback_phone(called, "843-532-8405")
check("17. a number spoken on the call is stored as a CALLBACK",
      called.callback_phone and _same_number(called.callback_phone, "8435328405"),
      called.callback_phone)
check("17. THE PRIMARY PHONE IS NOT TOUCHED",
      _same_number(called.phone, PHONE), called.phone)
check("17. and the source is recorded, so nobody wonders where it came from",
      (called.callback_phone_source or "").startswith("voice"),
      called.callback_phone_source)

again = record_callback_phone(called, called.phone)
check("17. confirming the number we already dialled stores nothing new",
      again is None and _same_number(called.callback_phone, "8435328405"))

ts_src = read("app/services/tenant_scheduling.py")
check("17. the bridge resolves the lead from the CALL before any phone match",
      ts_src.index("called = _lead_from_call") < ts_src.index("existing = q.filter(Lead.phone == phone)"))
check("17. the called lead is looked up inside its own tenant only",
      "Lead.organization_id == org.id" in ts_src
      and "VoiceCall.organization_id == org.id" in ts_src)
check("17. nothing in the bridge writes lead.phone from a spoken number",
      "lead.phone =" not in code_only(ts_src)
      and "called.phone =" not in code_only(ts_src))
check("17. the call id reaches the bridge from the request",
      "call_id=body.call_id" in read("app/routers/integrations_router.py"))


# ── 18. attempts: configurable, and a voicemail is not a conversation ───────

print("\n[18] ATTEMPT POLICY IS CONFIGURATION, NOT A CONSTANT")

from app.services import voice_attempt_policy as vap                    # noqa: E402

p = vap.resolve_attempt_policy(db, REST)
check("18. with nothing configured the system default is unchanged at 3",
      p.max_call_attempts == 3 and p.source["max_call_attempts"] == "system", p.as_dict())
check("18. dials are capped separately, and above conversations",
      p.max_dial_attempts >= p.max_call_attempts)

org.max_call_attempts = 5
db.commit()
p = vap.resolve_attempt_policy(db, REST)
check("18. the ORGANIZATION default is honoured",
      p.max_call_attempts == 5 and p.source["max_call_attempts"] == "organization",
      p.as_dict())


class _Cfg:                                # the use-case level
    max_call_attempts = 7
    max_dial_attempts = None


class _Camp:                               # the campaign level
    max_call_attempts = 2
    max_dial_attempts = None


p = vap.resolve_attempt_policy(db, REST, config=_Cfg())
check("18. the USE CASE overrides the organization",
      p.max_call_attempts == 7 and p.source["max_call_attempts"] == "use_case")
p = vap.resolve_attempt_policy(db, REST, config=_Cfg(), campaign=_Camp())
check("18. and the CAMPAIGN overrides the use case",
      p.max_call_attempts == 2 and p.source["max_call_attempts"] == "campaign")

org.max_call_attempts = 9999
db.commit()
p = vap.resolve_attempt_policy(db, REST)
check("18. NO CONFIGURATION BUYS AN UNLIMITED DIAL PATH",
      p.max_call_attempts == vap.HARD_CEILING_CALL_ATTEMPTS, p.max_call_attempts)
org.max_call_attempts = 0
db.commit()
p = vap.resolve_attempt_policy(db, REST)
check("18. zero is read as unconfigured, not as a permanent ban",
      p.max_call_attempts == 3 and p.source["max_call_attempts"] == "system")
org.max_call_attempts = None
db.commit()

check("18. a voicemail is NOT a live conversation",
      vap.is_live_conversation("voicemail") is False)
check("18. a human is", vap.is_live_conversation("human") is True)
for miss in ("no_answer", "busy", "failed"):
    check("18. %s does not spend a conversation" % miss,
          vap.is_live_conversation(miss) is False)
check("18. a row written before this existed still counts, so no lead gains "
      "attempts retroactively", vap.is_live_conversation(None) is True)

check("18. a full mailbox is recognised from the transcript the provider gave us",
      vap.classify_answer(
          transcript="Six nine five... is not available. The mailbox is full "
                     "and cannot accept any messages at this time. Goodbye.")
      == vap.ANSWERED_VOICEMAIL)
check("18. a real conversation is not mistaken for one",
      vap.classify_answer(
          transcript="Agent: Hi Mike... User: I'm good, how are you doing?",
          duration_seconds=136) != vap.ANSWERED_VOICEMAIL)
check("18. a short call is NOT guessed to be voicemail on length alone",
      vap.classify_answer(transcript="Agent: Hello? User: yes",
                          duration_seconds=4) == vap.ANSWERED_UNKNOWN)

orch = read("app/services/voice_orchestrator.py")
check("18. the orchestrator no longer carries its own constant",
      "MAX_CALL_ATTEMPTS = 3" not in code_only(orch))
check("18. it reads the resolved policy instead",
      "resolve_attempt_policy(db, organization_id, config=config)" in orch)
check("18. and counts conversations separately from dials",
      "is_live_conversation(getattr(r" in orch and '"max_dials"' in orch)
check("18. a redial cooldown stands between permitted attempts",
      '"cooldown"' in orch and "redial_cooldown_minutes" in orch)

hooks = read("app/routers/voice_webhooks_router.py")
check("18. the webhook records WHO answered on the call that ended",
      "_classify_answer(call, event)" in hooks)
check("18. a voicemail gets its own disposition, not 'no_answer'",
      'call.outcome = "voicemail"' in hooks)

god_src = read("app/routers/god_router.py")
check("18. the cap is settable per organization without a shell",
      '@router.patch("/orgs/{org_id}/attempt-policy")' in god_src)
check("18. and per use case",
      '@router.patch("/voice/agents/{config_id}/attempt-policy")' in god_src)
check("18. a cooldown cannot be shortened per use case - it protects the family",
      'cfg, req, ("max_call_attempts", "max_dial_attempts")' in god_src)
check("18. readiness reports the numbers behind its own refusal",
      '"attempts": attempts' in read("app/routers/voice_router.py"))


# ── 19. one sender ladder, everywhere ───────────────────────────────────────

print("\n[19] ADVISOR OVERRIDE -> ORG SENDER -> UNAVAILABLE, IN EVERY PATH")

cal_code = code_only(read("app/routers/calendar_router.py"))
check("19. the booking confirmation uses the shared resolver",
      "_resolve_twilio_creds(advisor, db)" in cal_code)
check("19. and no longer keeps its own org-then-advisor order",
      "getattr(org, 'org_twilio_account_sid', None) or advisor.twilio_account_sid"
      not in cal_code)
check("19. so it cannot build a Twilio client from hand-picked credentials",
      "TwilioClient(_sid, _auth)" not in cal_code)

# The ladder itself, proven end to end at the level Restland is about to use.
advisor.twilio_account_sid = None
advisor.twilio_auth_token_encrypted = None
advisor.twilio_phone_number = None
org.org_twilio_account_sid = "ACorgsid0000000000000000000000000"
org.org_twilio_auth_token_encrypted = "enc"
org.org_twilio_phone_number = "+14692241155"
db.commit()
s = sms.describe_sms_sender(advisor, db)
check("19. THE MOMENT AN ORG SENDER EXISTS, it is used - no redeploy",
      s["ready"] and s["source"] == "organization"
      and s["from_number"] == "+14692241155", s)

advisor.twilio_account_sid = "ACadvsid00000000000000000000000000"
advisor.twilio_auth_token_encrypted = "enc"
advisor.twilio_phone_number = "+14695551234"
db.commit()
s = sms.describe_sms_sender(advisor, db)
check("19. an advisor override still wins over the shared number",
      s["source"] == "advisor" and s["from_number"] == "+14695551234", s)

check("19. no secret is ever in the payload",
      not any(k for k in s if "token" in k or "secret" in k), list(s))
check("19. and only the last four of the sid is reported",
      len(s["account_sid_last4"] or "") <= 4, s["account_sid_last4"])

# EVERY path that mints a link must name the same advisor. The composer was
# fixed first; the email sender and the resend button had the identical bug and
# would have re-introduced it the moment either was used under impersonation.
for path in ("app/routers/email_router.py", "app/routers/leads_router.py"):
    src = code_only(read(path))
    check("19. %s mints its booking link for the lead's advisor"
          % path.rsplit("/", 1)[-1],
          "acting_advisor(db, lead, current_user)" in src, path)
    check("19.    and no longer for whoever is sending",
          "create_booking_link(db, lead, current_user)" not in src, path)


# ── 20. a reply to the shared number comes back ─────────────────────────────

print("\n[20] AN INBOUND REPLY TO AN ORG SENDER FINDS ITS LEAD")

inbound_src = code_only(read("app/routers/sms_router.py"))
check("20. the inbound webhook resolves the ORGANIZATION's shared number too",
      "Organization.org_twilio_phone_number == twilio_to" in inbound_src)
check("20. and still resolves an advisor's own number first",
      inbound_src.index("User.twilio_phone_number == twilio_to")
      < inbound_src.index("Organization.org_twilio_phone_number == twilio_to"))
check("20. the lead lookup stays scoped to the owning organization",
      "Lead.organization_id == org_id" in inbound_src)
check("20. an unowned number is still dropped rather than searched cross-tenant",
      "dropping inbound" in inbound_src and "return _twiml_ack()" in inbound_src)
check("20. the shared-sender path leaves the advisor to the LEAD, not the number",
      "advisor.organization_id if advisor else None" in inbound_src)


# ── 21. a placeholder is not a value ────────────────────────────────────────

print("\n[21] A TENANT SCREEN NEVER SHOWS THE PLATFORM'S OWN DETAILS AS AN EXAMPLE")

# A Restland org admin opened Org Settings and read the platform's live Twilio
# number and brand as their own configuration. Nothing was misconfigured: the
# fields were empty and these were HTML placeholders. That is worse, not
# better - the screen was lying quietly, and the Save button being disabled was
# the only clue the value was not real.
def jsx_code_only(src):
    """The JSX with `{/* ... */}` and `//` comments removed.

    Without this, an absence check reads the comment explaining why the old
    value was wrong and fails on it — which leaves deleting the explanation as
    the only way to go green. The rule is about what the SCREEN renders.
    """
    src = re.sub(r"\{/\*.*?\*/\}", "", src, flags=re.S)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("//"))


org_settings = jsx_code_only(read("frontend/src/pages/OrgSettings.jsx"))

PLATFORM_REAL_VALUES = (
    "+18449172171",          # the platform's live Twilio number
    'placeholder="EvoSys Pro"',
    'placeholder="BookaBoost"',
    "support@bookaboost.live",
    "support@evosyspro.live",
)
for value in PLATFORM_REAL_VALUES:
    check("21. %s does not appear on the org settings screen" % value,
          value not in org_settings, value)

check("21. the phone example uses the reserved fictional 555-01xx range",
      "+18005550100" in org_settings)
check("21. the caller-id example names the CUSTOMER, not the platform",
      'placeholder="Your business name"' in org_settings)
check("21. the from-address example is a generic domain",
      "support@yourdomain.com" in org_settings)
check("21. and the helper says what happens before a domain is verified",
      "until it is, mail goes out from the platform's verified address" in org_settings)


db.close()
if os.path.exists(DB_FILE):
    try:
        os.remove(DB_FILE)
    except OSError:
        pass

print("\n" + "=" * 68)
if failures:
    print("SWEEP PROBE: %d of %d checks FAILED" % (len(failures), checks))
    for f in failures:
        print("   - " + f)
    sys.exit(1)
print("SWEEP PROBE: all %d checks passed" % checks)
print("PUBLIC ROUTES SERVED, PREVIEW EQUALS SEND, CHANNELS INDEPENDENT")
sys.exit(0)
