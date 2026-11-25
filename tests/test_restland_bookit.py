"""Restland BookIT acceptance tests (directive s45) that run without a browser:
money, idempotency, record-level security, Evo 6 Block, ADD Leads, closing
a ticket. Synthetic data only."""
import json
from datetime import date, datetime, timedelta

import pytest

from app.models.bookit_models import (BookitAudit, BookitNotification, BookitReferralBatch, BookitReferralContact,
                                      BookitSale, BookitSaleLine)
from app.models.program_models import HandoffPendingEdit, ProgramTeamMember
from app.services.bookit import core, insights, network, sales, workflow
from app.services.programs import restland_sheet as RS
from tests.test_outreach_program import _lead_for, program  # noqa: F401
from tests.test_restland_handoff_2026_11_22 import _appt, _board, _prog, _run_team
from tests.test_restland_team_board import NOW, _booking, _serial
from tests.test_sci_appointment_setter import setter  # noqa: F401


class _U:
    def __init__(self, uid, name=None, email=None):
        self.id = uid
        self.full_name = name or uid
        self.email = email or ("platform@evosyspro.live" if uid == "owner" else uid + "@example.com")


def _live(db, prog):
    pol = json.loads(prog.booking_policy_json)
    pol["bookit"] = {"live": True}
    prog.booking_policy_json = json.dumps(pol)
    db.commit()


def _team(setter, sample_advisor):
    """Ann and Bob, one booked appointment each, Ann with a login."""
    db, org = setter["db"], setter["org"]
    prog = _prog(setter)
    l1, b1 = _booking(db, org, sample_advisor, "L001", "Tia")
    l2, b2 = _booking(db, org, sample_advisor, "L002", "Uma", day=datetime(2026, 12, 1, 10, 0))
    sheet = _board([("Ann", "TRUE"), ("Bob", "TRUE")])
    t = _serial(NOW)
    appts = [_appt(b1.id, t + 5), _appt(b2.id, t + 6)]
    _run_team(db, prog, sheet, appts, [], NOW)
    ann = db.query(ProgramTeamMember).filter_by(organization_id=org.id, name="Ann").one()
    ann.user_id = "u-ann"
    bob = db.query(ProgramTeamMember).filter_by(organization_id=org.id, name="Bob").one()
    bob.user_id = "u-bob"
    db.commit()
    from app.models.program_models import HandoffSheetRow as R
    owner = {r.row_id: r.assigned_to for r in db.query(R).filter_by(organization_id=org.id)}
    mine = b1 if owner[b1.id] == "Ann" else b2
    other = b2 if mine is b1 else b1
    return db, org, prog, sheet, appts, mine, other


# ── G. sales line items: exact server-side arithmetic ───────────────────────

def test_g_line_items_and_contract_discount():
    calc = sales.compute([{"category": "Cemetery property", "quantity": 2, "unit_price": "4,500.00"},
                          {"category": "Memorial / marker", "quantity": 1, "unit_price": 1800}],
                         contract_discount="$500")
    assert calc["gross_cents"] == 1_080_000 and calc["net_cents"] == 1_030_000
    assert calc["discount_cents"] == 50_000 and [l["gross_cents"] for l in calc["lines"]] == [900_000, 180_000]
    for bad in ([{"category": "X", "unit_price": "abc"}], [{"category": "X", "unit_price": -5}],
                [{"category": "", "unit_price": 5}], [{"category": "X", "quantity": 0, "unit_price": 5}]):
        with pytest.raises(core.BookitError):
            sales.compute(bad)
    with pytest.raises(core.BookitError):                     # discounts can't exceed the contract
        sales.compute([{"category": "X", "unit_price": 100}], contract_discount=200)
    assert sales.cents("0.105") == 11 and sales.cents("1,234.56") == 123456


# ── A. individual security ──────────────────────────────────────────────────

