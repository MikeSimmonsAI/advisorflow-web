# -*- coding: utf-8 -*-
"""BROWSER ACCEPTANCE - role dashboards (#439). The real built frontend in
Chromium; every API call answered in-process by the app. Synthetic people and
records only; nothing leaves the machine; no email.

For each of the seven starter experiences a synthetic person is invited with
that profile, given a few records of their own (and some that are NOT theirs),
signs in, lands on My Dashboard, and the test checks what the screen shows
against what their access allows - then screenshots it for the side-by-side
with the owner's mockups. Mockup 7 (restricted investor) is the existing Deal
Room investor portal, signed in as an invited investor.
Screenshots: $WA_SHOTS (default /tmp/wa-dash).
"""
import json
import mimetypes
import os
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.models.evosense_models import EvoSenseFollowUp, EvoSenseProperty
from app.models.models import User
from app.models.wholesale_models import (WholesaleApproval, WholesaleBuyer, WholesaleBuyerMatch, WholesaleBuyerOutreach,
                                         WholesaleEvent)
from app.models.wholesale_ops_models import (WholesaleCanvassDoor, WholesaleCanvassRoute, WholesaleFieldAppointment,
                                             WholesaleFieldVisit, WholesaleSellerCallback)
from app.services.auth_service import create_access_token
from tests.test_bookaboost_invites import PASSWORD, w  # noqa: F401
from tests.test_wholesale_access_builder import _deal, sent, ws  # noqa: F401
from tests.test_deal_room import room  # noqa: F401

pytest.importorskip("playwright.sync_api")
DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
ORIGIN = "https://advisorflow-backend.onrender.com"
SHOTS = Path(os.environ.get("WA_SHOTS", "/tmp/wa-dash"))
CHROME = os.environ.get("PW_CHROMIUM") or None
if not (DIST / "index.html").exists():
    pytest.skip("no frontend build at frontend/dist", allow_module_level=True)


def _router(client, token=None, workspace=None):
    def route(r):
        req = r.request
        u = urlsplit(req.url)
        if not req.url.startswith(ORIGIN):
            return r.abort()
        f = (DIST / u.path.lstrip("/")) if u.path not in ("", "/") else None
        if req.resource_type == "document" or (f is not None and f.is_file()):
            f = f if (f is not None and f.is_file()) else DIST / "index.html"
            ctype = "application/javascript" if f.suffix == ".js" else (mimetypes.guess_type(str(f))[0] or "application/octet-stream")
            return r.fulfill(status=200, body=f.read_bytes(), headers={"content-type": ctype})
        headers = {}
        auth = req.headers.get("authorization") or ("Bearer %s" % token if token else None)
        if auth:
            headers["authorization"] = auth
        if req.headers.get("content-type"):
            headers["content-type"] = req.headers["content-type"]
        if req.headers.get("x-workspace-id") or workspace:
            headers["x-workspace-id"] = req.headers.get("x-workspace-id") or workspace
        res = client.request(req.method, u.path + ("?" + u.query if u.query else ""),
                             headers=headers, content=req.post_data_buffer or None)
        return r.fulfill(status=res.status_code, body=res.content,
                         headers={"content-type": res.headers.get("content-type", "application/json")})
    return route


def _launch(p):
    try:
        return p.chromium.launch(executable_path=CHROME) if CHROME else p.chromium.launch()
    except Exception as exc:                                  # noqa: BLE001
        pytest.skip("no Chromium (%s)" % str(exc).splitlines()[0][:80])


def _page(b, client, db, user, width=1440, height=1000, workspace=None):
    token = create_access_token(user, db)
    me = json.dumps({"id": user.id, "full_name": user.full_name, "email": user.email, "role": user.role,
                     "organization_id": user.organization_id, "must_change_password": False})
    page = b.new_page(viewport={"width": width, "height": height})
    page.route("**/*", _router(client, token, workspace))
    init = "localStorage.setItem('af_token', %s); localStorage.setItem('af_user', %s);" % (json.dumps(token), json.dumps(me))
    if workspace:
        init += "localStorage.setItem('af_workspace_id', %s);" % json.dumps(workspace)
    page.add_init_script(init)
    return page


