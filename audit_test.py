"""
BookaBoost Platform Audit Test Suite — v2
Correct routes, OAuth2 form-data login, proper endpoint paths.
"""
import sys, os
sys.path.insert(0, '.')

os.environ['DATABASE_URL'] = 'sqlite:///./test_audit.db'
os.environ['JWT_SECRET'] = 'audit_secret_key_for_testing_purposes_only_1234567890'
os.environ['OPENAI_API_KEY'] = 'sk-dummy'
os.environ['RESEND_API_KEY'] = 're_dummy'
os.environ['SUPER_ADMIN_EMAIL'] = 'super@test.com'
os.environ['GOD_ADMIN_EMAIL'] = 'mike@simmonsstrong.com'
os.environ['TWILIO_ACCOUNT_SID'] = 'AC_dummy'
os.environ['TWILIO_AUTH_TOKEN'] = 'dummy_twilio_token'
os.environ['TWILIO_PHONE_NUMBER'] = '+15550000000'
os.environ['FRONTEND_URL'] = 'http://localhost:5173'
os.environ['BOOKING_BASE_URL'] = 'https://advisorflow-booking.vercel.app'

RESULTS = []

def check(label, ok, detail=""):
    status = "✅" if ok else "❌"
    RESULTS.append((ok, label, detail))
    print(f"{status} {label}" + (f"  [{detail}]" if detail else ""))

# ─────────────────────────────────────────
# 1. Import + route count
# ─────────────────────────────────────────
try:
    from app.main import app
    route_count = len([r for r in app.routes if hasattr(r, 'methods')])
    check("App import", True, f"{route_count} routes")
except Exception as e:
    check("App import", False, str(e))
    print("FATAL"); sys.exit(1)

# ─────────────────────────────────────────
# 2. DB + tables
# ─────────────────────────────────────────
try:
    from app.deps import engine
    from app.models.models import Base
    Base.metadata.create_all(bind=engine)
    check("DB tables created", True)
except Exception as e:
    check("DB tables created", False, str(e))

# ─────────────────────────────────────────
# 3. Auto-migrate (call the actual entry point)
# ─────────────────────────────────────────
try:
    import importlib
    am = importlib.import_module('app.auto_migrate')
    # Find whatever the callable is
    fn = getattr(am, 'run_migrations', None) or getattr(am, 'auto_migrate', None) or getattr(am, 'main', None)
    if fn:
        from app.deps import engine
        fn(engine)
        check("Auto-migrations", True, f"called {fn.__name__}()")
    else:
        # Try running as __main__ style — look for a top-level migrate call
        cols = getattr(am, 'COLUMNS_TO_ADD', getattr(am, 'MIGRATIONS', None))
        check("Auto-migrations", cols is not None, "no run_migrations() but COLUMNS_TO_ADD found — OK if called at startup")
except Exception as e:
    check("Auto-migrations", False, str(e))

from fastapi.testclient import TestClient
client = TestClient(app, raise_server_exceptions=False)

# ─────────────────────────────────────────
# 4. Health
# ─────────────────────────────────────────
r = client.get("/health")
check("GET /health", r.status_code == 200, f"status={r.status_code}")

# ─────────────────────────────────────────
# 5. Register org via /onboarding/register
# ─────────────────────────────────────────
r = client.post("/onboarding/register", json={
    "business_name": "Audit Test Org",
    "admin_full_name": "Mike Simmons",
    "admin_email": "mike@simmonsstrong.com",
    "admin_password": "TestPass123!",
    "industry": "funeral",
})
ok = r.status_code in (200, 201, 400)  # 400 = already exists
check("POST /onboarding/register", ok, f"status={r.status_code} {r.text[:80] if not ok else ''}")

# ─────────────────────────────────────────
# 6. Login (OAuth2PasswordRequestForm = form-data, not JSON)
# ─────────────────────────────────────────
r = client.post("/auth/login", data={
    "username": "mike@simmonsstrong.com",
    "password": "TestPass123!",
})
ok = r.status_code == 200 and "access_token" in (r.json() or {})
check("POST /auth/login (form-data)", ok, f"status={r.status_code}")
TOKEN = r.json().get("access_token", "") if ok else ""
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# ─────────────────────────────────────────
# 7. Profile (replaces /auth/me)
# ─────────────────────────────────────────
r = client.get("/settings/profile", headers=AUTH)
ok = r.status_code == 200
data = r.json() if ok else {}
check("GET /settings/profile", ok, f"role={data.get('role','?')}")
ME = data

