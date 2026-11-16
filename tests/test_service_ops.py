# -*- coding: utf-8 -*-
"""Service Operations (#452/#506): won contract -> customer -> location ->
job / recurring service -> team -> worker completion -> invoice -> payment
status -> buyer portal, with tenant isolation at the API.

Synthetic companies and people only. Everything runs through the real app,
real auth and real models; nothing here sends a message or calls a provider.
"""
import io
import json
from datetime import date, datetime, timedelta

import pytest

from app.models.models import User
from app.models.service_ops_models import (ServiceJob, ServiceJobEvent, ServiceRecurringSchedule,
                                           ServiceTeamMember)
from app.services.service_ops import recurring as R
from tests.test_acquisition_pilot import _add, _h, _move, _org, _user, pilot  # noqa: F401

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
       b"\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def _switch(p, org=None, **flags):
    org = org or p["shine"]
    if org is not p["shine"]:
        assert p["c"].post("/god/acquisition/%s/enable" % org.id, headers=_h(p["db"], p["god"])).status_code == 200
    body = {"ops_enabled": True, "advanced_enabled": True, "buyer_portal_enabled": True}
    body.update(flags)
    r = p["c"].put("/acquisition-ops/%s/service-ops" % org.id, json=body, headers=_h(p["db"], p["god"]))
    assert r.status_code == 200, r.text
    return r.json()


def _o(p):        # the company admin (workspace owner)
    return _h(p["db"], p["owner"], p["shine"].id)


def _u(p, u, org=None):   # any login standing in a company's workspace
    return _h(p["db"], u, (org or p["shine"]).id)


def _booked(p, name="Synthetic Tower Offices"):
    """A delivered appointment, booked the way the platform books it."""
    from app.models.acquisition_models import AcquisitionOpportunity
    from app.models.models import BookingLink
    db = p["db"]
    opp = _add(p, business_name=name, city="Arlington")
    assert _move(p, opp, "interested", note="Wants a walkthrough").status_code == 200
    o = db.query(AcquisitionOpportunity).get(opp)
    bl = BookingLink(lead_id=o.lead_id, user_id=p["owner"].id, status="booked",
                     booked_time=datetime.utcnow() - timedelta(days=1), appt_label="Walkthrough",
                     appt_duration=45, calendar_event_id="evt-test")
    db.add(bl)
    db.commit()
    o.booking_link_id = bl.id
    db.commit()
    assert _move(p, opp, "appointment_booked").status_code == 200
    return opp


def _won(p):
    """Booked -> the customer records held, estimate, won in its own portal."""
    opp = _booked(p)
    for body in ({"outcome": "appointment_held"}, {"outcome": "quote_issued", "quote_amount": 900, "monthly_value": 900},
                 {"outcome": "won", "monthly_value": 900}):
        r = p["c"].post("/acquisition/prospects/%s/outcome" % opp, json=body, headers=_o(p))
        assert r.status_code == 200, r.text
    return opp


def _worker(p, email="worker1@example.com", role="field_worker", team=None, availability=None):
    body = {"email": email, "full_name": "Synthetic Worker", "role": role, "team_name": team}
    if availability is not None:
        body["availability"] = availability
    r = p["c"].post("/service-ops/team", json=body, headers=_o(p))
    assert r.status_code == 200, r.text
    m = r.json()
    u = p["db"].query(User).filter(User.id == m["user_id"]).one()
    u.must_change_password = False            # as if they finished their setup link
    p["db"].commit()
    return m, u


@pytest.fixture()
def ops(pilot):
    _switch(pilot)
    return pilot


# ── switched off by default; EvoSys switches it on ────────────────────────
def test_off_by_default_and_only_evosys_switches_it_on(pilot):
    p = pilot
    me = p["c"].get("/service-ops/me", headers=_o(p))
    assert me.status_code == 200 and me.json()["ops_enabled"] is False
    assert p["c"].get("/service-ops/customers", headers=_o(p)).status_code == 402
    r = p["c"].put("/acquisition-ops/%s/service-ops" % p["shine"].id, json={"ops_enabled": True}, headers=_o(p))
    assert r.status_code in (401, 403)
    _switch(p, buyer_portal_enabled=False, advanced_enabled=False)
    assert p["c"].put("/service-ops/settings", json={"advanced_enabled": True}, headers=_o(p)).status_code == 403
    assert p["c"].get("/service-ops/me", headers=_o(p)).json()["role"] == "company_admin"


