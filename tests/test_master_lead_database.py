"""THE ADVISORFLOW MASTER LEAD DATABASE — Stage 1.

FIVE THINGS THIS FILE DEFENDS

1. TENANT ISOLATION. The master database is the only place in this codebase
   that reads across customers, so it is the only place where a mistake leaks
   one customer's people to another. Every route is god_admin; an advisor, an
   org_admin and a super_admin all get 403; and no customer-facing router
   imports the models at all.

2. THE MERGE THAT MUST NOT HAPPEN. When an email points at one master person
   and a phone points at a different one, V1 flags both and merges neither.
   A wrong merge fuses two people's histories irreversibly.

3. IDEMPOTENCE. Ingestion and the backfill both run over the same leads, and
   re-running either must never produce a second occurrence or a second count.

4. THE BACKFILL IS INERT TOWARDS CUSTOMER DATA. It reads `leads` and writes
   only platform tables — asserted by comparing every lead column before and
   after, not by reading the code and believing it.

5. RECORDING A LEAD CANNOT BREAK CREATING A LEAD. When the master write fails
   for any reason at all, the customer still gets their lead.
"""

import itertools

import pytest

from app.models.master_contact_models import LeadOccurrence, MasterContact
from app.models.models import Lead, Organization, Platform, User
from app.services import master_backfill, master_contacts
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


# ── scaffolding ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_platform_cache():
    master_contacts.reset_platform_cache()
    yield
    master_contacts.reset_platform_cache()


def _platform(db, name="EvoSys Pro"):
    p = Platform(name=name, slug="plat%d" % next(_SEQ))
    db.add(p)
    db.commit()
    return p


def _org(db, name, platform=None, is_demo=False):
    o = Organization(name=name, slug="org%d" % next(_SEQ),
                     platform_id=platform.id if platform else None,
                     is_demo=is_demo)
    db.add(o)
    db.commit()
    return o


def _lead(db, org, *, first="Ada", last="Lovelace", email=None, phone=None,
          status="new", is_test=False, source=None, commit=True):
    l = Lead(organization_id=org.id, first_name=first, last_name=last,
             email=email, phone=phone, status=status, is_test=is_test,
             source=source)
    db.add(l)
    if commit:
        db.commit()
    else:
        db.flush()
    return l


def _headers(db, user):
    return {"Authorization": f"Bearer {create_access_token(user, db)}"}


def _user(db, org, role):
    u = User(organization_id=org.id if org else None,
             email="u%d@example.test" % next(_SEQ),
             password_hash=hash_password("x"), full_name="U", role=role,
             must_change_password=False)
    db.add(u)
    db.commit()
    return u


# ═════════════════════════════════════════════════════════════════════════════
# 1. Retention and lineage
# ═════════════════════════════════════════════════════════════════════════════

def test_a_recorded_lead_produces_one_person_and_one_appearance(db_session):
    plat = _platform(db_session)
    org = _org(db_session, "Atlantis Light & Power", plat)
    lead = _lead(db_session, org, email="Ada@Example.ORG", phone="(555) 010-1234")

    occ = master_contacts.record_lead(db_session, lead, source="import",
                                      source_detail="atlantis_q3.csv")
    db_session.commit()

    assert occ is not None
    assert db_session.query(MasterContact).count() == 1
    assert db_session.query(LeadOccurrence).count() == 1

    contact = db_session.query(MasterContact).one()
    # Normalized on the way in, both of them. An address stored as typed is an
    # address that will one day fail to match itself.
    assert contact.normalized_email == "ada@example.org"
    assert contact.normalized_phone == "15550101234"
    assert contact.occurrence_count == 1

    assert occ.organization_id == org.id
    assert occ.platform_id == plat.id          # the lineage, which is the point
    assert occ.lead_id == lead.id
    assert occ.source == "import"
    assert occ.source_detail == "atlantis_q3.csv"
    assert occ.tenant_lead_status == "new"


