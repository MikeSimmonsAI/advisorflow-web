# -*- coding: utf-8 -*-
"""Roofing field inspections (issue #283): start from an appointment, save a
draft, photos (upload, label, serve, delete), completion rules, report numbers,
journey movement only from a completed report, the sales hand-off, several
inspections for one homeowner, salesperson scope and tenant isolation."""
import io
import os
from datetime import datetime, timedelta

import pytest

from app.models.models import BookingLink
from app.models.roofing_models import RoofingInspection, RoofingOpportunity
from tests.test_roofing_program import h, lead, org, user, world  # noqa: F401 (fixture)

os.environ.setdefault("MEDIA_STORAGE_BACKEND", "database")

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")


def _book(db, l, rep, when):
    b = BookingLink(lead_id=l.id, user_id=rep.id, status="booked", booked_time=when, appt_label="Roof Inspection",
                    appt_duration=60, token="t-%s" % os.urandom(6).hex())
    db.add(b)
    db.commit()
    return b


def _photo(client, db, u, iid, data=PNG, label="damage"):
    return client.post("/roofing-ops/inspections/%s/photos" % iid, headers=h(db, u),
                       files={"file": ("roof.png", io.BytesIO(data), "image/png")},
                       data={"label": label, "caption": "Hail hits on the north slope"})


@pytest.fixture()
def booked(world):
    db = world["db"]
    l = lead(db, world["a"], "Henry", owner=world["a_rep"], custom_fields='{"roof_issue": "Hail damage"}')
    b = _book(db, l, world["a_rep"], datetime.now() + timedelta(days=1))
    return dict(world, lead=l, booking=b)


def test_full_inspection_lifecycle(client, booked):
    db, rep, l, b = booked["db"], booked["a_rep"], booked["lead"], booked["booking"]
    dash = client.get("/roofing-ops/inspections", headers=h(db, rep)).json()
    assert [x["lead_id"] for x in dash["buckets"]["upcoming"]] == [l.id]
    r = client.post("/roofing-ops/inspections", json={"lead_id": l.id, "booking_link_id": b.id}, headers=h(db, rep))
    assert r.status_code == 200, r.text
    ins = r.json()
    assert ins["status"] == "in_progress" and ins["inspector_id"] == rep.id
    assert ins["homeowner"]["name"].startswith("Henry") and "At least one photo" in ins["missing"]
    # opening the same appointment again never makes a second report
    again = client.post("/roofing-ops/inspections", json={"lead_id": l.id, "booking_link_id": b.id}, headers=h(db, rep))
    assert again.json()["id"] == ins["id"]
    iid = ins["id"]
    # completing early is refused with what's missing
    r = client.post("/roofing-ops/inspections/%s/complete" % iid, headers=h(db, rep))
    assert r.status_code == 422 and "Roof condition" in r.json()["detail"]["missing"]
    # save a draft, reopen it
    r = client.patch("/roofing-ops/inspections/%s" % iid, headers=h(db, rep), json={
        "roof_type": "Architectural shingle", "roof_age_years": 18, "roof_condition": "poor",
        "damage": ["hail", "granule_loss"], "severity": "moderate", "affected_areas": ["North slope", "Ridge"],
        "notes": "Consistent hail bruising on the north slope.", "recommendation": "replacement",
        "insurance_claim": "yes"})
    assert r.status_code == 200, r.text
    assert client.patch("/roofing-ops/inspections/%s" % iid, headers=h(db, rep),
                        json={"damage": ["made_up"]}).status_code == 400
    d = client.get("/roofing-ops/inspections/%s" % iid, headers=h(db, rep)).json()
    assert d["damage"] == ["granule_loss", "hail"] and d["missing"] == ["At least one photo"]
    assert d["status"] == "in_progress"
    # photos: upload, duplicate retry, label, serve, delete
    p = _photo(client, db, rep, iid)
    assert p.status_code == 200, p.text
    assert _photo(client, db, rep, iid).json()["id"] == p.json()["id"]
    pid = p.json()["id"]
    assert client.patch("/roofing-ops/inspections/%s/photos/%s" % (iid, pid), headers=h(db, rep),
                        json={"label": "shingles", "caption": "Close-up"}).json()["label"] == "shingles"
    img = client.get("/roofing-ops/inspections/%s/photos/%s" % (iid, pid), headers=h(db, rep))
    assert img.status_code == 200 and img.content == PNG and img.headers["content-type"] == "image/png"
    assert client.post("/roofing-ops/inspections/%s/photos" % iid, headers=h(db, rep),
                       files={"file": ("x.txt", io.BytesIO(b"not an image"), "text/plain")}).status_code in (400, 415, 422)
    # stage has NOT moved yet: a draft is not a documented inspection
    opp = db.query(RoofingOpportunity).filter(RoofingOpportunity.lead_id == l.id).one()
    assert opp.stage != "inspected"
    r = client.post("/roofing-ops/inspections/%s/complete" % iid, headers=h(db, rep))
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["status"] == "completed" and done["report_number"].startswith("R-")
    assert done["next_action"] == "prepare_estimate" and done["opportunity"]["stage"] == "inspected"
    # a completed report is locked for the inspector
    assert client.patch("/roofing-ops/inspections/%s" % iid, headers=h(db, rep), json={"notes": "x"}).status_code == 409
    assert client.delete("/roofing-ops/inspections/%s/photos/%s" % (iid, pid), headers=h(db, rep)).status_code == 409
    dash = client.get("/roofing-ops/inspections", headers=h(db, rep)).json()
    assert [x["inspection_id"] for x in dash["buckets"]["next_step"]] == [iid]
    # hand-off: prepare estimate, then mark it sent -> Estimate Sent
    r = client.post("/roofing-ops/inspections/%s/handoff" % iid, headers=h(db, rep),
                    json={"action": "prepare_estimate", "estimate_amount": 14600, "scope": "Full tear-off"})
    assert r.status_code == 200 and r.json()["opportunity"]["stage"] == "inspected"
    assert r.json()["opportunity"]["estimate_amount"] == 14600
    r = client.post("/roofing-ops/inspections/%s/handoff" % iid, headers=h(db, rep),
                    json={"action": "prepare_estimate", "estimate_sent": True})
    assert r.json()["opportunity"]["stage"] == "estimate_sent"
    # the report stays on the homeowner
    lw = client.get("/roofing-ops/leads/%s" % l.id, headers=h(db, rep)).json()
    assert [x["report_number"] for x in lw["inspections"]] == [done["report_number"]]
    # manager can reopen; the salesperson cannot
    assert client.post("/roofing-ops/inspections/%s/reopen" % iid, headers=h(db, rep)).status_code == 403
    assert client.post("/roofing-ops/inspections/%s/reopen" % iid,
                       headers=h(db, booked["a_owner"])).json()["status"] == "in_progress"


