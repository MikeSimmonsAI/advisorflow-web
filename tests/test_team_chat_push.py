"""Team Chat -> phone alerts (web push).

A message queues ONE alert per other person in the conversation; the lock
screen line says who and where, never the message; tapping it opens that
conversation in the team app; it reaches the person's phone whichever
workspace the phone was in when alerts were turned on; and the immediate
send and the background pass can never both send the same alert.
"""
import pytest

from app.models.push_models import (EVENT_PENDING, EVENT_SENT, TYPE_TEAM_CHAT, PushEvent,
                                    PushSubscription)
from app.services import web_push_service as wps
from tests.test_team_app import team  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _no_thread(monkeypatch):
    sent = []
    monkeypatch.setattr(wps, "deliver_now_in_background", lambda ids: sent.append(list(ids)))
    return sent


def _events(db, **f):
    q = db.query(PushEvent).filter(PushEvent.type == TYPE_TEAM_CHAT)
    for k, v in f.items():
        q = q.filter(getattr(PushEvent, k) == v)
    return q.all()


def test_dm_queues_one_alert_for_the_other_person(client, team, _no_thread):  # noqa: F811
    h, ppl, db = team["h"], team["people"], team["db"]
    dm = client.post("/team-app/chat/dm", headers=h["rep1"], json={"user_id": ppl["rep2"].id}).json()
    r = client.post("/team-app/chat/rooms/%s/messages" % dm["id"], headers=h["rep1"],
                    json={"body": "the secret plan"})
    assert r.status_code in (200, 201), r.text
    evs = _events(db)
    assert len(evs) == 1
    ev = evs[0]
    assert ev.target_user_id == ppl["rep2"].id
    assert ev.title == "New message from Rep"
    assert "secret" not in ev.title
    assert ev.url == "/app/chat/%s" % dm["id"]
    assert ev.status == EVENT_PENDING
    assert _no_thread == [[ev.id]]
    payload = wps.payload_for(ev)
    assert "secret" not in str(payload)


def test_sender_never_alerted_and_repeat_send_does_not_double(client, team):  # noqa: F811
    h, ppl, db = team["h"], team["people"], team["db"]
    dm = client.post("/team-app/chat/dm", headers=h["rep1"], json={"user_id": ppl["rep2"].id}).json()
    for _ in range(2):
        client.post("/team-app/chat/rooms/%s/messages" % dm["id"], headers=h["rep1"],
                    json={"body": "hi", "client_key": "k-1"})
    assert len(_events(db)) == 1
    assert not _events(db, target_user_id=ppl["rep1"].id)


def test_announcement_alerts_everyone_with_chat_but_the_poster(client, team):  # noqa: F811
    h, ppl, db = team["h"], team["people"], team["db"]
    rooms = client.get("/team-app/chat/rooms", headers=h["owner"]).json()["rooms"]
    ann = next(r for r in rooms if r.get("kind") == "announcements")
    r = client.post("/team-app/chat/rooms/%s/messages" % ann["id"], headers=h["owner"], json={"body": "News"})
    assert r.status_code in (200, 201), r.text
    targets = {e.target_user_id for e in _events(db)}
    assert ppl["owner"].id not in targets
    assert ppl["rep1"].id in targets and ppl["rep2"].id in targets
    assert ppl["outsider"].id not in targets
    assert all(e.title == "New company announcement" for e in _events(db))


def test_team_chat_alert_reaches_phone_registered_in_any_workspace(db_session, sample_org, team):  # noqa: F811
    db = db_session
    rep2 = team["people"]["rep2"]
    db.add(PushSubscription(user_id=rep2.id, organization_id=sample_org.id, endpoint="https://push.example/a",
                            endpoint_hash=wps._hash("https://push.example/a"), p256dh="x" * 20, auth="y" * 10))
    db.commit()
    ev = wps.enqueue(db, target_user_id=rep2.id, organization_id=None, event_type=TYPE_TEAM_CHAT,
                     idempotency_key="t1", title="New message from Rep", url="/app/chat/r1")
    db.commit()
    calls = []
    r = wps.deliver_event(db, ev, sender=lambda *a: calls.append(a))
    assert r["sent"] == 1 and len(calls) == 1
    assert '"/app/chat/r1"' in calls[0][1]


def test_immediate_send_claims_once(db_session, team):  # noqa: F811
    db = db_session
    rep2 = team["people"]["rep2"]
    db.add(PushSubscription(user_id=rep2.id, endpoint="https://push.example/b",
                            endpoint_hash=wps._hash("https://push.example/b"), p256dh="x" * 20, auth="y" * 10))
    ev = wps.enqueue(db, target_user_id=rep2.id, organization_id=None, event_type=TYPE_TEAM_CHAT,
                     idempotency_key="t2", url="/app/chat/r2")
    db.commit()
    calls = []

    class _Keep:   # hand deliver_now the test session without letting it close it
        def __getattr__(self, k):
            return getattr(db, k)

        def close(self):
            pass

    sender = lambda *a: calls.append(a)  # noqa: E731
    assert wps.deliver_now([ev.id], sender=sender, session_factory=_Keep) == 1
    assert wps.deliver_now([ev.id], sender=sender, session_factory=_Keep) == 0
    assert len(calls) == 1
    db.refresh(ev)
    assert ev.status == EVENT_SENT


def test_app_urls_allowed_other_urls_fall_back(db_session, team):  # noqa: F811
    db = db_session
    uid = team["people"]["rep1"].id
    a = wps.enqueue(db, target_user_id=uid, organization_id=None, event_type=TYPE_TEAM_CHAT,
                    idempotency_key="u1", url="/app/chat/x")
    b = wps.enqueue(db, target_user_id=uid, organization_id=None, event_type=TYPE_TEAM_CHAT,
                    idempotency_key="u2", url="https://evil.example/")
    assert a.url == "/app/chat/x" and b.url == wps.DEFAULT_URL
