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
check("5. resolution order is advisor account -> advisor number on org "
      "credentials -> org shared number -> unavailable",
      svc.index("--- 1. Advisor's own Twilio account")
      < svc.index("--- 2. Advisor's assigned number")
      < svc.index("--- 3. Organization's optional shared number")
      < svc.index("--- 4. Unavailable"))
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

# THE ENDPOINT THAT ACTUALLY SENDS THE TEXT.
#
# The composer, the email sender and the resend button were all corrected to
# `acting_advisor`. `/sms/send` - the one that puts a message on the wire - was
# missed, and kept resolving the sender from whoever pressed Send. Under
# impersonation that refused outright, which is loud and safe. The quiet
# failure is the one that matters: with organization credentials present it
# would have resolved the ORGANIZATION's shared number instead of the advisor's
# own assigned number, and a family would have been texted from a number that
# is not their advisor's.
sms_send = code_only(read("app/routers/sms_router.py"))
check("19. /sms/send sends as the LEAD'S advisor, not the caller",
      "send_sms(db, acting_advisor(db, lead, current_user), lead," in sms_send)
check("19. /sms/send-mms does too",
      "send_mms(db, acting_advisor(db, lead, current_user), lead," in sms_send)
check("19. a BATCH resolves an advisor per lead, not one sender for all of them",
      "who = acting_advisor(db, lead, current_user)" in sms_send
      and "send_batch(db, who, group," in sms_send)
check("19. no send path in sms_router still passes the raw caller",
      "send_sms(db, current_user, lead" not in sms_send
      and "send_mms(db, current_user, lead" not in sms_send
      and "send_batch(db, current_user, leads" not in sms_send)
check("19. and the batch response keeps its shape",
      all(k in sms_send for k in ('"sent_count"', '"skipped_count"',
                                  '"sent_ids"', '"skipped_ids"')))


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

# Same class, second screen. An EvoSys customer opening Billing was told to
# email BookaBoost about their own invoice.
billing = jsx_code_only(read("frontend/src/pages/Billing.jsx"))
check("21. Billing does not hard-code one brand's support address",
      "support@bookaboost.live" not in billing)
check("21. it resolves the support address from the brand config",
      "BRAND_CONFIG[detectTheme()]" in billing and "SUPPORT_EMAIL" in billing)
check("21. and every mailto uses the resolved value",
      billing.count("mailto:${SUPPORT_EMAIL}") >= 3, billing.count("mailto:"))


# ── 22. credentials on the org, numbers on the people ───────────────────────

print("\n[22] ONE TWILIO ACCOUNT PER ORGANIZATION, ONE NUMBER PER ADVISOR")

# The shape Restland actually needs: ONE Restland Twilio account carrying ONE
# A2P brand and campaign, with FSA 1 / FSA 2 / FSA 3 each holding only their
# own local number. The old ladder used an advisor's number ONLY when that
# advisor row also carried a SID and an auth token, so this shape required
# copying the organization's auth token onto every advisor row - one secret
# duplicated per member of staff, and every copy a place it can leak from or
# fall out of date.

fsa = User(id="fsa-probe-1", email="fsa1@restland.test", full_name="FSA One",
           password_hash="x", role="advisor", organization_id=REST, is_active=True,
           twilio_phone_number="+12145550137")   # number ONLY - no sid, no token
db.add(fsa)
# A REAL ciphertext, not the "enc" sentinel the earlier sections use. This
# section exercises _resolve_twilio_creds, which actually decrypts - and the
# whole point of the new branch is that it reaches the ORGANISATION's token, so
# a probe that never decrypts one would not have proven anything.
from app.utils.crypto import encrypt_value as _encrypt                 # noqa: E402
_ORG_TOKEN_CIPHERTEXT = _encrypt("probe-not-a-real-twilio-token")
org.org_twilio_account_sid = "ACorgsid0000000000000000000000000"
org.org_twilio_auth_token_encrypted = _ORG_TOKEN_CIPHERTEXT
org.org_twilio_phone_number = None               # NO shared number, deliberately
org.org_twilio_caller_id_name = "Restland"
db.commit()

