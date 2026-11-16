"""EvoSys Pro Mobile: Chat and Activity (timekeeping was removed from the release).

Synthetic people only:
  owner      god_admin                     -> the platform: every right
  partner    brand executive, no grants    -> nothing until granted
  chatmgr    team_chat_manage grant        -> room membership, no DMs
  timemgr    team_chat grant only          -> Chat, nothing else
  rep1/rep2  sales reps on the brand team  -> Chat
  outsider   customer advisor              -> nothing
"""
from datetime import datetime, timedelta

import pytest

from app.models.models import Platform, User, UserCapabilityGrant
from app.models.sales_models import (BrandSalesOrg, Membership, ROLE_BRAND_EXECUTIVE,
                                     SCOPE_BRAND_SALES_ORG, SCOPE_PLATFORM)
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
    for who, keys in (("chatmgr", ["team_chat_manage"]), ("timemgr", ["team_chat"])):
        for k in keys:
            db.add(UserCapabilityGrant(user_id=people[who].id, scope_type=SCOPE_PLATFORM,
                                       scope_id=p.id, organization_id=None, capability=k,
                                       is_active=True))
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
    assert a["owner"]["is_platform"] and a["owner"]["chat"] and a["owner"]["announce"]
    assert a["rep1"]["chat"] and a["rep1"]["activity"]
    assert not a["rep1"]["announce"] and not a["rep1"]["chat_manage"]
    assert not a["partner"]["chat"] and not a["partner"]["announce"]
    assert a["chatmgr"]["chat_manage"] and a["chatmgr"]["chat"] and not a["chatmgr"]["announce"]
    assert a["timemgr"]["chat"] and not a["timemgr"]["chat_manage"]
    assert not a["outsider"]["chat"] and a["outsider"]["activity"]


def test_unauthorized_and_unauthenticated_are_refused(client, team):
    h = team["h"]
    for path in ("/team-app/chat/rooms", "/team-app/chat/people", "/team-app/chat/unread"):
        assert client.get(path, headers=h["outsider"]).status_code == 403
        assert client.get(path).status_code == 401
    assert client.get("/team-app/activity/updates").status_code == 401
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
    assert a["announce"] and a["chat"] and not a["chat_manage"]
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
               json={"keys": ["team_chat", "team_time"]})   # an unknown key is ignored
    from app.services import capabilities
    assert set(capabilities.platform_grants_for(db, partner.id, pid)) == {"demo_suite", "team_chat"}


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
    # Chat management alone opens no announcements and no access screen.
    assert client.get("/team-app/admin/people", headers=h["chatmgr"]).status_code == 403


# ── Time is gone ────────────────────────────────────────────────────────────

def test_timekeeping_is_not_part_of_the_release(client, team):
    h = team["h"]["owner"]
    for path in ("/team-app/time/status", "/team-app/time/mine", "/team-app/time/team"):
        assert client.get(path, headers=h).status_code == 404
    assert client.post("/team-app/time/clock-in", headers=h).status_code == 404
    a = client.get("/team-app/me", headers=h).json()["access"]
    assert "time" not in a and "time_manage" not in a and a["activity"] is True
    from app.services import capabilities
    assert "team_time" not in capabilities.CAPABILITIES and "team_time_manage" not in capabilities.CAPABILITIES


# ── Activity ────────────────────────────────────────────────────────────────

def _booking(db, org_id, advisor_id, when_local, label="File Review", status="booked", **kw):
    from app.models.models import BookingLink, Lead
    lead = Lead(organization_id=org_id, first_name="Synthetic", last_name="Family", is_test=kw.pop("is_test", False))
    db.add(lead)
    db.flush()
    b = BookingLink(lead_id=lead.id, user_id=advisor_id, status=status, booked_time=when_local,
                    appt_label=label, appt_duration=60, **kw)
    db.add(b)
    db.commit()
    return b


@pytest.fixture()
def activity(team, sample_org):
    """rep1 works in sample_org; a second org nobody here belongs to."""
    from app.models.models import Organization
    from app.models.sales_models import SCOPE_CUSTOMER_ORG
    db, ppl = team["db"], team["people"]
    other = Organization(name="Private Other Co", slug="private-other-co", plan="standard", industry="generic")
    db.add(other)
    db.commit()
    db.add(Membership(user_id=ppl["rep1"].id, scope_type=SCOPE_CUSTOMER_ORG, scope_id=sample_org.id,
                      role="advisor", is_active=True))
    db.commit()
    from app.services import workspace_access
    workspace_access.invalidate_workspace_memberships(ppl["rep1"])
    soon = datetime.utcnow().replace(microsecond=0) + timedelta(days=1)
    mine = _booking(db, sample_org.id, ppl["rep1"].id, soon, label="File Review")
    hidden = _booking(db, other.id, ppl["rep1"].id, soon, label="Secret Visit")
    test_lead = _booking(db, sample_org.id, ppl["rep1"].id, soon, label="Test Lead Visit", is_test=True)
    return {"org": sample_org, "other": other, "mine": mine, "hidden": hidden, "when": soon, "test": test_lead}