# ── slice A: customers, locations, sensitive instructions ─────────────────
def test_customers_locations_and_access_codes_only_for_authorized_people(ops):
    p = ops
    r = p["c"].post("/service-ops/customers", json={"name": "Synthetic Property Mgmt", "location": {
        "name": "Tower A", "address": "1 Main", "service_category": "window_washing", "access_instructions": "Gate 4412",
        "preferred_windows": [{"days": [0, 1, 2], "start": "07:00", "end": "11:00"}]}}, headers=_o(p))
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    d = p["c"].get("/service-ops/customers/%s" % cid, headers=_o(p)).json()
    loc = d["locations"][0]
    assert loc["access_instructions"] == "Gate 4412" and loc["preferred_windows"][0]["start"] == "07:00"
    second = p["c"].post("/service-ops/customers/%s/locations" % cid, json={"name": "Tower B",
                                                                           "service_category": "cleaning"},
                         headers=_o(p))
    assert second.status_code == 200
    m, u = _worker(p)
    wh = _u(p, u)
    # A field worker sees no customer list and no location it isn't assigned to.
    assert p["c"].get("/service-ops/customers", headers=wh).status_code == 403
    assert p["c"].get("/service-ops/locations/%s" % loc["id"], headers=wh).status_code == 404
    p["c"].put("/service-ops/locations/%s" % loc["id"], json={"assigned_member_ids": [m["id"]]}, headers=_o(p))
    seen = p["c"].get("/service-ops/locations/%s" % loc["id"], headers=wh).json()
    assert seen["access_instructions"] == "Gate 4412"
    viewer_m, viewer = _worker(p, "viewer@example.com", role="viewer")
    v = p["c"].get("/service-ops/locations/%s" % loc["id"], headers=_u(p, viewer)).json()
    assert v["access_instructions"] is None and v["has_access_instructions"] is True


# ── slice B: won contract -> job, idempotent ──────────────────────────────
def test_won_contract_converts_once_even_on_double_click(ops):
    p = ops
    opp = _won(p)
    won = p["c"].get("/service-ops/won", headers=_o(p)).json()["items"]
    assert [x["id"] for x in won] == [opp] and won[0]["job_id"] is None
    body = {"location": {"name": "Tower Main", "service_category": "window_washing"},
            "job": {"local_start": "2030-03-04T09:00", "service_type": "Exterior windows"}}
    a = p["c"].post("/service-ops/won/%s/convert" % opp, json=body, headers=_o(p))
    b = p["c"].post("/service-ops/won/%s/convert" % opp, json=body, headers=_o(p))
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    assert a.json()["created"] is True and b.json()["created"] is False
    assert a.json()["job"]["id"] == b.json()["job"]["id"]
    assert p["db"].query(ServiceJob).filter(ServiceJob.opportunity_id == opp).count() == 1
    j = a.json()["job"]
    assert j["status"] == "scheduled" and j["price"] == 900 and j["opportunity_id"] == opp
    # The appointment stays a sales record; the job is a separate, linked record.
    assert p["c"].get("/service-ops/won", headers=_o(p)).json()["items"][0]["job_id"] == j["id"]


def test_only_a_won_contract_converts(ops):
    p = ops
    opp = _booked(p, "Not Yet Won Co")
    r = p["c"].post("/service-ops/won/%s/convert" % opp, json={}, headers=_o(p))
    assert r.status_code == 409 and "won contract" in r.json()["detail"]
    hidden = _add(p, business_name="Raw Prospect Co")          # never delivered: not even visible
    assert p["c"].post("/service-ops/won/%s/convert" % hidden, json={}, headers=_o(p)).status_code == 404


# ── jobs: legitimate transitions, conflicts, completion, history ──────────
def _setup_job(p, start="2030-03-04T09:00", category="cleaning"):
    c = p["c"].post("/service-ops/customers", json={"name": "Synthetic Office Co", "location": {
        "name": "HQ", "service_category": category, "access_instructions": "Key box 9"}}, headers=_o(p)).json()
    loc = p["c"].get("/service-ops/customers/%s" % c["id"], headers=_o(p)).json()["locations"][0]
    j = p["c"].post("/service-ops/jobs", json={"location_id": loc["id"], "local_start": start, "duration_min": 120,
                                              "price": 250, "idempotency_key": "k-%s" % start},
                    headers=_o(p)).json()
    return c, loc, j