s = sms.describe_sms_sender(fsa, db)
check("22. an advisor with a NUMBER and no credentials can send",
      s["ready"] is True, s)
check("22. from THEIR OWN number, not a shared one",
      s["source"] == "advisor" and s["from_number"] == "+12145550137", s)
check("22. on the ORGANIZATION's Twilio account",
      s["credentials_source"] == "organization"
      and s["account_sid_last4"] == "0000", s)
check("22. and no auth token is stored on the advisor row",
      fsa.twilio_account_sid is None and fsa.twilio_auth_token_encrypted is None)
check("22. still no secret in the payload",
      not any(k for k in s if "token" in k or "secret" in k), list(s))

_client, _from, _cid = sms._resolve_twilio_creds(fsa, db)
check("22. the SEND agrees with the screen - same number",
      _from == "+12145550137", _from)
check("22. and inherits the organization's caller ID when it has none of its own",
      _cid == "Restland", _cid)

# A second FSA under the same account is exactly the same shape.
fsa2 = User(id="fsa-probe-2", email="fsa2@restland.test", full_name="FSA Two",
            password_hash="x", role="advisor", organization_id=REST, is_active=True,
            twilio_phone_number="+12145550188")
db.add(fsa2)
db.commit()
s2 = sms.describe_sms_sender(fsa2, db)
check("22. a second advisor gets a DIFFERENT number on the SAME account",
      s2["from_number"] == "+12145550188"
      and s2["account_sid_last4"] == s["account_sid_last4"], s2)

# NO SILENT PLATFORM FALLBACK. Credentials gone means refused, not borrowed.
org.org_twilio_account_sid = None
org.org_twilio_auth_token_encrypted = None
db.commit()
s3 = sms.describe_sms_sender(fsa, db)
check("22. a number with no org credentials is NOT ready", s3["ready"] is False, s3)
check("22. and the reason names the organization's missing credentials",
      "no Twilio credentials" in (s3["reason"] or ""), s3["reason"])
try:
    sms._resolve_twilio_creds(fsa, db)
    check("22. the send refuses rather than borrowing another account", False,
          "no exception raised")
except ValueError:
    check("22. the send refuses rather than borrowing another account", True)
org.org_twilio_account_sid = "ACorgsid0000000000000000000000000"
org.org_twilio_auth_token_encrypted = _ORG_TOKEN_CIPHERTEXT
db.commit()

# The platform owner still gets nothing, even holding a number of their own.
god.twilio_phone_number = "+18449172171"
db.commit()
sg = sms.describe_sms_sender(god, db)
check("22. the platform owner's own number is still never a tenant's sender",
      sg["from_number"] != "+18449172171", sg)

# INBOUND. An advisor holding only a number must still be found by the webhook,
# or a STOP arriving on that number is discarded.
inbound_src = code_only(read("app/routers/sms_router.py"))
check("22. inbound still matches an advisor by NUMBER, which needs no credentials",
      "User.twilio_phone_number == twilio_to" in inbound_src)
_hit = db.query(User).filter(User.twilio_phone_number == "+12145550137").first()
check("22. and that lookup finds the credential-less advisor",
      _hit is not None and _hit.id == fsa.id)
check("22.    scoped to their organization, so STOP lands in the right tenant",
      _hit.organization_id == REST)

# PER-ACCOUNT WEBHOOK AUTH. The signature is verified against the account that
# signed - the organization's - which the advisor row never carries.
from app.utils.twilio_webhook_guard import (                          # noqa: E402
    resolve_account_by_sid, account_sids_for_advisor)
_resolved = resolve_account_by_sid(db, "ACorgsid0000000000000000000000000")
check("22. the webhook guard resolves the ORGANIZATION's account sid",
      _resolved is not None and _resolved.organization_id == REST, _resolved)
