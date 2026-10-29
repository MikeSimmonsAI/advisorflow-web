# -*- coding: utf-8 -*-
"""LANE-E-BOOKABOOST [parity] (#272): BookaBoost Wholesale gets the right result
from the ONE shared Wholesale engine, and EvoSys reads exactly as before.

Each test drives a real endpoint (TestClient) with synthetic data, as a
BookaBoost workspace and - where the behaviour could have moved - as EvoSys:

  links     investor invites, signing links and owner permission links point at
            the workspace's own brand address (app.bookaboost.live); EvoSys keeps
            its environment variable / default
  site      the public Wholesale website maps a hostname to a workspace; a
            BookaBoost host serves a neutral page with its own name and its
            buyer form lands in that workspace only; EvoSys's host is unchanged
  sms       the seller SMS opt-in/HELP copy names the workspace's product
  text      contract notice, funding disclaimer, repair-cost source and the
            Deal Room design label carry no EvoSys name for BookaBoost
  switches  emailing / texting deal sheets to buyers needs the server switch AND
            the workspace's own switch; both start off; nothing is sent
  status    a disabled integration shows as not configured / paused, and
            BookaBoost sees the same list of integrations EvoSys sees
  sales     one Back Office per brand; a brand's sales staff never see another
            brand's records; an empty Back Office is an empty board; screens
            name the brand being sold
Nothing here reaches a provider: email is captured, SMS is never enabled.
"""
import itertools
import json
import re
from types import SimpleNamespace

import pytest

from app.models.models import Organization, Platform, User
from app.models.sales_models import (BrandSalesOrg, Membership, Opportunity, ROLE_SALES_MANAGER,
                                     ROLE_SALES_REP, SCOPE_BRAND_SALES_ORG, STAGE_PROSPECT)
from app.models.wholesale_models import WholesaleBuyer, WholesaleSettings
from app.services.auth_service import create_access_token, hash_password

import test_evosys_esign as ES
from test_evosys_esign import mail  # noqa: F401  (fixture)

_SEQ = itertools.count(1)
BAB_APP = "https://app.bookaboost.live"
BAB_FEATURES = ["leads", "users", "reports", "imports", "compliance", "sms", "email", "ai_assist",
                "calendar", "availability", "booking", "wholesale_real_estate", "wholesale_workspace"]
EVO_FEATURES = ["leads", "users", "reports", "imports", "compliance", "sms", "email", "ai_assist",
                "calendar", "availability", "booking", "wholesale_real_estate"]
LINK_ENVS = ("PUBLIC_APP_URL", "FRONTEND_URL", "WHOLESALE_ESIGN_PUBLIC_URL", "PERMISSION_LINK_BASE_URL")


# ── fixtures ────────────────────────────────────────────────────────────────

def _platform(db, slug, name):
    p = db.query(Platform).filter(Platform.slug == slug).first()
    if p is None:
        p = Platform(id="plt-%s" % slug, name=name, slug=slug, is_active=True)
        db.add(p)
        db.commit()
    return p


def _org(db, name, slug, feats, platform):
    o = Organization(name=name, slug=slug, plan="standard", industry="generic", is_active=True,
                     platform_id=platform.id if platform else None, enabled_features=json.dumps(feats))
    db.add(o)
    db.commit()
    from app.services.tier_config_service import seed_default_tier_definitions
    seed_default_tier_definitions(db, o.id, industry="generic")
    return o


def _admin(db, org, email):
    u = User(organization_id=org.id, email=email, full_name="Admin %d" % next(_SEQ), role="org_admin",
             is_active=True, password_hash=hash_password("Pass12345!"), must_change_password=False)
    db.add(u)
    db.commit()
    db.add(Membership(user_id=u.id, scope_type="customer_org", scope_id=org.id, role="org_admin", is_active=True))
    db.commit()
    return u


def _h(db, u, org=None, **extra):
    h = {"Authorization": "Bearer %s" % create_access_token(u, db)}
    if org is not None:
        h["X-Workspace-Id"] = org.id
    h.update(extra)
    return h


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:400])
    return r.json()