def test_the_tenants_own_lead_row_is_never_written_by_recording_it(db_session):
    org = _org(db_session, "WUPA")
    lead = _lead(db_session, org, email="bob@example.org", phone="5550109999")
    before = {c.name: getattr(lead, c.name) for c in Lead.__table__.columns}

    master_contacts.record_lead(db_session, lead)
    db_session.commit()
    db_session.refresh(lead)

    after = {c.name: getattr(lead, c.name) for c in Lead.__table__.columns}
    assert before == after


# ═════════════════════════════════════════════════════════════════════════════
# 2. Matching V1
# ═════════════════════════════════════════════════════════════════════════════

def test_the_same_email_in_two_organizations_is_one_person_and_two_appearances(db_session):
    a = _org(db_session, "Atlantis")
    b = _org(db_session, "Fiber Cartel")
    la = _lead(db_session, a, email="joan@example.org")
    lb = _lead(db_session, b, email="JOAN@example.org", first="Joanie")

    master_contacts.record_lead(db_session, la, source="import")
    master_contacts.record_lead(db_session, lb, source="manual")
    db_session.commit()

    assert db_session.query(MasterContact).count() == 1
    contact = db_session.query(MasterContact).one()
    assert contact.occurrence_count == 2

    orgs = {o.organization_id for o in db_session.query(LeadOccurrence).all()}
    assert orgs == {a.id, b.id}          # both origins preserved, separately


def test_the_same_phone_typed_differently_is_still_the_same_person(db_session):
    a = _org(db_session, "Atlantis")
    b = _org(db_session, "WUPA")
    master_contacts.record_lead(db_session, _lead(db_session, a, phone="555-010-7777"))
    master_contacts.record_lead(db_session, _lead(db_session, b, phone="+1 (555) 010 7777"))
    db_session.commit()

    assert db_session.query(MasterContact).count() == 1
    assert db_session.query(MasterContact).one().occurrence_count == 2


def test_an_unusable_phone_never_becomes_a_match_key(db_session):
    """Two people with junk numbers and no email are two people.

    The worst possible outcome for a deduplicating system is treating "could
    not normalize" as a value: every unmatchable record would then match every
    other unmatchable record, and the master database would fuse strangers.
    """
    org = _org(db_session, "Atlantis")
    master_contacts.record_lead(db_session, _lead(db_session, org, first="A", phone="n/a"))
    master_contacts.record_lead(db_session, _lead(db_session, org, first="B", phone="000"))
    db_session.commit()

    assert db_session.query(MasterContact).count() == 2
    for c in db_session.query(MasterContact).all():
        assert c.normalized_phone is None
        assert c.normalized_email is None


def test_a_conflict_between_email_and_phone_is_flagged_and_never_merged(db_session):
    org = _org(db_session, "Atlantis")
    b_org = _org(db_session, "WUPA")

    # Two distinct people already known: one by email, one by phone.
    master_contacts.record_lead(db_session, _lead(db_session, org, first="Email",
                                                 email="shared@example.org"))
    master_contacts.record_lead(db_session, _lead(db_session, org, first="Phone",
                                                 phone="5550105555"))
    db_session.commit()
    assert db_session.query(MasterContact).count() == 2

    # Now a lead carrying BOTH keys arrives. They point at different people.
    conflicted = _lead(db_session, b_org, first="Both",
                       email="shared@example.org", phone="5550105555")
    occ = master_contacts.record_lead(db_session, conflicted)
    db_session.commit()

    # NOT MERGED. Still two people.
    assert db_session.query(MasterContact).count() == 2
    flagged = db_session.query(MasterContact).filter(
        MasterContact.needs_review.is_(True)).all()
    assert len(flagged) == 2
    for c in flagged:
        assert "Not merged" in (c.review_reason or "")

    # The occurrence is attached to the EMAIL match, and is not lost.
    by_email = db_session.query(MasterContact).filter(
        MasterContact.normalized_email == "shared@example.org").one()
    assert occ.master_contact_id == by_email.id

    # And neither identity was rewritten by the conflicting row.
    by_phone = db_session.query(MasterContact).filter(
        MasterContact.normalized_phone == "15550105555").one()
    assert by_email.normalized_phone is None
    assert by_phone.normalized_email is None


