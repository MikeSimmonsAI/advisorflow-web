"""EvoSys Pro team app (Eagle Says mobile prototype): Chat and Time.

Synthetic people only:
  owner      god_admin                     -> the platform: every right
  partner    brand executive, no grants    -> nothing until granted
  chatmgr    team_chat_manage grant        -> room membership, no DMs, no Team Time
  timemgr    team_time_manage grant + scope over rep1 only
  rep1/rep2  sales reps on the brand team  -> own Chat and Time
  outsider   customer advisor              -> nothing
"""
from datetime import datetime, timedelta

import pytest

from app.models.models import Platform, User, UserCapabilityGrant
from app.models.sales_models import (BrandSalesOrg, Membership, ROLE_BRAND_EXECUTIVE,
                                     SCOPE_BRAND_SALES_ORG, SCOPE_PLATFORM)
from app.models.team_app_models import TeamTimeEntry, TeamTimeScope
from app.services.auth_service import create_access_token, hash_password

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _user(db, email, name, role="advisor", platform_id=None, org_id=None):
    u = User(email=email, full_name=name, role=role, password_hash=hash_password("TestPass123!"),
             must_change_password=False, platform_id=platform_id, organization_id=org_id)
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def team(db_session, sample_org):
    db = db_session
    p = Platform(name="EvoSys Pro", slug="evosyspro-test")
    db.add(p)
    db.commit()
    bso = BrandSalesOrg(platform_id=p.id, name="EvoSys Pro Sales", slug="evosys-sales-test")
    db.add(bso)
    db.commit()
    people = {
        "owner": _user(db, "owner@example.test", "Owner Person", role="god_admin"),
        "partner": _user(db, "partner@example.test", "Partner Person"),
        "chatmgr": _user(db, "chatmgr@example.test", "Chat Manager"),
        "timemgr": _user(db, "timemgr@example.test", "Time Manager"),
        "rep1": _user(db, "rep1@example.test", "Rep One"),
        "rep2": _user(db, "rep2@example.test", "Rep Two"),
        "outsider": _user(db, "outsider@example.test", "Out Sider", org_id=sample_org.id),
    }
    for k in ("rep1", "rep2"):
        db.add(Membership(user_id=people[k].id, scope_type=SCOPE_BRAND_SALES_ORG,
                          scope_id=bso.id, role="sales_rep", is_active=True))
    db.add(Membership(user_id=people["partner"].id, scope_type=SCOPE_PLATFORM, scope_id=p.id,
                      role=ROLE_BRAND_EXECUTIVE, is_active=True))
    for who, keys in (("chatmgr", ["team_chat_manage"]), ("timemgr", ["team_time_manage", "team_chat"])):
        for k in keys:
            db.add(UserCapabilityGrant(user_id=people[who].id, scope_type=SCOPE_PLATFORM,
                                       scope_id=p.id, organization_id=None, capability=k,
                                       is_active=True))
    db.add(TeamTimeScope(platform_id=p.id, manager_user_id=people["timemgr"].id,
                         member_user_id=people["rep1"].id))
    db.commit()
    headers = {k: {"Authorization": "Bearer " + create_access_token(u, db)} for k, u in people.items()}
    return {"db": db, "platform": p, "people": people, "h": headers}


def _rooms(client, h):
    r = client.get("/team-app/chat/rooms", headers=h)
    assert r.status_code == 200, r.text
    return {x["slug"] or x["name"]: x for x in r.json()["rooms"]}


# ── Access ──────────────────────────────────────────────────────────────────

def test_access_flags_per_person(client, team):
    h = team["h"]
    a = {k: client.get("/team-app/me", headers=h[k]).json()["access"] for k in h}
    assert a["owner"]["is_platform"] and a["owner"]["chat"] and a["owner"]["time_manage"]
    assert a["rep1"]["chat"] and a["rep1"]["time"] and not a["rep1"]["time_manage"]
    assert not a["rep1"]["announce"] and not a["rep1"]["chat_manage"]
    assert not a["partner"]["chat"] and not a["partner"]["time"] and not a["partner"]["announce"]
    assert a["chatmgr"]["chat_manage"] and a["chatmgr"]["chat"] and not a["chatmgr"]["time_manage"]
    assert a["timemgr"]["time_manage"] and not a["timemgr"]["chat_manage"]
    assert not a["outsider"]["chat"] and not a["outsider"]["time"]


