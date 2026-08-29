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