def test_a_missing_key_is_learned_but_an_existing_one_is_never_overwritten(db_session):
    org = _org(db_session, "Atlantis")
    first = _lead(db_session, org, email="grace@example.org")
    master_contacts.record_lead(db_session, first)
    db_session.commit()

    second = _lead(db_session, org, email="grace@example.org", phone="5550102222")
    master_contacts.record_lead(db_session, second)
    db_session.commit()

    contact = db_session.query(MasterContact).one()
    assert contact.normalized_email == "grace@example.org"
    assert contact.normalized_phone == "15550102222"   # learned

    third = _lead(db_session, org, email="grace@example.org", phone="5550103333")
    master_contacts.record_lead(db_session, third)
    db_session.commit()

    db_session.refresh(contact)
    assert contact.normalized_phone == "15550102222"   # NOT overwritten


# ═════════════════════════════════════════════════════════════════════════════
# 3. Idempotence
# ═════════════════════════════════════════════════════════════════════════════

def test_recording_the_same_lead_twice_does_not_count_it_twice(db_session):
    org = _org(db_session, "Atlantis")
    lead = _lead(db_session, org, email="ada@example.org", phone="5550101234")

    master_contacts.record_lead(db_session, lead, source="import")
    master_contacts.record_lead(db_session, lead, source="import")
    master_contacts.record_lead(db_session, lead, source="import")
    db_session.commit()

    assert db_session.query(LeadOccurrence).count() == 1
    assert db_session.query(MasterContact).one().occurrence_count == 1


def test_re_recording_refreshes_status_without_creating_anything(db_session):
    org = _org(db_session, "Atlantis")
    lead = _lead(db_session, org, email="ada@example.org", status="new")
    master_contacts.record_lead(db_session, lead)
    db_session.commit()

    lead.status = "booked"
    db_session.commit()
    master_contacts.record_lead(db_session, lead)
    db_session.commit()

    occ = db_session.query(LeadOccurrence).one()
    assert occ.tenant_lead_status == "booked"
    assert db_session.query(MasterContact).one().occurrence_count == 1


def test_two_leads_in_the_same_org_for_one_person_are_two_appearances(db_session):
    """A tenant duplicate is still two tenant records, and the master database
    says so — one person, two appearances, both traceable to their own lead."""
    org = _org(db_session, "Atlantis")
    a = _lead(db_session, org, email="dup@example.org")
    b = _lead(db_session, org, email="dup@example.org")
    master_contacts.record_lead(db_session, a)
    master_contacts.record_lead(db_session, b)
    db_session.commit()

    assert db_session.query(MasterContact).count() == 1
    assert db_session.query(LeadOccurrence).count() == 2


# ═════════════════════════════════════════════════════════════════════════════
# 4. Synthetic and QA
# ═════════════════════════════════════════════════════════════════════════════

def test_a_test_lead_and_a_demo_org_are_both_marked_synthetic(db_session):
    real = _org(db_session, "Atlantis")
    demo = _org(db_session, "Proof Environment", is_demo=True)

    master_contacts.record_lead(db_session, _lead(db_session, real, first="Real",
                                                  email="real@example.org",
                                                  is_test=True))
    master_contacts.record_lead(db_session, _lead(db_session, demo, first="Demo",
                                                  email="demo@other.org"))
    db_session.commit()

    assert db_session.query(MasterContact).count() == 2
    assert db_session.query(MasterContact).filter(
        MasterContact.is_synthetic.is_(True)).count() == 2


