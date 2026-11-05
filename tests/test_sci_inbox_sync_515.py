"""#515 (Nov 4): the SCI Conversations Inbox must show what is actually
happening. Production showed no reply was ever lost - but an opt-out that
closed a reply 14 seconds after a human-handoff alert (Kelvin Smith, "Dale"
then "Stop") vanished from every view except "Handled", there was no "latest
reply" / last-updated signal, the Handled view replaced the open-work counts,
and a failed refresh became an empty "Nobody is waiting" list.
Synthetic families; AI off; providers faked; nothing is sent."""
from datetime import datetime, timedelta

from app.models.models import Organization
from app.models.program_models import ProgramResponse
from app.services.programs import responses as R
from tests.test_outreach_program import _admin, _h, _lead_for, program  # noqa: F401 (fixture)
from tests.test_sci_appointment_setter import setter  # noqa: F401 (fixture)


def _quiet(setter):
    setter["prog"].ai_setter_mode = "off"
    setter["db"].commit()


def _fam(db, org, adv, ref, phone, first, email=None, last=None):
    l = _lead_for(db, org, adv, ref, phone=phone, first=first, email=email or "%s@example.com" % first.lower())
    l.is_test = False
    if last:
        l.last_name = last
    db.commit()
    return l


def _get(client, db, admin, **params):
    q = "&".join("%s=%s" % kv for kv in params.items())
    return client.get("/program/command" + ("?" + q if q else ""), headers=_h(db, admin)).json()


def test_a_new_text_and_a_new_email_appear_at_the_top_and_in_the_counts(client, setter, sample_advisor):
    db, org, admin = setter["db"], setter["org"], setter["admin"]
    _quiet(setter)
    a = _fam(db, org, sample_advisor, "L001", "2145550101", "Ann")
    b = _fam(db, org, sample_advisor, "L002", "2145550102", "Bea")
    R.on_inbound(db, a, "Can you call me about a plot?", "sms", now=datetime.utcnow() - timedelta(minutes=30))
    before = _get(client, db, admin)
    R.on_inbound(db, b, "Please send me the prices", "email", now=datetime.utcnow())
    after = _get(client, db, admin)
    assert after["inbox"][0]["lead_id"] == b.id and after["inbox"][0]["channel_label"] == "EMAIL"
    assert after["filter_counts"]["all"] == before["filter_counts"]["all"] + 1
    assert after["filter_counts"]["unread"] == before["filter_counts"]["unread"] + 1
    assert after["filter_counts"]["email"] == before["filter_counts"]["email"] + 1
    assert after["latest_activity"]["lead_id"] == b.id and after["latest_activity"]["where"] == "all"
    assert after["updated_at"]


def test_an_existing_conversation_moves_to_the_top_when_the_family_writes_again(client, setter, sample_advisor):
    db, org, admin = setter["db"], setter["org"], setter["admin"]
    _quiet(setter)
    a = _fam(db, org, sample_advisor, "L001", "2145550101", "Ann")
    b = _fam(db, org, sample_advisor, "L002", "2145550102", "Bea")
    R.on_inbound(db, a, "Call me please", "sms", now=datetime.utcnow() - timedelta(hours=3))
    R.on_inbound(db, b, "Call me please", "sms", now=datetime.utcnow() - timedelta(hours=1))
    assert [i["lead_id"] for i in _get(client, db, admin)["inbox"]][:2] == [b.id, a.id]
    R.on_inbound(db, a, "Are you there?", "sms", now=datetime.utcnow())
    inbox = _get(client, db, admin)["inbox"]
    assert [i["lead_id"] for i in inbox][:2] == [a.id, b.id]
    assert len([i for i in inbox if i["lead_id"] == a.id]) == 1          # one line per family


def test_the_kelvin_case_handoff_then_stop_is_closed_but_never_invisible(client, setter, sample_advisor):
    """"Dale" -> handoff alert; "Stop" 14 s later closes everything. The
    family is not open work, not a handoff, not "needs reply" - and it IS
    in Opted out, counted, and named as the latest reply."""
    db, org, admin = setter["db"], setter["org"], setter["admin"]
    _quiet(setter)
    k = _fam(db, org, sample_advisor, "L001", "2145550101", "Kelvin", last="Smith")
    t0 = datetime.utcnow() - timedelta(minutes=5)
    R.on_inbound(db, k, "Dale", "sms", now=t0)
    R.on_inbound(db, k, "Stop", "sms", now=t0 + timedelta(seconds=14))
    body = _get(client, db, admin)
    assert k.id not in [i["lead_id"] for i in body["inbox"]]
    c = body["filter_counts"]
    assert c["handoff"] == 0 and c["needs_reply"] == 0 and c["opted_out"] == 1
    la = body["latest_activity"]
    assert la["lead_id"] == k.id and la["where"] == "opted_out" and la["open"] is False
    out = _get(client, db, admin, filter="opted_out")
    assert [i["lead_id"] for i in out["inbox"]] == [k.id]
    assert out["filter_counts"] == c                     # a closed view never replaces the open counts
    assert db.query(ProgramResponse).filter_by(lead_id=k.id).filter(ProgramResponse.handling_status != "closed").count() == 0


