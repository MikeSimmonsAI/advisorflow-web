# -*- coding: utf-8 -*-
"""BROWSER ACCEPTANCE for Wholesale > Team custom access (#439): the real built
frontend (frontend/dist) in Chromium, every API call answered in-process by the
app. Nothing leaves the machine; no email is sent.

  1. The EvoSys owner builds a custom profile from a starter, invites a
     Francis-style wholesaler with it (effective-access preview shown), and
     fine-tunes that person's access in the member drawer.
  2. The invited wholesaler signs in and cannot open team administration;
     the API refuses what the profile doesn't include.
  3. Every tab fits a phone screen without sideways scrolling.
  4. BookaBoost's owner sees only BookaBoost's team.
Screenshots go to $WA_SHOTS (default /tmp/wa-shots). Synthetic identities only.
"""
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.models.models import User
from app.services.auth_service import create_access_token
from tests.test_bookaboost_invites import PASSWORD, w  # noqa: F401
from tests.test_wholesale_access_builder import sent, ws  # noqa: F401

DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
ORIGIN = "https://advisorflow-backend.onrender.com"
SHOTS = Path(os.environ.get("WA_SHOTS", "/tmp/wa-shots"))

pw = pytest.importorskip("playwright.sync_api")
CHROME = os.environ.get("PW_CHROMIUM") or None
if not (DIST / "index.html").exists():
    pytest.skip("no frontend build at frontend/dist", allow_module_level=True)


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


def _page(b, client, db, user, width, height):
    token = create_access_token(user, db)
    me = json.dumps({"id": user.id, "full_name": user.full_name, "email": user.email, "role": user.role,
                     "organization_id": user.organization_id, "must_change_password": False})
    page = b.new_page(viewport={"width": width, "height": height})
    page.route("**/*", _router(client, token))
    page.add_init_script("localStorage.setItem('af_token', %s); localStorage.setItem('af_user', %s);"
                         % (json.dumps(token), json.dumps(me)))
    return page


def _shot(page, name, full=True):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s.png" % name)), full_page=full)


def _fits(page):
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


def test_owner_builds_invites_and_tunes_access_in_a_real_browser(client, ws):
    from playwright.sync_api import sync_playwright
    p, db = ws, ws["db"]
    owner = p["evo_seat"]
    with sync_playwright() as pwr:
        b = _launch(pwr)
        page = _page(b, client, db, owner, 1366, 900)
        page.goto(ORIGIN + "/wholesale/team")
        page.get_by_role("tab", name="Access profiles").wait_for(timeout=30000)
        _shot(page, "01-team-directory-empty-desktop")

        # 1a. a profile from the Acquisition Wholesaler starter, then changed
        page.get_by_role("tab", name="Access profiles").click()
        page.get_by_label("Start from").select_option("acquisition_wholesaler")
        page.get_by_role("button", name="New profile").click()
        drawer = page.get_by_role("dialog")
        drawer.wait_for(timeout=10000)
        drawer.get_by_label("Profile name").fill("Francis - acquisitions")
        drawer.locator("#prof-contracts-create").uncheck()                # not contracts
        drawer.locator("#prof-buyer_intel-view").check()                  # but can look at buyers
        drawer.get_by_text("What they will be able to do").wait_for(timeout=10000)
        _shot(page, "02-permission-builder-desktop", full=False)
        drawer.get_by_role("button", name="Save profile").click()
        page.get_by_role("cell", name="Francis - acquisitions").first.wait_for(timeout=15000)
        _shot(page, "03-access-profiles-desktop")

        # 1b. invite with it; the preview shows before sending
        page.get_by_role("tab", name="Invite").click()
        page.get_by_label("Email", exact=True).fill("francis.sandbox@example.com")
        page.get_by_label("Name (for a new login)").fill("Francis Sandbox")
        page.get_by_label("Access profile").select_option(label="Francis - acquisitions")
        page.get_by_text("Buyer Intelligence:").wait_for(timeout=10000)
        page.get_by_role("button", name="Create invitation").click()
        page.get_by_text("Invitation ready for francis.sandbox@example.com").wait_for(timeout=15000)
        assert "Not emailed" in page.locator(".wt-link").inner_text()
        assert p["sent"] == []
        _shot(page, "04-invite-member-desktop")

        # 1c. fine-tune one person: take away Deals: Edit, see the preview change
        page.get_by_role("tab", name="Team").click()
        row = page.get_by_role("row").filter(has_text="francis.sandbox@example.com")
        row.get_by_role("button", name="Manage access").click()
        drawer = page.get_by_role("dialog")
        drawer.get_by_text("Take away part of the profile").click()
        drawer.locator("#deny-deals-edit").check()
        assert not drawer.locator("#deny-deals-view").is_checked()        # taking away Edit keeps View
        deals = drawer.locator(".wa-preview__list li").filter(has_text="Deal creation and management")
        page.wait_for_function("""() => [...document.querySelectorAll('.wa-preview__list li')]
            .some(li => li.textContent.trim() === 'Deal creation and management: View, Create')""", timeout=10000)
        assert deals.inner_text().strip() == "Deal creation and management: View, Create"
        _shot(page, "05-member-access-and-effective-preview-desktop", full=False)
        drawer.get_by_role("button", name="Save access").click()
        page.get_by_role("dialog").wait_for(state="detached", timeout=15000)

        page.get_by_role("tab", name="History").click()
        page.get_by_text("Changed someone's access").wait_for(timeout=15000)
        _shot(page, "06-access-history-desktop")

        # 3. phone width: every tab, no sideways scrolling
        phone = _page(b, client, db, owner, 390, 844)
        phone.goto(ORIGIN + "/wholesale/team")
        phone.get_by_role("tab", name="Team").wait_for(timeout=30000)
        for name, tab in (("team", "Team"), ("invite", "Invite"), ("profiles", "Access profiles"), ("history", "History")):
            phone.get_by_role("tab", name=tab).click()
            phone.wait_for_timeout(600)
            assert _fits(phone), tab
            _shot(phone, "07-mobile-%s" % name)
        b.close()

    francis = db.query(User).filter(User.email == "francis.sandbox@example.com").one()
    from app.services import wholesale_access as A
    eff = A.effective(db, francis, p["evo"].id)
    assert eff["mode"] == "custom" and eff["permissions"]["deals"] == ["view", "create"]
    assert eff["permissions"]["contracts"] == ["view"]                    # Create was unticked; View stays
    assert eff["permissions"]["buyer_intel"] == ["view"]


