# -*- coding: utf-8 -*-
"""Move Concierge as an employee works it: one record per move, nothing marked
complete until every item (including "services are active") is ticked, a
changed date is visible, a cancelled move leaves the work queues, and a
returning customer's form never undoes an advisor's work. Synthetic only."""
import itertools
import json
import uuid
from datetime import date, datetime, timedelta

import pytest

import app.models.energy_models  # noqa: F401
from app.models.energy_models import EnergyMoveRequest
from app.models.models import EmailMessage, Lead, Message, Organization, User
from app.models.work_models import LeadTask
from app.services import customer_sites as cs
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
PAGE = "<!DOCTYPE html><html><head><title>A</title></head><body>x</body></html>"


@pytest.fixture(autouse=True)
def _mounted():
    from app.main import app
    from app.routers.energy_ops_router import router as ops
    if "/energy-ops/queues" not in {getattr(r, "path", "") for r in app.routes}:
        app.include_router(ops)
    yield


@pytest.fixture()
def w(db_session):
    db = db_session
    o = Organization(name="Atl MC", slug="mc-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                     industry="energy", is_active=True)
    db.add(o)
    db.commit()
    u = User(organization_id=o.id, email="adm-%d@mc.example.test" % next(_SEQ),
             password_hash=hash_password("x"), full_name="Adm", role="org_admin",
             is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    slug = "mc-site-%d" % next(_SEQ)
    cs.publish(db, organization=o, slug=slug, html=PAGE, title="Atl MC")
    return dict(db=db, org=o, admin=u, slug=slug,
                h={"Authorization": "Bearer %s" % create_access_token(u, db)})


def _move_form(client, w, **kw):
    p = {"first_name": "Mo", "last_name": "Ver", "email": "mover@gmail.com", "phone": "(214) 555-0177",
         "request_type": "I'm moving", "service_area": "76102", "move_date": "2026-11-14",
         "services_requested": "Electricity, Internet", "form_started_at": 1}
    p.update(kw)
    assert client.post("/site/%s/inquiry" % w["slug"], json=p).status_code == 201


def _the_move(w):
    return w["db"].query(EnergyMoveRequest).filter_by(organization_id=w["org"].id).one()


def _tick(client, w, mid, key, done=True):
    r = client.post("/energy-ops/moves/%s/checklist" % mid, headers=w["h"], json={"key": key, "done": done})
    assert r.status_code == 200, r.text
    return r.json()


def _quiet(db):
    assert db.query(Message).count() == 0 and db.query(EmailMessage).count() == 0


def test_normal_move_completes_only_when_every_item_is_ticked(client, w):
    _move_form(client, w)
    m = _the_move(w)
    row = client.get("/energy-ops/moves/%s" % m.id, headers=w["h"]).json()
    assert row["next_action"].startswith("Confirm move date") and len(row["pending_items"]) == 4
    for key in ("confirm_details", "svc_electricity_start", "svc_internet"):
        _tick(client, w, m.id, key)
    # arranged is not active: "completed" is refused until the customer confirms
    r = client.patch("/energy-ops/moves/%s" % m.id, headers=w["h"], json={"status": "completed"})
    assert r.status_code == 409 and r.json()["detail"]["pending"] == [
        "Confirm with customer that services are active"]
    _tick(client, w, m.id, "confirm_complete")
    r = client.patch("/energy-ops/moves/%s" % m.id, headers=w["h"], json={"status": "completed"})
    assert r.status_code == 200 and r.json()["status"] == "completed" and r.json()["next_action"] is None
    _quiet(w["db"])


def test_repeated_submission_keeps_one_move_and_one_task(client, w):
    _move_form(client, w)
    _move_form(client, w)
    db = w["db"]
    assert db.query(EnergyMoveRequest).count() == 1
    lead = db.query(Lead).filter_by(organization_id=w["org"].id).one()
    assert db.query(LeadTask).filter_by(lead_id=lead.id, status="open").count() == 1
    assert _the_move(w).notes == "Opened from the website move form."     # nothing changed


def test_changed_move_date_is_recorded_and_late_tasks_surface(client, w):
    _move_form(client, w)
    m = _the_move(w)
    _move_form(client, w, move_date="2026-11-21")
    w["db"].refresh(m)
    assert m.move_date == date(2026, 11, 21) and "2026-11-14 -> 2026-11-21" in m.notes
    # an advisor task due the day after the move
    t = client.post("/energy-ops/moves/%s/tasks" % m.id, headers=w["h"],
                    json={"title": "Check internet install", "due_at": "2026-11-22T15:00:00"}).json()
    r = client.patch("/energy-ops/moves/%s" % m.id, headers=w["h"], json={"move_date": "2026-11-18"}).json()
    assert [x["id"] for x in r["tasks_to_review"]] == [t["id"]]


def test_partial_completion_is_never_undone_and_overdue_is_flagged(client, w):
    _move_form(client, w, move_date="2026-10-01")          # already past
    m = _the_move(w)
    _tick(client, w, m.id, "svc_electricity_start")
    _move_form(client, w, move_date="2026-10-01", services_requested="Electricity, Internet, Security")
    row = client.get("/energy-ops/moves/%s" % m.id, headers=w["h"]).json()
    done = {i["key"]: i["done"] for i in row["checklist"]}
    assert done["svc_electricity_start"] is True and done["svc_other"] is False
    assert [s["key"] for s in row["services"]] == ["electricity_start", "internet", "other"]
    assert row["overdue"] is True
    summary = client.get("/energy-ops/moves?status=open", headers=w["h"]).json()["summary"]
    assert summary["past_move_date"] == 1


def test_cancelled_move_leaves_the_work_queues(client, w):
    _move_form(client, w)
    m = _the_move(w)
    t = client.post("/energy-ops/moves/%s/tasks" % m.id, headers=w["h"],
                    json={"title": "Call about TV", "due_at": "2026-11-10T15:00:00"}).json()
    r = client.patch("/energy-ops/moves/%s" % m.id, headers=w["h"], json={"status": "cancelled"}).json()
    assert r["tasks_cancelled"] == 1 and r["next_action"] is None and r["pending_items"] == []
    assert w["db"].query(LeadTask).get(t["id"]).status == "cancelled"
    assert client.get("/energy-ops/moves?status=open", headers=w["h"]).json()["total"] == 0
    # the website follow-up task is not a move task and stays with the lead
    lead = w["db"].query(Lead).filter_by(organization_id=w["org"].id).one()
    assert w["db"].query(LeadTask).filter_by(lead_id=lead.id, source="website", status="open").count() == 1


def _tick_all(client, w, m):
    row = client.get("/energy-ops/moves/%s" % m.id, headers=w["h"]).json()
    for i in row["checklist"]:
        if not i["done"]:
            _tick(client, w, m.id, i["key"])


def test_completed_move_checklist_is_frozen(client, w):
    _move_form(client, w)
    m = _the_move(w)
    _tick_all(client, w, m)
    assert client.patch("/energy-ops/moves/%s" % m.id, headers=w["h"],
                        json={"status": "completed"}).status_code == 200
    r = client.post("/energy-ops/moves/%s/checklist" % m.id, headers=w["h"],
                    json={"key": "svc_internet", "done": False})
    assert r.status_code == 409


def test_adding_a_service_in_the_same_change_blocks_completion(client, w):
    _move_form(client, w)
    m = _the_move(w)
    _tick_all(client, w, m)
    r = client.patch("/energy-ops/moves/%s" % m.id, headers=w["h"],
                     json={"services": ["electricity_start", "internet", "other"], "status": "completed"})
    assert r.status_code == 409, r.text
    w["db"].refresh(m)
    assert m.status != "completed"


def test_cancelling_one_move_leaves_another_open_moves_tasks(client, w):
    _move_form(client, w)
    m = _the_move(w)
    db = w["db"]
    twin = EnergyMoveRequest(organization_id=m.organization_id, lead_id=m.lead_id, status=m.status,
                             move_date=m.move_date, checklist=m.checklist)
    db.add(twin)
    db.commit()
    t = client.post("/energy-ops/moves/%s/tasks" % m.id, headers=w["h"],
                    json={"title": "Call about TV", "due_at": "2026-11-10T15:00:00"}).json()
    r = client.patch("/energy-ops/moves/%s" % m.id, headers=w["h"], json={"status": "cancelled"}).json()
    assert r["status"] == "cancelled" and not r.get("tasks_cancelled")
    assert db.query(LeadTask).get(t["id"]).status == "open"