def test_close_no_work_and_follow_up(client, booked):
    db, rep, l, b = booked["db"], booked["a_rep"], booked["lead"], booked["booking"]
    iid = client.post("/roofing-ops/inspections", json={"lead_id": l.id, "booking_link_id": b.id},
                      headers=h(db, rep)).json()["id"]
    client.patch("/roofing-ops/inspections/%s" % iid, headers=h(db, rep), json={
        "roof_condition": "good", "severity": "none", "notes": "Roof is sound.", "recommendation": "monitor"})
    _photo(client, db, rep, iid, label="overview")
    assert client.post("/roofing-ops/inspections/%s/handoff" % iid, headers=h(db, rep),
                       json={"action": "close_no_work"}).status_code == 409   # report first
    done = client.post("/roofing-ops/inspections/%s/complete" % iid, headers=h(db, rep)).json()
    assert done["next_action"] == "schedule_follow_up"
    r = client.post("/roofing-ops/inspections/%s/handoff" % iid, headers=h(db, rep),
                    json={"action": "schedule_follow_up", "follow_up_at": "2026-12-01T10:00"})
    assert r.status_code == 200 and r.json()["follow_up_at"].startswith("2026-12-01")
    r = client.post("/roofing-ops/inspections/%s/handoff" % iid, headers=h(db, rep), json={"action": "close_no_work"})
    assert r.json()["opportunity"]["stage"] == "lost"