def test_the_handled_view_keeps_the_open_work_counts(client, setter, sample_advisor):
    db, org, admin = setter["db"], setter["org"], setter["admin"]
    _quiet(setter)
    a = _fam(db, org, sample_advisor, "L001", "2145550101", "Ann")
    b = _fam(db, org, sample_advisor, "L002", "2145550102", "Bea")
    R.on_inbound(db, a, "Call me please", "sms")
    R.on_inbound(db, b, "No longer interested.", "sms")
    open_counts = _get(client, db, admin)["filter_counts"]
    handled = _get(client, db, admin, filter="handled")
    assert handled["filter_counts"] == open_counts
    assert b.id in [i["lead_id"] for i in handled["inbox"]]


def test_a_cut_off_list_says_so_and_the_counts_are_the_real_total(client, setter, sample_advisor):
    db, org, admin = setter["db"], setter["org"], setter["admin"]
    _quiet(setter)
    for n in range(5):
        f = _fam(db, org, sample_advisor, "L00%d" % (n + 1), "214555011%d" % n, "Fam%d" % n)
        R.on_inbound(db, f, "Call me please", "sms", now=datetime.utcnow() - timedelta(minutes=n))
    body = _get(client, db, admin, limit=3)
    assert len(body["inbox"]) == 3 and body["shown"] == 3 and body["total"] == 5
    assert body["filter_counts"]["all"] == 5


def test_two_families_with_the_same_name_are_two_conversations(client, setter, sample_advisor):
    db, org, admin = setter["db"], setter["org"], setter["admin"]
    _quiet(setter)
    a = _fam(db, org, sample_advisor, "L001", "2145550101", "Kevin", last="Smith")
    b = _fam(db, org, sample_advisor, "L002", "2145550102", "Kevin", email="k2@example.com", last="Smith")
    R.on_inbound(db, a, "Call me", "sms")
    R.on_inbound(db, b, "Call me too", "sms")
    ids = [i["lead_id"] for i in _get(client, db, admin)["inbox"]]
    assert a.id in ids and b.id in ids and len(ids) == len(set(ids))


def test_the_same_inbound_message_twice_is_one_line_and_one_response(client, setter, sample_advisor):
    db, org, admin = setter["db"], setter["org"], setter["admin"]
    _quiet(setter)
    a = _fam(db, org, sample_advisor, "L001", "2145550101", "Ann")
    R.on_inbound(db, a, "Call me please", "sms", reply_id="SM-515-dup")
    R.on_inbound(db, a, "Call me please", "sms", reply_id="SM-515-dup")       # Twilio retried the webhook
    assert db.query(ProgramResponse).filter_by(lead_id=a.id).count() == 1
    assert [i["lead_id"] for i in _get(client, db, admin)["inbox"]].count(a.id) == 1


def test_another_workspace_sees_none_of_it(client, setter, sample_advisor, db_session):
    db, org = setter["db"], setter["org"]
    _quiet(setter)
    a = _fam(db, org, sample_advisor, "L001", "2145550101", "Ann")
    R.on_inbound(db, a, "Call me please", "sms")
    other = Organization(name="Elsewhere 515", slug="elsewhere-515")
    db.add(other)
    db.commit()
    stranger = _admin(db, other, email="stranger515@example.com")
    r = client.get("/program/command", headers=_h(db, stranger))
    assert r.status_code in (403, 404) or a.id not in [i["lead_id"] for i in r.json().get("inbox", [])]


def test_the_screen_shows_failures_and_freshness_instead_of_an_empty_list():
    src = open("frontend/src/pages/program/command/Command.jsx", encoding="utf-8").read()
    inbox = src[src.index("function InboxTab"):src.index("function Unknown")]
    assert "setR({ inbox: [] })" not in inbox            # a failed refresh is never "Nobody is waiting"
    assert "Couldn't refresh" in inbox and "Try again" in inbox
    assert "Updated ${time(r.updated_at)}" in inbox and "Refresh" in inbox
    assert "visibilitychange" in inbox and "'focus'" in inbox
    assert "latest_activity" in inbox and "'opted_out'" in src
