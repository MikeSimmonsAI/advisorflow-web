"""The funding deal packet uses only real system data, says NOT ON FILE for the
rest, carries no seller personal data or disposition economics, and is never sent."""
import pytest

from app.models.models import Organization, User
from app.models.wholesale_models import WholesaleEvent
from app.services.auth_service import create_access_token, hash_password


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:400])
    return r.json()


@pytest.fixture(autouse=True)
def _inline(monkeypatch):
    monkeypatch.setenv("INTAKE_INLINE_JOBS", "1")


@pytest.fixture
def bare_deal(client, auth_headers):
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "44 Packet Pl", "city": "Dallas", "state": "TX",
                                "zip_code": "75215", "bedrooms": 3}))
    ok(client.post("/wholesale/properties/%s/seller" % prop["id"], headers=auth_headers,
                   json={"first_name": "Priya", "last_name": "Private", "phone": "2145550188",
                         "email": "priya@private.test"}))
    return prop["deal"]["id"]


def test_a_bare_deal_invents_nothing(client, auth_headers, bare_deal):
    pk = ok(client.get("/wholesale/funding/deals/%s/packet" % bare_deal, headers=auth_headers))
    assert pk["valuation"]["arv"]["value"] is None and pk["valuation"]["arv"]["display"] == "Not established"
    assert pk["valuation"]["mao"]["value"] is None and pk["valuation"]["mao"]["display"] == "NOT CALCULATED"
    assert pk["repairs"]["amount"]["value"] is None
    assert pk["comps"] == []
    facts = dict((k, v) for k, v in pk["property"]["facts"])
    assert facts["Bedrooms"]["value"] == 3.0
    assert facts["Year built"]["display"] == "Not on file"
    assert dict(pk["title"])["Title status"]["display"] == "Not on file"
    assert "No photos or documents" in pk["attachments"]["note"]
    assert "does not lend" in pk["disclaimer"]


def test_real_figures_appear_with_their_basis(client, auth_headers, bare_deal):
    ok(client.patch("/wholesale/deals/%s/analysis" % bare_deal, headers=auth_headers,
                    json={"arv": 300000, "repair_estimate": 40000}))
    pk = ok(client.get("/wholesale/funding/deals/%s/packet" % bare_deal, headers=auth_headers))
    arv = pk["valuation"]["arv"]
    assert arv["value"] == 300000.0 and arv["basis"]
    assert pk["valuation"]["mao"]["display"] in ("NOT CALCULATED",) or pk["valuation"]["mao"]["value"]


def test_no_seller_personal_data_or_margin_in_the_packet(client, auth_headers, bare_deal):
    r = client.get("/wholesale/funding/deals/%s/packet?format=html" % bare_deal, headers=auth_headers)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "attachment" in r.headers["content-disposition"]
    body = r.text
    for secret in ("Priya", "Private", "2145550188", "priya@private.test",
                   "assignment fee", "Assignment fee", "wholesale fee"):
        assert secret not in body, secret
    assert "Not on file" in body and "NOT CALCULATED" in body and "does not lend" in body


def test_generating_is_logged_and_sends_nothing(client, db_session, auth_headers, bare_deal):
    from app.models.models import Message
    before = db_session.query(Message).count()
    p = ok(client.post("/wholesale/funding/partners", headers=auth_headers,
                       json={"name": "Packet Lender", "email": "pl@lender.test"}))
    s = ok(client.post("/wholesale/funding/deals/%s/submissions" % bare_deal, headers=auth_headers,
                       json={"partner_id": p["id"], "product": "fix_flip", "amount_requested": 150000}))
    pk = ok(client.get("/wholesale/funding/deals/%s/packet?submission_id=%s" % (bare_deal, s["id"]),
                       headers=auth_headers))
    assert pk["prepared_for"] == "Packet Lender"
    assert pk["request"]["product"] == "Fix & flip" and pk["request"]["amount_requested"] == "$150,000"
    ev = db_session.query(WholesaleEvent).filter(WholesaleEvent.action == "funding.packet_generated").all()
    assert len(ev) == 1 and "not sent" in ev[0].summary
    assert db_session.query(Message).count() == before


def test_another_tenant_gets_nothing(client, db_session, bare_deal):
    other = Organization(name="Packet Thief Org", slug="packet-thief", plan="enterprise")
    db_session.add(other)
    db_session.commit()
    u = User(organization_id=other.id, email="t@thief.test", password_hash=hash_password("Pass12345!"),
             full_name="T", role="org_admin", must_change_password=False)
    db_session.add(u)
    db_session.commit()
    h = {"Authorization": "Bearer %s" % create_access_token(u, db_session)}
    for fmt in ("json", "html"):
        assert client.get("/wholesale/funding/deals/%s/packet?format=%s" % (bare_deal, fmt),
                          headers=h).status_code in (403, 404)
