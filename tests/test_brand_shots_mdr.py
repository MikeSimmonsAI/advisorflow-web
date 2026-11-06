# -*- coding: utf-8 -*-
"""My Deal Room rebrand - BEFORE/AFTER evidence (branding only).

Captures the same screens, same size, same synthetic data, before and after
the rename, plus each screen's browser title, so the two sets can be
compared pixel by pixel: only the product name (and, once supplied, the logo)
may differ. Set BRAND_SHOTS=/path and BRAND_LABEL=before|after.
Skipped unless BRAND_SHOTS is set (it is evidence, not a regression test)."""
import json
import os
from pathlib import Path

import pytest

from tests.test_bookaboost_invites import w  # noqa: F401
from tests.test_deal_room import room  # noqa: F401
from tests.test_wholesale_access_builder import sent, ws  # noqa: F401
from tests.test_wholesale_access_browser import DIST, ORIGIN, _launch
from tests.test_wholesale_access_browser_roles import _page


def _router(client):
    """Static build + in-process API, forwarding the page's own Authorization
    (the investor portal sends its own token)."""
    import mimetypes
    from urllib.parse import urlsplit

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
        headers = {k: v for k, v in req.headers.items() if k in ("authorization", "content-type")}
        res = client.request(req.method, u.path + ("?" + u.query if u.query else ""), headers=headers,
                             content=req.post_data_buffer or None)
        return r.fulfill(status=res.status_code, body=res.content,
                         headers={"content-type": res.headers.get("content-type", "application/json")})
    return route

OUT = os.environ.get("BRAND_SHOTS")
LABEL = os.environ.get("BRAND_LABEL", "x")
pytestmark = pytest.mark.skipif(not OUT, reason="evidence capture only (set BRAND_SHOTS)")


def _save(page, name, titles, full=False):
    d = Path(OUT) / LABEL
    d.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(1200)
    page.screenshot(path=str(d / ("%s.png" % name)), full_page=full)
    titles[name] = page.title()


def test_capture_staff_screens(client, ws):
    from playwright.sync_api import sync_playwright
    p, db = ws, ws["db"]
    titles = {}
    with sync_playwright() as pwr:
        b = _launch(pwr)
        login = b.new_page(viewport={"width": 1440, "height": 900})
        login.route("**/*", _router(client))
        login.goto(ORIGIN + "/login")
        _save(login, "01-login", titles)
        login.close()
        for name, path, user, size in (("02-evosys-deal-operations", "/wholesale", p["evo_seat"], (1440, 900)),
                                       ("03-evosys-team", "/wholesale/team", p["evo_seat"], (1440, 900)),
                                       ("04-evosys-buyers", "/wholesale/buyers", p["evo_seat"], (1440, 900)),
                                       ("05-evosys-mobile", "/wholesale", p["evo_seat"], (390, 844)),
                                       ("06-bookaboost-deal-operations", "/wholesale", p["bab_owner"], (1440, 900)),
                                       ("07-bookaboost-team", "/wholesale/team", p["bab_owner"], (1440, 900))):
            pg = _page(b, client, db, user, size[0], size[1])
            pg.goto(ORIGIN + path)
            pg.wait_for_load_state("networkidle")
            _save(pg, name, titles)
            pg.close()
        b.close()
    extra = {"public_404": client.get("/__no_such_seller_page__", headers={"host": "sell.example.com"}).text[:300]}
    m = Path(__file__).resolve().parent.parent / "frontend" / "public" / "manifest-field.webmanifest"
    extra["field_manifest_name"] = json.loads(m.read_text()).get("name")
    (Path(OUT) / LABEL / "titles-staff.json").write_text(json.dumps({"titles": titles, **extra}, indent=2))


def test_capture_investor_portal(client, room):
    from playwright.sync_api import sync_playwright
    from tests.test_deal_room import _accept, _invite
    _inv, token = _invite(client, room["A"], "Riley Synth", "riley@practice.invalid")
    inv_h = _accept(client, token, full_name="Riley Synth", kind="individual")
    itok = inv_h["Authorization"].split(" ", 1)[1]
    titles = {}
    with sync_playwright() as pwr:
        b = _launch(pwr)
        page = b.new_page(viewport={"width": 1440, "height": 900})
        page.route("**/*", _router(client))
        page.add_init_script("localStorage.setItem('af_investor_token', %s);" % json.dumps(itok))
        page.goto(ORIGIN + "/investors")
        page.wait_for_load_state("networkidle")
        _save(page, "08-investor-portal", titles)
        b.close()
    (Path(OUT) / LABEL / "titles-investor.json").write_text(json.dumps(titles, indent=2))
