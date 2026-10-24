"""BROWSER ACCEPTANCE: the real Atlantis page, in a real browser, posting to
the real inquiry handler - at desktop AND phone width.

The published file (public-sites/atlantis-light-and-power/index.html) is
served by the app's own /site/<slug> route; Chromium loads it; each request
form is opened from its own button, filled the way a person fills it and
submitted; the page's own JavaScript posts to /site/<slug>/inquiry, answered
in-process. Then the database is checked: the lead is in Atlantis's workspace
with the right request, on Rate Requests under New, an admin was alerted
in-app, the page did not scroll sideways, and nothing was sent.

Ported from PR #116 (c1a15f23, which stays on HOLD). Change: #116's loop
broke after the desktop pass, so phone width never ran; here every form runs
at both sizes. Network: every request other than the page and its form post
is aborted - nothing leaves the machine. Skipped when no Chromium is found.
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
CHROME = os.environ.get("PW_CHROMIUM") or ("/opt/pw-browsers/chromium"
                                           if os.path.exists("/opt/pw-browsers/chromium") else None)

# (button that opens the form, what is typed, request_type posted, request_kind recorded)
CASES = [
    ("residential", {"fZip": "75204", "fUsage": "1,250 kWh"}, "Compare electricity options", "rate_review"),
    ("bill", {"fZip": "75204"}, "Analyze my current bill", "bill_review"),
    ("move", {"fZip": "76102", "fMoveDate": "2026-11-15"}, "I'm moving", "move"),
    ("commercial", {"fZip": "Dallas", "fCompany": "Synthetic Dental", "fLocations": "2 sites, 3 meters"},
     "Commercial energy procurement", "commercial"),
    ("partner", {"fCompany": "Synthetic Realty"}, "Partner / referral program", "partner"),
    ("advisor", {}, "Compare electricity options", "rate_review"),
]
SIZES = [("desktop", 1366, 900), ("phone", 390, 844)]


def _launch(p):
    try:
        return p.chromium.launch(executable_path=CHROME) if CHROME else p.chromium.launch()
    except Exception as exc:                                  # noqa: BLE001
        pytest.skip("no Chromium in this environment (%s)" % str(exc).splitlines()[0][:80])


@pytest.fixture
def atlantis(db_session):
    org = Organization(name="Atlantis Light & Power", slug="atlantis-light-power-t",
                       plan="trial", industry="energy")
    db_session.add(org)
    db_session.commit()
    admin = User(organization_id=org.id, email="admin@atlantis.example.test", password_hash=hash_password("x"),
                 full_name="Admin", role="org_admin", is_active=True, must_change_password=False)
    db_session.add(admin)
    db_session.commit()
    cs.publish(db_session, organization=org, slug=SLUG, html=PAGE.read_text(encoding="utf-8"),
               title=org.name,
               consent_text=("By checking this box you agree that Atlantis Light & Power may contact "
                             "you by phone, text message or email about your request."))
    return org, admin


def _browser_submit(client, kind, fields, n, width, height):
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
        b = _launch(p)
        page = b.new_page(viewport={"width": width, "height": height})
        page.route("**/*", route)
        page.goto(ORIGIN + "/site/%s" % SLUG)
        # The first button a person can actually see at this width (the header
        # copy is collapsed on a phone; the page's own buttons are not).
        page.locator('[data-open-modal="%s"]:visible' % kind).first.click()
        page.wait_for_selector("#leadForm", state="visible")
        # A field hidden for this form must not be on screen (the [hidden] fix).
        if kind not in ("commercial", "partner"):
            assert not page.locator("#fCompany").is_visible()
        page.fill("#fFirst", "Test%d" % n)
        page.fill("#fLast", "Visitor")
        page.fill("#fEmail", "visitor%d@example.test" % n)
        page.fill("#fPhone", "(214) 555-%04d" % (1000 + n))
        for fid, val in fields.items():
            loc = page.locator("#" + fid)
            if loc.is_visible():
                loc.fill(val)
        page.fill("#fMessage", "Acceptance test %s" % kind)
        page.check("#fConsent")
        time.sleep(2.2)                        # faster than this reads as a bot
        page.click("#submitBtn")
        page.wait_for_selector("#formDone", state="visible", timeout=10000)
        assert page.evaluate("document.documentElement.scrollWidth") <= width
        page.close()
        b.close()
    return posted


@pytest.mark.parametrize("size", SIZES, ids=[s[0] for s in SIZES])
@pytest.mark.parametrize("n,case", list(enumerate(CASES)), ids=[c[0] for c in CASES])
def test_every_form_on_the_page_files_into_atlantis_and_alerts_staff(
        client, db_session, atlantis, n, case, size):
    org, admin = atlantis
    kind, fields, request_type, request_kind = case
    posted = _browser_submit(client, kind, fields, n, size[1], size[2])
    assert len(posted) == 1
    body = posted[0]
    assert body["request_type"] == request_type and body["consent"] is True
    if kind not in ("commercial", "partner"):
        assert "company" not in body           # hidden fields are never sent
    lead = db_session.query(Lead).filter(Lead.organization_id == org.id,
                                         Lead.first_name == "Test%d" % n).one()
    cf = json.loads(lead.custom_fields or "{}")
    assert cf.get("request_type") == request_type and cf.get("request_kind") == request_kind
    if kind == "commercial":
        assert "Synthetic Dental" in json.dumps(cf) + (lead.notes or "") + (getattr(lead, "company_name", None) or "")
        assert cf.get("locations") == "2 sites, 3 meters"
    if kind == "move":
        assert cf.get("move_date") == "2026-11-15"
    h = {"Authorization": "Bearer %s" % create_access_token(admin, db_session)}
    rr = client.get("/rate-requests/?status=new", headers=h)
    assert rr.status_code == 200, rr.text
    assert lead.id in [i["id"] for i in rr.json()["items"]]
    note = db_session.query(Notification).filter(Notification.lead_id == lead.id).one()
    assert note.user_id == admin.id and request_type in note.message
    assert db_session.query(Message).count() == 0
    assert db_session.query(EmailMessage).count() == 0
