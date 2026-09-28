"""admin_router, lead_scope.own_records_only and crm_native config audits:
the SELECTED workspace decides the org acted on and the role.

admin_router authorized with `require_admin` (workspace-aware) and then found
target users, assignees, dashboards and audit tenants through
`current_user.organization_id` (home org). An admin of workspace B who is only
an advisor at home A could therefore deactivate / force-logout home A's people.
`own_records_only` judged `users.role`, so a home org_admin who is only an
advisor in B acted on every colleague's queue there.

Same fixture shape as test_crm_workspace_scope / test_workspace_role_sweep:

    dbl      org_admin of home A AND of B
    split    org_admin of home A, only an ADVISOR of B
    rising   advisor at home A, org_admin of B (the real workspace admin)
    b_adv    plain advisor homed in B
    a_adv    plain advisor homed in A

Classes: cross-workspace refusal (404 for foreign ids, 403 for the gate, nothing
changed), real workspace admin allowed, audit tenant correct, own_records_only
workspace-aware, no header == home org as before, god platform pass unchanged.
No SMS, email, AI or voice call leaves the process.
"""
import itertools
import json
import uuid

import pytest

from app.models.models import (AuditLogEntry, Lead, LeadStatus, LeadTier, MessageTrack,
                               Organization, Platform, User, VoiceCallCampaign)
from app.models.sales_models import Membership
from app.services.auth_service import create_access_token, hash_password
from app.services.workspace_access import SCOPE_CUSTOMER_ORG, WORKSPACE_HEADER

_SEQ = itertools.count(1)


# ── builders (same shape as test_crm_workspace_scope) ───────────────────────

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
    db.add(Membership(user_id=user.id, scope_type=SCOPE_CUSTOMER_ORG,
                      scope_id=org.id, role=role, is_active=True))
    db.commit()


def _h(db, user, workspace=None, **extra):
    h = {"Authorization": "Bearer " + create_access_token(user, db)}
    if workspace is not None:
        h[WORKSPACE_HEADER] = workspace.id
    h.update(extra)
    return h


def _lead(db, org, owner, **kw):
    lead = Lead(organization_id=org.id, assigned_to_id=(owner.id if owner else None),
                first_name=kw.pop("first_name", "Pat"), last_name=kw.pop("last_name", "Family"),
                phone=kw.pop("phone", "1214555%04d" % next(_SEQ)),
                email="lead-%s@example.com" % uuid.uuid4().hex[:6],
                tier=LeadTier.PRE_NEED, message_track=MessageTrack.PRE_NEED_LOCK_PRICE,
                status=kw.pop("status", LeadStatus.NEW), **kw)
    db.add(lead)
    db.commit()
    return lead


def _audit(db, action):
    db.expire_all()
    return db.query(AuditLogEntry).filter(AuditLogEntry.action == action).all()


def _fresh(db, model, pk):
    db.expire_all()
    return db.get(model, pk)


@pytest.fixture()
def ws(db_session):
    plat = _platform(db_session, "Admin Brand")
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
    b_adv = _user(db_session, "advisor", org=b, label="badv")
    _member(db_session, b_adv, b, "advisor")
    a_adv = _user(db_session, "advisor", org=a, label="aadv")
    _member(db_session, a_adv, a, "advisor")
    return dict(plat=plat, a=a, b=b, dbl=dbl, split=split, rising=rising,
                b_adv=b_adv, a_adv=a_adv)


_USER_ACTIONS = (
    ("patch", "/admin/users/%s/deactivate"),
    ("patch", "/admin/users/%s/reactivate"),
    ("post", "/admin/users/%s/force-logout"),
    ("patch", "/admin/users/%s/clear-setup"),
    ("get", "/admin/users/%s/detail"),
)
_USER_AUDITS = ("user.deactivate", "user.reactivate", "user.force_logout", "user.clear_setup")


