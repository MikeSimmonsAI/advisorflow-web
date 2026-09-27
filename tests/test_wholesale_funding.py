"""Funding / capital partners: contacts with lending attributes, not a lending platform.

A partner is captured into the contact database through Universal Intake as a
PARTNER (never a Lead); their criteria are what they STATED; what they did with
submitted deals is measured. EvoSys never presents itself as the lender.
"""
import pytest

from app.models.intake_models import OrgContact, RecordClass
from app.models.models import Lead, Organization, User
from app.models.wholesale_models import WholesaleFundingPartner, WholesaleFundingSubmission
from app.services.auth_service import create_access_token, hash_password
from app.services import wholesale_funding as FD


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:400])
    return r.json()


@pytest.fixture(autouse=True)
def _inline(monkeypatch):
    monkeypatch.setenv("INTAKE_INLINE_JOBS", "1")


@pytest.fixture
def deal(client, auth_headers):
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "1 Funding Ave", "city": "Dallas", "state": "TX",
                                "county": "Dallas", "zip_code": "75201",
                                "property_type": "single_family"}))
    return prop["deal"]["id"]


LENDER = {"name": "Lone Star Bridge Capital", "contact_person": "Pat Lender",
          "email": "pat@lonestarbridge.test", "phone": "2145550177",
          "products": ["fix_flip", "bridge"], "states": ["tx", "OK"],
          "property_types": ["single_family"], "min_loan": 75000, "max_loan": 1500000,
          "max_ltv_pct": 75, "typical_close_days": 10}


def test_a_partner_is_a_contact_classified_partner_never_a_lead(client, db_session, auth_headers, sample_org):
    p = ok(client.post("/wholesale/funding/partners", headers=auth_headers, json=LENDER))
    assert p["states"] == ["TX", "OK"] and p["products"] == ["fix_flip", "bridge"]
    assert p["verified"] is False and p["criteria_basis"] == "stated by the partner, not verified"
    contact = db_session.query(OrgContact).filter(OrgContact.id == p["org_contact_id"]).one()
    assert contact.organization_id == sample_org.id
    assert contact.record_class == RecordClass.PARTNER and contact.lead_id is None
    assert contact.company == "Lone Star Bridge Capital"
    assert db_session.query(Lead).filter(Lead.organization_id == sample_org.id,
                                         Lead.email == "pat@lonestarbridge.test").count() == 0


def test_bad_input_is_refused(client, auth_headers):
    r = client.post("/wholesale/funding/partners", headers=auth_headers,
                    json=dict(LENDER, products=["payday"]))
    assert r.status_code == 422
    r = client.post("/wholesale/funding/partners", headers=auth_headers,
                    json=dict(LENDER, min_loan=900000, max_loan=100000))
    assert r.status_code == 422
    r = client.post("/wholesale/funding/partners", headers=auth_headers,
                    json={"name": "No Contact Info"})
    assert r.status_code == 422


def test_options_show_every_reason_and_exclude_stated_mismatches(client, auth_headers, deal):
    ok(client.post("/wholesale/funding/partners", headers=auth_headers, json=LENDER))
    ok(client.post("/wholesale/funding/partners", headers=auth_headers,
                   json={"name": "Florida Only DSCR", "email": "fl@dscr.test", "products": ["dscr"],
                         "states": ["FL"]}))
    ok(client.post("/wholesale/funding/partners", headers=auth_headers,
                   json={"name": "Quiet Private Money", "email": "pm@private.test"}))
    out = ok(client.get("/wholesale/funding/deals/%s/options?product=fix_flip&amount=200000" % deal,
                        headers=auth_headers))
    assert "does not lend" in out["disclaimer"]
    by = {o["partner"]["name"]: o for o in out["options"]}
    assert by["Lone Star Bridge Capital"]["eligible"] is True
    assert by["Florida Only DSCR"]["eligible"] is False
    assert set(by["Florida Only DSCR"]["excluded_because"]) == {"product", "state"}
    quiet = by["Quiet Private Money"]
    assert quiet["eligible"] is True and all(r["fit"] is None for r in quiet["reasons"])
    assert out["options"][0]["partner"]["name"] == "Lone Star Bridge Capital"
    big = ok(client.get("/wholesale/funding/deals/%s/options?product=fix_flip&amount=5000000" % deal,
                        headers=auth_headers))
    lone = next(o for o in big["options"] if o["partner"]["name"] == "Lone Star Bridge Capital")
    assert lone["eligible"] is False and lone["excluded_because"] == ["loan amount"]


