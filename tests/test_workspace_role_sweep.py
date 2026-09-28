"""Sweep: routes that authorized in the SELECTED workspace but acted on the
HOME org, or authorized with the account-global `users.role`.

Same fixture shape as test_crm_workspace_scope:

    dbl      org_admin of home A AND of B
    split    org_admin of home A, only an ADVISOR of B
    rising   advisor at home A, org_admin of B (the real workspace admin)

No SMS, email, AI or voice call leaves the process: conftest refuses to build
a Twilio client, and the cadence runner is replaced where it would be reached.
"""
import itertools
import json
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.models.models import (AuditLogEntry, BookingLink, Campaign, Lead, LeadStatus,
                               LeadTier, MessageTrack, Organization, Platform, Proposal,
                               SuppressionEntry, User)
from app.models.sales_models import Membership
from app.services.auth_service import create_access_token, hash_password
from app.services.workspace_access import SCOPE_CUSTOMER_ORG, WORKSPACE_HEADER

_SEQ = itertools.count(1)


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


def _h(db, user, workspace=None):
    h = {"Authorization": "Bearer " + create_access_token(user, db)}
    if workspace is not None:
        h[WORKSPACE_HEADER] = workspace.id
    return h


def _lead(db, org, owner, **kw):
    lead = Lead(organization_id=org.id, assigned_to_id=owner.id,
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


@pytest.fixture()
def ws(db_session):
    plat = _platform(db_session, "Sweep Brand")
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
    return dict(plat=plat, a=a, b=b, dbl=dbl, split=split, rising=rising,
                b_adv=b_adv, a_adv=a_adv)


# ── org_settings_router._resolve_org ─────────────────────────────────────────

def test_org_settings_admin_write_hits_workspace_not_home(client, db_session, ws):
    r = client.patch("/org-settings/branding", headers=_h(db_session, ws["rising"], ws["b"]),
                     json={"brand_name": "B Brand"})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.get(Organization, ws["b"].id).brand_name == "B Brand"
    assert db_session.get(Organization, ws["a"].id).brand_name != "B Brand"
    # ...and the same person is not an admin at home.
    r = client.patch("/org-settings/branding", headers=_h(db_session, ws["rising"]),
                     json={"brand_name": "pwned"})
    assert r.status_code == 403, r.text


# ── templates_router ────────────────────────────────────────────────────────

def test_templates_write_hits_workspace_and_audits_it(client, db_session, ws):
    r = client.put("/templates/", headers=_h(db_session, ws["dbl"], ws["b"]),
                   json={"message_track": MessageTrack.PRE_NEED_LOCK_PRICE.value,
                         "channel": "sms", "body_template": "Only B {first_name}"})
    assert r.status_code == 200, r.text
    (e,) = _audit(db_session, "template.update")
    assert e.organization_id == ws["b"].id and e.actor_user_id == ws["dbl"].id
    rows = db_session.execute(text(
        "SELECT organization_id FROM message_templates")).fetchall()
    assert {r[0] for r in rows} == {ws["b"].id}


# ── proposal_router ─────────────────────────────────────────────────────────

def test_proposals_created_and_listed_in_workspace(client, db_session, ws):
    home = Proposal(id=str(uuid.uuid4()), organization_id=ws["a"].id,
                    created_by_id=ws["dbl"].id, title="Home A deal", status="draft")
    db_session.add(home)
    db_session.commit()
    h = _h(db_session, ws["dbl"], ws["b"])
    r = client.post("/proposals/", headers=h, json={"title": "B deal"})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.get(Proposal, r.json()["id"]).organization_id == ws["b"].id
    r = client.get("/proposals/", headers=h)
    assert [p["title"] for p in r.json()] == ["B deal"]
    # A guessed id from home A answers 404 inside B.
    assert client.get("/proposals/%s" % home.id, headers=h).status_code == 404
    assert client.delete("/proposals/%s" % home.id, headers=h).status_code == 404
    db_session.expire_all()
    assert db_session.get(Proposal, home.id).deleted_at is None


# ── compliance_router ───────────────────────────────────────────────────────

def test_permanent_dnc_suppresses_and_audits_the_workspace(client, db_session, ws):
    lb = _lead(db_session, ws["b"], ws["b_adv"], phone="12145550999")
    la = _lead(db_session, ws["a"], ws["dbl"], phone="12145550999")
    r = client.post("/compliance/permanent-dnc", headers=_h(db_session, ws["dbl"], ws["b"]),
                    json={"phone": "12145550999", "reason": "asked"})
    assert r.status_code == 201, r.text
    db_session.expire_all()
    entries = db_session.query(SuppressionEntry).all()
    assert [e.organization_id for e in entries] == [ws["b"].id]
    assert db_session.get(Lead, lb.id).status == "dnc"
    assert db_session.get(Lead, la.id).status != "dnc"
    (e,) = _audit(db_session, "compliance.permanent_dnc")
    assert e.organization_id == ws["b"].id


def test_unsuppress_other_tenant_entry_is_404(client, db_session, ws):
    ea = SuppressionEntry(organization_id=ws["a"].id, phone="12145550888", reason="x",
                          source="manual")
    db_session.add(ea)
    db_session.commit()
    r = client.delete("/compliance/suppression-list/%s" % ea.id,
                      headers=_h(db_session, ws["dbl"], ws["b"]))
    assert r.status_code == 404, r.text
    db_session.expire_all()
    assert db_session.get(SuppressionEntry, ea.id) is not None


# ── cadence_router /run-due ─────────────────────────────────────────────────

def test_run_due_runs_only_the_workspace_never_the_platform(client, db_session, ws,
                                                             monkeypatch):
    seen = []
    import app.routers.cadence_router as cr
    monkeypatch.setattr(cr, "run_due_cadences",
                        lambda db, organization_id=None: seen.append(organization_id) or {})
    floater = _user(db_session, "advisor", org=None, label="floater")
    _member(db_session, floater, ws["b"], "org_admin")
    assert client.post("/cadence/run-due",
                       headers=_h(db_session, floater, ws["b"])).status_code == 200
    assert client.post("/cadence/run-due",
                       headers=_h(db_session, ws["dbl"], ws["b"])).status_code == 200
    assert seen == [ws["b"].id, ws["b"].id], "None would have meant every organization"


# ── social_webhooks_router /token ───────────────────────────────────────────

def test_social_webhook_token_is_the_workspace_credential(client, db_session, ws):
    r = client.get("/webhooks/token", headers=_h(db_session, ws["rising"], ws["b"]))
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert r.json()["social_webhook_token"] == db_session.get(Organization, ws["b"].id).social_webhook_token
    assert db_session.get(Organization, ws["a"].id).social_webhook_token is None


# ── leads: global role / home org ───────────────────────────────────────────

def test_dedup_maintenance_refused_where_only_advisor(client, db_session, ws):
    r = client.post("/leads/maintenance/duplicate-dnc-repair?apply=true",
                    headers=_h(db_session, ws["split"], ws["b"]))
    assert r.status_code == 403, r.text
    r = client.post("/leads/maintenance/duplicate-dnc-repair?apply=true",
                    headers=_h(db_session, ws["rising"], ws["b"]))
    assert r.status_code == 200, r.text


def test_import_inventory_refused_where_only_advisor(client, db_session, ws):
    r = client.get("/leads/import-batches", headers=_h(db_session, ws["split"], ws["b"]))
    assert r.status_code == 403, r.text
    r = client.get("/leads/import-batches", headers=_h(db_session, ws["rising"], ws["b"]))
    assert r.status_code == 200, r.text


def test_manual_lead_create_lands_in_workspace_with_its_audit(client, db_session, ws):
    r = client.post("/leads/create", headers=_h(db_session, ws["dbl"], ws["b"]),
                    json={"first_name": "New", "last_name": "Family", "phone": "2145550777"})
    assert r.status_code == 201, r.text
    db_session.expire_all()
    lead = db_session.query(Lead).filter(Lead.first_name == "New").one()
    assert lead.organization_id == ws["b"].id
    (e,) = _audit(db_session, "lead.create_manual")
    assert e.organization_id == ws["b"].id


def test_workspace_admin_may_edit_colleagues_lead_home_admin_advisor_may_not(client,
                                                                             db_session, ws):
    lb = _lead(db_session, ws["b"], ws["b_adv"])
    r = client.patch("/leads/%s" % lb.id, headers=_h(db_session, ws["rising"], ws["b"]),
                     json={"first_name": "Edited"})
    assert r.status_code == 200, r.text
    (e,) = _audit(db_session, "lead.update")
    assert e.organization_id == ws["b"].id
    r = client.patch("/leads/%s" % lb.id, headers=_h(db_session, ws["split"], ws["b"]),
                     json={"first_name": "pwned"})
    assert r.status_code in (403, 404), r.text
    db_session.expire_all()
    assert db_session.get(Lead, lb.id).first_name == "Edited"


def test_lead_flag_audit_carries_lead_tenant(client, db_session, ws):
    lb = _lead(db_session, ws["b"], ws["b_adv"])
    r = client.patch("/leads/%s/flag" % lb.id, headers=_h(db_session, ws["dbl"], ws["b"]),
                     json={"flag_type": "bad_email"})
    assert r.status_code == 200, r.text
    (e,) = _audit(db_session, "lead.flag.bad_email")
    assert e.organization_id == ws["b"].id


# ── calendar / availability: global role + home org ─────────────────────────

def _booking(db, lead, user):
    b = BookingLink(lead_id=lead.id, user_id=user.id, status="booked",
                    booked_time=datetime.utcnow() + timedelta(days=2))
    db.add(b)
    db.commit()
    return b


def test_calendar_events_other_tenant_advisor_is_404(client, db_session, ws):
    _booking(db_session, _lead(db_session, ws["a"], ws["a_adv"]), ws["a_adv"])
    r = client.get("/calendar/events?advisor_id=%s" % ws["a_adv"].id,
                   headers=_h(db_session, ws["rising"], ws["b"]))
    assert r.status_code == 404, r.text
    # Home admin standing in B as an advisor gets their own view, not A's advisor.
    r = client.get("/calendar/events?advisor_id=%s" % ws["a_adv"].id,
                   headers=_h(db_session, ws["split"], ws["b"]))
    assert r.status_code == 200 and r.json() == [], r.text


def test_org_wide_views_use_workspace(client, db_session, ws):
    bb = _booking(db_session, _lead(db_session, ws["b"], ws["b_adv"]), ws["b_adv"])
    _booking(db_session, _lead(db_session, ws["a"], ws["a_adv"]), ws["a_adv"])
    h = _h(db_session, ws["rising"], ws["b"])
    r = client.get("/calendar/events?org_wide=true", headers=h)
    assert r.status_code == 200, r.text
    assert [e["id"] for e in r.json()] == [bb.id]
    r = client.get("/availability/upcoming?org_wide=true", headers=h)
    assert r.status_code == 200, r.text
    assert [e["id"] for e in r.json()] == [bb.id]
    # Home admin who is only an advisor in B: no org-wide view of anything.
    r = client.get("/calendar/events?org_wide=true", headers=_h(db_session, ws["split"], ws["b"]))
    assert r.json() == []


# ── campaign_router ─────────────────────────────────────────────────────────

def test_campaign_send_cannot_reach_home_campaign_from_workspace(client, db_session, ws):
    ca = Campaign(organization_id=ws["a"].id, name="Home A", created_by_id=ws["dbl"].id,
                  filter_criteria="{}")
    db_session.add(ca)
    db_session.commit()
    ws["b"].enabled_features = None
    db_session.commit()
    r = client.post("/campaigns/%s/send" % ca.id, headers=_h(db_session, ws["rising"], ws["b"]),
                    json={"campaign_id": ca.id, "message": "hi {first_name}",
                          "start_cadence": False})
    assert r.status_code == 404, r.text
    r = client.get("/campaigns", headers=_h(db_session, ws["rising"], ws["b"]))
    assert r.status_code == 200, r.text
    assert r.json() == [] or all(c["id"] != ca.id for c in r.json())


# ── auto_send_router ────────────────────────────────────────────────────────

def test_auto_send_enqueue_files_draft_in_the_leads_workspace(client, db_session, ws):
    lb = _lead(db_session, ws["b"], ws["b_adv"])
    h = _h(db_session, ws["dbl"], ws["b"])
    r = client.post("/auto-send/enqueue", headers=h,
                    json={"lead_id": lb.id, "message": "Checking in"})
    assert r.status_code == 200, r.text
    from app.routers.auto_send_router import AutoSendItem
    db_session.expire_all()
    (item,) = db_session.query(AutoSendItem).all()
    assert item.organization_id == ws["b"].id
    # Home A's queue does not show it.
    r = client.get("/auto-send/queue", headers=_h(db_session, ws["dbl"]))
    assert r.status_code == 200 and lb.id not in json.dumps(r.json())


# ── contacts_router (/crm/contacts) ─────────────────────────────────────────

@pytest.fixture()
def crm_contacts_raw(db_session):
    db_session.execute(text(
        "CREATE TABLE IF NOT EXISTS crm_contact_notes (id VARCHAR PRIMARY KEY, "
        "contact_id VARCHAR NOT NULL, created_by_id VARCHAR, content TEXT NOT NULL, "
        "created_at TIMESTAMP)"))
    db_session.commit()


def test_crm_contacts_hard_delete_needs_workspace_manager(client, db_session, ws,
                                                          crm_contacts_raw):
    from app.models.models import CRMContact
    cb = CRMContact(organization_id=ws["b"].id, first_name="B", last_name="Row")
    ca = CRMContact(organization_id=ws["a"].id, first_name="A", last_name="Row")
    db_session.add_all([cb, ca])
    db_session.commit()
    cb_id, ca_id = cb.id, ca.id
    # Home admin who is an advisor in B: refused.
    r = client.delete("/crm/contacts/%s" % cb_id, headers=_h(db_session, ws["split"], ws["b"]))
    assert r.status_code == 403, r.text
    # Workspace admin: A's row is not in B.
    r = client.delete("/crm/contacts/%s" % ca_id, headers=_h(db_session, ws["rising"], ws["b"]))
    assert r.status_code == 404, r.text
    r = client.delete("/crm/contacts/%s" % cb_id, headers=_h(db_session, ws["rising"], ws["b"]))
    assert r.status_code == 204, r.text
    (e,) = _audit(db_session, "crm_contact.deleted")
    assert e.organization_id == ws["b"].id
    db_session.expire_all()
    assert db_session.get(CRMContact, ca_id) is not None
    assert db_session.get(CRMContact, cb_id) is None


# ── ai_conversation_router: audit tenant ────────────────────────────────────

def test_ai_conversation_pause_audits_lead_tenant(client, db_session, ws):
    lb = _lead(db_session, ws["b"], ws["b_adv"])
    r = client.post("/ai-conversation/pause", headers=_h(db_session, ws["dbl"], ws["b"]),
                    json={"lead_id": lb.id})
    assert r.status_code == 200, r.text
    (e,) = _audit(db_session, "ai_conversation.paused")
    assert e.organization_id == ws["b"].id


# ── pipeline_router ─────────────────────────────────────────────────────────

def test_pipeline_conversations_scoped_to_workspace_and_workspace_role(client, db_session, ws):
    from app.models.models import PipelineConversation
    lb = _lead(db_session, ws["b"], ws["b_adv"])
    la = _lead(db_session, ws["a"], ws["a_adv"])
    pb = PipelineConversation(organization_id=ws["b"].id, lead_id=lb.id, advisor_id=ws["b_adv"].id)
    pa = PipelineConversation(organization_id=ws["a"].id, lead_id=la.id, advisor_id=ws["a_adv"].id)
    db_session.add_all([pb, pa])
    db_session.commit()
    r = client.get("/pipeline/conversations", headers=_h(db_session, ws["rising"], ws["b"]))
    assert r.status_code == 200, r.text
    assert [c["pipeline_id"] for c in r.json()] == [pb.id]
    # Home admin who is an advisor in B sees only their own (none).
    r = client.get("/pipeline/conversations", headers=_h(db_session, ws["split"], ws["b"]))
    assert r.status_code == 200 and r.json() == [], r.text
    # ...and cannot dismiss a colleague's conversation there.
    r = client.post("/pipeline/dismiss/%s" % pb.id, headers=_h(db_session, ws["split"], ws["b"]))
    assert r.status_code == 404, r.text