# ════════════════════════════════════════════════════════════════════════════
# admin_router — user management
# ════════════════════════════════════════════════════════════════════════════

def test_user_actions_on_home_org_user_from_workspace_are_404_and_unchanged(client, db_session,
                                                                            ws):
    """Workspace B admin (advisor at home A) cannot reach home A's people."""
    ws["a_adv"].must_change_password = True
    db_session.commit()
    for who in (ws["rising"], ws["dbl"]):
        h = _h(db_session, who, ws["b"])
        for method, path in _USER_ACTIONS:
            r = getattr(client, method)(path % ws["a_adv"].id, headers=h)
            assert r.status_code == 404, (who.full_name, path, r.text)
    u = _fresh(db_session, User, ws["a_adv"].id)
    assert u.is_active and u.must_change_password
    for act in _USER_AUDITS:
        assert _audit(db_session, act) == []


def test_user_actions_refused_where_home_admin_is_only_advisor(client, db_session, ws):
    h = _h(db_session, ws["split"], ws["b"])
    for method, path in _USER_ACTIONS:
        r = getattr(client, method)(path % ws["b_adv"].id, headers=h)
        assert r.status_code == 403, (path, r.text)
    r = client.post("/admin/users", headers=h,
                    json={"email": "x-%s@example.com" % uuid.uuid4().hex[:6],
                          "full_name": "X", "role": "org_admin"})
    assert r.status_code == 403, r.text
    assert _fresh(db_session, User, ws["b_adv"].id).is_active
    for act in _USER_AUDITS + ("user.create",):
        assert _audit(db_session, act) == []