check("22. an advisor holding only a number still authorizes that account",
      "ACorgsid0000000000000000000000000" in account_sids_for_advisor(db, fsa))
check("22. and an unknown sid resolves to nothing, so the request is denied",
      resolve_account_by_sid(db, "ACunknown00000000000000000000000") is None)

# THE ORG SHARED NUMBER IS OPTIONAL, NOT A PREREQUISITE.
org_api = code_only(read("app/routers/org_settings_router.py"))
check("22. saving org credentials no longer demands a shared number",
      "org_twilio_phone_number:   Optional[str] = None" in org_api)
check("22. a number can be assigned to one advisor without any credentials",
      "def assign_org_sending_number" in org_api)
check("22. that endpoint writes a number and never a secret",
      "target.twilio_phone_number = number" in org_api
      and "target.twilio_account_sid" not in org_api
      and "twilio_auth_token_encrypted" not in org_api.split(
          "def assign_org_sending_number")[1])
check("22. the assignment is confined to the resolved organization",
      "User.organization_id == org.id" in org_api)
check("22. and a duplicate number is refused, so inbound stays unambiguous",
      "def _assert_number_unused" in org_api
      and "Organization.org_twilio_phone_number == number" in org_api)
check("22. the roster excludes the platform owner from tenant staff",
      'User.role != "god_admin"' in org_api)

# A2P STATE ACTUALLY PERSISTS. Every one of these columns was written behind a
# hasattr() guard against a model that did not declare them, so every SID the
# registration flow obtained was dropped and /10dlc/status answered null.
from app.models.models import Organization as _Org                     # noqa: E402
for _col in ("twilio_messaging_service_sid", "twilio_a2p_brand_sid",
             "twilio_a2p_brand_status", "twilio_a2p_campaign_sid",
             "twilio_a2p_campaign_status", "twilio_a2p_campaign_use_case",
             "twilio_a2p_registered_at"):
    check("22. Organization.%s is declared, so registration state survives" % _col,
          hasattr(_Org, _col), _col)

org.twilio_a2p_brand_sid = "BNprobe0000000000000000000000000"
org.twilio_a2p_brand_status = "PENDING"
db.commit()
db.expire(org)
_reloaded = db.query(_Org).filter(_Org.id == REST).first()
check("22. an A2P brand sid written to the org is still there after a reload",
      _reloaded.twilio_a2p_brand_sid == "BNprobe0000000000000000000000000",
      _reloaded.twilio_a2p_brand_sid)
check("22. and a PENDING status is never reported as approved",
      _reloaded.twilio_a2p_brand_status == "PENDING")

dlc_src = code_only(read("app/routers/dlc_router.py"))
check("22. A2P registration uses the ORGANIZATION's credentials first",
      dlc_src.index("org.org_twilio_account_sid and org.org_twilio_auth_token_encrypted")
      < dlc_src.index("current_user.twilio_account_sid and current_user.twilio_auth_token_encrypted"))
check("22. and never registers a customer's brand on the platform's account",
      'is_owner = (getattr(current_user, "role", None) or "").lower() == "god_admin"'
      in dlc_src and "(not is_owner) and current_user.twilio_account_sid" in dlc_src)
check("22. the status payload reports credentials separately from approval",
      '"credentials_ready"' in dlc_src)


# ── 23. the booking link fits in an SMS ─────────────────────────────────────

print("\n[23] A BOOKING LINK IS SHORT, OPAQUE, AND CARRIES NO FAMILY DATA")

# The token was base64(json({lead, appt_type, duration, expires}))~sig - 379
# characters, carrying the family's NAME, PHONE and TIER in the URL. That made
# a normal Restland message 602 characters / 4 segments, and carriers filtered
# it: Twilio 30007, "Your message content was flagged as going against carrier
# guidelines", on every multi-segment send from +14692241155 since July. A
# 1-segment message on the same path to the same handset delivered.

