# -*- coding: utf-8 -*-
"""EvoSys Gold & Jewelry program - /jewelry-ops/*.

Covers: explicit feature gate (a NULL allow-list is NOT jewelry), god setup,
launcher routing (program_route / experience), tenant isolation, owner /
manager / staff / view-only roles, real CRUD persistence (customers, notes,
follow-ups, opt-outs, transactions), import preview + confirm with duplicate
and permission handling, export, appointments (overlap refusal, reschedule,
cancel, completion, reminder jobs skip them), campaigns (recipient
eligibility, schedule/pause/resume/cancel, approval, SEND IS REFUSED),
inventory and inquiries, reports that count only stored rows, and the AI
receptionist in test mode (price hand-off, emergency stop). Nothing reaches a
provider: the AI gateway is patched and campaigns never send.
"""
import io
import itertools
import json
import uuid
from datetime import datetime, timedelta

import pytest

from app.models.jewelry_models import JewelryAppointmentDetail, JewelryProfile
from app.models.models import BookingLink, Lead, Organization, SuppressionEntry, User
from app.models.sales_models import Membership
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
FEATS = ["leads", "imports", "booking", "calendar", "availability", "reports", "compliance", "users",
         "branding_settings", "sms", "email", "jewelry_program"]


def org(db, name, features=tuple(FEATS), industry="jewelry"):
    o = Organization(name=name, slug="jw-%s" % uuid.uuid4().hex[:8], plan="enterprise", industry=industry,
                     is_active=True, timezone="America/New_York",
                     enabled_features=json.dumps(list(features)) if features is not None else None)
    db.add(o)
    db.commit()
    return o