def test_submission_track_record_says_what_they_actually_did(client, auth_headers, deal):
    p = ok(client.post("/wholesale/funding/partners", headers=auth_headers, json=LENDER))
    s = ok(client.post("/wholesale/funding/deals/%s/submissions" % deal, headers=auth_headers,
                       json={"partner_id": p["id"], "product": "fix_flip", "amount_requested": 180000}))
    assert s["status"] == "submitted"
    r = client.post("/wholesale/funding/submissions/%s/response" % s["id"], headers=auth_headers,
                    json={"status": "funded"})
    assert r.status_code == 409                        # funded only after an approval
    ok(client.post("/wholesale/funding/submissions/%s/response" % s["id"], headers=auth_headers,
                   json={"status": "approved", "approved_amount": 170000}))
    done = ok(client.post("/wholesale/funding/submissions/%s/response" % s["id"], headers=auth_headers,
                          json={"status": "funded"}))
    assert done["funded_at"] and done["approved_amount"] == 170000
    partners = ok(client.get("/wholesale/funding/partners", headers=auth_headers))["partners"]
    tr = next(x for x in partners if x["id"] == p["id"])["track_record"]
    assert tr["deals_submitted"] == 1 and tr["approvals"] == 1 and tr["funded"] == 1
    assert tr["declines"] == 0 and tr["avg_response_hours"] is not None
    subs = ok(client.get("/wholesale/funding/deals/%s/submissions" % deal, headers=auth_headers))
    assert subs["submissions"][0]["partner_name"] == "Lone Star Bridge Capital"


def test_verification_is_a_persons_act_with_a_name_and_time(client, db_session, auth_headers, sample_advisor):
    p = ok(client.post("/wholesale/funding/partners", headers=auth_headers, json=LENDER))
    v = ok(client.patch("/wholesale/funding/partners/%s" % p["id"], headers=auth_headers,
                        json={"verified": True}))
    assert v["verified"] is True and v["verified_at"]
    row = db_session.query(WholesaleFundingPartner).filter(WholesaleFundingPartner.id == p["id"]).one()
    assert row.verified_by_id == sample_advisor.id


def test_another_tenant_sees_and_touches_nothing(client, db_session, auth_headers, deal):
    p = ok(client.post("/wholesale/funding/partners", headers=auth_headers, json=LENDER))
    s = ok(client.post("/wholesale/funding/deals/%s/submissions" % deal, headers=auth_headers,
                       json={"partner_id": p["id"]}))
    other = Organization(name="Other Funding Org", slug="other-funding", plan="enterprise")
    db_session.add(other)
    db_session.commit()
    u = User(organization_id=other.id, email="x@other-funding.test", password_hash=hash_password("Pass12345!"),
             full_name="X", role="org_admin", must_change_password=False)
    db_session.add(u)
    db_session.commit()
    h = {"Authorization": "Bearer %s" % create_access_token(u, db_session)}
    listing = client.get("/wholesale/funding/partners", headers=h)
    if listing.status_code == 200:
        assert listing.json()["partners"] == []
    else:
        assert listing.status_code in (403, 404)       # no wholesale entitlement on that org
    for method, url, body in (
            ("patch", "/wholesale/funding/partners/%s" % p["id"], {"verified": True}),
            ("get", "/wholesale/funding/deals/%s/options" % deal, None),
            ("get", "/wholesale/funding/deals/%s/submissions" % deal, None),
            ("post", "/wholesale/funding/deals/%s/submissions" % deal, {"partner_id": p["id"]}),
            ("post", "/wholesale/funding/submissions/%s/response" % s["id"], {"status": "declined"})):
        r = getattr(client, method)(url, headers=h, **({"json": body} if body is not None else {}))
        assert r.status_code in (403, 404), (url, r.status_code)
    assert db_session.query(WholesaleFundingSubmission).filter(
        WholesaleFundingSubmission.id == s["id"]).one().status == "submitted"


def test_match_is_pure_and_treats_unstated_as_neither_fit_nor_miss():
    class P:  # noqa: D401 - a plain stand-in
        def __init__(self, **kw):
            self.__dict__.update(dict(is_active=True, verified=False, products=None, states=None,
                                      property_types=None, min_loan=None, max_loan=None), **kw)
    out = FD.match_partners([P(products='["dscr"]'), P()], product="fix_flip", state="TX", amount=1)
    assert [o["eligible"] for o in out] == [True, False]