def test_a_each_advisor_sees_only_their_own(setter, sample_advisor):
    db, org, prog, sheet, appts, mine, other = _team(setter, sample_advisor)
    with pytest.raises(core.BookitError) as e:                # closed until opened to the team
        core.who(db, prog, _U("u-ann"), manager=False)
    assert e.value.status == 403
    _live(db, prog)
    ann = core.who(db, prog, _U("u-ann"), manager=False)
    with pytest.raises(core.BookitError):
        core.who(db, prog, _U("u-stranger"), manager=False)
    # appointment detail / ticket of someone else's appointment: 404, not 403
    with pytest.raises(core.BookitError) as e:
        workflow.appointment(db, prog, ann, other.id)
    assert e.value.status == 404
    with pytest.raises(core.BookitError):
        workflow.close_ticket(db, prog, ann, other.id, {"outcome": "Showed"})
    with pytest.raises(core.BookitError):
        core.require_family(db, prog, ann, other.lead_id)
    core.require_family(db, prog, ann, mine.lead_id)          # her own family is fine
    # her day only counts her own work
    day = insights.my_day(db, prog, ann)
    assert day["kpis"]["upcoming_appointments"] == 1
    # a manager sees the team; an advisor can't ask for someone else's day
    boss = core.who(db, prog, _U("owner"), manager=True)
    assert insights.my_day(db, prog, boss)["kpis"]["upcoming_appointments"] == 2
    assert insights.my_day(db, prog, ann, person="Bob")["kpis"]["upcoming_appointments"] == 1   # still her own
    # before BookIT is opened, another org admin isn't let in - only the owner's logins
    pol = json.loads(prog.booking_policy_json)
    pol["bookit"]["live"] = False
    prog.booking_policy_json = json.dumps(pol)
    db.commit()
    with pytest.raises(core.BookitError):
        core.who(db, prog, _U("u-admin", email="someone@example.com"), manager=True)
    assert core.who(db, prog, _U("owner"), manager=True).manager
    _live(db, prog)
    # suspending her access closes BookIT for her
    pol = json.loads(prog.booking_policy_json)
    pol["bookit"]["suspended"] = {ann.member_id: True}
    prog.booking_policy_json = json.dumps(pol)
    db.commit()
    with pytest.raises(core.BookitError):
        core.who(db, prog, _U("u-ann"), manager=False)


# ── H/I/J. one contract, pending isn't revenue, corrections are audited ─────

def test_h_i_j_sales_once_pending_then_corrected(setter, sample_advisor):
    db, org, prog, sheet, appts, mine, other = _team(setter, sample_advisor)
    _live(db, prog)
    ann = core.who(db, prog, _U("u-ann"), manager=False)
    att = workflow.attribution(db, prog, mine.lead_id, mine.id)
    assert att["assigned_advisor"] == "Ann" and att["acquisition_source"] in ("evosys_appointment", "independent")
    body = {"idempotency_key": "k1", "status": "pending", "lines": [], "notes": "thinking it over"}
    first = sales.record(db, org.id, ann, dict(body), dict(att))
    again = sales.record(db, org.id, ann, dict(body), dict(att))
    assert again["duplicate"] and again["sale"]["id"] == first["sale"]["id"]
    assert db.query(BookitSale).filter_by(organization_id=org.id).count() == 1
    perf = insights.performance(db, prog, ann, "lifetime")
    assert perf["metrics"]["closed_value_cents"] == 0 and perf["metrics"]["pending_opportunities"] == 1
    # she closes it later with the amounts
    sid = first["sale"]["id"]
    out = sales.correct(db, org.id, ann, sid, {"status": "closed", "sale_date": date.today().isoformat(), "lines": [
        {"category": "Cemetery property", "quantity": 2, "unit_price": 4500},
        {"category": "Memorial / marker", "quantity": 1, "unit_price": 1800}], "contract_discount": 500})
    assert out["sale"]["net_cents"] == 1_030_000 and out["sale"]["appointment_id"] == mine.id
    assert insights.performance(db, prog, ann, "lifetime")["metrics"]["closed_value_cents"] == 1_030_000
    # now it's closed: she can't change the money; management can, with a reason
    with pytest.raises(core.BookitError) as e:
        sales.correct(db, org.id, ann, sid, {"contract_discount": 0})
    assert e.value.status == 403
    boss = core.who(db, prog, _U("owner"), manager=True)
    with pytest.raises(core.BookitError):
        sales.correct(db, org.id, boss, sid, {"contract_discount": 1000})          # no reason
    fixed = sales.correct(db, org.id, boss, sid, {"contract_discount": 1000, "reason": "Signed contract shows $1,000"})
    assert fixed["sale"]["net_cents"] == 980_000
    trail = db.query(BookitAudit).filter_by(entity="sale", entity_id=sid, field="net_cents").all()
    assert any(a.old_value == "1030000" and a.new_value == "980000" and a.reason for a in trail)
    cc = insights.command_center(db, prog, boss, "lifetime")
    assert cc["team"] and sum(p["closed_value_cents"] for p in cc["team"]) == 980_000
    # Bob can't see or touch Ann's sale
    bob = core.who(db, prog, _U("u-bob"), manager=False)
    with pytest.raises(core.BookitError) as e:
        sales.correct(db, org.id, bob, sid, {"notes": "x"})
    assert e.value.status == 404
    assert sales.visible(db, org.id, bob) == []


# ── close appointment ticket ────────────────────────────────────────────────