def _shot(page, name, full=True):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s.png" % name)), full_page=full)


def _fits(page):
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


def _person(client, p, email, name, template, h=None, **kw):
    h = h or p["owner_h"]
    prof = client.post("/wholesale/team/access/profiles", headers=h,
                       json=dict({"name": "%s - %s" % (template.replace("_", " ").title(), name.split()[0]),
                                  "template_key": template}, **kw)).json()
    inv = client.post("/wholesale/team/invites", headers=h, json={"email": email, "full_name": name, "profile_id": prof["id"]}).json()
    tok = inv["link"].split("token=")[1]
    assert client.post("/auth/staff-activation/accept", json={"token": tok, "new_password": PASSWORD}).status_code == 200
    return prof, p["db"].query(User).filter(User.email == email).one()


_NAMES = ["Magnolia Ave", "Cedar Ln", "Lakeview Dr", "Red Oak St", "Willow Bend Ct", "Pecan Hollow Rd", "Bluebonnet Way",
          "Live Oak Blvd", "Trinity Pkwy", "Cottonwood Trl", "Mesquite Ct", "Sycamore St", "Juniper Ridge", "Elm Fork Dr",
          "Brazos Ave", "Hackberry Ln", "Prairie Wind Dr", "Stockyards Rd", "Lone Star Ct", "Caddo St", "Cypress Bend",
          "Post Oak Rd", "Rio Vista Dr", "Hillcrest Ave", "Bowie St", "Crockett Way", "Travis Park", "Austin Ridge",
          "Houston Pl", "Fannin Ct"]
_CITIES = [("Fort Worth", "76104"), ("Dallas", "75216"), ("Arlington", "76016"), ("Grand Prairie", "75052"), ("Mansfield", "76063")]
_SEEDED = {"n": 0}


def _streets():
    """Five distinct synthetic addresses per person seeded (no repeats on the
    whole-workspace screens)."""
    k = _SEEDED["n"]
    _SEEDED["n"] += 1
    return [("%d Synth %s" % (600 + 17 * (k * 5 + i), _NAMES[(k * 5 + i) % len(_NAMES)]),) + _CITIES[i] for i in range(5)]


STREETS = [("615 Synth Magnolia Ave", "Fort Worth", "76104")]


