"""RESTLAND RESET - DO-NOT-CONTACT PRESERVATION (Issue #507, Nov 15).

A family's do-not-contact must survive deleting its contact rows: the same
phone or email, imported again under a new record and in another format,
stays blocked at import, enrollment and sending. Synthetic data only."""
import uuid

import pytest

from app.models.models import ContactSuppression, Lead, Organization, Reply, SuppressionEntry
from app.services import contact_suppression as CS
from app.services.compliance_service import check_compliance_preflight
from app.services.programs import restland_reset as RR


@pytest.fixture()
def org(db_session):
    CS.register()
    o = Organization(id=RR.ORG_REQUIRED, name="Restland Cemetery and Funeral Home (test)", slug="restland-test")
    db_session.add(o)
    db_session.commit()
    return o


def _lead(db, org, **kw):
    base = dict(id=str(uuid.uuid4()), organization_id=org.id, first_name="Pat", last_name="Lee", status="new")
    base.update(kw)
    l = Lead(**base)
    db.add(l)
    db.flush()
    return l


def _seed(db, org):
    src = _lead(db, org, phone="2145550101", email="Source.Dnc@Example.com", status="dnc")      # source DNC
    parent = _lead(db, org, phone="2145550102", email="parent@example.com")
    dup = _lead(db, org, phone="(214) 555-0102", status="dnc", is_duplicate=True,
                duplicate_of_lead_id=parent.id)                                                   # bug-only
    pdnc = _lead(db, org, phone="2145550103", status="dnc")
    dup2 = _lead(db, org, phone="2145550104", email="dup2@example.com", status="dnc", is_duplicate=True,
                 duplicate_of_lead_id=pdnc.id)                                                    # parent DNC
    flag = _lead(db, org, phone="2145550105", manual_flag="remove_all")
    rep = _lead(db, org, phone="2145550106", email="reply@example.com", status="replied")
    db.add(Reply(lead_id=rep.id, body="Stop sending me this sales pitch."))
    bad = _lead(db, org, phone="2145550107", email="bad@example.com", manual_flag="bad_email")
    sup = _lead(db, org, phone="2145550108")
    db.add(SuppressionEntry(organization_id=org.id, phone="12145550108", reason="manual"))
    ok = _lead(db, org, phone="2145550199", email="fine@example.com")
    db.commit()
    return locals()


def test_classification_separates_real_blocks_from_duplicate_only(db_session, org):
    s = _seed(db_session, org)
    c = RR.classify(db_session, org.id)
    ids = lambda k: {l.id for l in c["cats"][k]}                        # noqa: E731
    assert s["src"].id in ids("source_dnc") and s["dup2"].id in ids("parent_dnc")
    assert s["flag"].id in ids("advisor_remove_all") and s["rep"].id in ids("opt_out_reply")
    assert s["sup"].id in ids("suppressed_phone") and s["bad"].id in ids("bad_email")
    assert {l.id for l in c["dup_only"]} == {s["dup"].id}


def test_preserve_moves_every_real_block_onto_its_destinations(db_session, org):
    _seed(db_session, org)
    out = RR.preserve(db_session, org)
    assert out["duplicate_only_flags"] == 1
    vals = {(r.kind, r.value, r.scope) for r in db_session.query(ContactSuppression).all()}
    assert ("email", "source.dnc@example.com", "family") in vals
    assert ("phone", "12145550101", "family") in vals and ("phone", "12145550104", "family") in vals
    assert ("email", "bad@example.com", "email") in vals
    assert ("phone", "12145550102", "family") not in vals               # duplicate-only stays open
    assert ("phone", "12145550199", "family") not in vals               # a normal family stays open
    again = RR.preserve(db_session, org)
    assert again["added_this_run"] == {}                                # idempotent


def test_blocks_survive_deletion_and_reimport_in_other_formats(db_session, org):
    s = _seed(db_session, org)
    RR.preserve(db_session, org)
    for l in db_session.query(Lead).filter_by(organization_id=org.id).all():
        db_session.query(Reply).filter_by(lead_id=l.id).delete()
        db_session.delete(l)
    db_session.commit()
    # import: brand-new records, other formats, other letter case
    a = _lead(db_session, org, phone="+1 (214) 555-0101", email=None)
    b = _lead(db_session, org, phone=None, email="  SOURCE.DNC@EXAMPLE.COM ")
    c = _lead(db_session, org, phone="214.555.0105", email="new.address@example.com")   # family scope via phone
    d = _lead(db_session, org, phone="2145550107", email="BAD@example.com")              # email-only scope
    e = _lead(db_session, org, phone="214-555-0108")                                    # suppressed phone
    f = _lead(db_session, org, phone="2145550102", email="parent@example.com")           # duplicate-only: open
    db_session.commit()
    assert a.status == "dnc" and b.status == "dnc" and c.status == "dnc"
    assert d.status == "new" and d.allow_email is False
    assert e.allow_sms is False
    assert f.status == "new" and f.allow_email is not False
    # dispatch: even with the status cleared, the destination refuses
    for l in (a, b, c):
        l.status = "new"
        for ch in ("sms", "email"):
            if ch == "sms" and not l.phone:
                continue
            with pytest.raises(ValueError):
                check_compliance_preflight(db_session, l, channel=ch)
    with pytest.raises(ValueError):
        d.allow_email = None
        check_compliance_preflight(db_session, d, channel="email")
    check_compliance_preflight(db_session, f, channel="email")           # still allowed


def test_enrollment_refuses_a_blocked_family(db_session, org):
    _seed(db_session, org)
    RR.preserve(db_session, org)
    from app.services.programs import send_approval
    n = Lead(id=str(uuid.uuid4()), organization_id=org.id, first_name="New", phone="2145550101", status="new")
    assert send_approval.blocker(db_session, n) == "Do not contact"


def test_verify_reports_no_failures_and_writes_nothing(db_session, org):
    _seed(db_session, org)
    RR.preserve(db_session, org)
    n = db_session.query(Lead).count()
    v = RR.verify(db_session, org)
    assert v["ok"] is True and v["checked"]["new_record"] > 0
    assert db_session.query(Lead).count() == n


def test_other_organizations_are_refused(db_session):
    other = Organization(id=str(uuid.uuid4()), name="Other", slug="other-" + str(uuid.uuid4())[:8])
    db_session.add(other)
    db_session.commit()
    with pytest.raises(RR.ResetRefused):
        RR.preserve(db_session, other)


def test_blocks_are_scoped_to_their_organization(db_session, org):
    _seed(db_session, org)
    RR.preserve(db_session, org)
    other = Organization(id=str(uuid.uuid4()), name="Other", slug="other-" + str(uuid.uuid4())[:8])
    db_session.add(other)
    db_session.commit()
    l = _lead(db_session, other, phone="2145550101", email="source.dnc@example.com")
    db_session.commit()
    assert l.status == "new"
