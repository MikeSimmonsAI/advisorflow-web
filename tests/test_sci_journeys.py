"""SCI FAMILY JOURNEYS - end-to-end synthetic acceptance tests (Oct 20).

Each test drives the CONNECTED system for one family journey and checks the
final state at every boundary:

    inbound SMS (sms_router.process_inbound_sms, real dedupe/STOP handling)
      -> program classification (responses.on_inbound)
      -> AI setter decision (ai_setter.on_family_reply; the model is a scripted fake)
      -> booking (appointments.book -> the booking page's own confirm path,
         REAL slot engine) or hand-off
      -> staff alert ledger (alert_ledger.page; staff texts/emails recorded)
      -> SCI Live (/program/command, tasks, /family page, advisor actions)

Everything is synthetic. Nothing reaches Twilio, Resend, an AI provider or a
calendar provider:
  * staff texts/emails: recorded by the `wired` fixture (test_alert_dedupe)
  * family texts: recorded at the provider boundary by `_FamilyLine`, which
    still runs the program's own send refusals (location review, DNC) and the
    real composer (compose_body - location sign-off) before recording
  * AI: _Fake scripted decisions (test_sci_appointment_setter)
  * platform reply classifier: patched (no AI); hard STOP keywords stay real
  * calendar: the real engine (compute_day_slots) on the advisor's own
    booked times - no provider connected

A journey that exposes a real defect keeps asserting the CORRECT behaviour
and is marked xfail(strict=True, reason="DEFECT: ...").
"""
import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.models.models import AuditLogEntry, BookingLink, CadenceState, Lead, Message
from app.models.program_models import ProgramAlert, ProgramConversation, ProgramConversationTurn, ProgramResponse
from app.services import send_source as SS
from app.services import sms_service
from app.services.programs import ai_setter as S
from app.services.programs import appointments as A
from app.services.programs import responses as R
from tests.test_alert_dedupe import OWNER_EMAIL, OWNER_PHONE, wired  # noqa: F401 (fixture)
from tests.test_outreach_program import _h, _lead_for, hash_password, program  # noqa: F401 (fixture)
from tests.test_sci_appointment_setter import _Fake, _d
from tests.test_sci_decline_alternatives import YES_HOME, _location

REAL_SEND_SMS = sms_service.send_sms

INVITE = "Ollie, quick question from Eastern Gate: could you stop by for a short visit this week?"
HOME_OFFER = "No problem - I can come to you instead, if that's easier. Would that be more convenient?"
GOODBYE = "Thank you for letting me know. If you ever need anything, feel free to reach out. Take care."
EG, SH = "Eastern Gate Memorial Gardens", "Alabama Heritage Funeral Home"
OLLIE = dict(ref="L001", phone="2145550101", first="Ollie", last="Testcase", email="ollie@example.com")
# L003/L004 share a household email and sit in duplicate review by design; L007 is a clean second family.
PAT = dict(ref="L007", phone="2145550107", first="Rosa", last="Diaz", email="rd@example.com")
LEE = dict(ref="L006", phone="2145550106", first="Lee", last="Nowhere", email="lm@example.com")


# ── the family's phone line: recorded at the provider boundary ──────────────

class _FamilyLine:
    """Stands in for sms_service.send_sms. Runs the program's own refusals
    first (location review, DNC) so nothing goes out under an invented
    identity, builds the exact text with the real composer, then records it
    as a sent Message instead of calling Twilio."""

    def __init__(self):
        self.sent = []

    def __call__(self, db, advisor, lead, template, include_booking_link=True, send_source=None,
                 sent_by_user_id=None):
        from app.services.programs import identity
        why = identity.send_refusal(db, lead, send_source)
        if why:
            raise ValueError("Lead %s: %s" % (lead.id, why))
        if (lead.status or "") == "dnc":
            raise ValueError("Lead %s is marked DNC - blocked from sending." % lead.id)
        body = sms_service.compose_body(template, lead, advisor, "")
        m = Message(lead_id=lead.id, sender_id=getattr(advisor, "id", None), body=body, send_source=send_source,
                    sent_at=datetime.utcnow(), delivery_status="delivered")
        db.add(m)
        db.flush()
        self.sent.append({"lead_id": lead.id, "to": lead.phone, "body": body, "source": send_source,
                          "template": template})
        return m

    def to(self, lead, source=None):
        return [s for s in self.sent if s["lead_id"] == lead.id and (source is None or s["source"] == source)]