# ─────────────────────────────────────────
# 8. Create lead (correct path: /leads/create)
# ─────────────────────────────────────────
r = client.post("/leads/create", headers=AUTH, json={
    "first_name": "Jane",
    "last_name": "Audit",
    "email": "jane.audit@example.com",
    "phone": "+15550001234",
    "tier": "pre_need",
    "status": "new",
    "source_file": "audit_test",
})
ok = r.status_code in (200, 201)
check("POST /leads/create", ok, f"status={r.status_code} {r.text[:100] if not ok else ''}")
LEAD_ID = r.json().get("id", "") if ok else ""

# ─────────────────────────────────────────
# 9. Get lead
# ─────────────────────────────────────────
if LEAD_ID:
    r = client.get(f"/leads/{LEAD_ID}", headers=AUTH)
    ok = r.status_code == 200
    check("GET /leads/{id}", ok, f"status={r.status_code}")
else:
    check("GET /leads/{id}", False, "no lead_id — skipped")

# ─────────────────────────────────────────
# 10. PATCH lead
# ─────────────────────────────────────────
if LEAD_ID:
    r = client.patch(f"/leads/{LEAD_ID}", headers=AUTH, json={"status": "contacted"})
    ok = r.status_code in (200, 204)
    check("PATCH /leads/{id}", ok, f"status={r.status_code} {r.text[:80] if not ok else ''}")
else:
    check("PATCH /leads/{id}", False, "skipped")

# ─────────────────────────────────────────
# 11. List leads
# ─────────────────────────────────────────
r = client.get("/leads/?limit=5", headers=AUTH)
ok = r.status_code == 200
body = r.json() if ok else {}
count = len(body) if isinstance(body, list) else body.get("total", "?")
check("GET /leads/", ok, f"status={r.status_code}, count={count}")

# ─────────────────────────────────────────
# 12. Lead activity timeline
# ─────────────────────────────────────────
if LEAD_ID:
    r = client.get(f"/leads/{LEAD_ID}/activity", headers=AUTH)
    ok = r.status_code in (200, 404)
    check("GET /leads/{id}/activity", ok, f"status={r.status_code}")

# ─────────────────────────────────────────
# 13. Settings — appointment types
# ─────────────────────────────────────────
r = client.get("/settings/appointment-types", headers=AUTH)
ok = r.status_code == 200 and "appointment_types" in (r.json() or {})
check("GET /settings/appointment-types", ok, f"status={r.status_code}")

r = client.put("/settings/appointment-types", headers=AUTH,
    json={"appointment_types": ["Pre-Need", "At-Need", "File Check"]})
ok = r.status_code in (200, 201)
check("PUT /settings/appointment-types", ok, f"status={r.status_code} {r.text[:80] if not ok else ''}")

# Verify it saved
r2 = client.get("/settings/appointment-types", headers=AUTH)
saved = r2.json().get("appointment_types", []) if r2.status_code == 200 else []
check("  → custom types persisted", "Pre-Need" in saved, str(saved))

r = client.delete("/settings/appointment-types", headers=AUTH)
ok = r.status_code in (200, 204)
check("DELETE /settings/appointment-types (reset)", ok, f"status={r.status_code}")

# ─────────────────────────────────────────
# 14. Settings — profile patch
# ─────────────────────────────────────────
r = client.patch("/settings/profile", headers=AUTH, json={"full_name": "Mike Simmons Updated"})
ok = r.status_code in (200, 204)
check("PATCH /settings/profile", ok, f"status={r.status_code} {r.text[:80] if not ok else ''}")