def test_unauthorized_and_unauthenticated_are_refused(client, team):
    h = team["h"]
    for path in ("/team-app/chat/rooms", "/team-app/time/status", "/team-app/time/mine"):
        assert client.get(path, headers=h["outsider"]).status_code == 403
        assert client.get(path).status_code == 401
    assert client.post("/team-app/time/clock-in", headers=h["outsider"]).status_code == 403
    assert client.get("/team-app/time/team", headers=h["rep1"]).status_code == 403
    assert client.get("/team-app/admin/people", headers=h["timemgr"]).status_code == 403


def test_responses_are_not_cacheable(client, team):
    r = client.get("/team-app/chat/rooms", headers=team["h"]["rep1"])
    assert "no-store" in r.headers["cache-control"]


# ── Chat ────────────────────────────────────────────────────────────────────

def test_builtin_rooms_and_announcement_rights(client, team):
    h = team["h"]
    rooms = _rooms(client, h["rep1"])
    assert "sales-team" in rooms and "announcements" in rooms
    ann = rooms["announcements"]["id"]
    r = client.post("/team-app/chat/rooms/%s/messages" % ann, headers=h["rep1"], json={"body": "hi"})
    assert r.status_code == 403
    r = client.post("/team-app/chat/rooms/%s/messages" % ann, headers=h["owner"],
                    json={"body": "Reminder: sales kickoff is Fri."})
    assert r.status_code == 200
    assert _rooms(client, h["rep2"])["announcements"]["unread"] == 1


def test_announce_right_is_separately_grantable(client, team):
    h, pid = team["h"], team["platform"].id
    partner = team["people"]["partner"]
    r = client.put("/team-app/admin/people/%s/grants" % partner.id, headers=h["owner"],
                   json={"keys": ["team_announce"]})
    assert r.status_code == 200, r.text
    a = client.get("/team-app/me", headers=h["partner"]).json()["access"]
    assert a["announce"] and a["chat"] and not a["chat_manage"] and not a["time_manage"]
    ann = _rooms(client, h["partner"])["announcements"]["id"]
    assert client.post("/team-app/chat/rooms/%s/messages" % ann, headers=h["partner"],
                       json={"body": "Posted by partner"}).status_code == 200


def test_grant_change_keeps_other_platform_grants(client, team):
    db, pid = team["db"], team["platform"].id
    partner = team["people"]["partner"]
    db.add(UserCapabilityGrant(user_id=partner.id, scope_type=SCOPE_PLATFORM, scope_id=pid,
                               organization_id=None, capability="demo_suite", is_active=True))
    db.commit()
    client.put("/team-app/admin/people/%s/grants" % partner.id, headers=team["h"]["owner"],
               json={"keys": ["team_time"]})
    from app.services import capabilities
    assert set(capabilities.platform_grants_for(db, partner.id, pid)) == {"demo_suite", "team_time"}


def test_dm_is_private_even_from_chat_manager_and_platform(client, team):
    h, ppl = team["h"], team["people"]
    dm = client.post("/team-app/chat/dm", headers=h["rep1"], json={"user_id": ppl["rep2"].id}).json()
    assert dm["name"] == "Rep Two"
    again = client.post("/team-app/chat/dm", headers=h["rep2"], json={"user_id": ppl["rep1"].id}).json()
    assert again["id"] == dm["id"]
    client.post("/team-app/chat/rooms/%s/messages" % dm["id"], headers=h["rep1"], json={"body": "secret"})
    for who in ("chatmgr", "owner", "timemgr"):
        assert client.get("/team-app/chat/rooms/%s/messages" % dm["id"], headers=h[who]).status_code == 404
        assert client.get("/team-app/chat/rooms/%s/members" % dm["id"], headers=h[who]).status_code in (403, 404)
    assert dm["id"] not in {r["id"] for r in client.get("/team-app/chat/rooms", headers=h["chatmgr"]).json()["rooms"]}
    msgs = client.get("/team-app/chat/rooms/%s/messages" % dm["id"], headers=h["rep2"]).json()["messages"]
    assert [m["body"] for m in msgs] == ["secret"]


