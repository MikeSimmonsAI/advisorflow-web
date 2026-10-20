"""#234 - a bare "No" answers the LAST QUESTION WE ASKED.

Production, Oct 19: "could you stop by for a short visit this week?" -> "No"
-> the setter said goodbye and closed. A no to COMING IN is not a no to every
kind of help: offer ONE alternative the location really offers, then respect
the answer. STOP / not interested always wins; nothing is invented.
The AI is a scripted fake (as in test_sci_appointment_setter); these tests
prove what the code makes of each decision.
"""
import json
from datetime import datetime, timedelta

import pytest

from app.models.models import Message
from app.services.programs import ai_setter as S
from tests.test_outreach_program import _lead_for, program  # noqa: F401 (fixture)
from tests.test_sci_appointment_setter import _Fake, _d, _run, _ai_turns, setter  # noqa: F401 (fixture)

INVITE = "Ollie, quick question from Eastern Gate: could you stop by for a short visit this week?"
HELP = "Is there anything else we can help you with?"
GOODBYE = "Thank you for letting me know. If you ever need anything, feel free to reach out. Take care."
GUIDE_OFFER = "No problem at all. Would it help if I sent you the planning guide so you have it handy?"
HOME_OFFER = "No problem - I can come to you instead, if that's easier. Would that be more convenient?"


# ── what was declined: decided in code ───────────────────────────────────────

@pytest.mark.parametrize("body,last,times,expect", [
    ("No", INVITE, False, S.DECLINED_VENUE),
    ("no.", INVITE, False, S.DECLINED_VENUE),
    ("Nope", INVITE, False, S.DECLINED_VENUE),
    ("No", HELP, False, S.UNCLEAR_NO),
    ("No", INVITE, True, S.DECLINED_TIME),
    ("Not this week", INVITE, True, S.DECLINED_TIME),
    ("Neither of those works", INVITE, True, S.DECLINED_TIME),
    ("I can't get there", INVITE, False, S.CANT_GET_THERE),
    ("I don't drive anymore", INVITE, False, S.CANT_GET_THERE),
    ("I don't want anyone coming to my home", INVITE, False, S.DECLINED_HOME),
    ("No thanks", INVITE, False, S.DECLINED_STOP),
    ("Not interested", INVITE, False, S.DECLINED_STOP),
    ("STOP", INVITE, False, S.DECLINED_STOP),
    ("please don't text me", INVITE, False, S.DECLINED_STOP),
    ("Yes Thursday works", INVITE, False, None),
    ("I'd rather you come to me", INVITE, False, None),
])
def test_what_a_no_declined(body, last, times, expect):
    assert S.what_was_declined(body, last, times) == expect


# ── the conversation ─────────────────────────────────────────────────────────

def _invited(db, lead, adv, text=INVITE):
    db.add(Message(lead_id=lead.id, sender_id=adv.id, body=text, delivery_status="delivered",
                   sent_at=datetime.utcnow() - timedelta(minutes=20)))
    db.commit()


def _location(db, lead, **settings):
    from app.services.programs import identity
    prof = identity.location_profile_for_lead(db, lead)
    bs = prof.brand_settings if isinstance(prof.brand_settings, dict) else json.loads(prof.brand_settings or "{}")
    bs.update(settings)
    prof.brand_settings = json.dumps(bs) if isinstance(prof.brand_settings, str) or prof.brand_settings is None else bs
    db.commit()


def _last_ai(db, conv):
    t = _ai_turns(db, conv)
    return t[-1] if t else None


