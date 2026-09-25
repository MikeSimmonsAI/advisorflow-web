"""Seller cadence — the control surface, and every way it must stop.

The cadence itself is `app/services/cadence_service.py` and is not re-tested
here. What is tested is the wholesale layer on top of it: who may be enrolled,
who may not, and the four conditions under which a running sequence has to stop
whether or not anybody remembers to stop it.

NOTHING HERE SENDS. `CADENCE_SMS_SENDING` is unset in the test environment, and
enrolment only schedules — the runner is a separate cron that this file never
invokes.
"""

import pytest

from app.models.models import CadenceState, Lead


def ok(response):
    assert response.status_code in (200, 201), \
        "%s %s" % (response.status_code, response.text[:400])
    return response.json()


def make_deal(client, headers, *, address, is_test=False, phone="2145556100"):
    prop = ok(client.post("/wholesale/properties", headers=headers,
                          json={"street_address": address, "city": "Dallas",
                                "state": "TX", "is_test": is_test}))
    if phone:
        ok(client.post("/wholesale/properties/%s/seller" % prop["id"],
                       headers=headers,
                       json={"first_name": "Cad", "last_name": "Owner",
                             "phone": phone}))
    return prop


# ── Status ──────────────────────────────────────────────────────────────────

def test_a_deal_with_no_owner_says_so_rather_than_offering_a_start_button(
        client, auth_headers):
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "200 No Owner St", "state": "TX"}))
    status = ok(client.get("/wholesale/deals/%s/cadence" % prop["deal"]["id"],
                           headers=auth_headers))
    assert status["state"] == "no_seller"
    assert status["can_start"] is False


def test_a_fresh_seller_can_be_started_and_the_status_says_it_is_not_running(
        client, auth_headers):
    prop = make_deal(client, auth_headers, address="201 Fresh St")
    status = ok(client.get("/wholesale/deals/%s/cadence" % prop["deal"]["id"],
                           headers=auth_headers))
    assert status["state"] == "not_started"
    assert status["can_start"] is True
    assert status["blockers"] == []


def test_the_status_admits_when_the_deployment_cannot_send(client, auth_headers):
    """Enrolling into a cadence that cannot send must not look like it is working."""
    prop = make_deal(client, auth_headers, address="202 Switch St")
    status = ok(client.get("/wholesale/deals/%s/cadence" % prop["deal"]["id"],
                           headers=auth_headers))
    assert status["sending_enabled"] is False
    # Phase 5 split this one string into two audiences. The note an
    # acquisitions person reads on the seller screen says what is true in
    # words they can act on; the variable name they cannot act on moved to
    # `sending_note_technical`, which is what an administrator reads. Both are
    # asserted, because losing either one is a regression: the first would make
    # the screen unreadable, the second would make the problem unfixable.
    assert "turned off" in status["sending_note"]
    assert "CADENCE_SMS_SENDING" not in status["sending_note"]
    assert "CADENCE_SMS_SENDING" in status["sending_note_technical"]


# ── The lifecycle ───────────────────────────────────────────────────────────

def test_start_pause_resume_stop(client, auth_headers):
    prop = make_deal(client, auth_headers, address="203 Lifecycle St")
    deal_id = prop["deal"]["id"]

    started = ok(client.post("/wholesale/deals/%s/cadence" % deal_id,
                             headers=auth_headers, json={"action": "start"}))
    assert started["state"] == "active"
    assert started["can_pause"] is True
    assert started["next_touch_due_at"]

    paused = ok(client.post("/wholesale/deals/%s/cadence" % deal_id,
                            headers=auth_headers, json={"action": "pause"}))
    assert paused["state"] == "paused"
    assert paused["can_resume"] is True

    resumed = ok(client.post("/wholesale/deals/%s/cadence" % deal_id,
                             headers=auth_headers, json={"action": "resume"}))
    assert resumed["state"] == "active"

    stopped = ok(client.post("/wholesale/deals/%s/cadence" % deal_id,
                             headers=auth_headers, json={"action": "stop"}))
    assert stopped["state"] == "stopped_manual"
    assert stopped["can_pause"] is False


def test_starting_twice_is_refused_rather_than_silently_ignored(client, auth_headers):
    prop = make_deal(client, auth_headers, address="204 Twice St")
    deal_id = prop["deal"]["id"]
    ok(client.post("/wholesale/deals/%s/cadence" % deal_id,
                   headers=auth_headers, json={"action": "start"}))
    again = client.post("/wholesale/deals/%s/cadence" % deal_id,
                        headers=auth_headers, json={"action": "start"})
    assert again.status_code == 409
    assert "already in a cadence" in again.json()["detail"]


