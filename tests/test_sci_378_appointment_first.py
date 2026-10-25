"""#378 - THE MISSION IS TO SET THE APPOINTMENT.

Kevin Quarles (Striffler-Hamby, Oct 23) got a touch-1 text about his veteran
benefits and, five minutes later, a touch-1 email offering to "send it over".
Neither asked for an appointment. Every test here uses synthetic records."""
import json
from datetime import datetime, timedelta

import pytest

from app.models.models import CadenceTemplate, CadenceTemplateTouch, Message
from app.models.program_models import CampaignFamily
from app.services.programs import ai_setter as S, appointment_copy as C, appointments as A, identity
from app.services.programs import message_brain as brain
from tests.test_outreach_program import _fake_provider, _lead_for, _touch_ready, program  # noqa: F401
from tests.test_sci_appointment_setter import SLOTS, _Fake, _ai_turns, _d, _run, setter  # noqa: F401
from tests.test_sci_platinum import _packet

ASK = r"(I can meet (you|with you)|short visit|what day works|would .* work)"


def _settings(db, lead, **bs):
    prof = identity.location_profile_for_lead(db, lead)
    cur = json.loads(prof.brand_settings or "{}") if isinstance(prof.brand_settings, str) or prof.brand_settings is None \
        else dict(prof.brand_settings)
    cur.update(bs)
    prof.brand_settings = json.dumps(cur)
    db.commit()
    return prof


def _old_copy(db, org):
    for fam in db.query(CampaignFamily).filter_by(organization_id=org.id).all():
        old = C.OLD_FAMILY_COPY.get(fam.key)
        if old:
            fam.sms_template, fam.email_body_template = old["sms"], old["email"]
            fam.email_subject_template = "Following up, {first_name}"
    tpl = CadenceTemplate(organization_id=org.id, name="SCI 10-Day Aggressive", industry="program",
                          is_default=True, is_active=True)
    db.add(tpl)
    db.flush()
    for n, text in [(1, "(Day 0 - not sent)")] + sorted(C.OLD_PROGRAM_TOUCHES.items()):
        db.add(CadenceTemplateTouch(template_id=tpl.id, touch_number=n, day_offset=n, send_hour=10, channel="sms",
                                    message_template=text, is_active=True))
    for t in db.query(CadenceTemplate).filter(CadenceTemplate.organization_id == org.id,
                                              CadenceTemplate.id != tpl.id).all():
        t.is_default = False
    db.commit()
    return tpl


# ── the ways a location meets families ───────────────────────────────────────

@pytest.mark.parametrize("bs,want", [({}, "here at X"), ({"home_visits": True}, "here at X or at your home"),
                                     ({"home_visits": True, "offer_phone_alternative": True},
                                      "here at X, at your home, or by phone"),
                                     ({"offer_phone_alternative": True}, "here at X or by phone")])
def test_only_the_ways_this_location_offers(bs, want):
    from types import SimpleNamespace as N
    assert identity.appointment_options(N(official_name="X", brand_settings=json.dumps(bs))) == want


# ── the copy itself ─────────────────────────────────────────────────────────

def test_no_family_or_cadence_text_is_information_only():
    import re
    for key, c in C.FAMILY_COPY.items():
        for ch in ("sms", "email"):
            assert re.search(ASK, c[ch], re.I), (key, ch)
            assert "{appointment_options}" in c[ch] and "{booking_line}" in c[ch], (key, ch)
            assert not re.search(r"(would you like me to send it|when's a good time to make sure|is there anything "
                                 r"i can help|keep (this|your request) open)", c[ch], re.I), (key, ch)
    for n, t in C.PROGRAM_TOUCHES.items():
        assert re.search(ASK, t, re.I) and "{appointment_options}" in t and "{booking_line}" in t, n
        assert "last note" not in t


def test_upgrade_replaces_only_the_exact_old_copy_and_is_idempotent(program):
    db, org = program["db"], program["org"]
    _old_copy(db, org)
    seminar = db.query(CampaignFamily).filter_by(organization_id=org.id, key="seminar").one()
    seminar.sms_template = "A person rewrote this. {call_line}"            # edited: never touched
    db.commit()
    dry = C.upgrade(db, org.id, dry_run=True)
    assert "veteran_planning_guide.sms" in dry["changed"] and "cadence.touch2" in dry["changed"]
    assert "seminar.sms" in dry["kept_edited"]
    vet = db.query(CampaignFamily).filter_by(organization_id=org.id, key="veteran_planning_guide").one()
    assert vet.sms_template == C.OLD_FAMILY_COPY["veteran_planning_guide"]["sms"]   # dry run wrote nothing
    live = C.upgrade(db, org.id, dry_run=False)
    db.refresh(vet)
    db.refresh(seminar)
    assert vet.sms_template == C.FAMILY_COPY["veteran_planning_guide"]["sms"]
    assert vet.email_body_template == C.FAMILY_COPY["veteran_planning_guide"]["email"]
    assert seminar.sms_template == "A person rewrote this. {call_line}"
    assert set(live["changed"]) == set(dry["changed"])
    assert C.upgrade(db, org.id, dry_run=False)["changed"] == []            # second run: nothing


