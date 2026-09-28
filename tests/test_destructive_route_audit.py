"""Destructive routes: correct target, workspace-aware authority, audit trail.

A. POST /availability/block/date-range?advisor_id=... with cancel_existing
   cancelled the CALLER's bookings instead of the target advisor's. It now
   cancels the target's, leaves the admin's alone, and audits the ids.
B. Additive audit entries on destructive routes, written against the org the
   rows belong to.
C. Lead delete / duplicate bulk-delete audit against the rows' org, not the
   caller's home org.
D. Lead delete / duplicate bulk-delete / DELETE /leads/import-batches decide
   admin authority in the SELECTED workspace (is_manager_here), not users.role.
E. DELETE /leads/import-batches tolerates a missing optional table without
   undoing the child deletes that had already succeeded.

No SMS is ever sent: conftest refuses to construct a Twilio client, and the
availability tests additionally fail if sms_service.send_sms is reached.
"""
import itertools
import json
import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import text

from app.models.models import (AuditLogEntry, BookingLink, CadenceTemplate,
                               CadenceTemplateTouch, CRMContact, Lead, LeadStatus,
                               LeadTier, MessageTrack, Organization, Platform,
                               TierDefinition, User)
from app.models.import_models import ImportBatch, ImportBatchStatus, ImportStagedRow
from app.models.sales_models import Membership
from app.services.auth_service import create_access_token, hash_password
from app.services.workspace_access import SCOPE_CUSTOMER_ORG, WORKSPACE_HEADER

_SEQ = itertools.count(1)


# ── builders (same shape as test_security_scope_fixes) ──────────────────────

def _platform(db, name):
    p = Platform(name=name, slug="brand-%s" % uuid.uuid4().hex[:8], short_name=name[:2],
                 tagline="t", support_email="support@%s.test" % uuid.uuid4().hex[:6])
    db.add(p)
    db.commit()
    return p


def _org(db, name, platform=None, industry="energy"):
    o = Organization(name=name, slug="o-%s" % uuid.uuid4().hex[:8], plan="standard",
                     industry=industry, is_active=True,
                     platform_id=(platform.id if platform else None))
    db.add(o)
    db.commit()
    return o