def test_a_stopped_cadence_can_be_started_again(client, auth_headers):
    """The engine's own start_cadence returns the existing row untouched, which
    would silently do nothing. The wholesale layer restarts it explicitly."""
    prop = make_deal(client, auth_headers, address="205 Restart St")
    deal_id = prop["deal"]["id"]
    ok(client.post("/wholesale/deals/%s/cadence" % deal_id,
                   headers=auth_headers, json={"action": "start"}))
    ok(client.post("/wholesale/deals/%s/cadence" % deal_id,
                   headers=auth_headers, json={"action": "stop"}))
    restarted = ok(client.post("/wholesale/deals/%s/cadence" % deal_id,
                               headers=auth_headers, json={"action": "start"}))
    assert restarted["state"] == "active"


def test_pausing_something_that_is_not_running_is_refused(client, auth_headers):
    prop = make_deal(client, auth_headers, address="206 Nothing St")
    response = client.post("/wholesale/deals/%s/cadence" % prop["deal"]["id"],
                           headers=auth_headers, json={"action": "pause"})
    assert response.status_code == 409


# ── The refusals ────────────────────────────────────────────────────────────

def test_a_sandbox_deal_cannot_be_enrolled(client, auth_headers):
    """A rehearsal that schedules real texts is not a rehearsal."""
    prop = make_deal(client, auth_headers, address="207 Sandbox St", is_test=True)
    status = ok(client.get("/wholesale/deals/%s/cadence" % prop["deal"]["id"],
                           headers=auth_headers))
    assert status["can_start"] is False
    assert any("sandbox" in b for b in status["blockers"])

    response = client.post("/wholesale/deals/%s/cadence" % prop["deal"]["id"],
                           headers=auth_headers, json={"action": "start"})
    assert response.status_code == 409
    assert "sandbox" in response.json()["detail"]


def test_a_seller_with_no_phone_cannot_be_enrolled(client, auth_headers):
    prop = make_deal(client, auth_headers, address="208 Nophone St", phone=None)
    ok(client.post("/wholesale/properties/%s/seller" % prop["id"],
                   headers=auth_headers,
                   json={"first_name": "Silent", "last_name": "Owner",
                         "email": "silent@example.com"}))
    status = ok(client.get("/wholesale/deals/%s/cadence" % prop["deal"]["id"],
                           headers=auth_headers))
    assert any("No phone number" in b for b in status["blockers"])


def test_a_dnc_seller_cannot_be_enrolled(client, auth_headers):
    prop = make_deal(client, auth_headers, address="209 Stop St")
    ok(client.post("/wholesale/deals/%s/seller-reply" % prop["deal"]["id"],
                   headers=auth_headers, json={"message": "STOP"}))
    status = ok(client.get("/wholesale/deals/%s/cadence" % prop["deal"]["id"],
                           headers=auth_headers))
    assert status["can_start"] is False
    assert any("DNC" in b for b in status["blockers"])


def test_a_closed_deal_cannot_be_enrolled(client, auth_headers):
    prop = make_deal(client, auth_headers, address="210 Closed St")
    ok(client.post("/wholesale/deals/%s/close" % prop["deal"]["id"],
                   headers=auth_headers,
                   json={"wholesale_fee_collected": 5000}))
    status = ok(client.get("/wholesale/deals/%s/cadence" % prop["deal"]["id"],
                           headers=auth_headers))
    assert status["can_start"] is False
    assert any("Closed" in b for b in status["blockers"])


# ── The automatic stops ─────────────────────────────────────────────────────

def test_an_opt_out_stops_a_running_cadence(client, auth_headers, db_session):
    """Not just the next message — the whole schedule."""
    prop = make_deal(client, auth_headers, address="211 Optout St")
    deal_id = prop["deal"]["id"]
    ok(client.post("/wholesale/deals/%s/cadence" % deal_id,
                   headers=auth_headers, json={"action": "start"}))

    ok(client.post("/wholesale/deals/%s/seller-reply" % deal_id,
                   headers=auth_headers,
                   json={"message": "stop texting me"}))

    status = ok(client.get("/wholesale/deals/%s/cadence" % deal_id,
                           headers=auth_headers))
    assert status["state"] == "stopped_dnc"


