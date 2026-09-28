"""CRM routers: the SELECTED workspace decides the org acted on and the role.

crm_router.py (/crm/connections*) and crm_native_router.py (/crm-native/*)
both authorized with `current_user.role` (account-global) and acted on
`current_user.organization_id` (home org) while the request could be standing
in a different workspace via X-Workspace-Id. Classes covered, one test each at
least, for both routers:

  * cross-tenant READ   - acting in B returns B's rows, never home A's
  * cross-tenant WRITE  - acting in B writes B, never home A
  * guessed id          - another tenant's id answers 404 and changes nothing
  * home admin acting in a workspace where they are only an advisor: refused
  * real workspace admin (advisor at home, org_admin of B): allowed in B
  * audit entries land on the tenant acted on, with the operator's user id
  * no header: behaviour is exactly the home org, as before
"""
import itertools
import json
import uuid

import pytest
from sqlalchemy import text

from app.models.models import (AuditLogEntry, CRMContact, Lead, LeadStatus, LeadTier,
                               MessageTrack, Organization, Platform, User)
from app.models.sales_models import Membership
from app.services.auth_service import create_access_token, hash_password
from app.services.workspace_access import SCOPE_CUSTOMER_ORG, WORKSPACE_HEADER

_SEQ = itertools.count(1)


# ── builders (same shape as test_destructive_route_audit) ───────────────────

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


def _h(db, user, workspace=None, **extra):
    h = {"Authorization": "Bearer " + create_access_token(user, db)}
    if workspace is not None:
        h[WORKSPACE_HEADER] = workspace.id
    h.update(extra)
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


def _contact(db, org, owner=None, **kw):
    c = CRMContact(organization_id=org.id, first_name=kw.pop("first_name", "Ada"),
                   last_name="Lovelace", assigned_to_id=(owner.id if owner else None), **kw)
    db.add(c)
    db.commit()
    return c


def _audit(db, action):
    db.expire_all()
    return db.query(AuditLogEntry).filter(AuditLogEntry.action == action).all()


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


def _conn(db, org, name):
    cid = str(uuid.uuid4())
    db.execute(text(
        "INSERT INTO crm_connections (id, organization_id, name, crm_type, created_at) "
        "VALUES (:id, :org, :name, 'webhook', CURRENT_TIMESTAMP)"),
        {"id": cid, "org": org.id, "name": name})
    db.commit()
    return cid


def _conn_row(db, cid):
    db.expire_all()
    return db.execute(text("SELECT * FROM crm_connections WHERE id = :id"),
                      {"id": cid}).mappings().first()


@pytest.fixture()
def ws(db_session):
    """Two workspaces on one brand and the people who make the defect visible.

    dbl      org_admin of home A AND of B
    split    org_admin of home A, only an ADVISOR of B
    rising   advisor at home A, org_admin of B (the real workspace admin)
    b_admin  plain org_admin of B
    b_adv    plain advisor of B
    """
    plat = _platform(db_session, "CRM Brand")
    a = _org(db_session, "Home A", plat)
    b = _org(db_session, "Workspace B", plat)
    dbl = _user(db_session, "org_admin", org=a, label="dbl")
    _member(db_session, dbl, a, "org_admin")
    _member(db_session, dbl, b, "org_admin")
    split = _user(db_session, "org_admin", org=a, label="split")
    _member(db_session, split, a, "org_admin")
    _member(db_session, split, b, "advisor")
    rising = _user(db_session, "advisor", org=a, label="rising")
    _member(db_session, rising, a, "advisor")
    _member(db_session, rising, b, "org_admin")
    b_admin = _user(db_session, "org_admin", org=b, label="badmin")
    _member(db_session, b_admin, b, "org_admin")
    b_adv = _user(db_session, "advisor", org=b, label="badv")
    _member(db_session, b_adv, b, "advisor")
    return dict(plat=plat, a=a, b=b, dbl=dbl, split=split, rising=rising,
                b_admin=b_admin, b_adv=b_adv)


# ════════════════════════════════════════════════════════════════════════════
# crm_router.py — /crm/connections
# ════════════════════════════════════════════════════════════════════════════