def user(db, o, role, label):
    u = User(organization_id=o.id if o else None, email="%s-%d@jw.test" % (label, next(_SEQ)),
             password_hash=hash_password("Pass12345!"), full_name=label.title(), role=role,
             is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def h(db, u, ws=None):
    hd = {"Authorization": "Bearer %s" % create_access_token(u, db)}
    if ws:
        hd["X-Workspace-Id"] = ws
    return hd


@pytest.fixture()
def world(db_session):
    db = db_session
    a = org(db, "Mountain Gold Test Co")
    b = org(db, "Other Coin Test Shop")
    god = user(db, None, "god_admin", "owner")
    a_owner = user(db, a, "org_admin", "aowner")
    a_mgr = user(db, a, "manager", "amanager")
    a_staff = user(db, a, "advisor", "astaff")
    a_view = user(db, a, "advisor", "aview")
    db.add(Membership(user_id=a_view.id, scope_type="customer_org", scope_id=a.id, role="spectator", is_active=True))
    db.commit()
    b_owner = user(db, b, "org_admin", "bowner")
    from app.services.jewelry import setup as S
    S.setup(db, a, god.id)
    S.setup(db, b, god.id)
    return dict(db=db, a=a, b=b, god=god, a_owner=a_owner, a_mgr=a_mgr, a_staff=a_staff, a_view=a_view,
                b_owner=b_owner)


def mk(client, db, u, **kw):
    body = {"first_name": "Pat", "last_name": "Customer %d" % next(_SEQ)}
    body.update(kw)
    r = client.post("/jewelry-ops/customers", json=body, headers=h(db, u))
    assert r.status_code == 201, r.text
    return r.json()


# ── gate, setup, launcher ──────────────────────────────────────────────────
def test_feature_required_and_null_allow_list_is_not_jewelry(client, db_session):
    db = db_session
    plain = org(db, "Plain", features=("leads",))
    legacy = org(db, "Legacy", features=None)
    u1, u2 = user(db, plain, "org_admin", "plain"), user(db, legacy, "org_admin", "legacy")
    assert client.get("/jewelry-ops/status", headers=h(db, u1)).status_code in (402, 403)
    r = client.get("/jewelry-ops/status", headers=h(db, u2))
    assert r.status_code == 403 and "Gold & Jewelry" in r.json()["detail"]


def test_god_setup_grants_features_and_sends_nothing(client, db_session):
    db = db_session
    o = org(db, "Fresh Store", features=("leads",), industry="generic")
    god = user(db, None, "god_admin", "god")
    staff = user(db, o, "org_admin", "fresh")
    assert client.post("/god/jewelry/setup", json={"organization_id": o.id}, headers=h(db, staff)).status_code == 403
    r = client.post("/god/jewelry/setup", json={"organization_id": o.id}, headers=h(db, god))
    assert r.status_code == 200, r.text
    db.refresh(o)
    assert "jewelry_program" in json.loads(o.enabled_features) and o.industry == "jewelry"
    assert r.json()["texts_on"] is False and r.json()["ai_mode"] == "test"
    assert db.query(Lead).filter(Lead.organization_id == o.id).count() == 0
    assert db.query(User).filter(User.organization_id == o.id).count() == 1     # nobody invented
    # idempotent
    assert client.post("/god/jewelry/setup", json={"organization_id": o.id}, headers=h(db, god)).status_code == 200
    assert db.query(JewelryProfile).filter(JewelryProfile.organization_id == o.id).count() == 1


def test_launcher_routes_member_to_jewelry_program(client, world):
    db, a = world["db"], world["a"]
    home = org(db, "Platform Home", features=("leads",), industry="generic")
    p = user(db, home, "org_admin", "platform")
    db.add(Membership(user_id=p.id, scope_type="customer_org", scope_id=home.id, role="org_admin", is_active=True))
    db.add(Membership(user_id=p.id, scope_type="customer_org", scope_id=a.id, role="org_admin", is_active=True))
    db.commit()
    r = client.get("/auth/my-contexts", headers=h(db, p))
    assert r.status_code == 200, r.text
    entry = next(w for w in r.json()["workspace_contexts"] if w["organization_id"] == a.id)
    assert entry["program_route"] == "/program/jewelry" and entry["experience"] == "jewelry"
    # and the member can work inside it through the workspace header
    r = client.get("/jewelry-ops/status", headers=h(db, p, ws=a.id))
    assert r.status_code == 200 and r.json()["organization_id"] == a.id and r.json()["is_owner"] is True


def test_identity_protected_funeral_home_with_key_is_not_jewelry(client, db_session):
    from app.services.program_experience import jewelry_identity
    db = db_session
    f = org(db, "A Funeral Home", industry="funeral")
    assert jewelry_identity(f) is False


# ── isolation & roles ──────────────────────────────────────────────────────
def test_other_business_ids_are_404_and_lists_are_isolated(client, world):
    db = world["db"]
    c = mk(client, db, world["a_owner"])
    rb = client.get("/jewelry-ops/customers/%s" % c["id"], headers=h(db, world["b_owner"]))
    assert rb.status_code == 404
    lst = client.get("/jewelry-ops/customers", headers=h(db, world["b_owner"])).json()
    assert lst["total"] == 0
    r = client.patch("/jewelry-ops/customers/%s" % c["id"], json={"first_name": "X"}, headers=h(db, world["b_owner"]))
    assert r.status_code == 404


def test_staff_scope_and_manager_only_actions(client, world):
    db = world["db"]
    staff, mgr = world["a_staff"], world["a_mgr"]
    mine = mk(client, db, staff)                      # staff-created -> assigned to staff
    theirs = mk(client, db, mgr)                      # manager-created -> unassigned
    lst = client.get("/jewelry-ops/customers", headers=h(db, staff)).json()
    assert [x["id"] for x in lst["customers"]] == [mine["id"]]
    assert client.get("/jewelry-ops/customers/%s" % theirs["id"], headers=h(db, staff)).status_code == 404
    assert client.get("/jewelry-ops/customers", headers=h(db, mgr)).json()["total"] == 2
    for path, method in (("/jewelry-ops/campaigns", "get"), ("/jewelry-ops/customers/export", "get"),
                         ("/jewelry-ops/reports", "get"), ("/jewelry-ops/team", "get")):
        assert getattr(client, method)(path, headers=h(db, staff)).status_code == 403, path
    assert client.post("/jewelry-ops/campaigns", json={"kind": "vip"}, headers=h(db, staff)).status_code == 403
    assert client.put("/jewelry-ops/profile", json={"business_name": "X"}, headers=h(db, mgr)).status_code == 403
    assert client.put("/jewelry-ops/profile", json={"business_name": "X"},
                      headers=h(db, world["a_owner"])).status_code == 200


def test_view_only_cannot_write(client, world):
    db = world["db"]
    r = client.post("/jewelry-ops/customers", json={"first_name": "No"}, headers=h(db, world["a_view"], ws=world["a"].id))
    assert r.status_code == 403
    assert client.get("/jewelry-ops/status", headers=h(db, world["a_view"], ws=world["a"].id)).status_code == 200


# ── customer vault persistence ─────────────────────────────────────────────
def test_customer_crud_notes_followups_history_and_opt_out(client, world):
    db, owner = world["db"], world["a_owner"]
    c = mk(client, db, owner, phone="(276) 555-0142", email="pat@example.com",
           segments=["gold_seller", "vip", "bogus"], interests=["Gold chains", "gold chains", "Coins"])
    assert c["phone"] == "(276) 555-0142" and c["assigned_segments"] == ["gold_seller", "vip"]
    assert c["interests"] == ["Gold chains", "Coins"] and c["allow_sms"] is False
    dup = client.post("/jewelry-ops/customers", json={"first_name": "Dup", "phone": "2765550142"},
                      headers=h(db, owner))
    assert dup.status_code == 409 and dup.json()["detail"]["duplicate_id"] == c["id"]
    # permission needs a source
    r = client.patch("/jewelry-ops/customers/%s" % c["id"], json={"allow_sms": True}, headers=h(db, owner))
    assert r.status_code == 400
    r = client.patch("/jewelry-ops/customers/%s" % c["id"],
                     json={"allow_sms": True, "permission_source": "Signed in-store form 11/2026", "city": "Abingdon"},
                     headers=h(db, owner))
    assert r.status_code == 200 and r.json()["allow_sms"] is True and r.json()["city"] == "Abingdon"
    assert client.post("/jewelry-ops/customers/%s/notes" % c["id"], json={"body": "Likes 18K"},
                       headers=h(db, owner)).status_code == 201
    r = client.post("/jewelry-ops/customers/%s/follow-ups" % c["id"],
                    json={"title": "Call about chain", "due_at": "2026-01-02T10:00"}, headers=h(db, owner))
    assert r.status_code == 201
    hist = client.get("/jewelry-ops/customers/%s/history" % c["id"], headers=h(db, owner)).json()
    assert hist["notes"][0]["body"] == "Likes 18K" and hist["follow_ups"][0]["title"] == "Call about chain"
    assert client.get("/jewelry-ops/customers/%s" % c["id"], headers=h(db, owner)).json()["follow_up"] == "follow_up_due"
    fu = client.get("/jewelry-ops/follow-ups", headers=h(db, owner)).json()["follow_ups"]
    assert fu and fu[0]["overdue"] is True
    assert client.post("/jewelry-ops/follow-ups/%s/complete" % fu[0]["id"], headers=h(db, owner)).status_code == 200
    # opt-out is one-way and suppresses the phone
    r = client.post("/jewelry-ops/customers/%s/opt-out" % c["id"], json={}, headers=h(db, owner))
    assert r.status_code == 200 and r.json()["opted_out"] is True and r.json()["allow_sms"] is False
    assert db.query(SuppressionEntry).filter(SuppressionEntry.organization_id == world["a"].id,
                                             SuppressionEntry.phone == "+12765550142").count() == 1
    r = client.patch("/jewelry-ops/customers/%s" % c["id"], json={"allow_sms": True, "permission_source": "x"},
                     headers=h(db, owner))
    assert r.status_code == 409
    # search
    assert client.get("/jewelry-ops/customers?q=0142", headers=h(db, owner)).json()["total"] == 1
    assert client.get("/jewelry-ops/customers?segment=vip", headers=h(db, owner)).json()["total"] == 1
    assert client.get("/jewelry-ops/customers?interest=coin", headers=h(db, owner)).json()["total"] == 1


def test_dashboard_has_no_invented_numbers_and_counts_real_rows(client, world):
    db, owner = world["db"], world["a_owner"]
    d = client.get("/jewelry-ops/dashboard", headers=h(db, owner)).json()
    assert d["customers"]["total"] == 0 and d["transactions"]["revenue"] == 0 and d["appointments"]["upcoming"] == 0
    assert d["transactions"]["pos_connected"] is False and d["campaigns"]["delivery_connected"] is False
    assert all(s["revenue"] == 0 for s in d["series"]) and d["definitions"]["revenue"]
    c = mk(client, db, owner)
    today = datetime.utcnow().date().isoformat()
    for kind, amt in (("sale", 450.0), ("purchase", 300.0), ("trade", 50.0)):
        r = client.post("/jewelry-ops/customers/%s/transactions" % c["id"],
                        json={"kind": kind, "amount": amt, "occurred_on": today}, headers=h(db, owner))
        assert r.status_code == 201, r.text
    assert client.post("/jewelry-ops/customers/%s/transactions" % c["id"],
                       json={"kind": "sale", "amount": -5}, headers=h(db, owner)).status_code == 400
    d = client.get("/jewelry-ops/dashboard?period=7d", headers=h(db, owner)).json()
    assert d["customers"]["total"] == 1 and d["customers"]["returning"] == 1
    assert d["transactions"]["count"] == 3 and d["transactions"]["revenue"] == 500.0
    assert d["transactions"]["purchases"] == 300.0
    assert "previous" in client.get("/jewelry-ops/customers/%s" % c["id"], headers=h(db, owner)).json()["segments"]


def test_reactivation_counted_from_a_real_gap(client, world):
    db, owner = world["db"], world["a_owner"]
    c = mk(client, db, owner)
    old = (datetime.utcnow() - timedelta(days=300)).date().isoformat()
    new = (datetime.utcnow() - timedelta(days=2)).date().isoformat()
    for d_ in (old, new):
        client.post("/jewelry-ops/customers/%s/transactions" % c["id"], json={"amount": 100, "occurred_on": d_},
                    headers=h(db, owner))
    d = client.get("/jewelry-ops/dashboard?period=30d", headers=h(db, owner)).json()
    assert d["customers"]["reactivated"] == 1


# ── import / export ────────────────────────────────────────────────────────
CSV = (b"Name,Phone,Email,Segment,Interests,SMS Opt In\n"
       b"Ada Lovelace,276-555-0101,ada@example.com,VIP; Coins,Morgan dollars,yes\n"
       b"Ben Franklin,2765550102,ben@example.com,Gold Seller,,no\n"
       b"Dup Row,276-555-0101,,,,\n"
       b"Bad Phone,12345,,jewelry,,\n"
       b",,,,,\n")


def test_import_preview_then_confirm(client, world):
    db, owner = world["db"], world["a_owner"]
    files = {"file": ("customers.csv", io.BytesIO(CSV), "text/csv")}
    r = client.post("/jewelry-ops/customers/import/preview", files=files, headers=h(db, owner))
    assert r.status_code == 200, r.text
    p = r.json()
    by = {x["first_name"]: x for x in p["rows"]}
    assert by["Ada"]["segments"] == ["vip", "coin_collector"] and by["Ada"]["sms_permission"] is True
    assert by["Ben"]["sms_permission"] is False and by["Dup"]["status"] == "duplicate"
    assert "Phone isn't a valid US number" in by["Bad"]["issues"]
    assert db.query(Lead).filter(Lead.organization_id == world["a"].id).count() == 0      # preview writes nothing
    files = {"file": ("customers.csv", io.BytesIO(CSV), "text/csv")}
    r = client.post("/jewelry-ops/customers/import/confirm", files=files, headers=h(db, owner))
    assert r.status_code == 200 and r.json()["created"] == 3 and r.json()["messages_sent"] == 0
    leads = db.query(Lead).filter(Lead.organization_id == world["a"].id).all()
    ada = next(l for l in leads if l.first_name == "Ada")
    assert ada.allow_sms is True and ada.sms_consent_source == "import:customers.csv" and ada.permission_review
    ben = next(l for l in leads if l.first_name == "Ben")
    assert not ben.allow_sms and not ben.allow_email
    # importing again: everything is a duplicate now
    files = {"file": ("customers.csv", io.BytesIO(CSV), "text/csv")}
    p2 = client.post("/jewelry-ops/customers/import/preview", files=files, headers=h(db, owner)).json()
    assert p2["summary"]["ready"] == 1 and p2["summary"]["duplicate"] == 3      # "Bad" has no phone dup
    files = {"file": ("customers.csv", io.BytesIO(CSV), "text/csv")}
    assert client.post("/jewelry-ops/customers/import/preview", files=files,
                       headers=h(db, world["a_staff"])).status_code == 403
    exp = client.get("/jewelry-ops/customers/export", headers=h(db, owner))
    assert exp.status_code == 200 and "Ada" in exp.text and exp.headers["content-type"].startswith("text/csv")


def test_import_rejects_unknown_file_type(client, world):
    db = world["db"]
    files = {"file": ("x.pdf", io.BytesIO(b"%PDF"), "application/pdf")}
    assert client.post("/jewelry-ops/customers/import/preview", files=files,
                       headers=h(db, world["a_owner"])).status_code == 400


# ── appointments ───────────────────────────────────────────────────────────
def test_appointments_persist_refuse_overlap_and_skip_reminder_jobs(client, world):
    db, owner, staff = world["db"], world["a_owner"], world["a_staff"]
    c = mk(client, db, owner)
    start = (datetime.utcnow() + timedelta(days=3)).replace(hour=14, minute=0, second=0, microsecond=0)
    body = {"lead_id": c["id"], "type": "Gold Evaluation", "starts_at": start.isoformat(timespec="minutes"),
            "staff_id": staff.id, "duration_minutes": 30, "internal_notes": "Two rings", "follow_up_days": 3}
    r = client.post("/jewelry-ops/appointments", json=body, headers=h(db, owner))
    assert r.status_code == 201, r.text
    appt = r.json()
    b = db.query(BookingLink).filter(BookingLink.id == appt["id"]).first()
    assert b.reminder_24hr_sent and b.reminder_1hr_sent and b.confirmation_sent and b.review_request_sent_at
    overlap = dict(body, starts_at=(start + timedelta(minutes=15)).isoformat(timespec="minutes"))
    assert client.post("/jewelry-ops/appointments", json=overlap, headers=h(db, owner)).status_code == 409
    after = dict(body, starts_at=(start + timedelta(minutes=30)).isoformat(timespec="minutes"))
    assert client.post("/jewelry-ops/appointments", json=after, headers=h(db, owner)).status_code == 201
    bad = dict(body, type="Haircut")
    assert client.post("/jewelry-ops/appointments", json=bad, headers=h(db, owner)).status_code == 400
    # reschedule into the other one is refused; elsewhere is fine
    r = client.patch("/jewelry-ops/appointments/%s" % appt["id"],
                     json={"starts_at": (start + timedelta(minutes=40)).isoformat(timespec="minutes")}, headers=h(db, owner))
    assert r.status_code == 409
    r = client.patch("/jewelry-ops/appointments/%s" % appt["id"],
                     json={"starts_at": (start + timedelta(days=1)).isoformat(timespec="minutes")}, headers=h(db, owner))
    assert r.status_code == 200
    r = client.patch("/jewelry-ops/appointments/%s" % appt["id"], json={"status": "completed"}, headers=h(db, owner))
    assert r.status_code == 200 and r.json()["status"] == "completed"
    lead = db.query(Lead).filter(Lead.id == c["id"]).first()
    db.refresh(lead)
    assert json.loads(lead.custom_fields)["jw_last_visit"] == (start + timedelta(days=1)).date().isoformat()
    # staff sees their own appointments; listing a range works
    lst = client.get("/jewelry-ops/appointments?start=%s&end=%s" % (
        start.date().isoformat() + "T00:00", (start + timedelta(days=5)).date().isoformat() + "T00:00"),
        headers=h(db, staff)).json()
    assert len(lst["appointments"]) == 2 and lst["reminders_enabled"] is False
    # cancel
    other = lst["appointments"][0]["id"] if lst["appointments"][0]["id"] != appt["id"] else lst["appointments"][1]["id"]
    r = client.patch("/jewelry-ops/appointments/%s" % other, json={"status": "cancelled"}, headers=h(db, owner))
    assert r.status_code == 200
    assert db.query(BookingLink).filter(BookingLink.id == other).first().status == "cancelled"
    # other business can't touch it
    assert client.patch("/jewelry-ops/appointments/%s" % other, json={"status": "confirmed"},
                        headers=h(db, world["b_owner"])).status_code == 404


def test_staff_cannot_book_for_someone_else(client, world):
    db, staff, mgr = world["db"], world["a_staff"], world["a_mgr"]
    c = mk(client, db, staff)
    start = (datetime.utcnow() + timedelta(days=2)).replace(hour=11, minute=0, second=0, microsecond=0)
    r = client.post("/jewelry-ops/appointments", json={"lead_id": c["id"], "type": "Jewelry Consultation",
                                                      "starts_at": start.isoformat(timespec="minutes"),
                                                      "staff_id": mgr.id}, headers=h(db, staff))
    assert r.status_code == 403


# ── campaigns ──────────────────────────────────────────────────────────────
def test_campaign_lifecycle_eligibility_and_send_is_refused(client, world):
    db, owner, mgr = world["db"], world["a_owner"], world["a_mgr"]
    ok = mk(client, db, owner, phone="2765550201", segments=["vip"], allow_sms=True, permission_source="Form")
    mk(client, db, owner, phone="2765550202", segments=["vip"])                       # no permission
    out = mk(client, db, owner, phone="2765550203", segments=["vip"], allow_sms=True, permission_source="Form")
    client.post("/jewelry-ops/customers/%s/opt-out" % out["id"], json={}, headers=h(db, owner))
    mk(client, db, owner, phone="2765550204", segments=["coin_collector"], allow_sms=True, permission_source="Form")
    r = client.post("/jewelry-ops/campaigns", json={"kind": "vip", "segments": ["vip"]}, headers=h(db, mgr))
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert "{business_name}" not in (r.json()["sms_body"] or "")
    d = client.get("/jewelry-ops/campaigns/%s?preview_lead_id=%s" % (cid, ok["id"]), headers=h(db, mgr)).json()
    rec = d["recipients"]
    assert rec["matched"] == 3 and rec["eligible"] == 1 and rec["sample"][0]["id"] == ok["id"]
    assert rec["excluded"]["opted_out"] == 1 and rec["excluded"]["no_sms_permission"] == 1
    assert d["preview"]["sms"].startswith("Pat,") and "STOP" in d["preview"]["sms"]
    assert d["readiness"]["ready"] is False
    # schedule / pause / resume / cancel
    past = (datetime.utcnow() - timedelta(days=1)).isoformat(timespec="minutes")
    assert client.post("/jewelry-ops/campaigns/%s/schedule" % cid, json={"scheduled_for": past},
                       headers=h(db, mgr)).status_code == 400
    future = (datetime.utcnow() + timedelta(days=4)).isoformat(timespec="minutes")
    r = client.post("/jewelry-ops/campaigns/%s/schedule" % cid, json={"scheduled_for": future}, headers=h(db, mgr))
    assert r.status_code == 200 and r.json()["status"] == "scheduled" and r.json()["sending_locked"] is True
    assert client.post("/jewelry-ops/campaigns/%s/pause" % cid, headers=h(db, mgr)).json()["status"] == "paused"
    assert client.post("/jewelry-ops/campaigns/%s/resume" % cid, headers=h(db, mgr)).json()["status"] == "scheduled"
    # approval is owner-only
    assert client.post("/jewelry-ops/campaigns/%s/approve" % cid, headers=h(db, mgr)).status_code == 403
    assert client.post("/jewelry-ops/campaigns/%s/approve" % cid, headers=h(db, owner)).json()["approved"] is True
    # SEND IS ALWAYS REFUSED - and nothing reached the message tables
    from app.models.models import EmailMessage, Message
    before = db.query(Message).count() + db.query(EmailMessage).count()
    r = client.post("/jewelry-ops/campaigns/%s/send" % cid, headers=h(db, owner))
    assert r.status_code == 409 and "Nothing was sent" in r.json()["detail"]["message"]
    assert db.query(Message).count() + db.query(EmailMessage).count() == before
    # editing clears approval and returns to draft
    r = client.patch("/jewelry-ops/campaigns/%s" % cid, json={"sms_body": "New copy {first_name}"}, headers=h(db, mgr))
    assert r.json()["status"] == "draft" and r.json()["approved"] is False
    assert client.post("/jewelry-ops/campaigns/%s/cancel" % cid, headers=h(db, mgr)).json()["status"] == "cancelled"
    assert client.get("/jewelry-ops/campaigns/%s" % cid, headers=h(db, world["b_owner"])).status_code == 404


# ── inventory ──────────────────────────────────────────────────────────────
def test_inventory_items_interest_and_promotional_draft(client, world):
    db, owner, staff = world["db"], world["a_owner"], world["a_staff"]
    collector = mk(client, db, owner, segments=["coin_collector"])
    r = client.post("/jewelry-ops/items", json={"name": "1921 Morgan dollar", "category": "coins",
                                               "interest_tags": ["Morgan"], "featured": True}, headers=h(db, owner))
    assert r.status_code == 201, r.text
    it = r.json()
    assert it["matching_customers"] == 1 and it["featured"] is True
    assert client.post("/jewelry-ops/items", json={"name": "x", "category": "cars"}, headers=h(db, owner)).status_code == 400
    assert client.post("/jewelry-ops/items", json={"name": "x"}, headers=h(db, staff)).status_code == 403
    r = client.post("/jewelry-ops/items/%s/interest" % it["id"], json={"lead_id": collector["id"], "note": "Wants it"},
                    headers=h(db, owner))
    assert r.status_code == 201
    assert client.get("/jewelry-ops/items/%s/inquiries" % it["id"], headers=h(db, owner)).json()["inquiries"][0]["note"] == "Wants it"
    r = client.patch("/jewelry-ops/items/%s" % it["id"], json={"status": "sold"}, headers=h(db, owner))
    assert r.json()["status"] == "sold" and r.json()["inquiries"] == 1
    r = client.post("/jewelry-ops/items/%s/promote" % it["id"], headers=h(db, owner))
    assert r.status_code == 201 and r.json()["kind"] == "coin_alerts" and r.json()["segments"] == ["coin_collector"]
    assert client.get("/jewelry-ops/items", headers=h(db, world["b_owner"])).json()["items"] == []


def test_item_photo_upload_and_fetch(client, world, monkeypatch):
    db, owner = world["db"], world["a_owner"]
    monkeypatch.setenv("MEDIA_STORAGE_BACKEND", "database")
    it = client.post("/jewelry-ops/items", json={"name": "Ring"}, headers=h(db, owner)).json()
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
           b"\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")
    r = client.post("/jewelry-ops/items/%s/photos" % it["id"], files={"file": ("r.png", io.BytesIO(png), "image/png")},
                    headers=h(db, owner))
    if r.status_code == 503:
        pytest.skip("media storage not configured in this environment")
    assert r.status_code == 201, r.text
    got = client.get(r.json()["url"], headers=h(db, owner))
    assert got.status_code == 200 and got.content == png
    assert client.get(r.json()["url"], headers=h(db, world["b_owner"])).status_code == 404