def test_job_lifecycle_worker_completion_history_and_photo(ops):
    p = ops
    c, loc, j = _setup_job(p)
    assert j["status"] == "scheduled" and len(j["checklist"]) == 5
    again = p["c"].post("/service-ops/jobs", json={"location_id": loc["id"], "local_start": "2030-03-04T09:00",
                                                   "idempotency_key": "k-2030-03-04T09:00"}, headers=_o(p)).json()
    assert again["id"] == j["id"] and again["created"] is False
    m, u = _worker(p)
    wh = _u(p, u)
    assert p["c"].get("/service-ops/jobs/%s" % j["id"], headers=wh).status_code == 404     # not assigned yet
    r = p["c"].post("/service-ops/jobs/%s/assign" % j["id"], json={"add": [m["id"]]}, headers=_o(p))
    assert r.json()["status"] == "assigned"
    mine = p["c"].get("/service-ops/jobs?mine=true", headers=wh).json()["items"]
    assert [x["id"] for x in mine] == [j["id"]]
    detail = p["c"].get("/service-ops/jobs/%s" % j["id"], headers=wh).json()
    assert detail["location_detail"]["access_instructions"] == "Key box 9" and detail["is_mine"]
    # A worker can't skip ahead, cancel or approve.
    assert p["c"].post("/service-ops/jobs/%s/complete" % j["id"], json={}, headers=wh).status_code == 409
    assert p["c"].post("/service-ops/jobs/%s/status" % j["id"], json={"status": "cancelled", "reason": "x"},
                       headers=wh).status_code == 403
    assert p["c"].post("/service-ops/jobs/%s/start" % j["id"], headers=wh).json()["status"] == "in_progress"
    keys = [i["key"] for i in detail["checklist"]]
    p["c"].post("/service-ops/jobs/%s/checklist" % j["id"], json={"key": keys[0], "done": True}, headers=wh)
    blocked = p["c"].post("/service-ops/jobs/%s/complete" % j["id"], json={}, headers=wh)
    assert blocked.status_code == 409 and "checklist" in blocked.json()["detail"]
    for k in keys[1:]:
        p["c"].post("/service-ops/jobs/%s/checklist" % j["id"], json={"key": k, "done": True}, headers=wh)
    up = p["c"].post("/service-ops/jobs/%s/photos" % j["id"], files={"file": ("done.png", io.BytesIO(PNG), "image/png")},
                     data={"caption": "After"}, headers=wh)
    assert up.status_code == 200, up.text
    done = p["c"].post("/service-ops/jobs/%s/complete" % j["id"], json={"notes": "All glass done"}, headers=wh).json()
    assert done["status"] == "completed" and done["completion_notes"] == "All glass done"
    twice = p["c"].post("/service-ops/jobs/%s/complete" % j["id"], json={}, headers=wh).json()
    assert twice["status"] == "completed"                                   # retry-safe
    assert done["history_check"]["intact"] is True
    kinds = [h["kind"] for h in done["history"]]
    assert {"created", "assigned", "checklist", "photo"} <= set(kinds)
    # Photo is private: the worker and office can fetch it; another company can't.
    aid = up.json()["id"]
    assert p["c"].get("/service-ops/jobs/%s/photos/%s" % (j["id"], aid), headers=wh).content == PNG
    # Tampering with history is detected.
    ev = p["db"].query(ServiceJobEvent).filter(ServiceJobEvent.job_id == j["id"], ServiceJobEvent.seq == 2).one()
    ev.data = json.dumps({"forged": True})
    p["db"].commit()
    chk = p["c"].get("/service-ops/jobs/%s" % j["id"], headers=_o(p)).json()["history_check"]
    assert chk == {"intact": False, "broken_at": 2, "events": chk["events"]}


def test_assignment_conflicts_and_availability(ops):
    p = ops
    _, loc, j1 = _setup_job(p, "2030-03-04T09:00")
    j2 = p["c"].post("/service-ops/jobs", json={"location_id": loc["id"], "local_start": "2030-03-04T10:00",
                                               "duration_min": 60}, headers=_o(p)).json()
    m, _ = _worker(p, availability={"0": ["08:00", "17:00"]})               # Mondays only (2030-03-04 is a Monday)
    assert p["c"].post("/service-ops/jobs/%s/assign" % j1["id"], json={"add": [m["id"]]}, headers=_o(p)).status_code == 200
    clash = p["c"].post("/service-ops/jobs/%s/assign" % j2["id"], json={"add": [m["id"]]}, headers=_o(p))
    assert clash.status_code == 409 and "already has a job" in clash.json()["detail"]
    tue = p["c"].post("/service-ops/jobs", json={"location_id": loc["id"], "local_start": "2030-03-05T09:00"},
                      headers=_o(p)).json()
    off = p["c"].post("/service-ops/jobs/%s/assign" % tue["id"], json={"add": [m["id"]]}, headers=_o(p))
    assert off.status_code == 409 and "availability" in off.json()["detail"]


