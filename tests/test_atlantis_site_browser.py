"""BROWSER ACCEPTANCE: the real Atlantis page, in a real browser, posting to
the real inquiry handler.

The published file (public-sites/atlantis-light-and-power/index.html) is
served by the app's own /site/<slug> route; Chromium loads it; every one of
the page's request forms is opened from its own button, filled the way a
person fills it and submitted; the page's own JavaScript posts to
/site/<slug>/inquiry, which is answered by the app in-process. Then the
database is checked: the lead is in Atlantis's workspace with the right
request type and fields, an admin was alerted in-app, and nothing was sent.

Skipped when Chromium is not installed (it is in the cloud test container).
"""
import json
import os
import time
from pathlib import Path

import pytest

from app.models.models import EmailMessage, Lead, Message, Notification, Organization, User
from app.services import customer_sites as cs
from app.services.auth_service import create_access_token, hash_password

PAGE = Path(__file__).resolve().parent.parent / "public-sites" / "atlantis-light-and-power" / "index.html"
SLUG = "atlantis-light-and-power"
ORIGIN = "http://atlantis.test"

pw = pytest.importorskip("playwright.sync_api")
CHROME = os.environ.get("PW_CHROMIUM") or "/opt/pw-browsers/chromium"
if not os.path.exists(CHROME):
    pytest.skip("no Chromium in this environment", allow_module_level=True)

# (button that opens the form, what is typed, what the workspace must show)
CASES = [
    ("residential", {"fZip": "75204", "fUsage": "1,250 kWh"}, "Compare electricity options"),
    ("bill", {"fZip": "75204"}, "Analyze my current bill"),
    ("move", {"fZip": "76102", "fMoveDate": "2026-11-15"}, "I'm moving"),
    ("commercial", {"fZip": "Dallas", "fCompany": "Oak Lawn Dental", "fLocations": "2 sites, 3 meters"},
     "Commercial energy procurement"),
    ("partner", {"fCompany": "North Texas Realty"}, "Partner / referral program"),
    ("advisor", {}, "Compare electricity options"),
]


@pytest.fixture
def atlantis(db_session):
    org = Organization(name="Atlantis Light & Power", slug="atlantis-light-power-t",
                       plan="trial", industry="energy")
    db_session.add(org)
    db_session.commit()
    admin = User(organization_id=org.id, email="josh@atlantis.example", password_hash=hash_password("x"),
                 full_name="Josh", role="org_admin", is_active=True, must_change_password=False)
    db_session.add(admin)
    db_session.commit()
    cs.publish(db_session, organization=org, slug=SLUG, html=PAGE.read_text(encoding="utf-8"),
               title=org.name,
               consent_text=("By checking this box you agree that Atlantis Light & Power may contact "
                             "you by phone, text message or email about your request."))
    return org, admin


def _browser_submit(client, kind, fields, n):
    """Open the form from its button, fill it, wait like a person, submit."""
    from playwright.sync_api import sync_playwright
    posted = []

    def route(r):
        req = r.request
        if req.url.startswith(ORIGIN + "/site/") and req.method == "GET":
            res = client.get("/site/%s" % SLUG)
            return r.fulfill(status=res.status_code, body=res.text,
                             headers={"content-type": "text/html; charset=utf-8"})
        if req.url == ORIGIN + "/site/%s/inquiry" % SLUG and req.method == "POST":
            body = json.loads(req.post_data or "{}")
            posted.append(body)
            res = client.post("/site/%s/inquiry" % SLUG, json=body,
                              headers={"user-agent": "acceptance"})
            return r.fulfill(status=res.status_code, body=res.text,
                             headers={"content-type": "application/json"})
        return r.abort()                       # fonts, analytics: nothing leaves

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME)
        for width, height in ((1366, 900), (390, 844)):
            if width == 390 and n % 2 == 0:
                continue                       # half the cases also run at phone size
            page = b.new_page(viewport={"width": width, "height": height})
            page.route("**/*", route)
            page.goto(ORIGIN + "/site/%s" % SLUG)
            page.locator('[data-open-modal="%s"]' % kind).first.click()
            page.wait_for_selector("#leadForm", state="visible")
            page.fill("#fFirst", "Test%d" % n)
            page.fill("#fLast", "Visitor")
            page.fill("#fEmail", "visitor%d.%d@example.com" % (n, width))
            page.fill("#fPhone", "(214) 555-%04d" % (1000 + n))
            for fid, val in fields.items():
                loc = page.locator("#" + fid)
                if loc.is_visible():
                    loc.fill(val)
            page.fill("#fMessage", "Acceptance test %s" % kind)
            page.check("#fConsent")
            time.sleep(2.2)                    # faster than this reads as a bot
            page.click("#submitBtn")
            page.wait_for_selector("#formDone", state="visible", timeout=10000)
            assert page.evaluate("document.documentElement.scrollWidth") <= width
            page.close()
            break
        b.close()
    return posted


@pytest.mark.parametrize("n,case", list(enumerate(CASES)))
def test_every_form_on_the_page_files_into_atlantis_and_alerts_staff(client, db_session, atlantis, n, case):
    org, admin = atlantis
    kind, fields, request_type = case
    posted = _browser_submit(client, kind, fields, n)
    assert len(posted) == 1
    body = posted[0]
    assert body["request_type"] == request_type and body["consent"] is True
    # Fields hidden for this form are never sent.
    if kind != "commercial" and kind != "partner":
        assert "company" not in body
    lead = db_session.query(Lead).filter(Lead.organization_id == org.id,
                                         Lead.first_name == "Test%d" % n).one()
    cf = json.loads(lead.custom_fields or "{}")
    assert cf.get("request_type") == request_type
    if kind == "commercial":
        assert "Oak Lawn Dental" in json.dumps(cf) + (lead.notes or "")
        assert "2 sites, 3 meters" in json.dumps(cf) + (lead.notes or "")
    if kind == "move":
        assert cf.get("move_date") == "2026-11-15"
    # It shows on the advisor's Rate Requests queue, under New.
    h = {"Authorization": "Bearer %s" % create_access_token(admin, db_session)}
    rr = client.get("/rate-requests/?status=new", headers=h)
    assert rr.status_code == 200, rr.text
    assert lead.id in [i["id"] for i in rr.json()["items"]]
    note = db_session.query(Notification).filter(Notification.lead_id == lead.id).one()
    assert note.user_id == admin.id and request_type in note.message
    assert db_session.query(Message).count() == 0
    assert db_session.query(EmailMessage).count() == 0


def test_a_second_request_from_the_same_person_updates_them(client, db_session, atlantis):
    org, _ = atlantis
    _browser_submit(client, "residential", {"fZip": "75204"}, 7)
    _browser_submit(client, "bill", {"fZip": "75204"}, 7)
    assert db_session.query(Lead).filter(Lead.organization_id == org.id,
                                         Lead.first_name == "Test7").count() == 1