def test_connections_cross_tenant_read_uses_selected_workspace(client, db_session, ws,
                                                               crm_table):
    a_id = _conn(db_session, ws["a"], "A-conn")
    b_id = _conn(db_session, ws["b"], "B-conn")
    r = client.get("/crm/connections", headers=_h(db_session, ws["dbl"], ws["b"]))
    assert r.status_code == 200, r.text
    assert [c["id"] for c in r.json()] == [b_id]
    # No header: the home org, exactly as before.
    r = client.get("/crm/connections", headers=_h(db_session, ws["dbl"]))
    assert r.status_code == 200, r.text
    assert [c["id"] for c in r.json()] == [a_id]


def test_connections_cross_tenant_write_lands_in_selected_workspace(client, db_session, ws,
                                                                    crm_table):
    r = client.post("/crm/connections", headers=_h(db_session, ws["dbl"], ws["b"]),
                    json={"name": "Made in B", "crm_type": "webhook"})
    assert r.status_code == 200, r.text
    row = _conn_row(db_session, r.json()["id"])
    assert row["organization_id"] == ws["b"].id


def test_connections_guessed_id_from_other_tenant_is_404_and_unchanged(client, db_session, ws,
                                                                       crm_table):
    a_id = _conn(db_session, ws["a"], "A-conn")
    h = _h(db_session, ws["b_admin"], ws["b"])
    assert client.put("/crm/connections/%s" % a_id, headers=h,
                      json={"name": "pwned"}).status_code == 404
    assert client.post("/crm/connections/%s/test" % a_id, headers=h).status_code == 404
    assert client.delete("/crm/connections/%s" % a_id, headers=h).status_code == 404
    # Same for the double admin standing in B: their home A row is not B's.
    h2 = _h(db_session, ws["dbl"], ws["b"])
    assert client.put("/crm/connections/%s" % a_id, headers=h2,
                      json={"name": "pwned"}).status_code == 404
    assert client.delete("/crm/connections/%s" % a_id, headers=h2).status_code == 404
    row = _conn_row(db_session, a_id)
    assert row is not None and row["name"] == "A-conn"
    assert _audit(db_session, "crm_connection.deleted") == []


def test_connections_home_admin_refused_where_only_advisor(client, db_session, ws, crm_table):
    b_id = _conn(db_session, ws["b"], "B-conn")
    h = _h(db_session, ws["split"], ws["b"])
    assert client.get("/crm/connections", headers=h).status_code == 403
    assert client.post("/crm/connections", headers=h,
                       json={"name": "x"}).status_code == 403
    assert client.put("/crm/connections/%s" % b_id, headers=h,
                      json={"name": "pwned"}).status_code == 403
    assert client.post("/crm/connections/%s/test" % b_id, headers=h).status_code == 403
    assert client.delete("/crm/connections/%s" % b_id, headers=h).status_code == 403
    assert _conn_row(db_session, b_id)["name"] == "B-conn"
    n = db_session.execute(text("SELECT COUNT(*) FROM crm_connections")).scalar()
    assert n == 1
    assert _audit(db_session, "crm_connection.deleted") == []


def test_connections_real_workspace_admin_allowed(client, db_session, ws, crm_table):
    """Advisor at home, org_admin of B: an admin in B, not in A."""
    b_id = _conn(db_session, ws["b"], "B-conn")
    h = _h(db_session, ws["rising"], ws["b"])
    r = client.get("/crm/connections", headers=h)
    assert r.status_code == 200 and [c["id"] for c in r.json()] == [b_id]
    r = client.put("/crm/connections/%s" % b_id, headers=h, json={"name": "Renamed"})
    assert r.status_code == 200, r.text
    assert _conn_row(db_session, b_id)["name"] == "Renamed"
    # ...and still not an admin at home.
    assert client.get("/crm/connections",
                      headers=_h(db_session, ws["rising"])).status_code == 403


def test_connection_delete_audit_carries_workspace_tenant(client, db_session, ws, crm_table):
    a_id = _conn(db_session, ws["a"], "A-conn")
    b_id = _conn(db_session, ws["b"], "B-conn")
    r = client.delete("/crm/connections/%s" % b_id, headers=_h(db_session, ws["dbl"], ws["b"]))
    assert r.status_code == 200, r.text
    (e,) = _audit(db_session, "crm_connection.deleted")
    assert e.organization_id == ws["b"].id
    assert e.actor_user_id == ws["dbl"].id and e.target_id == b_id
    assert _conn_row(db_session, b_id) is None and _conn_row(db_session, a_id) is not None


