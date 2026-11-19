"""EvoSys Sales texting (owner request 2026-11-17): text steps go out from the
team's own number, phone-only leads are reached, every text carries the opt-out
line, and a STOP or a reply to that number stops the lead."""
from datetime import datetime

import pytest

from app.models.evosys_sales_models import EvoSysOutreachMessage, EvoSysOutreachState
from app.models.sales_models import Opportunity, OpportunityEvent
from app.services import sales_suppression as SDNC
from app.services.evosys_sales import campaigns as CA
from app.services.evosys_sales import outreach as O
from app.services.evosys_sales import sms_inbound as IN

from tests.test_sales_workspace import (  # noqa: F401  (fixtures)
    platform, brand, manager, manager_headers, _make_sales_user, _add_membership, _token_headers, _SEQ,
)

MON_10AM = datetime(2026, 11, 23, 16, 0)   # Monday 10:00 Central
TEAM_NO = "+18664870628"


@pytest.fixture(autouse=True)
def _brand_env(monkeypatch, brand):
    monkeypatch.setenv("EVOSYS_SALES_BRAND_ID", brand.id)


class Rec:
    def __init__(self):
        self.calls = []

    def __call__(self, *a):
        self.calls.append(a)
        return {"success": True, "provider_message_id": "SM%d" % len(self.calls)}


STEPS = [
    {"day": 0, "channel": "text", "subject": "",
     "body": "Hi {first_name}, this is {sender_name} with EvoSys Pro. Saw {company}'s cleaning ad. "
             "Can I send you the details?"},
    {"day": 2, "channel": "email", "subject": "Details for {company}", "body": "Hi {first_name}. {booking_link}"},
]


def _ready(db, brand, number=TEAM_NO):
    s = O.get_settings(db, brand.id)
    s.mailing_address, s.offer = "100 Main St, Dallas, TX 75201", "Free for 30 days."
    s.sms_from_number = number
    db.commit()
    c = CA.ensure_default(db, s)
    c.steps = STEPS
    c.approved_hash = CA.content_hash(c, s)
    c.sending_on = True
    db.commit()
    return s


def _lead(db, brand, owner, company, phone=None, email=None):
    o = Opportunity(brand_sales_org_id=brand.id, company_name=company, contact_name="Ana Ruiz",
                    phone=phone, email=email, stage="prospect", status="open", owner_user_id=owner.id)
    db.add(o); db.commit()
    return o


def test_phone_only_lead_gets_the_text_with_the_stop_line(db_session, brand, manager):
    _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "Shine Co", phone="(214) 555-0101")
    texts, emails = Rec(), Rec()
    out = O.run_pass(db_session, MON_10AM, emails, texts)
    assert out["sent"] == 1 and not emails.calls
    to, body, _s = texts.calls[0]
    assert to == "+12145550101"
    assert "EvoSys Pro" in body and "Shine Co" in body and body.endswith("Reply STOP to opt out.")
    m = db_session.query(EvoSysOutreachMessage).filter_by(opportunity_id=o.id, channel="text").one()
    assert m.status == "sent" and m.provider_message_id == "SM1"
    assert db_session.get(EvoSysOutreachState, o.id).next_step == 1


def test_email_only_lead_skips_the_text_step(db_session, brand, manager):
    _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "Mail Co", email="owner@mail.test")
    texts, emails = Rec(), Rec()
    O.run_pass(db_session, MON_10AM, emails, texts)
    assert not texts.calls
    st = db_session.get(EvoSysOutreachState, o.id)
    assert st.status == "active" and st.next_step == 1      # moved on to the email step
    assert not db_session.query(EvoSysOutreachMessage).filter_by(opportunity_id=o.id).count()


def test_without_a_number_texts_are_held_and_phone_only_leads_wait(db_session, brand, manager):
    _ready(db_session, brand, number=None)
    phone_only = _lead(db_session, brand, manager, "P Co", phone="2145550102")
    both = _lead(db_session, brand, manager, "B Co", phone="2145550103", email="b@b.test")
    texts = Rec()
    O.run_pass(db_session, MON_10AM, Rec(), texts)
    assert not texts.calls
    assert db_session.get(EvoSysOutreachState, phone_only.id) is None      # not reachable: not enrolled
    held = db_session.query(EvoSysOutreachMessage).filter_by(opportunity_id=both.id).one()
    assert held.status == "held" and held.channel == "text"