def _user(db, role, org=None, platform=None, label="u"):
    u = User(organization_id=(org.id if org else None),
             platform_id=(platform.id if platform else None),
             email="%s-%d-%s@test.local" % (label, next(_SEQ), uuid.uuid4().hex[:6]),
             password_hash=hash_password("TestPass123!"), full_name=label.title(),
             role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _member(db, user, org, role):
    m = Membership(user_id=user.id, scope_type=SCOPE_CUSTOMER_ORG,
                   scope_id=org.id, role=role, is_active=True)
    db.add(m)
    db.commit()
    return m


def _h(db, user, workspace=None):
    h = {"Authorization": "Bearer " + create_access_token(user, db)}
    if workspace is not None:
        h[WORKSPACE_HEADER] = workspace.id
    return h


def _lead(db, org, owner, **kw):
    lead = Lead(organization_id=org.id, assigned_to_id=owner.id,
                first_name=kw.pop("first_name", "Pat"), last_name="Family",
                phone=kw.pop("phone", "1214555%04d" % next(_SEQ)),
                email="lead-%s@example.com" % uuid.uuid4().hex[:6],
                tier=LeadTier.PRE_NEED, message_track=MessageTrack.PRE_NEED_LOCK_PRICE,
                status=LeadStatus.NEW, **kw)
    db.add(lead)
    db.commit()
    return lead


def _audit(db, action):
    db.expire_all()
    return db.query(AuditLogEntry).filter(AuditLogEntry.action == action).all()


@pytest.fixture()
def one_org(db_session):
    plat = _platform(db_session, "Solo Brand")
    org = _org(db_session, "Solo Org", plat)
    admin = _user(db_session, "org_admin", org=org, label="admin")
    advisor = _user(db_session, "advisor", org=org, label="advisor")
    return {"org": org, "admin": admin, "advisor": advisor, "platform": plat}


@pytest.fixture()
def split_person(db_session):
    """org_admin of A (home), ordinary advisor of B; plus a real admin of B."""
    plat = _platform(db_session, "Shared Brand")
    a = _org(db_session, "Workspace A", plat)
    b = _org(db_session, "Workspace B", plat)
    person = _user(db_session, "org_admin", org=a, label="split")
    _member(db_session, person, a, "org_admin")
    _member(db_session, person, b, "advisor")
    b_admin = _user(db_session, "org_admin", org=b, label="badmin")
    _member(db_session, b_admin, b, "org_admin")
    b_advisor = _user(db_session, "advisor", org=b, label="badvisor")
    _member(db_session, b_advisor, b, "advisor")
    return {"a": a, "b": b, "person": person, "b_admin": b_admin, "b_advisor": b_advisor}


# ════════════════════════════════════════════════════════════════════════════
# A. availability block/date-range cancels the TARGET advisor's bookings
# ════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def no_sms(monkeypatch):
    """Belt and braces on top of conftest's Twilio refusal."""
    from app.services import sms_service
    calls = []

    def _refuse(*a, **k):
        calls.append((a, k))
        raise AssertionError("block/date-range must not send SMS in tests")

    monkeypatch.setattr(sms_service, "send_sms", _refuse)
    return calls


def _booking(db, org, user, when):
    lead = _lead(db, org, user)
    b = BookingLink(lead_id=lead.id, user_id=user.id, status="booked", booked_time=when)
    db.add(b)
    db.commit()
    return b


def test_admin_block_cancels_target_advisors_bookings_not_admins(client, db_session,
                                                                 one_org, no_sms):
    org, admin, advisor = one_org["org"], one_org["admin"], one_org["advisor"]
    day = date.today() + timedelta(days=5)
    at = datetime(day.year, day.month, day.day, 10, 0)
    adv_in = _booking(db_session, org, advisor, at)
    adv_out = _booking(db_session, org, advisor, at + timedelta(days=10))
    admin_in = _booking(db_session, org, admin, at)

    r = client.post("/availability/block/date-range", params={"advisor_id": advisor.id},
                    headers=_h(db_session, admin),
                    json={"start_date": day.isoformat(), "end_date": day.isoformat(),
                          "reason": "vacation", "cancel_existing": True})
    assert r.status_code == 200, r.text
    assert r.json()["cancelled_bookings"] == 1
    assert set(r.json()) == {"block_id", "cancelled_bookings"}

    db_session.expire_all()
    assert db_session.get(BookingLink, adv_in.id).status == "cancelled"
    assert db_session.get(BookingLink, adv_out.id).status == "booked", "outside range"
    assert db_session.get(BookingLink, admin_in.id).status == "booked", \
        "the admin's own booking must be untouched"
    assert no_sms == []

    entries = _audit(db_session, "availability.bookings_cancelled")
    assert len(entries) == 1
    e = entries[0]
    assert e.organization_id == org.id and e.actor_user_id == admin.id
    assert e.target_id == advisor.id
    assert json.loads(e.details)["cancelled_booking_ids"] == [adv_in.id]


def test_block_without_cancel_writes_no_cancellation_audit(client, db_session, one_org):
    org, advisor = one_org["org"], one_org["advisor"]
    day = date.today() + timedelta(days=3)
    b = _booking(db_session, org, advisor, datetime(day.year, day.month, day.day, 9, 0))
    r = client.post("/availability/block/date-range", headers=_h(db_session, advisor),
                    json={"start_date": day.isoformat(), "end_date": day.isoformat()})
    assert r.status_code == 200, r.text
    assert r.json()["cancelled_bookings"] == 0
    db_session.expire_all()
    assert db_session.get(BookingLink, b.id).status == "booked"
    assert _audit(db_session, "availability.bookings_cancelled") == []


def test_advisor_self_block_cancels_own_bookings(client, db_session, one_org, no_sms):
    org, advisor = one_org["org"], one_org["advisor"]
    day = date.today() + timedelta(days=4)
    b = _booking(db_session, org, advisor, datetime(day.year, day.month, day.day, 11, 0))
    r = client.post("/availability/block/date-range", headers=_h(db_session, advisor),
                    json={"start_date": day.isoformat(), "end_date": day.isoformat(),
                          "cancel_existing": True})
    assert r.status_code == 200, r.text
    assert r.json()["cancelled_bookings"] == 1
    db_session.expire_all()
    assert db_session.get(BookingLink, b.id).status == "cancelled"


def test_workspace_admin_cannot_target_home_org_colleague(client, db_session, no_sms):
    """Admin of B, only an advisor at home in A, standing in B: advisor_id of an
    A colleague must not resolve (the target is looked up in the workspace the
    manager check was made in), so the colleague's bookings stay booked."""
    plat = _platform(db_session, "Target Brand")
    a = _org(db_session, "Home A", plat)
    b = _org(db_session, "Admin B", plat)
    person = _user(db_session, "advisor", org=a, label="advhome")
    _member(db_session, person, a, "advisor")
    _member(db_session, person, b, "org_admin")
    colleague = _user(db_session, "advisor", org=a, label="colleague")
    day = date.today() + timedelta(days=6)
    theirs = _booking(db_session, a, colleague, datetime(day.year, day.month, day.day, 10, 0))

    r = client.post("/availability/block/date-range", params={"advisor_id": colleague.id},
                    headers=_h(db_session, person, b),
                    json={"start_date": day.isoformat(), "end_date": day.isoformat(),
                          "cancel_existing": True})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.get(BookingLink, theirs.id).status == "booked"


# ════════════════════════════════════════════════════════════════════════════
# B. additive audit entries
# ════════════════════════════════════════════════════════════════════════════

def test_tier_reset_defaults_is_audited(client, db_session, one_org):
    from app.services.tier_config_service import seed_default_tier_definitions
    org, admin = one_org["org"], one_org["admin"]
    seed_default_tier_definitions(db_session, org.id, industry="energy")
    db_session.commit()
    before = db_session.query(TierDefinition).filter(
        TierDefinition.organization_id == org.id).count()
    r = client.post("/tier-definitions/reset-defaults", headers=_h(db_session, admin),
                    params={"industry": "funeral"})
    assert r.status_code == 200, r.text
    (e,) = _audit(db_session, "tier_definitions.reset_defaults")
    assert e.organization_id == org.id and e.actor_user_id == admin.id
    assert json.loads(e.before_state) == {"count": before}
    assert json.loads(e.after_state) == {"count": r.json()["reset"]}


def test_demo_wipe_is_audited_against_target_org(client, db_session):
    from app.services.data_cleanup import SAMPLE_TAG
    plat = _platform(db_session, "Wipe Brand")
    home = _org(db_session, "Operator Home", plat)
    target = _org(db_session, "Demo Customer", plat)
    sup = _user(db_session, "super_admin", org=home, platform=plat, label="sup")
    owner = _user(db_session, "advisor", org=target, label="owner")
    _lead(db_session, target, owner, source_file=SAMPLE_TAG)
    real = _lead(db_session, target, owner)

    r = client.delete("/admin/demo/wipe/%s" % target.id, headers=_h(db_session, sup))
    assert r.status_code == 200, r.text
    assert r.json()["leads_deleted"] == 1
    (e,) = _audit(db_session, "demo.wipe")
    assert e.organization_id == target.id, "the wiped org, not the operator's home"
    assert json.loads(e.details) == {"leads_deleted": 1, "demo_advisors_deleted": 0}
    assert db_session.get(Lead, real.id) is not None


def test_crm_contact_delete_is_audited_with_snapshot(client, db_session, one_org):
    # crm_contact_notes is raw-SQL (auto_migrate), unknown to create_all().
    db_session.execute(text(
        "CREATE TABLE IF NOT EXISTS crm_contact_notes (id VARCHAR PRIMARY KEY, "
        "contact_id VARCHAR NOT NULL, created_by_id VARCHAR, content TEXT NOT NULL, "
        "created_at TIMESTAMP)"))
    db_session.commit()
    org, admin = one_org["org"], one_org["admin"]
    c = CRMContact(organization_id=org.id, first_name="Ada", last_name="Lovelace",
                   email="ada@example.com", phone="2145550100")
    db_session.add(c)
    db_session.commit()
    cid = c.id
    r = client.delete("/crm/contacts/%s" % cid, headers=_h(db_session, admin))
    assert r.status_code == 204, r.text
    (e,) = _audit(db_session, "crm_contact.deleted")
    assert e.organization_id == org.id and e.target_id == cid
    snap = json.loads(e.before_state)
    assert snap["email"] == "ada@example.com" and snap["first_name"] == "Ada"
    assert snap["phone"] == "2145550100"
    db_session.expire_all()
    assert db_session.get(CRMContact, cid) is None


def test_import_batch_delete_is_audited_with_row_count(client, db_session, one_org):
    org, admin = one_org["org"], one_org["admin"]
    b = ImportBatch(organization_id=org.id, source_type="csv",
                    source_filename="families.csv", status=ImportBatchStatus.READY_FOR_REVIEW)
    db_session.add(b)
    db_session.commit()
    for n in range(3):
        db_session.add(ImportStagedRow(batch_id=b.id, organization_id=org.id, row_number=n + 1))
    db_session.commit()
    r = client.delete("/import-batches/%s" % b.id, headers=_h(db_session, admin))
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": True, "id": b.id}
    (e,) = _audit(db_session, "import_batch.deleted")
    assert e.organization_id == org.id and e.target_id == b.id
    assert json.loads(e.details) == {"staged_rows_deleted": 3}
    assert json.loads(e.before_state)["source_filename"] == "families.csv"


def test_cadence_touch_replacement_is_audited(client, db_session, one_org):
    org, admin = one_org["org"], one_org["admin"]
    t = CadenceTemplate(organization_id=org.id, name="Seven Touch")
    db_session.add(t)
    db_session.commit()
    old = []
    for n in (1, 2):
        tt = CadenceTemplateTouch(template_id=t.id, touch_number=n, day_offset=n)
        db_session.add(tt)
        old.append(tt)
    db_session.commit()
    old_ids = [x.id for x in old]

    r = client.patch("/cadence-templates/%s" % t.id, headers=_h(db_session, admin),
                     json={"touches": [{"touch_number": 1, "day_offset": 0}]})
    assert r.status_code == 200, r.text
    (e,) = _audit(db_session, "cadence_template.touches_replaced")
    assert e.organization_id == org.id and e.target_id == t.id
    assert json.loads(e.before_state) == {"touch_count": 2, "touch_ids": old_ids}
    assert json.loads(e.after_state) == {"touch_count": 1}

    # A rename without touches writes no touches entry.
    r = client.patch("/cadence-templates/%s" % t.id, headers=_h(db_session, admin),
                     json={"name": "Renamed"})
    assert r.status_code == 200, r.text
    assert len(_audit(db_session, "cadence_template.touches_replaced")) == 1


@pytest.fixture()
def crm_table(db_session):
    """crm_connections is created by raw SQL in auto_migrate, not the ORM."""
    db_session.execute(text("""
        CREATE TABLE IF NOT EXISTS crm_connections (
            id VARCHAR PRIMARY KEY, organization_id VARCHAR NOT NULL,
            name VARCHAR NOT NULL, crm_type VARCHAR NOT NULL DEFAULT 'webhook',
            webhook_url VARCHAR, webhook_secret VARCHAR, api_key_encrypted VARCHAR,
            api_base_url VARCHAR, sync_mode VARCHAR DEFAULT 'push_only',
            push_events TEXT DEFAULT '[]', annotation_tag VARCHAR, field_mapping TEXT,
            active BOOLEAN DEFAULT 1, last_synced_at TIMESTAMP, created_at TIMESTAMP)
    """))
    db_session.commit()


def test_crm_connection_delete_is_audited_without_secrets(client, db_session, one_org,
                                                          crm_table):
    org, admin = one_org["org"], one_org["admin"]
    cid = str(uuid.uuid4())
    db_session.execute(text(
        "INSERT INTO crm_connections (id, organization_id, name, crm_type, webhook_secret, "
        "api_key_encrypted) VALUES (:id, :org, 'GHL', 'gohighlevel', 'hook-s3cret', 'k3y')"),
        {"id": cid, "org": org.id})
    db_session.commit()
    r = client.delete("/crm/connections/%s" % cid, headers=_h(db_session, admin))
    assert r.status_code == 200, r.text
    assert r.json() == {"message": "Deleted"}
    (e,) = _audit(db_session, "crm_connection.deleted")
    assert e.organization_id == org.id and e.target_id == cid
    blob = (e.before_state or "") + (e.details or "")
    assert "hook-s3cret" not in blob and "k3y" not in blob
    assert json.loads(e.before_state)["crm_type"] == "gohighlevel"


def test_crm_native_stage_reset_is_audited(client, db_session, one_org):
    org, admin = one_org["org"], one_org["admin"]
    org.crm_stages = json.dumps([{"key": "x", "label": "X"}])
    db_session.commit()
    r = client.delete("/crm-native/stages/reset", headers=_h(db_session, admin))
    assert r.status_code == 200, r.text
    assert r.json()["reset"] is True
    (e,) = _audit(db_session, "crm_native.stages_reset")
    assert e.organization_id == org.id
    assert "\"x\"" in json.loads(e.before_state)["crm_stages"]


def test_settings_resets_are_audited_against_selected_workspace(client, db_session):
    plat = _platform(db_session, "Settings Brand")
    a = _org(db_session, "Home A", plat)
    b = _org(db_session, "Selected B", plat)
    person = _user(db_session, "org_admin", org=a, label="dbl")
    _member(db_session, person, a, "org_admin")
    _member(db_session, person, b, "org_admin")
    b.products = json.dumps([{"key": "only_b", "label": "Only B", "icon": ""}])
    b.appointment_types = json.dumps(["Only B Visit"])
    db_session.commit()

    h = _h(db_session, person, b)
    assert client.delete("/settings/products", headers=h).status_code == 200
    assert client.delete("/settings/appointment-types", headers=h).status_code == 200

    (p,) = _audit(db_session, "settings.products_reset")
    (t,) = _audit(db_session, "settings.appointment_types_reset")
    assert p.organization_id == b.id and t.organization_id == b.id
    assert "only_b" in json.loads(p.before_state)["products"]
    assert "Only B Visit" in json.loads(t.before_state)["appointment_types"]
    db_session.expire_all()
    assert db_session.get(Organization, b.id).products is None


# ════════════════════════════════════════════════════════════════════════════
# C + D. lead deletes: workspace-aware admin, audit against the rows' org
# ════════════════════════════════════════════════════════════════════════════

def test_bulk_delete_duplicates_refused_where_only_advisor(client, db_session, split_person):
    p, b, owner = split_person["person"], split_person["b"], split_person["b_advisor"]
    dup = _lead(db_session, b, owner, is_duplicate=True)
    r = client.delete("/leads/duplicates/bulk-delete", headers=_h(db_session, p, b))
    assert r.status_code == 403, r.text
    db_session.expire_all()
    assert db_session.get(Lead, dup.id) is not None
    assert _audit(db_session, "lead.bulk_delete_duplicates") == []


def test_bulk_delete_duplicates_by_real_admin_audits_workspace_org(client, db_session,
                                                                   split_person):
    b, b_admin, owner = split_person["b"], split_person["b_admin"], split_person["b_advisor"]
    dup = _lead(db_session, b, owner, is_duplicate=True)
    keep = _lead(db_session, b, owner)
    r = client.delete("/leads/duplicates/bulk-delete", headers=_h(db_session, b_admin, b))
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 1
    db_session.expire_all()
    assert db_session.get(Lead, dup.id) is None and db_session.get(Lead, keep.id) is not None
    (e,) = _audit(db_session, "lead.bulk_delete_duplicates")
    assert e.organization_id == b.id and e.target_id == b.id


def test_bulk_delete_audit_uses_workspace_not_home_org(client, db_session):
    """Admin at home in A and in B, acting in B: the entry is B's."""
    plat = _platform(db_session, "Dbl Brand")
    a = _org(db_session, "Home A", plat)
    b = _org(db_session, "Work B", plat)
    person = _user(db_session, "org_admin", org=a, label="dbl")
    _member(db_session, person, a, "org_admin")
    _member(db_session, person, b, "org_admin")
    owner = _user(db_session, "advisor", org=b, label="owner")
    _lead(db_session, b, owner, is_duplicate=True)
    home_dup = _lead(db_session, a, person, is_duplicate=True)
    r = client.delete("/leads/duplicates/bulk-delete", headers=_h(db_session, person, b))
    assert r.status_code == 200, r.text
    (e,) = _audit(db_session, "lead.bulk_delete_duplicates")
    assert e.organization_id == b.id
    db_session.expire_all()
    assert db_session.get(Lead, home_dup.id) is not None


def test_delete_lead_refused_for_home_admin_acting_as_advisor(client, db_session,
                                                              split_person):
    p, b, owner = split_person["person"], split_person["b"], split_person["b_advisor"]
    lead = _lead(db_session, b, owner)
    r = client.delete("/leads/%s" % lead.id, headers=_h(db_session, p, b))
    assert r.status_code in (403, 404), r.text
    db_session.expire_all()
    assert db_session.get(Lead, lead.id) is not None
    assert _audit(db_session, "lead.delete") == []


def test_delete_lead_by_workspace_admin_audits_lead_org(client, db_session, split_person):
    b, b_admin, owner = split_person["b"], split_person["b_admin"], split_person["b_advisor"]
    lead = _lead(db_session, b, owner)
    r = client.delete("/leads/%s" % lead.id, headers=_h(db_session, b_admin, b))
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": True, "id": lead.id}
    db_session.expire_all()
    assert db_session.get(Lead, lead.id) is None
    (e,) = _audit(db_session, "lead.delete")
    assert e.organization_id == b.id


def test_advisor_can_still_delete_own_lead_but_not_colleagues(client, db_session, one_org):
    org, advisor, admin = one_org["org"], one_org["advisor"], one_org["admin"]
    mine = _lead(db_session, org, advisor)
    theirs = _lead(db_session, org, admin)
    assert client.delete("/leads/%s" % mine.id,
                         headers=_h(db_session, advisor)).status_code == 200
    assert client.delete("/leads/%s" % theirs.id,
                         headers=_h(db_session, advisor)).status_code in (403, 404)
    db_session.expire_all()
    assert db_session.get(Lead, theirs.id) is not None


def test_import_batch_leads_delete_refused_where_only_advisor(client, db_session,
                                                              split_person):
    p, b, owner = split_person["person"], split_person["b"], split_person["b_advisor"]
    lead = _lead(db_session, b, owner, source_file="batch-b.csv")
    r = client.delete("/leads/import-batches", params={"source_file": "batch-b.csv"},
                      headers=_h(db_session, p, b))
    assert r.status_code == 403, r.text
    db_session.expire_all()
    assert db_session.get(Lead, lead.id) is not None


def test_import_batch_leads_delete_by_workspace_admin_hits_workspace(client, db_session,
                                                                     split_person):
    a, b = split_person["a"], split_person["b"]
    b_admin, owner, person = split_person["b_admin"], split_person["b_advisor"], split_person["person"]
    in_b = _lead(db_session, b, owner, source_file="shared.csv")
    in_a = _lead(db_session, a, person, source_file="shared.csv")
    in_b_id, in_a_id = in_b.id, in_a.id
    r = client.delete("/leads/import-batches", params={"source_file": "shared.csv"},
                      headers=_h(db_session, b_admin, b))
    assert r.status_code == 200, r.text
    assert r.json()["deleted"]["leads"] == 1
    db_session.expire_all()
    assert db_session.get(Lead, in_b_id) is None
    assert db_session.get(Lead, in_a_id) is not None
    entries = _audit(db_session, "import_batch_deleted")
    assert [e.organization_id for e in entries] == [b.id]


# ════════════════════════════════════════════════════════════════════════════
# E. a missing optional table no longer undoes earlier child deletes
# ════════════════════════════════════════════════════════════════════════════

def test_import_batch_leads_delete_survives_missing_optional_table(client, db_session,
                                                                   one_org):
    """`voice_calls` sits AFTER booking/message tables in the delete order.
    Dropping it used to trigger a full rollback that restored every child row
    already deleted - here the earlier-deleted booking_links / cadence rows -
    before the leads delete ran. Now only that one step is skipped."""
    org, admin, advisor = one_org["org"], one_org["admin"], one_org["advisor"]
    lead = _lead(db_session, org, advisor, source_file="batch.csv")
    other = _lead(db_session, org, advisor, source_file="other.csv")
    # A lead outside the batch pointing INTO it (step 1: un-link).
    other.duplicate_of_lead_id = lead.id
    bl = BookingLink(lead_id=lead.id, user_id=advisor.id, status="booked")
    db_session.add(bl)
    db_session.commit()
    lead_id, bl_id, other_id = lead.id, bl.id, other.id
    db_session.execute(text("DROP TABLE IF EXISTS voice_calls"))
    db_session.commit()

    r = client.delete("/leads/import-batches", params={"source_file": "batch.csv"},
                      headers=_h(db_session, admin))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"]["leads"] == 1
    assert "voice_calls" not in body["deleted"]
    assert body["deleted"].get("booking_links") == 1
    db_session.expire_all()
    assert db_session.get(Lead, lead_id) is None
    assert db_session.get(BookingLink, bl_id) is None
    survivor = db_session.get(Lead, other_id)
    assert survivor is not None and survivor.duplicate_of_lead_id is None, \
        "the un-link done before the failing step must not have been rolled back"
    (e,) = _audit(db_session, "import_batch_deleted")
    assert e.organization_id == org.id


def test_import_batch_leads_delete_unknown_batch_is_404(client, db_session, one_org):
    r = client.delete("/leads/import-batches", params={"source_file": "nope.csv"},
                      headers=_h(db_session, one_org["admin"]))
    assert r.status_code == 404, r.text
