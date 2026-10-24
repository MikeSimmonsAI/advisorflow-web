"""The public Wholesale website served by the backend on wholesale.evosyspro.live.

  * on the site's hostname: pages and assets serve, the API does not exist,
    source files (.php/.py/.md) never serve, and the buyer form lands a buyer
    in EXACTLY the configured organization - creating nothing anywhere else;
  * on any other hostname the site's internal routes are 404;
  * a repeated submission (same submission_id) writes nothing new;
  * contact permission is required; a bot-filled honeypot creates nothing;
  * a cross-site Origin is refused.
"""
import pytest

from app.models.models import Organization, Platform
from app.models.wholesale_models import WholesaleBuyBox, WholesaleBuyer, WholesaleSettings
from app.routers import wholesale_public_site_router as W

HOST = "wholesale.evosyspro.live"
KEY_EVO = "evo-site-key-aaaaaaaaaaaaaaaaaaaaaa"
KEY_OTHER = "other-site-key-bbbbbbbbbbbbbbbbbbbb"


def _org(db, platform, name, key):
    org = Organization(name=name, slug=name.lower().replace(" ", "-"), plan="standard",
                       industry="real_estate", platform_id=platform.id)
    db.add(org)
    db.flush()
    db.add(WholesaleSettings(organization_id=org.id, public_intake_key=key))
    db.commit()
    return org


@pytest.fixture()
def site(db_session, monkeypatch):
    db = db_session
    evo = Platform(name="EvoSys Pro", slug="evosyspro")
    db.add(evo)
    db.flush()
    a = _org(db, evo, "EVO Integrated Solutions LLC", KEY_EVO)
    b = _org(db, evo, "Other Wholesaler", KEY_OTHER)
    monkeypatch.setenv("WHOLESALE_SITE_ORG_ID", a.id)
    monkeypatch.delenv("WHOLESALE_SITE_HOSTS", raising=False)
    from app.services import entitlements
    monkeypatch.setattr(entitlements, "org_has_feature", lambda org, feature: True)
    return {"db": db, "evo": a, "other": b}


def H(**extra):
    return dict({"host": HOST}, **extra)


def form(**over):
    base = {"intent": "buyer", "full_name": "Site Test Investor", "email": "site.test@example.com",
            "phone": "(214) 555-0142", "markets": "Dallas, Tarrant County", "strategy": "Buy & Hold",
            "budget": "$100,000–$250,000", "notes": "synthetic test", "contact_permission": "yes",
            "submission_id": "WEB-test-1", "organization_id": "someone-else"}
    base.update(over)
    return base


def test_pages_and_assets_serve_on_the_site_host(client, site):
    r = client.get("/", headers=H())
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "EvoSys Pro" in r.text
    for path in ("/buyers.html", "/sell.html", "/careers.html", "/platform.html", "/contact.html",
                 "/assets/site.css", "/assets/site.js", "/robots.txt", "/sitemap.xml"):
        assert client.get(path, headers=H()).status_code == 200, path


def test_source_files_and_the_api_are_not_reachable_through_the_site_host(client, site):
    for path in ("/intake.php", "/bridge.php", "/resend.php", "/build.py", "/DEPLOYMENT.md",
                 "/CLAUDE_HANDOFF.md", "/health", "/docs", "/auth/me", "/../app/main.py",
                 "/site-intake/wholesale/%s/buyer-inquiry" % KEY_EVO):
        r = client.get(path, headers=H())
        assert r.status_code == 404, path
        assert "Page not found" in r.text, path
    r = client.post("/site-intake/wholesale/%s/buyer-inquiry" % KEY_EVO, json=form(), headers=H())
    assert r.status_code == 404


def test_site_routes_are_404_on_any_other_host(client, site):
    assert client.get("/_wholesale_site/page/index.html").status_code == 404
    assert client.post("/_wholesale_site/intake", data=form()).status_code == 404
    assert site["db"].query(WholesaleBuyer).count() == 0


def test_buyer_form_lands_in_the_configured_org_only(client, site):
    db = site["db"]
    r = client.post("/intake.php", data=form(), headers=H(origin="https://" + HOST))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["success"] is True and body["reference"]
    buyers = db.query(WholesaleBuyer).all()
    assert len(buyers) == 1 and buyers[0].organization_id == site["evo"].id
    assert db.query(WholesaleBuyer).filter_by(organization_id=site["other"].id).count() == 0
    assert db.query(WholesaleBuyBox).filter_by(organization_id=site["evo"].id).count() == 1


def test_repeat_submission_writes_nothing_new(client, site):
    db = site["db"]
    first = client.post("/intake.php", data=form(submission_id="WEB-repeat"), headers=H()).json()
    second = client.post("/intake.php", data=form(submission_id="WEB-repeat"), headers=H()).json()
    assert first["reference"] == second["reference"]
    assert db.query(WholesaleBuyer).count() == 1
    assert db.query(WholesaleBuyBox).count() == 1


def test_permission_honeypot_origin_and_validation(client, site):
    db = site["db"]
    assert client.post("/intake.php", data=form(contact_permission=""), headers=H()).status_code == 422
    r = client.post("/intake.php", data=form(company_url="http://spam"), headers=H())
    assert r.status_code == 200 and r.json()["reference"] == "RECEIVED"
    assert client.post("/intake.php", data=form(), headers=H(origin="https://evil.example")).status_code == 403
    r = client.post("/intake.php", data=form(strategy="Wholesale", submission_id="WEB-bad"), headers=H())
    assert r.status_code == 422 and r.json()["success"] is False
    assert db.query(WholesaleBuyer).count() == 0


def test_missing_org_configuration_refuses_without_creating_anything(client, site, monkeypatch):
    monkeypatch.delenv("WHOLESALE_SITE_ORG_ID")
    r = client.post("/intake.php", data=form(), headers=H())
    assert r.status_code == 503
    assert site["db"].query(WholesaleBuyer).count() == 0


def test_path_mapping_is_strict():
    assert W.site_path("GET", "/") == "/_wholesale_site/page/index.html"
    assert W.site_path("GET", "/assets/site.css") == "/_wholesale_site/page/assets/site.css"
    assert W.site_path("GET", "/../secret") == "/_wholesale_site/not-found"
    assert W.site_path("POST", "/intake.php") == "/_wholesale_site/intake"
    assert W.site_path("POST", "/auth/login") == "/_wholesale_site/not-found"
    assert W.site_path("DELETE", "/index.html") == "/_wholesale_site/not-found"
    assert not any(k.endswith((".php", ".py", ".md")) for k in W.served_files())
