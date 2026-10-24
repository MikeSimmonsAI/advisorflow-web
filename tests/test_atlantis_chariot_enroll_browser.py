"""BROWSER ACCEPTANCE: "Enroll online with Chariot" beside "Advisor enrolls
manually", on the real build in Chromium at 390px and 1366px. Every API call is
answered in-process and Chariot itself is a recording fake: nothing leaves.

  switched off: both options show, the online form can be filled but Submit
  is disabled and nothing is sent;
  switched on: find plans -> pick a meter -> details -> submit -> the
  submission is queued on Suppliers -> a person confirms "enrolled" -> the
  customer is enrolled (same Enroll action). The SSN reached Chariot and was
  stored nowhere.
"""
import json
import uuid

import pytest

from app.models.models import AuditLogEntry, Lead, Organization, User
from app.models.provider_transaction_models import ProviderTransaction
from app.services import customer_sites as cs
from app.services.auth_service import create_access_token, hash_password
from app.services.providers import chariot
from tests.test_atlantis_ops_browser import DIST, ORIGIN, _launch, _router  # noqa: F401

pw = pytest.importorskip("playwright.sync_api")
if not (DIST / "index.html").exists():
    pytest.skip("no frontend build at frontend/dist", allow_module_level=True)
PAGE = "<!DOCTYPE html><html><head><title>A</title></head><body>x</body></html>"
SSN = "666730809"
PLANS = [{"UtilityId": 1, "UtilityName": "Oncor", "Status": "Success",
          "Plans": [{"ProductId": 23639, "Title": "Synthetic Saver 12", "Terms": 12, "kWh1000": "13.9",
                     "EFL": "https://example.com/efl.pdf"}]}]
ADDRS = {"Status": "Success", "TotalAddresses": 1, "Addresses": [
    {"ESIID": "10032789426879101", "FullAddress": "12 Synthetic St, Dallas TX 75201", "SwitchHoldIndicator": "0"}]}
DONE = {"Status": "Enrollment has been completed", "Message": "Passed CreditCheck & No Deposit required",
        "RefrenceNumber": "R-UI-1"}


class _Resp:
    def __init__(self, body):
        self._body, self.status_code, self.text = body, 200, json.dumps(body)

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    from app.main import app
    from app.routers.energy_ops_router import router as ops
    from app.routers.rate_requests_router import router as rr
    paths = {getattr(r, "path", "") for r in app.routes}
    if "/energy-ops/queues" not in paths:
        app.include_router(ops)
    if "/rate-requests/summary" not in paths:
        app.include_router(rr)
    monkeypatch.setenv(chariot.ENV_API_KEY, "test-key-not-a-real-credential")
    monkeypatch.setenv(chariot.ENV_ENVIRONMENT, "test")
    monkeypatch.delenv(chariot.ENV_ALLOW_TRANSACTIONS, raising=False)
    yield


@pytest.fixture()
def wire():
    calls = []

    def fake(method, url, *, params=None, files=None, timeout=None):
        calls.append({"url": url, "files": files})
        if url.endswith("GetPlans"):
            return _Resp(PLANS)
        if url.endswith("GetErcotAddresses"):
            return _Resp(ADDRS)
        return _Resp(DONE)
    prev = chariot.set_transport(fake)
    fake.calls = calls
    yield fake
    chariot.set_transport(prev)


def _setup(client, db):
    org = Organization(name="Atlantis E", slug="ce-%s" % uuid.uuid4().hex[:6], plan="enterprise",
                       industry="energy", is_active=True)
    db.add(org)
    db.commit()
    mgr = User(organization_id=org.id, email="m-%s@example.com" % uuid.uuid4().hex[:5],
               password_hash=hash_password("x"), full_name="Morgan", role="org_admin",
               is_active=True, must_change_password=False)
    db.add(mgr)
    db.commit()
    slug = "ce-site-%s" % uuid.uuid4().hex[:6]
    cs.publish(db, organization=org, slug=slug, html=PAGE, title=org.name)
    email = "ella.%s@gmail.com" % uuid.uuid4().hex[:5]
    assert client.post("/site/%s/inquiry" % slug, json={
        "first_name": "Ella", "last_name": "Synthetic", "email": email, "phone": "(214) 555-0601",
        "consent": True, "request_type": "Compare electricity options", "service_area": "75201",
        "form_started_at": 1}).status_code == 201
    lead = db.query(Lead).filter_by(organization_id=org.id, email=email).one()
    lead.assigned_to_id = mgr.id
    db.commit()
    return org, mgr, lead