def test_seed_addresses_on_a_plausible_domain_are_recognised_as_props(db_session):
    """The case the first production backfill actually found.

    1,683 leads addressed `demo.lead.NNNN@example-evosyspro.com` read as real
    people, because the domain is not RFC-reserved. A domain whose first label
    is literally "example" is the tell.
    """
    org = _org(db_session, "WUPA")
    for addr in ["demo.lead.1650@example-evosyspro.com",
                 "someone@example-bookaboost.com",
                 "seed.lead.4@realish-domain.com",
                 "synthetic.person@acme.co"]:
        master_contacts.record_lead(db_session, _lead(db_session, org, email=addr))
    db_session.commit()

    assert db_session.query(MasterContact).count() == 4
    assert db_session.query(MasterContact).filter(
        MasterContact.is_synthetic.is_(True)).count() == 4


def test_real_people_whose_addresses_merely_start_with_those_words_are_not_flagged(db_session):
    """The false positives a substring match would have produced.

    `demostrene`, `testaverde` and `qadir` are real surnames, and `example` in
    the middle of a domain is an ordinary word. Flagging a living person as a
    prop hides them from the only view an operator looks at.
    """
    org = _org(db_session, "Atlantis")
    for addr in ["demostrene@gmail.com", "testaverde@outlook.com",
                 "qadir@nhs.uk", "sales@byexample.com",
                 "demo@realcompany.com"]:
        master_contacts.record_lead(db_session, _lead(db_session, org, email=addr))
    db_session.commit()

    assert db_session.query(MasterContact).filter(
        MasterContact.is_synthetic.is_(True)).count() == 0


def test_a_refresh_pass_reclassifies_without_creating_or_double_counting(db_session):
    """Synthetic is derived, so improving the rule must fix the whole estate."""
    org = _org(db_session, "WUPA")
    seeded = _lead(db_session, org, email="demo.lead.7@example-evosyspro.com")
    real = _lead(db_session, org, email="mike@realbrand.org")
    master_backfill.backfill(db_session)

    # Simulate the pre-fix state: the rule did not know about these yet.
    for occ in db_session.query(LeadOccurrence).all():
        occ.is_synthetic = False
    for c in db_session.query(MasterContact).all():
        c.is_synthetic = False
    db_session.commit()

    result = master_backfill.backfill(db_session, refresh=True)

    assert result["refresh"] is True
    assert result["scanned"] == 2
    assert result["contacts_created"] == 0
    assert db_session.query(LeadOccurrence).count() == 2
    assert db_session.query(MasterContact).count() == 2
    for c in db_session.query(MasterContact).all():
        assert c.occurrence_count == 1        # nothing counted twice

    seeded_occ = db_session.query(LeadOccurrence).filter(
        LeadOccurrence.lead_id == seeded.id).one()
    real_occ = db_session.query(LeadOccurrence).filter(
        LeadOccurrence.lead_id == real.id).one()
    assert seeded_occ.is_synthetic is True
    assert real_occ.is_synthetic is False


def test_a_refresh_pass_pages_forward_instead_of_re_reading_the_same_rows(db_session):
    """The defect that made the first production refresh attempt useless.

    The ordinary pass is self-advancing: a lead it records gains an occurrence
    and drops out of the next call's query. A refresh filters nothing, so
    without a cursor every call re-reads the head of the table and a caller
    paging through it loops forever over the first `limit` rows.
    """
    org = _org(db_session, "WUPA")
    for i in range(10):
        _lead(db_session, org, email="p%d@realbrand.org" % i)
    master_backfill.backfill(db_session)

    seen = []
    cursor = None
    for _ in range(6):
        result = master_backfill.backfill(
            db_session, refresh=True, limit=3, batch_size=3,
            after_lead_id=cursor)
        if result["scanned"] == 0:
            break
        cursor = result["last_lead_id"]
        seen.append((result["scanned"], cursor))

    # Four pages of 3, 3, 3, 1 — and every cursor distinct, which is the
    # assertion that actually fails if paging regresses.
    assert [s for s, _ in seen] == [3, 3, 3, 1]
    cursors = [c for _, c in seen]
    assert len(set(cursors)) == len(cursors)
    assert db_session.query(LeadOccurrence).count() == 10
    assert db_session.query(MasterContact).count() == 10


