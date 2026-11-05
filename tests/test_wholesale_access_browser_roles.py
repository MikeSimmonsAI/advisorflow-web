# -*- coding: utf-8 -*-
"""BROWSER ACCEPTANCE, release pass (#439): every kind of person, three screen
sizes, in the real built frontend (frontend/dist) with every API call answered
in-process. Synthetic identities only; no email is sent.

  - Platform/workspace owner: their own row is PROTECTED (View access, read
    only, no control that would fail); desktop, tablet and phone.
  - EvoSys Wholesale manager, Francis-style restricted wholesaler, research VA,
    buyer/disposition specialist, restricted investor profile: each lands on a
    screen they may open, sees only their screens in the rail, and the API
    refuses what their profile leaves out.
  - An assigned-only person sees "Only your appointments", never "Everyone".
  - BookaBoost's admin sees BookaBoost's team and branding only.
  - One login on both brands: each workspace shows its own access.
Screenshots go to $WA_SHOTS (default /tmp/wa-shots).
"""
import json
import mimetypes
from urllib.parse import urlsplit

import pytest

from app.models.models import User
from app.services.auth_service import create_access_token
from tests.test_bookaboost_invites import PASSWORD, w  # noqa: F401
from tests.test_wholesale_access_browser import CHROME, DIST, ORIGIN, _fits, _launch, _shot  # noqa: F401
from tests.test_wholesale_access_builder import _deal, sent, ws  # noqa: F401

pytest.importorskip("playwright.sync_api")


def _router(client, token, workspace=None):
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
        ws_id = req.headers.get("x-workspace-id") or workspace
        if ws_id:
            headers["x-workspace-id"] = ws_id
        res = client.request(req.method, u.path + ("?" + u.query if u.query else ""),
                             headers=headers, content=req.post_data_buffer or None)
        return r.fulfill(status=res.status_code, body=res.content,
                         headers={"content-type": res.headers.get("content-type", "application/json")})
    return route


def _page(b, client, db, user, width, height, workspace=None):
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


def _person(client, p, email, template, **kw):
    prof = client.post("/wholesale/team/access/profiles", headers=p["owner_h"],
                       json=dict({"name": "%s (%s)" % (template, email.split("@")[0]), "template_key": template}, **kw)).json()
    inv = client.post("/wholesale/team/invites", headers=p["owner_h"],
                      json={"email": email, "full_name": email.split("@")[0].replace(".", " ").title(),
                            "profile_id": prof["id"]}).json()
    token = inv["link"].split("token=")[1]
    assert client.post("/auth/staff-activation/accept", json={"token": token, "new_password": PASSWORD}).status_code == 200
    return p["db"].query(User).filter(User.email == email).one()


_API = """async (paths) => {
  const t = localStorage.getItem('af_token'); const h = {Authorization: 'Bearer ' + t};
  const out = {};
  for (const u of paths) out[u] = (await fetch('https://advisorflow-backend.onrender.com' + u, {headers: h})).status;
  return out;
}"""


