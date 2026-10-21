"""BROWSER ACCEPTANCE: Team & Alerts + New Requests on the real build, in
Chromium, at PHONE (390px) and DESKTOP (1366px) width. Every API call is
answered in-process; everything else is aborted; nothing is sent.

  manager turns Ava's in-app alerts on from Team & Alerts (and sees
  "Email not configured") -> a website request arrives -> Ava gets the alert
  -> Ava claims it on New Requests -> it is hers, with its task -> the manager
  switches Ava off and Ben on from the screen -> the next request alerts Ben,
  not Ava. No sideways scrolling on either screen.
"""
import json
import uuid

import pytest

from app.models.models import EmailMessage, Lead, Message, Notification, Organization, User
from app.models.work_models import LeadTask
from app.services import customer_sites as cs
from app.services.auth_service import create_access_token, hash_password
from tests.test_atlantis_ops_browser import DIST, ORIGIN, _launch, _router  # noqa: F401

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


def _user(db, org, role, name):
    u = User(organization_id=org.id, email="%s-%s@example.com" % (name, uuid.uuid4().hex[:6]),
             password_hash=hash_password("x"), full_name=name, role=role, is_active=True,
             must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _open(p, client, db, org, user, width):
    token = create_access_token(user, db)
    info = json.dumps({"full_name": user.full_name, "role": user.role, "organization_id": org.id,
                       "must_change_password": False})
    b = _launch(p)
    page = b.new_page(viewport={"width": width, "height": 900})
    page.route("**/*", _router(client, token))
    page.add_init_script("localStorage.setItem('af_token', %s); localStorage.setItem('af_user', %s);"
                         % (json.dumps(token), json.dumps(info)))
    return b, page


def _form(client, slug, first):
    p = {"first_name": first, "last_name": "Synthetic", "email": "%s.%s@gmail.com" % (first.lower(), uuid.uuid4().hex[:5]),
         "phone": "(214) 555-0%03d" % (uuid.uuid4().int % 1000), "consent": True,
         "request_type": "Compare electricity options", "service_area": "75201", "form_started_at": 1}
    assert client.post("/site/%s/inquiry" % slug, json=p).status_code == 201
    return p


def _no_sideways(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


@pytest.mark.parametrize("width", [390, 1366])
def test_manager_and_advisor_run_alerts_and_the_shared_inbox(client, db_session, width):
    from playwright.sync_api import sync_playwright
    db = db_session
    org = Organization(name="Atlantis UI", slug="tau-%s" % uuid.uuid4().hex[:6], plan="enterprise",
                       industry="energy", is_active=True)
    db.add(org)
    db.commit()
    mgr, ava, ben = _user(db, org, "org_admin", "Morgan"), _user(db, org, "advisor", "Ava"), _user(db, org, "advisor", "Ben")
    slug = "tau-site-%s" % uuid.uuid4().hex[:6]
    cs.publish(db, organization=org, slug=slug, html=PAGE, title=org.name)

    with sync_playwright() as p:
        b, page = _open(p, client, db, org, mgr, width)
        page.goto(ORIGIN + "/energy/follow-up?view=team")
        page.locator('[data-testid="team-table"]').wait_for(timeout=30000)
        assert page.locator('[data-testid="email-state"]').inner_text().strip() == "Email not configured"
        page.locator('[data-member="%s"] [data-flag="in_app"]' % ava.id).check()
        page.locator('[data-member="%s"] [data-flag="in_app"]:checked' % ava.id).wait_for(timeout=15000)
        _no_sideways(page)
        b.close()

        first = _form(client, slug, "First")
        lead = db.query(Lead).filter_by(organization_id=org.id, email=first["email"].lower()).one()
        assert {n.user_id for n in db.query(Notification).filter_by(lead_id=lead.id)} == {mgr.id, ava.id}

        b, page = _open(p, client, db, org, ava, width)
        page.goto(ORIGIN + "/energy/follow-up?view=new")
        row = page.locator('[data-request="%s"]' % lead.id)
        row.wait_for(timeout=30000)
        _no_sideways(page)
        row.get_by_role("button", name="Claim").click()
        page.locator('[data-testid="new-requests-msg"]', has_text="is yours").wait_for(timeout=15000)
        page.locator('[data-state="mine"]').click()
        page.locator('[data-request="%s"] a' % lead.id).wait_for(timeout=15000)       # now openable
        b.close()

        db.refresh(lead)
        assert lead.assigned_to_id == ava.id
        assert db.query(LeadTask).filter_by(lead_id=lead.id, source="website").one().assigned_to_id == ava.id

        b, page = _open(p, client, db, org, mgr, width)
        page.goto(ORIGIN + "/energy/follow-up?view=team")
        page.locator('[data-testid="team-table"]').wait_for(timeout=30000)
        page.locator('[data-member="%s"] [data-flag="in_app"]' % ava.id).uncheck()
        page.locator('[data-member="%s"] [data-flag="in_app"]:not(:checked)' % ava.id).wait_for(timeout=15000)
        page.locator('[data-member="%s"] [data-flag="in_app"]' % ben.id).check()
        page.locator('[data-member="%s"] [data-flag="in_app"]:checked' % ben.id).wait_for(timeout=15000)
        b.close()

    second = _form(client, slug, "Second")
    nxt = db.query(Lead).filter_by(organization_id=org.id, email=second["email"].lower()).one()
    assert {n.user_id for n in db.query(Notification).filter_by(lead_id=nxt.id)} == {mgr.id, ben.id}
    assert db.query(Message).count() == 0 and db.query(EmailMessage).count() == 0