def test_connection_create_by_membership_only_admin_is_not_org_none(client, db_session, ws,
                                                                    crm_table):
    """No home column: the org used to be the string "None"."""
    floater = _user(db_session, "advisor", org=None, label="floater")
    _member(db_session, floater, ws["b"], "org_admin")
    r = client.post("/crm/connections", headers=_h(db_session, floater, ws["b"]),
                    json={"name": "Floater conn"})
    assert r.status_code == 200, r.text
    assert _conn_row(db_session, r.json()["id"])["organization_id"] == ws["b"].id


def test_connections_god_platform_pass_in_entered_tenant(client, db_session, ws, crm_table):
    god = _user(db_session, "god_admin", label="god")
    b_id = _conn(db_session, ws["b"], "B-conn")
    _conn(db_session, ws["a"], "A-conn")
    r = client.get("/crm/connections",
                   headers=_h(db_session, god, **{"X-Org-Override": ws["b"].id}))
    assert r.status_code == 200, r.text
    assert [c["id"] for c in r.json()] == [b_id]
    # Neutral owner cannot create a connection owned by nobody.
    r = client.post("/crm/connections", headers=_h(db_session, god), json={"name": "x"})
    assert r.status_code == 409, r.text


# ════════════════════════════════════════════════════════════════════════════
# crm_native_router.py — /crm-native/*
# ════════════════════════════════════════════════════════════════════════════

def test_native_contacts_cross_tenant_read_uses_selected_workspace(client, db_session, ws):
    ca = _contact(db_session, ws["a"], ws["dbl"], first_name="HomeA")
    cb = _contact(db_session, ws["b"], ws["b_adv"], first_name="WorkB")
    r = client.get("/crm-native/contacts", headers=_h(db_session, ws["dbl"], ws["b"]))
    assert r.status_code == 200, r.text
    assert [c["id"] for c in r.json()["items"]] == [cb.id]
    r = client.get("/crm-native/contacts", headers=_h(db_session, ws["dbl"]))
    assert [c["id"] for c in r.json()["items"]] == [ca.id]


def test_native_contact_guessed_id_from_other_tenant_is_404_and_unchanged(client, db_session,
                                                                          ws):
    ca = _contact(db_session, ws["a"], ws["dbl"], first_name="HomeA")
    h = _h(db_session, ws["dbl"], ws["b"])
    assert client.get("/crm-native/contacts/%s" % ca.id, headers=h).status_code == 404
    assert client.patch("/crm-native/contacts/%s" % ca.id, headers=h,
                        json={"first_name": "pwned"}).status_code == 404
    assert client.get("/crm-native/contacts/%s/notes" % ca.id, headers=h).status_code == 404
    assert client.post("/crm-native/contacts/%s/notes" % ca.id, headers=h,
                       json={"content": "x"}).status_code == 404
    assert client.delete("/crm-native/contacts/%s" % ca.id, headers=h).status_code == 404
    db_session.expire_all()
    c = db_session.get(CRMContact, ca.id)
    assert c.first_name == "HomeA" and not c.is_archived
    assert _audit(db_session, "crm_native.contact_archived") == []


def test_native_advisor_in_workspace_sees_only_own_contacts(client, db_session, ws):
    """Home admin who is an advisor in B gets the advisor's view of B."""
    mine = _contact(db_session, ws["b"], ws["split"], first_name="Mine")
    _contact(db_session, ws["b"], ws["b_adv"], first_name="Theirs")
    r = client.get("/crm-native/contacts", headers=_h(db_session, ws["split"], ws["b"]))
    assert r.status_code == 200, r.text
    assert [c["id"] for c in r.json()["items"]] == [mine.id]