@pytest.fixture
def journey(wired, sample_advisor, monkeypatch):
    """`wired` (real alert ledger, staff sends recorded) + the setter in AUTO
    on the program line + the REAL slot engine + the family line recorder."""
    db, prog = wired["db"], wired["prog"]
    prog.booking_user_id = sample_advisor.id
    prog.ai_setter_mode = "auto"
    from app.models.program_models import CampaignFamily
    for fam in db.query(CampaignFamily).filter_by(organization_id=wired["org"].id):
        fam.is_active = True                       # a live program: its campaigns are switched on
    db.commit()
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", "true")
    monkeypatch.delenv("SCI_SMS_LINKS", raising=False)
    monkeypatch.delenv("AI_SCI_SETTER_ENABLED", raising=False)
    from app.services import ai_gateway
    ai_gateway._reset_for_tests()
    hours = {"open": True}
    monkeypatch.setattr("app.services.contact_hours.check", lambda lead, now=None: {"permitted": hours["open"]})
    monkeypatch.setattr("app.services.contact_hours.next_permitted_utc",
                        lambda lead, now=None: datetime.utcnow() + timedelta(hours=1))
    line = _FamilyLine()
    monkeypatch.setattr(sms_service, "send_sms", line)
    return dict(wired, line=line, hours=hours, advisor=sample_advisor)


def _family(j, who=OLLIE):
    return _lead_for(j["db"], j["org"], j["advisor"], who["ref"], phone=who["phone"], first=who["first"],
                     last=who["last"], email=who["email"])


def _inbound(j, lead, body, sid, fake):
    """One inbound text through the REAL inbound SMS path (what the Twilio
    webhook calls once it has resolved the organization)."""
    from app.routers.sms_router import process_inbound_sms
    with patch("app.services.reply_classification_service.classify_reply",
               return_value={"classification": "neutral", "confidence": "high", "reasoning": "synthetic"}), \
         patch.object(S, "_get_client", return_value=fake):
        out = process_inbound_sms(j["db"], org_id=j["org"].id, advisor=None, From="+1" + lead.phone[-10:],
                                  Body=body, MessageSid=sid)
    j["db"].expire_all()
    return out


def _conv(db, lead):
    return S.conversation_for(db, lead, create=False)


def _ai_turns(db, lead):
    conv = _conv(db, lead)
    if conv is None:
        return []
    return (db.query(ProgramConversationTurn)
            .filter(ProgramConversationTurn.conversation_id == conv.id, ProgramConversationTurn.role == "ai")
            .order_by(ProgramConversationTurn.created_at).all())


def _owner_texts(j):
    return [b for c, t, b in j["sent"] if c == "sms" and t == OWNER_PHONE]


def _owner_emails(j):
    return [b for c, t, b in j["sent"] if c == "email" and t == OWNER_EMAIL]


def _bookings(db, lead):
    return db.query(BookingLink).filter(BookingLink.lead_id == lead.id,
                                        BookingLink.status.in_(("booked", "confirmed"))).all()


def _pick(slot):
    """The family's own words picking exactly this time."""
    return "%s at %s works for me" % (slot.strftime("%A"), slot.strftime("%-I"))


def _live(client, j, **params):
    r = client.get("/program/command", headers=_h(j["db"], j["admin"]), params=params)
    assert r.status_code == 200, r.text[:300]
    return r.json()


def _item(home, lead):
    return next((i for i in home["inbox"] if i["lead_id"] == lead.id), None)


def _family_page(client, j, lead, headers=None):
    r = client.get("/program/command/family/%s" % lead.id, headers=headers or _h(j["db"], j["admin"]))
    assert r.status_code == 200, r.text[:300]
    return r.json()


def _offer(j, lead, sid="SM_A1", body="Yes, I would like to come in for a visit"):
    """The family asks to come in; the setter offers two REAL calendar times."""
    fake = _Fake([_d("offer_times", "I'd be glad to meet with you. Would {TIMES} work for a short visit?",
                     appointment_kind="visit")])
    _inbound(j, lead, body, sid, fake)
    conv = _conv(j["db"], lead)
    return conv, A.offered(conv), fake


# ═════════════════════════════════════════════════════════════════════════════
# A  cemetery visit accepted
# ═════════════════════════════════════════════════════════════════════════════

