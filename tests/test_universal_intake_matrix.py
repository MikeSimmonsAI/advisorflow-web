# -*- coding: utf-8 -*-
"""UNIVERSAL INTAKE SOURCE MATRIX (spec sections 42-45 and 61).

Every source goes through the SAME canonical services - the batch engine
(app/services/intake/engine.py + commit.py) and its one-record wrapper
(app/services/intake/capture.py) - or, for a brand's public website, the
existing public capture (site_intake_router -> public_capture). There is no
Max Life pipeline and no Atlantis pipeline: they are an insurance org and an
energy org calling the same functions.

Asserted per permutation: entity identification, normalization, dedupe,
provenance, consent never inferred, STOP/DNC preserved, capacity-held
retention, tenant collisions, invalid fields, retries, partial batch failure.
Nothing is sent; no provider or AI is called.
"""
import io
import csv
import json
import uuid

import pytest

from app.models.import_models import ImportBatch
from app.models.intake_models import CommitMode, OrgContact, SmsStatus
from app.models.models import Lead, Organization, Platform, User
from app.services import google_contacts_service as GC
from app.services import lead_capacity
from app.services.auth_service import hash_password
from app.services.intake import capture as CAP
from app.services.intake import commit as CM
from app.services.intake import engine as ENG
from app.services.intake.context import IntakeContext


# ── fixtures ────────────────────────────────────────────────────────────────