_bl = _sms.create_booking_link(db, phone_only, advisor)
check("23. the token is short enough to sit in a one-segment message",
      len(_bl.token) <= 32, len(_bl.token))
check("23. it is opaque - no '~' payload separator, nothing to decode",
      "~" not in _bl.token, _bl.token)

_leak = (phone_only.first_name or "", phone_only.last_name or "",
         phone_only.phone or "", phone_only.tier or "")
import base64 as _b64
try:
    _pad = _bl.token + "=" * (-len(_bl.token) % 4)
    _decoded = _b64.urlsafe_b64decode(_pad).decode("utf-8", "replace")
except Exception:
    _decoded = ""
for _v in _leak:
    if _v:
        check("23. the URL does not carry the family's %s"
              % ("phone" if _v == phone_only.phone else "details"),
              _v not in _bl.token and _v not in _decoded, _v)

check("23. the appointment details are on the ROW the token keys",
      bool(_bl.appt_label) and bool(_bl.appt_duration),
      (_bl.appt_label, _bl.appt_duration))
from datetime import datetime as _dt                                   # noqa: E402
check("23. expiry is stored server-side, where it can be revoked",
      _bl.expires_at is not None and _bl.expires_at > _dt.utcnow())
check("23. two links are never the same token",
      _sms.create_booking_link(db, phone_only, advisor).token != _bl.token)
check("23. and the preview still reuses one link rather than minting per keystroke",
      _sms.get_or_create_booking_link(db, phone_only, advisor).id
      == _sms.get_or_create_booking_link(db, phone_only, advisor).id)

# THE WHOLE POINT: the message has to fit.
_url = "https://app.evosyspro.live/book/" + _bl.token
_body = _sms.compose_body(
    "Hi {first_name}, this is {advisor_name} with Restland Cemetery and Funeral "
    "Home. Following up on your pre-need planning inquiry - you can pick a time "
    "that works for you here: {booking_link}  Reply STOP to opt out.",
    phone_only, advisor, _url)