def test_two_inspections_for_one_homeowner(client, booked):
    db, rep, l, b = booked["db"], booked["a_rep"], booked["lead"], booked["booking"]
    b2 = _book(db, l, rep, datetime.now() + timedelta(days=9))
    i1 = client.post("/roofing-ops/inspections", json={"lead_id": l.id, "booking_link_id": b.id}, headers=h(db, rep)).json()
    i2 = client.post("/roofing-ops/inspections", json={"lead_id": l.id, "booking_link_id": b2.id}, headers=h(db, rep)).json()
    assert i1["id"] != i2["id"]
    assert db.query(RoofingInspection).filter(RoofingInspection.lead_id == l.id).count() == 2


def test_scope_and_tenant_isolation(client, booked):
    db, l, b = booked["db"], booked["lead"], booked["booking"]
    iid = client.post("/roofing-ops/inspections", json={"lead_id": l.id, "booking_link_id": b.id},
                      headers=h(db, booked["a_rep"])).json()["id"]
    pid = _photo(client, db, booked["a_rep"], iid).json()["id"]
    # another salesperson in the same company cannot see this homeowner's inspection
    assert client.get("/roofing-ops/inspections/%s" % iid, headers=h(db, booked["a_rep2"])).status_code in (403, 404)
    # another roofing company cannot see it, its photos or start one on the lead
    other = booked["b_owner"]
    assert client.get("/roofing-ops/inspections/%s" % iid, headers=h(db, other)).status_code == 404
    assert client.get("/roofing-ops/inspections/%s/photos/%s" % (iid, pid), headers=h(db, other)).status_code == 404
    assert client.post("/roofing-ops/inspections", json={"lead_id": l.id}, headers=h(db, other)).status_code in (403, 404)
    # the owner sees everything
    assert client.get("/roofing-ops/inspections/%s" % iid, headers=h(db, booked["a_owner"])).status_code == 200


def test_demo_story_has_sample_inspections_with_photos(db_session):
    from app.models.roofing_models import RoofingInspectionPhoto
    from app.services.roofing import demo_seed
    out = demo_seed.seed(db_session)
    assert out["inspections_added"] > 5 and out["inspection_photos_added"] >= out["inspections_added"]
    rows = db_session.query(RoofingInspection).filter(RoofingInspection.organization_id == out["organization_id"]).all()
    done = [r for r in rows if r.status == "completed"]
    assert done and all(r.report_number and r.notes and r.recommendation for r in done)
    assert 0 < len([r for r in rows if r.status == "in_progress"]) <= 2
    ph = db_session.query(RoofingInspectionPhoto).filter(RoofingInspectionPhoto.organization_id == out["organization_id"]).all()
    assert ph and all(p.is_sample for p in ph)
    # PR #285 review: a sample report is never completed before its visit, its
    # history runs in order, and nothing in it reads like a coverage decision.
    from app.models.roofing_models import RoofingInspectionEvent
    now = datetime.utcnow()
    for r in done:
        assert r.scheduled_at <= r.started_at < r.completed_at <= now, r.report_number
        if r.booking_link_id:
            assert db_session.get(BookingLink, r.booking_link_id).booked_time <= r.completed_at
        ev = (db_session.query(RoofingInspectionEvent).filter(RoofingInspectionEvent.inspection_id == r.id)
              .order_by(RoofingInspectionEvent.at).all())
        assert [e.action for e in ev][:2] == ["started", "completed"]
        assert all(r.started_at <= e.at <= now for e in ev)
        assert "threshold" not in (r.notes or "").lower() and r.insurance_claim not in ("yes", "maybe", "no")
        assert r.stories == 2 and r.roof_type == "Architectural shingle"   # what the sample overview photo shows
    assert all("connection" not in (p.caption or "").lower() for p in ph)
    assert demo_seed.seed(db_session)["inspections_added"] == 0     # idempotent


def _ready(client, db, u, iid):
    client.patch("/roofing-ops/inspections/%s" % iid, headers=h(db, u), json={
        "roof_condition": "fair", "severity": "minor", "damage": ["wind"], "notes": "Lifted tabs.",
        "recommendation": "repair"})
    _photo(client, db, u, iid, data=PNG + os.urandom(4))