def test_do_not_contact_phone_is_never_texted(db_session, brand, manager):
    _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "D Co", phone="2145550104")
    SDNC.record(db_session, o, manager, "asked")
    db_session.commit()
    texts = Rec()
    O.run_pass(db_session, MON_10AM, Rec(), texts)
    assert not texts.calls


def test_stop_by_text_opts_out_and_records_do_not_contact(db_session, brand, manager):
    _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "S Co", phone="214-555-0105")
    O.run_pass(db_session, MON_10AM, Rec(), Rec())
    assert IN.handle(db_session, from_number="+12145550105", to_number=TEAM_NO, body="STOP", message_sid="SMin1")
    st = db_session.get(EvoSysOutreachState, o.id)
    assert st.status == "stopped" and st.stop_reason == "opted_out" and st.opted_out_at
    assert SDNC.find(db_session, brand.id, phone="2145550105") is not None
    # a Twilio retry of the same message is not recorded twice
    assert IN.handle(db_session, from_number="+12145550105", to_number=TEAM_NO, body="STOP", message_sid="SMin1")
    assert db_session.query(EvoSysOutreachMessage).filter_by(opportunity_id=o.id, direction="in").count() == 1


def test_a_reply_by_text_stops_follow_up_and_needs_a_person(db_session, brand, manager):
    _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "R Co", phone="2145550106")
    O.run_pass(db_session, MON_10AM, Rec(), Rec())
    assert IN.handle(db_session, from_number="+12145550106", to_number=TEAM_NO,
                     body="Yes send it over", message_sid="SMin2")
    st = db_session.get(EvoSysOutreachState, o.id)
    assert st.stop_reason == "replied"
    ev = db_session.query(OpportunityEvent).filter_by(opportunity_id=o.id, event_type="outreach_reply").one()
    assert ev.summary.startswith("NEEDS A PERSON")
    assert SDNC.find(db_session, brand.id, phone="2145550106") is None


def test_other_numbers_are_left_to_the_customer_pipeline(db_session, brand, manager):
    _ready(db_session, brand)
    assert IN.handle(db_session, from_number="+12145550107", to_number="+18553601164",
                     body="STOP", message_sid="SMin3") is False


def test_emails_must_go_from_support(db_session, brand):
    s = _ready(db_session, brand)
    s.from_email = "info@evosyspro.live"
    assert any("support@evosyspro.live" in p for p in O.settings_problems(s))
    s.from_email = "support@evosyspro.live"
    assert not any("go out from" in p for p in O.settings_problems(s))
    assert O._Sender(s).from_email == "support@evosyspro.live"


def test_a_reply_alerts_the_owner_by_text_and_email(db_session, brand, manager, monkeypatch):
    s = _ready(db_session, brand)
    s.alert_phone, s.alert_email = "+18435550199", "owner@alerts.test"
    db_session.commit()
    o = _lead(db_session, brand, manager, "Alert Co", phone="2145550108")
    texts, mails = [], []

    class _Msgs:
        def create(self, **kw):
            texts.append(kw)

    class _Client:
        messages = _Msgs()

    from app.services import sms_programs, email_service
    monkeypatch.setattr(sms_programs, "sci_sender", lambda: (_Client(), "+18449172171"))
    monkeypatch.setattr(email_service, "send_email_via_provider",
                        lambda to, subj, html, **kw: mails.append((to, subj, kw["org"].from_email)) or {"success": True})
    IN.handle(db_session, from_number="+12145550108", to_number=TEAM_NO, body="Call me", message_sid="SMin4")
    assert texts and texts[0]["to"] == "+18435550199" and "Alert Co" in texts[0]["body"]
    assert mails and mails[0][0] == "owner@alerts.test" and mails[0][2] == "support@evosyspro.live"
    # STOP does not page anyone
    texts.clear(); mails.clear()
    _lead(db_session, brand, manager, "Quiet Co", phone="2145550109")
    IN.handle(db_session, from_number="+12145550109", to_number=TEAM_NO, body="stop", message_sid="SMin5")
    assert not texts and not mails
    assert o is not None