def test_updates_show_only_workspaces_this_login_may_open(client, team, activity):
    h = team["h"]
    r = client.get("/team-app/activity/updates", headers=h["rep1"])
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    body = r.json()
    titles = [i["title"] for i in body["items"] if i["kind"] == "booking"]
    assert "File Review" in titles
    assert "Secret Visit" not in titles and "Test Lead Visit" not in titles
    item = [i for i in body["items"] if i.get("title") == "File Review"][0]
    assert item["organization_id"] == activity["org"].id and item["text"] == "Appointment booked."
    for leak in ("Synthetic", "Family", "phone", "email"):
        assert leak not in str(item)
    assert {s["key"] for s in body["sources"]} == {"workspace_bookings", "sales_appointments", "chat_mentions"}
    # Someone with no workspace sees no booking at all.
    other = client.get("/team-app/activity/updates", headers=h["partner"]).json()
    assert not [i for i in other["items"] if i["kind"] == "booking"]


def test_updates_follow_access_changes(client, team, activity):
    from app.services import workspace_access
    db, rep1 = team["db"], team["people"]["rep1"]
    m = db.query(Membership).filter(Membership.user_id == rep1.id, Membership.scope_id == activity["org"].id).first()
    m.is_active = False
    db.commit()
    workspace_access.invalidate_workspace_memberships(rep1)
    body = client.get("/team-app/activity/updates", headers=team["h"]["rep1"]).json()
    assert not [i for i in body["items"] if i["kind"] == "booking"]


def test_cancel_and_reschedule_show_as_updates(client, team, activity):
    db = team["db"]
    b = activity["mine"]
    b.status = "cancelled"
    b.cancelled_at = datetime.utcnow()
    db.commit()
    items = client.get("/team-app/activity/updates", headers=team["h"]["rep1"]).json()["items"]
    assert any(i["kind"] == "booking" and i["change"] == "cancelled" and i["text"] == "Appointment cancelled."
               for i in items)


def test_upcoming_lists_the_days_appointments_in_local_time(client, team, activity):
    day = activity["when"].date().isoformat()
    body = client.get("/team-app/activity/upcoming", headers=team["h"]["rep1"], params={"date": day}).json()
    assert body["date"] == day and body["time_zone"] == "America/Chicago"
    titles = [i["title"] for i in body["items"]]
    assert titles == ["File Review"]
    assert body["items"][0]["starts_at"].startswith(day)
    empty = client.get("/team-app/activity/upcoming", headers=team["h"]["rep1"],
                       params={"date": (activity["when"].date() + timedelta(days=3)).isoformat()}).json()
    assert empty["items"] == []
    assert client.get("/team-app/activity/upcoming", headers=team["h"]["rep1"],
                      params={"date": "nope"}).status_code == 400
    assert client.get("/team-app/activity/upcoming").status_code == 401


def test_mentions_appear_only_while_the_room_is_readable(client, team):
    h, ppl = team["h"], team["people"]
    room = _rooms(client, h["rep1"])["sales-team"]["id"]
    client.post("/team-app/chat/rooms/%s/messages" % room, headers=h["rep1"],
                json={"body": "@Rep Two please look", "mentions": [ppl["rep2"].id]})
    items = client.get("/team-app/activity/updates", headers=h["rep2"]).json()["items"]
    mention = [i for i in items if i["kind"] == "mention"]
    assert mention and mention[0]["room_id"] == room and mention[0]["headline"] == "Sales Team"
    assert client.get("/team-app/activity/updates", headers=h["rep1"]).json()["items"] == [] or \
        not [i for i in client.get("/team-app/activity/updates", headers=h["rep1"]).json()["items"] if i["kind"] == "mention"]
    client.delete("/team-app/chat/rooms/%s/members/%s" % (room, ppl["rep2"].id), headers=h["chatmgr"])
    items = client.get("/team-app/activity/updates", headers=h["rep2"]).json()["items"]
    assert not [i for i in items if i["kind"] == "mention"]


