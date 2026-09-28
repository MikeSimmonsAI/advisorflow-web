"""PRODUCTS AND SERVICES ARE PER ORGANIZATION, NOT A HARD-CODED INSURANCE LIST.

The Client Record "Products" tab used to offer every organization the same
twenty-three insurance and funeral products. These tests lock in:

  * an unknown / generic business gets a neutral list — no insurance products
  * an insurance or funeral business keeps its exact legacy keys and labels
  * an organization's own list (PUT /settings/products) wins over its template
  * one organization's list never reaches another
  * /case-file/constants/all resolves per organization, same shape as before
  * every key the old list could have stored still has a label
"""

import itertools

import pytest

from app.models.models import Organization, User
from app.services import industry_templates as templates
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)

INSURANCE_KEYS = {
    "final_expense", "term_life_10yr", "term_life_20yr", "term_life_30yr",
    "whole_life", "universal_life_iul", "universal_life_vul",
    "universal_life_gul", "annuity_fixed", "annuity_fixed_indexed",
    "annuity_variable", "medicare_supplement", "medicare_advantage",
    "long_term_care", "disability_income", "dental_vision_hearing",
}
FUNERAL_KEYS = {"burial_preneed", "cemetery_property", "marker_monument",
                "memorial", "funeral_arrangement"}
ALL_LEGACY_KEYS = INSURANCE_KEYS | FUNERAL_KEYS | {"veterans_benefits", "other"}


def _org(db, industry=None, name="Org"):
    kwargs = {"industry": industry} if industry is not None else {}
    o = Organization(name=name, slug="pc-%d" % next(_SEQ), plan="standard",
                     is_active=True, **kwargs)
    db.add(o)
    db.commit()
    return o