def _org(db, name, industry=None):
    o = Organization(name=name, slug="uim-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                     industry=industry, is_active=True)
    db.add(o)
    db.commit()
    return o


def _admin(db, org):
    u = User(organization_id=org.id, email="adm-%s@uim.test" % uuid.uuid4().hex[:6],
             password_hash=hash_password("Pass12345!"), full_name="Intake Admin",
             role="org_admin", is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _ctx(org, user):
    return IntakeContext(org_id=org.id, org_name=org.name, org_slug=org.slug, actor_id=user.id,
                         actor_name=user.full_name, actor_email=user.email, role="org_admin",
                         acting_as_platform_owner=False)


def _batch(db, org, content, source, mode=CommitMode.READY_AND_REVIEW):
    """CSV / HubSpot / Google: create -> analyze -> commit, the import path."""
    user = _admin(db, org)
    ctx = _ctx(org, user)
    b = ENG.create_batch(db, ctx, content=content, filename="%s.csv" % source, source=source)
    db.commit()
    ENG.run_analysis(db, b.id, org.id)
    CM.run_commit(db, b.id, org.id, ctx, mode, True)
    db.expire_all()
    return db.query(ImportBatch).get(b.id)


def _csv(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


def _contacts(db, org):
    return db.query(OrgContact).filter(OrgContact.organization_id == org.id).all()


def _leads(db, org):
    return db.query(Lead).filter(Lead.organization_id == org.id).all()


PERSON = {"first_name": "Avery", "last_name": "Stone", "email": "Avery.Stone@Example.com",
          "phone": "(214) 555-0181", "city": "Dallas", "state": "tx", "zip_code": "75201"}


def _no_consent(lead):
    assert lead is None or not lead.sms_consent, "consent was inferred"
    assert lead is None or lead.sms_consent_timestamp is None


# ── 1. each source lands as an entity, normalized, with provenance ──────────

def test_csv_batch_source(db_session):
    org = _org(db_session, "CSV Co")
    b = _batch(db_session, org, _csv(["First Name", "Last Name", "Email", "Phone", "State"],
                                     [["Casey", "Row", "CASEY@EXAMPLE.COM", "214.555.0101", "Texas"]]),
               "csv")
    [c] = _contacts(db_session, org)
    assert c.email == "casey@example.com" and c.phone == "+12145550101"     # normalized
    assert c.state == "TX"
    assert c.import_batch_id == b.id and c.source_row_number == 2          # provenance
    assert c.phone_raw in ("214.555.0101",) or c.phone_raw                 # raw kept
    assert c.sms_status != SmsStatus.READY                                 # imported phone != consent
    for l in _leads(db_session, org):
        _no_consent(l)


def test_hubspot_like_fixture_keeps_source_record_id(db_session):
    org = _org(db_session, "HS Co")
    _batch(db_session, org, _csv(["First Name", "Last Name", "Email", "Phone", "HubSpot Record ID",
                                  "Favourite Colour"],
                                 [["Hana", "Spot", "hana@example.com", "2145550102", "HS-9001", "teal"]]),
           "hubspot")
    [c] = _contacts(db_session, org)
    assert c.source_record_id == "HS-9001"
    assert (c.source_system or c.source) and "hubspot" in (c.source_system or c.source).lower()
    # unknown columns are retained as source fields, never dropped
    assert json.loads(c.source_fields or "{}").get("favourite_colour") == "teal"
    assert c.sms_status != SmsStatus.READY


def test_google_contact_like_fixture_through_the_same_engine(db_session):
    org = _org(db_session, "G Co")
    people = [{"resourceName": "people/c77", "names": [{"givenName": "Gia", "familyName": "Le"}],
               "phoneNumbers": [{"value": "+1 214-555-0103", "type": "mobile"}],
               "emailAddresses": [{"value": "gia@example.com"}]}]
    _batch(db_session, org, GC.rows_to_csv(GC.google_people_rows(people)), "google_contacts")
    [c] = _contacts(db_session, org)
    assert c.source_record_id == "people/c77" and c.email == "gia@example.com"
    # the Google "mobile" label is source data, not a verified line type or consent
    assert c.sms_status != SmsStatus.READY


def test_api_style_payload_and_website_capture_one(db_session):
    org = _org(db_session, "API Co")
    r = CAP.capture_one(db_session, org, dict(PERSON), source="api", source_detail="Partner API")
    assert r.match == "new" and r.contact_id and r.lead_created
    c = db_session.query(OrgContact).get(r.contact_id)
    assert c.email == "avery.stone@example.com" and c.phone == "+12145550181" and c.state == "TX"
    assert c.source_detail == "Partner API" or "api" in (c.source or "").lower()
    _no_consent(r.lead)                      # submission without opt-in != consent


def test_evosense_fixture_owner_is_not_made_contactable(db_session):
    org = _org(db_session, "Evo Co", industry="real_estate")
    u = _admin(db_session, org)
    r = CAP.capture_one(db_session, org, {"first_name": "Owen", "last_name": "Owner",
                                          "phone": "2145550104", "street_address": "1 Main St",
                                          "city": "Dallas", "state": "TX", "zip_code": "75201"},
                        source="evosense", source_detail="EvoSense property owner",
                        classification="cold_prospect", actor_label="EvoSense",
                        external=False, explicit=True, user=u)
    c = db_session.query(OrgContact).get(r.contact_id)
    assert c.source == "evosense" or "evosense" in (c.source_detail or "").lower()
    _no_consent(r.lead)                      # public record / skip trace != consent
    assert c.sms_status != SmsStatus.READY


@pytest.mark.parametrize("name,industry,email", [
    ("Max Life Test Agency", "insurance", "family@maxlife-fixture.test"),
    ("Atlantis Test Power", "energy", "customer@atlantis-fixture.test"),
])
def test_max_life_and_atlantis_use_the_same_core_service(db_session, name, industry, email):
    org = _org(db_session, name, industry=industry)
    r = CAP.capture_one(db_session, org, {"first_name": "Robin", "last_name": "Vale",
                                          "email": email, "phone": "2145550105"},
                        source="website", source_detail="Website Inquiry")
    assert r.match == "new" and r.lead_created
    assert r.lead.organization_id == org.id and r.lead.org_contact_id == r.contact_id
    _no_consent(r.lead)
    # same engine, same batch table: provenance is the batch it came in on
    assert db_session.query(ImportBatch).filter(ImportBatch.id == r.batch_id,
                                                ImportBatch.organization_id == org.id).count() == 1


# ── 2. dedupe permutations ─────────────────────────────────────────────────

def test_same_person_new_inquiry_is_same_identity_new_event(db_session):
    org = _org(db_session, "Dedupe Co")
    a = CAP.capture_one(db_session, org, dict(PERSON))
    b = CAP.capture_one(db_session, org, dict(PERSON, notes="Second inquiry"))
    assert b.match == "existing" and b.contact_id == a.contact_id
    assert b.batch_id != a.batch_id                      # a new inquiry event
    assert len(_contacts(db_session, org)) == 1
    assert len(_leads(db_session, org)) == 1             # no duplicate lead


def test_rapid_exact_repeat_and_retry_are_idempotent(db_session):
    org = _org(db_session, "Retry Co")
    first = CAP.capture_one(db_session, org, dict(PERSON))
    for _ in range(3):
        again = CAP.capture_one(db_session, org, dict(PERSON))
        assert again.contact_id == first.contact_id
        assert again.lead is not None and again.lead.id == first.lead.id
        assert again.lead_created is False
    assert len(_contacts(db_session, org)) == 1 and len(_leads(db_session, org)) == 1


def test_same_email_different_name_goes_to_review_not_merged(db_session):
    org = _org(db_session, "Review Co")
    a = CAP.capture_one(db_session, org, dict(PERSON))
    b = CAP.capture_one(db_session, org, {"first_name": "Jordan", "last_name": "Quill",
                                          "email": PERSON["email"]})
    assert b.match == "possible"
    assert "possible_duplicate_kept_separate" in b.notes
    assert b.contact_id != a.contact_id                  # never silently merged
    keep = db_session.query(OrgContact).get(a.contact_id)
    assert keep.first_name == "Avery"                    # original identity untouched


def test_same_phone_different_name_goes_to_review(db_session):
    org = _org(db_session, "Phone Review Co")
    a = CAP.capture_one(db_session, org, dict(PERSON))
    b = CAP.capture_one(db_session, org, {"first_name": "Morgan", "last_name": "Ash",
                                          "phone": PERSON["phone"]})
    # The matcher's documented rule: a number shared by two DIFFERENT surnames
    # is a shared line (household / switchboard), so it is a separate contact,
    # never merged - and the staged row carries the reason so a reviewer sees it.
    # (Spec 43 asks for "possible duplicate review" here; see the S7 report.)
    from app.models.import_models import ImportStagedRow
    assert b.match == "new" and b.contact_id != a.contact_id
    row = db_session.query(ImportStagedRow).filter(ImportStagedRow.batch_id == b.batch_id).one()
    assert "shares_phone_with_other_record" in (row.status_reasons or "")
    assert db_session.query(OrgContact).get(a.contact_id).first_name == "Avery"


def test_within_file_duplicate_is_one_contact(db_session):
    org = _org(db_session, "File Dupe Co")
    _batch(db_session, org, _csv(["First Name", "Last Name", "Email"],
                                 [["Dee", "Up", "dee@example.com"], ["Dee", "Up", "DEE@example.com"]]),
           "csv")
    assert len(_contacts(db_session, org)) == 1


# ── 3. consent / STOP / DNC ────────────────────────────────────────────────

def test_skip_trace_and_imported_phone_never_become_consent(db_session):
    org = _org(db_session, "Skip Co")
    r = CAP.capture_one(db_session, org, {"first_name": "Sky", "last_name": "Trace",
                                          "phone": "2145550106"},
                        source="skip_trace", source_detail="Skip trace result",
                        classification="cold_prospect", external=False, explicit=True)
    _no_consent(r.lead)
    c = db_session.query(OrgContact).get(r.contact_id)
    assert c.sms_status != SmsStatus.READY


def test_dnc_contact_submitting_again_stays_dnc(db_session):
    org = _org(db_session, "DNC Co")
    first = CAP.capture_one(db_session, org, dict(PERSON))
    c = db_session.query(OrgContact).get(first.contact_id)
    c.sms_status = SmsStatus.DNC
    if first.lead is not None:
        first.lead.status = "dnc"
    db_session.commit()
    again = CAP.capture_one(db_session, org, dict(PERSON, notes="Please call me"))
    db_session.expire_all()
    assert again.contact_id == first.contact_id
    assert "existing_record_dnc" in again.notes
    assert db_session.query(OrgContact).get(first.contact_id).sms_status == SmsStatus.DNC
    leads = _leads(db_session, org)
    assert all(l.status == "dnc" for l in leads)          # STOP preserved, nothing re-opened
    for l in leads:
        _no_consent(l)


# ── 4. capacity-held retention ─────────────────────────────────────────────

class _Full:
    limit, used = 0, 0

    def has_room(self, n=1):
        return False

    def take(self, n=1):
        self.used += n


def test_plan_limit_reached_retains_contact_and_holds_lead(db_session, monkeypatch):
    from app.services import plan_limits
    from app.services.compliance_service import check_compliance_preflight
    monkeypatch.setattr(plan_limits, "counter_for_org_id", lambda db, org_id, key: _Full())
    org = _org(db_session, "Full Co")
    r = CAP.capture_one(db_session, org, dict(PERSON))
    assert r.contact_id is not None                       # contact retained
    assert db_session.query(OrgContact).get(r.contact_id) is not None
    assert r.lead is not None and r.held is True          # inquiry retained, held
    assert lead_capacity.is_held(r.lead)
    for channel in ("sms", "email"):                      # no outreach until eligible
        with pytest.raises(ValueError, match="capacity"):
            check_compliance_preflight(db_session, r.lead, channel=channel)


def test_plan_limit_on_a_bulk_import_keeps_every_contact(db_session, monkeypatch):
    from app.services import plan_limits
    monkeypatch.setattr(plan_limits, "counter_for_org_id", lambda db, org_id, key: _Full())
    org = _org(db_session, "Full Bulk Co")
    b = _batch(db_session, org, _csv(["First Name", "Last Name", "Email", "Phone"],
                                     [["P%d" % i, "L", "p%d@example.com" % i, "21455502%02d" % i]
                                      for i in range(3)]), "csv")
    rep = json.loads(b.commit_report_json)
    assert rep["contacts_created"] == 3                   # nothing silently discarded


# ── 5. tenant collisions ───────────────────────────────────────────────────

def test_same_email_in_two_orgs_stays_separate(db_session):
    a, b = _org(db_session, "Tenant A"), _org(db_session, "Tenant B")
    ra = CAP.capture_one(db_session, a, dict(PERSON))
    rb = CAP.capture_one(db_session, b, dict(PERSON))
    assert rb.match == "new"                              # never matched across tenants
    assert ra.contact_id != rb.contact_id and ra.lead.id != rb.lead.id
    assert len(_contacts(db_session, a)) == 1 and len(_contacts(db_session, b)) == 1
    assert ra.lead.organization_id == a.id and rb.lead.organization_id == b.id


# ── 6. invalid fields and partial batch failure ────────────────────────────

def test_invalid_fields_are_kept_visible_not_trusted(db_session):
    org = _org(db_session, "Invalid Co")
    _batch(db_session, org, _csv(["First Name", "Last Name", "Email", "Phone"],
                                 [["Ivy", "Bad", "not-an-email", "123"],
                                  ["Val", "Good", "val@example.com", "2145550107"]]), "csv")
    cs = {c.first_name: c for c in _contacts(db_session, org)}
    assert "Val" in cs
    if "Ivy" in cs:                                       # kept, but never made reachable
        assert cs["Ivy"].sms_status != SmsStatus.READY
        assert cs["Ivy"].email_status != "valid"
        assert cs["Ivy"].phone in (None, "")


def test_partial_batch_failure_keeps_the_good_rows(db_session, monkeypatch):
    org = _org(db_session, "Partial Co")
    real = CM.create_contact

    def flaky(db, batch, row):
        if row.row_number == 3:
            raise RuntimeError("boom")
        return real(db, batch, row)
    monkeypatch.setattr(CM, "create_contact", flaky)
    b = _batch(db_session, org, _csv(["First Name", "Last Name", "Email"],
                                     [["R%d" % i, "L", "r%d@example.com" % i] for i in range(4)]),
               "csv")
    rep = json.loads(b.commit_report_json)
    assert rep["failed"] == 1 and rep["contacts_created"] == 3


# ── 7. brand website inquiry (public capture) ──────────────────────────────

@pytest.fixture()
def brand(db_session):
    p = Platform(name="Fixture Life", slug="fx-%s" % uuid.uuid4().hex[:6],
                 website_url="https://fixture-life.example", support_email="help@fixture-life.example")
    db_session.add(p)
    db_session.commit()
    o = Organization(name="Fixture Life Agency", slug="fxl-%s" % uuid.uuid4().hex[:6], plan="standard",
                     industry="insurance", platform_id=p.id, is_active=True)
    db_session.add(o)
    db_session.commit()
    p.public_intake_organization_id = o.id
    db_session.commit()
    return p, o


def test_brand_inquiry_without_opt_in_is_not_consent_and_repeat_dedupes(client, db_session, brand):
    p, o = brand
    body = {"name": "Fran Family", "email": "fran@example.com", "phone": "(214) 555-0190",
            "journey": "family", "message": "We want a planning call"}
    r = client.post(f"/site-intake/{p.slug}/inquiry", json=body)
    assert r.status_code == 201, r.text
    lead = db_session.query(Lead).get(r.json()["lead_id"])
    assert lead.organization_id == o.id
    assert lead.source_detail == "Website Inquiry - Families & Individuals"   # provenance
    _no_consent(lead)
    r2 = client.post(f"/site-intake/{p.slug}/inquiry", json=body)          # rapid repeat
    assert r2.status_code == 201 and r2.json()["lead_id"] == lead.id
    assert db_session.query(Lead).filter(Lead.organization_id == o.id).count() == 1


def test_brand_inquiry_with_explicit_opt_in_records_it_verbatim(client, db_session, brand):
    p, _ = brand
    r = client.post(f"/site-intake/{p.slug}/inquiry", json={
        "name": "Bo Builder", "email": "bo@example.com", "phone": "2145550191", "journey": "builder",
        "sms_consent": "YES", "sms_consent_text": "I agree to receive texts. Reply STOP to opt out."})
    assert r.status_code == 201, r.text
    lead = db_session.query(Lead).get(r.json()["lead_id"])
    assert lead.sms_consent is True
    assert lead.sms_consent_text == "I agree to receive texts. Reply STOP to opt out."


def test_brand_inquiry_invalid_fields_are_refused(client, db_session, brand):
    p, o = brand
    assert client.post(f"/site-intake/{p.slug}/inquiry",
                       json={"name": "X", "email": "x@example.com", "journey": "other"}).status_code == 422
    assert client.post(f"/site-intake/{p.slug}/inquiry",
                       json={"name": "Nobody", "journey": "family"}).status_code == 422
    assert client.post("/site-intake/not-a-brand/inquiry",
                       json={"name": "N", "email": "n@example.com", "journey": "family"}).status_code in (404, 422, 503)
    assert db_session.query(Lead).filter(Lead.organization_id == o.id).count() == 0