def test_sales_appointments_for_participants_only(client, team):
    from app.models.scheduling_models import AppointmentParticipant, SalesAppointment
    from app.models.sales_models import BrandSalesOrg
    db, ppl = team["db"], team["people"]
    bso = db.query(BrandSalesOrg).first()
    start = datetime.utcnow().replace(microsecond=0) + timedelta(days=2)
    a = SalesAppointment(brand_sales_org_id=bso.id, title="Demo with Synthetic Co", starts_at=start,
                         ends_at=start + timedelta(minutes=45))
    db.add(a)
    db.flush()
    db.add(AppointmentParticipant(appointment_id=a.id, user_id=ppl["rep1"].id, busy_start_at=start,
                                  busy_end_at=start + timedelta(minutes=45)))
    db.commit()
    up = client.get("/team-app/activity/updates", headers=team["h"]["rep1"]).json()["items"]
    assert any(i["kind"] == "sales_appointment" and i["title"] == "Demo with Synthetic Co" for i in up)
    other = client.get("/team-app/activity/updates", headers=team["h"]["rep2"]).json()["items"]
    assert not [i for i in other if i["kind"] == "sales_appointment"]
    from zoneinfo import ZoneInfo
    from datetime import timezone as _tz
    local_day = start.replace(tzinfo=_tz.utc).astimezone(ZoneInfo("America/Chicago")).date().isoformat()
    cal = client.get("/team-app/activity/upcoming", headers=team["h"]["rep1"], params={"date": local_day}).json()
    assert [i["minutes"] for i in cal["items"] if i["kind"] == "sales_appointment"] == [45]


def test_one_failing_source_does_not_hide_the_others(client, team, monkeypatch):
    from app.services import team_activity

    def boom(*a, **k):
        raise RuntimeError("source down")
    monkeypatch.setattr(team_activity, "_workspaces", boom)
    h, ppl = team["h"], team["people"]
    room = _rooms(client, h["rep1"])["sales-team"]["id"]
    client.post("/team-app/chat/rooms/%s/messages" % room, headers=h["rep1"],
                json={"body": "@Rep Two hi", "mentions": [ppl["rep2"].id]})
    body = client.get("/team-app/activity/updates", headers=h["rep2"]).json()
    assert {s["key"]: s["available"] for s in body["sources"]}["workspace_bookings"] is False
    assert [i for i in body["items"] if i["kind"] == "mention"]


# ── Programs: phone pins ────────────────────────────────────────────────────

def test_pins_are_per_account_capped_at_two_and_editable(client, team):
    h = team["h"]
    assert client.get("/team-app/pins", headers=h["rep1"]).json() == {"keys": [], "max": 2}
    r = client.put("/team-app/pins", json={"keys": ["workspace:a:", "deal_builder::calc"]}, headers=h["rep1"])
    assert r.status_code == 200 and r.json()["keys"] == ["workspace:a:", "deal_builder::calc"]
    # Order kept, duplicates dropped, a third pin refused and nothing changed.
    assert client.put("/team-app/pins", json={"keys": ["x", "y", "z"]}, headers=h["rep1"]).status_code == 400
    assert client.get("/team-app/pins", headers=h["rep1"]).json()["keys"] == ["workspace:a:", "deal_builder::calc"]
    # Another account sees only its own pins.
    assert client.get("/team-app/pins", headers=h["rep2"]).json()["keys"] == []
    # Change and remove.
    client.put("/team-app/pins", json={"keys": ["deal_builder::calc", "deal_builder::calc"]}, headers=h["rep1"])
    assert client.get("/team-app/pins", headers=h["rep1"]).json()["keys"] == ["deal_builder::calc"]
    client.put("/team-app/pins", json={"keys": []}, headers=h["rep1"])
    assert client.get("/team-app/pins", headers=h["rep1"]).json()["keys"] == []
    assert client.get("/team-app/pins").status_code == 401


def test_phone_pins_do_not_touch_desktop_pins(client, team):
    h = team["h"]["owner"]
    client.put("/launcher/pins", json={"keys": ["a", "b", "c"]}, headers=h)
    client.put("/team-app/pins", json={"keys": ["b"]}, headers=h)
    assert client.get("/launcher/pins", headers=h).json()["keys"] == ["a", "b", "c"]
    client.put("/launcher/pins", json={"keys": ["c"]}, headers=h)
    assert client.get("/team-app/pins", headers=h).json()["keys"] == ["b"]
