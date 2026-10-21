# -*- coding: utf-8 -*-
"""LANE-E-BOOKABOOST P4 (#272): BookaBoost's own identity inside the shared
Wholesale app, and EvoSys exactly as before.

White-label rule on file: a brand's users never see another brand's name. So in
BookaBoost Wholesale: the product is "BookaBoost Wholesale", the engine is not
called EvoSense, and e-signature emails / signing page / signed PDF / contract
notes / notices never say EvoSys. EvoSys organizations (and any org without a
brand) keep every string byte for byte. All records synthetic.
"""
import json
import zlib
import re

import pytest

from app.models.models import Organization, Platform, User
from app.models.sales_models import Membership
from app.services.auth_service import create_access_token, hash_password

import test_evosys_esign as ES
from test_evosys_esign import mail  # noqa: F401  (fixture)

BAB_FEATURES = ["leads", "users", "reports", "imports", "compliance", "sms", "email", "ai_assist",
                "calendar", "availability", "booking", "wholesale_real_estate", "wholesale_workspace"]


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
    u = User(organization_id=org.id, email=email, full_name="Admin", role="org_admin", is_active=True,
             password_hash=hash_password("Pass12345!"), must_change_password=False)
    db.add(u)
    db.commit()
    db.add(Membership(user_id=u.id, scope_type="customer_org", scope_id=org.id, role="org_admin", is_active=True))
    db.commit()
    return u


def _h(db, u, org):
    return {"Authorization": "Bearer %s" % create_access_token(u, db), "X-Workspace-Id": org.id}


@pytest.fixture()
def brands(db_session):
    db = db_session
    evp, bbp = _platform(db, "evosyspro", "EvoSys Pro"), _platform(db, "bookaboost", "BookaBoost")
    evo = _org(db, "EVO Integrated Solutions LLC", "evo-integrated-solutions-llc",
               ["leads", "users", "wholesale_real_estate"], evp)
    bab = _org(db, "BookaBoost Wholesale", "bookaboost-wholesale", BAB_FEATURES, bbp)
    bab.brand_name = "BookaBoost Wholesale"
    fiber = _org(db, "Fiber Customer (TEST)", "fiber-test", ["leads", "users", "wholesale_real_estate"], bbp)
    nobrand = _org(db, "No Brand Co (TEST)", "nobrand-test", ["leads", "users", "wholesale_real_estate"], None)
    db.commit()
    return dict(db=db, evo=evo, bab=bab, fiber=fiber, nobrand=nobrand,
                evo_admin=_admin(db, evo, "a@evo.test"), bab_admin=_admin(db, bab, "a@bab.test"),
                fiber_admin=_admin(db, fiber, "a@fiber.test"))


# ── names the app is told ────────────────────────────────────────────────────

def test_branding_payload_names_bookaboost_and_leaves_everyone_else_unchanged(client, brands):
    db = brands["db"]

    def products(who, org):
        r = client.get("/branding/org", headers=_h(db, brands[who], brands[org]))
        assert r.status_code == 200, r.text[:200]
        return r.json()["platform"]["products"]
    assert products("bab_admin", "bab") == {"wholesale": "BookaBoost Wholesale", "engine": None}
    assert products("evo_admin", "evo") == {"wholesale": "EvoSys Wholesale"}          # byte-identical
    assert products("fiber_admin", "fiber") == {"wholesale": None}                    # unchanged


def test_brand_word_and_product_helpers(brands):
    from app.services.brand_config import brand_word, wholesale_product
    db = brands["db"]
    assert brand_word(db, brands["evo"]) == "EvoSys"
    assert brand_word(db, brands["nobrand"]) == "EvoSys"          # unresolvable brand: unchanged
    assert brand_word(db, brands["bab"]) == "BookaBoost"
    assert brand_word(db, brands["bab"].id) == "BookaBoost"       # by id too
    assert wholesale_product(db, brands["evo"]) == "EvoSys Wholesale"
    assert wholesale_product(db, brands["nobrand"]) == "EvoSys Wholesale"
    assert wholesale_product(db, brands["bab"]) == "BookaBoost Wholesale"
    assert wholesale_product(db, brands["fiber"]) == "Wholesale"


def test_settings_signer_label_is_the_orgs_brand(client, brands):
    db = brands["db"]

    def label(who, org):
        r = client.get("/wholesale/settings", headers=_h(db, brands[who], brands[org]))
        assert r.status_code == 200, r.text[:200]
        return {p["key"]: p["label"] for p in r.json()["providers"]["esign"]}["evosys"]
    assert label("bab_admin", "bab") == "BookaBoost e-signature"
    assert label("evo_admin", "evo") == "EvoSys e-signature"