def test_closing_a_ticket_requires_the_real_answers_and_reaches_the_sheet(setter, sample_advisor):
    db, org, prog, sheet, appts, mine, other = _team(setter, sample_advisor)
    _live(db, prog)
    ann = core.who(db, prog, _U("u-ann"), manager=False)
    draft = workflow.close_ticket(db, prog, ann, mine.id, {"outcome": "Showed"})
    assert draft["disposition"]["is_draft"]
    with pytest.raises(core.BookitError) as e:
        workflow.close_ticket(db, prog, ann, mine.id, {"close": True})
    assert "sales result" in str(e.value) and "Evo 6 Block" in str(e.value)
    with pytest.raises(core.BookitError) as e:                 # partial Evo 6 Block needs a reason
        workflow.close_ticket(db, prog, ann, mine.id, {"close": True, "sales_disposition": "no_sale",
                                                       "pta_status": "completed", "ppg_status": "provided",
                                                       "evo6_status": "partial", "final_notes": "Good visit"})
    assert "isn't complete" in str(e.value)
    done = workflow.close_ticket(db, prog, ann, mine.id, {
        "close": True, "sales_disposition": "no_sale", "pta_status": "completed", "ppg_status": "provided",
        "evo6_status": "partial", "evo6_reason": "Only 3 names today", "final_notes": "Good visit"})
    assert not done["disposition"]["is_draft"] and "open_evo6" in done["next"]
    pend = {e.key: e.value for e in db.query(HandoffPendingEdit).filter_by(row_id=mine.id)}
    assert pend["outcome"] == "Showed" and pend["sold"] == "No"
    out = _run_team(db, prog, sheet, appts, [], NOW + timedelta(minutes=2))
    assert out["board_edits"] >= 2
    assert workflow.paperwork_dict(workflow.paperwork(db, org.id, mine.lead_id))["pta_status"] == "completed"
    with pytest.raises(core.BookitError):                     # closed: the advisor can't rewrite it
        workflow.close_ticket(db, prog, ann, mine.id, {"outcome": "No-Show"})
    # a "closed sale" needs the sale recorded first
    boss = core.who(db, prog, _U("owner"), manager=True)
    with pytest.raises(core.BookitError) as e:
        workflow.close_ticket(db, prog, boss, mine.id, {"close": True, "sales_disposition": "closed_sale"})
    assert "record the sale first" in str(e.value)


# ── D. Evo 6 Block ──────────────────────────────────────────────────────────

def test_d_evo6_block_one_parent_six_independent_contacts(setter, sample_advisor):
    db, org, prog, sheet, appts, mine, other = _team(setter, sample_advisor)
    _live(db, prog)
    ann = core.who(db, prog, _U("u-ann"), manager=False)
    body = {"idempotency_key": "evo-1", "origin_lead_id": mine.lead_id, "source_appointment_id": mine.id}
    b = network.create_batch(db, prog, ann, dict(body))
    assert network.create_batch(db, prog, ann, dict(body))["id"] == b["id"]                # resubmit: one batch
    assert network.create_batch(db, prog, ann, dict(body, idempotency_key="evo-2"))["id"] == b["id"]  # same review
    people = [("Cara", "2145550301"), ("Dan", "2145550302"), ("Eve", "2145550303"), ("Finn", "2145550304"),
              ("Gus", "2145550305"), ("Hal", "2145550306")]
    for i, (n, ph) in enumerate(people):
        network.save_contact(db, prog, ann, b["id"], {"first_name": n, "last_name": "Test", "relationship": "Child",
                                                      "phone": ph, "outreach_authorized": i < 3})
    with pytest.raises(core.BookitError):                     # same person twice in one network
        network.save_contact(db, prog, ann, b["id"], {"first_name": "Cara", "phone": "(214) 555-0301"})
    with pytest.raises(core.BookitError):
        network.save_contact(db, prog, ann, b["id"], {"first_name": "Ivy", "phone": "555"})
    got = network.batch_dict(db, ann, db.query(BookitReferralBatch).get(b["id"]))
    assert db.query(BookitReferralBatch).filter_by(organization_id=org.id).count() == 1
    assert len(got["contacts"]) == 6 and got["collection_status"] == "complete"
    assert got["progress"]["validated"] == 6 and got["progress"]["eligible"] == 3
    assert all(c["lead_id"] for c in got["contacts"])           # each is a family record of their own
    # someone already on file is linked as a duplicate, and Ann isn't told whose
    from app.models.models import Lead
    existing = db.query(Lead).filter(Lead.id == other.lead_id).one()
    existing.phone = "12145550399"
    db.commit()
    dup = network.save_contact(db, prog, ann, b["id"], {"first_name": "Zed", "phone": "2145550399"})
    assert dup["validation_status"] == "duplicate" and "duplicate_of_lead_id" not in dup and not dup["eligible"]
    # independent journeys; nothing is enrolled without management + an approved template
    c0 = got["contacts"][0]["id"]
    assert network.enroll(db, prog, ann, c0, "request")["enrollment_state"] == "pending_approval"
    with pytest.raises(core.BookitError):
        network.enroll(db, prog, ann, got["contacts"][5]["id"], "request")    # not authorized for outreach
    with pytest.raises(core.BookitError):
        network.enroll(db, prog, ann, c0, "approve")                           # advisors don't approve
    boss = core.who(db, prog, _U("owner"), manager=True)
    with pytest.raises(core.BookitError) as e:
        network.enroll(db, prog, boss, c0, "approve")
    assert "template" in str(e.value)
    states = {c.id: c.enrollment_state for c in db.query(BookitReferralContact).filter_by(batch_id=b["id"])}
    assert list(states.values()).count("pending_approval") == 1                  # only that one moved
    # a contact's booking/sale keeps the Evo 6 Block origin
    att = workflow.attribution(db, prog, got["contacts"][1]["lead_id"], None)
    assert att["acquisition_source"] == "evo6_block" and att["referring_advisor"] == "Ann"
    # Bob can't open Ann's network
    bob = core.who(db, prog, _U("u-bob"), manager=False)
    with pytest.raises(core.BookitError):
        network._batch(db, prog, bob, b["id"])