def _seed(db, org_id, uid, other_id, owner_id):
    """A believable, SYNTHETIC book of work for `uid`, plus records that belong
    to someone else (which must never show for an assigned-only person)."""
    now = datetime.utcnow()
    deals = []
    STREETS = _streets()
    for i, (st, city, z) in enumerate(STREETS):
        d, prop = _deal(db, org_id, assigned_to=uid if i < 4 else other_id, addr=st)
        prop.city, prop.zip_code, prop.owner_name = city, z, ["Carlos Synth", "Angela Synth", "James Synth", "Lisa Synth", "Mark Synth"][i]
        d.stage = ["seller_engaged", "qualifying", "analysis", "offer_sent", "outreach_active"][i]
        evo = EvoSenseProperty(organization_id=org_id, street_address=st, city=city, state="TX", zip_code=z,
                               county="Tarrant" if city != "Dallas" else "Dallas", status=["needs_you", "high_opportunity", "ready_for_outreach", "needs_you", "waiting_for_data"][i],
                               urgency_score=[92, 87, 78, 74, 69][i], signal_count=[4, 3, 3, 2, 2][i], promoted_deal_id=d.id,
                               next_best_action=["Confirm appointment", "Follow up call", "Send property details", "Book appointment", "Initial outreach call"][i],
                               has_conflicts=(i in (1, 4)), data_confidence=["high", "medium", "high", "low", "medium"][i], discovered_at=now - timedelta(days=i))
        db.add(evo)
        db.add(WholesaleEvent(organization_id=org_id, deal_id=d.id, action="note", actor_type="user",
                              actor_user_id=uid if i < 4 else other_id,
                              summary=["Called seller", "Texted owner", "Note added", "Called - left voicemail", "Lead assigned"][i],
                              created_at=now - timedelta(hours=2 + 3 * i)))
        deals.append(d)
    db.flush()
    db.add_all([
        WholesaleSellerCallback(organization_id=org_id, due_at=now + timedelta(hours=3), assigned_to_id=uid, notes="Carlos Synth - wants a 4 PM call"),
        WholesaleSellerCallback(organization_id=org_id, due_at=now - timedelta(hours=1), assigned_to_id=uid, notes="Angela Synth - asked for the offer range"),
        WholesaleSellerCallback(organization_id=org_id, due_at=now - timedelta(hours=1), assigned_to_id=other_id, notes="Other rep - their seller callback"),
        EvoSenseFollowUp(organization_id=org_id, property_id="evo-synth", due_at=now - timedelta(hours=2), assigned_to_id=uid, status="due", note="Send property info to interested seller"),
        WholesaleFieldAppointment(organization_id=org_id, starts_at=now + timedelta(hours=20), assigned_to_id=uid, status="scheduled", address=STREETS[0][0], title="Walkthrough"),
        WholesaleFieldAppointment(organization_id=org_id, starts_at=now + timedelta(days=3), assigned_to_id=uid, status="scheduled", address=STREETS[2][0], title="Seller meeting"),
        WholesaleFieldAppointment(organization_id=org_id, starts_at=now + timedelta(days=1), assigned_to_id=other_id, status="scheduled", address="Other rep - their appointment"),
        WholesaleApproval(organization_id=org_id, deal_id=deals[1].id, kind="offer", status="pending", amount=125000),
        WholesaleApproval(organization_id=org_id, deal_id=deals[4].id, kind="contract", status="pending"),
    ])
    route = WholesaleCanvassRoute(organization_id=org_id, name="Fort Worth south loop", area="76104", assigned_to_id=uid,
                                  route_date=__import__("app.services.wholesale_appointments", fromlist=["day_window"]).day_window()[0], status="open")
    db.add(route)
    db.flush()
    for i, (st, city, z) in enumerate(STREETS):
        db.add(WholesaleCanvassDoor(organization_id=org_id, route_id=route.id, address="%s, %s" % (st, city), sort=i + 1,
                                    result="talked" if i == 0 else None, knocked_at=now if i == 0 else None))
    done = WholesaleFieldAppointment(organization_id=org_id, starts_at=now - timedelta(hours=5), assigned_to_id=uid,
                                     status="completed", outcome="offer_made", address=STREETS[1][0])
    db.add(done)
    db.flush()
    db.add(WholesaleFieldVisit(organization_id=org_id, appointment_id=done.id, interview="{}"))
    pool = ["Synth Practice Capital", "River City Synth Investments", "Hilltop Synth Properties", "Blue Tower Synth Capital",
            "Victory Synth Land", "Trinity Synth Holdings", "Lone Star Synth Buyers", "Prairie Synth Partners",
            "Cedar Synth Equity", "Magnolia Synth Fund", "Brazos Synth Homes", "Pecan Synth Ventures"]
    k = (_SEEDED["n"] - 1) * 2
    for i, name in enumerate([pool[(k + j) % len(pool)] for j in range(3)]):
        b = WholesaleBuyer(organization_id=org_id, company_name=name, contact_name=name.split()[0], is_active=True)
        db.add(b)
        db.flush()
        db.add(WholesaleBuyerMatch(organization_id=org_id, deal_id=deals[i].id, buyer_id=b.id, score=[98, 96, 94][i]))
        db.add(WholesaleBuyerOutreach(organization_id=org_id, deal_id=deals[i].id, buyer_id=b.id, channel="email",
                                      status="sent", sent_at=now - timedelta(days=1), created_at=now - timedelta(days=1)))
    db.commit()
    return deals