# ── Kevin's text: touch 1 and the follow-ups ask for the appointment ──────────

def test_kevins_first_text_now_asks_for_a_visit_the_ways_his_location_offers(program, sample_advisor, monkeypatch):
    db, org = program["db"], program["org"]
    _old_copy(db, org)
    C.upgrade(db, org.id, dry_run=False)
    lead = _lead_for(db, org, sample_advisor, "L001", first="Kevin")
    monkeypatch.setattr(identity, "program_booking_url", lambda db_, l: "https://easterngate.evosyspro.live/k1")
    _settings(db, lead, home_visits=True)
    t1 = identity.cadence_text(db, lead, 1)
    assert "Veteran Benefits Guide" in t1                                        # the conversation starter
    assert "I can meet you here at Eastern Gate Memorial Gardens or at your home - what day works for you?" in t1
    assert "phone" not in t1.lower().replace("call us at", "")                   # phone appointments are off
    assert "https://easterngate.evosyspro.live/k1" in t1 and "{" not in t1
    assert "When's a good time to make sure" not in t1
    for n in (2, 3, 4, 5, 6):
        t = identity.cadence_text(db, lead, n)
        assert "or at your home" in t and "https://easterngate.evosyspro.live/k1" in t, (n, t)


def test_phone_is_offered_only_where_the_location_turned_it_on(program, sample_advisor, monkeypatch):
    db, org = program["db"], program["org"]
    _old_copy(db, org)
    C.upgrade(db, org.id, dry_run=False)
    lead = _lead_for(db, org, sample_advisor, "L001")
    monkeypatch.setattr(identity, "program_booking_url", lambda db_, l: "")
    _settings(db, lead, home_visits=True, offer_phone_alternative=True)
    assert "at your home, or by phone" in identity.cadence_text(db, lead, 1)


# ── Kevin's email: appointment ask, booking link, never on top of a text ─────

def test_kevins_first_email_asks_for_a_visit_with_his_booking_link(program, sample_advisor, monkeypatch):
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch, first="Kevin")
    _old_copy(db, org)
    C.upgrade(db, org.id, dry_run=False)
    db.query(CampaignFamily).filter_by(organization_id=org.id, key="veteran_planning_guide").one().is_active = True
    db.commit()
    _settings(db, lead, home_visits=True)
    monkeypatch.setattr(identity, "program_booking_url", lambda db_, l: "https://easterngate.evosyspro.live/k1")
    sent = []
    with _fake_provider(sent):
        r = et.run(db, org.id, force_hours=True)
    assert r["sent"] == 1, r
    html = sent[0]["body_html"]
    assert "I can meet with you here at Eastern Gate Memorial Gardens or at your home" in html
    assert '<a href="https://easterngate.evosyspro.live/k1">' in html
    assert "send it over" not in html and "reply" in html.lower()


def test_the_email_waits_after_a_text_so_they_never_land_together(program, sample_advisor, monkeypatch):
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    db.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="touch 1 text", send_source="cadence",
                   delivery_status="delivered", sent_at=datetime.utcnow() - timedelta(minutes=5)))
    db.commit()
    sent = []
    with _fake_provider(sent):
        r = et.run(db, org.id, force_hours=True)
    assert sent == [] and r["skipped"] == 1                                      # Kevin's 5-minute pair
    db.query(Message).filter_by(lead_id=lead.id).one().sent_at = datetime.utcnow() - timedelta(hours=5)
    db.commit()
    with _fake_provider(sent):
        assert et.run(db, org.id, force_hours=True)["sent"] == 1                # later: it goes


def test_a_text_that_never_arrived_does_not_hold_the_email(program, sample_advisor, monkeypatch):
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    db.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="blocked", send_source="cadence",
                   delivery_status="undelivered", sent_at=datetime.utcnow() - timedelta(minutes=5)))
    db.commit()
    sent = []
    with _fake_provider(sent):
        assert et.run(db, org.id, force_hours=True)["sent"] == 1


def test_brain_written_emails_and_texts_name_the_locations_ways():
    p = _packet(appointment_options="here at Eastern Gate Memorial Gardens or at your home")
    for t in (1, 4):
        m = brain.compose(p, t, "email", booking_url="https://x.example/b")
        assert "or at your home" in m["body"] and brain.quality(p, m)["ok"], t
    for t in (1, 3):
        m = brain.compose(p, t, "sms")
        assert "or at your home" in m["body"] and brain.quality(p, m)["ok"], t