_segments = 1 if len(_body) <= 160 else -(-len(_body) // 153)
check("23. A REAL MESSAGE WITH A BOOKING LINK IS 1-2 SEGMENTS, NOT 4",
      _segments <= 2, "%d chars / %d segments" % (len(_body), _segments))

# Old links already in families' hands must keep working.
cal_src = code_only(read("app/routers/calendar_router.py"))
check("23. the reader prefers the stored label",
      'if getattr(booking, "appt_label", None):' in cal_src)
check("23. and keeps the legacy decode as a FALLBACK, so live links still open",
      "urlsafe_b64decode" in cal_src and cal_src.index('getattr(booking, "appt_label", None)')
      < cal_src.index("urlsafe_b64decode"))
from app.models.models import BookingLink as _BL                       # noqa: E402
check("23. the columns are declared on the model",
      hasattr(_BL, "appt_label") and hasattr(_BL, "appt_duration"))
svc_tok = code_only(read("app/services/sms_service.py"))
check("23. no payload is ever encoded into a token again",
      "urlsafe_b64encode" not in svc_tok)
check("23. the token is generated from a CSPRNG, not a truncated hash",
      "secrets.token_urlsafe" in svc_tok)

# A LEAD THAT ALREADY HAS A LONG LINK MUST NOT KEEP IT.
#
# get_or_create_booking_link reuses the newest pending link. Without this, the
# shortening would have changed nothing for any lead that already had one -
# which is most of them - and their messages would have stayed at 4 segments.
_legacy = _sms.create_booking_link(db, email_only, advisor)
_legacy.token = "eyJsZWFkIjogeyJGaXJzdCBOYW1lIjogIk1pa2UifX0~0c1d500c88ebb4c8"
db.commit()
_fresh = _sms.get_or_create_booking_link(db, email_only, advisor)
check("23. a lead holding a LEGACY long link is issued a short one instead",
      "~" not in _fresh.token and _fresh.token != _legacy.token, _fresh.token)
check("23.    and the legacy row is left intact, so a family's live link still works",
      db.query(_BL).filter(_BL.id == _legacy.id).first().token == _legacy.token)
check("23.    while the new short link is then reused, not re-minted per call",
      _sms.get_or_create_booking_link(db, email_only, advisor).id == _fresh.id)


# ── 24. no email ever goes out under another brand's name ───────────────────

print("\n[24] AN UNRESOLVED BRAND REFUSES TO SEND, IT DOES NOT BORROW ONE")

# A Restland family received mail From noreply@bookaboost.live - a company they
# have never heard of - and their employer's gateway put it in Junk behind an
# "arrived from outside" warning. public_identity had done its job and returned
# from_email=None rather than guess a brand; send_email_via_provider then
# substituted the deployment-wide EMAIL_FROM_ADDRESS one line later, which on a
# deployment serving three brands is exactly the guess the resolver refused.
from app.services.public_identity import SendingIdentity                # noqa: E402
from app.services import email_service as _es                            # noqa: E402

_unresolved = SendingIdentity(from_email=None)
check("24. a resolved identity marks itself as having asked every level",
      _unresolved.resolved is True)
_r = _es.send_email_via_provider("family@example.test", "s", "<p>b</p>",
                                 org=_unresolved)
check("24. AN UNRESOLVED BRAND REFUSES TO SEND", _r["success"] is False, _r)
check("24. and says so in terms an admin can act on",
      "another brand" in (_r.get("error") or "")
      and "Org Settings" in (_r.get("error") or ""), _r.get("error"))
check("24. nothing was handed to the provider",
      _r.get("provider_message_id") is None)

es_src = code_only(read("app/services/email_service.py"))
check("24. the refusal is keyed on the RESOLVED flag, not on a missing value",
      'getattr(org, "resolved", False) and not getattr(org, "from_email", None)'
      in es_src)
check("24. a raw Organization row still falls back, so old callers keep working",
      'from_addr = (getattr(org, "from_email", None) or FROM_EMAIL) if org else FROM_EMAIL'
      in es_src)
check("24. the lead send path hands over the RESOLVED identity",
      "sending_identity_for_org(db, lead.organization_id)" in es_src)

# THE CUSTOM-BODY BRANCH WAS THE ONE STILL LEAKING.
#
# email_router has two send paths. The template path was corrected to the
# resolver; the custom-body path - the one the composer and every manual send
# use - still passed the bare Organization row, so Restland's null from_email
# fell through to EMAIL_FROM_ADDRESS and the family got BookaBoost.
er_src = code_only(read("app/routers/email_router.py"))
check("24. the CUSTOM-BODY email path resolves the identity too",
      "org=sending_identity_for_org(db, lead.organization_id)" in er_src)
check("24. and no email path still hands over the raw Organization row",
      "org=org)" not in er_src
      and "filter_by(id=current_user.organization_id).first()" not in er_src)
check("24. a template email is sent AS THE LEAD'S ADVISOR, not the caller",
      "send_email_to_lead(db, _acting(db, lead, current_user), lead)" in er_src)
check("24.    and no longer signs the platform owner's name",
      "send_email_to_lead(db, current_user, lead)" not in er_src)
check("24. the stored record attributes the email to that advisor",
      "sender_id=acting_advisor(db, lead, current_user).id" in er_src)


# ── 25. a duplicate is not a DNC, and a flag can be undone ──────────────────

print("\n[25] DUPLICATE IS A DATA-QUALITY FLAG, NOT A SUPPRESSION")

# The importer set `status = "dnc"` alongside `is_duplicate`. Those are two
# different facts: DNC means a human asked not to be contacted; duplicate means
# we may already hold this person under another row. Conflating them put leads
# into the do-not-contact population for a bookkeeping reason, where the only
# endpoint that touched the flag DELETED the row.
imp_src = code_only(read("app/services/import_service.py"))
_dupe_blocks = imp_src.count('lead.is_duplicate = True')
check("25. every duplicate branch in the importer is still there",
      _dupe_blocks >= 4, _dupe_blocks)
check("25. NO duplicate branch sets DNC any more",
      'lead.is_duplicate = True\n                lead.status = "dnc"' not in imp_src
      and 'lead.duplicate_of_lead_id = existing_email_lead.id\n                    lead.status = "dnc"' not in imp_src)
check("25. a REAL suppression still sets DNC",
      'if call_restricted:\n                    lead.status = "dnc"' in imp_src)
check("25. and every flag now records WHY it fired",
      imp_src.count("lead.duplicate_reason =") >= 4
      and imp_src.count("lead.duplicate_match_field =") >= 4
      and imp_src.count("lead.duplicate_match_value =") >= 4)
check("25. including which of the two registry rules matched",
      '"registry_placeholder" if _placeholder' in imp_src)

for _c in ("duplicate_reason", "duplicate_match_field", "duplicate_match_value",
           "duplicate_resolved_at", "duplicate_resolved_by"):
    check("25. Lead.%s is declared" % _c, hasattr(Lead, _c), _c)

lr_src = code_only(read("app/routers/leads_router.py"))
check("25. KEEP SEPARATE exists, so a flag no longer means delete-or-live-with-it",
      '@router.post("/{lead_id}/not-duplicate")' in lr_src)
check("25. it deletes nothing",
      "db.delete" not in lr_src.split("def keep_lead_separate")[1].split("def _has_real_dnc_reason")[0])
check("25. it records the resolution and who made it",
      "lead.duplicate_resolved_at = datetime.utcnow()" in lr_src
      and "lead.duplicate_resolved_by = current_user.id" in lr_src)
check("25. and writes it to the audit log",
      '"lead.duplicate_resolved_keep_separate"' in lr_src)
check("25. A REAL DNC IS NEVER SILENTLY RE-OPENED",
      "not _has_real_dnc_reason(db, lead)" in lr_src)
check("25. and the real-reason check fails CLOSED",
      "except Exception:\n        return True" in lr_src)
check("25. a STOP reply counts as a real reason",
      "ReplyClassification.DNC" in lr_src)
check("25. so does the org suppression list",
      "_is_suppressed(db, lead)" in lr_src)

check("25. both repair passes are DRY RUN by default",
      lr_src.count('apply: bool = Query(False') == 2)
check("25. the duplicate/DNC repair only touches rows carrying the bug's signature",
      "Lead.status == \"dnc\"," in lr_src and "Lead.is_duplicate == True," in lr_src)
# "partial" IS THE IMPORTER'S PLACEHOLDER, NOT A TIER — and it is truthy.
# A `if not lead.tier` check reads it as "tier present", which would have
# released every unclassified lead in the org straight into outreach. All
# 7,372 needs_tier_review leads in Restland carry exactly this value.
check("25. 'partial' is treated as UNCLASSIFIED, not as a tier",
      "def _has_real_tier" in lr_src
      and 'tier not in ("partial", "none", "unknown")' in lr_src)
check("25. no status restore uses a bare truthiness check on tier",
      'if not lead.tier else "new"' not in lr_src)
check("25. every restore routes an unclassified lead back to review",
      lr_src.count('"new" if _has_real_tier(lead) else "needs_tier_review"') >= 2)
check("25. the tier repair releases only leads a human has classified",
      "if not _has_real_tier(lead):" in lr_src)
check("25. and the dry run says how many would actually become sendable",
      '"would_become_sendable"' in lr_src
      and "_has_real_tier(l) and l.phone" in lr_src)
check("25. and never releases a duplicate, a phoneless lead, or a real DNC",
      "lead.is_duplicate or not lead.phone or _has_real_dnc_reason(db, lead)" in lr_src)
check("25. an old flag can still be explained",
      '@router.get("/{lead_id}/duplicate-explain")' in lr_src
      and "registry_entries_for_this_phone" in lr_src)

leads_jsx = jsx_code_only(read("frontend/src/pages/Leads.jsx"))
check("25. the row offers KEEP SEPARATE, not only deletion",
      "handleKeepSeparate" in leads_jsx and "Keep separate" in leads_jsx)
check("25. and says what it matched",
      "handleExplainDupe" in leads_jsx and "duplicate-explain" in leads_jsx)
check("25. the confirm promises nothing is deleted",
      "Nothing is deleted and nothing is merged" in leads_jsx)

# MANUAL "+ Add lead" MATCHED ON PHONE ALONE.
#
# dedup_service is explicit that phone-only matching is wrong - a phone can be
# a household, a father and a son - and its registry keys on phone + last name.
# The manual add endpoint quietly did the opposite, so every lead added on a
# number the org had ever used was flagged against an unrelated person. Ashton
# Jamon was flagged against Jennifer Breeder on nothing but a shared number.
check("25. manual add matches on phone AND last name, never phone alone",
      "normalize_last_name(existing.last_name or \"\") == last_name_normalized" in lr_src)
check("25.    so a shared household number is not a duplicate by itself",
      "if phone_normalized and last_name_normalized:" in lr_src)
check("25.    and it records the parent and the reason",
      'duplicate_reason="manual_add_phone_last_name" if is_dup else None' in lr_src
      and "duplicate_of_lead_id=dup_of," in lr_src)
check("25.    and never re-flags a lead a human already resolved",
      "Lead.duplicate_resolved_at.is_(None)," in lr_src)
check("25. the email cleanup sweep no longer suppresses either",
      'lead.duplicate_of_lead_id = seen[key]\n                lead.duplicate_reason' in lr_src)



# ---------------------------------------------------------------------------
# 26. A BLOCKED OR UNDELIVERED MESSAGE MUST NEVER LOOK LIKE A DELIVERED ONE
#
# The failure this locks down: an SMS to a live test lead was accepted by
# Twilio and then came back `undelivered`. The conversation transcript showed
# it as an ordinary outbound bubble - body, timestamp, nothing else - because
# ConversationBubble rendered no status field at all. The backend was already
# sending `status` in each event and the frontend simply dropped it. An
# operator therefore had no way, inside the product, to tell a message that
# reached a family from one that never left the carrier.
#
# Twilio's ErrorCode was not stored anywhere either, so even after noticing
# the failure the reason was only obtainable by logging into the Twilio
# console by hand.
# ---------------------------------------------------------------------------
import app.services.message_state as _ms

ms_src = read("app/services/message_state.py")

check("26. the five states are named exactly once, in one module",
      _ms.ALL_STATES == ("blocked", "queued", "sent", "delivered", "failed"))
check("26. undelivered is a failure, not a fourth kind of sent",
      _ms.normalize_provider_status("undelivered") == "failed"
      and _ms.normalize_provider_status("failed") == "failed"
      and _ms.normalize_provider_status("canceled") == "failed")
check("26. queued and sent are distinct - handed to a carrier is not arrival",
      _ms.normalize_provider_status("queued") == "queued"
      and _ms.normalize_provider_status("sent") == "sent"
      and _ms.normalize_provider_status("delivered") == "delivered")
check("26. an unknown provider status degrades to queued, never to sent",
      _ms.normalize_provider_status("something-new") == "queued"
      and _ms.normalize_provider_status(None) == "queued"
      and _ms.normalize_provider_status("") == "queued")


class _Row:
    def __init__(self, **kw):
        self.twilio_sid = kw.get("sid")
        self.send_state = kw.get("send_state")
        self.delivery_status = kw.get("delivery_status")
        self.twilio_status = kw.get("twilio_status")
        self.error_code = kw.get("error_code")
        self.error_message = kw.get("error_message")


# THE LOAD-BEARING GUARANTEE. No provider SID means no provider request.
check("26. a row with no provider SID reads BLOCKED",
      _ms.send_state_for(_Row(sid=None)) == "blocked")
check("26.    even when its own columns claim it was delivered",
      _ms.send_state_for(_Row(sid=None, send_state="delivered",
                              delivery_status="delivered")) == "blocked")
check("26.    and a blank SID counts as no SID",
      _ms.send_state_for(_Row(sid="   ")) == "blocked")
check("26. an undelivered message with a SID reads FAILED, not sent",
      _ms.send_state_for(_Row(sid="SM1", delivery_status="undelivered")) == "failed")
check("26. this codebase's own 'pending' placeholder reads QUEUED",
      _ms.send_state_for(_Row(sid="SM1", delivery_status="pending")) == "queued")
check("26. a legacy row with no state at all still resolves from twilio_status",
      _ms.send_state_for(_Row(sid="SM1", twilio_status="delivered")) == "delivered")
check("26. describe() carries the code and reason, not just a word",
      _ms.describe(_Row(sid="SM1", delivery_status="undelivered",
                        error_code="30007",
                        error_message="Carrier violation"))["error_code"] == "30007")

# The receipt must capture WHY, not only THAT.
sms_router_src = read("app/routers/sms_router.py")
check("26. the status callback accepts Twilio's ErrorCode",
      "ErrorCode: str | None = Form(None)" in sms_router_src
      and "ErrorMessage: str | None = Form(None)" in sms_router_src)
check("26. and persists it",
      "msg.error_code = str(ErrorCode)[:32]" in sms_router_src)
check("26. and never clears a code it already holds",
      "if ErrorCode:" in sms_router_src)
check("26. and writes the explicit state from the provider's own receipt",
      "msg.send_state = normalize_provider_status(MessageStatus)" in sms_router_src)

sms_service_src = read("app/services/sms_service.py")
check("26. a freshly created message records what Twilio actually said",
      sms_service_src.count("send_state=normalize_provider_status(twilio_msg.status)") >= 2)
check("26. the pre-send gates still raise before any provider call",
      "raise ValueError" in sms_service_src
      and sms_service_src.index("is marked DNC")
          < sms_service_src.index("client.messages.create"))

# The transcript is the surface the operator actually reads.
lr_src26 = read("app/routers/leads_router.py")
check("26. the conversation endpoint embeds the explicit delivery block",
      '"delivery": _describe_delivery(m),' in lr_src26)

ld_jsx = jsx_code_only(read("frontend/src/pages/LeadDetail.jsx"))
check("26. the transcript renders a delivery chip on every outbound bubble",
      "DeliveryChip" in ld_jsx
      and "{e.type === 'outbound' && <DeliveryChip delivery={e.delivery} />}" in ld_jsx)
check("26. and names all five states",
      all(("  " + s + ":") in ld_jsx or (s + ":") in ld_jsx
          for s in ("blocked", "queued", "sent", "delivered", "failed")))
check("26. and shows the provider error code when there is one",
      "Twilio ${delivery.error_code}" in ld_jsx)

# Forensics without a console login.
trace_src = read("app/routers/god_sms_trace_router.py")
check("26. the trace endpoint is god-only",
      "_god: User = Depends(require_god)" in trace_src)
# Precise: the prose above the code SAYS "messages.create" to explain what it
# will never do, so match an actual call site, not the word.
check("26. and is read-only - it can never create a message",
      ".create(" not in trace_src and ".update(" not in trace_src)
check("26. and never returns an auth token",
      "auth_token" not in trace_src)
check("26. and says plainly when a row was never submitted",
      "the message was never" in trace_src)
main_src26 = read("app/main.py")
check("26. and is actually mounted",
      "app.include_router(god_sms_trace_router)" in main_src26)

migr_src = read("app/auto_migrate.py")
check("26. the new columns are added without a shell",
      '("messages", "send_state", "VARCHAR")' in migr_src
      and '("messages", "error_code", "VARCHAR")' in migr_src
      and '("messages", "error_message", "VARCHAR")' in migr_src)


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