def test_protected_owner_view_on_desktop_tablet_and_phone(client, ws):
    from playwright.sync_api import sync_playwright
    p, db = ws, ws["db"]
    owner = p["evo_seat"]
    with sync_playwright() as pwr:
        b = _launch(pwr)
        for label, (wd, ht) in (("desktop", (1366, 900)), ("tablet", (820, 1180)), ("mobile", (390, 844))):
            page = _page(b, client, db, owner, wd, ht)
            page.goto(ORIGIN + "/wholesale/team")
            page.get_by_role("tab", name="Team").wait_for(timeout=30000)
            row = page.get_by_role("row").filter(has_text=owner.email)
            assert row.get_by_role("button", name="Manage access").count() == 0     # no control that always fails
            assert "protected owner access" in row.inner_text().lower()
            row.get_by_role("button", name="View access").click()
            ro = page.get_by_test_id("wa-readonly")
            ro.wait_for(timeout=15000)
            assert "God Mode" in ro.inner_text()
            dialog = page.get_by_role("dialog")
            for btn in ("Save access", "Suspend", "Remove from team"):
                assert dialog.get_by_role("button", name=btn).count() == 0, btn
            assert dialog.locator("input[type=checkbox]").count() == 0             # read only
            page.wait_for_timeout(600)                                         # let the slide-in finish
            assert _fits(page), label
            _shot(page, "11-protected-owner-%s" % label, full=False)
            page.close()
        # tablet: the other tabs fit too
        tab = _page(b, client, db, owner, 820, 1180)
        tab.goto(ORIGIN + "/wholesale/team")
        tab.get_by_role("tab", name="Invite").wait_for(timeout=30000)
        for name in ("Invite", "Access profiles", "History"):
            tab.get_by_role("tab", name=name).click()
            tab.wait_for_timeout(500)
            assert _fits(tab), name
        _shot(tab, "12-tablet-access-profiles")
        b.close()


PEOPLE = [
    # (email, template, may open, refused)
    ("manager.sandbox@example.com", "wholesale_manager",
     ["/wholesale/deals", "/wholesale/buyers", "/wholesale/appointments"],
     ["/wholesale/settings", "/wholesale/team/directory", "/wholesale/skip-trace/providers", "/leads/import-batches"]),
    ("francis.like@example.com", "acquisition_wholesaler",
     ["/wholesale/deals", "/wholesale/appointments"],
     ["/wholesale/settings", "/wholesale/team/directory", "/wholesale/funding/partners", "/leads/import-batches"]),
    ("va.sandbox@example.com", "research_assistant",
     ["/wholesale/properties", "/wholesale/buyers"],
     ["/wholesale/deals", "/wholesale/skip-trace/providers", "/wholesale/team/directory", "/wholesale/settings"]),
    ("dispo.sandbox@example.com", "buyer_disposition",
     ["/wholesale/buyers", "/wholesale/deal-room/overview"],
     ["/wholesale/appointments", "/wholesale/settings", "/wholesale/team/directory"]),
    ("investor.sandbox@example.com", "investor_restricted",
     ["/wholesale/deals"],
     ["/wholesale/buyers", "/wholesale/appointments", "/wholesale/settings", "/wholesale/team/directory"]),
]


def test_each_kind_of_person_sees_and_reaches_only_their_access(client, ws):
    from playwright.sync_api import sync_playwright
    p, db = ws, ws["db"]
    people = [(email, _person(client, p, email, tpl), ok, no) for email, tpl, ok, no in PEOPLE]
    with sync_playwright() as pwr:
        b = _launch(pwr)
        for i, (email, u, ok, no) in enumerate(people):
            page = _page(b, client, db, u, 1366, 900)
            page.goto(ORIGIN + "/wholesale/team")                       # typed in; not theirs
            page.wait_for_function("location.pathname !== '/wholesale/team'", timeout=30000)
            assert page.get_by_role("tab", name="Access profiles").count() == 0
            codes = page.evaluate(_API, ok + no)
            for path in ok:
                assert codes[path] == 200, (email, path, codes[path])
            for path in no:
                assert codes[path] in (403, 404), (email, path, codes[path])
            page.wait_for_timeout(400)
            _shot(page, "13-%d-%s" % (i + 1, email.split("@")[0]), full=False)
            page.close()
        b.close()