ROLES = [
    # email, name, template, panels that MUST show, panels that must NOT show
    ("francis.synth@example.com", "Francis Synth", "acquisition_wholesaler",
     ["My Work Queue", "Deal Pipeline", "Your Opportunities"], ["Team Performance", "Provider Readiness", "Buyer Match Queue"]),
    ("morgan.synth@example.com", "Morgan Synth", "wholesale_manager",
     ["Team Performance", "Deal Pipeline", "Needs a Decision", "Live Team Activity"], ["Provider Readiness"]),
    ("tasha.synth@example.com", "Tasha Synth", "field_representative",
     ["Today's Route", "Upcoming Appointments", "Recent Field Visits"], ["Deal Pipeline", "Team Performance", "Provider Readiness"]),
    ("amber.synth@example.com", "Amber Synth", "buyer_disposition",
     ["Deal Room Status", "Buyer Match Queue", "Outreach Activity (7 days)"], ["Team Performance", "Calls to Return", "Provider Readiness"]),
    ("nina.synth@example.com", "Nina Synth", "research_assistant",
     ["Research Queue", "My Areas"], ["Team Performance", "Deal Pipeline", "Calls to Return" if False else "Needs a Decision"]),
    ("keisha.synth@example.com", "Keisha Synth", "virtual_assistant",
     ["My Work Queue", "Calls to Return", "Upcoming Appointments"], ["Deal Pipeline", "Team Performance", "Provider Readiness", "Deal Room Status"]),
]


def test_each_starter_experience_in_the_browser(client, ws):
    from playwright.sync_api import sync_playwright
    p, db = ws, ws["db"]
    with sync_playwright() as pwr:
        b = _launch(pwr)
        for i, (email, name, tpl, must, never) in enumerate(ROLES, start=1):
            prof, u = _person(client, p, email, name, tpl)
            _seed(db, p["evo"].id, u.id, p["evo_seat"].id, p["evo_seat"].id)
            page = _page(b, client, db, u)
            page.goto(ORIGIN + "/")                                         # signed in: where do they land?
            page.wait_for_function("location.pathname === '/wholesale/home'", timeout=30000)
            page.get_by_role("heading", name="Welcome back").first.wait_for(timeout=30000) if False else None
            page.locator(".wh-kpi").first.wait_for(timeout=30000)
            text = page.locator(".wh").inner_text()
            for title in must:
                assert title in text, (tpl, title)
            for title in never:
                assert title not in text, (tpl, title)
            if tpl in ("acquisition_wholesaler", "field_representative", "virtual_assistant"):
                assert "Other rep" not in text, tpl                          # assigned-only: never someone else's records
            else:
                assert prof["record_scope"] in ("team", "workspace"), tpl
            assert _fits(page), tpl
            _shot(page, "%02d-%s-desktop" % (i, tpl))
            # the first shortcut opens a real screen they may use (no bounce)
            first = page.locator(".wh-action").first
            target = first.get_attribute("href").split("?")[0]
            first.click()
            page.wait_for_function("location.pathname === %s" % json.dumps(target), timeout=30000)
            page.close()
        b.close()


