# -*- coding: utf-8 -*-
"""A website request from a reserved test address (RFC 2606/6761) is filed
normally but flagged Lead.is_test, so release checks and QA runs never count
as real customers or receive outreach. Real people are never flagged."""
import uuid

import pytest

from app.models.models import Lead, Notification, Organization, User
from app.services import customer_sites as cs
from app.services import test_records
from app.services.auth_service import hash_password

PAGE = "<!DOCTYPE html><html><head><title>A</title></head><body>x</body></html>"


@pytest.mark.parametrize("email,expected", [
    ("release-check@example.com", True), ("a@EXAMPLE.ORG", True), ("a@mail.example.net", True),
    ("a@atl.example.test", True), ("a@foo.invalid", True), ("a@x.example", True),
    ("a@gmail.com", False), ("a@example.co", False), ("a@notexample.com", False),
    ("a@atlantis-enterprises.com", False), ("", False), (None, False), ("no-at-sign", False),
])
def test_reserved_domains(email, expected):
    assert cs.is_reserved_test_email(email) is expected


@pytest.fixture()
def site(db_session):
    db = db_session
    org = Organization(name="Atl TR", slug="tr-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                       industry="energy", is_active=True)
    db.add(org)
    db.commit()
    db.add(User(organization_id=org.id, email="adm-%s@tr.example.test" % uuid.uuid4().hex[:6],
                password_hash=hash_password("x"), full_name="Adm", role="org_admin",
                is_active=True, must_change_password=False))
    db.commit()
    slug = "tr-site-%s" % uuid.uuid4().hex[:6]
    cs.publish(db, organization=org, slug=slug, html=PAGE, title=org.name)
    return db, org, slug


def _post(client, slug, **kw):
    p = {"first_name": "Rel", "last_name": "Check", "request_type": "Compare electricity options",
         "form_started_at": 1}
    p.update(kw)
    r = client.post("/site/%s/inquiry" % slug, json=p)
    assert r.status_code == 201, r.text


def test_a_release_check_is_filed_as_a_test_record(client, site):
    db, org, slug = site
    _post(client, slug, email="atlantis-release-check@example.com")
    lead = db.query(Lead).filter_by(organization_id=org.id).one()
    assert lead.is_test is True and lead.tier == "new_inquiry"     # still fully processed
    assert test_records.is_outreach_eligible(lead) is False
    note = db.query(Notification).filter_by(lead_id=lead.id).one()
    assert note.message.startswith("TEST record - ")


def test_a_real_address_is_never_flagged(client, site):
    db, org, slug = site
    _post(client, slug, email="dana.whitfield@gmail.com")
    lead = db.query(Lead).filter_by(organization_id=org.id).one()
    assert lead.is_test is False
    assert not db.query(Notification).filter_by(lead_id=lead.id).one().message.startswith("TEST")


def test_an_existing_real_person_is_not_turned_into_a_test_record(client, site):
    db, org, slug = site
    _post(client, slug, email="dana@gmail.com", phone="(214) 555-0123")
    # same person (phone match) later types a reserved address
    _post(client, slug, email="dana@example.com", phone="(214) 555-0123")
    leads = db.query(Lead).filter_by(organization_id=org.id).all()
    assert len(leads) == 1 and leads[0].is_test is False


def test_a_test_record_that_hears_from_a_real_address_is_unflagged(client, site):
    db, org, slug = site
    _post(client, slug, email="qa@example.com", phone="(214) 555-0155")
    lead = db.query(Lead).filter_by(organization_id=org.id).one()
    assert lead.is_test is True
    _post(client, slug, email="real.person@gmail.com", phone="(214) 555-0155")
    db.refresh(lead)
    assert db.query(Lead).filter_by(organization_id=org.id).count() == 1
    assert lead.is_test is False and "cleared" in (lead.test_note or "")


def test_a_manually_flagged_test_record_stays_flagged(client, site):
    db, org, slug = site
    _post(client, slug, email="ops@gmail.com", phone="(214) 555-0156")
    lead = db.query(Lead).filter_by(organization_id=org.id).one()
    lead.is_test, lead.test_note = True, "Marked by the owner"
    db.commit()
    _post(client, slug, email="ops@gmail.com", phone="(214) 555-0156")
    db.refresh(lead)
    assert lead.is_test is True and lead.test_note == "Marked by the owner"