def test_bare_no_to_the_cemetery_invite_offers_the_guide_not_a_goodbye(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    _invited(db, lead, sample_advisor)
    fake = _Fake([_d("close", GOODBYE), _d("answer", GUIDE_OFFER)])
    _, conv = _run(db, lead, "No", fake)
    assert len(fake.calls) == 2                                              # the goodbye was corrected
    assert "COME IN" in json.dumps(fake.calls[1]["messages"])               # told exactly why
    t = _last_ai(db, conv)
    assert t.text == GUIDE_OFFER and t.action == "answer" and conv.state != "closed"
    mem = S.memory_of(conv)
    assert mem["objection"] == S.DECLINED_VENUE and mem["alternative_offered"] == S.ALT_GUIDE


def test_location_with_home_visits_offers_to_come_to_them(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    _location(db, lead, home_visits=True)
    _invited(db, lead, sample_advisor)
    fake = _Fake([_d("answer", HOME_OFFER)])
    _, conv = _run(db, lead, "No", fake)
    ctx = json.loads(fake.calls[0]["messages"][-1]["content"])
    assert ctx["facts_known_for_certain"]["decline"]["alternative_to_offer"] == S.ALT_HOME
    assert _last_ai(db, conv).text == HOME_OFFER
    assert S.memory_of(conv)["alternative_offered"] == S.ALT_HOME


def test_no_home_visit_is_promised_where_the_location_does_not_offer_them(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    _location(db, lead, personal_delivery=True)          # the guide-delivery flag is NOT a home-visit permission
    _invited(db, lead, sample_advisor)
    fake = _Fake([_d("answer", HOME_OFFER), _d("answer", HOME_OFFER)])
    _, conv = _run(db, lead, "No", fake)
    t = _last_ai(db, conv)
    assert t.delivery == "blocked" and conv.state == "human"                # never sent; a person decides


def test_phone_alternative_when_the_location_offers_it(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    _location(db, lead, offer_phone_alternative=True)
    _invited(db, lead, sample_advisor)
    fake = _Fake([_d("offer_times", "No problem - would a short phone call work instead, {TIMES}?",
                     appointment_kind="phone")])
    _, conv = _run(db, lead, "I can't get there", fake)
    assert conv.appointment_kind == "phone" and S.memory_of(conv)["alternative_offered"] == S.ALT_PHONE


def test_second_no_after_the_alternative_closes_warmly(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    _invited(db, lead, sample_advisor)
    _run(db, lead, "No", _Fake([_d("answer", GUIDE_OFFER)]))
    db.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body=GUIDE_OFFER, delivery_status="delivered",
                   sent_at=datetime.utcnow()))
    db.commit()
    fake = _Fake([_d("close", GOODBYE)])
    _, conv = _run(db, lead, "No", fake)
    assert len(fake.calls) == 1 and conv.state == "closed"                  # one alternative, then respect it
    assert S.memory_of(conv)["alternative_outcome"] == "declined"


@pytest.mark.parametrize("body", ["No thanks", "Not interested"])
def test_a_clear_no_is_honoured_with_no_pitch(setter, sample_advisor, body):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    _invited(db, lead, sample_advisor)
    fake = _Fake([])
    _, conv = _run(db, lead, body, fake)
    assert fake.calls == [] and conv.state == "stopped"                     # opt-out rules: no AI, nothing sent
    assert not [t for t in _ai_turns(db, conv) if t.text]


def test_a_clear_no_that_reaches_the_ai_never_gets_an_alternative_pitch(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    _invited(db, lead, sample_advisor)
    fake = _Fake([_d("offer_times", "Understood - would a phone call work instead, {TIMES}?"),
                  _d("close", GOODBYE)])
    _, conv = _run(db, lead, "That's not for me", fake)
    assert len(fake.calls) == 2 and conv.state == "closed" and _last_ai(db, conv).text == GOODBYE


def test_dont_come_to_my_home_never_gets_a_home_offer(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    _location(db, lead, home_visits=True)
    _invited(db, lead, sample_advisor)
    fake = _Fake([_d("answer", HOME_OFFER), _d("answer", GUIDE_OFFER)])
    _, conv = _run(db, lead, "I don't want anyone coming to my home", fake)
    assert len(fake.calls) == 2 and _last_ai(db, conv).text == GUIDE_OFFER


def test_no_to_a_general_question_is_not_treated_as_a_venue_no(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    _invited(db, lead, sample_advisor, text=HELP)
    fake = _Fake([_d("close", GOODBYE)])
    _, conv = _run(db, lead, "No", fake)
    assert len(fake.calls) == 1 and conv.state == "closed"


def test_a_home_visit_yes_is_never_booked_as_a_cemetery_visit(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    _location(db, lead, home_visits=True)
    _invited(db, lead, sample_advisor)
    _run(db, lead, "No", _Fake([_d("answer", HOME_OFFER)]))
    db.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body=HOME_OFFER, delivery_status="delivered",
                   sent_at=datetime.utcnow()))
    db.commit()
    fake = _Fake([_d("book", "", slot_choice=1),
                  _d("handoff", "Wonderful - I'll reach out to you personally to find a good time.")])
    _, conv = _run(db, lead, "Yes that would be easier", fake)
    assert len(fake.calls) == 2 and "arranged personally" in json.dumps(fake.calls[1]["messages"])
    assert conv.state == "human" and conv.booked_time is None
    assert S.memory_of(conv)["alternative_outcome"] == "accepted"


def test_a_manager_turns_home_visits_on_per_location(client, setter, sample_advisor):
    from app.models.program_models import LocationProfile
    from tests.test_outreach_program import _h
    db, org, admin = setter["db"], setter["org"], setter["admin"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    from app.services.programs import identity
    prof = identity.location_profile_for_lead(db, lead)
    prof.brand_settings = json.dumps({"planning_guide_link": "https://example.com/g"})
    db.commit()
    r = client.patch("/program/locations/%s" % prof.id, headers=_h(db, admin), json={"home_visits": True})
    assert r.status_code == 200
    db.refresh(prof)
    bs = json.loads(prof.brand_settings)
    assert bs["home_visits"] is True and bs["planning_guide_link"] == "https://example.com/g"   # nothing else lost
    conv = S.conversation_for(db, lead, create=True)
    assert S.alternative_for(db, lead, conv, S.DECLINED_VENUE) == S.ALT_HOME
    assert S.alternative_for(db, lead, conv, S.DECLINED_HOME) != S.ALT_HOME


# ── "stop by" is a family coming in, not a STOP (overnight Oct 19) ───────────

@pytest.mark.parametrize("body", ["I can stop by today.", "I'll stop by tomorrow", "Can I stop in Friday?",
                                  "We can stop over after church", "I will stop at the office later"])
def test_stop_by_is_never_read_as_a_stop(body):
    assert S.what_was_declined(body, INVITE, False) != S.DECLINED_STOP


@pytest.mark.parametrize("body", ["Stop", "STOP.", "stop texting me", "Please stop messaging me", "stop it"])
def test_a_real_stop_is_still_a_stop(body):
    assert S.what_was_declined(body, INVITE, False) == S.DECLINED_STOP


def test_family_stopping_by_gets_times_not_a_goodbye(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    _invited(db, lead, sample_advisor)
    fake = _Fake([_d("offer_times", "Wonderful - would {TIMES} work for you?")])
    _, conv = _run(db, lead, "I can stop by this week", fake)
    assert len(fake.calls) == 1                                              # no "they said stop" correction
    assert _last_ai(db, conv).action == "offer_times" and conv.state != "closed"


@pytest.mark.parametrize("body", ["Please stop", "Stop please", "pls stop", "STOP NOW", "Stop. Thank you",
                                  "stop this", "stop these texts", "Stop the messages"])
def test_polite_stops_are_still_stops(body):
    # Independent review: these are not opt-outs to the reply rules, so the
    # setter itself must still read them as a stop.
    assert S.what_was_declined(body, INVITE, False) == S.DECLINED_STOP