# ── Kevin's reply: "I did get it, thank you" ────────────────────────────────

@pytest.mark.parametrize("body,yes", [("Hi Kerry,\nI did get it, thank you.\n\nKind regards, Kevin", True),
                                      ("Yes I received the guide", True), ("We got the information, thanks", True),
                                      ("We already have a plan", False), ("I did not get it", False),
                                      ("Stop texting me", False),
                                      # independent review (#378): never a pitch for these
                                      ("We have it, we bought our plan from you in 2015. Thank you", False),
                                      ("I got it, thank you. My husband passed away last week.", False),
                                      ("I have the paperwork, we are using another funeral home", False),
                                      ("I got it. We're good, thank you", False), ("I got it, that's all I needed", False),
                                      ("I got it but we can't afford anything right now", False),
                                      ("I didn't ask for this", False), ("We have them, our plots are at Restland", False),
                                      ("I got it, I'll reach out if I need anything", False),
                                      ("I got it - what does it cost?", False)])
def test_got_the_information_is_recognised(body, yes):
    assert S.got_the_information(body) is yes


def test_kevins_reply_gets_real_times_to_go_over_it_not_a_goodbye(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Kevin")
    fake = _Fake([_d("answer", "I'm glad to hear you received the information. If you have any questions, "
                               "feel free to reach out.")])
    resp, conv = _run(db, lead, "Hi Kerry,\nI did get it, thank you.\n\nKind regards, Kevin", fake)
    text = resp.suggested_reply or _ai_turns(db, conv)[-1].text
    assert conv.state == "offered" and A.describe(SLOTS[0]) in text and A.describe(SLOTS[1]) in text
    assert "feel free to reach out" not in text and "go over it with you" in text


def test_the_ai_is_told_only_the_ways_this_location_offers(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    _settings(db, lead, home_visits=True)
    fake = _Fake([_d("offer_times", "Would {TIMES} work for a short visit?")])
    _run(db, lead, "Tell me more", fake)
    ctx = json.loads(fake.calls[0]["messages"][1]["content"])
    assert ctx["facts_known_for_certain"]["appointment_ways_this_location_offers"] == [
        "a visit at the location", "a visit at the family's home"]
    assert "Never offer a way that is not on that list" in fake.calls[0]["messages"][0]["content"]


# ── busy is not a no: work toward a later appointment ───────────────────────

def test_busy_this_week_gets_times_next_week(setter, sample_advisor, monkeypatch):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    monkeypatch.setattr(A, "_today_central", lambda: datetime(2030, 1, 9, 14, 0))            # a Wednesday
    seen = []

    def on_day(db_, prog, d, want=2, **k):
        seen.append(d)
        return ([datetime(d.year, d.month, d.day, 10, 0), datetime(d.year, d.month, d.day, 14, 0)], None) \
            if d.weekday() == 1 else ([], "none")                               # next Tuesday is open
    monkeypatch.setattr(A, "slots_on_day", on_day)
    resp, conv = _run(db, lead, "I'm slammed this week, next week is better",
                      _Fake([_d("offer_times", "No problem - would {TIMES} work?", memory_update={"best_times": "next week"})]))
    assert conv.state == "offered" and all(s.date().isoformat() == "2030-01-15" for s in A.offered(conv))


@pytest.mark.parametrize("body,yes", [("I'm slammed this week, next week is better", True),
                                      ("Next week works", True), ("I'm out of town next week, maybe the week after", False),
                                      ("not next week", False), ("the week after next week", False),
                                      ("Can't do next week", False)])
def test_next_week_only_when_they_mean_it(body, yes):
    assert S.wants_next_week(body) is yes


def test_an_existing_customer_who_got_it_is_never_pitched(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    fake = _Fake([_d("answer", "Thank you - glad you're all set.")])
    _, conv = _run(db, lead, "We have it, we bought our plan from you in 2015. Thank you", fake)
    assert conv.state != "offered" and not A.offered(conv)


def test_phone_is_never_offered_where_the_location_does_not(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    _settings(db, lead, home_visits=True)
    fake = _Fake([_d("offer_times", "Would {TIMES} work? We can also do it by phone."),
                  _d("offer_times", "Would {TIMES} work? We can also do it by phone.")])
    _, conv = _run(db, lead, "Tell me more", fake)
    assert "phone appointment" in fake.calls[-1]["messages"][1]["content"]          # corrected once
    assert _ai_turns(db, conv)[-1].delivery == "blocked"                            # then held for a person


def test_a_hidden_location_is_not_named():
    from types import SimpleNamespace as N
    p = N(official_name="Family Services", hide_location=True, brand_settings=json.dumps({"home_visits": True}))
    assert identity.appointment_options(p) == "here at our office or at your home"