def test_A_visit_offered_from_calendar_picked_booked_once_paged_once_and_live(journey, client):
    j, db, line = journey, journey["db"], journey["line"]
    lead = _family(j)

    conv, offered, fake = _offer(j, lead)
    # times came from the real calendar engine and are still genuinely open
    assert conv.state == "offered" and len(offered) == 2
    assert all(A.still_free(db, j["prog"], s, A.VISIT) for s in offered)
    sent = line.to(lead, SS.SCI_SETTER)
    assert len(sent) == 1 and all(A.describe(s) in sent[0]["body"] for s in offered)
    assert "- Kerry Allan, %s" % EG in sent[0]["body"]
    assert len(_owner_texts(j)) == 1 and _owner_texts(j)[0].startswith("SCI Appointment Request | Ollie Testcase")

    choice = offered[0]
    fake2 = _Fake([_d("book", "", slot_choice=1)])
    _inbound(j, lead, _pick(choice), "SM_A2", fake2)

    # booked exactly once, at exactly the chosen time, through the real confirm path
    links = _bookings(db, lead)
    assert len(links) == 1 and links[0].booked_time.replace(tzinfo=None) == choice
    assert not A.still_free(db, j["prog"], choice, A.VISIT)               # the time is now held
    conv = _conv(db, lead)
    assert conv.state == "booked" and conv.booked_time == choice
    assert len(line.to(lead, SS.APPOINTMENT_FOLLOWUP)) == 1               # one confirmation text
    # team paged once for the booking (and once for the original request)
    booked = [t for t in _owner_texts(j) if t.startswith("SCI Booked")]
    assert len(booked) == 1 and A.describe(choice) in booked[0]
    assert len(_owner_texts(j)) == 2
    assert db.query(ProgramAlert).filter(ProgramAlert.lead_id == lead.id, ProgramAlert.kind == "booked",
                                         ProgramAlert.channel == "sms", ProgramAlert.recipient == OWNER_PHONE,
                                         ProgramAlert.send_state == "sent").count() == 1
    # a re-confirmation of the same time never books twice
    again = A.book(db, j["prog"], lead, choice, A.VISIT)
    assert again["status"] == "confirmed" and again.get("already") and len(_bookings(db, lead)) == 1

    home = _live(client, j)
    item = _item(home, lead)
    assert item and item["badge"] == "Booked" and item["setter_state"] == "booked"
    assert home["counts"]["visits_booked"] == 1
    fam = _family_page(client, j, lead)
    assert fam["booking"]["when"].startswith(choice.strftime("%Y-%m-%dT%H:%M"))
    assert fam["setter"]["state"] == "booked" and fam["location"] == EG


# ═════════════════════════════════════════════════════════════════════════════
# B  visit declined -> home visit offered once -> accepted -> person, no booking
# ═════════════════════════════════════════════════════════════════════════════

def _invited(db, lead, adv):
    db.add(Message(lead_id=lead.id, sender_id=adv.id, body=INVITE, delivery_status="delivered",
                   sent_at=datetime.utcnow() - timedelta(minutes=20)))
    db.commit()


def _home_offered(j, lead):
    _location(j["db"], lead, home_visits=True)
    _invited(j["db"], lead, j["advisor"])
    fake = _Fake([_d("answer", HOME_OFFER)])
    _inbound(j, lead, "No", "SM_B1", fake)
    return fake


def test_B_declined_visit_home_offered_once_accepted_goes_to_a_person(journey, client):
    j, db, line = journey, journey["db"], journey["line"]
    lead = _family(j)
    fake = _home_offered(j, lead)
    ctx = json.loads(fake.calls[0]["messages"][-1]["content"])
    assert ctx["facts_known_for_certain"]["decline"]["alternative_to_offer"] == S.ALT_HOME
    assert [s["template"] for s in line.to(lead, SS.SCI_SETTER)] == [HOME_OFFER]       # offered once
    assert S.memory_of(_conv(db, lead))["alternative_offered"] == S.ALT_HOME

    fake2 = _Fake([_d("book", "", slot_choice=1),
                   _d("handoff", "Wonderful - I'll reach out to you personally to find a good time.")])
    _inbound(j, lead, "Yes that would be easier", "SM_B2", fake2)
    conv = _conv(db, lead)
    assert len(fake2.calls) == 2                                   # the "book" was refused and corrected
    assert conv.state == "human" and conv.handoff_reason == S.HOME_VISIT_REASON
    assert conv.booked_time is None and not _bookings(db, lead)    # NO automatic booking
    assert db.query(BookingLink).filter_by(lead_id=lead.id).count() == 0
    assert [s["template"] for s in line.to(lead, SS.SCI_SETTER)].count(HOME_OFFER) == 1
    assert S.memory_of(conv)["alternative_outcome"] == "accepted"
    hv = [t for t in _owner_texts(j) if t.startswith("SCI Home Visit Request | Ollie Testcase")]
    assert len(hv) == 1
    assert any(S.HOME_VISIT_REASON in e for e in _owner_emails(j))

    home = _live(client, j)
    item = _item(home, lead)
    assert item["badge"] == "Home visit" and item["home_visit"] is True and home["inbox"][0]["lead_id"] == lead.id
    assert home["filter_counts"]["home_visits"] == 1
    assert [t["lead_id"] for t in home["tasks"] if t["kind"] == "home_visit"] == [lead.id]
    assert _family_page(client, j, lead)["home_visit"]["waiting"] is True


