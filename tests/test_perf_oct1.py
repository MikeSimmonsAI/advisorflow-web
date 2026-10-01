# -*- coding: utf-8 -*-
"""S18 performance pass (2026-10-01) - hot endpoints.

Each test pins the OUTPUT against the pre-change algorithm (re-implemented
here as a reference) and bounds the SQL statement count so it does not grow
with the number of rows / organizations.

  * inbound mailbox routing: candidate_org_ids no longer resolves the full
    sending identity of every org for every mailbox every five minutes;
  * GET /god/email/inbound-mailboxes resolves the org address map once;
  * GET /intake/contacts/summary: one aggregate pass instead of ten COUNTs;
  * GET /energy-ops/queues: renewal window computed once, column-only.
"""
import itertools
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app.models.inbound_mailbox_models import InboundMailbox
from app.models.intake_models import OrgContact
from app.models.models import Lead, Organization, Platform, User
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


@contextmanager
def count_queries(db):
    engine = db.get_bind()
    seen = []

    def _c(conn, cursor, statement, params, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", _c)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", _c)


def _org(db, name=None, **kw):
    kw.setdefault("is_active", True)
    o = Organization(name=name or "Perf %d" % next(_SEQ), slug="pf-%s" % uuid.uuid4().hex[:8],
                     plan="enterprise", industry=kw.pop("industry", "energy"), **kw)
    db.add(o)
    db.commit()
    return o