def test_acquisition_wholesaler_gains_buyer_panel_by_override_tablet_and_phone(client, ws):
    from playwright.sync_api import sync_playwright
    p, db = ws, ws["db"]
    prof, u = _person(client, p, "francis.override@example.com", "Francis Override", "acquisition_wholesaler")
    _seed(db, p["evo"].id, u.id, p["evo_seat"].id, p["evo_seat"].id)
    with sync_playwright() as pwr:
        b = _launch(pwr)
        page = _page(b, client, db, u)
        page.goto(ORIGIN + "/wholesale/home")
        page.locator(".wh-kpi").first.wait_for(timeout=30000)
        assert "Buyer Match Queue" not in page.locator(".wh").inner_text()
        assert client.put("/wholesale/team/members/%s/access" % u.id, headers=p["owner_h"],
                          json={"profile_id": prof["id"], "grants": {"buyer_intel": ["view"]}}).status_code == 200
        page.reload()
        page.get_by_text("Buyer Match Queue").wait_for(timeout=30000)
        assert page.locator(".wh-action", has_text="Buyer Intelligence").count() == 1   # (may sit under More)
        page.wait_for_function("(document.querySelector('aside.sidebar') || {}).innerText.includes('Buyer Intelligence')",
                               timeout=30000)                                # the sidebar gains it too
        _shot(page, "08-acquisition-plus-buyer-intelligence-desktop")
        page.close()
        for label, (wd, ht) in (("tablet", (820, 1180)), ("mobile", (390, 844))):
            pg = _page(b, client, db, u, wd, ht)
            pg.goto(ORIGIN + "/wholesale/home")
            pg.locator(".wh-kpi").first.wait_for(timeout=30000)
            pg.wait_for_timeout(500)
            assert _fits(pg), label
            _shot(pg, "08-acquisition-%s" % label)
            pg.close()
        b.close()


def test_restricted_va_cannot_reach_beyond_their_dashboard(client, ws):
    from playwright.sync_api import sync_playwright
    p, db = ws, ws["db"]
    _prof, u = _person(client, p, "keisha.locked@example.com", "Keisha Locked", "virtual_assistant")
    _seed(db, p["evo"].id, u.id, p["evo_seat"].id, p["evo_seat"].id)
    with sync_playwright() as pwr:
        b = _launch(pwr)
        page = _page(b, client, db, u)
        for typed in ("/wholesale/settings", "/wholesale/evosense/controls", "/wholesale/team", "/wholesale/buyers"):
            page.goto(ORIGIN + typed)
            page.wait_for_function("location.pathname !== %s" % json.dumps(typed), timeout=30000)
        codes = page.evaluate("""async () => {
          const h = {Authorization: 'Bearer ' + localStorage.getItem('af_token')};
          const g = async (u) => (await fetch('https://advisorflow-backend.onrender.com' + u, {headers: h})).status;
          return {settings: await g('/wholesale/settings'), providers: await g('/wholesale/skip-trace/providers'),
                  team: await g('/wholesale/team/directory'), deals: await g('/wholesale/deals'),
                  home: await g('/wholesale/home'), god: await g('/god/customers')};
        }""")
        assert codes == {"settings": 403, "providers": 403, "team": 403, "deals": 403, "home": 200, "god": 403}, codes
        page.wait_for_function("(document.querySelector('aside.sidebar') || {}).innerText.includes('My Dashboard')", timeout=30000)
        rail = page.locator("aside.sidebar").inner_text()
        for hidden in ("Providers & Controls", "Wholesale Settings", "Deal Room", "Buyer Intelligence", "Team\n"):
            assert hidden not in rail, hidden
        page.goto(ORIGIN + "/wholesale/home")
        page.locator(".wh-kpi").first.wait_for(timeout=30000)
        _shot(page, "09-va-restricted-proof", full=False)
        b.close()