def test_two_users_exchange_and_unread_updates(client, team):
    h = team["h"]
    room = _rooms(client, h["rep1"])["sales-team"]["id"]
    _rooms(client, h["rep2"])
    client.post("/team-app/chat/rooms/%s/read" % room, headers=h["rep2"])
    first = client.post("/team-app/chat/rooms/%s/messages" % room, headers=h["rep1"],
                        json={"body": "Here's the updated sales script.", "client_key": "k1"}).json()
    assert client.get("/team-app/chat/unread", headers=h["rep2"]).json()["unread_total"] == 1
    # Polling with ?after returns only what is new.
    page = client.get("/team-app/chat/rooms/%s/messages" % room, headers=h["rep2"],
                      params={"after": first["created_at"]}).json()["messages"]
    assert page == []
    reply = client.post("/team-app/chat/rooms/%s/messages" % room, headers=h["rep2"],
                        json={"body": "Got it. I'll share it with the team.", "reply_to_id": first["id"],
                              "mentions": [team["people"]["rep1"].id, team["people"]["outsider"].id]}).json()
    assert reply["reply_to"]["id"] == first["id"]
    assert reply["mentions"] == [team["people"]["rep1"].id]     # outsider dropped
    assert client.get("/team-app/chat/unread", headers=h["rep2"]).json()["unread_total"] == 0
    new = client.get("/team-app/chat/rooms/%s/messages" % room, headers=h["rep1"],
                     params={"after": first["created_at"]}).json()["messages"]
    assert [m["id"] for m in new] == [reply["id"]] and new[0]["mentions_me"]


def test_send_retry_is_not_duplicated(client, team):
    h = team["h"]
    room = _rooms(client, h["rep1"])["sales-team"]["id"]
    a = client.post("/team-app/chat/rooms/%s/messages" % room, headers=h["rep1"],
                    json={"body": "once", "client_key": "same"}).json()
    b = client.post("/team-app/chat/rooms/%s/messages" % room, headers=h["rep1"],
                    json={"body": "once", "client_key": "same"}).json()
    assert a["id"] == b["id"]
    msgs = client.get("/team-app/chat/rooms/%s/messages" % room, headers=h["rep1"]).json()["messages"]
    assert sum(1 for m in msgs if m["body"] == "once") == 1


def test_private_file_upload_and_download(client, team, monkeypatch):
    monkeypatch.delenv("MEDIA_STORAGE_BACKEND", raising=False)
    h, ppl = team["h"], team["people"]
    dm = client.post("/team-app/chat/dm", headers=h["rep1"], json={"user_id": ppl["rep2"].id}).json()
    up = client.post("/team-app/chat/rooms/%s/attachments" % dm["id"], headers=h["rep1"],
                     files={"file": ("Sales Script.pdf", PDF, "application/pdf")})
    assert up.status_code == 200, up.text
    att = up.json()
    assert "storage_key" not in att
    # Not yet sent: only the uploader can fetch it.
    assert client.get(att["download_path"], headers=h["rep2"]).status_code == 404
    client.post("/team-app/chat/rooms/%s/messages" % dm["id"], headers=h["rep1"],
                json={"body": "", "attachment_ids": [att["id"]]})
    got = client.get(att["download_path"], headers=h["rep2"])
    assert got.status_code == 200 and got.content == PDF
    assert "no-store" in got.headers["cache-control"]
    for who in ("chatmgr", "owner"):
        assert client.get(att["download_path"], headers=h[who]).status_code == 404
    assert client.get(att["download_path"], headers=h["outsider"]).status_code == 403