@pytest.fixture()
def w(db_session, monkeypatch):
    for env in LINK_ENVS:
        monkeypatch.delenv(env, raising=False)
    db = db_session
    evp, bbp = _platform(db, "evosyspro", "EvoSys Pro"), _platform(db, "bookaboost", "BookaBoost")
    evo = _org(db, "EVO Integrated Solutions LLC", "evo-integrated-solutions-llc", EVO_FEATURES, evp)
    bab = _org(db, "BookaBoost Wholesale", "bookaboost-wholesale", BAB_FEATURES, bbp)
    bab.brand_name = "BookaBoost Wholesale"
    nobrand = _org(db, "No Brand Co (TEST)", "nobrand-test", EVO_FEATURES, None)
    db.commit()
    return SimpleNamespace(db=db, evp=evp, bbp=bbp, evo=evo, bab=bab, nobrand=nobrand,
                           evo_admin=_admin(db, evo, "admin.evo.%d@example.com" % next(_SEQ)),
                           bab_admin=_admin(db, bab, "admin.bab.%d@example.com" % next(_SEQ)))


def _deal(client, h, street="2620 Kirby St"):
    prop = ok(client.post("/wholesale/properties", headers=h, json={
        "street_address": street, "city": "Dallas", "state": "TX", "county": "Dallas",
        "zip_code": "75203", "owner_name": "Elizabeth Bryant"}))
    return prop["deal"]["id"]


# ── 1. investor invite links ────────────────────────────────────────────────

def _invite(client, db, admin, org):
    return ok(client.post("/wholesale/deal-room/invites", headers=_h(db, admin, org),
                          json={"name": "Practice Investor", "email": "investor.%d@example.com" % next(_SEQ),
                                "is_test": True}))["link"]


def test_investor_invite_link_uses_the_workspaces_own_brand_address(client, w, monkeypatch):
    link = _invite(client, w.db, w.bab_admin, w.bab)
    assert link.startswith(BAB_APP + "/investors/invite/"), link
    # The environment does not pull a BookaBoost link back onto the EvoSys domain.
    monkeypatch.setenv("PUBLIC_APP_URL", "https://app.evosyspro.live")
    assert _invite(client, w.db, w.bab_admin, w.bab).startswith(BAB_APP + "/investors/invite/")


def test_evosys_invite_link_is_unchanged(client, w, monkeypatch):
    assert _invite(client, w.db, w.evo_admin, w.evo).startswith("https://app.evosyspro.live/investors/invite/")
    monkeypatch.setenv("PUBLIC_APP_URL", "https://custom-evo.example.com/")
    assert _invite(client, w.db, w.evo_admin, w.evo).startswith("https://custom-evo.example.com/investors/invite/")
    from app.services import wholesale_deal_room as DR
    assert DR.public_base() == "https://custom-evo.example.com"                      # old call shape
    assert DR.public_base(w.db, w.nobrand.id) == "https://custom-evo.example.com"    # unbranded: unchanged


# ── 2. e-signature links ────────────────────────────────────────────────────

def _send_contract(client, db, admin, org, mail):
    h = _h(db, admin, org)
    deal = _deal(client, h)
    r = ok(client.post("/wholesale/deals/%s/contracts/purchase_agreement/send" % deal, headers=h,
                       json={"values": ES.FULL, "message": "Here is the contract."}))
    assert r["sent"] is True
    links = [re.search(r"href='([^']+/sign/[A-Za-z0-9_-]+)'", m["html"]) for m in mail if m["type"] == "esign_link"]
    return [m.group(1) for m in links if m]


def test_signing_link_opens_on_the_workspaces_brand(client, w, mail, monkeypatch):
    monkeypatch.setenv("WHOLESALE_ESIGN_PUBLIC_URL", "https://app.evosyspro.live")
    links = _send_contract(client, w.db, w.bab_admin, w.bab, mail)
    assert links and all(u.startswith(BAB_APP + "/sign/") for u in links), links
    # ...and the signing page that link opens names BookaBoost (brand travels with the envelope).
    tok = links[0].rsplit("/", 1)[1]
    assert ok(client.get("/esign/sign/%s" % tok))["brand"] == "BookaBoost"


def test_evosys_signing_link_is_unchanged(client, w, mail, monkeypatch):
    monkeypatch.setenv("WHOLESALE_ESIGN_PUBLIC_URL", "https://sign-evo.example.com")
    links = _send_contract(client, w.db, w.evo_admin, w.evo, mail)
    assert links and all(u.startswith("https://sign-evo.example.com/sign/") for u in links), links
    from app.services import evosys_esign
    monkeypatch.delenv("WHOLESALE_ESIGN_PUBLIC_URL")
    assert evosys_esign.sign_url("abc") == "https://app.evosyspro.live/sign/abc"


# ── 3. owner permission links ───────────────────────────────────────────────