# ═════════════════════════════════════════════════════════════════════════════
# C  home visit declined -> respectful close, no further pressure
# ═════════════════════════════════════════════════════════════════════════════

def test_C_home_visit_declined_closes_respectfully_and_never_pushes_again(journey):
    j, db, line = journey, journey["db"], journey["line"]
    lead = _family(j)
    _home_offered(j, lead)
    fake = _Fake([_d("close", GOODBYE)])
    _inbound(j, lead, "No", "SM_C2", fake)
    conv = _conv(db, lead)
    assert len(fake.calls) == 1 and conv.state == "closed"         # no correction pushing an alternative
    assert S.memory_of(conv)["alternative_outcome"] == "declined"
    assert conv.next_followup_at is None and not _bookings(db, lead)
    assert line.to(lead, SS.SCI_SETTER)[-1]["template"] == GOODBYE
    n = len(line.sent)
    # nothing comes back later: the follow-up and held-reply sweeps send nothing
    later = _Fake([_d("offer_times", "Would {TIMES} work for a visit?")])
    with patch.object(S, "_get_client", return_value=later):
        S.followup_sweep(db, now=datetime.utcnow() + timedelta(days=10))
        S.sweep(db, now=datetime.utcnow() + timedelta(days=10))
    assert later.calls == [] and len(line.sent) == n
    assert not [t for t in _ai_turns(db, lead) if t.action == "offer_times"]


# ═════════════════════════════════════════════════════════════════════════════
# D  planning guide requested -> guide workflow recorded, person can assist
# ═════════════════════════════════════════════════════════════════════════════

def test_D_guide_request_invites_in_then_printed_copy_becomes_a_mail_task(journey, client):
    j, db, line = journey, journey["db"], journey["line"]
    lead = _family(j)
    fake = _Fake([_d("send_guide", "Happy to. I'll get the planning guide to you.")])
    _inbound(j, lead, "Can you send me the planning guide?", "SM_D1", fake)
    conv = _conv(db, lead)
    # links are off on the SCI line: no link is texted, the guide is arranged and they are invited in
    first = line.to(lead, SS.SCI_SETTER)[0]["body"]
    assert "http" not in first and "printed copy" in first and conv.state == "offered"
    assert all(A.describe(s) in first for s in A.offered(conv))
    assert any("guide to be arranged" in c for c in S.memory_of(conv)["commitments"])

    fake2 = _Fake([_d("mail_guide", "Absolutely - I'll have a printed copy sent to that address, and I'd be "
                                    "glad to sit down and go over it with you once you've had a look.")])
    _inbound(j, lead, "Please mail a printed copy to 123 Main Street", "SM_D2", fake2)
    conv = _conv(db, lead)
    assert conv.state == "info_requested" and S.memory_of(conv)["mail_request"]["requested"] is True
    assert db.query(ProgramAlert).filter_by(organization_id=j["org"].id, kind="task", reason="mail_guide").count() == 1
    assert not _bookings(db, lead)

    h = _h(db, j["admin"])
    home = _live(client, j)
    assert [t["lead_id"] for t in home["tasks"] if t["kind"] == "mail_guide"] == [lead.id]
    assert _family_page(client, j, lead)["mail_task"]["done"] is False
    # a person assists: mails it and marks it done
    r = client.post("/program/command/mail-task", headers=h, json={"lead_id": lead.id, "done": True})
    assert r.status_code == 200 and r.json()["mail_task"]["done"] is True
    assert not [t for t in _live(client, j)["tasks"] if t["kind"] == "mail_guide"]
    assert _family_page(client, j, lead)["mail_task"]["done_by"] == "SCI Admin"


def test_D_with_links_on_the_guide_link_is_texted(journey, monkeypatch):
    j, db, line = journey, journey["db"], journey["line"]
    monkeypatch.setenv("SCI_SMS_LINKS", "on")
    lead = _family(j)
    fake = _Fake([_d("send_guide", "Happy to. Here is the planning guide.")])
    _inbound(j, lead, "Can you send me the planning guide?", "SM_D3", fake)
    conv = _conv(db, lead)
    assert "http" in line.to(lead, SS.SCI_SETTER)[0]["body"]
    assert conv.state == "info_requested" and conv.next_followup_at is not None    # one check-in scheduled


# ═════════════════════════════════════════════════════════════════════════════
# E  family wants a human
# ═════════════════════════════════════════════════════════════════════════════