def test_real_workspace_admin_manages_workspace_users_and_audit_is_workspace(client,
                                                                             db_session, ws):
    h = _h(db_session, ws["rising"], ws["b"])
    target = ws["b_adv"].id
    r = client.get("/admin/users/%s/detail" % target, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == target
    for method, path in _USER_ACTIONS[:4]:
        r = getattr(client, method)(path % target, headers=h)
        assert r.status_code == 200, (path, r.text)
    for act in _USER_AUDITS:
        (e,) = _audit(db_session, act)
        assert e.organization_id == ws["b"].id, act
        assert e.actor_user_id == ws["rising"].id and e.target_id == target
    # ...and the same person is not an admin at home.
    r = client.patch("/admin/users/%s/deactivate" % ws["a_adv"].id,
                     headers=_h(db_session, ws["rising"]))
    assert r.status_code == 403, r.text
    assert _fresh(db_session, User, ws["a_adv"].id).is_active


def test_no_header_is_home_org_as_before(client, db_session, ws):
    h = _h(db_session, ws["dbl"])
    r = client.patch("/admin/users/%s/deactivate" % ws["b_adv"].id, headers=h)
    assert r.status_code == 404, r.text
    r = client.patch("/admin/users/%s/deactivate" % ws["a_adv"].id, headers=h)
    assert r.status_code == 200, r.text
    (e,) = _audit(db_session, "user.deactivate")
    assert e.organization_id == ws["a"].id
    assert _fresh(db_session, User, ws["b_adv"].id).is_active


def test_detail_shows_workspace_member_homed_elsewhere_with_workspace_metrics(client,
                                                                              db_session, ws):
    """rising is homed in A but a member of B: B's user list shows them, so the
    detail page answers too - with B's numbers only."""
    _lead(db_session, ws["b"], ws["rising"])
    _lead(db_session, ws["a"], ws["rising"])
    _lead(db_session, ws["a"], ws["rising"])
    r = client.get("/admin/users/%s/detail" % ws["rising"].id,
                   headers=_h(db_session, ws["dbl"], ws["b"]))
    assert r.status_code == 200, r.text
    assert r.json()["metrics"]["leads_owned"] == 1


def test_create_user_lands_in_workspace_and_audits_it(client, db_session, ws):
    email = "new-%s@example.com" % uuid.uuid4().hex[:6]
    r = client.post("/admin/users", headers=_h(db_session, ws["rising"], ws["b"]),
                    json={"email": email, "full_name": "New", "role": "org_admin"})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    u = db_session.query(User).filter(User.email == email).one()
    assert u.organization_id == ws["b"].id
    (e,) = _audit(db_session, "user.create")
    assert e.organization_id == ws["b"].id and e.actor_user_id == ws["rising"].id
    # An advisor at home cannot create anyone in home A.
    r = client.post("/admin/users", headers=_h(db_session, ws["rising"]),
                    json={"email": "y-" + email, "full_name": "Y", "role": "org_admin"})
    assert r.status_code == 403, r.text


def test_create_user_neutral_god_still_409(client, db_session, ws):
    god = _user(db_session, "god_admin", label="god")
    r = client.post("/admin/users", headers=_h(db_session, god),
                    json={"email": "g-%s@example.com" % uuid.uuid4().hex[:6],
                          "full_name": "G", "role": "advisor"})
    assert r.status_code == 409, r.text


def test_god_platform_pass_unchanged_and_audit_is_targets_tenant(client, db_session, ws):
    god = _user(db_session, "god_admin", label="god")
    r = client.patch("/admin/users/%s/deactivate" % ws["b_adv"].id, headers=_h(db_session, god))
    assert r.status_code == 200, r.text
    (e,) = _audit(db_session, "user.deactivate")
    assert e.organization_id == ws["b"].id and e.actor_user_id == god.id
    assert not _fresh(db_session, User, ws["b_adv"].id).is_active


# ════════════════════════════════════════════════════════════════════════════
# admin_router — reassign, merge, fix-contact
# ════════════════════════════════════════════════════════════════════════════

def test_reassign_assignee_must_be_in_the_workspace(client, db_session, ws):
    lb = _lead(db_session, ws["b"], ws["b_adv"])
    h = _h(db_session, ws["dbl"], ws["b"])
    # a_adv is home A only: not B's person, even though dbl's home is A.
    r = client.post("/admin/leads/reassign", headers=h,
                    json={"lead_ids": [lb.id], "new_assigned_to_id": ws["a_adv"].id})
    assert r.status_code == 404, r.text
    assert _fresh(db_session, Lead, lb.id).assigned_to_id == ws["b_adv"].id
    assert _audit(db_session, "lead.reassign") == []
    # rising is homed in A but a member of B: a legitimate B assignee.
    r = client.post("/admin/leads/reassign", headers=h,
                    json={"lead_ids": [lb.id], "new_assigned_to_id": ws["rising"].id})
    assert r.status_code == 200, r.text
    assert r.json()["reassigned_count"] == 1
    assert _fresh(db_session, Lead, lb.id).assigned_to_id == ws["rising"].id
    (e,) = _audit(db_session, "lead.reassign")
    assert e.organization_id == ws["b"].id and e.actor_user_id == ws["dbl"].id


def test_reassign_home_leads_from_workspace_are_skipped(client, db_session, ws):
    la = _lead(db_session, ws["a"], ws["a_adv"])
    r = client.post("/admin/leads/reassign", headers=_h(db_session, ws["rising"], ws["b"]),
                    json={"lead_ids": [la.id], "new_assigned_to_id": ws["b_adv"].id})
    assert r.status_code == 200, r.text
    assert r.json()["reassigned_count"] == 0 and r.json()["skipped_ids"] == [la.id]
    assert _fresh(db_session, Lead, la.id).assigned_to_id == ws["a_adv"].id


def test_merge_and_fix_contact_audit_the_workspace(client, db_session, ws):
    keep = _lead(db_session, ws["b"], ws["b_adv"], first_name="Keep")
    dup = _lead(db_session, ws["b"], ws["b_adv"], first_name="Dup")
    h = _h(db_session, ws["dbl"], ws["b"])
    r = client.patch("/admin/leads/%s/fix-contact-info" % keep.id, headers=h,
                     json={"first_name": "Fixed"})
    assert r.status_code == 200, r.text
    (e,) = _audit(db_session, "lead.fix_contact_info")
    assert e.organization_id == ws["b"].id
    r = client.post("/admin/leads/merge", headers=h,
                    json={"keep_lead_id": keep.id, "merge_lead_ids": [dup.id]})
    assert r.status_code == 200, r.text
    (e,) = _audit(db_session, "lead.merge")
    assert e.organization_id == ws["b"].id and e.target_id == keep.id


# ════════════════════════════════════════════════════════════════════════════
# admin_router — dashboards
# ════════════════════════════════════════════════════════════════════════════

def test_dashboards_read_the_workspace_for_a_workspace_admin(client, db_session, ws):
    _lead(db_session, ws["b"], ws["b_adv"], status=LeadStatus.BOOKED)
    _lead(db_session, ws["a"], ws["a_adv"])
    _lead(db_session, ws["a"], ws["a_adv"])
    h = _h(db_session, ws["rising"], ws["b"])

    r = client.get("/admin/dashboard", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) == {"organization_id", "is_god_view", "total_leads",
                      "total_duplicates_prevented", "advisors"}
    assert d["organization_id"] == ws["b"].id and d["total_leads"] == 1
    assert [a["advisor_id"] for a in d["advisors"]] == [ws["b_adv"].id]

    r = client.get("/admin/dashboard/funnel", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["organization_id"] == ws["b"].id
    assert r.json()["total_leads"] == 1 and r.json()["booked"] == 1

    r = client.get("/admin/dashboard/metrics", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["organization_id"] == ws["b"].id
    assert [a["advisor_id"] for a in r.json()["advisors"]] == [ws["b_adv"].id]

    r = client.get("/admin/dashboard/team-activity", headers=h)
    assert r.status_code == 200, r.text
    ids = {row["advisor_id"] for row in r.json()["advisors"]}
    assert ws["a_adv"].id not in ids and ws["b_adv"].id in ids

    r = client.get("/admin/dashboard/revenue", headers=h)
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"total_sales", "by_advisor", "product_mix", "monthly_trend",
                             "recent_sale_notes"}

    # Only an advisor in B: no dashboard at all.
    assert client.get("/admin/dashboard",
                      headers=_h(db_session, ws["split"], ws["b"])).status_code == 403


def test_dashboards_without_header_read_home_as_before(client, db_session, ws):
    _lead(db_session, ws["b"], ws["b_adv"])
    _lead(db_session, ws["a"], ws["a_adv"])
    _lead(db_session, ws["a"], ws["a_adv"])
    r = client.get("/admin/dashboard", headers=_h(db_session, ws["dbl"]))
    assert r.status_code == 200, r.text
    assert r.json()["organization_id"] == ws["a"].id and r.json()["total_leads"] == 2
    r = client.get("/admin/dashboard/funnel", headers=_h(db_session, ws["dbl"]))
    assert r.json()["total_leads"] == 2


# ════════════════════════════════════════════════════════════════════════════
# admin_router — operator audits land on the org acted on
# ════════════════════════════════════════════════════════════════════════════

def test_provision_and_org_update_audit_the_org_acted_on(client, db_session, ws):
    god = _user(db_session, "god_admin", label="god")
    h = _h(db_session, god)
    slug = "prov-%s" % uuid.uuid4().hex[:8]
    r = client.post("/admin/provision-client", headers=h,
                    json={"org_name": "Prov Co", "org_slug": slug,
                          "supervisor_full_name": "Sup",
                          "supervisor_email": "sup-%s@example.com" % uuid.uuid4().hex[:6],
                          "supervisor_password": "LongEnough123!"})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    new_org = db_session.query(Organization).filter(Organization.slug == slug).one()
    (e,) = _audit(db_session, "provision_client")
    assert e.organization_id == new_org.id
    r = client.put("/admin/organizations/%s" % ws["b"].id, headers=h, json={"name": "B Renamed"})
    assert r.status_code == 200, r.text
    (e,) = _audit(db_session, "org.update")
    assert e.organization_id == ws["b"].id


# ════════════════════════════════════════════════════════════════════════════
# lead_scope.own_records_only — auto_send_router and voice_router
# ════════════════════════════════════════════════════════════════════════════

def _queue_item(db, org, lead, advisor):
    from app.routers.auto_send_router import AutoSendItem
    item = AutoSendItem(organization_id=org.id, lead_id=lead.id, advisor_id=advisor.id,
                        message="Queued hello", status="pending")
    db.add(item)
    db.commit()
    return item


def _voice_campaign(db, org, advisor, name):
    c = VoiceCallCampaign(organization_id=org.id, advisor_id=advisor.id, name=name,
                          status="pending", total_leads=0)
    db.add(c)
    db.commit()
    return c


def test_auto_send_advisor_in_workspace_cannot_touch_colleagues_queue(client, db_session, ws):
    from app.routers.auto_send_router import AutoSendItem
    lb = _lead(db_session, ws["b"], ws["b_adv"])
    item = _queue_item(db_session, ws["b"], lb, ws["b_adv"])
    h = _h(db_session, ws["split"], ws["b"])
    assert client.patch("/auto-send/%s/edit" % item.id, headers=h,
                        json={"message": "pwned"}).status_code == 404
    assert client.post("/auto-send/%s/skip" % item.id, headers=h).status_code == 404
    assert client.post("/auto-send/%s/approve" % item.id, headers=h).status_code == 404
    got = _fresh(db_session, AutoSendItem, item.id)
    assert got.status == "pending" and got.message == "Queued hello"
    assert _audit(db_session, "auto_send.skipped") == []


def test_auto_send_workspace_admin_may_action_the_teams_queue(client, db_session, ws):
    from app.routers.auto_send_router import AutoSendItem
    lb = _lead(db_session, ws["b"], ws["b_adv"])
    item = _queue_item(db_session, ws["b"], lb, ws["b_adv"])
    h = _h(db_session, ws["rising"], ws["b"])
    r = client.patch("/auto-send/%s/edit" % item.id, headers=h, json={"message": "Edited"})
    assert r.status_code == 200, r.text
    r = client.post("/auto-send/%s/skip" % item.id, headers=h)
    assert r.status_code == 200, r.text
    assert _fresh(db_session, AutoSendItem, item.id).status == "skipped"
    (e,) = _audit(db_session, "auto_send.skipped")
    assert e.organization_id == ws["b"].id


def test_voice_campaigns_judged_on_workspace_role(client, db_session, ws):
    mine = _voice_campaign(db_session, ws["b"], ws["split"], "Split's")
    theirs = _voice_campaign(db_session, ws["b"], ws["b_adv"], "B adv's")
    _voice_campaign(db_session, ws["a"], ws["a_adv"], "Home A")
    h = _h(db_session, ws["split"], ws["b"])
    r = client.get("/voice/campaigns", headers=h)
    assert r.status_code == 200, r.text
    assert [c["id"] for c in r.json()] == [mine.id]
    assert client.get("/voice/campaigns/%s" % theirs.id, headers=h).status_code == 404
    assert client.post("/voice/campaigns/%s/pause" % theirs.id, headers=h).status_code == 404
    assert client.post("/voice/campaigns/%s/cancel" % theirs.id, headers=h).status_code == 404
    assert _fresh(db_session, VoiceCallCampaign, theirs.id).status == "pending"

    h2 = _h(db_session, ws["rising"], ws["b"])
    r = client.get("/voice/campaigns", headers=h2)
    assert r.status_code == 200, r.text
    assert {c["id"] for c in r.json()} == {mine.id, theirs.id}
    r = client.post("/voice/campaigns/%s/pause" % theirs.id, headers=h2)
    assert r.status_code == 200, r.text
    assert _fresh(db_session, VoiceCallCampaign, theirs.id).status == "paused"


def test_own_records_only_without_db_is_users_role_as_before(db_session, ws):
    """Signature-compatible: no db => the account role, exactly as before."""
    from app.services import lead_scope
    q = db_session.query(VoiceCallCampaign)
    _voice_campaign(db_session, ws["b"], ws["b_adv"], "x")
    # rising's users.role is advisor -> narrowed; split's is org_admin -> not.
    assert lead_scope.own_records_only(q, VoiceCallCampaign.advisor_id,
                                       ws["rising"]).count() == 0
    assert lead_scope.own_records_only(q, VoiceCallCampaign.advisor_id,
                                       ws["split"]).count() == 1
    god = _user(db_session, "god_admin", label="god")
    assert lead_scope.own_records_only(q, VoiceCallCampaign.advisor_id, god,
                                       db_session).count() == 1


# ════════════════════════════════════════════════════════════════════════════
# crm_native_router — PUT /stages and /custom-fields are audited
# ════════════════════════════════════════════════════════════════════════════

def test_crm_native_config_writes_audit_under_workspace(client, db_session, ws):
    ws["b"].crm_stages = json.dumps([{"key": "old", "label": "Old"}])
    db_session.commit()
    h = _h(db_session, ws["dbl"], ws["b"])
    r = client.put("/crm-native/stages", headers=h,
                   json={"stages": [{"key": "new", "label": "New"}]})
    assert r.status_code == 200, r.text
    (e,) = _audit(db_session, "crm_native.stages_updated")
    assert e.organization_id == ws["b"].id and e.actor_user_id == ws["dbl"].id
    assert "old" in json.loads(e.before_state)["crm_stages"]
    assert "new" in json.loads(e.after_state)["crm_stages"]
    r = client.put("/crm-native/custom-fields", headers=h,
                   json={"fields": [{"key": "k", "label": "K", "type": "text"}]})
    assert r.status_code == 200, r.text
    (e,) = _audit(db_session, "crm_native.custom_fields_updated")
    assert e.organization_id == ws["b"].id and e.target_id == ws["b"].id


def test_crm_native_refused_config_write_writes_no_audit(client, db_session, ws):
    h = _h(db_session, ws["split"], ws["b"])
    assert client.put("/crm-native/stages", headers=h,
                      json={"stages": [{"key": "x", "label": "X"}]}).status_code == 403
    assert client.put("/crm-native/custom-fields", headers=h,
                      json={"fields": [{"key": "k", "label": "K", "type": "text"}]}
                      ).status_code == 403
    assert _audit(db_session, "crm_native.stages_updated") == []
    assert _audit(db_session, "crm_native.custom_fields_updated") == []


# ════════════════════════════════════════════════════════════════════════════
# lead_scope.reject_ownership_fields — workspace role, not users.role
# ════════════════════════════════════════════════════════════════════════════

def test_home_admin_who_is_advisor_in_b_cannot_reassign_via_lead_edit(client, db_session, ws):
    lead = _lead(db_session, ws["b"], ws["split"])
    r = client.patch("/leads/%s" % lead.id, json={"assigned_to_id": ws["b_adv"].id},
                     headers=_h(db_session, ws["split"], ws["b"]))
    assert r.status_code == 403
    assert _fresh(db_session, Lead, lead.id).assigned_to_id == ws["split"].id


def test_workspace_admin_may_send_ownership_fields_in_b(client, db_session, ws):
    lead = _lead(db_session, ws["b"], ws["b_adv"])
    r = client.patch("/leads/%s" % lead.id, json={"first_name": "Kim",
                                                  "assigned_to_id": ws["b_adv"].id},
                     headers=_h(db_session, ws["rising"], ws["b"]))
    assert r.status_code != 403, r.text