def _permission_url(client, db, admin, org, **headers):
    from app.models.evosense_models import EvoSenseProperty
    p = EvoSenseProperty(organization_id=org.id, street_address="%d Kirby St" % next(_SEQ), city="Dallas",
                         state="TX", zip_code="75203", county="Dallas", parcel_apn="0000%04d" % next(_SEQ))
    db.add(p)
    db.commit()
    h = _h(db, admin, org, **headers)
    ok(client.post("/wholesale/evosense/properties/%s/owner" % p.id, headers=h, json={"name": "Maria Lopez"}))
    return ok(client.post("/wholesale/evosense/properties/%s/permission-link" % p.id, headers=h,
                          json={"phone": "214-555-0142", "owner_name": "Maria Lopez"}))["url"]


def test_permission_link_brand_first_env_only_as_fallback(client, w, monkeypatch):
    monkeypatch.setenv("PERMISSION_LINK_BASE_URL", "https://app.evosyspro.live")
    assert _permission_url(client, w.db, w.bab_admin, w.bab).startswith(BAB_APP + "/permission/")
    # EvoSys: exactly as before - the environment value wins, then the caller's origin.
    assert _permission_url(client, w.db, w.evo_admin, w.evo).startswith("https://app.evosyspro.live/permission/")
    monkeypatch.delenv("PERMISSION_LINK_BASE_URL")
    url = _permission_url(client, w.db, w.evo_admin, w.evo, origin="https://app.evosyspro.live")
    assert url.startswith("https://app.evosyspro.live/permission/")


# ── 4. public Wholesale website, per workspace ──────────────────────────────

EVO_HOST, BAB_HOST = "wholesale.evosyspro.live", "deals.bookaboost-test.example.com"


@pytest.fixture()
def sites(w, monkeypatch):
    w.db.add(WholesaleSettings(organization_id=w.evo.id, public_intake_key="evo-site-key-%022d" % 1))
    w.db.add(WholesaleSettings(organization_id=w.bab.id, public_intake_key="bab-site-key-%022d" % 2,
                               public_business_name="BookaBoost Wholesale",
                               public_contact_email="deals@bookaboost-test.example.com"))
    w.db.commit()
    monkeypatch.setenv("WHOLESALE_SITE_ORG_ID", w.evo.id)
    monkeypatch.setenv("WHOLESALE_SITE_HOSTS", "%s,%s=%s" % (EVO_HOST, BAB_HOST, w.bab.slug))
    from app.services import entitlements
    monkeypatch.setattr(entitlements, "org_has_feature", lambda org, feature: True)
    return w


def _buyer_form(**over):
    base = {"intent": "buyer", "full_name": "Site Test Investor", "email": "site.%d@example.com" % next(_SEQ),
            "phone": "(214) 555-0142", "markets": "Dallas", "strategy": "Buy & Hold",
            "budget": "$100,000–$250,000", "contact_permission": "yes",
            "submission_id": "WEB-%d" % next(_SEQ), "organization_id": "someone-else"}
    base.update(over)
    return base


def test_bookaboost_site_host_serves_its_own_neutral_page(client, sites):
    r = client.get("/", headers={"host": BAB_HOST})
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "BookaBoost Wholesale" in r.text and "deals@bookaboost-test.example.com" in r.text
    assert "EvoSys" not in r.text and "evosyspro" not in r.text.lower() and "{{" not in r.text
    for path in ("/assets/site.css", "/assets/tenant.js", "/assets/favicon.svg", "/robots.txt"):
        assert client.get(path, headers={"host": BAB_HOST}).status_code == 200, path
    # The EvoSys pages and scripts are not served on another workspace's host.
    for path in ("/platform.html", "/careers.html", "/assets/site.js", "/README.md"):
        nf = client.get(path, headers={"host": BAB_HOST})
        assert nf.status_code == 404 and "EvoSys" not in nf.text, path
    # The API is still not reachable through the site's host.
    assert client.get("/auth/me", headers={"host": BAB_HOST}).status_code == 404


def test_bookaboost_site_buyer_form_lands_in_that_workspace_only(client, sites):
    r = client.post("/intake.php", data=_buyer_form(), headers={"host": BAB_HOST, "origin": "https://" + BAB_HOST})
    assert r.status_code == 201, r.text
    rows = sites.db.query(WholesaleBuyer).all()
    assert [b.organization_id for b in rows] == [sites.bab.id]