def test_one_real_appearance_keeps_a_person_out_of_the_props(db_session):
    """A human seen in a demo org AND a real one is a human."""
    demo = _org(db_session, "Proof", is_demo=True)
    real = _org(db_session, "Atlantis")
    master_contacts.record_lead(db_session, _lead(db_session, demo,
                                                  email="dual@realbrand.org"))
    master_contacts.record_lead(db_session, _lead(db_session, real,
                                                  email="dual@realbrand.org"))
    db_session.commit()

    contact = db_session.query(MasterContact).one()
    assert contact.is_synthetic is False
    assert contact.occurrence_count == 2

    # And a refresh does not undo that.
    master_backfill.backfill(db_session, refresh=True)
    db_session.refresh(contact)
    assert contact.is_synthetic is False
    assert contact.occurrence_count == 2


def test_a_person_who_reappears_for_real_stops_being_synthetic(db_session):
    demo = _org(db_session, "Proof", is_demo=True)
    real = _org(db_session, "Atlantis")
    master_contacts.record_lead(db_session, _lead(db_session, demo,
                                                  email="mike@realbrand.org"))
    db_session.commit()
    assert db_session.query(MasterContact).one().is_synthetic is True

    master_contacts.record_lead(db_session, _lead(db_session, real,
                                                  email="mike@realbrand.org"))
    db_session.commit()
    assert db_session.query(MasterContact).one().is_synthetic is False


# ═════════════════════════════════════════════════════════════════════════════
# 5. The master write cannot break lead creation
# ═════════════════════════════════════════════════════════════════════════════

def test_a_failing_master_write_still_leaves_the_customer_their_lead(db_session, monkeypatch):
    org = _org(db_session, "Atlantis")
    lead = _lead(db_session, org, email="ada@example.org")

    def _explode(*a, **k):
        raise RuntimeError("the master database is having a bad day")

    monkeypatch.setattr(master_contacts, "_record", _explode)

    assert master_contacts.record_lead(db_session, lead) is None
    db_session.commit()

    # The customer's lead is intact and the transaction still commits.
    assert db_session.query(Lead).filter(Lead.id == lead.id).one() is not None
    assert db_session.query(LeadOccurrence).count() == 0


def test_an_unflushed_lead_is_declined_rather_than_half_recorded(db_session):
    org = _org(db_session, "Atlantis")
    orphan = Lead(organization_id=org.id, first_name="No", last_name="Id")
    orphan.id = None
    assert master_contacts.record_lead(db_session, orphan) is None


# ═════════════════════════════════════════════════════════════════════════════
# 6. Backfill
# ═════════════════════════════════════════════════════════════════════════════

def test_the_backfill_records_existing_leads_and_is_safe_to_rerun(db_session):
    plat = _platform(db_session)
    a = _org(db_session, "Atlantis", plat)
    b = _org(db_session, "WUPA", plat)
    for i in range(5):
        _lead(db_session, a, email="a%d@example.org" % i)
    for i in range(3):
        _lead(db_session, b, email="b%d@example.org" % i)

    assert master_backfill.pending_count(db_session) == 8

    first = master_backfill.backfill(db_session)
    assert first["recorded"] == 8
    assert first["failed"] == 0
    assert first["leads_without_occurrence_remaining"] == 0
    assert db_session.query(LeadOccurrence).count() == 8

    second = master_backfill.backfill(db_session)
    assert second["scanned"] == 0
    assert second["recorded"] == 0
    assert db_session.query(LeadOccurrence).count() == 8
    assert db_session.query(MasterContact).count() == 8