def test_time_zone_is_kept_per_job(ops):
    p = ops
    c = p["c"].post("/service-ops/customers", json={"name": "Coast Co", "location": {
        "name": "LA site", "service_category": "cleaning", "timezone": "America/Los_Angeles"}}, headers=_o(p)).json()
    loc = p["c"].get("/service-ops/customers/%s" % c["id"], headers=_o(p)).json()["locations"][0]
    j = p["c"].post("/service-ops/jobs", json={"location_id": loc["id"], "local_start": "2030-07-01T09:00"},
                    headers=_o(p)).json()
    assert j["timezone"] == "America/Los_Angeles" and j["scheduled_start"] == "2030-07-01T16:00:00Z"
    w = p["c"].post("/service-ops/jobs", json={"location_id": loc["id"], "local_start": "2030-01-07T09:00"},
                    headers=_o(p)).json()
    assert w["scheduled_start"] == "2030-01-07T17:00:00Z" and w["local_start"] == "2030-01-07T09:00"


# ── recurring service ─────────────────────────────────────────────────────
def test_recurring_bounded_no_duplicates_dst_safe_and_skip_one(ops):
    p = ops
    _, loc, _ = _setup_job(p)
    now = datetime(2030, 3, 1, 12, 0)
    from app.services.service_ops import core as K
    from app.models.models import Organization
    ctx = K.OpsCtx(p["db"], p["owner"], p["shine"], "company_admin", None)
    s = R.create_schedule(ctx, {"location_id": loc["id"], "frequency": "weekly", "start_date": "2030-03-04",
                                "start_time": "09:00", "price_per_visit": 200}, now=now)
    jobs = p["db"].query(ServiceJob).filter(ServiceJob.schedule_id == s.id).order_by(ServiceJob.occurrence_date).all()
    assert 5 <= len(jobs) <= 6                                   # 35-day horizon, not unlimited
    # DST began Mar 10 2030 in the US: 09:00 Central is 15:00Z before and 14:00Z after.
    assert jobs[0].scheduled_start == datetime(2030, 3, 4, 15, 0)
    assert jobs[1].scheduled_start == datetime(2030, 3, 11, 14, 0)
    assert s.expected_monthly_value == round(200 * 4.35, 2)
    # Restart / second instance / retried pass: nothing new.
    assert R.materialize(p["db"], s, now=now) == 0
    assert R.run_pass(p["db"], now=now)["created"] == 0
    s.materialized_through = None                                # even a lost bookmark can't duplicate
    p["db"].commit()
    R.materialize(p["db"], s, now=now)
    assert p["db"].query(ServiceJob).filter(ServiceJob.schedule_id == s.id).count() == len(jobs)
    # Skip ONE visit: the series and other visits are untouched; it's never recreated.
    r = p["c"].post("/service-ops/jobs/%s/skip" % jobs[1].id, json={"reason": "Holiday"}, headers=_o(p))
    assert r.json()["status"] == "cancelled"
    R.materialize(p["db"], s, now=now)
    assert p["db"].query(ServiceJob).filter(ServiceJob.schedule_id == s.id).count() == len(jobs)
    assert p["db"].query(ServiceRecurringSchedule).get(s.id).status == "active"
    # Cancelling the series cancels future un-started visits - none get billed.
    R.set_schedule_status(ctx, s.id, "cancelled", "Lost contract", now=now)
    open_ = p["db"].query(ServiceJob).filter(ServiceJob.schedule_id == s.id, ServiceJob.status != "cancelled").count()
    assert open_ == 0
    cancelled = jobs[2]
    assert p["c"].post("/service-ops/invoices", json={"job_id": cancelled.id}, headers=_o(p)).status_code == 409