def test_evosys_site_host_is_unchanged_next_to_a_tenant_host(client, sites):
    r = client.get("/", headers={"host": EVO_HOST})
    assert r.status_code == 200 and "EvoSys Pro" in r.text
    assert client.get("/platform.html", headers={"host": EVO_HOST}).status_code == 200
    nf = client.get("/nope.html", headers={"host": EVO_HOST})
    assert nf.status_code == 404 and "Page not found | EvoSys Pro Wholesale" in nf.text
    r = client.post("/intake.php", data=_buyer_form(), headers={"host": EVO_HOST})
    assert r.status_code == 201, r.text
    assert [b.organization_id for b in sites.db.query(WholesaleBuyer).all()] == [sites.evo.id]


def test_a_tenant_host_naming_no_workspace_refuses_and_creates_nothing(client, sites, monkeypatch):
    monkeypatch.setenv("WHOLESALE_SITE_HOSTS", "%s,%s=no-such-workspace" % (EVO_HOST, BAB_HOST))
    r = client.post("/intake.php", data=_buyer_form(), headers={"host": BAB_HOST})
    assert r.status_code == 503
    assert sites.db.query(WholesaleBuyer).count() == 0


def test_host_map_parsing():
    import os
    from app.routers import wholesale_public_site_router as W
    old = os.environ.get("WHOLESALE_SITE_HOSTS")
    try:
        os.environ.pop("WHOLESALE_SITE_HOSTS", None)
        assert W.host_map() == {"wholesale.evosyspro.live": None}           # the default, unchanged
        os.environ["WHOLESALE_SITE_HOSTS"] = " A.example.com , b.example.com = org-b ,"
        assert W.host_map() == {"a.example.com": None, "b.example.com": "org-b"}
        assert W.tenant_of("B.example.com:443") == "org-b" and W.tenant_of("a.example.com") is None
    finally:
        if old is None:
            os.environ.pop("WHOLESALE_SITE_HOSTS", None)
        else:
            os.environ["WHOLESALE_SITE_HOSTS"] = old


# ── 5. SMS opt-in / HELP wording ────────────────────────────────────────────

def test_sms_program_copy_names_the_workspaces_product(client, w):
    from app.services import wholesale_sms as WS
    assert WS.program_brand(w.db, w.bab.id)["brand"] == "BookaBoost Wholesale"
    assert WS.program_brand(w.db, w.evo.id)["brand"] == "EvoSys Wholesale"      # unchanged
    assert WS.program_brand(w.db, w.nobrand.id)["brand"] == "Wholesale"         # unchanged
    assert WS.OPT_IN_CONFIRMATION.format(brand="BookaBoost Wholesale").startswith("BookaBoost Wholesale: ")
    # The status screen still reports texting as not sending for a fresh workspace.
    st = ok(client.get("/wholesale/sms/status", headers=_h(w.db, w.bab_admin, w.bab)))
    assert st["enabled"] is False and st["can_send"] is False and st["messaging_service_configured"] is False


# ── 6. customer-facing text carries no EvoSys name for BookaBoost ───────────

def test_contract_notice_funding_disclaimer_and_repair_source_follow_the_brand(client, w):
    from app.services import wholesale_contract_docs as docs
    from app.services import wholesale_funding as FD
    from app.services import wholesale_photo_repairs as PR
    hb, he = _h(w.db, w.bab_admin, w.bab), _h(w.db, w.evo_admin, w.evo)
    kb, ke = ok(client.get("/wholesale/contract-kits", headers=hb)), ok(client.get("/wholesale/contract-kits", headers=he))
    assert "EvoSys" not in kb["notice"] and "written for BookaBoost," in kb["notice"]
    assert ke["notice"] == docs.STARTER_NOTICE                                         # byte for byte
    fb, fe = ok(client.get("/wholesale/funding/products", headers=hb)), ok(client.get("/wholesale/funding/products", headers=he))
    assert "EvoSys" not in fb["disclaimer"] and "BookaBoost does not lend" in fb["disclaimer"]
    assert fe["disclaimer"] == FD.DISCLAIMER
    assert ok(client.get("/wholesale/funding/partners", headers=hb))["disclaimer"] == fb["disclaimer"]
    db_deal, de_deal = _deal(client, hb), _deal(client, he, street="9 Elm St")
    pb = ok(client.get("/wholesale/deals/%s/repairs/photo-ai" % db_deal, headers=hb))
    pe = ok(client.get("/wholesale/deals/%s/repairs/photo-ai" % de_deal, headers=he))
    assert "EvoSys" not in pb["cost_source"] and pb["cost_source"].startswith("BookaBoost DFW")
    assert pe["cost_source"] == PR.COST_SOURCE
    assert PR.price_lines([{"item": "roof_repair", "quantity": 1}], None)[0]["cost_source"] == PR.COST_SOURCE