def test_texts_only_campaign_needs_no_mailing_address(db_session, brand):
    s = _ready(db_session, brand)
    s.mailing_address = None
    c = CA.ensure_default(db_session, s)
    c.steps = [STEPS[0]]
    assert not any("mailing address" in p for p in CA.problems(c, s))
    c.steps = STEPS
    assert any("mailing address" in p for p in CA.problems(c, s))


def test_test_text_goes_only_to_the_alert_phone(db_session, brand, manager, monkeypatch):
    from app.routers import evosys_bd_router as R
    s = _ready(db_session, brand)
    c = CA.ensure_default(db_session, s)
    o = _lead(db_session, brand, manager, "Test Co", phone="2145550110")
    db_session.add(EvoSysOutreachState(opportunity_id=o.id, brand_sales_org_id=brand.id, campaign_id=c.id,
                                       status="queued", next_step=0))
    db_session.commit()
    sent = []
    monkeypatch.setattr(O, "_send_text", lambda to, body, s: sent.append((to, body)) or {"success": True})
    ctx = R.Ctx(manager, brand.id, True)
    with pytest.raises(Exception):
        R.test_text(c.id, R.TestTextIn(step=1), ctx, db_session)      # no alert phone yet
    s.alert_phone = "+18435550199"
    db_session.commit()
    out = R.test_text(c.id, R.TestTextIn(step=1), ctx, db_session)
    assert sent == [("+18435550199", out["text"])]
    assert out["text"].endswith("Reply STOP to opt out.") and "Test Co" in out["text"]
    assert not db_session.query(EvoSysOutreachMessage).filter_by(opportunity_id=o.id).count()


def test_texts_drop_bracketed_notes_and_hold_unnamed_businesses():
    assert O.text_company_name("Office Pride (DFW)") == "Office Pride"
    assert O.text_company_name("Fresh Nest (A KGM Services Company)") == "Fresh Nest"
    assert O.text_company_name("Limpieza (name not shown)") is None
    assert O.text_company_name("Shine Co") == "Shine Co"


def test_unnamed_business_is_held_not_texted(db_session, brand, manager):
    _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "Cleaning (name not shown)", phone="2145550111")
    texts = Rec()
    O.run_pass(db_session, MON_10AM, Rec(), texts)
    assert not texts.calls
    assert (db_session.get(EvoSysOutreachState, o.id).held_reason or "").startswith("missing:company")


# ── AI answers to text replies (owner request 2026-11-18) ─────────────────────

import json as _json
from types import SimpleNamespace as _NS

from app.services.evosys_sales import ai as AI


class _FakeAI:
    def __init__(self, *answers):
        self.answers, self.seen = list(answers), []
        self.chat = _NS(completions=_NS(create=self._create))

    def _create(self, **kw):
        self.seen.append(kw)
        return _NS(choices=[_NS(message=_NS(content=_json.dumps(self.answers.pop(0))))], usage=None)


def _auto_world(db_session, brand, manager, monkeypatch, answer, auto=True):
    s = _ready(db_session, brand)
    s.text_auto_reply = auto
    db_session.commit()
    o = _lead(db_session, brand, manager, "Shine Co", phone="2145550120")
    O.run_pass(db_session, MON_10AM, Rec(), Rec())
    fake = _FakeAI(answer)
    monkeypatch.setattr(AI, "_get_client", lambda: fake)
    sent = []
    monkeypatch.setattr(O, "_send_text", lambda to, body, s: sent.append((to, body)) or {"success": True, "provider_message_id": "SMai"})
    from app.services import sms_programs
    monkeypatch.setattr(sms_programs, "sci_sender", lambda: (_NS(messages=_NS(create=lambda **k: None)), "+18449172171"))
    return o, fake, sent


def test_ai_answers_a_simple_yes_by_itself(db_session, brand, manager, monkeypatch):
    o, fake, sent = _auto_world(db_session, brand, manager, monkeypatch, {
        "category": "interested", "summary": "Said yes", "simple": True,
        "draft": "Awesome! Let's get you those 3 appointments. Got 10 minutes for a quick call? https://x.test/book"})
    IN.handle(db_session, from_number="+12145550120", to_number=TEAM_NO, body="Yes", message_sid="SMy1")
    assert sent and sent[0][0] == "+12145550120" and "3 appointments" in sent[0][1]
    out = db_session.query(EvoSysOutreachMessage).filter_by(opportunity_id=o.id, label="ai_answer").one()
    assert out.status == "sent"
    # the model was told the owner's offer, in background mode for this one feature
    assert "absolutely free" in fake.seen[0]["messages"][0]["content"]