def test_report_numbers_never_collide_and_history_survives_reopen(client, booked):
    from app.models.roofing_models import RoofingInspectionEvent
    db, rep, owner, l = booked["db"], booked["a_rep"], booked["a_owner"], booked["lead"]
    ids = []
    for k in range(3):
        b = _book(db, l, rep, datetime.now() + timedelta(days=k + 2))
        iid = client.post("/roofing-ops/inspections", json={"lead_id": l.id, "booking_link_id": b.id},
                          headers=h(db, rep)).json()["id"]
        _ready(client, db, rep, iid)
        ids.append(iid)
    nums = [client.post("/roofing-ops/inspections/%s/complete" % i, headers=h(db, rep)).json()["report_number"] for i in ids]
    assert len(set(nums)) == 3
    # delete the middle report's row: the next number still never repeats one in use
    db.query(RoofingInspection).filter(RoofingInspection.id == ids[1]).delete()
    db.commit()
    b = _book(db, l, rep, datetime.now() + timedelta(days=9))
    iid = client.post("/roofing-ops/inspections", json={"lead_id": l.id, "booking_link_id": b.id}, headers=h(db, rep)).json()["id"]
    _ready(client, db, rep, iid)
    n4 = client.post("/roofing-ops/inspections/%s/complete" % iid, headers=h(db, rep)).json()["report_number"]
    assert n4 not in (nums[0], nums[2]) and int(n4.split("-")[1]) > max(int(x.split("-")[1]) for x in nums)
    # the database refuses a duplicate number outright
    from sqlalchemy.exc import IntegrityError
    dup = db.query(RoofingInspection).filter(RoofingInspection.id == ids[2]).one()
    dup.report_number = nums[0]
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    # reopen + complete again: same number, earlier completion kept in history
    first = client.get("/roofing-ops/inspections/%s" % ids[0], headers=h(db, rep)).json()
    client.post("/roofing-ops/inspections/%s/reopen" % ids[0], headers=h(db, owner))
    client.patch("/roofing-ops/inspections/%s" % ids[0], headers=h(db, rep), json={"notes": "Lifted tabs; also a cracked boot."})
    again = client.post("/roofing-ops/inspections/%s/complete" % ids[0], headers=h(db, owner)).json()
    assert again["report_number"] == first["report_number"] and again["revised"] is True
    acts = [e["action"] for e in again["history"]]
    assert acts == ["started", "completed", "reopened", "recompleted"]
    reopened = next(e for e in again["history"] if e["action"] == "reopened")
    assert reopened["detail"]["previously_completed_at"] == first["completed_at"]
    assert db.query(RoofingInspectionEvent).filter(RoofingInspectionEvent.inspection_id == ids[0]).count() == 4


def test_estimate_steps_follow_the_stage(client, booked):
    db, rep, l, b = booked["db"], booked["a_rep"], booked["lead"], booked["booking"]
    iid = client.post("/roofing-ops/inspections", json={"lead_id": l.id, "booking_link_id": b.id}, headers=h(db, rep)).json()["id"]
    _ready(client, db, rep, iid)
    client.post("/roofing-ops/inspections/%s/complete" % iid, headers=h(db, rep))
    hand = lambda body: client.post("/roofing-ops/inspections/%s/handoff" % iid, headers=h(db, rep), json=body)  # noqa: E731
    # can't win a job before an estimate was given
    assert hand({"action": "mark_won"}).status_code == 409
    r = hand({"action": "prepare_estimate", "estimate_amount": 9800}).json()
    assert r["opportunity"]["stage"] == "inspected" and r["handoff"]["label"] == "Estimate prepared"
    assert r["next_action"] == "send_estimate"
    dash = client.get("/roofing-ops/inspections", headers=h(db, rep)).json()
    assert next(x for x in dash["buckets"]["next_step"] if x["inspection_id"] == iid)["action"]["label"] == "Record estimate given"
    r = hand({"action": "record_estimate_given"}).json()
    assert r["opportunity"]["stage"] == "estimate_sent" and r["handoff"]["label"] == "Estimate given to homeowner"
    dash = client.get("/roofing-ops/inspections", headers=h(db, rep)).json()
    assert next(x for x in dash["buckets"]["next_step"] if x["inspection_id"] == iid)["action"]["label"] == "Follow up on estimate"
    r = hand({"action": "schedule_follow_up", "follow_up_at": "2026-11-02T09:00", "note": "Questions on scope"}).json()
    assert r["opportunity"]["stage"] == "estimate_sent"
    assert hand({"action": "mark_lost"}).status_code == 400                     # a reason is required
    r = hand({"action": "mark_won", "won_amount": 9500}).json()
    assert r["opportunity"]["stage"] == "won" and r["opportunity"]["won_amount"] == 9500
    acts = [(e["action"], (e["detail"] or {}).get("step")) for e in r["history"]]
    assert ("handoff", "mark_won") in acts and ("handoff", "schedule_follow_up") in acts