def _page(p, client, db, org, user, width):
    token = create_access_token(user, db)
    info = json.dumps({"full_name": user.full_name, "role": user.role, "organization_id": org.id,
                       "must_change_password": False})
    b = _launch(p)
    page = b.new_page(viewport={"width": width, "height": 900})
    page.route("**/*", _router(client, token))
    page.add_init_script("localStorage.setItem('af_token', %s); localStorage.setItem('af_user', %s);"
                         % (json.dumps(token), json.dumps(info)))
    return b, page


def _sideways(page):
    return page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")


@pytest.mark.parametrize("width", [390, 1366])
def test_both_enroll_options_and_the_online_path_end_to_end(client, db_session, wire, monkeypatch, width):
    from playwright.sync_api import sync_playwright
    db = db_session
    org, mgr, lead = _setup(client, db)
    with sync_playwright() as p:
        # switched OFF: both options, form usable, Submit disabled, nothing sent
        b, page = _page(p, client, db, org, mgr, width)
        page.goto(ORIGIN + "/leads/%s?tab=overview" % lead.id)
        page.locator('[data-testid="enroll-options"]').wait_for(timeout=30000)
        assert page.locator('[data-option="manual"]').is_visible()
        assert page.locator('[data-testid="switched-off"]').is_visible()
        assert page.get_by_role("button", name="Submit to Chariot").is_disabled()
        assert not _sideways(page)
        b.close()

        # switched ON
        monkeypatch.setenv(chariot.ENV_ALLOW_TRANSACTIONS, "true")
        b, page = _page(p, client, db, org, mgr, width)
        page.goto(ORIGIN + "/leads/%s?tab=overview" % lead.id)
        form = page.locator('[data-testid="online-form"]')
        form.wait_for(timeout=30000)
        form.get_by_role("button", name="Find plans").click()
        form.locator('[data-testid="plans"] input[type=radio]').first.check()
        form.get_by_label("Service street address").fill("12 Synthetic St")
        form.get_by_role("button", name="Find meter").click()
        form.locator('[data-testid="meters"] input[type=radio]').first.check()
        form.get_by_label("Start date (MM/DD/YYYY)").fill("11/20/2026")
        form.get_by_label("Date of birth (MM/DD/YYYY)").fill("12/12/1990")
        form.get_by_label("SSN").fill(SSN)
        form.get_by_label("The customer authorized").check()
        assert not _sideways(page)
        form.get_by_role("button", name="Submit to Chariot").click()
        page.locator('[data-testid="online-result"]').wait_for(timeout=30000)
        assert "confirm with the customer" in page.locator('[data-testid="online-result"]').inner_text()

        page.goto(ORIGIN + "/energy/suppliers")
        row = page.locator("[data-submission]").first
        row.wait_for(timeout=30000)
        row.get_by_label("What happened").select_option("enrolled")
        row.get_by_label("How you verified it").fill("Customer read back the Chariot welcome email")
        row.get_by_role("button", name="Save").click()
        page.locator('[data-testid="chariot-notice"]', has_text="is enrolled").wait_for(timeout=30000)
        assert not _sideways(page)
        b.close()

    db.expire_all()
    lead = db.query(Lead).get(lead.id)
    assert lead.enrolled_at is not None and lead.tier == "contract_signed"
    assert sum(1 for c in wire.calls if c["url"].endswith("SubmitEnrollment")) == 1
    sub = next(c for c in wire.calls if c["url"].endswith("SubmitEnrollment"))
    assert SSN in json.dumps(sub["files"], default=str)                     # reached Chariot
    for r in db.query(ProviderTransaction).all():                           # stored nowhere
        assert SSN not in json.dumps([r.request_echo, r.response_body, r.plan_snapshot, r.raw_message])
    for e in db.query(AuditLogEntry).all():
        assert SSN not in str(e.details)
    assert SSN not in (lead.custom_fields or "") + (lead.notes or "")