def test_E_family_asks_for_a_person_ai_stops_staff_paged_once_with_context(journey, client):
    j, db, line = journey, journey["db"], journey["line"]
    lead = _family(j)
    words = "I would like to talk to someone on your team directly, please have someone call me"
    fake = _Fake([_d("handoff", "Of course - I'll reach out to you personally.",
                     reason="family asked to speak with a person")])
    _inbound(j, lead, words, "SM_E1", fake)
    conv = _conv(db, lead)
    assert conv.state == "human" and conv.handoff_reason == "family asked to speak with a person"
    owner = _owner_texts(j)
    assert len(owner) == 1 and owner[0].startswith("SCI Needs Attention | Ollie Testcase")
    em = _owner_emails(j)
    assert len(em) == 1 and "needs a person" in em[0] and "family asked to speak with a person" in em[0]
    assert words in em[0] and "(214) 555-0101" in em[0]               # actionable: their words + how to reach them

    # the AI stays out of it from here on
    n = len(line.sent)
    silent = _Fake([_d("answer", "Anything else?")])
    _inbound(j, lead, "Thanks, any time after 3 is good", "SM_E2", silent)
    assert silent.calls == [] and len(line.sent) == n and _conv(db, lead).state == "human"
    assert len(_owner_texts(j)) == 1                                  # merged into the same conversation page

    fam = _family_page(client, j, lead)
    assert fam["handling"]["owner"] == "human" and fam["handling"]["reason"] == "family asked to speak with a person"


# ═════════════════════════════════════════════════════════════════════════════
# F  two quick replies
# ═════════════════════════════════════════════════════════════════════════════

def test_F_two_quick_replies_answered_in_order_once_each_one_alert(journey):
    j, db, line = journey, journey["db"], journey["line"]
    lead = _family(j)
    a1 = "It covers your options, your wishes and the questions families usually ask."
    a2 = "Yes - it walks through burial and cremation choices alike."
    fake = _Fake([_d("answer", a1), _d("answer", a2)])
    _inbound(j, lead, "What does the guide cover?", "SM_F1", fake)
    _inbound(j, lead, "Does it include cremation options too?", "SM_F2", fake)
    assert len(fake.calls) == 2
    # the second decision saw the first answer the family actually received
    assert a1 in fake.calls[1]["messages"][-1]["content"]
    assert [s["template"] for s in line.to(lead, SS.SCI_SETTER)] == [a1, a2]
    resps = db.query(ProgramResponse).filter_by(lead_id=lead.id).order_by(ProgramResponse.received_at).all()
    assert len(resps) == 2
    for resp in resps:                                                # one AI answer per reply
        assert len([t for t in _ai_turns(db, lead) if t.response_id == resp.id]) == 1
    assert len(_owner_texts(j)) == 1 and len(_owner_emails(j)) == 1   # coalesced
    assert db.query(ProgramAlert).filter_by(lead_id=lead.id, channel="sms", recipient=OWNER_PHONE,
                                            send_state="merged").count() == 1


# ═════════════════════════════════════════════════════════════════════════════
# G  duplicate inbound webhook
# ═════════════════════════════════════════════════════════════════════════════

def test_G_duplicate_webhooks_never_double_answer_alert_or_book(journey, client):
    j, db, line = journey, journey["db"], journey["line"]
    lead = _family(j)
    fake = _Fake([_d("offer_times", "Would {TIMES} work for a short visit?", appointment_kind="visit")])
    first = _inbound(j, lead, "Yes, I would like to come in for a visit", "SM_G1", fake)
    again = _inbound(j, lead, "Yes, I would like to come in for a visit", "SM_G1", fake)
    assert first["status"] == "processed" and again["status"] == "duplicate"
    offered = A.offered(_conv(db, lead))
    pick = _Fake([_d("book", "", slot_choice=1)])
    _inbound(j, lead, _pick(offered[0]), "SM_G2", pick)
    _inbound(j, lead, _pick(offered[0]), "SM_G2", pick)

    assert len(fake.calls) == 1 and len(pick.calls) == 1                     # no second AI answer
    assert db.query(ProgramResponse).filter_by(lead_id=lead.id).count() == 2
    assert len(line.to(lead, SS.SCI_SETTER)) == 1
    assert len(line.to(lead, SS.APPOINTMENT_FOLLOWUP)) == 1
    assert len(_bookings(db, lead)) == 1
    assert all(v == 1 for v in _count(_owner_texts(j)).values()) and len(_owner_texts(j)) == 2
    assert len(_owner_emails(j)) == 2
    from app.models.models import Reply
    assert db.query(Reply).filter_by(lead_id=lead.id).count() == 2
    home = _live(client, j)
    assert len([i for i in home["inbox"] if i["lead_id"] == lead.id]) == 1


def _count(xs):
    out = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return out