def _user(db, org, role="org_admin"):
    u = User(organization_id=org.id, email="pc-%d@test.local" % next(_SEQ),
             password_hash=hash_password("TestPass123!"), full_name="User",
             role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def _keys(body):
    return [p["key"] for p in body["products"]]


# ── the registry ────────────────────────────────────────────────────────────

def test_every_template_has_a_renderable_product_list():
    for key, tpl in templates.TEMPLATES.items():
        assert tpl["products"], key
        for p in tpl["products"]:
            assert p["key"] and p["label"], key


def test_unknown_industry_gets_generic_not_insurance():
    for value in (None, "", "generic", "artisanal widgets"):
        keys = {p["key"] for p in templates.products(value)}
        assert keys == {p["key"] for p in templates.PRODUCT_CATALOGS["generic"]}
        assert not (keys & (INSURANCE_KEYS | FUNERAL_KEYS)), value


def test_legacy_keys_are_all_covered_by_insurance_and_funeral():
    ins = {p["key"] for p in templates.products("insurance")}
    fun = {p["key"] for p in templates.products("funeral")}
    assert INSURANCE_KEYS <= ins
    assert FUNERAL_KEYS <= fun
    assert ins | fun == ALL_LEGACY_KEYS
    assert set(templates.LEGACY_PRODUCT_LABELS) == ALL_LEGACY_KEYS


def test_legacy_labels_are_preserved_exactly():
    by_key = {p["key"]: p for p in templates.products("insurance")}
    assert by_key["universal_life_iul"]["label"] == "IUL (Indexed UL)"
    assert by_key["term_life_10yr"]["label"] == "Term Life — 10yr"
    assert templates.LEGACY_PRODUCT_LABELS["cemetery_property"] == "Cemetery Property"


def test_other_verticals_get_their_own_words():
    assert "roof_replacement" in {p["key"] for p in templates.products("roofing")}
    assert "sell_property" in {p["key"] for p in templates.products("real_estate")}
    assert "electricity_plan" in {p["key"] for p in templates.products("Energy & Procurement")}
    for industry in ("roofing", "real_estate", "energy", "cleaning", "home_services"):
        keys = {p["key"] for p in templates.products(industry)}
        assert not (keys & (INSURANCE_KEYS | FUNERAL_KEYS)), industry


def test_products_returns_copies():
    templates.products("insurance")[0]["label"] = "mutated"
    assert templates.products("insurance")[0]["label"] != "mutated"


# ── GET /settings/products ──────────────────────────────────────────────────

def test_generic_org_gets_no_insurance_products(client, db_session):
    org = _org(db_session, industry=None)
    r = client.get("/settings/products", headers=_h(db_session, _user(db_session, org, "advisor")))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_custom"] is False
    assert body["industry"] == "generic"
    assert not (set(_keys(body)) & (INSURANCE_KEYS | FUNERAL_KEYS))
    # Old stored keys can still be labelled on this org's case files.
    assert body["legacy_labels"]["whole_life"] == "Whole Life"


def test_insurance_org_gets_its_legacy_keys(client, db_session):
    org = _org(db_session, industry="insurance")
    body = client.get("/settings/products",
                      headers=_h(db_session, _user(db_session, org, "advisor"))).json()
    assert INSURANCE_KEYS <= set(_keys(body))
    assert body["industry"] == "insurance"


def test_funeral_org_gets_funeral_keys(client, db_session, sample_org, auth_headers):
    body = client.get("/settings/products", headers=auth_headers).json()
    assert FUNERAL_KEYS <= set(_keys(body))
    assert not (set(_keys(body)) & {"annuity_variable", "universal_life_vul"})


# ── PUT / DELETE ────────────────────────────────────────────────────────────

def test_override_via_put_then_get(client, db_session):
    org = _org(db_session, industry="roofing")
    h = _h(db_session, _user(db_session, org))
    r = client.put("/settings/products", headers=h, json={"products": [
        {"key": "gutter_guard", "label": "Gutter Guard", "icon": "G"},
        {"key": "skylight", "label": "Skylight"},
        {"key": "gutter_guard", "label": "Duplicate"},
    ]})
    assert r.status_code == 200, r.text
    assert r.json()["is_custom"] is True
    body = client.get("/settings/products", headers=h).json()
    assert body["is_custom"] is True
    assert _keys(body) == ["gutter_guard", "skylight"]          # deduped, ordered
    assert body["products"][0]["label"] == "Gutter Guard"

    reset = client.delete("/settings/products", headers=h).json()
    assert reset["is_custom"] is False
    assert "roof_replacement" in _keys(reset)


@pytest.mark.parametrize("payload", [
    {"products": []},
    {"products": [{"key": "Bad Key", "label": "x"}]},
    {"products": [{"key": "ok_key", "label": ""}]},
    {"products": [{"key": "ok_key", "label": "x" * 81}]},
    {"products": [{"key": "k%d" % i, "label": "L"} for i in range(101)]},
])
def test_validation_errors(client, db_session, payload):
    org = _org(db_session, industry="roofing")
    h = _h(db_session, _user(db_session, org))
    r = client.put("/settings/products", headers=h, json=payload)
    assert r.status_code == 400, r.text
    db_session.refresh(org)
    assert org.products is None


def test_malformed_payload_is_rejected(client, db_session):
    org = _org(db_session, industry="roofing")
    h = _h(db_session, _user(db_session, org))
    r = client.put("/settings/products", headers=h, json={"products": [{"label": "no key"}]})
    assert r.status_code == 422


def test_non_admin_cannot_put(client, db_session):
    org = _org(db_session, industry="roofing")
    h = _h(db_session, _user(db_session, org, "advisor"))
    r = client.put("/settings/products", headers=h,
                   json={"products": [{"key": "x", "label": "X"}]})
    assert r.status_code == 403


def test_tenant_isolation(client, db_session):
    a = _org(db_session, industry="insurance", name="A")
    b = _org(db_session, industry="insurance", name="B")
    ha = _h(db_session, _user(db_session, a))
    hb = _h(db_session, _user(db_session, b))
    r = client.put("/settings/products", headers=ha,
                   json={"products": [{"key": "a_only", "label": "A Only"}]})
    assert r.status_code == 200, r.text
    body_b = client.get("/settings/products", headers=hb).json()
    assert "a_only" not in _keys(body_b)
    assert body_b["is_custom"] is False
    db_session.refresh(b)
    assert b.products is None


def test_org_admin_cannot_target_another_org_via_query(client, db_session):
    a = _org(db_session, industry="insurance", name="A")
    b = _org(db_session, industry="roofing", name="B")
    ha = _h(db_session, _user(db_session, a))
    client.put("/settings/products?org_id=%s" % b.id, headers=ha,
               json={"products": [{"key": "sneaky", "label": "Sneaky"}]})
    db_session.refresh(b)
    assert b.products is None


# ── /case-file/constants/all ────────────────────────────────────────────────

def test_constants_all_resolves_per_org(client, db_session):
    gen = _org(db_session, industry=None, name="Gen")
    ins = _org(db_session, industry="insurance", name="Ins")
    g = client.get("/case-file/constants/all",
                   headers=_h(db_session, _user(db_session, gen, "advisor")))
    i = client.get("/case-file/constants/all",
                   headers=_h(db_session, _user(db_session, ins, "advisor")))
    assert g.status_code == 200, g.text
    gb, ib = g.json(), i.json()
    # Historic shape: products is a list of key strings.
    assert all(isinstance(k, str) for k in gb["products"])
    assert not (set(gb["products"]) & INSURANCE_KEYS)
    assert INSURANCE_KEYS <= set(ib["products"])
    assert "case_statuses" in gb and "outcome_types" in gb and "next_actions" in gb
    assert gb["product_catalog"][0]["label"]
    assert gb["legacy_product_labels"]["final_expense"] == "Final Expense"


def test_constants_all_uses_org_override(client, db_session):
    org = _org(db_session, industry="insurance")
    h = _h(db_session, _user(db_session, org))
    client.put("/settings/products", headers=h,
               json={"products": [{"key": "custom_one", "label": "Custom One"}]})
    body = client.get("/case-file/constants/all", headers=h).json()
    assert body["products"] == ["custom_one"]


def test_hand_written_override_of_bare_keys_still_renders(db_session):
    org = _org(db_session, industry=None)
    org.products = '["whole_life", "mystery"]'
    db_session.commit()
    resolved = templates.products_for_org(org)
    assert resolved["is_custom"] is True
    labels = {p["key"]: p["label"] for p in resolved["products"]}
    assert labels == {"whole_life": "Whole Life", "mystery": "mystery"}