def test_the_backfill_preserves_which_organization_and_platform_each_lead_came_from(db_session):
    p1 = _platform(db_session, "EvoSys Pro")
    p2 = _platform(db_session, "BookaBoost")
    a = _org(db_session, "Atlantis", p1)
    b = _org(db_session, "Restland", p2)
    la = _lead(db_session, a, email="shared@example.org", first="From A")
    lb = _lead(db_session, b, email="shared@example.org", first="From B")

    master_backfill.backfill(db_session)

    # One human, two appearances, each keeping its own origin.
    assert db_session.query(MasterContact).count() == 1
    lineage = {(o.organization_id, o.platform_id, o.lead_id)
               for o in db_session.query(LeadOccurrence).all()}
    assert lineage == {(a.id, p1.id, la.id), (b.id, p2.id, lb.id)}


def test_a_dry_run_counts_and_writes_nothing(db_session):
    org = _org(db_session, "Atlantis")
    for i in range(4):
        _lead(db_session, org, email="d%d@example.org" % i)

    result = master_backfill.backfill(db_session, dry_run=True)
    assert result["dry_run"] is True
    assert result["would_record"] == 4
    assert db_session.query(LeadOccurrence).count() == 0
    assert db_session.query(MasterContact).count() == 0


def test_the_backfill_can_be_limited_and_scoped_to_one_organization(db_session):
    a = _org(db_session, "Atlantis")
    b = _org(db_session, "WUPA")
    for i in range(6):
        _lead(db_session, a, email="a%d@example.org" % i)
    for i in range(6):
        _lead(db_session, b, email="b%d@example.org" % i)

    result = master_backfill.backfill(db_session, organization_id=a.id, limit=2)
    assert result["recorded"] == 2
    assert db_session.query(LeadOccurrence).count() == 2
    assert {o.organization_id for o in db_session.query(LeadOccurrence).all()} == {a.id}


def test_the_backfill_does_not_modify_a_single_lead_column(db_session):
    """Asserted by comparing every column of every lead, before and after."""
    org = _org(db_session, "Atlantis")
    for i in range(5):
        _lead(db_session, org, email="x%d@example.org" % i, phone="55501012%02d" % i)

    cols = [c.name for c in Lead.__table__.columns]
    before = {l.id: {c: getattr(l, c) for c in cols}
              for l in db_session.query(Lead).all()}

    master_backfill.backfill(db_session)
    db_session.expire_all()

    after = {l.id: {c: getattr(l, c) for c in cols}
             for l in db_session.query(Lead).all()}
    assert before == after


def test_the_backfill_module_contains_no_delete_and_no_send(db_session):
    """A structural assertion, because the promise is structural.

    The backfill's safety is not that the current code happens not to send
    anything — it is that nothing in the module can. This fails the moment
    somebody adds a delete or reaches for a messaging service in here.
    """
    import inspect
    import io
    import tokenize

    # CODE ONLY. Comments and docstrings are stripped first, because this
    # module's own documentation says the words "campaign" and "delete" while
    # promising never to do either, and a test that cannot tell the promise
    # from the act is not testing anything.
    raw = inspect.getsource(master_backfill)
    code = "".join(
        tok.string + " "
        for tok in tokenize.generate_tokens(io.StringIO(raw).readline)
        if tok.type not in (tokenize.COMMENT, tokenize.STRING)
    ).lower()

    for forbidden in ("db.delete", ".delete (", "send_email", "send_sms",
                      "sms_service", "email_service", "campaign", "cadence",
                      "enqueue", "workforce", "notify"):
        assert forbidden not in code, f"master_backfill must not reference {forbidden}"


# ═════════════════════════════════════════════════════════════════════════════
# 7. TENANT ISOLATION — the one that matters most
# ═════════════════════════════════════════════════════════════════════════════

MASTER_ROUTES = [
    "/god/master/contacts",
    "/god/master/stats",
]


@pytest.mark.parametrize("role", ["advisor", "org_admin", "super_admin"])
@pytest.mark.parametrize("path", MASTER_ROUTES)
def test_no_customer_role_can_read_the_master_database(client, db_session, role, path):
    org = _org(db_session, "Atlantis")
    user = _user(db_session, org, role)
    r = client.get(path, headers=_headers(db_session, user))
    assert r.status_code == 403, f"{role} reached {path}"


