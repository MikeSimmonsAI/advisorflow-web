"""SCI POC scenario C (Oct 21): a family who already bought a plan FROM US is
an existing customer - no new-sales appointment is pushed, nothing is booked,
and no generic calendar filler is sent. Covers the Oct 20 fixes 09a31ade,
18604683 and 914ff39d, which shipped without tests."""
import pytest

from app.models.models import BookingLink
from app.services.programs import ai_setter as S
from tests.test_outreach_program import _lead_for, program  # noqa: F401 (fixture)
from tests.test_sci_appointment_setter import _Fake, _ai_turns, _d, _run, setter  # noqa: F401 (fixture)

PURCHASED = "We purchased a plan from you last December"
ACK = ("Thank you for letting me know about the plan you purchased with us. Is there anything you need "
       "help with on your existing arrangements?")


@pytest.mark.parametrize("text,expected", [
    (PURCHASED, True),
    ("We already bought our plots from you years ago", True),
    ("My husband and I paid for our arrangements through this funeral home in 2019", True),
    ("We never bought a plan from you", False),
    ("We bought a plan from another funeral home", False),
    ("Can you send me information about plans?", False),
])
def test_existing_customer_claim_is_recognised(text, expected):
    assert S.existing_plan_purchased_with_us(text) is expected


def test_existing_customer_is_not_pushed_into_an_appointment(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    # the model first tries to sell a visit; the validator refuses it and the retry answers
    fake = _Fake([_d("offer_times", "Wonderful! Would {TIMES} work to meet?"), _d("answer", ACK)])
    _, conv = _run(db, lead, PURCHASED, fake)
    assert len(fake.calls) == 2                                       # the push was caught and corrected
    last = _ai_turns(db, conv)[-1]
    assert last.action == "answer" and "{TIMES}" not in (last.text or "")
    assert S.A.offered(conv) == [] and conv.state != "booked"
    assert db.query(BookingLink).filter_by(lead_id=lead.id).count() == 0


def test_existing_customer_is_never_closed_as_uninterested(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    fake = _Fake([_d("close", "Thank you, have a great day."), _d("answer", ACK)])
    _, conv = _run(db, lead, PURCHASED, fake)
    assert len(fake.calls) == 2 and _ai_turns(db, conv)[-1].action == "answer"
    assert conv.state != "closed"


def test_existing_customer_asking_for_a_review_may_get_times(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    fake = _Fake([_d("offer_times", "I'd be glad to go over your plan with you - would {TIMES} work?")])
    _, conv = _run(db, lead, "We bought a plan from you last year - can we come in to review it?", fake)
    assert len(fake.calls) == 1 and _ai_turns(db, conv)[-1].action == "offer_times"
    assert S.A.offered(conv)


def test_generic_scheduling_filler_is_not_an_answer_to_an_existing_customer(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    conv = S.conversation_for(db, _lead_for(db, org, sample_advisor, "L001"))
    problems = S.validate_decision({"action": "answer", "reply": "Would you like to set up a time to talk?"},
                                   PURCHASED, conv)
    assert any("EXISTING CUSTOMER" in p for p in problems)