def test_deal_room_design_label_has_no_evosys_name_for_bookaboost(client, w):
    from app.services import wholesale_deal_room_setup as SET
    vb = ok(client.get("/wholesale/deal-room/settings", headers=_h(w.db, w.bab_admin, w.bab)))
    ve = ok(client.get("/wholesale/deal-room/settings", headers=_h(w.db, w.evo_admin, w.evo)))
    assert [d["label"] for d in vb["options"]["designs"]] == ["Investor Portal"]
    assert [d["key"] for d in vb["options"]["designs"]] == [d["key"] for d in SET.DESIGNS]   # same design
    assert ve["options"]["designs"] == SET.DESIGNS                                          # unchanged


def test_field_agreement_notice_uses_the_brand(w):
    from app.services import wholesale_contract_docs as docs
    assert docs.starter_notice_for(w.db, w.evo.id) == docs.STARTER_NOTICE
    assert docs.starter_notice_for(w.db, w.nobrand.id) == docs.STARTER_NOTICE
    assert "EvoSys" not in docs.starter_notice_for(w.db, w.bab.id)


# ── 7. the installable field app ────────────────────────────────────────────

def test_field_app_manifests():
    import pathlib
    pub = pathlib.Path(__file__).resolve().parents[1] / "frontend" / "public"
    evo = json.loads((pub / "manifest-field.webmanifest").read_text(encoding="utf-8"))
    neutral = json.loads((pub / "manifest-field-neutral.webmanifest").read_text(encoding="utf-8"))
    assert (evo["name"], evo["short_name"]) == ("EvoSys Wholesale", "EvoSys Field")      # unchanged
    assert "EvoSys" not in json.dumps(neutral) and neutral["short_name"] == "Field"
    assert neutral["start_url"] == evo["start_url"] and neutral["icons"] == evo["icons"]
    js = (pub.parent / "src" / "pages" / "wholesale" / "appts" / "fieldApp.js").read_text(encoding="utf-8")
    assert "manifest-field-neutral.webmanifest" in js and "engine() ? FIELD.manifest" in js


# ── 8. email / text to buyers: two switches, both off by default ────────────

def _channels(client, db, admin, org):
    return {c["channel"]: c for c in ok(client.get("/wholesale/disposition/channels",
                                                   headers=_h(db, admin, org)))["channels"]}


def test_buyer_send_switches_need_server_and_workspace(client, w, monkeypatch):
    for env in ("OUTBOUND_EMAIL_WHOLESALE_BUYER_DISPOSITION", "OUTBOUND_SMS_WHOLESALE_BUYER_DISPOSITION"):
        monkeypatch.delenv(env, raising=False)
    for admin, org in ((w.bab_admin, w.bab), (w.evo_admin, w.evo)):
        ch = _channels(client, w.db, admin, org)
        assert {k: (v["enabled"], v["off_at"]) for k, v in ch.items()} == {"email": (False, "server"),
                                                                           "sms": (False, "server")}
        assert "not switched on for this platform" in ch["email"]["detail"]
    monkeypatch.setenv("OUTBOUND_EMAIL_WHOLESALE_BUYER_DISPOSITION", "true")
    monkeypatch.setenv("OUTBOUND_SMS_WHOLESALE_BUYER_DISPOSITION", "true")
    ch = _channels(client, w.db, w.bab_admin, w.bab)
    assert {k: (v["enabled"], v["off_at"]) for k, v in ch.items()} == {"email": (False, "workspace"),
                                                                       "sms": (False, "workspace")}
    assert "turned off for this workspace" in ch["sms"]["detail"]
    # The workspace's own switches. Email: the audited per-customer list. Text: an admin setting.
    w.bab.outbound_email_sources = json.dumps(["wholesale_buyer_disposition"])
    w.db.commit()
    s = ok(client.patch("/wholesale/settings", headers=_h(w.db, w.bab_admin, w.bab), json={"buyer_sms_enabled": True}))
    assert s["buyer_sms_enabled"] is True
    ch = _channels(client, w.db, w.bab_admin, w.bab)
    assert ch["email"]["enabled"] is True and ch["sms"]["enabled"] is True
    # ...and EvoSys's switches did not move with BookaBoost's.
    ch = _channels(client, w.db, w.evo_admin, w.evo)
    assert ch["email"]["enabled"] is False and ch["sms"]["enabled"] is False