def test_closing_the_deal_stops_the_cadence(client, auth_headers):
    prop = make_deal(client, auth_headers, address="212 Closing St")
    deal_id = prop["deal"]["id"]
    ok(client.post("/wholesale/deals/%s/cadence" % deal_id,
                   headers=auth_headers, json={"action": "start"}))
    ok(client.post("/wholesale/deals/%s/close" % deal_id, headers=auth_headers,
                   json={"wholesale_fee_collected": 9000}))
    status = ok(client.get("/wholesale/deals/%s/cadence" % deal_id,
                           headers=auth_headers))
    assert status["state"] == "stopped_deal_closed"


def test_marking_the_deal_dead_stops_the_cadence(client, auth_headers):
    prop = make_deal(client, auth_headers, address="213 Dead St")
    deal_id = prop["deal"]["id"]
    ok(client.post("/wholesale/deals/%s/cadence" % deal_id,
                   headers=auth_headers, json={"action": "start"}))
    ok(client.post("/wholesale/deals/%s/stage" % deal_id, headers=auth_headers,
                   json={"stage": "dead", "note": "seller went quiet"}))
    status = ok(client.get("/wholesale/deals/%s/cadence" % deal_id,
                           headers=auth_headers))
    assert status["state"] == "stopped_deal_dead"


def test_a_not_interested_reply_stops_the_cadence_through_the_dead_stage(
        client, auth_headers):
    prop = make_deal(client, auth_headers, address="214 NoThanks St")
    deal_id = prop["deal"]["id"]
    ok(client.post("/wholesale/deals/%s/cadence" % deal_id,
                   headers=auth_headers, json={"action": "start"}))
    ok(client.post("/wholesale/deals/%s/seller-reply" % deal_id,
                   headers=auth_headers,
                   json={"message": "Not interested, I'm not selling."}))
    status = ok(client.get("/wholesale/deals/%s/cadence" % deal_id,
                           headers=auth_headers))
    assert status["state"] == "stopped_deal_dead"


# ── Audit and isolation ─────────────────────────────────────────────────────

def test_every_cadence_action_is_audited(client, auth_headers):
    prop = make_deal(client, auth_headers, address="215 Audit St")
    deal_id = prop["deal"]["id"]
    for action in ("start", "pause", "resume", "stop"):
        ok(client.post("/wholesale/deals/%s/cadence" % deal_id,
                       headers=auth_headers, json={"action": action}))
    events = ok(client.get("/wholesale/events", headers=auth_headers,
                           params={"deal_id": deal_id}))["events"]
    actions = {e["action"] for e in events}
    for action in ("start", "pause", "resume", "stop"):
        assert "cadence.%s" % action in actions


def test_an_automatic_stop_is_audited_as_an_automation_not_a_person(
        client, auth_headers):
    prop = make_deal(client, auth_headers, address="216 AutoAudit St")
    deal_id = prop["deal"]["id"]
    ok(client.post("/wholesale/deals/%s/cadence" % deal_id,
                   headers=auth_headers, json={"action": "start"}))
    ok(client.post("/wholesale/deals/%s/close" % deal_id, headers=auth_headers,
                   json={"wholesale_fee_collected": 1000}))
    events = ok(client.get("/wholesale/events", headers=auth_headers,
                           params={"deal_id": deal_id}))["events"]
    auto = [e for e in events if e["action"] == "cadence.auto_stopped"]
    assert auto and auto[0]["actor_type"] == "automation"


def test_the_deal_room_carries_the_cadence_so_the_screen_cannot_guess(
        client, auth_headers):
    prop = make_deal(client, auth_headers, address="217 Room St")
    room = ok(client.get("/wholesale/deals/%s" % prop["deal"]["id"],
                         headers=auth_headers))
    assert "cadence" in room
    assert room["cadence"]["state"] == "not_started"
    assert "disposition_channels" in room


def test_the_deal_room_reports_whether_a_document_file_can_be_held(
        client, auth_headers):
    """The documents tab asks for a file name. It must also be able to say
    whether the FILE could live here, rather than letting somebody assume it
    does. The answer comes from the platform's own `mobile_storage` capability
    — no second storage layer — and in the test environment it is off."""
    prop = make_deal(client, auth_headers, address="218 Docs St")
    room = ok(client.get("/wholesale/deals/%s" % prop["deal"]["id"],
                         headers=auth_headers))
    storage = room["document_storage"]
    assert storage["uploads_enabled"] is False
    assert storage["reason"]                      # in plain words, not a code
    assert storage["env"] == "MEDIA_STORAGE_BACKEND"