def test_monthly_and_custom_occurrences():
    s = ServiceRecurringSchedule(frequency="monthly", start_date=date(2030, 1, 31), end_date=None)
    assert R.occurrences(s, date(2030, 1, 1), date(2030, 4, 30)) == [date(2030, 1, 31), date(2030, 2, 28),
                                                                      date(2030, 3, 31), date(2030, 4, 30)]
    s = ServiceRecurringSchedule(frequency="custom", interval_days=10, start_date=date(2030, 1, 1), end_date=date(2030, 1, 25))
    assert R.occurrences(s, date(2030, 1, 5), date(2030, 2, 28)) == [date(2030, 1, 11), date(2030, 1, 21)]
    s = ServiceRecurringSchedule(frequency="one_time", start_date=date(2030, 1, 5), end_date=None)
    assert R.occurrences(s, date(2030, 1, 1), date(2030, 1, 31)) == [date(2030, 1, 5)]


# ── billing ───────────────────────────────────────────────────────────────
def _finished_job(p):
    _, loc, j = _setup_job(p)
    m, u = _worker(p, "w-bill@example.com")
    wh = _u(p, u)
    p["c"].post("/service-ops/jobs/%s/assign" % j["id"], json={"add": [m["id"]]}, headers=_o(p))
    p["c"].post("/service-ops/jobs/%s/start" % j["id"], headers=wh)
    for i in p["c"].get("/service-ops/jobs/%s" % j["id"], headers=wh).json()["checklist"]:
        p["c"].post("/service-ops/jobs/%s/checklist" % j["id"], json={"key": i["key"], "done": True}, headers=wh)
    assert p["c"].post("/service-ops/jobs/%s/complete" % j["id"], json={}, headers=wh).json()["status"] == "completed"
    return j