def test_owner_preview_and_bookaboost_dashboard(client, ws):
    from playwright.sync_api import sync_playwright
    p, db = ws, ws["db"]
    prof, u = _person(client, p, "preview.target@example.com", "Preview Target", "field_representative")
    _seed(db, p["evo"].id, p["evo_seat"].id, u.id, p["evo_seat"].id)
    # BookaBoost: its own manager and its own records - never EvoSys's
    bprof, bu = _person(client, p, "bab.manager@example.com", "Bree Synth", "wholesale_manager", h=p["bab_h"])
    _seed(db, p["bab"].id, bu.id, p["bab_owner"].id, p["bab_owner"].id)
    with sync_playwright() as pwr:
        b = _launch(pwr)
        page = _page(b, client, db, p["evo_seat"])
        page.goto(ORIGIN + "/wholesale/team")
        page.get_by_role("tab", name="Access profiles").click()
        page.locator("a", has_text="Preview dashboard").first.click()
        page.get_by_text("Preview:").wait_for(timeout=30000)
        assert "Today's Route" in page.locator(".wh").inner_text()
        _shot(page, "10-owner-previews-a-profile")
        page.close()
        owner = _page(b, client, db, p["evo_seat"])
        owner.goto(ORIGIN + "/wholesale/home")
        owner.locator(".wh-kpi").first.wait_for(timeout=30000)
        _shot(owner, "11-owner-dashboard")
        owner.close()
        bab = _page(b, client, db, bu)
        bab.goto(ORIGIN + "/wholesale/home")
        bab.locator(".wh-kpi").first.wait_for(timeout=30000)
        text = bab.locator(".wh").inner_text()
        assert "Team Performance" in text
        evo_ids = {d.id for d in db.query(__import__("app.models.wholesale_models", fromlist=["WholesaleDeal"]).WholesaleDeal)
                   .filter_by(organization_id=p["evo"].id)}
        assert not any(i in bab.content() for i in evo_ids)                # no EvoSys deal links on BookaBoost
        _shot(bab, "12-bookaboost-manager-dashboard")
        b.close()


def test_restricted_investor_portal_shows_only_shared_deals(client, room, db_session):
    """Mockup 7 is the EXISTING Deal Room investor portal: an invited investor
    signs in and sees only the deals shared with them (no staff screens)."""
    from playwright.sync_api import sync_playwright
    from tests.test_deal_room import _accept, _invite
    inv, token = _invite(client, room["A"], "Riley Synth", "riley@practice.invalid")
    I = _accept(client, token, full_name="Riley Synth", kind="individual")
    client.patch("/investor/me/identity", headers=I, json={"kind": "individual", "legal_name": "Riley Synth",
                                                            "preferred_name": "Riley", "phone": "+12145550199"})
    me = client.get("/investor/me", headers=I).json()
    th = client.get("/investor/threads", headers=I).json()["threads"]
    concierge = [t for t in th if t["kind"] == "concierge"][0]
    for msg in ("Yes, actively buying", "Single family houses", "Dallas County", "Up to $200k"):
        client.post("/investor/threads/%s/messages" % concierge["id"], headers=I, json={"text": msg})
    box = [b for b in client.get("/investor/me", headers=I).json()["buy_boxes"] if b["source"] == "ai_conversation"][0]
    client.put("/investor/me/buy-boxes/%s" % box["id"], headers=I,
               json={"property_types": ["single_family"], "counties": ["Dallas"], "max_price": 200000})
    from tests.test_deal_room import DRS
    lst = client.post(DRS + "/deals/%s/listing" % room["deal_id"], headers=room["H"]).json()
    client.post(DRS + "/listings/%s/approve" % lst["id"], headers=room["H"])
    assert client.post(DRS + "/listings/%s/publish" % lst["id"], headers=room["H"]).status_code == 200
    dash = client.get("/investor/dashboard", headers=I).json()
    assert dash["counts"]["opportunities"] == 1                               # the one deal shared with them
    assert me["account"]["identity_complete"] is not None
    itok = I["Authorization"].split(" ", 1)[1]
    assert client.get("/wholesale/deals", headers=I).status_code in (401, 403)        # staff side: closed
    assert client.get("/wholesale/home", headers=I).status_code in (401, 403)
    with sync_playwright() as pwr:
        b = _launch(pwr)
        page = b.new_page(viewport={"width": 1440, "height": 1000})
        page.route("**/*", _router(client))
        page.add_init_script("localStorage.setItem('af_investor_token', %s);" % json.dumps(itok))
        page.goto(ORIGIN + "/investors")
        page.get_by_text("Welcome Back").first.wait_for(timeout=30000)
        page.wait_for_timeout(2500)
        _shot(page, "07-restricted-investor-portal")
        text = page.locator("body").inner_text()
        assert "91 Deal Room Ct" in text or "Deal Room Ct" in text or "$129,000" in text      # their shared deal
        assert "Rival Practice" not in text                                   # never another investor's data
        b.close()