# ═════════════════════════════════════════════════════════════════════════════
# H  two families, two locations
# ═════════════════════════════════════════════════════════════════════════════

def test_H_two_families_at_two_locations_keep_their_own_identity(journey, client):
    j, db, line = journey, journey["db"], journey["line"]
    ollie, pat = _family(j, OLLIE), _family(j, PAT)
    fo = _Fake([_d("answer", "Thank you for writing back. What questions can I answer for you?")])
    fp = _Fake([_d("answer", "Thank you for writing back. What would be most helpful to know?")])
    _inbound(j, ollie, "Yes, I would like to come in for a visit", "SM_H1", fo)
    _inbound(j, pat, "Yes, I would like to come in for a visit", "SM_H2", fp)

    # the AI wrote as each family's own location
    assert EG in fo.calls[0]["messages"][0]["content"] and SH not in fo.calls[0]["messages"][0]["content"]
    assert SH in fp.calls[0]["messages"][0]["content"] and EG not in fp.calls[0]["messages"][0]["content"]
    # each family's text is signed by its own location, never the other's
    to_o, to_p = line.to(ollie, SS.SCI_SETTER)[0]["body"], line.to(pat, SS.SCI_SETTER)[0]["body"]
    assert "- Kerry Allan, %s" % EG in to_o and SH not in to_o
    assert "- Kerry Allan, %s" % SH in to_p and EG not in to_p
    # each alert carries the family's own location
    owner = _owner_texts(j)
    assert len(owner) == 2
    assert [t for t in owner if "Ollie Testcase" in t][0].count("Location: %s." % EG) == 1
    assert [t for t in owner if "Rosa Diaz" in t][0].count("Location: %s." % SH) == 1
    ro = db.query(ProgramResponse).filter_by(lead_id=ollie.id).one()
    rp = db.query(ProgramResponse).filter_by(lead_id=pat.id).one()
    assert ro.location_id and rp.location_id and ro.location_id != rp.location_id
    home = _live(client, j)
    assert _item(home, ollie)["location"] == EG and _item(home, pat)["location"] == SH
    only_sh = _live(client, j, location_id=rp.location_id)
    assert [i["lead_id"] for i in only_sh["inbox"]] == [pat.id]


# ═════════════════════════════════════════════════════════════════════════════
# I  explicit STOP
# ═════════════════════════════════════════════════════════════════════════════

def test_I_stop_opts_out_ends_cadence_and_nothing_else_goes_out(journey):
    j, db, line = journey, journey["db"], journey["line"]
    lead = _family(j)
    j["hours"]["open"] = False                                     # the AI's offer is held for contact hours
    conv, offered, _ = _offer(j, lead)
    held = [t for t in _ai_turns(db, lead) if t.delivery == "held"]
    assert len(held) == 1 and not line.sent
    pages_before = len(_owner_texts(j))

    none = _Fake([])
    _inbound(j, lead, "STOP", "SM_I1", none)
    lead = db.query(Lead).get(lead.id)
    assert lead.status == "dnc" and none.calls == []
    st = db.query(CadenceState).filter_by(lead_id=lead.id).one()
    # already stopped by the first reply ("replied"); the STOP keeps it ended, never resumed
    assert str(getattr(st.status, "value", st.status)).startswith("stopped")
    assert _conv(db, lead).state == "stopped"
    stop = db.query(ProgramResponse).filter_by(lead_id=lead.id, response_class="opt_out").one()
    assert stop.handling_status == "closed"
    assert len(_owner_texts(j)) == pages_before                    # no page for an opt-out

    # nothing after it: held-reply sweep, follow-up sweep, SLA reminder, booking, setter
    j["hours"]["open"] = True
    later = _Fake([_d("answer", "Just checking in!")])
    with patch.object(S, "_get_client", return_value=later):
        assert S.sweep(db)["sent"] == 0
        S.followup_sweep(db, now=datetime.utcnow() + timedelta(days=5))
    R.sla_sweep(db, now=datetime.utcnow() + timedelta(hours=2))
    assert A.book(db, j["prog"], lead, offered[0], A.VISIT)["status"] == "opted_out"
    _inbound(j, lead, "what times do you have", "SM_I2", later)
    assert later.calls == [] and line.sent == []
    assert db.query(ProgramConversationTurn).get(held[0].id).delivery == "dropped"
    assert not [t for t in _owner_texts(j)[pages_before:] if t.startswith(("SCI Appointment Request", "SCI Still"))]
    assert not _bookings(db, lead)