def test_ai_never_auto_sends_a_price_or_a_hard_one(db_session, brand, manager, monkeypatch):
    o, fake, sent = _auto_world(db_session, brand, manager, monkeypatch, {
        "category": "question", "summary": "Asked the price", "simple": True,
        "draft": "After 30 days it's $497 a month."})
    IN.handle(db_session, from_number="+12145550120", to_number=TEAM_NO, body="How much after?", message_sid="SMy2")
    assert not sent
    t = AI.latest(db_session, "text_reply",
                  db_session.query(EvoSysOutreachMessage).filter_by(provider_message_id="SMy2").one().id)
    assert t.output["auto_ok"] is False and t.output["draft"] == ""


def test_with_ai_answers_off_nothing_is_sent_or_asked(db_session, brand, manager, monkeypatch):
    o, fake, sent = _auto_world(db_session, brand, manager, monkeypatch, {
        "category": "interested", "summary": "", "simple": True, "draft": "Hi"}, auto=False)
    IN.handle(db_session, from_number="+12145550120", to_number=TEAM_NO, body="Yes", message_sid="SMy3")
    assert not sent and not fake.seen


def test_ai_stops_after_three_answers_per_lead(db_session, brand, manager, monkeypatch):
    ans = {"category": "interested", "summary": "", "simple": True, "draft": "Great, let's talk: https://x.test"}
    o, fake, sent = _auto_world(db_session, brand, manager, monkeypatch, ans)
    fake.answers = [dict(ans) for _ in range(4)]
    for i in range(4):
        IN.handle(db_session, from_number="+12145550120", to_number=TEAM_NO, body="yes %d" % i, message_sid="SMz%d" % i)
    assert len(sent) == 3


# ── own booking page (owner request 2026-11-18) ───────────────────────────────

from app.services.evosys_sales import booking_page as BP
from app.services.evosys_sales import facts as FACTS


def test_booking_link_is_each_leads_own_page(db_session, brand, manager, monkeypatch):
    monkeypatch.setenv("PROGRAM_ASSET_BASE_URL", "https://links.example.test")
    s = _ready(db_session, brand)
    c = CA.ensure_default(db_session, s)
    c.booking_url = "https://links.example.test/evosys-sales-public/book"
    o = _lead(db_session, brand, manager, "Book Co", phone="2145550130")
    r = O.render_text(o, s, {"body": "Grab a time: {booking_link}"}, c)
    url = r["text"].split("Grab a time: ")[1].split(" ")[0]
    assert url.startswith("https://links.example.test/evosys-sales-public/book/")
    assert BP.read(url.rsplit("/", 1)[1]) == o.id
    assert BP.read("forged") is None


def test_booking_from_the_link_books_stops_and_confirms(db_session, brand, manager, monkeypatch):
    from datetime import timedelta as _td
    s = _ready(db_session, brand)
    s.alert_phone = "+18435550199"
    db_session.commit()
    o = _lead(db_session, brand, manager, "Book Co", phone="2145550131")
    O.run_pass(db_session, MON_10AM, Rec(), Rec())
    when = datetime.utcnow().replace(microsecond=0, second=0, minute=0) + _td(days=2)
    monkeypatch.setattr(BP, "slots", lambda db, opp, start=None, days=7: {"slots": [when.isoformat() + "Z"]})
    made = []
    import app.routers.sales_scheduling_router as SR
    monkeypatch.setattr(SR, "create_appointment", lambda body, user, db: made.append(body) or {"id": "a1"})
    sent, alerts = [], []
    monkeypatch.setattr(O, "_send_text", lambda to, body, s: sent.append((to, body)) or {"success": True})
    monkeypatch.setattr(IN, "notify_owner", lambda s, line, subj, html: alerts.append(line))
    out = BP.book(db_session, o, when)
    assert out["status"] == "booked" and made and made[0].opportunity_id == o.id
    assert db_session.get(EvoSysOutreachState, o.id).stop_reason == "booked"
    assert sent and sent[0][0] == "+12145550131" and "booked" in sent[0][1]
    assert alerts and "BOOKED" in alerts[0]
    # a time not on offer is refused
    assert BP.book(db_session, _lead(db_session, brand, manager, "Two Co", phone="2145550132"),
                   when + _td(hours=1))["status"] == "taken"