def test_drive_links_only(client, team):
    h = team["h"]
    room = _rooms(client, h["rep1"])["sales-team"]["id"]
    bad = client.post("/team-app/chat/rooms/%s/messages" % room, headers=h["rep1"],
                      json={"drive_url": "https://evil.example.com/x"})
    assert bad.status_code == 400
    ok = client.post("/team-app/chat/rooms/%s/messages" % room, headers=h["rep1"],
                     json={"drive_url": "https://docs.google.com/document/d/abc/edit"}).json()
    assert ok["attachments"][0]["kind"] == "drive_link" and ok["attachments"][0]["filename"] == "Google Doc"


def test_chat_manager_manages_room_membership(client, team):
    h, ppl = team["h"], team["people"]
    room = _rooms(client, h["rep1"])["sales-team"]["id"]
    assert client.get("/team-app/chat/rooms/%s/members" % room, headers=h["rep1"]).status_code == 403
    r = client.delete("/team-app/chat/rooms/%s/members/%s" % (room, ppl["rep2"].id), headers=h["chatmgr"])
    assert r.status_code == 200
    assert "sales-team" not in _rooms(client, h["rep2"])       # removed, and not re-enrolled
    assert client.get("/team-app/chat/rooms/%s/messages" % room, headers=h["rep2"]).status_code == 404
    client.post("/team-app/chat/rooms/%s/members" % room, headers=h["chatmgr"], json={"user_id": ppl["rep2"].id})
    assert "sales-team" in _rooms(client, h["rep2"])
    # Chat management alone opens no Team Time.
    assert client.get("/team-app/time/team", headers=h["chatmgr"]).status_code == 403


# ── Time ────────────────────────────────────────────────────────────────────

def test_clock_in_out_is_idempotent(client, team):
    h = team["h"]
    a = client.post("/team-app/time/clock-in", headers=h["rep1"]).json()
    b = client.post("/team-app/time/clock-in", headers=h["rep1"]).json()
    assert a["clocked_in"] and not a["already"] and b["already"]
    assert a["entry"]["id"] == b["entry"]["id"]
    c = client.post("/team-app/time/clock-out", headers=h["rep1"]).json()
    d = client.post("/team-app/time/clock-out", headers=h["rep1"]).json()
    assert not c["clocked_in"] and not c["already"] and d["already"]
    assert team["db"].query(TeamTimeEntry).filter(TeamTimeEntry.user_id == team["people"]["rep1"].id).count() == 1