# Found by this suite (Oct 20): a text after STOP re-opened booking because the
# opted-out phone check compared "+1..." with "1...". Fixed in appointments._opted_out.
def test_I_a_text_after_stop_never_reopens_booking(journey, client):
    j, db, line = journey, journey["db"], journey["line"]
    lead = _family(j)
    _, offered, _ = _offer(j, lead)
    _inbound(j, lead, "STOP", "SM_I3", _Fake([]))
    _inbound(j, lead, "ok what times do you have", "SM_I4", _Fake([]))   # no START: still opted out
    db.expire_all()
    lead = db.query(Lead).get(lead.id)
    assert A._opted_out(db, lead), "family is no longer treated as opted out (status=%s)" % lead.status
    r = client.post("/program/command/schedule", headers=_h(db, j["admin"]),
                    json={"lead_id": lead.id, "slot": offered[1].isoformat()})
    assert r.json().get("status") == "opted_out", r.json()
    assert not _bookings(db, lead) and not line.to(lead, SS.APPOINTMENT_FOLLOWUP)


# ═════════════════════════════════════════════════════════════════════════════
# J  the chosen time is gone / the calendar cannot be read
# ═════════════════════════════════════════════════════════════════════════════

def test_J_time_taken_in_a_race_offers_real_alternatives_never_the_taken_one(journey):
    j, db, line = journey, journey["db"], journey["line"]
    lead = _family(j)
    _, offered, _ = _offer(j, lead)
    taken = offered[0]
    other = _family(j, PAT)                                       # another family takes it first
    assert A.book(db, j["prog"], other, taken, A.VISIT)["status"] == "confirmed"

    fake = _Fake([_d("book", "", slot_choice=1)])
    _inbound(j, lead, _pick(taken), "SM_J1", fake)
    conv = _conv(db, lead)
    assert not _bookings(db, lead) and conv.state == "offered" and conv.booked_time is None
    new = A.offered(conv)
    assert new and taken not in new
    assert all(A.still_free(db, j["prog"], s, A.VISIT) for s in new)          # real, open times
    text = line.to(lead, SS.SCI_SETTER)[-1]["body"]
    assert "just filled up" in text and A.describe(taken) not in text
    assert all(A.describe(s) in text for s in new)
    assert len(_bookings(db, other)) == 1                                      # the other family keeps it
    assert not [t for t in _owner_texts(j) if t.startswith("SCI Booked | Ollie")]


def test_J_unreadable_calendar_hands_off_and_never_invents_a_time(journey, monkeypatch):
    j, db, line = journey, journey["db"], journey["line"]
    lead = _family(j)
    monkeypatch.setattr("app.routers.calendar_router.compute_day_slots",
                        lambda *a, **k: {"slots": [], "calendar_error": "provider unreachable"})
    fake = _Fake([_d("offer_times", "Would {TIMES} work for a short visit?")])
    _inbound(j, lead, "Yes, I would like to come in for a visit", "SM_J2", fake)
    conv = _conv(db, lead)
    assert conv.state == "human" and "no times to offer" in conv.handoff_reason
    assert A.offered(conv) == [] and not _bookings(db, lead)
    # Oct 20 (09a31ade): with no times to offer the setter sends the family
    # NOTHING - a person picks it up; no filler text, no invented time.
    assert line.to(lead, SS.SCI_SETTER) == []
    owner = _owner_texts(j)
    assert len(owner) == 1 and owner[0].startswith("SCI Needs Attention | Ollie Testcase")


# ═════════════════════════════════════════════════════════════════════════════
# K  no location mapping
# ═════════════════════════════════════════════════════════════════════════════

def test_K_family_without_a_location_is_held_for_review_and_never_texted(journey, client, monkeypatch):
    j, db = journey, journey["db"]
    monkeypatch.setattr(sms_service, "send_sms", REAL_SEND_SMS)       # the production sender, gates and all
    lead = _family(j, LEE)
    fake = _Fake([_d("answer", "Thank you for writing back. What questions can I answer for you?")])
    _inbound(j, lead, "Yes I would like to come in", "SM_K1", fake)

    family_phone = "+1" + LEE["phone"]
    assert not [1 for c, t, b in j["sent"] if c == "sms" and t in (family_phone, "1" + LEE["phone"])]
    assert db.query(Message).filter_by(lead_id=lead.id).count() == 0
    resp = db.query(ProgramResponse).filter_by(lead_id=lead.id).one()
    from app.models.program_models import LocationProfile
    bucket = db.query(LocationProfile).filter_by(organization_id=j["org"].id, location_id=resp.location_id).first()
    assert resp.location_id is None or bucket.is_review_bucket          # review bucket - never a guessed home
    assert _item(_live(client, j), lead)["location"] is None            # SCI Live names no location for it
    conv = _conv(db, lead)
    assert conv.state == "human" and "LOCATION REVIEW" in (conv.handoff_reason or "")
    assert [t.delivery for t in _ai_turns(db, lead)] == ["blocked"]
    owner = _owner_texts(j)
    assert len(owner) == 1 and owner[0].startswith("SCI Needs Attention | Lee Nowhere")
    assert "Location:" not in owner[0] or "Location: Unassigned / Location Review." in owner[0]   # honest, not invented
    for loc in (EG, SH, "Striffler-Hamby"):
        assert loc not in owner[0]