# ── reports ────────────────────────────────────────────────────────────────
def test_reports_are_manager_only_and_campaign_results_count_real_rows(client, world):
    db, owner = world["db"], world["a_owner"]
    c = mk(client, db, owner)
    camp = client.post("/jewelry-ops/campaigns", json={"kind": "gold_buying", "marketing_cost": 120},
                       headers=h(db, owner)).json()
    client.post("/jewelry-ops/customers/%s/transactions" % c["id"],
                json={"amount": 900, "campaign_id": camp["id"], "kind": "sale"}, headers=h(db, owner))
    rep = client.get("/jewelry-ops/reports?period=30d", headers=h(db, owner)).json()
    row = next(x for x in rep["campaign_rows"] if x["id"] == camp["id"])
    assert row["results"]["recorded_revenue"] == 900 and row["results"]["sent"] == 0
    assert row["results"]["delivery_connected"] is False and rep["campaigns"]["marketing_cost"] == 120
    other = client.post("/jewelry-ops/campaigns", json={"kind": "vip"}, headers=h(db, world["b_owner"])).json()
    assert client.post("/jewelry-ops/customers/%s/transactions" % c["id"], json={"amount": 1, "campaign_id": other["id"]},
                       headers=h(db, owner)).status_code == 404


# ── AI receptionist (test mode) ────────────────────────────────────────────
def test_ai_never_quotes_prices_and_honours_emergency_stop(client, world, monkeypatch):
    db, owner, staff = world["db"], world["a_owner"], world["a_staff"]
    from app.services import ai_gateway

    def refuse(**kw):
        raise ai_gateway.AIDisabled("off in tests")
    monkeypatch.setattr(ai_gateway, "chat_completion", refuse)
    r = client.post("/jewelry-ops/ai/ask", json={"question": "How much will you pay for my 14k ring?"},
                    headers=h(db, staff))
    assert r.status_code == 200 and r.json()["needs_human"] is True and "$" not in r.json()["answer"]
    r = client.post("/jewelry-ops/ai/ask", json={"question": "Can I schedule an evaluation?"}, headers=h(db, staff))
    assert r.json()["source"] == "fallback" and "visit" in r.json()["answer"]
    r = client.post("/jewelry-ops/ai/ask", json={"question": "Are you open Sundays?"}, headers=h(db, staff))
    assert r.json()["needs_human"] is True                  # hours not confirmed -> no invented hours

    class Fake:
        class choices:  # noqa: N801
            pass
    def fake_ai(**kw):
        class M:
            content = "Gold is $2,400 per ounce today."
        class Ch:
            message = M()
        class R:
            choices = [Ch()]
        return R()
    monkeypatch.setattr(ai_gateway, "chat_completion", fake_ai)
    r = client.post("/jewelry-ops/ai/ask", json={"question": "Tell me about your store"}, headers=h(db, staff))
    assert "$" not in r.json()["answer"] and r.json()["needs_human"] is True     # post-check caught it
    # emergency stop: managers only
    assert client.post("/jewelry-ops/ai/controls", json={"paused": True}, headers=h(db, staff)).status_code == 403
    assert client.post("/jewelry-ops/ai/controls", json={"paused": True}, headers=h(db, owner)).json()["paused"] is True
    r = client.post("/jewelry-ops/ai/ask", json={"question": "Hello"}, headers=h(db, staff))
    assert r.json()["answered_by"] == "blocked"
    assert client.post("/jewelry-ops/ai/controls", json={"mode": "live"}, headers=h(db, owner)).status_code == 400
    ov = client.post("/jewelry-ops/ai/exchanges/%s/override" % r.json()["id"], json={"answer": "We'll call you."},
                     headers=h(db, owner))
    assert ov.status_code == 200 and ov.json()["answered_by"] == "operator"
    o = client.get("/jewelry-ops/ai", headers=h(db, owner)).json()
    assert o["live_available"] is False and o["summary"]["blocked"] >= 1
    acts = client.get("/jewelry-ops/activity", headers=h(db, owner)).json()["entries"]
    assert any(a["action"] == "jewelry.ai.controls" for a in acts)


def test_approved_answers_are_used_verbatim(client, world, monkeypatch):
    db, owner = world["db"], world["a_owner"]
    from app.services import ai_gateway
    monkeypatch.setattr(ai_gateway, "chat_completion", lambda **kw: (_ for _ in ()).throw(ai_gateway.AIDisabled("x")))
    client.put("/jewelry-ops/profile", json={"approved_answers": [{"q": "Do you buy class rings?",
                                                                   "a": "Yes, we evaluate class rings in store."}]},
               headers=h(db, owner))
    r = client.post("/jewelry-ops/ai/ask", json={"question": "do you buy class rings"}, headers=h(db, owner))
    assert r.json()["answer"] == "Yes, we evaluate class rings in store." and r.json()["source"] == "approved_answer"