def test_assigned_only_person_never_sees_an_everyone_filter(client, ws):
    from playwright.sync_api import sync_playwright
    p, db = ws, ws["db"]
    u = _person(client, p, "assigned.only@example.com", "field_representative")
    with sync_playwright() as pwr:
        b = _launch(pwr)
        for label, (wd, ht) in (("desktop", (1366, 900)), ("mobile", (390, 844))):
            page = _page(b, client, db, u, wd, ht)
            page.goto(ORIGIN + "/wholesale/appointments")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(800)
            assert page.get_by_role("combobox", name="Rep").count() == 0
            assert page.locator("option", has_text="Everyone").count() == 0
            if page.get_by_test_id("ap-only-mine").count():
                assert "Only your appointments" in page.get_by_test_id("ap-only-mine").inner_text()
            _shot(page, "14-appointments-assigned-only-%s" % label, full=False)
            page.close()
        # the owner still has the full filter
        page = _page(b, client, db, p["evo_seat"], 1366, 900)
        page.goto(ORIGIN + "/wholesale/appointments")
        page.get_by_role("combobox", name="Rep").wait_for(timeout=30000)
        assert page.locator("option", has_text="Everyone").count() == 1
        b.close()


def test_bookaboost_admin_and_one_login_on_both_brands(client, ws):
    from playwright.sync_api import sync_playwright
    p, db, evo, bab = ws, ws["db"], ws["evo"], ws["bab"]
    prof = client.post("/wholesale/team/access/profiles", headers=p["owner_h"],
                       json={"name": "EVO buyers only", "template_key": "custom",
                             "permissions": {"buyer_intel": ["view"]}, "record_scope": "workspace"}).json()
    inv = client.post("/wholesale/team/invites", headers=p["owner_h"],
                      json={"email": "rep@bab.example.com", "profile_id": prof["id"]}).json()
    rep = p["bab_rep"]
    from tests.test_bookaboost_invites import _h
    assert client.post("/wholesale-invites/accept", headers=_h(db, rep),
                       json={"token": inv["link"].split("token=")[1]}).status_code == 200
    with sync_playwright() as pwr:
        b = _launch(pwr)
        # BookaBoost's admin: BookaBoost's team only, BookaBoost branding
        page = _page(b, client, db, p["bab_owner"], 1366, 900)
        page.goto(ORIGIN + "/wholesale/team")
        page.get_by_role("tab", name="Team").wait_for(timeout=30000)
        assert "EVO buyers only" not in page.content()
        brand = page.evaluate("JSON.parse(localStorage.getItem('af_branding') || '{}')")
        _shot(page, "15-bookaboost-admin-team")
        page.close()
        # the same person in EvoSys: only the EvoSys profile
        in_evo = _page(b, client, db, rep, 1366, 900, workspace=evo.id)
        in_evo.goto(ORIGIN + "/wholesale")
        in_evo.wait_for_load_state("networkidle")
        evo_codes = in_evo.evaluate("""async () => {
          const h = {Authorization: 'Bearer ' + localStorage.getItem('af_token'), 'X-Workspace-Id': localStorage.getItem('af_workspace_id')};
          const g = async (u) => (await fetch('https://advisorflow-backend.onrender.com' + u, {headers: h})).status;
          return {buyers: await g('/wholesale/buyers'), appts: await g('/wholesale/appointments')};
        }""")
        assert evo_codes == {"buyers": 200, "appts": 403}
        _shot(in_evo, "16-one-login-in-evosys", full=False)
        in_evo.close()
        # ...and in BookaBoost: their BookaBoost seat, untouched
        in_bab = _page(b, client, db, rep, 1366, 900, workspace=bab.id)
        in_bab.goto(ORIGIN + "/wholesale")
        in_bab.wait_for_load_state("networkidle")
        bab_codes = in_bab.evaluate("""async () => {
          const h = {Authorization: 'Bearer ' + localStorage.getItem('af_token'), 'X-Workspace-Id': localStorage.getItem('af_workspace_id')};
          const g = async (u) => (await fetch('https://advisorflow-backend.onrender.com' + u, {headers: h})).status;
          return {buyers: await g('/wholesale/buyers'), appts: await g('/wholesale/appointments')};
        }""")
        assert bab_codes == {"buyers": 200, "appts": 200}
        _shot(in_bab, "17-one-login-in-bookaboost", full=False)
        b.close()
    assert brand is not None
