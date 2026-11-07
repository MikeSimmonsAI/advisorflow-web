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


def _router(client, page_origin=ORIGIN):
    """Static build + in-process API, forwarding the page's own Authorization
    (the investor portal sends its own token). page_origin lets a page be
    served from a brand host (e.g. evosyspro.live) while the API stays on
    ORIGIN, exactly as in production."""
    import mimetypes
    from urllib.parse import urlsplit

    def route(r):
        req = r.request
        u = urlsplit(req.url)
        on_page = req.url.startswith(page_origin)
        if not (on_page or req.url.startswith(ORIGIN)):
            return r.abort()
        f = (DIST / u.path.lstrip("/")) if u.path not in ("", "/") else None
        if on_page and (req.resource_type == "document" or (f is not None and f.is_file())):
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


_ICON_JS = """async () => {
  const l = document.querySelector("link[rel~='icon']");
  if (!l) return null;
  const r = await fetch(l.href); const blob = await r.blob();
  return await new Promise(ok => { const f = new FileReader(); f.onload = () => ok(f.result); f.readAsDataURL(blob); });
}"""


def _icon(page):
    """The page's current favicon as a data URL, plus its title."""
    return {"src": page.evaluate(_ICON_JS), "title": page.title()}


def _favicon_sheet(b, icons, titles, name):
    """Headless Chromium draws no tab strip, so render each page's real favicon
    the way a browser tab shows it (16px + title) and enlarged (64px)."""
    rows = "".join(
        '<div class="row"><div class="tab"><img src="%s" width="16" height="16"><span>%s</span></div>'
        '<img src="%s" width="64" height="64"><code>%s</code></div>' % (v["src"] or "", v["title"], v["src"] or "", k)
        for k, v in icons.items())
    pg = b.new_page(viewport={"width": 900, "height": 120 + 90 * len(icons)})
    pg.set_content("<style>body{font:14px Arial;background:#dee1e6;padding:20px}.row{display:flex;align-items:center;gap:24px;"
                   "margin:10px 0}.tab{background:#fff;border-radius:8px 8px 0 0;padding:8px 12px;width:260px;display:flex;gap:8px;"
                   "align-items:center}code{color:#333}</style>" + rows)
    _save(pg, name, titles)
    pg.close()


def test_capture_staff_screens(client, ws):
    from playwright.sync_api import sync_playwright
    p, db = ws, ws["db"]
    titles, icons = {}, {}
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
        # Mobile sidebar opened (where the logo lives on a phone).
        pg = _page(b, client, db, p["evo_seat"], 390, 844)
        pg.goto(ORIGIN + "/wholesale")
        pg.wait_for_load_state("networkidle")
        pg.click(".mobile-menu-btn")
        _save(pg, "05b-evosys-mobile-sidebar-open", titles)
        pg.close()
        pg = _page(b, client, db, p["bab_owner"], 390, 844)
        pg.goto(ORIGIN + "/wholesale")
        pg.wait_for_load_state("networkidle")
        pg.click(".mobile-menu-btn")
        _save(pg, "07b-bookaboost-mobile-sidebar-open", titles)
        pg.close()
        # EvoSys Pro parent (non-wholesale) screen: must not change.
        pg = _page(b, client, db, p["evo_seat"], 1440, 900)
        pg.goto(ORIGIN + "/")
        pg.wait_for_load_state("networkidle")
        _save(pg, "12-evosys-parent-home", titles)
        icons["12-evosys-parent-home"] = _icon(pg)
        pg.goto(ORIGIN + "/wholesale")
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(1200)
        icons["02-evosys-deal-operations"] = _icon(pg)
        pg.close()
        pg = _page(b, client, db, p["bab_owner"], 1440, 900)
        pg.goto(ORIGIN + "/wholesale")
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(1200)
        icons["06-bookaboost-deal-operations"] = _icon(pg)
        pg.close()
        # EvoSys Pro parent brand surfaces outside My Deal Room: must not change.
        for name, origin in (("13-evosys-pro-login", "https://evosyspro.live"),
                             ("14-bookaboost-login", "https://bookaboost.com")):
            pg = b.new_page(viewport={"width": 1440, "height": 900})
            pg.route("**/*", _router(client, origin))
            pg.goto(origin + "/login")
            pg.wait_for_load_state("networkidle")
            _save(pg, name, titles)
            icons[name] = _icon(pg)
            pg.close()
        _favicon_sheet(b, icons, titles, "11-favicons")
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
        icons = {"08-investor-portal": _icon(page)}
        page.close()
        page = b.new_page(viewport={"width": 390, "height": 844})
        page.route("**/*", _router(client))
        page.add_init_script("localStorage.setItem('af_investor_token', %s);" % json.dumps(itok))
        page.goto(ORIGIN + "/investors")
        page.wait_for_load_state("networkidle")
        _save(page, "09-investor-portal-mobile", titles)
        page.close()
        for name, origin in (("10-investor-login-evosyspro", "https://evosyspro.live"),
                             ("10b-investor-login-bookaboost", "https://bookaboost.com")):
            page = b.new_page(viewport={"width": 1440, "height": 900})
            page.route("**/*", _router(client, origin))
            page.goto(origin + "/investors")
            page.wait_for_load_state("networkidle")
            _save(page, name, titles)
            icons[name] = _icon(page)
            page.close()
        _favicon_sheet(b, icons, titles, "11b-favicons-investor")
        b.close()
    (Path(OUT) / LABEL / "titles-investor.json").write_text(json.dumps(titles, indent=2))