def test_restricted_wholesaler_cannot_reach_team_admin_in_the_browser(client, ws):
    from playwright.sync_api import sync_playwright
    p, db = ws, ws["db"]
    prof = client.post("/wholesale/team/access/profiles", headers=p["owner_h"],
                       json={"name": "Field only", "template_key": "field_representative"}).json()
    inv = client.post("/wholesale/team/invites", headers=p["owner_h"],
                      json={"email": "field.sandbox@example.com", "profile_id": prof["id"]}).json()
    token = inv["link"].split("token=")[1]
    assert client.post("/auth/staff-activation/accept", json={"token": token, "new_password": PASSWORD}).status_code == 200
    field = db.query(User).filter(User.email == "field.sandbox@example.com").one()
    with sync_playwright() as pwr:
        b = _launch(pwr)
        page = _page(b, client, db, field, 1366, 900)
        page.goto(ORIGIN + "/wholesale/team")                         # typed straight into the address bar
        page.wait_for_function("location.pathname === '/wholesale/home'", timeout=30000)   # their own dashboard (#439)
        assert page.get_by_role("tab", name="Access profiles").count() == 0
        _shot(page, "08-restricted-user-sent-to-their-screen-desktop")
        codes = page.evaluate("""async () => {
          const t = localStorage.getItem('af_token'); const h = {Authorization: 'Bearer ' + t};
          const g = async (u) => (await fetch('https://advisorflow-backend.onrender.com' + u, {headers: h})).status;
          return {buyers: await g('/wholesale/buyers'), settings: await g('/wholesale/settings'),
                  directory: await g('/wholesale/team/directory'), appts: await g('/wholesale/appointments')};
        }""")
        assert codes == {"buyers": 403, "settings": 403, "directory": 403, "appts": 200}
        # The rail shows only what their access includes (#439), and Wholesale's
        # home sends them to a screen they can open.
        page.goto(ORIGIN + "/wholesale")
        page.wait_for_function("location.pathname === '/wholesale/home'", timeout=30000)   # their own dashboard (#439)
        rail = page.locator("nav").first.inner_text()
        # Strategies is readable with Properties view, so a field rep may see it.
        for hidden in ("Buyer Intelligence", "Deal Room", "Funding", "Investor Outreach", "Wholesale Settings"):
            assert hidden not in rail, hidden
        _shot(page, "10-restricted-user-rail-desktop")
        b.close()


def test_bookaboost_owner_sees_only_bookaboost_team(client, ws):
    from playwright.sync_api import sync_playwright
    p, db = ws, ws["db"]
    client.post("/wholesale/team/access/profiles", headers=p["owner_h"], json={"name": "EVO secret profile", "template_key": "custom"})
    with sync_playwright() as pwr:
        b = _launch(pwr)
        page = _page(b, client, db, p["bab_owner"], 1366, 900)
        page.goto(ORIGIN + "/wholesale/team")
        page.get_by_role("tab", name="Access profiles").wait_for(timeout=30000)
        page.get_by_role("tab", name="Access profiles").click()
        page.wait_for_timeout(500)
        assert "EVO secret profile" not in page.content()
        page.get_by_role("tab", name="Team").click()
        page.wait_for_timeout(500)
        assert "wholesale@evo.example.com" not in page.content()
        _shot(page, "09-bookaboost-team-desktop")
        b.close()
