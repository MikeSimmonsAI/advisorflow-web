# -*- coding: utf-8 -*-
"""ATLANTIS sales pipeline board + enrollment truth + energy-ops count scope.

Board cards link to real records; age in stage reads a real timestamp (or says
"since created"); losing a lead requires and stores a reason; enrollment
records enrolled_at and the Overview counts it; energy-ops queue counts equal
list totals for an advisor and a manager; another tenant's lead is a 404.
Nothing is sent anywhere.
"""
import itertools
import json
import uuid
from datetime import datetime, timedelta

import pytest

import app.models.energy_models  # noqa: F401
from app.models.models import AuditLogEntry, BookingLink, Lead, Organization, User
from app.models.work_models import LeadTask
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


@pytest.fixture(autouse=True)
def _mounted():
    from app.main import app
    from app.routers.energy_ops_router import router as ops
    from app.routers.rate_requests_router import router as rr
    paths = {getattr(r, "path", "") for r in app.routes}
    if "/energy-ops/queues" not in paths:
        app.include_router(ops)
    if "/rate-requests/summary" not in paths:
        app.include_router(rr)
    yield


def _org(db, name):
    o = Organization(name=name, slug="atp-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                     industry="energy", is_active=True)
    db.add(o)
    db.commit()
    return o


def _user(db, org, role, label):
    u = User(organization_id=org.id, email="%s-%d@atp.test" % (label, next(_SEQ)),
             password_hash=hash_password("Pass12345!"), full_name=label.title(),
             role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


def _lead(db, org, owner, tier="new_inquiry", first="Pia", last="Pipe", **kw):
    l = Lead(organization_id=org.id, assigned_to_id=owner.id if owner else None,
             first_name=first, last_name=last, tier=tier, status=kw.pop("status", "new"), **kw)
    db.add(l)
    db.commit()
    return l


@pytest.fixture()
def w(db_session):
    db = db_session
    org = _org(db, "Atlantis Pipeline Power")
    other = _org(db, "Other Pipeline Tenant")
    return dict(db=db, org=org, other=other,
                admin=_user(db, org, "org_admin", "padmin"),
                adv=_user(db, org, "advisor", "padv"),
                adv2=_user(db, org, "advisor", "padvtwo"),
                fadmin=_user(db, other, "org_admin", "pfadmin"))


def _card(board, lead_id):
    for col in board["columns"]:
        for c in col["cards"]:
            if c["id"] == lead_id:
                return col["key"], c
    for c in board["lost"]["cards"]:
        if c["id"] == lead_id:
            return "__lost__", c
    return None, None


# ── board: stages, card links and fields ────────────────────────────────────

def test_board_columns_are_org_tiers_and_cards_link_to_records(client, w):
    db, org, adv = w["db"], w["org"], w["adv"]
    l = _lead(db, org, adv, tier="rate_review", notes="Asked about fixed rates")
    db.add(LeadTask(organization_id=org.id, lead_id=l.id, title="Call back",
                    due_at=datetime.utcnow() + timedelta(days=1), assigned_to_id=adv.id))
    db.add(BookingLink(lead_id=l.id, user_id=adv.id, status="booked",
                       booked_time=datetime.utcnow() + timedelta(days=2)))
    db.commit()
    r = client.get("/pipeline/board", headers=_h(db, w["admin"]))
    assert r.status_code == 200, r.text
    b = r.json()
    keys = [c["key"] for c in b["columns"]]
    assert keys[:5] == ["new_inquiry", "rate_review", "proposal_sent", "contract_signed", "renewal_due"]
    col, c = _card(b, l.id)
    assert col == "rate_review"
    assert c["link"] == f"/leads/{l.id}"
    assert c["owner"] == "Padv"
    assert c["next_task"]["title"] == "Call back" and c["next_task"]["link"] == f"/leads/{l.id}"
    assert c["appointment"] and c["appointment"]["past"] is False
    assert c["rate_request"]["link"] == f"/rate-requests?open={l.id}"
    assert c["activity"]["link"].startswith(f"/leads/{l.id}")
    assert c["notes_preview"] == "Asked about fixed rates"
    assert c["customer"] is None and c["lost"] is None
    count = next(x["count"] for x in b["columns"] if x["key"] == "rate_review")
    assert count == 1


def test_age_in_stage_real_timestamp_and_created_fallback(client, w):
    db, org, adv = w["db"], w["org"], w["adv"]
    l = _lead(db, org, adv, tier="new_inquiry")
    # Simulate a lead from before the column shipped: no stamp, created 10 days ago.
    l.stage_entered_at = None
    l.created_at = datetime.utcnow() - timedelta(days=10)
    db.commit()
    _, c = _card(client.get("/pipeline/board", headers=_h(db, adv)).json(), l.id)
    assert c["age_basis"] == "created" and c["age_in_stage_days"] == 10
    assert c["stage_entered_at"] is None

    r = client.post(f"/pipeline/board/{l.id}/stage", headers=_h(db, adv), json={"stage": "proposal_sent"})
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["stage"] == "proposal_sent" and c["age_basis"] == "stage_entered"
    assert c["age_in_stage_days"] == 0 and c["stage_entered_at"]
    db.refresh(l)
    entered = l.stage_entered_at
    assert entered and (datetime.utcnow() - entered).total_seconds() < 60
    # Re-setting the same stage does not restart the clock.
    l.tier = "proposal_sent"
    db.commit()
    db.refresh(l)
    assert l.stage_entered_at == entered
    assert db.query(AuditLogEntry).filter(AuditLogEntry.target_id == l.id,
                                          AuditLogEntry.action == "lead.stage_moved").count() == 1
    # A stage the org does not have is refused.
    assert client.post(f"/pipeline/board/{l.id}/stage", headers=_h(db, adv),
                       json={"stage": "made_up"}).status_code == 400


def test_mark_lost_requires_reason_and_stores_it(client, w):
    db, org, adv = w["db"], w["org"], w["adv"]
    l = _lead(db, org, adv, tier="proposal_sent")
    h = _h(db, adv)
    assert client.post(f"/pipeline/board/{l.id}/lost", headers=h, json={}).status_code == 422
    assert client.post(f"/pipeline/board/{l.id}/lost", headers=h, json={"reason": "bogus"}).status_code == 400
    assert client.post(f"/pipeline/board/{l.id}/lost", headers=h, json={"reason": "other"}).status_code == 400
    r = client.post(f"/pipeline/board/{l.id}/lost", headers=h,
                    json={"reason": "competitor", "detail": "Went with incumbent"})
    assert r.status_code == 200, r.text
    assert r.json()["lost"]["reason"] == "competitor: Went with incumbent"
    db.refresh(l)
    assert l.pipeline_lost_at and l.status == "dead" and "[Lost" in (l.notes or "")
    b = client.get("/pipeline/board", headers=h).json()
    col, c = _card(b, l.id)
    assert col == "__lost__" and c["lost"]["reason_label"] == "Chose another provider"
    assert b["lost"]["count"] == 1
    # lost cards cannot be moved until reopened
    assert client.post(f"/pipeline/board/{l.id}/stage", headers=h,
                       json={"stage": "new_inquiry"}).status_code == 409
    assert client.post(f"/pipeline/board/{l.id}/reopen", headers=h).status_code == 200
    db.refresh(l)
    assert l.pipeline_lost_at is None and l.status == "new"


def test_advisor_sees_only_own_cards(client, w):
    db, org = w["db"], w["org"]
    mine = _lead(db, org, w["adv"], first="Mine")
    theirs = _lead(db, org, w["adv2"], first="Theirs")
    b = client.get("/pipeline/board", headers=_h(db, w["adv"])).json()
    assert _card(b, mine.id)[1] and _card(b, theirs.id)[1] is None
    assert client.post(f"/pipeline/board/{theirs.id}/lost", headers=_h(db, w["adv"]),
                       json={"reason": "timing"}).status_code == 404


def test_cross_tenant_board_isolation(client, w):
    db, org = w["db"], w["org"]
    l = _lead(db, org, w["adv"])
    fb = client.get("/pipeline/board", headers=_h(db, w["fadmin"])).json()
    assert _card(fb, l.id)[1] is None
    fh = _h(db, w["fadmin"])
    assert client.post(f"/pipeline/board/{l.id}/stage", headers=fh, json={"stage": "rate_review"}).status_code == 404
    assert client.post(f"/pipeline/board/{l.id}/lost", headers=fh, json={"reason": "price"}).status_code == 404
    assert client.post(f"/pipeline/board/{l.id}/reopen", headers=fh).status_code == 404
    db.refresh(l)
    assert l.tier == "new_inquiry" and l.pipeline_lost_at is None


# ── enrollment truth ─────────────────────────────────────────────────────────

def test_enroll_sets_enrolled_at_and_overview_counts_it(client, w):
    db, org, adv = w["db"], w["org"], w["adv"]
    l = _lead(db, org, adv, tier="proposal_sent")
    legacy = _lead(db, org, adv, tier="contract_signed", first="Legacy", relationship_type="customer")
    h = _h(db, w["admin"])
    before = client.get("/energy-ops/queues", headers=h).json()["enrollments"]
    assert before["this_month"] == 0 and before["date_not_recorded"] == 1

    r = client.post(f"/rate-requests/{l.id}/enroll", headers=_h(db, adv),
                    json={"current_supplier": "Example Supplier"})
    assert r.status_code == 200, r.text
    assert r.json()["enrolled_at"]
    db.refresh(l)
    assert l.enrolled_at and l.relationship_type == "customer" and l.stage_entered_at
    e = client.get("/energy-ops/queues", headers=h).json()["enrollments"]
    assert e["this_month"] == 1
    assert e["date_not_recorded"] == 1          # the legacy customer is NOT invented a date
    # The drawer reads enrollment from the rate request itself (no extra call).
    got = client.get(f"/rate-requests/{l.id}", headers=_h(db, adv)).json()
    assert got["enrolled_at"] and got["relationship_type"] == "customer"
    assert [x["id"] for x in e["recent"]] == [l.id]
    db.refresh(legacy)
    assert legacy.enrolled_at is None
    # last month's enrollment is not counted in this month
    l.enrolled_at = datetime.utcnow().replace(day=1) - timedelta(days=2)
    db.commit()
    assert client.get("/energy-ops/queues", headers=h).json()["enrollments"]["this_month"] == 0
    # board shows the customer state
    _, c = _card(client.get("/pipeline/board", headers=h).json(), legacy.id)
    assert c["customer"]["label"].endswith("date not recorded")
    # other tenant never sees it
    assert client.get("/energy-ops/queues", headers=_h(db, w["fadmin"])).json()["enrollments"]["date_not_recorded"] == 0


# ── energy-ops: counts == list totals, advisor and manager ─────────────────

def _seed_tasks(db, w):
    org, adv, adv2, admin = w["org"], w["adv"], w["adv2"], w["admin"]
    now = datetime.utcnow()
    # Noon on the WORKSPACE's day. This was noon on the UTC date, which from
    # 7 PM Central is tomorrow in the workspace, so the test failed every evening.
    from app.services import workspace_time
    start, _, _ = workspace_time.day_bounds(db, org.id, now=now)
    noon = start + timedelta(hours=12)
    mine = _lead(db, org, adv, first="Own")
    theirs = _lead(db, org, adv2, first="Other")
    _lead(db, org, adv, first="Flag", manual_flag="hot")
    specs = [
        (mine, adv, noon),                                      # due today (mine)
        (theirs, adv, noon),                                    # assigned to adv, lead NOT in adv scope
        (mine, adv, now - timedelta(days=5)),                   # overdue + escalation
        (theirs, adv, now - timedelta(days=6)),                 # out-of-scope overdue
        (theirs, adv2, now - timedelta(days=5)),                # adv2's own overdue
        (None, adv, now + timedelta(days=3)),                   # upcoming, no lead
        (mine, admin, now + timedelta(days=2)),                 # upcoming, manager's
    ]
    for lead, who, due in specs:
        db.add(LeadTask(organization_id=org.id, lead_id=lead.id if lead else None,
                        title="T", due_at=due, assigned_to_id=who.id))
    # a foreign task must never show
    flead = _lead(db, w["other"], w["fadmin"])
    db.add(LeadTask(organization_id=w["other"].id, lead_id=flead.id, title="F",
                    due_at=now - timedelta(days=5), assigned_to_id=w["fadmin"].id))
    db.commit()


@pytest.mark.parametrize("who", ["adv", "admin"])
def test_queue_counts_equal_list_totals(client, w, who):
    db = w["db"]
    _seed_tasks(db, w)
    h = _h(db, w[who])
    summary = {q["key"]: q["count"] for q in client.get("/energy-ops/queues", headers=h).json()["queues"]}
    for key in ("follow_up_due", "overdue", "upcoming", "escalation", "no_response", "customers"):
        seen, page, total = 0, 1, None
        while True:
            r = client.get(f"/energy-ops/queues/{key}?page={page}&per_page=2", headers=h).json()
            total = r["total"]
            seen += len(r["items"])
            if len(r["items"]) < 2:
                break
            page += 1
        assert summary[key] == total == seen, (who, key, summary[key], total, seen)
    if who == "adv":
        # out-of-scope lead tasks are excluded from BOTH count and list
        assert summary["follow_up_due"] == 1 and summary["overdue"] == 1
        assert summary["escalation"] == 2   # 1 overdue 3+ days + 1 flagged lead
        assert summary["upcoming"] == 1
    else:
        assert summary["overdue"] == 3 and summary["upcoming"] == 2