def test_booking_page_renders_for_a_real_lead_only(db_session, brand, manager):
    from app.routers import evosys_sales_router as R
    o = _lead(db_session, brand, manager, "Office Pride (DFW)", phone="2145550133")
    tok = BP.url_for(o.id).rsplit("/", 1)[1]
    page = R.booking_page(tok, db_session)
    assert page.status_code == 200 and b"Office Pride &mdash;" in page.body and b"(DFW)" not in page.body
    assert R.booking_page("nope", db_session).status_code == 404


def test_reply_language_and_short_link(db_session, brand, manager, monkeypatch):
    assert AI.looks_spanish("Con gusto nomás mándeme la dirección de los trabajos")
    assert not AI.looks_spanish("Hi Mike, thanks for reaching out! What cities are the businesses located in?")
    o, fake, sent = _auto_world(db_session, brand, manager, monkeypatch, {
        "category": "question", "summary": "", "simple": True,
        "draft": "Hola, gracias por su interés. Las empresas están en su zona. ¿Hablamos?"})
    IN.handle(db_session, from_number="+12145550120", to_number=TEAM_NO,
              body="Thanks! What cities are the businesses in, and is it really free?", message_sid="SMl1")
    assert not sent                                   # Spanish draft to an English text: held for a person
    assert '"reply_language": "English"' in fake.seen[0]["messages"][1]["content"]
    monkeypatch.setenv("PROGRAM_ASSET_BASE_URL", "https://links.example.test")
    url = BP.short_url_for(o)
    assert url.startswith("https://links.example.test/evosys-sales-public/b/") and len(url) < 70
    assert BP.by_code(db_session, url.rsplit("/", 1)[1]).id == o.id


def test_ai_reply_gets_the_leads_own_booking_link(db_session, brand, manager, monkeypatch):
    monkeypatch.setenv("PROGRAM_ASSET_BASE_URL", "https://links.example.test")
    o, fake, sent = _auto_world(db_session, brand, manager, monkeypatch, {
        "category": "interested", "summary": "", "simple": True, "draft": "Great! Book here: https://x.test"})
    s = O.get_settings(db_session, brand.id)
    s.booking_url = "https://links.example.test/evosys-sales-public/book"
    for c in CA.all_for(db_session, s):
        c.booking_url = s.booking_url
    db_session.commit()
    IN.handle(db_session, from_number="+12145550120", to_number=TEAM_NO, body="Yes", message_sid="SMbl1")
    link = _json.loads(fake.seen[0]["messages"][1]["content"])["booking_link"]
    assert "/evosys-sales-public/b/" in link


def test_text_reminders_day_before_and_hour_before_once_each(db_session, brand, manager, monkeypatch):
    from datetime import timedelta as _td
    from app.models.scheduling_models import SalesAppointment
    _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "Remind Co", phone="2145550140")
    call = datetime(2026, 11, 24, 16, 0)            # Tue 10:00 AM Central
    db_session.add(SalesAppointment(brand_sales_org_id=brand.id, opportunity_id=o.id, title="Intro call",
                                    starts_at=call, ends_at=call + _td(minutes=30), status="scheduled",
                                    created_by_user_id=manager.id) if hasattr(SalesAppointment, "created_by_user_id")
                   else SalesAppointment(brand_sales_org_id=brand.id, opportunity_id=o.id, title="Intro call",
                                         starts_at=call, ends_at=call + _td(minutes=30), status="scheduled"))
    db_session.commit()
    sent = []
    monkeypatch.setattr(O, "_send_text", lambda to, body, s: sent.append(body) or {"success": True})
    BP.send_reminders(db_session, datetime(2026, 11, 23, 8, 0))     # Mon 2 AM Central: too early, nothing
    assert not sent
    BP.send_reminders(db_session, datetime(2026, 11, 23, 17, 0))    # Mon 11 AM Central: day-before
    BP.send_reminders(db_session, datetime(2026, 11, 23, 18, 0))    # again: not repeated
    assert len(sent) == 1 and "tomorrow at 10:00 AM Central" in sent[0]
    BP.send_reminders(db_session, datetime(2026, 11, 24, 15, 0))    # 1 hour before
    BP.send_reminders(db_session, datetime(2026, 11, 24, 15, 10))
    assert len(sent) == 2 and "in about an hour" in sent[1]


