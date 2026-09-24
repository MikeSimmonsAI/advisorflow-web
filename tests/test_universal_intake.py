# -*- coding: utf-8 -*-
"""Universal intake: stage -> normalize -> match -> classify -> commit -> rollback.

These tests pin the behaviour the importer exists for:

  * uploading a contact is not creating a lead
  * creating a lead is not making someone outreach-ready
  * nothing crosses an organization boundary
  * nothing is written to the CRM without an explicit decision
  * a rollback never destroys work done after the import
"""
import io
import json
import time

import pytest

from app.models.import_models import ImportBatch, ImportBatchStatus, ImportStagedRow
from app.models.intake_models import (CommitMode, ContactLifecycle, DuplicateResolution,
                                      EmailStatus, ImportRecordVersion, IntakeStatus,
                                      MatchType, OrgContact, SmsStatus)
from app.models.models import Lead, Organization, User
from app.services.intake import classification as C
from app.services.intake import commit as CM
from app.services.intake import engine as ENG
from app.services.intake import fields as F
from app.services.intake import normalize as N
from app.services.intake import rollback as RB
from app.services.intake.context import IntakeContext


# ══════════════════════════════════════════════════════════════════════════
# helpers
# ══════════════════════════════════════════════════════════════════════════

def _ctx(org, user_id="u-test", role="org_admin", owner=False):
    return IntakeContext(org.id, org.name, org.slug, user_id, "Test Importer",
                         "t@example.com", role, owner)


def _csv(header, rows):
    buf = io.StringIO()
    buf.write(",".join(header) + "\n")
    for r in rows:
        buf.write(",".join('"%s"' % str(c).replace('"', '""') for c in r) + "\n")
    return buf.getvalue().encode("utf-8")


def _stage(db, org, content, filename="t.csv", source="csv", mapping=None,
           classification=None, update_policy=None, user_id=None):
    user_id = user_id or _user(db, org).id
    ctx = _ctx(org, user_id)
    b = ENG.create_batch(db, ctx, content=content, filename=filename, source=source)
    db.commit()
    if mapping or classification or update_policy:
        probs = ENG.save_mapping(db, b, mapping=mapping, classification=classification,
                                 update_policy=update_policy)
        assert not probs, probs
        db.commit()
    ENG.run_analysis(db, b.id, org.id)
    db.refresh(b)
    return b, ctx


