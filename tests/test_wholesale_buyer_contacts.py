"""P3: cash buyers share the tenant-scoped contact database without becoming Leads,
and without losing anything about the buyer role."""
from datetime import datetime

import pytest

from app.models.intake_models import OrgContact, RecordClass
from app.models.models import Lead, Organization, User
from app.models.wholesale_models import (WholesaleBuyBox, WholesaleBuyer, WholesaleBuyerOutreach,
                                         WholesaleEvent)
from app.services import wholesale_buyer_contacts as BC
from app.services.auth_service import create_access_token, hash_password


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:400])
    return r.json()


@pytest.fixture(autouse=True)
def _inline(monkeypatch):
    monkeypatch.setenv("INTAKE_INLINE_JOBS", "1")


def _admin(db, org, email="boss@buyers.test"):
    u = User(organization_id=org.id, email=email, password_hash=hash_password("Pass12345!"),
             full_name="Boss", role="org_admin", must_change_password=False)
    db.add(u)
    db.commit()
    return u, {"Authorization": "Bearer %s" % create_access_token(u, db)}


def test_a_new_buyer_is_a_partner_contact_and_never_a_lead(client, db_session, auth_headers, sample_org):
    b = ok(client.post("/wholesale/buyers", headers=auth_headers,
                       json={"company_name": "Trinity Cash Homes", "contact_name": "Ana Ruiz",
                             "email": "ana@trinitycash.test", "phone": "2145550131"}))
    assert b["org_contact_id"]
    c = db_session.query(OrgContact).filter(OrgContact.id == b["org_contact_id"]).one()
    assert c.organization_id == sample_org.id and c.record_class == RecordClass.PARTNER
    assert c.company == "Trinity Cash Homes" and c.first_name == "Ana" and c.last_name == "Ruiz"
    assert c.lead_id is None
    assert db_session.query(Lead).filter(Lead.organization_id == sample_org.id,
                                         Lead.email == "ana@trinitycash.test").count() == 0


def test_test_buyers_stay_out_of_the_contact_database(client, db_session, auth_headers):
    b = ok(client.post("/wholesale/buyers", headers=auth_headers,
                       json={"company_name": "Sandbox Buyer", "email": "sb@x.test", "is_test": True}))
    assert b["org_contact_id"] is None
    assert db_session.query(OrgContact).filter(OrgContact.email == "sb@x.test").count() == 0


def test_existing_buyers_migrate_without_losing_the_buyer_role(client, db_session, sample_org):
    admin, h = _admin(db_session, sample_org)
    old = WholesaleBuyer(organization_id=sample_org.id, company_name="Legacy Capital",
                         contact_name="Lee Gacy", email="lee@legacy.test", past_deals_count=12,
                         proof_of_funds_on_file=True, notes="claimed 12 deals")
    nochan = WholesaleBuyer(organization_id=sample_org.id, company_name="No Channel LLC")
    test = WholesaleBuyer(organization_id=sample_org.id, company_name="Test LLC",
                          email="t@t.test", is_test=True)
    db_session.add_all([old, nochan, test])
    db_session.flush()
    db_session.add(WholesaleBuyBox(organization_id=sample_org.id, buyer_id=old.id, label="DFW",
                                   cities='["dallas"]'))
    db_session.commit()
    dry = ok(client.post("/wholesale/buyers/link-contacts?dry_run=true", headers=h))
    assert dry["counts"] == {"would_link": 1, "skipped_no_channel": 1, "skipped_test": 1}
    assert db_session.query(OrgContact).count() == 0                     # dry run wrote nothing
    real = ok(client.post("/wholesale/buyers/link-contacts?dry_run=false", headers=h))
    assert real["counts"]["linked_new"] == 1
    again = ok(client.post("/wholesale/buyers/link-contacts?dry_run=false", headers=h))
    assert again["counts"].get("already_linked") == 1 and "linked_new" not in again["counts"]
    db_session.expire_all()
    b = db_session.query(WholesaleBuyer).filter(WholesaleBuyer.id == old.id).one()
    assert b.org_contact_id and b.past_deals_count == 12 and b.proof_of_funds_on_file
    assert b.notes == "claimed 12 deals"
    assert db_session.query(WholesaleBuyBox).filter(WholesaleBuyBox.buyer_id == b.id).count() == 1
    assert db_session.query(OrgContact).count() == 1
    assert {e.action for e in db_session.query(WholesaleEvent).all()} >= {"buyer.contacts_linked"}


