"""Restland BookIT acceptance tests (directive s45) that run without a browser:
money, idempotency, record-level security, File Review 6 Block, At-Need 6 Blocks, closing
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
    assert "sales result" in str(e.value) and "File Review 6 Block" in str(e.value)
    with pytest.raises(core.BookitError) as e:                 # partial File Review 6 Block needs a reason
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


# ── D. File Review 6 Block ──────────────────────────────────────────────────────────

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
    # a contact's booking/sale keeps the File Review 6 Block origin
    att = workflow.attribution(db, prog, got["contacts"][1]["lead_id"], None)
    assert att["acquisition_source"] == "evo6_block" and att["referring_advisor"] == "Ann"
    # Bob can't open Ann's network
    bob = core.who(db, prog, _U("u-bob"), manager=False)
    with pytest.raises(core.BookitError):
        network._batch(db, prog, bob, b["id"])


def test_a_6_block_stays_with_the_advisor_who_entered_it(setter, sample_advisor):
    """Owner, Nov 26: the 6 Block names an advisor collects stay with that
    advisor's login - even when the family it came from is reassigned, and
    even when they are taken out of the rotation."""
    db, org, prog, sheet, appts, mine, other = _team(setter, sample_advisor)
    _live(db, prog)
    ann = core.who(db, prog, _U("u-ann"), manager=False)
    b = network.create_batch(db, prog, ann, {"idempotency_key": "keep-1", "origin_lead_id": mine.lead_id,
                                             "source_appointment_id": mine.id})
    c = network.save_contact(db, prog, ann, b["id"], {"first_name": "Kim", "last_name": "Test", "phone": "2145550311"})
    # the family the network came from is handed to Bob, and Ann leaves the rotation
    from app.models.program_models import HandoffSheetRow as R
    for r in db.query(R).filter_by(organization_id=org.id, lead_id=mine.lead_id):
        r.assigned_to = "Bob"
    db.query(ProgramTeamMember).filter_by(organization_id=org.id, name="Ann").one().active = False
    db.commit()
    ann = core.who(db, prog, _U("u-ann"), manager=False)          # Ann logs in again
    bob = core.who(db, prog, _U("u-bob"), manager=False)
    assert network._batch(db, prog, ann, b["id"]).submitting_advisor == "Ann"
    core.require_family(db, prog, ann, c["lead_id"])                # the name she collected is still hers
    with pytest.raises(core.BookitError):
        network._batch(db, prog, bob, b["id"])                      # Bob doesn't get her network
    with pytest.raises(core.BookitError):
        core.require_family(db, prog, bob, c["lead_id"])


# ── F. At-Need 6 Block ─────────────────────────────────────────────────────────────

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


# ── FSA Coach (owner, Nov 26) ───────────────────────────────────────────────

class _FakeAI:
    """Stands in for the AI provider; records what it was sent."""
    def __init__(self):
        self.sent = []
        outer = self

        class _C:
            def create(self, model, messages, **kw):
                outer.sent.append({"model": model, "messages": messages, **kw})

                class _M:
                    content = "Call them back this afternoon."

                class _Ch:
                    message = _M()

                class _U:
                    prompt_tokens, completion_tokens = 1200, 80

                class _R:
                    choices, usage = [_Ch()], _U()
                return _R()

        class _Chat:
            completions = _C()
        self.chat = _Chat()


def test_fsa_coach_reads_only_your_families_and_counts_against_your_limit(setter, sample_advisor, monkeypatch):
    from app.services.bookit import coach
    from app.models.models import Lead
    db, org, prog, sheet, appts, mine, other = _team(setter, sample_advisor)
    _live(db, prog)
    fake = _FakeAI()
    monkeypatch.setattr(coach, "_get_client", lambda: fake)
    monkeypatch.setenv("AI_MANUAL_ACTIONS_ENABLED", "true")
    ann = core.who(db, prog, _U("u-ann"), manager=False)
    my_name = db.query(Lead).get(mine.lead_id).first_name
    their_name = db.query(Lead).get(other.lead_id).first_name
    out = coach.ask(db, prog, ann, _U("u-ann"), "What should I say to %s and %s?" % (my_name, their_name))
    assert out["answer"].startswith("Call them back")
    sent = "\n".join(m["content"] for m in fake.sent[0]["messages"])
    assert fake.sent[0]["model"] == "gpt-4o-mini" and fake.sent[0]["max_tokens"] == coach.MAX_ANSWER_TOKENS
    assert "FAMILY DETAIL: %s" % my_name in sent                  # her family, in detail
    assert "FAMILY DETAIL: %s" % their_name not in sent           # Bob's family never reaches the AI
    # counted (tokens only - the question isn't stored)
    from app.models.bookit_models import BookitAudit
    rows = db.query(BookitAudit).filter_by(organization_id=org.id, entity="coach").all()
    assert len(rows) == 1 and rows[0].new_value == "1200/80" and their_name not in (rows[0].new_value or "")
    # the per-person limit holds
    boss = core.who(db, prog, _U("owner"), manager=True)
    coach.save_settings(db, prog, boss, {"per_person_day": 1})
    with pytest.raises(core.BookitError) as e:
        coach.ask(db, prog, ann, _U("u-ann"), "Who should I call?")
    assert e.value.status == 429 and len(fake.sent) == 1
    with pytest.raises(core.BookitError):
        coach.save_settings(db, prog, ann, {"per_person_day": 99})     # advisors can't raise it
    coach.save_settings(db, prog, boss, {"per_person_day": 5, "enabled": False})
    with pytest.raises(core.BookitError):
        coach.ask(db, prog, ann, _U("u-ann"), "Who should I call?")    # switched off
    rep = coach.usage_report(db, prog)
    assert rep["total"]["today"] == 1 and rep["people"][0]["name"] == "Ann" and rep["total"]["cost_month"] > 0


def test_fsa_coach_says_plainly_when_the_ai_account_is_out_of_credit(setter, sample_advisor, monkeypatch):
    from app.services.bookit import coach
    db, org, prog, sheet, appts, mine, other = _team(setter, sample_advisor)
    _live(db, prog)

    class _Broke:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    raise RuntimeError("Error code: 429 - {'error': {'type': 'insufficient_quota', "
                                       "'code': 'credit_balance_exhausted'}}")
    monkeypatch.setattr(coach, "_get_client", lambda: _Broke())
    ann = core.who(db, prog, _U("u-ann"), manager=False)
    with pytest.raises(core.BookitError) as e:
        coach.ask(db, prog, ann, _U("u-ann"), "Who should I call?")
    assert "taking a break" in str(e.value) and "credit" not in str(e.value)   # advisors get a plain line
    assert coach.remaining(db, prog, ann)["used_today"] == 0          # a failed question isn't counted
    boss = core.who(db, prog, _U("owner"), manager=True)
    with pytest.raises(core.BookitError) as e:
        coach.ask(db, prog, boss, _U("owner"), "Who should I call?")
    assert "out of credit" in str(e.value) and "platform.openai.com" in str(e.value)
    assert db.query(BookitNotification).filter_by(organization_id=org.id, kind="coach_credit").count() == 1


# ── logins and the owner's test login (owner, Nov 26) ───────────────────────

def test_create_login_links_the_team_member_and_sends_nothing(setter, sample_advisor, monkeypatch):
    from app.models.models import User
    from app.services.bookit import logins
    db, org, prog, sheet, appts, mine, other = _team(setter, sample_advisor)
    sent = []
    monkeypatch.setattr(logins, "_email", lambda u, url, base: sent.append((u.email, url)) or True)
    boss_user = db.query(User).filter(User.id == sample_advisor.id).first()
    boss = core.who(db, prog, _U("owner"), manager=True)
    cara = ProgramTeamMember(organization_id=org.id, name="Cara", email="cara.test@nsmg.example", active=True)
    db.add(cara)
    db.commit()
    out = logins.create_login(db, prog, boss, boss_user, cara.id)
    db.commit()
    u = db.query(User).filter(User.email == "cara.test@nsmg.example").one()
    assert out["new_account"] and cara.user_id == u.id and u.must_change_password and not sent
    with pytest.raises(core.BookitError):                       # not before BookIT is opened to the team
        core.who(db, prog, u, manager=False)
    _live(db, prog)
    assert core.who(db, prog, u, manager=False).name == "Cara"   # her own name, so her own work
    r = logins.send_setup(db, prog, boss, boss_user, cara.id, "https://app.evosyspro.live", True)
    assert r["emailed"] and sent[0][0] == u.email and "/activate?token=stf_" in sent[0][1]
    ann = core.who(db, prog, _U("u-ann"), manager=False)
    with pytest.raises(core.BookitError):
        logins.create_login(db, prog, ann, boss_user, cara.id)    # advisors can't make logins


def test_test_login_shows_one_advisors_side_view_only_before_launch(setter, sample_advisor):
    from app.models.models import User
    from app.services.bookit import logins
    db, org, prog, sheet, appts, mine, other = _team(setter, sample_advisor)
    boss_user = db.query(User).filter(User.id == sample_advisor.id).first()
    boss = core.who(db, prog, _U("owner"), manager=True)
    with pytest.raises(core.BookitError):                       # not one of his real logins
        logins.set_test_login(db, prog, boss, boss_user, boss_user.email, "Ann")
    t = logins.set_test_login(db, prog, boss, boss_user, "owner+bookittest@example.com", "Ann")
    db.commit()
    u = db.query(User).filter(User.email == "owner+bookittest@example.com").one()
    w = core.who(db, prog, u, manager=False)                     # BookIT isn't live, and it still gets in
    assert w.name == "Ann" and w.view_only and not w.manager and core.is_view_only(db, u)
    assert "Test Advisor" not in core.team_names(db, org.id)    # never in a list or the rotation
    logins.set_test_login(db, prog, boss, boss_user, None, "Bob")
    assert core.who(db, prog, u, manager=False).name == "Bob"
    assert not core.is_view_only(db, boss_user)


def test_a_bookit_login_reaches_bookit_and_nothing_else_of_the_workspace(setter, sample_advisor):
    """Owner, Nov 26: the test login opened all of Restland Live. A login that
    exists for BookIT must see BookIT only - server-side, every route."""
    from app.deps import _bookit_fence
    from app.models.models import User
    from app.services import workspace_access as WA
    from app.services.bookit import logins
    db, org, prog, sheet, appts, mine, other = _team(setter, sample_advisor)
    boss_user = db.query(User).filter(User.id == sample_advisor.id).first()
    boss = core.who(db, prog, _U("owner"), manager=True)
    logins.set_test_login(db, prog, boss, boss_user, "tester+bk@example.com", "Ann")
    db.commit()
    u = db.query(User).filter(User.email == "tester+bk@example.com").one()
    assert WA.bookit_only_orgs(db, u) == {org.id}

    class _Req:
        def __init__(self, path, method="GET"):
            self.method, self.headers = method, {"x-workspace-id": org.id}
            self.url = type("U", (), {"path": path})()
    for blocked in ("/program/me", "/program/live/summary", "/leads", "/conversations/inbox", "/program/campaigns/x"):
        assert _bookit_fence(db, u, _Req(blocked)) == "This login is for Restland BookIT only."
    assert _bookit_fence(db, u, _Req("/outbox/send", "POST"))
    for allowed in ("/restland-bookit/me", "/restland-bookit/work", "/auth/my-contexts", "/launch/me"):
        assert _bookit_fence(db, u, _Req(allowed)) is None
    ctx = WA.authorized_contexts(db, u)
    assert not [w for w in ctx["workspace_contexts"] if w.get("organization_id") == org.id]
    assert ctx["bookit_contexts"] and ctx["default_context"]["path"] == "/workspaces" and ctx["restland_staff"]
    # the owner and managers are never fenced
    assert WA.bookit_only_orgs(db, boss_user) == set()


def test_my_quick_view_counts_only_your_own_booked_next_7_days_by_visit_type(setter, sample_advisor):
    from datetime import date as _d
    from app.services.programs import team_board as TB
    db, org, prog, sheet, appts, mine, other = _team(setter, sample_advisor)
    _live(db, prog)
    ann = core.who(db, prog, _U("u-ann"), manager=False)
    bob = core.who(db, prog, _U("u-bob"), manager=False)
    mine_row = [a for a in TB.work(db, prog, person="Ann")["appointments"] if a["id"] == mine.id][0]
    day = _d.fromisoformat(mine_row["date"])
    mine.meeting_type = None
    db.commit()
    q = insights.quick_view(db, prog, ann, today=day)
    assert q["total"] == 1 and q["unspecified"] == 1 and q["onsite"] == 0      # missing type is never guessed
    assert q["next"]["id"] == mine.id
    for mt, key in (("office", "onsite"), ("video", "call"), ("home", "offsite")):
        mine.meeting_type = mt
        db.commit()
        q = insights.quick_view(db, prog, ann, today=day)
        assert q[key] == 1 and q["total"] == 1 and q["onsite"] + q["call"] + q["offsite"] + q["unspecified"] == 1
    # Bob's own appointment never shows in Ann's view, and a week with nothing is a zero
    assert other.id not in {(q["next"] or {}).get("id")}
    later = insights.quick_view(db, prog, ann, today=day + __import__("datetime").timedelta(days=30))
    assert later["total"] == 0 and later["next"] is None
    # cancelled is excluded
    mine.status = "cancelled"
    db.commit()
    assert insights.quick_view(db, prog, ann, today=day)["total"] == 0
    # a manager with no seat gets no organization totals
    boss = core.who(db, prog, _U("owner"), manager=True)
    assert "total" not in insights.quick_view(db, prog, boss, today=day)
    assert TB.visit_kind("remote") == "call" and TB.visit_kind("weird") == "unknown"


def test_bookit_cancel_and_reschedule_reach_only_your_own_and_never_message_in_tests(setter, sample_advisor, monkeypatch):
    """Owner, Nov 26: booking changes from BookIT, tested on test data only."""
    from app.services.programs import appointment_notifications as AN
    from app.services.programs import appointment_ops as OPS
    sent = []
    monkeypatch.setattr(AN, "on_cancelled", lambda db, b, **k: sent.append(("cancel", b.id)) or {})
    monkeypatch.setattr(AN, "on_booked", lambda db, b, **k: sent.append(("moved", b.id)) or {})
    monkeypatch.setattr(AN, "run", lambda *a, **k: None)
    db, org, prog, sheet, appts, mine, other = _team(setter, sample_advisor)
    _live(db, prog)
    ann = core.who(db, prog, _U("u-ann"), manager=False)
    with pytest.raises(core.BookitError):
        workflow.appointment(db, prog, ann, other.id)           # not hers: refused before anything moves
    b, _ = workflow.appointment(db, prog, ann, mine.id)
    OPS.cancel(db, prog, b, _U("u-ann"), acknowledge=True)
    assert b.status == "cancelled" and sent == [("cancel", mine.id)]
    with pytest.raises(OPS.OpError):
        OPS.cancel(db, prog, b, _U("u-ann"))                      # only once
    with pytest.raises(OPS.OpError):
        OPS.reschedule(db, prog, b, datetime(2026, 12, 3, 10, 0), _U("u-ann"))


def test_management_can_hold_someone_out_of_the_rotation(setter, sample_advisor):
    db, org, prog, sheet, appts, mine, other = _team(setter, sample_advisor)
    cfg = RS._cfg(prog)
    cfg["held"] = ["Ann"]
    RS._save_cfg(prog, cfg)
    db.commit()
    l3, b3 = _booking(db, org, sample_advisor, "L003", "Vera", day=datetime(2026, 12, 2, 10, 0))
    _run_team(db, prog, sheet, appts + [_appt(b3.id, _serial(NOW) + 7)], [], NOW)
    from app.models.program_models import HandoffSheetRow as R
    row = db.query(R).filter_by(organization_id=org.id, row_id=b3.id).one()
    assert row.assigned_to == "Bob"                               # Ann is held out; her own work stays hers
    assert db.query(R).filter_by(organization_id=org.id, row_id=mine.id).one().assigned_to == "Ann"


# ── Revision SS1-SS6 (owner, Nov 26) ─────────────────────────────────────────

def _fu_case(setter, sample_advisor, text="call me please"):
    """A real family message -> an open follow-up assigned to Ann."""
    from tests.test_restland_handoff_2026_11_22 import _fu
    from tests.test_sci_appointment_setter import _Fake, _d, _run
    db, org = setter["db"], setter["org"]
    prog = _prog(setter)
    lead = _lead_for(db, org, sample_advisor, "L003", phone="2145550103", first="Ivy")
    _, conv = _run(db, lead, text, _Fake([_d("callback", "Sure, I'll call you.", callback_window="today")]))
    sheet = _board([("Ann", "TRUE")])
    fus = [_fu("FU-" + conv.id)]
    _run_team(db, prog, sheet, [], fus, NOW)
    return db, org, prog, lead, conv


def test_follow_up_shows_its_real_last_activity_and_new_reply(setter, sample_advisor):
    from app.services.programs import team_board as TB
    db, org, prog, lead, conv = _fu_case(setter, sample_advisor)
    f = [x for x in TB.work(db, prog)["follow_ups"] if x["id"] == "FU-" + conv.id][0]
    assert f["activity"]["kind"] == "Customer replied" and f["activity"]["at"]
    assert f["new_reply"] is True and f["replied_at"] is None
    first = f["activity"]["at"]
    # loading again never makes it look newer
    again = [x for x in TB.work(db, prog)["follow_ups"] if x["id"] == "FU-" + conv.id][0]
    assert again["activity"]["at"] == first
    # a person replies by text: the reply is logged with its time and channel; the family's message stays
    from app.models.models import Message
    from app.services import send_source as SS
    db.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="Calling you at 3.",
                   send_source=SS.MANUAL, sent_at=datetime.utcnow() + timedelta(minutes=5), delivery_status="delivered"))
    db.commit()
    f = [x for x in TB.work(db, prog)["follow_ups"] if x["id"] == "FU-" + conv.id][0]
    assert f["new_reply"] is False and f["replied_by"] == "text" and f["replied_at"]
    assert f["activity"]["kind"] == "Advisor replied by text" and f["family_at"]


def test_a_family_message_alerts_the_assigned_advisor_once_and_reading_does_not_close_it(setter, sample_advisor):
    from app.services.bookit import alerts
    db, org, prog, lead, conv = _fu_case(setter, sample_advisor)
    _live(db, prog)
    assert alerts.sweep(db, prog, force=True) == 1
    assert alerts.sweep(db, prog, force=True) == 0                     # retries never duplicate
    n = db.query(BookitNotification).filter_by(organization_id=org.id, kind="family_reply").one()
    assert n.recipient == "Ann" and n.ref_type == "follow_up" and n.ref_id == "FU-" + conv.id
    db.query(ProgramTeamMember).filter_by(organization_id=org.id, name="Ann").one().user_id = "u-ann"
    db.commit()
    ann = core.who(db, prog, _U("u-ann"), manager=False)
    assert [x["id"] for x in core.notifications(db, org.id, ann, unread_only=True)] == [n.id]
    core.mark_read(db, org.id, ann, [n.id])
    db.commit()
    db.refresh(conv)
    assert conv.state == "callback_requested"                          # reading the alert is not completing it
    # our own outgoing message never alerts
    from app.models.models import Message
    db.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="Thanks!",
                   sent_at=datetime.utcnow(), delivery_status="delivered"))
    db.commit()
    assert alerts.sweep(db, prog, force=True) == 0


def test_6_block_view_and_start_never_create_from_a_read_only_view(setter, sample_advisor):
    db, org, prog, sheet, appts, mine, other = _team(setter, sample_advisor)
    _live(db, prog)
    mine.status = "completed"                                         # the file review happened (Showed)
    db.commit()
    ann = core.who(db, prog, _U("u-ann"), manager=False)
    r = network.reviews(db, prog, ann)
    row = [x for x in r["reviews"] if x["appointment_id"] == mine.id][0]
    assert row["network"] is None and row["can_start"] is True and r["totals"]["networks"] == 0
    assert db.query(BookitReferralBatch).count() == 0                  # listing creates nothing
    # the read-only preview sees it, may not start it
    ro = core.Who(_U("u-ann"), False, "Ann", ann.member_id, view_only=True)
    assert [x for x in network.reviews(db, prog, ro)["reviews"] if x["appointment_id"] == mine.id][0]["can_start"] is False
    assert db.query(BookitReferralBatch).count() == 0
    # a manager starting it files it under the advisor who did the review; Ann opens it
    boss = core.who(db, prog, _U("owner"), manager=True)
    b = network.create_batch(db, prog, boss, {"idempotency_key": "m-1", "origin_lead_id": mine.lead_id,
                                              "source_appointment_id": mine.id})
    db.commit()
    assert b["submitting_advisor"] == "Ann"
    assert network._batch(db, prog, ann, b["id"]).id == b["id"]
    # retries and a second Start reuse the same network
    again = network.create_batch(db, prog, ann, {"idempotency_key": "a-2", "origin_lead_id": mine.lead_id,
                                                 "source_appointment_id": mine.id})
    assert again["id"] == b["id"] and db.query(BookitReferralBatch).count() == 1
    row = [x for x in network.reviews(db, prog, ann)["reviews"] if x["appointment_id"] == mine.id][0]
    assert row["network"]["id"] == b["id"] and row["can_start"] is False
    # Bob can't start a network on Ann's review
    bob = core.who(db, prog, _U("u-bob"), manager=False)
    with pytest.raises(core.BookitError):
        network.create_batch(db, prog, bob, {"idempotency_key": "b-1", "origin_lead_id": mine.lead_id,
                                             "source_appointment_id": other.id if False else mine.id})


def test_a_network_started_from_the_owner_account_belongs_to_the_reviews_advisor(setter, sample_advisor):
    """Nov 26: one network was started from the owner's account before the fix
    and filed under the owner's name - the review's advisor must still see it."""
    db, org, prog, sheet, appts, mine, other = _team(setter, sample_advisor)
    _live(db, prog)
    mine.status = "completed"
    db.add(BookitReferralBatch(organization_id=org.id, idempotency_key="old-1", kind="evo6", origin_lead_id=mine.lead_id,
                               source_appointment_id=mine.id, submitting_advisor="EvoSys Platform User"))
    db.commit()
    ann = core.who(db, prog, _U("u-ann"), manager=False)
    bob = core.who(db, prog, _U("u-bob"), manager=False)
    row = [x for x in network.reviews(db, prog, ann)["reviews"] if x["appointment_id"] == mine.id][0]
    assert row["network"] and network._batch(db, prog, ann, row["network"]["id"])
    assert len(network.batches(db, prog, ann, "evo6")) == 1
    with pytest.raises(core.BookitError):
        network._batch(db, prog, bob, row["network"]["id"])


def test_reply_alerts_only_for_follow_ups_on_the_board_and_one_per_burst(setter, sample_advisor):
    from app.models.program_models import ProgramConversationTurn as T
    from app.services.bookit import alerts
    from tests.test_sci_appointment_setter import _Fake, _d, _run
    db, org, prog, lead, conv = _fu_case(setter, sample_advisor)
    _live(db, prog)
    # a family whose conversation isn't on the board alerts nobody (not management either)
    other = _lead_for(db, org, sample_advisor, "L004", phone="2145550104", first="Dana")
    _run(db, other, "hello?", _Fake([_d("callback", "Sure.", callback_window="today")]))
    assert alerts.sweep(db, prog, force=True) == 1
    assert db.query(BookitNotification).filter_by(organization_id=org.id, kind="family_reply").count() == 1
    assert db.query(BookitNotification).filter_by(organization_id=org.id, recipient=core.MANAGEMENT).count() == 0
    # a second message that arrived before that alert is covered by it
    db.add(T(conversation_id=conv.id, organization_id=org.id, lead_id=lead.id, role="family", text="also, tomorrow?",
             created_at=datetime.utcnow() - timedelta(seconds=5), response_id="r-2"))
    db.commit()
    assert alerts.sweep(db, prog, force=True) == 0
    # a new one afterwards raises a new alert
    db.add(T(conversation_id=conv.id, organization_id=org.id, lead_id=lead.id, role="family", text="hello again",
             created_at=datetime.utcnow() + timedelta(minutes=1), response_id="r-3"))
    db.commit()
    assert alerts.sweep(db, prog, force=True, now=datetime.utcnow() + timedelta(minutes=2)) == 1


# ── booking a new appointment from BookIT (owner, Nov 27) ───────────────────

def test_book_a_new_appointment_from_bookit_only_your_own_once(setter, sample_advisor):
    from unittest.mock import patch
    from app.models.models import BookingLink
    from tests.test_sci_appointment_setter import SLOTS
    db, org, prog, lead, conv = _fu_case(setter, sample_advisor)
    prog.booking_user_id = sample_advisor.id
    _live(db, prog)
    ann_m = db.query(ProgramTeamMember).filter_by(organization_id=org.id, name="Ann").one()
    ann_m.user_id = "u-ann"
    db.commit()
    ann = core.who(db, prog, _U("u-ann"), manager=False)
    slot = SLOTS[0].strftime("%Y-%m-%dT%H:%M")
    body = {"lead_id": lead.id, "slot": slot, "how": "At Restland", "notify": False}
    with pytest.raises(core.BookitError):                                      # the past is refused
        workflow.new_appointment(db, prog, ann, dict(body, slot="2020-01-01T10:00"))
    with pytest.raises(core.BookitError):                                      # a home visit needs the address
        workflow.new_appointment(db, prog, ann, dict(body, how="At Their Home"))
    ro = core.Who(_U("u-ann"), False, "Ann", ann.member_id, view_only=True)
    with pytest.raises(core.BookitError):                                      # never from the preview
        workflow.new_appointment(db, prog, ro, dict(body))
    assert db.query(BookingLink).filter_by(lead_id=lead.id).count() == 0
    with patch("app.services.programs.alert_ledger.page", return_value=[]):
        r = workflow.new_appointment(db, prog, ann, dict(body), now=datetime(2026, 11, 23, 15, 0))
        db.commit()
        assert r["booked"] and r["booking_id"] and not r["already"]
        again = workflow.new_appointment(db, prog, ann, dict(body), now=datetime(2026, 11, 23, 15, 0))
    assert again["already"] and again["booking_id"] == r["booking_id"]       # a retry is the same booking
    b = db.query(BookingLink).filter_by(lead_id=lead.id).one()
    assert b.booked_time == SLOTS[0] and b.status in ("booked", "confirmed")
    from app.models.work_models import LeadNote
    assert any("Restland BookIT (Ann): booked" in n.body for n in db.query(LeadNote).filter_by(lead_id=lead.id))
    # someone else's family is refused
    db.add(ProgramTeamMember(organization_id=org.id, name="Bob", user_id="u-bob", active=True))
    db.commit()
    bob = core.who(db, prog, _U("u-bob"), manager=False)
    with pytest.raises(core.BookitError):
        workflow.new_appointment(db, prog, bob, dict(body))


def test_preview_access_is_owner_only_and_adds_a_login(setter, sample_advisor):
    db, org, prog, sheet, appts, mine, other = _team(setter, sample_advisor)
    co = _U("u-co", "Co Owner", "co@example.com")
    assert not core.previewer(prog, co)
    boss = core.who(db, prog, _U("owner"), manager=True)
    core.set_preview_emails(db, prog, boss, ["Co@Example.com", "not-an-email", "platform@evosyspro.live"])
    db.commit()
    assert core.previewer(prog, co)
    assert core.preview_emails(prog).count("platform@evosyspro.live") == 1
    assert core.who(db, prog, co, manager=True).manager                        # the manager side, before launch
    assert core.cfg(prog)["preview_emails"] == ["co@example.com"]


def test_restland_setup_email_never_names_the_platform():
    from app.services.bookit import logins
    html = logins.setup_email("Ann", "https://app.example/activate?token=t&brand=restland", "https://app.example")
    assert "EvoSys" not in html and "evosys" not in html.lower()
    assert "https://app.example/restland" in html and "brand=restland" in html
    assert logins.sender().from_name == "Restland BookIT"
    assert logins.sender().from_email == "support@evosyspro.live"           # never the server's default sender


# ── setter fixes (owner, Nov 27: Leon Folks) ───────────────────────────────

def test_opener_times_are_read_from_the_opening_text():
    from app.services.programs.ai_setter import opener_times
    sent = datetime(2026, 11, 27, 9, 6)
    t = "What time tomorrow works best for you, 10:00 AM or 2:30 PM? - Mike"
    assert opener_times(t, sent) == [datetime(2026, 11, 28, 10, 0), datetime(2026, 11, 28, 14, 30)]
    assert opener_times("Would 10:00 AM work?", sent) == []                    # no day named: nothing


def test_a_see_you_line_without_a_booking_is_blocked():
    from app.services.programs.ai_setter import _CLAIM_BOOKED
    assert _CLAIM_BOOKED.search("Great, Leon. I'll see you tomorrow at 2:30 PM.")
    assert _CLAIM_BOOKED.search("Looking forward to seeing you on Saturday.")
    assert not _CLAIM_BOOKED.search("Would Friday or Saturday work for you?")


def test_restland_offers_no_same_day_times(setter, monkeypatch):
    from app.services.programs import appointments as A
    from app.services.programs import booking_policy as BP
    prog = _prog(setter)
    assert not A.same_day_off(prog)
    monkeypatch.setattr(BP, "NO_SAME_DAY_ORGS", {prog.organization_id})
    assert A.same_day_off(prog)
    assert A.slots_on_day(setter["db"], prog, A._today_central().date())[0] == []
    assert BP.RESTLAND_ORG_ID in {"01fed629-9a95-412b-895a-c8b09f37a98c"}


def test_no_same_day_booking_or_move_by_anyone(setter, sample_advisor, monkeypatch):
    from app.services.programs import appointments as A
    from app.services.programs import booking_policy as BP
    db, org, prog, sheet, appts, mine, other = _team(setter, sample_advisor)
    monkeypatch.setattr(BP, "NO_SAME_DAY_ORGS", {org.id})
    from app.models.models import Lead
    lead = db.query(Lead).filter(Lead.id == mine.lead_id).one()
    today = A._today_central().replace(hour=16, minute=0, second=0, microsecond=0, tzinfo=None)
    assert A.book(db, prog, lead, today, A.VISIT)["status"] == "same_day"


# ── wrong number: texts stop, emails continue (owner, Nov 27 - Emily Flores) ──

def test_a_wrong_number_text_stops_texts_and_keeps_emailing(setter, sample_advisor):
    from app.models.program_models import ProgramSourceRecord
    from app.services.programs import wrong_contact as WC
    from tests.test_sci_appointment_setter import _Fake, _d, _run
    db, org = setter["db"], setter["org"]
    prog = _prog(setter)
    lead = _lead_for(db, org, sample_advisor, "L005", phone="2145550105", first="Emily")
    lead.email = "emily@example.com"
    db.commit()
    resp, conv = _run(db, lead, "Wrong number", _Fake([_d("answer", "ok")]))
    db.refresh(lead)
    assert lead.contact_channel == "email_only" and lead.phone_line_type == "invalid"
    assert lead.phone.endswith("2145550105")                                   # nothing deleted
    assert resp.handling_status == "closed"
    rec = db.query(ProgramSourceRecord).filter_by(lead_id=lead.id).first()
    if rec is not None:
        flags = json.loads(rec.data_note_flags or "[]")
        assert WC.FLAG in flags and "WRONG NUMBER / ADDRESS (reply)" not in flags
    assert conv.state == "waiting"                                             # no hand-off, nothing sent
    assert WC.is_wrong_number_only(db, lead)                                   # emails don't treat it as a reply
    from app.services.programs import send_approval
    assert not send_approval.textable(lead)
    # undo: texting allowed again
    WC.mark_wrong_number(db, prog, lead, by="Owner", undo=True)
    assert lead.contact_channel == "sms" and lead.phone_line_type is None


def test_wrong_number_without_an_email_still_goes_to_a_person(setter, sample_advisor):
    from tests.test_sci_appointment_setter import _Fake, _d, _run
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L006", phone="2145550106", first="Ned")
    lead.email = None
    db.commit()
    resp, conv = _run(db, lead, "Wrong number", _Fake([_d("answer", "ok")]))
    db.refresh(lead)
    assert lead.contact_channel != "email_only"
    assert conv.state == "human"


def test_wrong_email_text_is_not_a_wrong_phone():
    from app.services.programs import wrong_contact as WC
    assert WC.says_wrong_phone("Wrong number")
    assert WC.says_wrong_phone("You have the wrong person, this is not her")
    assert not WC.says_wrong_phone("you have the wrong email for me")


def test_a_reply_answers_everything_the_family_sent_before_it(setter, sample_advisor):
    """Nov 27, Margaret Degeeter: voicemail 2:04, text 2:06, Mike answered from
    the voicemail at 2:08 - the 2:06 text stayed open and was paged at 2:21."""
    from app.models.program_models import ProgramResponse
    from app.services.programs import responses as R
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L004", phone="2145550104", first="Margaret")
    t0 = datetime(2026, 11, 27, 20, 4)
    vm = ProgramResponse(organization_id=org.id, lead_id=lead.id, channel="voicemail", response_class="hot",
                         handling_status="new", received_at=t0, recommended_action="x", summary="vm")
    tx = ProgramResponse(organization_id=org.id, lead_id=lead.id, channel="sms", response_class="hot",
                         handling_status="new", received_at=t0 + timedelta(minutes=2), recommended_action="x", summary="tx")
    later = ProgramResponse(organization_id=org.id, lead_id=lead.id, channel="sms", response_class="hot",
                            handling_status="new", received_at=t0 + timedelta(minutes=30), recommended_action="x",
                            summary="later")
    db.add_all([vm, tx, later])
    db.commit()
    R.mark(db, vm, "responded", sample_advisor, now=t0 + timedelta(minutes=4))
    db.refresh(tx)
    db.refresh(later)
    assert vm.handling_status == "responded" and tx.handling_status == "responded"
    assert later.handling_status == "new"                                      # sent after the reply: still open
    # a text not tied to one message answers what came before it too
    assert R.answered_by_person(db, lead, sample_advisor, now=t0 + timedelta(minutes=31)) == 1
    db.refresh(later)
    assert later.handling_status == "responded"


def test_restland_never_sends_a_guide_link_it_offers_times(setter, sample_advisor, monkeypatch):
    """Owner, Nov 27 (Hsienchang Chiu): the AI drafted 'I'll send you the planning
    guide ... https://links.evosyspro.live/planning-guide'. Restland gives the
    guide at the visit - the AI offers times instead, never a guide link."""
    from app.services.programs import ai_setter
    from tests.test_sci_appointment_setter import _Fake, _d, _run
    db, org = setter["db"], setter["org"]
    monkeypatch.setattr(ai_setter, "GUIDE_AT_VISIT_ORGS", {org.id})
    lead = _lead_for(db, org, sample_advisor, "L003", phone="2145550103", first="Hsien")
    resp, conv = _run(db, lead, "Just send me the guide please",
                      _Fake([_d("send_guide", "I'll send you the planning guide so you can review it.")]))
    turns = [t for t in ai_setter.ProgramConversationTurn.__table__.c] and \
        db.query(ai_setter.ProgramConversationTurn).filter_by(conversation_id=conv.id, role="ai").all()
    assert turns, "the family got an answer"
    last = turns[-1]
    assert last.action == "offer_times"
    assert "http" not in (last.text or "").split("Or pick another time here")[0]
    assert "send you" not in (last.text or "").lower() and "guide" in (last.text or "").lower()