def test_a_buyer_send_with_the_workspace_switch_off_reaches_no_provider(client, w, monkeypatch):
    monkeypatch.setenv("OUTBOUND_EMAIL_WHOLESALE_BUYER_DISPOSITION", "true")
    from app.services import email_service
    calls = []
    monkeypatch.setattr(email_service, "send_email", lambda *a, **k: calls.append(k) or {"success": True})
    h = _h(w.db, w.bab_admin, w.bab)
    deal = _deal(client, h, street="5 Buyer Way")
    buyer = ok(client.post("/wholesale/buyers", headers=h, json={"company_name": "Live Buyer Capital",
                                                                  "email": "live.buyer@example.com"}))
    res = ok(client.post("/wholesale/deals/%s/disposition" % deal, headers=h, json={"buyer_ids": [buyer["id"]]}))
    assert res["sent"] == 0 and res["results"][0]["code"] == "not_enabled"
    assert "turned off for this workspace" in res["results"][0]["reason"]
    assert calls == []


# ── 9. disabled integrations are listed, never hidden ───────────────────────

def test_disabled_integrations_show_as_not_configured_and_never_vanish(client, w):
    hb, he = _h(w.db, w.bab_admin, w.bab), _h(w.db, w.evo_admin, w.evo)
    sb, se = ok(client.get("/wholesale/settings", headers=hb)), ok(client.get("/wholesale/settings", headers=he))
    for group in ("enrichment", "comps", "esign"):
        assert [p["key"] for p in sb["providers"][group]] == [p["key"] for p in se["providers"][group]], group
    unconfigured = [p for p in sb["providers"]["enrichment"] if not p.get("configured")]
    assert unconfigured and all(p.get("status") for p in unconfigured)
    pb, pe = ok(client.get("/wholesale/evosense/providers", headers=hb)), ok(client.get("/wholesale/evosense/providers", headers=he))
    assert sorted(pb.keys()) == sorted(pe.keys())
    sk_b = ok(client.get("/wholesale/skip-trace/providers", headers=hb))
    sk_e = ok(client.get("/wholesale/skip-trace/providers", headers=he))
    assert [a.get("key") for a in sk_b["adapters"]] == [a.get("key") for a in sk_e["adapters"]]
    assert ok(client.get("/wholesale/sms/status", headers=hb)).keys() == ok(client.get("/wholesale/sms/status", headers=he)).keys()
    assert set(_channels(client, w.db, w.bab_admin, w.bab)) == {"email", "sms"}


# ── 10/11. Back Office: one per brand, never another brand's records ────────

def _sales_user(db, email):
    u = User(organization_id=None, email=email, password_hash=hash_password("x"), full_name="Seller %d" % next(_SEQ),
             role="advisor", is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _seat_sales(db, u, team, role):
    db.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG, scope_id=team.id, role=role, is_active=True))
    db.commit()


@pytest.fixture()
def sales(w):
    db = w.db
    evo_team = BrandSalesOrg(platform_id=w.evp.id, name="EvoSys Pro Sales", slug="evosyspro-sales-%d" % next(_SEQ))
    bab_team = BrandSalesOrg(platform_id=w.bbp.id, name="BookaBoost Sales", slug="bookaboost-sales-%d" % next(_SEQ))
    db.add_all([evo_team, bab_team])
    db.commit()
    evo_mgr = _sales_user(db, "evo.mgr.%d@example.com" % next(_SEQ))
    bab_mgr = _sales_user(db, "bab.mgr.%d@example.com" % next(_SEQ))
    both = _sales_user(db, "both.%d@example.com" % next(_SEQ))
    _seat_sales(db, evo_mgr, evo_team, ROLE_SALES_MANAGER)
    _seat_sales(db, bab_mgr, bab_team, ROLE_SALES_MANAGER)
    _seat_sales(db, both, evo_team, ROLE_SALES_MANAGER)
    _seat_sales(db, both, bab_team, ROLE_SALES_REP)
    evo_opp = Opportunity(brand_sales_org_id=evo_team.id, company_name="Evo Prospect (TEST)", stage=STAGE_PROSPECT)
    db.add(evo_opp)
    db.commit()
    return SimpleNamespace(w=w, db=db, evo_team=evo_team, bab_team=bab_team, evo_mgr=evo_mgr,
                           bab_mgr=bab_mgr, both=both, evo_opp=evo_opp)