def test_a_buyer_who_is_already_a_contact_is_reused_not_duplicated(client, db_session, sample_org, auth_headers):
    from app.services.intake import capture as CAP
    CAP.capture_one(db_session, sample_org, {"first_name": "Sam", "last_name": "Seller",
                                             "email": "sam@both.test"},
                    source="manual", classification="contact", external=False, explicit=True)
    before = db_session.query(OrgContact).count()
    b = ok(client.post("/wholesale/buyers", headers=auth_headers,
                       json={"contact_name": "Sam Seller", "email": "sam@both.test"}))
    assert db_session.query(OrgContact).count() == before
    assert b["org_contact_id"] == db_session.query(OrgContact).filter(
        OrgContact.email == "sam@both.test").one().id


def test_buyer_edits_write_through_and_are_protected(client, db_session, auth_headers):
    b = ok(client.post("/wholesale/buyers", headers=auth_headers,
                       json={"company_name": "Old Name LLC", "email": "old@edit.test"}))
    ok(client.patch("/wholesale/buyers/%s" % b["id"], headers=auth_headers,
                    json={"company_name": "New Name LLC", "email": "New@Edit.test",
                          "reliability_rating": 4}))
    c = db_session.query(OrgContact).filter(OrgContact.id == b["org_contact_id"]).one()
    db_session.refresh(c)
    assert c.company == "New Name LLC" and c.email == "new@edit.test"
    assert "company" in c.manually_edited_fields and "email" in c.manually_edited_fields


def test_standing_matching_and_activity_survive_linking(client, db_session, auth_headers, sample_org):
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "3 Link Rd", "city": "Dallas", "state": "TX",
                                "zip_code": "75201", "is_test": True}))
    b = WholesaleBuyer(organization_id=sample_org.id, company_name="Kept Activity", email="k@a.test",
                       is_test=True)
    db_session.add(b)
    db_session.flush()
    db_session.add(WholesaleBuyerOutreach(organization_id=sample_org.id, deal_id=prop["deal"]["id"],
                                          buyer_id=b.id, status="replied", sent_at=datetime.utcnow(),
                                          replied_at=datetime.utcnow()))
    db_session.commit()
    rows = ok(client.get("/wholesale/buyers?with_activity=true&include_test=true", headers=auth_headers))
    mine = next(x for x in rows["buyers"] if x["id"] == b.id)
    assert mine["standing"]["standing"] == "responsive" and mine["activity"]["sheets_sent"] == 1


def test_import_links_the_whole_list_in_one_batch(client, db_session, auth_headers):
    csv = ("company,contact,email,phone\n"
           "Alpha Buys,Al Pha,al@alpha.test,2145550151\n"
           "Beta Buys,Bea Ta,bea@beta.test,\n")
    r = ok(client.post("/wholesale/buyers/import", headers=auth_headers,
                       files={"file": ("buyers.csv", csv.encode(), "text/csv")}))
    assert r["created"] == 2 and r["contacts"].get("linked_new") == 2
    assert db_session.query(WholesaleBuyer).filter(WholesaleBuyer.org_contact_id.isnot(None)).count() == 2


def test_linking_is_tenant_scoped(client, db_session, sample_org):
    other = Organization(name="Other Buyer Org", slug="other-buyer-org", plan="enterprise")
    db_session.add(other)
    db_session.commit()
    stranger, h = _admin(db_session, other, "boss@other-buyer.test")
    mine = WholesaleBuyer(organization_id=sample_org.id, company_name="Mine", email="m@m.test")
    db_session.add(mine)
    db_session.commit()
    r = client.post("/wholesale/buyers/link-contacts?dry_run=false", headers=h)
    if r.status_code == 200:
        assert r.json()["buyers"] == 0
    db_session.refresh(mine)
    assert mine.org_contact_id is None
    # a link pointing at another org's contact is not honoured
    foreign = OrgContact(organization_id=other.id, first_name="F", email="f@f.test")
    db_session.add(foreign)
    db_session.commit()
    mine.org_contact_id = foreign.id
    db_session.commit()
    assert BC.linked_contact(db_session, mine) is None


def test_a_non_admin_cannot_run_the_migration(client, auth_headers):
    assert client.post("/wholesale/buyers/link-contacts?dry_run=false", headers=auth_headers).status_code == 403


def test_an_intake_failure_never_loses_the_buyer(client, db_session, auth_headers, monkeypatch):
    from app.services.intake import capture as CAP

    def boom(*a, **k):
        raise RuntimeError("intake down")
    monkeypatch.setattr(CAP, "capture_one", boom)
    b = ok(client.post("/wholesale/buyers", headers=auth_headers,
                       json={"company_name": "Survivor LLC", "email": "s@s.test"}))
    assert b["org_contact_id"] is None and b["company_name"] == "Survivor LLC"
    assert db_session.query(WholesaleEvent).filter(
        WholesaleEvent.action == "buyer.contact_link_error").count() == 1