def test_native_home_admin_refused_config_writes_where_only_advisor(client, db_session, ws):
    ws["b"].crm_stages = json.dumps([{"key": "b1", "label": "B1"}])
    db_session.commit()
    h = _h(db_session, ws["split"], ws["b"])
    assert client.put("/crm-native/stages", headers=h,
                      json={"stages": [{"key": "x", "label": "X"}]}).status_code == 403
    assert client.delete("/crm-native/stages/reset", headers=h).status_code == 403
    assert client.put("/crm-native/custom-fields", headers=h,
                      json={"fields": [{"key": "k", "label": "K", "type": "text"}]}
                      ).status_code == 403
    db_session.expire_all()
    b = db_session.get(Organization, ws["b"].id)
    assert json.loads(b.crm_stages) == [{"key": "b1", "label": "B1"}]
    assert b.crm_custom_fields is None
    a = db_session.get(Organization, ws["a"].id)
    assert a.crm_stages is None and a.crm_custom_fields is None
    assert _audit(db_session, "crm_native.stages_reset") == []


def test_native_real_workspace_admin_writes_config_in_workspace_not_home(client, db_session,
                                                                         ws):
    h = _h(db_session, ws["rising"], ws["b"])
    r = client.put("/crm-native/stages", headers=h,
                   json={"stages": [{"key": "bx", "label": "BX"}]})
    assert r.status_code == 200, r.text
    r = client.put("/crm-native/custom-fields", headers=h,
                   json={"fields": [{"key": "k", "label": "K", "type": "text"}]})
    assert r.status_code == 200, r.text
    r = client.get("/crm-native/stages", headers=h)
    assert r.json()["stages"] == [{"key": "bx", "label": "BX"}]
    r = client.get("/crm-native/custom-fields", headers=h)
    assert r.json() == [{"key": "k", "label": "K", "type": "text"}]
    db_session.expire_all()
    a = db_session.get(Organization, ws["a"].id)
    assert a.crm_stages is None and a.crm_custom_fields is None
    # ...and an advisor at home.
    assert client.put("/crm-native/stages", headers=_h(db_session, ws["rising"]),
                      json={"stages": [{"key": "x", "label": "X"}]}).status_code == 403


def test_native_dbl_admin_config_write_hits_workspace_not_home(client, db_session, ws):
    r = client.put("/crm-native/stages", headers=_h(db_session, ws["dbl"], ws["b"]),
                   json={"stages": [{"key": "only_b", "label": "Only B"}]})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.get(Organization, ws["a"].id).crm_stages is None
    assert "only_b" in db_session.get(Organization, ws["b"].id).crm_stages


def test_native_stage_reset_audit_carries_workspace_tenant(client, db_session, ws):
    ws["a"].crm_stages = json.dumps([{"key": "a1", "label": "A1"}])
    ws["b"].crm_stages = json.dumps([{"key": "b1", "label": "B1"}])
    db_session.commit()
    r = client.delete("/crm-native/stages/reset", headers=_h(db_session, ws["dbl"], ws["b"]))
    assert r.status_code == 200, r.text
    (e,) = _audit(db_session, "crm_native.stages_reset")
    assert e.organization_id == ws["b"].id and e.actor_user_id == ws["dbl"].id
    assert "b1" in json.loads(e.before_state)["crm_stages"]
    db_session.expire_all()
    assert db_session.get(Organization, ws["b"].id).crm_stages is None
    assert db_session.get(Organization, ws["a"].id).crm_stages is not None


def test_native_contact_archive_audit_carries_workspace_tenant(client, db_session, ws):
    cb = _contact(db_session, ws["b"], ws["b_adv"], first_name="WorkB")
    r = client.delete("/crm-native/contacts/%s" % cb.id,
                      headers=_h(db_session, ws["dbl"], ws["b"]))
    assert r.status_code == 200, r.text
    (e,) = _audit(db_session, "crm_native.contact_archived")
    assert e.organization_id == ws["b"].id and e.actor_user_id == ws["dbl"].id
    assert e.target_id == cb.id


def test_native_create_contact_lands_in_selected_workspace(client, db_session, ws):
    floater = _user(db_session, "advisor", org=None, label="floater")
    _member(db_session, floater, ws["b"], "advisor")
    for who in (ws["dbl"], floater):
        r = client.post("/crm-native/contacts", headers=_h(db_session, who, ws["b"]),
                        json={"first_name": "New"})
        assert r.status_code == 200, r.text
        assert r.json()["organization_id"] == ws["b"].id