def _user(db, org, role="org_admin"):
    u = db.query(User).filter(User.organization_id == org.id, User.role == role).first()
    if u:
        return u
    from app.services.auth_service import hash_password
    u = User(organization_id=org.id, email=f"{role}-{org.slug}@example.com",
             password_hash=hash_password("Pass12345!"), full_name=f"{role} {org.slug}",
             role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _rows(db, b):
    return {r.row_number: r for r in db.query(ImportStagedRow)
            .filter(ImportStagedRow.batch_id == b.id).all()}


def _org(db, name="Atlantis Test Energy", slug="atl-test"):
    o = Organization(name=name, slug=slug, plan="enterprise", industry="energy")
    db.add(o)
    db.commit()
    return o


HDR = ["First Name", "Last Name", "Company", "Phone", "Mobile Phone", "Email",
       "HubSpot Record ID", "Import Segment", "Email Verification", "State", "ZIP"]


# ══════════════════════════════════════════════════════════════════════════
# normalization
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("raw,expected,state", [
    ("(214) 555-1234", "+12145551234", "valid"),
    ("1-214-555-1234", "+12145551234", "valid"),
    ("214.555.1234 x204", "+12145551234", "valid"),
    ("2145551234.0", "+12145551234", "valid"),
    ("+44 20 7946 0958", "+442079460958", "valid"),
    ("555-1234", None, "invalid"),
    ("(014) 555-1234", None, "invalid"),
    ("0000000000", None, "invalid"),
    ("", None, "missing"),
])
def test_phone_normalization(raw, expected, state):
    assert N.phone(raw) == (expected, state)


def test_email_normalization_keeps_problems_visible():
    assert N.email("  Jane.Doe@Example.COM ") == ("jane.doe@example.com", None)
    assert N.email("mailto:a@b.co") == ("a@b.co", None)
    assert N.email("Jane <jane@x.com>") == ("jane@x.com", None)
    assert N.email("not-an-email") == (None, "invalid_format")


def test_state_zip_and_company_normalization():
    assert N.state("Texas") == ("TX", None)
    assert N.state("tx") == ("TX", None)
    assert N.state("Ontario")[1] == "unrecognized_state"
    assert N.zip_code("02110") == ("02110", None)
    assert N.zip_code("2110") == ("02110", "zip_leading_zero_restored")
    assert N.zip_code("752011234") == ("75201-1234", None)
    assert N.company_key("The Acme Co., Inc.") == N.company_key("ACME COMPANY")
    assert N.company("  Acme   Widgets  LLC ") == "Acme Widgets LLC"


def test_dates_are_never_guessed():
    assert N.date("2023-10-03 15:36")[0].year == 2023
    assert N.date("03/04/2021") == (None, "ambiguous_date")
    assert N.date("03/04/2021", N.DATE_MDY)[0].month == 3
    assert N.date("25/04/2021")[0].day == 25
    assert N.slash_date_order_hint(["03/04/2021", "12/25/2020"]) == N.DATE_MDY
    assert N.slash_date_order_hint(["03/04/2021", "25/12/2020"]) == N.DATE_DMY


# ══════════════════════════════════════════════════════════════════════════
# mapping
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("header", ["Last Activity Date", "Last Activity", "LastActivityDate",
                                    "last_activity_date", "Last-Activity-Date"])
def test_header_aliases_map_to_one_field(header):
    s = F.suggest(header)
    assert (s["kind"], s["target"]) == ("standard", "last_activity_date")


def test_unknown_columns_never_disappear_and_unreliable_flags_are_not_trusted():
    m = F.suggest_mapping(["Email", "Favourite Colour", "Current Customer",
                           "Historical Atlantis Customer", "Contract End Date"])
    assert m["Favourite Colour"]["kind"] == "source"          # kept, visible
    assert m["Current Customer"]["kind"] == "source"          # not auto-trusted
    assert m["Historical Atlantis Customer"]["target"] == "historical_customer"
    assert m["Contract End Date"]["kind"] == "vertical"       # never a Lead column


def test_mapping_rejects_two_columns_on_one_field():
    probs = F.validate_mapping(["A", "B"], {"A": {"kind": "standard", "target": "email"},
                                            "B": {"kind": "standard", "target": "email"}})
    assert probs and "both mapped" in probs[0]


# ══════════════════════════════════════════════════════════════════════════
# classification
# ══════════════════════════════════════════════════════════════════════════

def test_classification_suggestions_and_resolution():
    cat = C.catalog()
    assert C.suggest_value("Previous Customer - Win-Back", cat) == "win_back"
    assert C.suggest_value("General Contact Database", cat) == "imported_database"
    assert C.suggest_value("Customer", cat) == "existing_customer"
    assert C.resolve("", {}, "contact", cat) == ("contact", "fallback")
    assert C.resolve("Weird Value", {}, "contact", cat)[0] == C.NEEDS_CLASSIFICATION
    assert C.resolve("weird value", {"Weird Value": "partner"}, "contact", cat) == \
        ("partner", "row")
    # an outside database is never assumed to be new inquiries
    assert C.DEFAULT_FALLBACK == "contact"
    assert cat["contact"].creates_lead is False


def test_row_level_classification_honors_the_file(db_session):
    org = _org(db_session)
    content = _csv(HDR, [
        ["Ann", "One", "", "2145550101", "", "ann@a.com", "1", "Previous Customer - Win-Back", "", "", ""],
        ["Bob", "Two", "", "2145550102", "", "bob@a.com", "2", "General Contact Database", "", "", ""],
        ["Cy", "Three", "", "2145550103", "", "cy@a.com", "3", "", "", "", ""],
        ["Di", "Four", "", "2145550104", "", "di@a.com", "4", "Mystery Segment", "", "", ""],
    ])
    b, _ = _stage(db_session, org, content)
    r = _rows(db_session, b)
    assert (r[2].classification, r[2].record_class, r[2].creates_lead) == \
        ("win_back", "previous_customer", True)
    assert (r[3].classification, r[3].creates_lead) == ("imported_database", False)
    assert (r[4].classification, r[4].classification_source) == ("contact", "fallback")
    assert r[5].classification == C.NEEDS_CLASSIFICATION
    assert r[5].intake_status == IntakeStatus.NEEDS_REVIEW


# ══════════════════════════════════════════════════════════════════════════
# staging never writes the CRM; outreach status is conservative
# ══════════════════════════════════════════════════════════════════════════

def test_staging_writes_no_leads_and_no_contacts(db_session):
    org = _org(db_session)
    content = _csv(HDR, [[f"P{i}", f"L{i}", "", f"21455501{i:02d}", "", f"p{i}@a.com",
                          str(i), "", "", "TX", "75201"] for i in range(20)])
    b, _ = _stage(db_session, org, content)
    assert b.status == ImportBatchStatus.READY_FOR_REVIEW
    assert db_session.query(Lead).filter(Lead.organization_id == org.id).count() == 0
    assert db_session.query(OrgContact).filter(OrgContact.organization_id == org.id).count() == 0
    assert db_session.query(ImportStagedRow).filter(ImportStagedRow.batch_id == b.id).count() == 20


def test_ten_digits_never_means_sms_ready(db_session):
    org = _org(db_session)
    content = _csv(["First Name", "Last Name", "Phone", "Mobile Phone", "Allow SMS",
                    "Email", "Email Verification", "Unsubscribed from all email"], [
        ["A", "A", "2145550101", "", "", "", "", ""],                 # phone only
        ["B", "B", "", "2145550102", "", "", "", ""],                 # mobile, no consent
        ["C", "C", "", "2145550103", "Yes", "", "", ""],              # mobile + consent
        ["D", "D", "", "", "", "d@acmewidgets.com", "HARD BOUNCE", ""],
        ["E", "E", "", "", "", "e@acmewidgets.com", "USABLE", "true"],
        ["F", "F", "", "", "", "f@acmewidgets.com", "USABLE", ""],
        ["G", "G", "", "", "", "info@acmewidgets.com", "", ""],
    ])
    b, _ = _stage(db_session, org, content)
    r = _rows(db_session, b)
    assert r[2].sms_status == SmsStatus.PENDING_VALIDATION
    assert r[3].sms_status == SmsStatus.PENDING_VALIDATION
    assert r[4].sms_status == SmsStatus.READY
    assert r[5].email_status == EmailStatus.HARD_BOUNCE
    assert r[6].email_status == EmailStatus.UNSUBSCRIBED
    assert r[7].email_status == EmailStatus.READY
    assert r[8].email_status == EmailStatus.REVIEW          # role address
    # a hard-bounced-only row has no usable channel
    assert r[5].needs_enrichment is True
    assert r[5].intake_status == IntakeStatus.NEEDS_ENRICHMENT


def test_no_direct_contact_is_preserved_as_needs_enrichment(db_session):
    org = _org(db_session)
    content = _csv(["Company", "Street Address", "City", "State", "ZIP"], [
        ["Acme Widgets", "1 Main St", "Dallas", "Texas", "75201"]])
    b, ctx = _stage(db_session, org, content)
    row = list(_rows(db_session, b).values())[0]
    assert row.intake_status == IntakeStatus.NEEDS_ENRICHMENT
    assert row.state == "TX"
    CM.run_commit(db_session, b.id, org.id, ctx, CommitMode.READY_ONLY, True)
    c = db_session.query(OrgContact).filter(OrgContact.organization_id == org.id).one()
    assert c.lifecycle == ContactLifecycle.NEEDS_ENRICHMENT
    assert c.lead_id is None
    assert db_session.query(Lead).filter(Lead.organization_id == org.id).count() == 0


# ══════════════════════════════════════════════════════════════════════════
# dedupe
# ══════════════════════════════════════════════════════════════════════════

def test_within_file_duplicates(db_session):
    org = _org(db_session)
    content = _csv(HDR, [
        ["Ann", "Smith", "", "2145550101", "", "ann@a.com", "100", "", "", "", ""],
        ["Ann", "Smith", "", "", "", "ANN@A.COM", "101", "", "", "", ""],     # same email
        ["Zed", "Other", "", "", "", "z@a.com", "100", "", "", "", ""],       # same source id
        ["Tom", "Jones", "Acme", "2145550199", "", "", "", "", "", "", ""],
        ["Sue", "Brown", "Acme", "2145550199", "", "", "", "", "", "", ""],   # shared line
        ["", "", "Acme", "2145550188", "", "", "", "", "", "", ""],
        ["Pat", "", "Acme", "2145550188", "", "", "", "", "", "", ""],        # unconfirmed
    ])
    b, _ = _stage(db_session, org, content)
    r = _rows(db_session, b)
    assert r[3].intake_status == IntakeStatus.DUPLICATE
    assert r[3].duplicate_resolution == DuplicateResolution.MERGE or \
        r[3].duplicate_resolution in (DuplicateResolution.MERGE,)
    assert r[4].intake_status == IntakeStatus.DUPLICATE          # source id
    assert r[6].intake_status != IntakeStatus.DUPLICATE          # different surname
    assert "shares_phone_with_other_record" in r[6].status_reasons
    assert r[8].intake_status == IntakeStatus.NEEDS_REVIEW       # possible, not merged


def test_duplicate_source_ids_in_one_file(db_session):
    org = _org(db_session)
    content = _csv(HDR, [["A", "A", "", "", "", "a@x.com", "77", "", "", "", ""],
                         ["B", "B", "", "", "", "b@x.com", "77", "", "", "", ""]])
    b, _ = _stage(db_session, org, content)
    r = _rows(db_session, b)
    assert r[3].intake_status == IntakeStatus.DUPLICATE
    assert json.loads(r[3].match_keys) == ["source_record_id"]


def test_existing_crm_match_and_dnc_block(db_session):
    org = _org(db_session)
    db_session.add(Lead(organization_id=org.id, first_name="Jane", last_name="Smith",
                        phone="+12145550101", email="jane@a.com", status="new"))
    db_session.add(Lead(organization_id=org.id, first_name="Dan", last_name="Nope",
                        phone="+12145550102", status="dnc"))
    db_session.commit()
    content = _csv(HDR, [["Jane", "Smith", "", "", "", "jane@a.com", "", "", "", "", ""],
                         ["Dan", "Nope", "", "(214) 555-0102", "", "", "", "", "", "", ""],
                         ["Jan", "Smithe", "", "", "", "other@a.com", "", "", "", "", ""]])
    b, _ = _stage(db_session, org, content)
    r = _rows(db_session, b)
    assert r[2].intake_status == IntakeStatus.EXISTING_MATCH
    assert r[2].match_type == MatchType.EXACT and r[2].matched_lead_id
    assert r[3].intake_status == IntakeStatus.BLOCKED
    assert r[3].sms_status == SmsStatus.DNC
    assert r[4].intake_status == IntakeStatus.READY


def test_matching_never_crosses_organizations(db_session):
    org_a = _org(db_session, "Org A", "org-a")
    org_b = _org(db_session, "Org B", "org-b")
    db_session.add(Lead(organization_id=org_a.id, first_name="Same", last_name="Person",
                        phone="+12145550111", email="same@x.com", status="new"))
    db_session.add(OrgContact(organization_id=org_a.id, email="same@x.com",
                              source_system="csv", source_record_id="9"))
    db_session.commit()
    content = _csv(HDR, [["Same", "Person", "", "2145550111", "", "same@x.com", "9",
                          "", "", "", ""]])
    b, _ = _stage(db_session, org_b, content)
    row = list(_rows(db_session, b).values())[0]
    assert row.match_type == MatchType.NEW
    assert row.matched_lead_id is None and row.matched_contact_id is None
    assert row.organization_id == org_b.id


# ══════════════════════════════════════════════════════════════════════════
# commit: contact vs lead, stage-only, updates, custom fields, partial failure
# ══════════════════════════════════════════════════════════════════════════

def _mixed_file():
    return _csv(HDR, [
        ["Ann", "One", "", "2145550101", "", "ann@a.com", "1", "Previous Customer - Win-Back", "", "", ""],
        ["Bob", "Two", "", "2145550102", "", "bob@a.com", "2", "General Contact Database", "", "", ""],
        ["", "", "NoChannel Co", "", "", "", "3", "Previous Customer - Win-Back", "", "", ""],
        ["Di", "Four", "", "2145550104", "", "di@a.com", "4", "Mystery", "", "", ""],
    ])


def test_stage_only_is_the_default_and_writes_nothing(db_session):
    org = _org(db_session)
    b, ctx = _stage(db_session, org, _mixed_file())
    assert CM.CommitMode.STAGE_ONLY == "stage_only"
    CM.run_commit(db_session, b.id, org.id, ctx, CommitMode.STAGE_ONLY, True)
    db_session.refresh(b)
    assert b.status == ImportBatchStatus.STAGED
    assert db_session.query(OrgContact).count() == 0
    assert db_session.query(Lead).filter(Lead.organization_id == org.id).count() == 0


def test_contact_versus_lead(db_session):
    org = _org(db_session)
    b, ctx = _stage(db_session, org, _mixed_file())
    preview = CM.commit_preview(db_session, b, CommitMode.READY_ONLY, True)
    assert preview["activate_leads"] == 1
    CM.run_commit(db_session, b.id, org.id, ctx, CommitMode.READY_ONLY, True)
    contacts = db_session.query(OrgContact).filter(OrgContact.organization_id == org.id).all()
    leads = db_session.query(Lead).filter(Lead.organization_id == org.id).all()
    # Ann (win-back, reachable) -> contact + lead. Bob (database) -> contact only.
    # NoChannel Co -> contact in needs_enrichment, no lead. Di -> review, not written.
    assert len(contacts) == 3
    assert len(leads) == 1
    lead = leads[0]
    assert lead.first_name == "Ann" and lead.tier is None and lead.sms_consent is False
    assert lead.org_contact_id and lead.import_batch_id == b.id
    assert lead.relationship_type == "past_customer"
    by_name = {(c.first_name or c.company): c for c in contacts}
    assert by_name["Bob"].lead_id is None
    assert by_name["NoChannel Co"].lifecycle == ContactLifecycle.NEEDS_ENRICHMENT
    db_session.refresh(b)
    assert b.status == ImportBatchStatus.PARTIALLY_COMMITTED      # Di still in review
    rep = json.loads(b.commit_report_json)
    assert rep["leads_created"] == 1 and rep["contacts_created"] == 3


def test_ready_and_review_never_activates_unresolved_rows(db_session):
    org = _org(db_session)
    b, ctx = _stage(db_session, org, _mixed_file())
    CM.run_commit(db_session, b.id, org.id, ctx, CommitMode.READY_AND_REVIEW, True)
    assert db_session.query(OrgContact).filter(OrgContact.organization_id == org.id).count() == 4
    assert db_session.query(Lead).filter(Lead.organization_id == org.id).count() == 1


def test_existing_record_update_respects_policy_and_manual_edits(db_session):
    org = _org(db_session)
    c = OrgContact(organization_id=org.id, first_name="Jane", last_name="Smith",
                   email="jane@a.com", city=None, company="Old Co",
                   source_system="hubspot", source_record_id="55",
                   manually_edited_fields=json.dumps(["company"]))
    db_session.add(c)
    db_session.commit()
    content = _csv(["First Name", "Last Name", "Email", "City", "Company", "Record ID"],
                   [["Jane", "Smith", "jane@a.com", "Dallas", "New Co", "55"]])
    b, ctx = _stage(db_session, org, content, source="hubspot",
                    update_policy={"mode": "overwrite", "fields": ["company", "city"]})
    row = list(_rows(db_session, b).values())[0]
    assert row.intake_status == IntakeStatus.EXISTING_MATCH
    CM.run_commit(db_session, b.id, org.id, ctx, CommitMode.READY_ONLY, False)
    db_session.refresh(c)
    assert c.city == "Dallas"                       # blank filled
    assert c.company == "Old Co"                    # manual correction protected
    assert db_session.query(OrgContact).filter(OrgContact.organization_id == org.id).count() == 1
    v = db_session.query(ImportRecordVersion).filter(ImportRecordVersion.batch_id == b.id,
                                                     ImportRecordVersion.action == "updated").one()
    assert json.loads(v.before_json)["city"] is None


def test_reimport_updates_instead_of_duplicating(db_session):
    org = _org(db_session)
    content = _csv(HDR, [["Jane", "Smith", "", "", "", "jane@a.com", "5091051", "", "", "", ""]])
    b1, ctx = _stage(db_session, org, content, source="hubspot")
    CM.run_commit(db_session, b1.id, org.id, ctx, CommitMode.READY_ONLY, True)
    content2 = _csv(HDR, [["Jane", "Smith", "", "2145550101", "", "jane.new@a.com", "5091051",
                           "", "", "", ""]])
    b2, ctx = _stage(db_session, org, content2, source="hubspot")
    row = list(_rows(db_session, b2).values())[0]
    assert row.intake_status == IntakeStatus.EXISTING_MATCH
    assert json.loads(row.match_keys) == ["source_record_id"]
    CM.run_commit(db_session, b2.id, org.id, ctx, CommitMode.READY_ONLY, True)
    assert db_session.query(OrgContact).filter(OrgContact.organization_id == org.id).count() == 1


def test_custom_and_vertical_fields(db_session):
    org = _org(db_session)
    content = _csv(["First Name", "Last Name", "Email", "Supplier", "Favourite Colour"],
                   [["Al", "Bee", "al@b.com", "Hudson Energy", "Blue"]])
    b, ctx = _stage(db_session, org, content, mapping={
        "First Name": {"kind": "standard", "target": "first_name"},
        "Last Name": {"kind": "standard", "target": "last_name"},
        "Email": {"kind": "standard", "target": "email"},
        "Supplier": {"kind": "vertical", "target": "supplier"},
        "Favourite Colour": {"kind": "custom", "target": "favourite_colour"}})
    CM.run_commit(db_session, b.id, org.id, ctx, CommitMode.READY_ONLY, True)
    c = db_session.query(OrgContact).filter(OrgContact.organization_id == org.id).one()
    assert json.loads(c.vertical_fields) == {"supplier": "Hudson Energy"}
    assert json.loads(c.custom_fields) == {"favourite_colour": "Blue"}
    db_session.refresh(org)
    assert any(d["key"] == "favourite_colour" for d in json.loads(org.crm_custom_fields))
    assert not hasattr(Lead, "supplier")               # never a Lead column


def test_partial_failure_does_not_sink_the_batch(db_session, monkeypatch):
    org = _org(db_session)
    content = _csv(HDR, [[f"P{i}", f"L{i}", "", "", "", f"p{i}@a.com", "", "", "", "", ""]
                         for i in range(5)])
    b, ctx = _stage(db_session, org, content)
    real = CM.create_contact

    def flaky(db, batch, row):
        if row.row_number == 4:
            raise RuntimeError("boom")
        return real(db, batch, row)
    monkeypatch.setattr(CM, "create_contact", flaky)
    CM.run_commit(db_session, b.id, org.id, ctx, CommitMode.READY_ONLY, True)
    db_session.refresh(b)
    rep = json.loads(b.commit_report_json)
    assert rep["failed"] == 1 and rep["contacts_created"] == 4
    assert b.status == ImportBatchStatus.PARTIALLY_COMMITTED
    assert _rows(db_session, b)[4].intake_status == IntakeStatus.FAILED


# ══════════════════════════════════════════════════════════════════════════
# rollback
# ══════════════════════════════════════════════════════════════════════════

def test_rollback_removes_untouched_and_keeps_worked_records(db_session, sample_advisor):
    from app.models.models import Message
    org = _org(db_session)
    content = _csv(HDR, [
        ["Ann", "One", "", "2145550101", "", "ann@a.com", "1", "Win-Back", "", "", ""],
        ["Bob", "Two", "", "2145550102", "", "bob@a.com", "2", "Win-Back", "", "", ""],
        ["Cy", "Three", "", "", "", "cy@a.com", "3", "", "", "", ""],
    ])
    b, ctx = _stage(db_session, org, content)
    CM.run_commit(db_session, b.id, org.id, ctx, CommitMode.READY_ONLY, True)
    bob = db_session.query(Lead).filter(Lead.organization_id == org.id,
                                        Lead.first_name == "Bob").one()
    db_session.add(Message(lead_id=bob.id, sender_id=sample_advisor.id, body="hi"))
    db_session.commit()
    plan = RB.plan(db_session, b)
    assert plan["fully_reversible"] is False
    by = {(i["target_type"], i["target_id"]): i for i in plan["items"]}
    assert by[("lead", bob.id)]["outcome"] == RB.KEEP
    assert "messages" in by[("lead", bob.id)]["reason"]
    RB.execute(db_session, b, ctx)
    db_session.refresh(b)
    assert b.status == ImportBatchStatus.PARTIALLY_ROLLED_BACK
    leads = db_session.query(Lead).filter(Lead.organization_id == org.id).all()
    assert [l.first_name for l in leads] == ["Bob"]                # kept: has activity
    contacts = db_session.query(OrgContact).filter(OrgContact.organization_id == org.id).all()
    assert len(contacts) == 1 and contacts[0].lifecycle == ContactLifecycle.ARCHIVED


def test_rollback_restores_updated_records(db_session):
    org = _org(db_session)
    c = OrgContact(organization_id=org.id, first_name="Jane", last_name="Smith",
                   email="jane@a.com")
    db_session.add(c)
    db_session.commit()
    content = _csv(["First Name", "Last Name", "Email", "City"],
                   [["Jane", "Smith", "jane@a.com", "Dallas"]])
    b, ctx = _stage(db_session, org, content)
    CM.run_commit(db_session, b.id, org.id, ctx, CommitMode.READY_ONLY, True)
    db_session.refresh(c)
    assert c.city == "Dallas"
    RB.execute(db_session, b, ctx)
    db_session.refresh(c)
    assert c.city is None
    db_session.refresh(b)
    assert b.status == ImportBatchStatus.ROLLED_BACK


# ══════════════════════════════════════════════════════════════════════════
# parsing robustness
# ══════════════════════════════════════════════════════════════════════════

def test_malformed_rows_missing_and_extra_columns(db_session):
    org = _org(db_session)
    content = (b"First Name,Last Name,Email\n"
               b"Ann,One,ann@a.com\n"
               b"Bob,Two\n"                                  # short row
               b"Cy,Three,cy@a.com,EXTRA,MORE\n"             # extra cells
               b",,\n"                                       # blank row
               b"Di,\"Fo\"\"ur\",di@a.com\n")
    b, _ = _stage(db_session, org, content)
    a = json.loads(b.analysis_json)
    assert a["total_rows"] == 4
    assert a["read"]["blank_rows"] == 1
    assert a["read"]["malformed_count"] == 1
    r = _rows(db_session, b)
    assert r[4].last_name == "Three"
    assert "_extra_cells" in json.loads(r[4].normalized_json)["source_meta"]


def test_empty_and_headerless_files_are_refused(db_session):
    org = _org(db_session)
    ctx = _ctx(org, _user(db_session, org).id)
    with pytest.raises(ENG.IntakeError):
        ENG.create_batch(db_session, ctx, content=b"", filename="x.csv", source="csv")
    with pytest.raises(ENG.IntakeError):
        ENG.create_batch(db_session, ctx, content=b"A,B\n", filename="x.csv", source="csv")
    with pytest.raises(ENG.IntakeError):
        ENG.create_batch(db_session, ctx, content=b"A,B\n1,2\n", filename="x.pdf", source="csv")


def test_xlsx_upload(db_session):
    from openpyxl import Workbook
    org = _org(db_session)
    wb = Workbook()
    ws = wb.active
    ws.append(["First Name", "Last Name", "ZIP", "Phone"])
    ws.append(["Ann", "One", "02110", 2145550101])
    buf = io.BytesIO()
    wb.save(buf)
    b, _ = _stage(db_session, org, buf.getvalue(), filename="x.xlsx")
    row = list(_rows(db_session, b).values())[0]
    assert row.zip_code == "02110" and row.phone_normalized == "+12145550101"


def test_legacy_commit_refuses_a_universal_batch(db_session):
    from app.services.import_commit_service import UniversalBatchError, commit_batch
    org = _org(db_session)
    b, ctx = _stage(db_session, org, _mixed_file())
    with pytest.raises(UniversalBatchError):
        commit_batch(b.id, org.id, db_session, ctx.actor_id)


def test_fifteen_thousand_rows(db_session):
    org = _org(db_session)
    rows = []
    for i in range(15000):
        kind = i % 5
        rows.append([f"F{i}", f"L{i}", f"Company {i % 900}",
                     f"214{5000000 + i:07d}" if kind in (0, 1) else "",
                     f"469{5000000 + i:07d}" if kind == 2 else "",
                     f"p{i}@example{i % 50}.com" if kind in (0, 3) else "",
                     str(100000 + i), "General Contact Database" if i % 7 else "Win-Back",
                     "", "Texas", "75201"])
    content = _csv(HDR, rows)
    t = time.time()
    b, ctx = _stage(db_session, org, content)
    elapsed = time.time() - t
    a = json.loads(b.analysis_json)
    assert a["total_rows"] == 15000
    assert a["coverage"]["no_direct_contact"] == 3000
    assert elapsed < 90, elapsed
    assert db_session.query(Lead).filter(Lead.organization_id == org.id).count() == 0


def test_rollback_of_a_match_to_a_preexisting_lead_keeps_the_lead(db_session):
    org = _org(db_session)
    lead = Lead(organization_id=org.id, first_name="Pre", last_name="Existing",
                email="pre@acmewidgets.com", status="new")
    db_session.add(lead)
    db_session.commit()
    content = _csv(["First Name", "Last Name", "Email", "City"],
                   [["Pre", "Existing", "pre@acmewidgets.com", "Dallas"]])
    b, ctx = _stage(db_session, org, content)
    CM.run_commit(db_session, b.id, org.id, ctx, CommitMode.READY_ONLY, True)
    db_session.refresh(lead)
    assert lead.org_contact_id is not None
    plan = RB.plan(db_session, b)
    assert plan["fully_reversible"] is True, plan
    RB.execute(db_session, b, ctx)
    db_session.refresh(lead)
    assert lead.org_contact_id is None and lead.city is None
    assert db_session.query(OrgContact).filter(OrgContact.organization_id == org.id).count() == 0


def test_plan_capacity_holds_leads_but_still_imports_contacts(db_session, monkeypatch):
    from app.services import plan_limits

    class OneLeft:
        limit, used = 1, 0

        def has_room(self, n=1):
            return self.used + n <= self.limit

        def take(self, n=1):
            self.used += n
    monkeypatch.setattr(plan_limits, "counter_for_org_id", lambda db, org_id, key: OneLeft())
    org = _org(db_session)
    content = _csv(HDR, [[f"P{i}", f"L{i}", "", f"21455502{i:02d}", "", f"p{i}@acmewidgets.com",
                          str(i), "Win-Back", "", "", ""] for i in range(3)])
    b, ctx = _stage(db_session, org, content)
    CM.run_commit(db_session, b.id, org.id, ctx, CommitMode.READY_ONLY, True)
    rep = json.loads(db_session.query(ImportBatch).get(b.id).commit_report_json)
    assert rep["leads_created"] == 1 and rep["held_for_capacity"] == 2
    assert rep["contacts_created"] == 3