def test_report_names_the_real_inspector_and_the_real_completer(client, booked):
    """PR #285 final QA: 'Inspected by' is the inspector; 'completed by' and the
    history name whoever really finished it - never one borrowed for the other."""
    db, rep, owner, l, b = booked["db"], booked["a_rep"], booked["a_owner"], booked["lead"], booked["booking"]
    iid = client.post("/roofing-ops/inspections", json={"lead_id": l.id, "booking_link_id": b.id},
                      headers=h(db, rep)).json()["id"]
    _ready(client, db, rep, iid)
    r = client.post("/roofing-ops/inspections/%s/complete" % iid, headers=h(db, owner)).json()
    assert r["inspector_id"] == rep.id and r["inspector_name"] and r["completed_by_name"]
    assert r["completed_by_name"] != r["inspector_name"]
    by = {e["action"]: e["by"] for e in r["history"]}
    assert by["started"] == r["inspector_name"] and by["completed"] == r["completed_by_name"]
    r = client.post("/roofing-ops/inspections/%s/handoff" % iid, headers=h(db, rep),
                    json={"action": "prepare_estimate", "estimate_amount": 7000}).json()
    last = r["history"][-1]
    assert last["label"] == "Sales step: Estimate amount and scope recorded" and last["by"] == r["inspector_name"]


def test_insurance_is_homeowner_reported_not_a_coverage_call(client, booked):
    db, rep, l, b = booked["db"], booked["a_rep"], booked["lead"], booked["booking"]
    iid = client.post("/roofing-ops/inspections", json={"lead_id": l.id, "booking_link_id": b.id},
                      headers=h(db, rep)).json()["id"]
    ch = client.get("/roofing-ops/inspections/choices", headers=h(db, rep)).json()
    labels = " ".join(c["label"] for c in ch["insurance_claim"]).lower()
    assert "likely" not in labels and "threshold" not in labels and "homeowner" in labels
    r = client.patch("/roofing-ops/inspections/%s" % iid, headers=h(db, rep), json={"insurance_claim": "open_claim"}).json()
    assert r["insurance_claim_label"] == "Homeowner reports an open claim"
    assert "insurance provider" in r["insurance_note"]
    r = client.patch("/roofing-ops/inspections/%s" % iid, headers=h(db, rep), json={"insurance_claim": "yes"}).json()
    assert r["insurance_claim"] == "yes" and "likely" not in r["insurance_claim_label"].lower()   # older value, kept, neutral label
    r = client.patch("/roofing-ops/inspections/%s" % iid, headers=h(db, rep), json={"insurance_claim": "none_reported"}).json()
    assert r["insurance_note"] is None


def test_repeated_sales_clicks_record_once(client, booked):
    from app.models.roofing_models import RoofingInspectionEvent, RoofingOpportunity
    db, rep, l, b = booked["db"], booked["a_rep"], booked["lead"], booked["booking"]
    iid = client.post("/roofing-ops/inspections", json={"lead_id": l.id, "booking_link_id": b.id},
                      headers=h(db, rep)).json()["id"]
    _ready(client, db, rep, iid)
    client.post("/roofing-ops/inspections/%s/complete" % iid, headers=h(db, rep))
    hand = lambda body: client.post("/roofing-ops/inspections/%s/handoff" % iid, headers=h(db, rep), json=body)  # noqa: E731
    hand({"action": "prepare_estimate", "estimate_amount": 9800})
    for _ in range(3):
        assert hand({"action": "record_estimate_given"}).status_code == 200
    for _ in range(2):
        assert hand({"action": "mark_won"}).status_code == 200
    steps = [C.get("step") for C in (__import__("json").loads(e.detail or "{}") for e in
             db.query(RoofingInspectionEvent).filter(RoofingInspectionEvent.inspection_id == iid,
                                                     RoofingInspectionEvent.action == "handoff").all())]
    assert steps.count("record_estimate_given") == 1 and steps.count("mark_won") == 1
    assert db.query(RoofingOpportunity).filter(RoofingOpportunity.lead_id == l.id).count() == 1