def test_native_create_contact_with_guessed_foreign_ids_is_404(client, db_session, ws):
    home_lead = _lead(db_session, ws["a"], ws["dbl"])
    stranger = _user(db_session, "advisor", org=ws["a"], label="stranger")
    h = _h(db_session, ws["b_admin"], ws["b"])
    r = client.post("/crm-native/contacts", headers=h,
                    json={"first_name": "X", "lead_id": home_lead.id})
    assert r.status_code == 404, r.text
    r = client.post("/crm-native/contacts", headers=h,
                    json={"first_name": "X", "assigned_to_id": stranger.id})
    assert r.status_code == 404, r.text
    cb = _contact(db_session, ws["b"], ws["b_adv"])
    r = client.patch("/crm-native/contacts/%s" % cb.id, headers=h,
                     json={"lead_id": home_lead.id})
    assert r.status_code == 404, r.text
    r = client.patch("/crm-native/contacts/%s" % cb.id, headers=h,
                     json={"assigned_to_id": stranger.id})
    assert r.status_code == 404, r.text
    db_session.expire_all()
    assert db_session.query(CRMContact).count() == 1
    c = db_session.get(CRMContact, cb.id)
    assert c.lead_id is None and c.assigned_to_id == ws["b_adv"].id
    # An in-workspace reassignment still works, and re-sending unchanged
    # values (what the UI does) is an ordinary edit.
    r = client.patch("/crm-native/contacts/%s" % cb.id, headers=h,
                     json={"assigned_to_id": ws["b_admin"].id, "first_name": "Ok"})
    assert r.status_code == 200, r.text
    r = client.patch("/crm-native/contacts/%s" % cb.id, headers=h,
                     json=r.json())
    assert r.status_code == 200, r.text


def test_native_sync_from_leads_writes_workspace_not_home(client, db_session, ws):
    lb = _lead(db_session, ws["b"], ws["b_adv"], first_name="BFamily")
    la = _lead(db_session, ws["a"], ws["dbl"], first_name="AFamily")
    r = client.post("/crm-native/sync-from-leads", headers=_h(db_session, ws["dbl"], ws["b"]))
    assert r.status_code == 200, r.text
    assert r.json()["synced"] == 1
    db_session.expire_all()
    rows = db_session.query(CRMContact).all()
    assert [(c.lead_id, c.organization_id) for c in rows] == [(lb.id, ws["b"].id)]
    assert la.id not in {c.lead_id for c in rows}


def test_native_contact_from_lead_writes_workspace_and_404s_foreign_lead(client, db_session,
                                                                         ws):
    lb = _lead(db_session, ws["b"], ws["b_adv"])
    la = _lead(db_session, ws["a"], ws["dbl"])
    h = _h(db_session, ws["dbl"], ws["b"])
    r = client.post("/crm-native/contacts/from-lead/%s" % lb.id, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["organization_id"] == ws["b"].id
    r = client.post("/crm-native/contacts/from-lead/%s" % la.id, headers=h)
    assert r.status_code == 404, r.text
    db_session.expire_all()
    assert db_session.query(CRMContact).filter(CRMContact.lead_id == la.id).count() == 0


def test_native_neutral_god_writes_still_409(client, db_session, ws):
    god = _user(db_session, "god_admin", label="god")
    h = _h(db_session, god)
    assert client.post("/crm-native/contacts", headers=h,
                       json={"first_name": "x"}).status_code == 409
    assert client.post("/crm-native/sync-from-leads", headers=h).status_code == 409
    # Entered tenant: writes land there.
    r = client.post("/crm-native/contacts",
                    headers=_h(db_session, god, **{"X-Org-Override": ws["b"].id}),
                    json={"first_name": "x"})
    assert r.status_code == 200, r.text
    assert r.json()["organization_id"] == ws["b"].id


def test_native_stages_super_admin_org_param_stays_brand_scoped(client, db_session, ws):
    other = _org(db_session, "Other brand org", _platform(db_session, "Other"))
    sa = _user(db_session, "super_admin", org=ws["a"], platform=ws["plat"], label="sa")
    r = client.get("/crm-native/stages?org_id=%s" % other.id, headers=_h(db_session, sa))
    assert r.status_code == 404, r.text
    r = client.get("/crm-native/stages?org_id=%s" % ws["b"].id, headers=_h(db_session, sa))
    assert r.status_code == 200, r.text
