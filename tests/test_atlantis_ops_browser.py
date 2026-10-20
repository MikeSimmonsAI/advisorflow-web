"""BROWSER ACCEPTANCE for the Phase 2 staff screens: the real built frontend
(frontend/dist) in Chromium at PHONE width, every API call answered in-process
by the app. Nothing leaves the machine: the page is served at the backend's own
origin from dist, API requests go to the TestClient, anything else is aborted.

  1. a rep logs an interested resident on Field visits -> a referral lands in
     the workspace, credited to the rep, without sideways scrolling
  2. a manager sees the New requests panel on Tasks & Follow-Up
  3. on Suppliers, the open hand-off is listed and an outcome is recorded from
     the page - and the customer is NOT enrolled
No message or email is created at any point. Skipped without Chromium or a
frontend build (run `npm run build` in frontend/ first).
"""
import json
import mimetypes
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.models.models import EmailMessage, Lead, Message, Organization, User
from app.services.auth_service import create_access_token, hash_password

DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
ORIGIN = "https://advisorflow-backend.onrender.com"     # the API base baked into the build

pw = pytest.importorskip("playwright.sync_api")
CHROME = os.environ.get("PW_CHROMIUM") or ("/opt/pw-browsers/chromium"
                                           if os.path.exists("/opt/pw-browsers/chromium") else None)
if not (DIST / "index.html").exists():
    pytest.skip("no frontend build at frontend/dist", allow_module_level=True)


@pytest.fixture(autouse=True)
def _mounted():
    from app.main import app
    from app.routers.energy_ops_router import router as ops
    if "/energy-ops/queues" not in {getattr(r, "path", "") for r in app.routes}:
        app.include_router(ops)
    yield


@pytest.fixture()
def ws(db_session):
    db = db_session
    org = Organization(name="Atlantis Light & Power", slug="atl-ui-%s" % uuid.uuid4().hex[:6],
                       plan="enterprise", industry="energy", is_active=True)
    db.add(org)
    db.commit()
    admin = User(organization_id=org.id, email="mgr-%s@atl.example.test" % uuid.uuid4().hex[:6],
                 password_hash=hash_password("x"), full_name="Morgan Manager", role="org_admin",
                 is_active=True, must_change_password=False)
    db.add(admin)
    db.commit()
    return db, org, admin


def _router(client, token):
    def route(r):
        req = r.request
        u = urlsplit(req.url)
        if not req.url.startswith(ORIGIN):
            return r.abort()
        f = (DIST / u.path.lstrip("/")) if u.path not in ("", "/") else None
        if req.resource_type == "document" or (f is not None and f.is_file()):
            f = f if (f is not None and f.is_file()) else DIST / "index.html"
            ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            if f.suffix == ".js":
                ctype = "application/javascript"
            return r.fulfill(status=200, body=f.read_bytes(), headers={"content-type": ctype})
        headers = {"authorization": "Bearer %s" % token}
        if req.headers.get("content-type"):
            headers["content-type"] = req.headers["content-type"]
        res = client.request(req.method, u.path + ("?" + u.query if u.query else ""),
                             headers=headers, content=req.post_data_buffer or None)
        return r.fulfill(status=res.status_code, body=res.content,
                         headers={"content-type": res.headers.get("content-type", "application/json")})
    return route


def _launch(p):
    try:
        return p.chromium.launch(executable_path=CHROME) if CHROME else p.chromium.launch()
    except Exception as exc:                                  # noqa: BLE001
        pytest.skip("no Chromium in this environment (%s)" % str(exc).splitlines()[0][:80])


def test_staff_screens_work_in_a_real_browser_at_phone_width(client, ws):
    from playwright.sync_api import sync_playwright
    db, org, admin = ws
    token = create_access_token(admin, db)
    user = json.dumps({"full_name": admin.full_name, "role": admin.role, "organization_id": org.id,
                       "must_change_password": False})
    with sync_playwright() as p:
        b = _launch(p)
        page = b.new_page(viewport={"width": 390, "height": 844})
        page.route("**/*", _router(client, token))
        page.add_init_script("localStorage.setItem('af_token', %s); localStorage.setItem('af_user', %s);"
                             % (json.dumps(token), json.dumps(user)))

        # 1. Field visits: an interested resident who agreed to be contacted
        page.goto(ORIGIN + "/energy/follow-up?view=field")
        form = page.locator('[data-testid="field-visit-form"]')
        form.wait_for(state="visible", timeout=30000)
        form.locator("select").first.select_option("interested")
        form.get_by_label("Address").fill("41 Synthetic Way")
        form.get_by_label("ZIP").fill("76102")
        form.get_by_label("First name").fill("Paula")
        form.get_by_label("Phone").fill("(817) 555-0301")
        form.get_by_label("They said yes").check()
        form.get_by_role("button", name="Save visit").click()
        page.get_by_text("Referral created").wait_for(timeout=15000)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        lead = db.query(Lead).filter_by(organization_id=org.id, source="Atlantis Field Referral").one()
        assert json.loads(lead.custom_fields)["field_rep_id"] == admin.id

        # 2. manager panel on the queues screen
        page.goto(ORIGIN + "/energy/follow-up")
        page.locator('[data-testid="intake-health"]').wait_for(timeout=30000)
        assert "#238" in page.locator('[data-testid="timing-provisional"]').inner_text()
        assert page.locator('[data-kpi="unassigned"] .eo-kpi-n').inner_text().strip() in ("0", "1")

        # 3. an open hand-off, its outcome recorded from the Suppliers page
        assert client.post("/energy-ops/leads/%s/handoff" % lead.id, json={"supplier": "comparepower"},
                           headers={"Authorization": "Bearer %s" % token}).status_code == 200
        page.goto(ORIGIN + "/energy/suppliers")
        row = page.locator("[data-handoff]").first
        row.wait_for(timeout=30000)
        row.get_by_label("Outcome").select_option("awaiting_provider")
        row.get_by_label("Note").fill("Customer said they are finishing the ComparePower sign-up")
        row.get_by_role("button", name="Save").click()
        notice = page.locator('[data-testid="outcome-notice"]')
        notice.wait_for(timeout=15000)
        assert "Not enrolled" in notice.inner_text() and "Paula" in notice.inner_text()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        b.close()

    db.refresh(lead)
    h = json.loads(lead.custom_fields)["supplier_handoffs"][0]
    assert h["status"] == "awaiting_provider" and h["status_source"] == "staff_report"
    assert lead.enrolled_at is None
    assert db.query(Message).count() == 0 and db.query(EmailMessage).count() == 0