def test_database_refuses_a_second_open_shift(team):
    """Two simultaneous Clock In requests: the second insert loses in the DB."""
    from sqlalchemy.exc import IntegrityError
    db, p, rep = team["db"], team["platform"], team["people"]["rep1"]
    db.add(TeamTimeEntry(platform_id=p.id, user_id=rep.id, clock_in_at=datetime.utcnow(), open_marker=rep.id))
    db.commit()
    db.add(TeamTimeEntry(platform_id=p.id, user_id=rep.id, clock_in_at=datetime.utcnow(), open_marker=rep.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    from app.services import team_time
    out = team_time.clock_in(db, rep, p.id)
    assert out["already"]


def _entry(db, p, u, start, end):
    e = TeamTimeEntry(platform_id=p.id, user_id=u.id, clock_in_at=start, clock_out_at=end)
    db.add(e)
    db.commit()
    return e


def test_weekly_total_overnight_and_daylight_saving(client, team):
    """US clocks fell back at 2:00 AM Sunday 2026-11-01 (America/Chicago)."""
    db, p, rep = team["db"], team["platform"], team["people"]["rep1"]
    # Sat 10:00 PM CDT -> Sun 6:00 AM CST = 9 real hours, across the DST change.
    _entry(db, p, rep, datetime(2026, 11, 1, 3, 0), datetime(2026, 11, 1, 12, 0))
    # Sun 10:00 PM CST -> Mon 2:00 AM CST: 2h this week, 2h next week.
    _entry(db, p, rep, datetime(2026, 11, 2, 4, 0), datetime(2026, 11, 2, 8, 0))
    wk = client.get("/team-app/time/mine", headers=team["h"]["rep1"], params={"week_of": "2026-10-28"}).json()
    assert wk["time_zone"] == "America/Chicago"
    assert wk["week_start"] == "2026-10-26"
    assert wk["total_seconds"] == 11 * 3600
    days = {d["date"]: d["seconds"] for d in wk["days"]}
    assert days["2026-10-31"] == 2 * 3600          # 10 PM - midnight CDT
    assert days["2026-11-01"] == 9 * 3600          # 7h of the first shift + 2h of the second
    nxt = client.get("/team-app/time/mine", headers=team["h"]["rep1"], params={"week_of": "2026-11-02"}).json()
    assert nxt["total_seconds"] == 2 * 3600


def test_team_time_is_scoped_to_assigned_people(client, team):
    db, p, ppl, h = team["db"], team["platform"], team["people"], team["h"]
    start = datetime.utcnow() - timedelta(hours=5)
    e1 = _entry(db, p, ppl["rep1"], start, start + timedelta(hours=2))
    e2 = _entry(db, p, ppl["rep2"], start, start + timedelta(hours=3))
    mine = client.get("/team-app/time/team", headers=h["timemgr"]).json()
    assert {x["user_id"] for x in mine["people"]} == {ppl["rep1"].id}
    assert {x["id"] for x in mine["entries"]} == {e1.id}
    assert client.get("/team-app/time/team", headers=h["timemgr"],
                      params={"user_id": ppl["rep2"].id}).status_code == 403
    assert client.patch("/team-app/time/entries/%s" % e2.id, headers=h["timemgr"],
                        json={"reason": "fix", "clock_out_at": None}).status_code == 404
    everyone = client.get("/team-app/time/team", headers=h["owner"]).json()
    assert {ppl["rep1"].id, ppl["rep2"].id} <= {x["user_id"] for x in everyone["people"]}


def test_correction_needs_reason_and_is_recorded(client, team):
    db, p, ppl, h = team["db"], team["platform"], team["people"], team["h"]
    start = datetime(2026, 11, 10, 14, 0)
    e = _entry(db, p, ppl["rep1"], start, start + timedelta(hours=8))
    url = "/team-app/time/entries/%s" % e.id
    assert client.patch(url, headers=h["timemgr"], json={"reason": "", "clock_out_at": "2026-11-10T15:00"}).status_code == 400
    assert client.patch(url, headers=h["rep1"], json={"reason": "mine", "clock_out_at": "2026-11-10T15:00"}).status_code == 403
    r = client.patch(url, headers=h["timemgr"],
                     json={"reason": "Forgot to clock out at lunch", "clock_out_at": "2026-11-10T12:00"})
    assert r.status_code == 200, r.text
    assert r.json()["entry"]["clock_out_at"] == "2026-11-10T18:00:00Z"   # noon CST = 18:00 UTC
    hist = r.json()["history"][0]
    assert hist["reason"] == "Forgot to clock out at lunch"
    assert hist["changed_by"] == ppl["timemgr"].id
    assert hist["before"]["clock_out_at"] == "2026-11-10T22:00:00Z"
    # The rep sees the history of their own entry.
    own = client.get(url + "/history", headers=h["rep1"])
    assert own.status_code == 200 and len(own.json()["history"]) == 1
    assert client.get(url + "/history", headers=h["rep2"]).status_code == 403


def test_correction_rejects_overlap_and_future(client, team):
    db, p, ppl, h = team["db"], team["platform"], team["people"], team["h"]
    a = _entry(db, p, ppl["rep1"], datetime(2026, 11, 9, 14, 0), datetime(2026, 11, 9, 18, 0))
    _entry(db, p, ppl["rep1"], datetime(2026, 11, 9, 19, 0), datetime(2026, 11, 9, 22, 0))
    r = client.patch("/team-app/time/entries/%s" % a.id, headers=h["owner"],
                     json={"reason": "extend", "clock_out_at": "2026-11-09T20:00Z"})
    assert r.status_code == 409
    r = client.patch("/team-app/time/entries/%s" % a.id, headers=h["owner"],
                     json={"reason": "future", "clock_out_at": "2099-01-01T00:00Z"})
    assert r.status_code == 400