def _board(client, db, u, **headers):
    body = ok(client.get("/sales/opportunities", headers=_h(db, u, **headers)))
    return body, [c["company_name"] for st in body["stages"] for c in st.get("opportunities", [])]


def test_a_brands_sales_manager_never_sees_another_brands_records(client, sales):
    db = sales.db
    bab_opp = Opportunity(brand_sales_org_id=sales.bab_team.id, company_name="BB Prospect (TEST)", stage=STAGE_PROSPECT)
    db.add(bab_opp)
    db.commit()
    body, names = _board(client, db, sales.bab_mgr)
    assert body["brand_sales_org"]["id"] == sales.bab_team.id and names == ["BB Prospect (TEST)"]
    body, names = _board(client, db, sales.evo_mgr)
    assert body["brand_sales_org"]["id"] == sales.evo_team.id and names == ["Evo Prospect (TEST)"]
    # By id, across brands: not found (no existence probe).
    assert client.get("/sales/opportunities/%s" % sales.evo_opp.id, headers=_h(db, sales.bab_mgr)).status_code == 404
    assert client.get("/sales/opportunities/%s" % bab_opp.id, headers=_h(db, sales.evo_mgr)).status_code == 404
    # Naming the other brand's team or the other brand does not widen anything.
    assert client.get("/sales/opportunities?brand_sales_org_id=%s" % sales.evo_team.id,
                      headers=_h(db, sales.bab_mgr)).status_code == 404
    body, names = _board(client, db, sales.bab_mgr, **{"X-Brand-Override": sales.w.evp.id})
    assert body["brand_sales_org"]["id"] == sales.bab_team.id and names == ["BB Prospect (TEST)"]


def test_an_empty_back_office_is_an_empty_board_named_for_its_brand(client, sales):
    db = sales.db
    body, names = _board(client, db, sales.bab_mgr)
    assert names == [] and body["brand_sales_org"]["name"] == "BookaBoost Sales"
    assert all(st["count"] == 0 for st in body["stages"])
    me = ok(client.get("/sales/me", headers=_h(db, sales.bab_mgr)))
    assert (me["brand_display_name"], me["brand_short_name"]) == ("BookaBoost", "BookaBoost")
    assert me["platform"]["slug"] == "bookaboost"
    me = ok(client.get("/sales/me", headers=_h(db, sales.evo_mgr)))
    assert (me["brand_display_name"], me["brand_short_name"]) == ("EvoSys Pro", "EvoSys")    # unchanged words


def test_one_back_office_destination_per_brand_and_the_chosen_one_is_honoured(client, sales):
    db = sales.db
    ctx = ok(client.get("/auth/my-contexts", headers=_h(db, sales.both)))
    dests = ctx["back_office_destinations"]
    assert sorted(d["platform_slug"] for d in dests) == ["bookaboost", "evosyspro"]
    assert {d["brand_sales_org_id"] for d in dests} == {sales.evo_team.id, sales.bab_team.id}
    assert ctx["has_back_office"] is True and ctx["back_office_path"] == "/sales"           # unchanged keys
    single = ok(client.get("/auth/my-contexts", headers=_h(db, sales.evo_mgr)))["back_office_destinations"]
    assert [d["brand_sales_org_id"] for d in single] == [sales.evo_team.id]
    # The chosen brand travels with the request and the server narrows to it.
    body, names = _board(client, db, sales.both, **{"X-Brand-Override": sales.w.bbp.id})
    assert body["brand_sales_org"]["id"] == sales.bab_team.id and names == []
    body, names = _board(client, db, sales.both, **{"X-Brand-Override": sales.w.evp.id})
    assert body["brand_sales_org"]["id"] == sales.evo_team.id and names == ["Evo Prospect (TEST)"]
    me = ok(client.get("/sales/me", headers=_h(db, sales.both, **{"X-Brand-Override": sales.w.bbp.id})))
    assert me["brand_display_name"] == "BookaBoost" and me["role"] == ROLE_SALES_REP
    # In the BookaBoost Back Office, an EvoSys record is not found.
    assert client.get("/sales/opportunities/%s" % sales.evo_opp.id,
                      headers=_h(db, sales.both, **{"X-Brand-Override": sales.w.bbp.id})).status_code == 404
    # A brand they do not sell for is ignored, never granted; no header = as before.
    other = _platform(db, "harmonyhustle", "Harmony Hustle")
    body, _ = _board(client, db, sales.both, **{"X-Brand-Override": other.id})
    assert body["brand_sales_org"]["id"] == sorted([sales.evo_team.id, sales.bab_team.id])[0]
    body, _ = _board(client, db, sales.both)
    assert body["brand_sales_org"]["id"] == sorted([sales.evo_team.id, sales.bab_team.id])[0]


