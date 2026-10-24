"""BROWSER ACCEPTANCE: the energy panel on the shared lead page (real build,
Chromium, 390px, every API call answered in-process, everything else aborted).

  * a home customer: next step shown, details saved from the page
  * a business: stage moved, a location added and quoted, "won" refused
    before supplier confirmation, never enrolled
  * a NON-energy workspace's lead page does not show the panel at all
Nothing is sent. Skipped without Chromium or a frontend build.
"""
import json
import uuid

import pytest

from app.models.models import EmailMessage, Lead, Message, Organization, User
from app.services import customer_sites as cs
from app.services.auth_service import create_access_token, hash_password
from tests.test_atlantis_ops_browser import CHROME, DIST, ORIGIN, _launch, _router  # noqa: F401

pw = pytest.importorskip("playwright.sync_api")
if not (DIST / "index.html").exists():
    pytest.skip("no frontend build at frontend/dist", allow_module_level=True)
PAGE = "<!DOCTYPE html><html><head><title>A</title></head><body>x</body></html>"


@pytest.fixture(autouse=True)
def _mounted():
    from app.main import app
    from app.routers.energy_ops_router import router as ops
    if "/energy-ops/queues" not in {getattr(r, "path", "") for r in app.routes}:
        app.include_router(ops)
    yield


def _ws(db, industry):
    org = Organization(name="WS %s" % industry, slug="lp-%s" % uuid.uuid4().hex[:6], plan="enterprise",
                       industry=industry, is_active=True)
    db.add(org)
    db.commit()
    admin = User(organization_id=org.id, email="a-%s@lp.example.test" % uuid.uuid4().hex[:6],
                 password_hash=hash_password("x"), full_name="Avery Admin", role="org_admin",
                 is_active=True, must_change_password=False)
    db.add(admin)
    db.commit()
    return org, admin


def _page(p, client, db, org, admin):
    token = create_access_token(admin, db)
    user = json.dumps({"full_name": admin.full_name, "role": admin.role, "organization_id": org.id,
                       "must_change_password": False})
    b = _launch(p)
    page = b.new_page(viewport={"width": 390, "height": 844})
    page.route("**/*", _router(client, token))
    page.add_init_script("localStorage.setItem('af_token', %s); localStorage.setItem('af_user', %s);"
                         % (json.dumps(token), json.dumps(user)))
    return b, page


def _submit(client, db, org, slug, **kw):
    p = {"first_name": "Lena", "last_name": "Panel", "email": "lena.%s@gmail.com" % uuid.uuid4().hex[:6],
         "phone": "(214) 555-0%03d" % (uuid.uuid4().int % 1000), "form_started_at": 1}
    p.update(kw)
    assert client.post("/site/%s/inquiry" % slug, json=p).status_code == 201
    return db.query(Lead).filter_by(organization_id=org.id, email=p["email"]).one()


def test_energy_panel_on_the_lead_page(client, db_session):
    from playwright.sync_api import sync_playwright
    db = db_session
    org, admin = _ws(db, "energy")
    slug = "lp-site-%s" % uuid.uuid4().hex[:6]
    cs.publish(db, organization=org, slug=slug, html=PAGE, title=org.name)
    home = _submit(client, db, org, slug, request_type="Compare electricity options", service_area="75201",
                   monthly_usage="1,000 kWh")
    biz = _submit(client, db, org, slug, request_type="Commercial energy procurement", company="Synthetic Bakery",
                  service_area="Dallas", locations="2 sites")
    for l in (home, biz):
        l.assigned_to_id = admin.id
    db.commit()

    with sync_playwright() as p:
        b, page = _page(p, client, db, org, admin)
        # home customer
        page.goto(ORIGIN + "/leads/%s?tab=overview" % home.id)
        panel = page.locator('[data-testid="energy-qualification"]')
        panel.wait_for(timeout=30000)
        assert "Review options" in page.locator('[data-testid="next-step"]').inner_text()
        assert page.locator('[data-supplier="chariot"]').inner_text().count("Can be handed off") == 1
        panel.get_by_role("button", name="Update details").click()
        form = page.locator('[data-testid="qualification-form"]')
        form.get_by_label("Current supplier").fill("Synthetic Power Co")
        form.get_by_role("button", name="Save").click()
        page.locator('[data-field="current_supplier"]', has_text="Synthetic Power Co").wait_for(timeout=15000)
        box = panel.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 391

        # business
        page.goto(ORIGIN + "/leads/%s?tab=overview" % biz.id)
        com = page.locator('[data-testid="energy-commercial"]')
        com.wait_for(timeout=30000)
        assert page.locator('[data-supplier="chariot"]').inner_text().count("Not for this customer") == 1
        com.get_by_label("Move to stage").select_option("quote_requested")
        com.get_by_role("button", name="Save stage").click()
        page.locator('[data-testid="commercial-stage"]', has_text="Quote requested").wait_for(timeout=15000)
        com.get_by_label("Site address").fill("300 Synthetic Ave, Dallas")
        com.get_by_role("button", name="Add location").click()
        com.get_by_label("Quote for 300 Synthetic Ave, Dallas").select_option("requested")
        page.wait_for_timeout(800)
        com.get_by_label("Move to stage").select_option("won")
        com.get_by_role("button", name="Save stage").click()
        page.locator('[data-testid="commercial-msg"]').wait_for(timeout=15000)
        assert "Quote requested" in page.locator('[data-testid="commercial-stage"]').inner_text()
        b.close()

    db.refresh(home)
    db.refresh(biz)
    assert json.loads(home.custom_fields)["current_supplier"] == "Synthetic Power Co"
    c = json.loads(biz.custom_fields)["commercial"]
    assert c["stage"] == "quote_requested" and c["locations"][0]["quote_status"] == "requested"
    assert biz.enrolled_at is None and home.enrolled_at is None
    assert db.query(Message).count() == 0 and db.query(EmailMessage).count() == 0


def test_other_industries_do_not_get_the_panel(client, db_session):
    from playwright.sync_api import sync_playwright
    db = db_session
    org, admin = _ws(db, "funeral")
    lead = Lead(organization_id=org.id, first_name="Fern", last_name="Other", email="fern@gmail.com",
                status="new", assigned_to_id=admin.id)
    db.add(lead)
    db.commit()
    with sync_playwright() as p:
        b, page = _page(p, client, db, org, admin)
        page.goto(ORIGIN + "/leads/%s?tab=overview" % lead.id)
        page.get_by_text("Fern").first.wait_for(timeout=30000)
        page.wait_for_timeout(1500)
        assert page.locator('[data-testid="energy-lead-panel"]').count() == 0
        b.close()