# ── F. ADD Lead ─────────────────────────────────────────────────────────────

def test_f_add_lead_case_is_human_led_and_private(setter, sample_advisor):
    db, org, prog, sheet, appts, mine, other = _team(setter, sample_advisor)
    _live(db, prog)
    boss = core.who(db, prog, _U("owner"), manager=True)
    body = {"idempotency_key": "add-1", "deceased_name": "Pat Example", "contact_name": "Sam Example",
            "contact_relationship": "Son", "contact_phone": "2145550400", "immediate_needs": "Help with arrangements"}
    c = network.create_case(db, prog, boss, dict(body))
    assert network.create_case(db, prog, boss, dict(body))["id"] == c["id"]
    assert c["assigned_advisor"] in ("Ann", "Bob") and c["status"] == "intake"
    kinds = {n.kind for n in db.query(BookitNotification).filter_by(organization_id=org.id)}
    assert {"add_assigned", "add_new"} <= kinds
    with pytest.raises(core.BookitError):
        network.update_case(db, prog, boss, c["id"], {"status": "resolved"})     # needs a resolution
    owner = core.who(db, prog, _U("u-ann" if c["assigned_advisor"] == "Ann" else "u-bob"), manager=False)
    stranger = core.who(db, prog, _U("u-bob" if c["assigned_advisor"] == "Ann" else "u-ann"), manager=False)
    with pytest.raises(core.BookitError):
        network.update_case(db, prog, stranger, c["id"], {"notes": "x"})
    assert network.cases(db, prog, stranger) == []
    with pytest.raises(core.BookitError):
        network.update_case(db, prog, owner, c["id"], {"assigned_advisor": "Ann"})  # only management
    net = network.create_batch(db, prog, owner, {"idempotency_key": "addnet-1", "kind": "add", "add_case_id": c["id"]})
    k = network.save_contact(db, prog, owner, net["id"], {"first_name": "Lee", "phone": "2145550401",
                                                          "outreach_authorized": True})
    assert workflow.attribution(db, prog, k["lead_id"], None)["acquisition_source"] == "add_lead"
    done = network.update_case(db, prog, owner, c["id"], {"resolution": "Arrangements made", "status": "resolved"})
    assert done["status"] == "resolved"


def test_service_request_needs_a_resolution_to_close(setter, sample_advisor):
    db, org, prog, sheet, appts, mine, other = _team(setter, sample_advisor)
    _live(db, prog)
    ann = core.who(db, prog, _U("u-ann"), manager=False)
    r = workflow.save_service_request(db, prog, ann, {"description": "Niche cleaning", "lead_id": mine.lead_id})
    assert r["assigned_to"] == "Ann"
    with pytest.raises(core.BookitError):
        workflow.save_service_request(db, prog, ann, {"status": "resolved"}, r["id"])
    with pytest.raises(core.BookitError):
        workflow.save_service_request(db, prog, ann, {"description": "x", "lead_id": other.lead_id})
    done = workflow.save_service_request(db, prog, ann, {"resolution": "Cleaned", "status": "resolved"}, r["id"])
    assert done["status"] == "resolved"
    assert insights.window("custom", "2026-11-30", "2026-11-01")[0] == date(2026, 11, 1)
    assert insights.rate(0, 0) is None