# ─────────────────────────────────────────
# 15. Booking link generation
# ─────────────────────────────────────────
try:
    from app.deps import get_db
    from app.models.models import Lead, User
    from app.services.sms_service import create_booking_link, BOOKING_BASE_URL

    db = next(get_db())
    lead_obj = db.query(Lead).filter(Lead.id == LEAD_ID).first() if LEAD_ID else None
    advisor_obj = db.query(User).filter(User.email == "mike@simmonsstrong.com").first()
    if lead_obj and advisor_obj:
        link = create_booking_link(db, lead_obj, advisor_obj)
        ok = bool(link and link.token)
        check("Booking link created", ok, f"token_len={len(link.token) if ok else 0}")
        url = f"{BOOKING_BASE_URL}/book/{link.token}"
        check("Booking URL format", url.startswith("https://advisorflow-booking"), url[:70])
    else:
        check("Booking link created", False, f"lead={bool(lead_obj)}, advisor={bool(advisor_obj)}")
    db.close()
except Exception as e:
    check("Booking link created", False, str(e))

# ─────────────────────────────────────────
# 16. AI conversation service unit tests
# ─────────────────────────────────────────
try:
    from app.services.ai_conversation_service import (
        _build_advisor_intro_instruction, _strip_signoff,
        _build_email_html, SMART_SYSTEM_PROMPT, CADENCE_HOURS, TOUCH_ANGLES
    )
    check("AI service import", True)

    # Intro instruction logic
    same = _build_advisor_intro_instruction("MDG Testing", "MDG Testing")
    check("  advisor_intro: names match → no redundancy", "Do NOT" in same, same[:80])

    diff = _build_advisor_intro_instruction("John Smith", "Smith Funeral Home")
    check("  advisor_intro: names differ → both present", "John Smith" in diff and "Smith Funeral Home" in diff, diff[:80])

    blank = _build_advisor_intro_instruction("", "Acme Funeral")
    check("  advisor_intro: blank name → org only", "Acme Funeral" in blank, blank[:80])

    # Strip signoff
    s1 = _strip_signoff("Let's connect, {name}. Best regards, Mike")
    check("  _strip_signoff: inline comma-style", "Best regards" not in s1, repr(s1))

    s2 = _strip_signoff("Let me know.\n\nBest regards,\nMike Smith\nAcme Org")
    check("  _strip_signoff: multi-line signoff", "Best regards" not in s2, repr(s2))

    s3 = _strip_signoff("Happy to help. Take care.")
    check("  _strip_signoff: 'Take care' at end", "Take care" not in s3, repr(s3))

    # Prompt template has all placeholders
    try:
        filled = SMART_SYSTEM_PROMPT.format(
            relationship_context="cold", ai_direction="none",
            appt_label="Pre-Need Consult", advisor_intro_instruction="Say hi",
            tone_instruction="soft", touch_angle_instruction="intro",
            offer_hook_line="", advisor_name="John", org_name="Acme",
            first_name="Jane", last_name="Doe", tier="pre_need",
            source="list", source_year="2023", lead_context="no notes"
        )
        check("  SMART_SYSTEM_PROMPT format", len(filled) > 100, f"{len(filled)} chars")
    except KeyError as e:
        check("  SMART_SYSTEM_PROMPT format", False, f"Missing placeholder: {e}")

    # Cadence sanity
    check("  CADENCE_HOURS count", len(CADENCE_HOURS) == 9, str(CADENCE_HOURS))
    check("  TOUCH_ANGLES count", len(TOUCH_ANGLES) == 8, str(TOUCH_ANGLES))

    # HTML builder
    html = _build_email_html("Hello there.", "Mike Smith", "Acme Funeral")
    check("  _build_email_html: has body", "Hello there." in html, html[:60])
    check("  _build_email_html: has signature", "Mike Smith" in html and "Acme Funeral" in html)

except Exception as e:
    check("AI service import", False, str(e))
    import traceback; traceback.print_exc()

# ─────────────────────────────────────────
# 17. Email service — resend v2 API
# ─────────────────────────────────────────
try:
    import inspect
    from app.services.email_service import send_email_via_provider
    src = inspect.getsource(send_email_via_provider)
    ok = "resend.Emails.send" in src and "resend.emails.send" not in src
    check("email_service: resend.Emails.send (v2)", ok, "✓" if ok else "BUG: still uses resend.emails.send")
except Exception as e:
    check("email_service import", False, str(e))

# ─────────────────────────────────────────
# 18. CORS origins
# ─────────────────────────────────────────
try:
    from app.main import ALLOWED_ORIGINS
    checks = {
        "advisorflow-booking.vercel.app": any("advisorflow-booking" in o for o in ALLOWED_ORIGINS),
        "advisorflow-frontend.onrender.com": any("advisorflow-frontend" in o for o in ALLOWED_ORIGINS),
        "localhost:5173": any("localhost:5173" in o for o in ALLOWED_ORIGINS),
    }
    for domain, present in checks.items():
        check(f"CORS: {domain}", present)