def test_confirmation_has_zoom_and_speaks_spanish(db_session, brand, manager, monkeypatch):
    from datetime import timedelta as _td
    _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "Limpieza Sol", phone="2145550150")
    db_session.add(EvoSysOutreachMessage(brand_sales_org_id=brand.id, opportunity_id=o.id, direction="in",
                                         channel="text", body="Sí, me interesa mucho, gracias", status="received"))
    db_session.commit()
    monkeypatch.setattr(BP, "zoom_link", lambda db, appt_id: "https://zoom.example/j/1")
    sent = []
    monkeypatch.setattr(O, "_send_text", lambda to, body, s: sent.append(body) or {"success": True})
    monkeypatch.setattr(IN, "notify_owner", lambda *a: None)
    BP._after_booking(db_session, o, datetime(2026, 11, 24, 21, 30), "appt-1")
    assert sent and sent[0].startswith("¡Listo!") and "https://zoom.example/j/1" in sent[0] and "martes 24" in sent[0]


def test_ai_learns_from_answers_a_person_sent(db_session, brand, manager, monkeypatch):
    o, fake, sent = _auto_world(db_session, brand, manager, monkeypatch, {
        "category": "interested", "summary": "", "simple": True, "draft": "Hi! Pick a time: https://x.test"})
    other = _lead(db_session, brand, manager, "Earlier Co", phone="2145550160")
    db_session.add(EvoSysOutreachMessage(brand_sales_org_id=brand.id, opportunity_id=other.id, direction="in",
                                         channel="text", body="is this legit?", status="received",
                                         created_at=datetime(2026, 11, 18, 20, 0)))
    db_session.add(EvoSysOutreachMessage(brand_sales_org_id=brand.id, opportunity_id=other.id, direction="out",
                                         channel="text", body="Totally legit! Grab a time: https://x/b/abc",
                                         status="sent", actor_user_id=manager.id,
                                         created_at=datetime(2026, 11, 18, 20, 5)))
    db_session.commit()
    IN.handle(db_session, from_number="+12145550120", to_number=TEAM_NO, body="Yes please", message_sid="SMex1")
    ex = _json.loads(fake.seen[0]["messages"][1]["content"])["examples"]
    assert ex == [{"they_said": "is this legit?", "mike_answered": "Totally legit! Grab a time: [booking link]"}]


def test_booking_page_keeps_15_minutes_clear_around_calls(db_session, brand, manager, monkeypatch):
    from datetime import timedelta as _td
    from app.models.scheduling_models import SalesAppointment, AppointmentParticipant
    import app.routers.sales_scheduling_router as SR
    o = _lead(db_session, brand, manager, "Gap Co", phone="2145550161")
    day = (datetime.utcnow() + _td(days=3)).replace(hour=20, minute=0, second=0, microsecond=0)
    a = SalesAppointment(brand_sales_org_id=brand.id, title="x", starts_at=day, ends_at=day + _td(minutes=30),
                         status="scheduled")
    db_session.add(a); db_session.flush()
    db_session.add(AppointmentParticipant(appointment_id=a.id, user_id=manager.id, busy_start_at=day,
                                          busy_end_at=day + _td(minutes=30), is_blocking=True))
    db_session.commit()
    offered = [day + _td(minutes=m) for m in (-60, -45, -30, 30, 45, 60)]
    monkeypatch.setattr(SR, "find_team_time", lambda body, user, db: {"slots": [{"starts_at": t.isoformat() + "Z"} for t in offered]})
    got = BP.slots(db_session, o, day.date(), 2)["slots"]
    mins = sorted(int((datetime.fromisoformat(x[:-1]) - day).total_seconds() // 60) for x in got)
    assert mins == [-60, -45, 45, 60]        # -30 and +30 are inside the 15-minute gap