# ── e-signature end to end: a BookaBoost seller never reads "EvoSys" ─────────

def _pdf_text(pdf: bytes) -> str:
    """Decompress every stream of a reportlab PDF and return the raw text ops."""
    import base64
    out = [pdf.decode("latin-1", "ignore")]                 # plain objects, e.g. /Author
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        chunk = m.group(1).strip()
        for attempt in ("flate", "a85+flate"):
            try:
                data = chunk
                if attempt == "a85+flate":                    # reportlab: ASCII85 then Flate
                    data = base64.a85decode(data[:-2] if data.endswith(b"~>") else data)
                out.append(zlib.decompress(data).decode("latin-1"))
                break
            except Exception:  # noqa: BLE001
                continue
    return "\n".join(out)


def _journey(client, db, org, admin, mail):
    h = _h(db, admin, org)
    prop = ES.ok(client.post("/wholesale/properties", headers=h, json={
        "street_address": "2620 Kirby St", "city": "Dallas", "state": "TX", "county": "Dallas",
        "zip_code": "75203", "owner_name": "Elizabeth Bryant"}))
    deal = prop["deal"]["id"]
    r = ES.ok(client.post("/wholesale/deals/%s/contracts/purchase_agreement/send" % deal, headers=h,
                          json={"values": ES.FULL, "message": "Here is the contract."}))
    assert r["sent"] is True and r["provider"] == "evosys"
    tok = ES.link_token(mail[0])
    view = ES.ok(client.get("/esign/sign/%s" % tok))
    ES.sign_as(client, mail, tok)
    btok = ES.link_token([m for m in mail if m["type"] == "esign_link"][-1])
    ES.sign_as(client, mail, btok, name="Test Buyer")
    from app.models.esign_models import EsignEnvelope
    env = db.query(EsignEnvelope).filter(EsignEnvelope.organization_id == org.id).one()
    from app.models.wholesale_models import WholesaleDocument, WholesaleEvent
    docs = db.query(WholesaleDocument).filter(WholesaleDocument.organization_id == org.id).all()
    events = db.query(WholesaleEvent).filter(WholesaleEvent.organization_id == org.id).all()
    return view, env, docs, events


def test_bookaboost_esign_journey_never_says_evosys(client, brands, mail):
    db = brands["db"]
    view, env, docs, events = _journey(client, db, brands["bab"], brands["bab_admin"], mail)
    assert view["brand"] == "BookaBoost"
    assert mail and all("EvoSys" not in m["html"] and "EvoSys" not in m["subject"] for m in mail), \
        [m["subject"] for m in mail if "EvoSys" in m["html"] or "EvoSys" in m["subject"]]
    assert any("Sent with BookaBoost e-signature" in m["html"] for m in mail)
    assert any("Your BookaBoost signing code" in m["html"] for m in mail)
    text = _pdf_text(env.final_pdf)
    assert "EvoSys" not in text and "BookaBoost" in text
    assert all("EvoSys" not in (d.notes or "") for d in docs)
    assert all("EvoSys" not in (e.summary or "") for e in events)


def test_evosys_esign_journey_reads_exactly_as_before(client, brands, mail):
    db = brands["db"]
    view, env, docs, events = _journey(client, db, brands["evo"], brands["evo_admin"], mail)
    assert view["brand"] == "EvoSys"
    assert any("Sent with EvoSys e-signature on behalf of" in m["html"] for m in mail)
    assert any("Your EvoSys signing code" in m["html"] for m in mail)
    assert "EvoSys electronic signature record" in _pdf_text(env.final_pdf)
    assert any("Generated from the EvoSys starter template" in (d.notes or "") for d in docs)
    assert any("(EvoSys e-signature)" in (e.summary or "") for e in events)


def test_wholesale_inquiry_notice_names_the_workspaces_own_product(brands):
    from app.services.wholesale_notify import _email_html
    job = {"kind": "new", "details": {"name": "Test Seller", "address": "1 Test St"}}
    _, bab_html = _email_html("BookaBoost Wholesale", job, None, "BookaBoost Wholesale")
    assert "turned on in BookaBoost Wholesale settings" in bab_html and "EvoSys" not in bab_html
    _, evo_html = _email_html("EVO Integrated Solutions LLC", job, None)          # old call shape
    assert "turned on in EvoSys Wholesale settings" in evo_html