# ═════════════════════════════════════════════════════════════════════════════
# L  the authorized advisor works the item; a spectator cannot
# ═════════════════════════════════════════════════════════════════════════════

def _spectator(db, org):
    from app.models.models import User
    from app.services.workspace_access import grant_workspace_membership
    u = User(organization_id=None, email="viewer.only@example.com", password_hash=hash_password("Pass12345!"),
             full_name="Viewer Only", role="advisor", is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    grant_workspace_membership(db, u.id, org.id, role="spectator", check_capacity=False)
    return dict(_h(db, u), **{"X-Workspace-Id": org.id})


def test_L_advisor_acts_with_an_audit_trail_and_a_spectator_cannot(journey, client):
    j, db = journey, journey["db"]
    hv = _family(j)                                                       # home visit waiting
    _home_offered(j, hv)
    _inbound(j, hv, "Yes that would be easier", "SM_L1",
             _Fake([_d("handoff", "Wonderful - I'll reach out to you personally to find a good time.")]))
    person = _family(j, PAT)                                              # wants a person
    _inbound(j, person, "Can someone call me back?", "SM_L2",
             _Fake([_d("handoff", "Of course - I'll reach out to you personally.", reason="asked for a call")]))
    view = _spectator(db, j["org"])
    admin = _h(db, j["admin"])

    # the spectator SEES the work...
    r = client.get("/program/command", headers=view)
    assert r.status_code == 200
    assert {i["lead_id"] for i in r.json()["inbox"]} >= {hv.id, person.id}
    assert client.get("/program/command/family/%s" % hv.id, headers=view).status_code == 200
    # ...and cannot act on it, by any route
    resp_p = db.query(ProgramResponse).filter_by(lead_id=person.id).one()
    slot = A.available_slots(db, j["prog"])[0][0]
    for path, body in (("/program/command/home-visit", {"lead_id": hv.id}),
                       ("/program/responses/%s/mark" % resp_p.id, {"state": "closed"}),
                       ("/program/command/schedule", {"lead_id": person.id, "slot": slot.isoformat()}),
                       ("/program/command/handling", {"lead_id": person.id, "owner": "ai"})):
        r = client.post(path, headers=view, json=body)
        assert r.status_code == 403, (path, r.status_code, r.text[:200])
    db.expire_all()
    assert _live(client, j)["filter_counts"]["home_visits"] == 1
    assert not _bookings(db, person) and resp_p.handling_status != "closed"

    # the authorized advisor marks the home visit arranged ...
    r = client.post("/program/command/home-visit", headers=admin, json={"lead_id": hv.id, "note": "Thu 2pm at home"})
    assert r.status_code == 200 and r.json()["home_visit"]["done_by"] == "SCI Admin"
    assert db.query(ProgramAlert).filter_by(reason="home_visit_arranged").one().message.startswith("SCI Admin arranged")
    assert not _bookings(db, hv)
    # ... schedules the other family into a real time ...
    pages = len(_owner_texts(j))
    r = client.post("/program/command/schedule", headers=admin, json={"lead_id": person.id, "slot": slot.isoformat()})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    db.expire_all()
    assert len(_bookings(db, person)) == 1 and _conv(db, person).state == "booked"
    assert [t for t in _owner_texts(j)[pages:] if t.startswith("SCI Booked | Rosa Diaz")]
    # ... resolves the reply and hands the conversation back, both recorded
    assert client.post("/program/responses/%s/mark" % resp_p.id, headers=admin,
                       json={"state": "closed"}).status_code == 200
    db.expire_all()
    resp_p = db.query(ProgramResponse).get(resp_p.id)
    assert resp_p.handling_status == "closed" and resp_p.opened_by == j["admin"].id and resp_p.closed_at
    r = client.post("/program/command/handling", headers=admin, json={"lead_id": hv.id, "owner": "human"})
    assert r.status_code == 200
    assert db.query(AuditLogEntry).filter_by(action="sci.conversation_owner", target_id=hv.id).count() == 1

    home = _live(client, j)
    assert home["filter_counts"]["home_visits"] == 0
    assert not [t for t in home["tasks"] if t["kind"] == "home_visit"]
    assert _item(home, person) is None or _item(home, person)["badge"] == "Booked"