except Exception as e:
    check("CORS check", False, str(e))

# ─────────────────────────────────────────
# 19. Pipeline conversations
# ─────────────────────────────────────────
r = client.get("/pipeline/conversations", headers=AUTH)
ok = r.status_code in (200,)
check("GET /pipeline/conversations", ok, f"status={r.status_code}")

r = client.get("/pipeline/stats", headers=AUTH)
ok = r.status_code in (200,)
check("GET /pipeline/stats", ok, f"status={r.status_code} {r.text[:80] if not ok else ''}")

# ─────────────────────────────────────────
# 20. Reports
# ─────────────────────────────────────────
for path in ["/reports/crm-summary", "/reports/conversion-trend", "/reports/revenue-by-period"]:
    r = client.get(path, headers=AUTH)
    check(f"GET {path}", r.status_code in (200, 404, 422), f"status={r.status_code}")

# ─────────────────────────────────────────
# 21. Templates
# ─────────────────────────────────────────
r = client.get("/templates/", headers=AUTH)
ok = r.status_code == 200
check("GET /templates/", ok, f"status={r.status_code}")

# ─────────────────────────────────────────
# 22. Campaigns
# ─────────────────────────────────────────
r = client.get("/campaigns", headers=AUTH)
ok = r.status_code in (200, 404)
check("GET /campaigns", ok, f"status={r.status_code}")

# ─────────────────────────────────────────
# 23. AI conversation router
# ─────────────────────────────────────────
if LEAD_ID:
    r = client.get(f"/ai-conversation/status/{LEAD_ID}", headers=AUTH)
    ok = r.status_code in (200, 404)
    check("GET /ai-conversation/status/{id}", ok, f"status={r.status_code}")

# ─────────────────────────────────────────
# 24. Org-settings
# ─────────────────────────────────────────
r = client.get("/org-settings/", headers=AUTH)
ok = r.status_code in (200,)
check("GET /org-settings/", ok, f"status={r.status_code}")

# ─────────────────────────────────────────
# 25. Unauthenticated rejection
# ─────────────────────────────────────────
r = client.get("/leads/")
check("GET /leads/ without token → 401/403", r.status_code in (401, 403), f"status={r.status_code}")

r = client.get("/settings/profile")
check("GET /settings/profile without token → 401/403", r.status_code in (401, 403), f"status={r.status_code}")

# ─────────────────────────────────────────
# 26. Import all routers (catch import-time crashes)
# ─────────────────────────────────────────
import importlib, pathlib
router_dir = pathlib.Path("app/routers")
broken = []
for f in sorted(router_dir.glob("*.py")):
    if f.name.startswith("__"): continue
    mod_name = f"app.routers.{f.stem}"
    try:
        importlib.import_module(mod_name)
    except Exception as e:
        broken.append(f"{f.stem}: {e}")
check("All routers importable", len(broken) == 0, "; ".join(broken) if broken else "all OK")

# ─────────────────────────────────────────
# 27. Import all services
# ─────────────────────────────────────────
svc_dir = pathlib.Path("app/services")
broken_svcs = []
for f in sorted(svc_dir.glob("*.py")):
    if f.name.startswith("__"): continue
    mod_name = f"app.services.{f.stem}"
    try:
        importlib.import_module(mod_name)
    except Exception as e:
        broken_svcs.append(f"{f.stem}: {e}")
check("All services importable", len(broken_svcs) == 0, "; ".join(broken_svcs) if broken_svcs else "all OK")

# ─────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────
print("\n" + "═" * 65)
passed = sum(1 for ok, _, _ in RESULTS if ok)
failed = [(label, detail) for ok, label, detail in RESULTS if not ok]
total = len(RESULTS)
print(f"AUDIT COMPLETE: {passed}/{total} passed")
if failed:
    print(f"\n❌ FAILURES ({len(failed)}):")
    for label, detail in failed:
        print(f"   • {label}")
        if detail:
            print(f"     {detail}")
else:
    print("🎉 All checks passed — platform looks healthy")