def test_invoice_once_per_job_totals_pdf_and_payment_states(ops):
    p = ops
    p["c"].put("/service-ops/settings", json={"default_tax_rate": 8.25, "business_name": "Shine Synthetic LLC",
                                              "payment_instructions": "Check to Shine Synthetic LLC"}, headers=_o(p))
    j = _finished_job(p)
    a = p["c"].post("/service-ops/invoices", json={"job_id": j["id"]}, headers=_o(p)).json()
    b = p["c"].post("/service-ops/invoices", json={"job_id": j["id"]}, headers=_o(p)).json()
    assert a["created"] is True and b["created"] is False and a["id"] == b["id"]
    assert a["subtotal"] == 250 and a["tax"] == 20.63 and a["total"] == 270.63      # half-up cents
    assert a["status"] == "draft" and a["number"] == "INV-1001"
    pdf = p["c"].get("/service-ops/invoices/%s/pdf" % a["id"], headers=_o(p))
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    from pypdf import PdfReader
    txt = "".join(pg.extract_text() for pg in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "INV-1001" in txt and "Shine Synthetic LLC" in txt and "270.63" in txt and "20.63" in txt and "Check to Shine" in txt
    iss = p["c"].post("/service-ops/invoices/%s/issue" % a["id"], headers=_o(p)).json()
    assert iss["status"] == "issued" and iss["due_date"]
    rep = p["c"].post("/service-ops/invoices/%s/payments" % a["id"], json={"amount": 100, "method": "check",
                                                                          "reference": "SYN-1"}, headers=_o(p)).json()
    assert rep["status"] == "issued" and rep["amount_reported"] == 100 and rep["amount_confirmed"] == 0
    pid = rep["payments"][0]["id"]
    conf = p["c"].post("/service-ops/invoices/%s/payments/%s/confirm" % (a["id"], pid), json={}, headers=_o(p)).json()
    assert conf["status"] == "partially_paid" and conf["balance"] == round(270.63 - 100, 2)
    rest = p["c"].post("/service-ops/invoices/%s/payments" % a["id"], json={"amount": 170.63}, headers=_o(p)).json()
    pid2 = rest["payments"][1]["id"]
    assert p["c"].post("/service-ops/invoices/%s/payments/%s/confirm" % (a["id"], pid2), json={},
                       headers=_o(p)).json()["status"] == "paid"
    assert p["c"].post("/service-ops/invoices/%s/void" % a["id"], json={"reason": "x"}, headers=_o(p)).status_code == 409
    # Invoice numbers are unique per company and keep counting.
    second = p["c"].post("/service-ops/invoices", json={"customer_id": a["customer_id"],
                                                        "lines": [{"description": "Extra", "quantity": 2, "rate": 10}]},
                         headers=_o(p)).json()
    assert second["number"] == "INV-1002" and second["subtotal"] == 20


def test_pdf_needs_advanced_operations(pilot):
    p = pilot
    _switch(p, advanced_enabled=False)
    j = _finished_job(p)
    inv = p["c"].post("/service-ops/invoices", json={"job_id": j["id"]}, headers=_o(p)).json()
    assert p["c"].get("/service-ops/invoices/%s/pdf" % inv["id"], headers=_o(p)).status_code == 402


def test_estimates_numbered_and_linked(ops):
    p = ops
    c = p["c"].post("/service-ops/customers", json={"name": "Est Co"}, headers=_o(p)).json()
    e = p["c"].post("/service-ops/estimates", json={"customer_id": c["id"], "discount": 50, "tax_rate": 0,
                                                    "lines": [{"description": "Windows", "quantity": 1, "rate": 400}]},
                    headers=_o(p)).json()
    assert e["number"] == "EST-1001" and e["total"] == 350 and e["status"] == "draft"
    assert p["c"].post("/service-ops/estimates/%s/status" % e["id"], json={"status": "accepted"},
                       headers=_o(p)).json()["status"] == "accepted"
    assert p["c"].post("/service-ops/estimates/%s/status" % e["id"], json={"status": "sent"},
                       headers=_o(p)).status_code == 409


# ── tenant isolation & role enforcement at the API ────────────────────────
def test_zero_cross_tenant_visibility(ops):
    p = ops
    c, loc, j = _setup_job(p)
    other = p["other"]
    _switch(p, org=other)
    oh = _u(p, p["outsider"], p["other"])
    assert p["c"].get("/service-ops/customers", headers=oh).json()["items"] == []
    assert p["c"].get("/service-ops/jobs", headers=oh).json()["items"] == []
    for path in ("/service-ops/jobs/%s" % j["id"], "/service-ops/customers/%s" % c["id"],
                 "/service-ops/locations/%s" % loc["id"]):
        assert p["c"].get(path, headers=oh).status_code == 404, path
    assert p["c"].post("/service-ops/jobs/%s/start" % j["id"], headers=oh).status_code == 404
    assert p["c"].post("/service-ops/jobs", json={"location_id": loc["id"]}, headers=oh).status_code == 404
    # A worker of company A can't see company B by asking for B's workspace.
    m, u = _worker(p)
    r = p["c"].get("/service-ops/jobs", headers=_u(p, u, other))
    assert r.status_code in (403, 404) or r.json()["items"] == []


def test_workers_and_buyers_are_kept_out_of_the_sales_portal_and_admin(ops):
    p = ops
    m, u = _worker(p)
    wh = _u(p, u)
    assert p["c"].get("/acquisition/status", headers=wh).status_code == 403
    assert p["c"].get("/acquisition/appointments", headers=wh).status_code == 403
    assert p["c"].post("/service-ops/team", json={"email": "x@example.com", "full_name": "X"}, headers=wh).status_code == 403
    assert p["c"].get("/service-ops/invoices", headers=wh).status_code == 403
    assert p["c"].get("/god/acquisition/workforce", headers=wh).status_code in (401, 403)
    assert p["c"].get("/acquisition-ops/%s/service-ops" % p["shine"].id, headers=wh).status_code in (401, 403)
    assert p["c"].get("/service-ops/settings", headers=wh).status_code == 200      # reading, not changing
    assert p["c"].put("/service-ops/settings", json={"business_name": "x"}, headers=wh).status_code == 403
    # Only a company admin can create another company admin.
    mgr_m, mgr = _worker(p, "mgr@example.com", role="ops_manager")
    r = p["c"].post("/service-ops/team", json={"email": "boss@example.com", "full_name": "B", "role": "company_admin"},
                    headers=_u(p, mgr))
    assert r.status_code == 403
    # Deactivated member loses access.
    p["c"].put("/service-ops/team/%s" % m["id"], json={"active": False}, headers=_o(p))
    assert p["c"].get("/service-ops/jobs?mine=true", headers=wh).status_code == 403


def test_team_invite_returns_setup_link_and_sends_nothing(ops):
    p = ops
    from app.models.models import EmailMessage
    r = p["c"].post("/service-ops/team", json={"email": "new.tech@example.com", "full_name": "New Tech",
                                               "role": "team_lead", "team_name": "North"}, headers=_o(p)).json()
    assert r["setup_url"] and r["role"] == "team_lead" and r["team_name"] == "North"
    assert p["db"].query(EmailMessage).count() == 0
    team = p["c"].get("/service-ops/team", headers=_o(p)).json()["items"]
    assert any(t["email"] == "new.tech@example.com" for t in team)
    # A login from another company can't be pulled in.
    r = p["c"].post("/service-ops/team", json={"email": p["outsider"].email, "full_name": "X"}, headers=_o(p))
    assert r.status_code == 409


# ── optional buyer portal ─────────────────────────────────────────────────
def test_buyer_portal_scoped_revocable_and_isolated(ops):
    p = ops
    j = _finished_job(p)
    cust_id = j["customer"]["id"]
    other = p["c"].post("/service-ops/customers", json={"name": "Other Buyer Co", "location": {
        "name": "Other site", "service_category": "cleaning"}}, headers=_o(p)).json()
    inv = p["c"].post("/service-ops/buyers", json={"customer_id": cust_id, "email": "pm@example.com",
                                                   "full_name": "Pat Manager", "can_approve_estimates": True},
                      headers=_o(p)).json()
    assert inv["setup_url"] and inv["status"] == "invited"
    inv2 = p["c"].post("/service-ops/buyers", json={"customer_id": other["id"], "email": "pm2@example.com",
                                                    "full_name": "Other PM"}, headers=_o(p)).json()
    bu = p["db"].query(User).filter(User.email == "pm@example.com").one()
    bu.must_change_password = False
    p["db"].commit()
    bh = _u(p, bu)
    home = p["c"].get("/service-ops/buyer/home", headers=bh).json()
    assert home["customer"]["id"] == cust_id and len(home["locations"]) == 1
    hist = p["c"].get("/service-ops/buyer/history", headers=bh).json()["items"]
    assert [h["id"] for h in hist] == [j["id"]]
    detail = p["c"].get("/service-ops/buyer/visits/%s" % j["id"], headers=bh).json()
    assert "internal_notes" not in detail and all(set(a) == {"name"} for a in detail["assigned"])
    # Cannot see the other customer's records, staff screens or the sales portal.
    other_job = p["c"].post("/service-ops/jobs", json={"location_id": other["locations"][0]["id"] if other.get("locations")
                                                       else p["c"].get("/service-ops/customers/%s" % other["id"],
                                                                       headers=_o(p)).json()["locations"][0]["id"],
                                                       "local_start": "2030-05-01T09:00"}, headers=_o(p)).json()
    assert p["c"].get("/service-ops/buyer/visits/%s" % other_job["id"], headers=bh).status_code == 404
    assert p["c"].get("/service-ops/jobs", headers=bh).status_code == 403
    assert p["c"].get("/acquisition/status", headers=bh).status_code == 403
    # Requests land with the company, scoped to this customer.
    rq = p["c"].post("/service-ops/buyer/requests", json={"kind": "new_service", "service_category": "pest_control",
                                                         "body": "Also need pest control"}, headers=bh)
    assert rq.status_code == 200 and rq.json()["status"] == "open"
    staff = p["c"].get("/service-ops/requests", headers=_o(p)).json()["items"]
    assert staff[0]["customer_id"] == cust_id
    # Invoices: drafts are invisible; issued ones and their PDF are visible.
    invc = p["c"].post("/service-ops/invoices", json={"job_id": j["id"]}, headers=_o(p)).json()
    assert p["c"].get("/service-ops/buyer/invoices", headers=bh).json()["items"] == []
    p["c"].post("/service-ops/invoices/%s/issue" % invc["id"], headers=_o(p))
    assert len(p["c"].get("/service-ops/buyer/invoices", headers=bh).json()["items"]) == 1
    assert p["c"].get("/service-ops/buyer/invoices/%s/pdf" % invc["id"], headers=bh).content[:5] == b"%PDF-"
    paid = p["c"].post("/service-ops/buyer/invoices/%s/payments" % invc["id"], json={"amount": 10}, headers=bh).json()
    assert paid["amount_reported"] == 10 and paid["amount_confirmed"] == 0 and paid["status"] == "issued"
    # Revoke: the login stops working here.
    p["c"].post("/service-ops/buyers/%s/revoke" % inv["id"], headers=_o(p))
    p["db"].refresh(bu)
    assert bu.is_active is False


def test_buyer_portal_off_means_no_buyer_access(pilot):
    p = pilot
    _switch(p, buyer_portal_enabled=False)
    c = p["c"].post("/service-ops/customers", json={"name": "B"}, headers=_o(p)).json()
    r = p["c"].post("/service-ops/buyers", json={"customer_id": c["id"], "email": "b@example.com", "full_name": "B"},
                    headers=_o(p))
    assert r.status_code == 402


# ── buyer network foundation ──────────────────────────────────────────────
def test_buyer_network_needs_consent_and_eligibility_and_copies_nothing(ops):
    p = ops
    g = _h(p["db"], p["god"])
    r = p["c"].post("/god/acquisition/buyer-network", json={"buyer_name": "Synthetic PM Group", "city": "Dallas",
                                                            "service_categories": ["cleaning", "pest_control"]},
                    headers=g).json()
    assert r["status"] == "new"
    elig = p["c"].get("/god/acquisition/buyer-network/%s/eligible" % r["id"], headers=g).json()["items"]
    shine = next(x for x in elig if x["organization_id"] == p["shine"].id)
    assert shine["eligible"] is True
    no = p["c"].post("/god/acquisition/buyer-network/%s/offer" % r["id"], json={"organization_id": p["shine"].id},
                     headers=g)
    assert no.status_code == 409 and "agreed" in no.json()["detail"]
    from app.models.service_ops_models import ServiceBuyerNetworkRequest
    row = p["db"].query(ServiceBuyerNetworkRequest).get(r["id"])
    row.buyer_authorized = True
    p["db"].commit()
    ok = p["c"].post("/god/acquisition/buyer-network/%s/offer" % r["id"], json={"organization_id": p["shine"].id},
                     headers=g).json()
    assert ok["status"] == "offered"
    offers = p["c"].get("/service-ops/network-offers", headers=_o(p)).json()["items"]
    assert [o["id"] for o in offers] == [r["id"]]
    acc = p["c"].post("/service-ops/network-offers/%s" % r["id"], json={"accept": True}, headers=_o(p)).json()
    assert acc["status"] == "accepted"
    assert any(c["name"] == "Synthetic PM Group" for c in p["c"].get("/service-ops/customers", headers=_o(p)).json()["items"])
    # A company that wasn't offered it can't see or take it.
    _switch(p, org=p["other"])
    oh = _u(p, p["outsider"], p["other"])
    assert p["c"].get("/service-ops/network-offers", headers=oh).json()["items"] == []
    assert p["c"].post("/service-ops/network-offers/%s" % r["id"], json={"accept": True}, headers=oh).status_code == 404


# ── reporting & suggestions & AI gate ─────────────────────────────────────
def test_report_suggestions_and_ai_gate(ops):
    p = ops
    j = _finished_job(p)
    rep = p["c"].get("/service-ops/report", headers=_o(p)).json()
    assert rep["completed_in_period"] == 1 and rep["active_customers"] >= 1
    sug = p["c"].get("/service-ops/suggestions", headers=_o(p)).json()["items"]
    assert any(s["kind"] == "not_invoiced" and s["job_id"] == j["id"] for s in sug)
    assert all(s["suggestion"] is True for s in sug)
    # AI assist is off unless switched on; nothing is called.
    from app.models.acquisition_models import AcquisitionAICall
    assert p["c"].get("/service-ops/jobs/%s/ai-summary" % j["id"], headers=_o(p)).status_code == 402
    assert p["db"].query(AcquisitionAICall).count() == 0


def test_portal_unchanged_when_feature_off(pilot):
    p = pilot
    assert p["c"].get("/acquisition/status", headers=_o(p)).status_code == 200
    me = p["c"].get("/service-ops/me", headers=_o(p)).json()
    assert me["ops_enabled"] is False and me["role"] is None


def test_invoice_dates_use_the_company_day_not_utc(ops):
    """Found in the live check: issued 7 PM Central on Nov 15 was dated Nov 16."""
    from app.services.service_ops import billing as BL
    p = ops
    evening_utc = datetime(2026, 11, 16, 0, 50)          # 6:50 PM Central, Nov 15
    assert BL.local_today(p["db"], p["shine"].id, evening_utc) == date(2026, 11, 15)
    p["c"].put("/service-ops/settings", json={"timezone": "Asia/Tokyo"}, headers=_o(p))
    assert BL.local_today(p["db"], p["shine"].id, evening_utc) == date(2026, 11, 16)