def _user(db, org, role, label="u"):
    u = User(organization_id=org.id if org else None,
             email="%s-%d@perf.test" % (label, next(_SEQ)),
             password_hash=hash_password("Pass12345!"), full_name=label.title(),
             role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


def ok(r):
    assert r.status_code == 200, "%s %s" % (r.status_code, r.text[:400])
    return r.json()


# ── inbound mailbox routing ────────────────────────────────────────────────

def _old_candidate_org_ids(db, box):
    """The pre-S18 algorithm, verbatim in behaviour."""
    from app.services.public_identity import sending_identity_for_org
    out = []
    if box.organization_id:
        out.append(box.organization_id)
    addr = box.address.lower()
    for (oid,) in db.query(Organization.id).filter(Organization.is_active.isnot(False)).all():
        if oid in out:
            continue
        try:
            ident = sending_identity_for_org(db, oid)
        except Exception:  # noqa: BLE001
            continue
        for v in (getattr(ident, "from_email", None), getattr(ident, "reply_to_email", None)):
            if v and v.strip().lower() == addr:
                out.append(oid)
                break
    return out


def _routing_world(db, extra_orgs=0):
    p_mail = Platform(name="Brand A", slug="perf-brand-a-%s" % uuid.uuid4().hex[:4],
                      support_email="Support@BrandA.test")
    p_none = Platform(name="Brand B", slug="perf-brand-b-%s" % uuid.uuid4().hex[:4])
    p_reg = Platform(name="EvoSys Pro", slug="evosyspro")   # registry / brand-config fallback
    db.add_all([p_mail, p_none, p_reg])
    db.commit()
    orgs = [
        _org(db, "own from", from_email="support@branda.test", platform_id=p_none.id),
        _org(db, "inherits platform", platform_id=p_mail.id),
        _org(db, "reply-to only", reply_to_email=" SUPPORT@branda.test ", platform_id=p_none.id),
        _org(db, "no platform"),
        _org(db, "other address", from_email="hello@elsewhere.test", platform_id=p_mail.id),
        _org(db, "inactive", is_active=False, from_email="support@branda.test"),
        _org(db, "registry", platform_id=p_reg.id),
        _org(db, "blank from", from_email="", platform_id=p_mail.id),
    ]
    for i in range(extra_orgs):
        orgs.append(_org(db, "extra %d" % i, platform_id=(p_mail, p_none, p_reg)[i % 3].id))
    return orgs


@pytest.mark.parametrize("address,owner_idx", [
    ("support@branda.test", None), ("SUPPORT@BRANDA.TEST", 3), ("hello@elsewhere.test", None),
    ("nobody@nowhere.test", 0)])
def test_candidate_org_ids_matches_old_algorithm(db_session, address, owner_idx):
    from app.services.inbound_mailbox_service import candidate_org_ids, org_sending_addresses
    orgs = _routing_world(db_session)
    box = InboundMailbox(address=address, is_active=True,
                         organization_id=orgs[owner_idx].id if owner_idx is not None else None)
    db_session.add(box)
    db_session.commit()
    expected = _old_candidate_org_ids(db_session, box)
    assert candidate_org_ids(db_session, box) == expected
    assert candidate_org_ids(db_session, box, org_sending_addresses(db_session)) == expected
    if address == "support@branda.test":
        names = {o.id: o.name for o in orgs}
        assert {names[i] for i in expected} == {"own from", "inherits platform", "reply-to only",
                                                "blank from"}


def test_org_sending_addresses_match_sending_identity_for_every_org(db_session):
    from app.services.inbound_mailbox_service import org_sending_addresses
    from app.services.public_identity import sending_identity_for_org
    _routing_world(db_session, extra_orgs=6)
    got = org_sending_addresses(db_session)
    active = [o for o in db_session.query(Organization).all() if o.is_active is not False]
    assert set(got) == {o.id for o in active}
    for o in active:
        ident = sending_identity_for_org(db_session, o.id)
        want = {v.strip().lower() for v in (ident.from_email, ident.reply_to_email) if v and v.strip()}
        assert got[o.id] == want, o.name


def test_candidate_org_ids_query_count_does_not_grow_with_orgs(db_session):
    from app.services.inbound_mailbox_service import candidate_org_ids
    _routing_world(db_session)
    box = InboundMailbox(address="support@branda.test", is_active=True)
    db_session.add(box)
    db_session.commit()
    with count_queries(db_session) as small:
        candidate_org_ids(db_session, box)
    p = db_session.query(Platform).filter(Platform.name == "Brand A").first()
    for i in range(25):
        _org(db_session, "more %d" % i, platform_id=p.id)
    with count_queries(db_session) as big:
        candidate_org_ids(db_session, box)
    assert len(big) == len(small)
    # Old algorithm: several statements per org. New: one org read plus one
    # identity resolution per distinct platform.
    assert len(big) <= 20


def test_poll_all_resolves_org_addresses_once_per_run(db_session, monkeypatch):
    from app.services import inbound_mailbox_service as S
    _routing_world(db_session)
    db_session.add_all([InboundMailbox(address="support@branda.test", is_active=True),
                        InboundMailbox(address="hello@elsewhere.test", is_active=True),
                        InboundMailbox(address="off@x.test", is_active=False)])
    db_session.commit()
    calls, seen = [], []
    real = S.org_sending_addresses
    monkeypatch.setattr(S, "org_sending_addresses", lambda db: calls.append(1) or real(db))

    def fake_poll(db, box, *, fetch=None, now=None, addresses=None):   # never reaches Graph
        seen.append((box.address, S.candidate_org_ids(db, box, addresses)))
        return {"mailbox": box.address, "checked": 0, "matched": 0, "errors": 0}

    monkeypatch.setattr(S, "poll_mailbox", fake_poll)
    out = S.poll_all_mailboxes(db_session)
    assert out["mailboxes_polled"] == 2 and len(calls) == 1
    for address, ids in seen:
        box = db_session.query(InboundMailbox).filter(InboundMailbox.address == address).one()
        assert ids == _old_candidate_org_ids(db_session, box)


def test_god_inbound_mailboxes_same_routes_bounded_queries(client, db_session):
    orgs = _routing_world(db_session)
    god = _user(db_session, None, "god_admin", "god")
    db_session.add_all([InboundMailbox(address="support@branda.test", is_active=True,
                                       connected_at=datetime.utcnow()),
                        InboundMailbox(address="hello@elsewhere.test", is_active=True,
                                       connected_at=datetime.utcnow() - timedelta(hours=1))])
    db_session.commit()
    h = _h(db_session, god)
    with count_queries(db_session) as small:
        body = ok(client.get("/god/email/inbound-mailboxes", headers=h))
    names = {o.id: o.name for o in orgs}
    by_addr = {b["address"]: b for b in body["mailboxes"]}
    for b in db_session.query(InboundMailbox).all():
        want = _old_candidate_org_ids(db_session, b)
        assert [r["id"] for r in by_addr[b.address]["routes_to"]] == want
        assert [r["name"] for r in by_addr[b.address]["routes_to"]] == [names[i] for i in want]
    p = db_session.query(Platform).filter(Platform.name == "Brand A").first()
    for i in range(20):
        _org(db_session, "more %d" % i, platform_id=p.id)
    with count_queries(db_session) as big:
        ok(client.get("/god/email/inbound-mailboxes", headers=h))
    assert len(big) == len(small)


# ── /intake/contacts/summary ───────────────────────────────────────────────

def _old_contacts_summary_counts(db, org_id):
    from app.routers.intake_router import _has_text, _is_mobile, _phone_present
    from app.models.intake_models import ContactLifecycle
    base = db.query(OrgContact).filter(OrgContact.organization_id == org_id,
                                       OrgContact.archived_at.is_(None))
    return {
        "contacts": base.count(),
        "sms_ready": base.filter(OrgContact.sms_status == "ready").count(),
        "email_ready": base.filter(OrgContact.email_status == "ready").count(),
        "needs_enrichment": base.filter(OrgContact.lifecycle == ContactLifecycle.NEEDS_ENRICHMENT).count(),
        "historical_customers": base.filter(OrgContact.historical_customer.is_(True)).count(),
        "with_email": base.filter(_has_text(OrgContact.email)).count(),
        "with_phone": base.filter(_phone_present()).count(),
        "valid_phones": base.filter(_phone_present(), (OrgContact.sms_status.is_(None))
                                    | (OrgContact.sms_status != "invalid")).count(),
        "mobile": base.filter(_is_mobile()).count(),
        "promoted": base.filter(OrgContact.lead_id.isnot(None)).count(),
    }


def _seed_contacts(db, org, n, start=0):
    rows = []
    for i in range(start, start + n):
        rows.append(OrgContact(
            organization_id=org.id, first_name="C%d" % i, last_name="Z%d" % i,
            email=("c%d@x.test" % i) if i % 3 == 0 else ("" if i % 3 == 1 else None),
            phone=("+1972555%04d" % i) if i % 4 else ("" if i % 8 == 0 else None),
            mobile_phone=("+1469555%04d" % i) if i % 5 == 0 else None,
            phone_line_type=("mobile" if i % 6 == 0 else ("landline" if i % 6 == 1 else None)),
            record_class=("customer", "previous_customer", "renewal", "lead", "contact")[i % 5],
            lifecycle=("active", "needs_enrichment")[i % 2],
            sms_status=(None, "ready", "invalid")[i % 3],
            email_status=(None, "ready")[i % 2],
            historical_customer=(None, True, False)[i % 3],
            lead_id=("lead-%d" % i) if i % 7 == 0 else None,
            archived_at=datetime.utcnow() if i % 11 == 0 else None))
    db.add_all(rows)
    db.commit()


def test_contacts_summary_same_numbers_one_pass(client, db_session):
    org = _org(db_session)
    other = _org(db_session)
    admin = _user(db_session, org, "org_admin", "admin")
    _seed_contacts(db_session, org, 60)
    _seed_contacts(db_session, other, 15)          # another tenant: never counted
    h = _h(db_session, admin)
    with count_queries(db_session) as small:
        body = ok(client.get("/intake/contacts/summary", headers=h))
    want = _old_contacts_summary_counts(db_session, org.id)
    for k, v in want.items():
        assert body[k] == v, k
    assert want["contacts"] == 60 - len([i for i in range(60) if i % 11 == 0])
    assert body["customers"] == body["by_record_class"].get("customer", 0)
    _seed_contacts(db_session, org, 200, start=60)
    with count_queries(db_session) as big:
        body2 = ok(client.get("/intake/contacts/summary", headers=h))
    assert len(big) == len(small)
    for k, v in _old_contacts_summary_counts(db_session, org.id).items():
        assert body2[k] == v, k


def test_contacts_summary_empty_org_is_zero(client, db_session):
    org = _org(db_session)
    admin = _user(db_session, org, "org_admin", "admin")
    body = ok(client.get("/intake/contacts/summary", headers=_h(db_session, admin)))
    for k in ("contacts", "sms_ready", "email_ready", "needs_enrichment", "historical_customers",
              "with_email", "with_phone", "valid_phones", "mobile", "promoted"):
        assert body[k] == 0, k


# ── /energy-ops/queues ─────────────────────────────────────────────────────

@pytest.fixture()
def _energy_mounted():
    from app.main import app
    from app.routers.energy_ops_router import router as ops
    if "/energy-ops/queues" not in {getattr(r, "path", "") for r in app.routes}:
        app.include_router(ops)
    yield


def _seed_renewals(db, org, owner, n, start=0):
    today = datetime.utcnow().date()
    for i in range(start, start + n):
        cf = None
        if i % 4 == 1:
            cf = json.dumps({"contract_end_date": (today + timedelta(days=(i * 13) % 300 - 60)).isoformat()})
        elif i % 4 == 2:
            cf = json.dumps({"contract_end_date": "not a date"})
        elif i % 4 == 3:
            cf = json.dumps({"current_supplier": "X"})
        db.add(Lead(organization_id=org.id, assigned_to_id=owner.id, first_name="R%d" % i,
                    last_name="N", phone="+1214777%04d" % i,
                    tier=("contract_signed", "renewal_due", "new_inquiry")[i % 3],
                    status=(None, "new", "dnc", "dead")[i % 4] if i % 5 else "new",
                    custom_fields=cf))
    db.commit()


def test_energy_queues_renewal_counts_unchanged_and_bounded(client, db_session, _energy_mounted,
                                                            monkeypatch):
    from app.routers import energy_ops_router as E
    org = _org(db_session)
    admin = _user(db_session, org, "org_admin", "admin")
    adv = _user(db_session, org, "advisor", "adv")
    _seed_renewals(db_session, org, adv, 40)
    h = _h(db_session, admin)
    with count_queries(db_session) as small:
        body = ok(client.get("/energy-ops/queues", headers=h))
    inside, missing = E._renewal_rows(db_session, admin, None, E._now())
    q = {x["key"]: x for x in body["queues"]}
    assert q["renewal_window"]["count"] == len(inside)
    assert body["renewal_date_missing"] == missing
    assert len(inside) > 0 and missing > 0

    # The summary no longer builds the full renewal rows at all.
    called = []
    real = E._renewal_rows
    monkeypatch.setattr(E, "_renewal_rows", lambda *a, **k: called.append(1) or real(*a, **k))
    _seed_renewals(db_session, org, adv, 80, start=40)
    with count_queries(db_session) as big:
        body2 = ok(client.get("/energy-ops/queues", headers=h))
    assert called == []
    assert len(big) == len(small)
    inside2, missing2 = real(db_session, admin, None, E._now())
    assert {x["key"]: x for x in body2["queues"]}["renewal_window"]["count"] == len(inside2)
    assert body2["renewal_date_missing"] == missing2
    # the queue page still agrees with the summary count
    page = ok(client.get("/energy-ops/queues/renewal_window?per_page=100", headers=h))
    assert page["total"] == len(inside2)