def test_back_office_scheduling_text_names_the_brand_being_sold(client, sales):
    from app.routers import sales_scheduling_router as SS
    vis = {"u1": {"external_checked": False, "state": "not_connected"}}
    assert "outside EvoSys Pro cannot" in SS._external_visibility_summary(vis)["note"]          # unchanged
    assert "outside BookaBoost cannot" in SS._external_visibility_summary(vis, "BookaBoost")["note"]
    assert SS._brand_label(sales.db, sales.bab_team) == "BookaBoost"
    assert SS._brand_label(sales.db, sales.evo_team) == "EvoSys Pro"
    r = client.get("/sales/calendar", headers=_h(sales.db, sales.bab_mgr))
    if r.status_code == 200:
        assert "EvoSys" not in json.dumps(r.json())


def test_demo_request_emails_name_the_brand_being_sold(w, monkeypatch):
    from app.services import public_demo_notifications as N
    sent = []
    monkeypatch.setattr(N, "_send", lambda to, subject, body, identity: sent.append((subject, body)) or {"success": True})
    monkeypatch.setattr(N, "_brand", lambda db, p: {"name": p.name, "support_email": "sales@example.com",
                                                    "website": None, "platform_id": p.id})
    lead = SimpleNamespace(id="lead-1", first_name="Pat", last_name="Prospect", email="pat@example.com", phone=None)
    payload = SimpleNamespace(model_extra={}, submitted_at=None, message="hi", goals=None, company="Acme",
                              email="pat@example.com", phone=None, industry=None, page_url=None,
                              source_url=None, referrer=None)
    N.notify_demo_request(w.db, platform=w.bbp, lead=lead, payload=payload)
    assert sent and all("EvoSys" not in s and "EvoSys" not in b for s, b in sent)
    assert sent[0][0].startswith("New BookaBoost Demo Request") and "BookaBoost — We Received" in sent[1][0]
    sent.clear()
    N.notify_demo_request(w.db, platform=w.evp, lead=lead, payload=payload)
    assert sent[0][0] == "New EvoSys Pro Demo Request — Pat Prospect"
    assert sent[1][0] == "EvoSys Pro — We Received Your Demo Request"


def test_bookaboost_back_office_setup_script_is_a_dry_run_by_default(w):
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "setup_bookaboost_sales_org.py"
    spec = importlib.util.spec_from_file_location("setup_bab_sales", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    db = w.db
    person = _sales_user(db, "future.bab.mgr.%d@example.com" % next(_SEQ))
    before = (db.query(BrandSalesOrg).count(), db.query(Membership).count())
    r = mod.plan_and_apply(db, managers=[person.email])
    assert r["ok"] and not r["applied"] and "DRY RUN - nothing changed." in r["steps"]
    assert (db.query(BrandSalesOrg).count(), db.query(Membership).count()) == before
    r = mod.plan_and_apply(db, managers=[person.email], apply=True, approved_by="", confirm="")
    assert r["stop"] and not r["applied"]
    assert (db.query(BrandSalesOrg).count(), db.query(Membership).count()) == before
    r = mod.plan_and_apply(db, managers=[person.email], apply=True, approved_by="Test Owner", confirm=mod.PHRASE)
    assert r["applied"]
    team = db.query(BrandSalesOrg).filter(BrandSalesOrg.platform_id == w.bbp.id).one()
    assert team.name == "BookaBoost Sales"
    m = db.query(Membership).filter(Membership.user_id == person.id, Membership.scope_id == team.id).one()
    assert m.role == ROLE_SALES_MANAGER and m.is_active
    again = mod.plan_and_apply(db, managers=[person.email], apply=True, approved_by="Test Owner", confirm=mod.PHRASE)
    assert again["applied"] and db.query(BrandSalesOrg).filter(BrandSalesOrg.platform_id == w.bbp.id).count() == 1
    god = User(organization_id=None, email="god.%d@example.com" % next(_SEQ), password_hash=hash_password("x"), full_name="Owner",
               role="god_admin", is_active=True, must_change_password=False)
    db.add(god)
    db.commit()
    assert "god_admin" in (mod.plan_and_apply(db, reps=[god.email])["stop"] or "")