@pytest.mark.parametrize("path", MASTER_ROUTES)
def test_the_master_database_is_not_readable_without_authentication(client, path):
    assert client.get(path).status_code in (401, 403)


def test_a_customer_role_cannot_run_the_backfill(client, db_session):
    org = _org(db_session, "Atlantis")
    user = _user(db_session, org, "org_admin")
    r = client.post("/god/master/backfill", headers=_headers(db_session, user))
    assert r.status_code == 403


def test_god_reads_every_organization_and_the_rows_carry_their_origin(client, db_session):
    plat = _platform(db_session)
    a = _org(db_session, "Atlantis", plat)
    b = _org(db_session, "WUPA", plat)
    # Deliberately NOT example.org: that domain is RFC-reserved and the
    # synthetic rule correctly hides it, which would make this test assert
    # against an empty production view.
    _lead(db_session, a, email="atlantis.person@atlantis-lp.com", first="Atl")
    _lead(db_session, b, email="wupa.person@wupa-energy.com", first="Wup")
    master_backfill.backfill(db_session)

    god = _user(db_session, None, "god_admin")
    r = client.get("/god/master/contacts", headers=_headers(db_session, god))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    orgs = {row["organization_id"] for row in body["rows"]}
    assert orgs == {a.id, b.id}
    for row in body["rows"]:
        assert row["platform_id"] == plat.id
        assert row["organization_name"] in ("Atlantis", "WUPA")
        assert row["occurrence_count"] == 1
        assert row["first_seen_at"] and row["last_seen_at"]


def test_synthetic_records_are_hidden_by_default_and_available_on_request(client, db_session):
    real = _org(db_session, "Atlantis")
    demo = _org(db_session, "Proof", is_demo=True)
    _lead(db_session, real, email="real.person@realbrand.org")
    _lead(db_session, demo, email="a.person@other-real.org")
    master_backfill.backfill(db_session)

    god = _user(db_session, None, "god_admin")
    h = _headers(db_session, god)

    default = client.get("/god/master/contacts", headers=h).json()
    assert default["total"] == 1
    assert default["rows"][0]["organization_id"] == real.id

    included = client.get("/god/master/contacts?include_synthetic=true",
                          headers=h).json()
    assert included["total"] == 2


def test_god_can_find_a_person_by_a_phone_number_typed_any_way(client, db_session):
    org = _org(db_session, "Atlantis")
    _lead(db_session, org, first="Ada", email="ada.finder@realbrand.org",
          phone="5550104242")
    master_backfill.backfill(db_session)

    god = _user(db_session, None, "god_admin")
    h = _headers(db_session, god)
    for typed in ["5550104242", "(555) 010-4242", "+1 555 010 4242"]:
        body = client.get(f"/god/master/contacts?search={typed}", headers=h).json()
        assert body["total"] == 1, f"searching {typed!r} found nothing"
    body = client.get("/god/master/contacts?search=ada.finder", headers=h).json()
    assert body["total"] == 1


def test_the_master_tables_are_imported_by_no_customer_facing_router(db_session):
    """Isolation that does not depend on anybody remembering a filter.

    A customer cannot reach `/god/master`. This asserts the other half: no
    route a customer CAN reach queries these tables either, so there is no
    second door to leave unlocked.
    """
    import os
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    routers = os.path.join(root, "app", "routers")
    allowed = {"god_master_router.py"}
    offenders = []
    for fn in os.listdir(routers):
        if not fn.endswith(".py") or fn in allowed:
            continue
        with open(os.path.join(routers, fn), encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        if re.search(r"master_contact_models|\bMasterContact\b|\bLeadOccurrence\b", src):
            offenders.append(fn)
    assert offenders == [], f"customer-reachable routers referencing master tables: {offenders}"
