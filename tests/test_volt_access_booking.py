# -*- coding: utf-8 -*-
"""VOLT #535 (2026-11-06): People & Access and staff-controlled Zoom booking.

Synthetic people only (example.test / example.com). Capacity under real
concurrency is exercised against PostgreSQL by docs/volt/535-recruiting/qa/
booking_race.py; these tests cover the rules.
"""
import json
from datetime import datetime, timedelta

from app.models.models import Organization, User
from app.models.sales_models import Membership
from app.models.volt_models import (
    VoltAccess, VoltApplication, VoltAuditEvent, VoltEmail, VoltSession, VoltSessionAttendee,
)
from tests.test_volt_recruiting import (  # noqa: F401  (fixture import)
    H, PDF, SLUG, _complete, _no_required_docs, _upload, _user, person, volt,
)

JOIN = "https://us02web.zoom.us/j/81234567890?pwd=abc"


def _future(days=7, hour=10):
    d = (datetime.utcnow() + timedelta(days=days)).date()
    return "%sT%02d:00" % (d.isoformat(), hour)


def _submitted(volt, **kw):
    _, tok, out = volt["register"](**kw)
    _complete(volt["c"], tok, out["application_id"])
    return tok, out


def _session(volt, *, kind="interview", project="aca", capacity=1, open_=True, starts=None, **extra):
    c, db = volt["c"], volt["db"]
    body = dict({"kind": kind, "project_id": volt["project"](project).id, "starts_local": starts or _future(),
                 "timezone": "America/Chicago", "duration_minutes": 30, "join_url": JOIN, "capacity": capacity,
                 "booking_open": open_, "title": "Intro call"}, **extra)
    r = c.post("/volt/ops/%s/sessions" % volt["org"].id, headers=H(db, volt["staff"]), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _eligible(volt, app_id, stage="interview"):
    r = volt["c"].post("/volt/ops/%s/applications/%s/scheduling" % (volt["org"].id, app_id),
                       headers=H(volt["db"], volt["staff"]), json={"stage": stage})
    assert r.status_code == 200, r.text
    return r.json()


def _book(volt, tok, app_id, sid):
    return volt["c"].post("/volt/rep/%s/applications/%s/zoom-booking" % (SLUG, app_id), headers=tok,
                          json={"session_id": sid})


def _access(volt, actor, user_id, action, reason=None):
    return volt["c"].post("/volt/ops/%s/people/%s/access" % (volt["org"].id, user_id),
                          headers=H(volt["db"], actor), json={"action": action, "reason": reason})


def _second_admin(volt):
    return _user(volt["db"], "advisor", volt["org"], "org_admin")


# ═══════════════════════════════════════════════════════ PEOPLE & ACCESS ═══

def test_suspend_blocks_existing_and_new_access_and_restore_needs_fresh_session(volt):
    c, db = volt["c"], volt["db"]
    body, tok, out = volt["register"](first="Sus", last="Pended")
    up = _upload(c, tok, out["application_id"])
    assert up.status_code == 200
    doc_id = up.json()["id"]
    uid = db.query(User).filter(User.email == body["email"]).one().id
    r = _access(volt, volt["staff"], uid, "suspend", "Duplicate account")
    assert r.status_code == 200 and r.json()["access"] == "suspended"
    # Existing token: every VOLT request refused, downloads included.
    assert c.get("/volt/rep/%s/me" % SLUG, headers=tok).status_code in (401, 403)
    assert c.get("/volt/rep/%s/documents/%s/file" % (SLUG, doc_id), headers=tok).status_code in (401, 403)
    # New sign-in refused; re-registering the same identity is refused.
    li = c.post("/volt/public/%s/login" % SLUG, json={"email": body["email"], "password": body["password"]})
    assert li.status_code == 403
    assert c.post("/volt/public/%s/register" % SLUG, json=dict(body)).status_code == 409
    # Password reset sends nothing.
    before = db.query(VoltEmail).count()
    c.post("/auth/forgot-password", json={"email": body["email"]})
    assert db.query(VoltEmail).filter(VoltEmail.kind == "password_reset").count() == 0 and db.query(VoltEmail).count() == before
    # Records preserved.
    assert db.get(VoltApplication, out["application_id"]) is not None
    # Restore: the OLD token stays dead; a fresh sign-in works.
    assert _access(volt, volt["staff"], uid, "restore").json()["access"] == "active"
    assert c.get("/volt/rep/%s/me" % SLUG, headers=tok).status_code == 401
    li = c.post("/volt/public/%s/login" % SLUG, json={"email": body["email"], "password": body["password"]})
    assert li.status_code == 200
    fresh = {"Authorization": "Bearer " + li.json()["access_token"]}
    assert c.get("/volt/rep/%s/me" % SLUG, headers=fresh).status_code == 200
    ev = [e for e in db.query(VoltAuditEvent).filter(VoltAuditEvent.entity_type == "access",
                                                     VoltAuditEvent.entity_id == uid).all()]
    assert [e.action for e in ev] == ["access_suspend", "access_restore"]
    assert ev[0].actor_user_id == volt["staff"].id and ev[0].reason == "Duplicate account"


def test_archive_hides_from_queue_but_never_changes_sign_in(volt):
    c, db = volt["c"], volt["db"]
    org = volt["org"].id
    tok, out = _submitted(volt, first="Arch", last="Ived")
    from app.models.volt_models import VoltRep
    uid = db.get(VoltRep, db.get(VoltApplication, out["application_id"]).rep_serial).user_id
    sh = H(db, volt["staff"])
    q = {"include_test": 1, "reached": "submitted"}
    ids = lambda params: [x["application_id"] for x in c.get("/volt/ops/%s/applications" % org, headers=sh, params=params).json()["items"]]
    assert out["application_id"] in ids(q)
    r = c.post("/volt/ops/%s/people/%s/archive" % (org, uid), headers=sh, json={"archived": True, "reason": "Old"})
    assert r.status_code == 200 and r.json()["archived"] is True and r.json()["access"] == "active"
    assert out["application_id"] not in ids(q)
    assert out["application_id"] in ids(dict(q, include_archived=1))
    assert c.get("/volt/rep/%s/me" % SLUG, headers=tok).status_code == 200          # still signed in
    assert c.post("/volt/ops/%s/people/%s/archive" % (org, uid), headers=sh, json={"archived": False}).json()["archived"] is False
    assert out["application_id"] in ids(q)


def test_remove_shared_staff_affects_volt_only_and_restore_puts_the_role_back(volt):
    c, db = volt["c"], volt["db"]
    org = volt["org"]
    shared = _user(db, "advisor", org, "manager")
    other = Organization(name="Other Biz", slug="other-biz-x", plan="enterprise", is_active=True)
    db.add(other)
    db.commit()
    db.add(Membership(user_id=shared.id, scope_type="customer_org", scope_id=other.id, role="org_admin", is_active=True))
    db.commit()
    tok = H(db, shared)
    assert c.get("/volt/ops/%s" % org.id, headers=tok).status_code == 200
    r = _access(volt, volt["staff"], shared.id, "remove", "Left the team")
    assert r.status_code == 200 and r.json()["access"] == "removed"
    assert c.get("/volt/ops/%s" % org.id, headers=tok).status_code in (401, 403)
    assert c.get("/volt/ops/%s" % org.id, headers=H(db, shared)).status_code == 403        # new session too
    db.expire_all()
    mems = {m.scope_id: m for m in db.query(Membership).filter(Membership.user_id == shared.id).all()}
    assert mems[org.id].is_active is False and mems[other.id].is_active is True
    assert db.get(User, shared.id).is_active is True                         # platform login kept
    assert c.get("/auth/sessions", headers=tok).status_code == 200                # other business unaffected
    r = _access(volt, volt["staff"], shared.id, "restore")
    assert r.json()["access"] == "active" and r.json()["staff_role"] == "manager"
    assert c.get("/volt/ops/%s" % org.id, headers=tok).status_code == 401              # old session stays dead
    assert c.get("/volt/ops/%s" % org.id, headers=H(db, shared)).status_code == 200


def test_permission_rules_owner_protection_last_admin_and_self(volt):
    c, db = volt["c"], volt["db"]
    org = volt["org"].id
    admin = volt["staff"]                                   # the only org admin
    assert _access(volt, admin, admin.id, "suspend").status_code == 409                # self-lockout
    assert _access(volt, admin, volt["god"].id, "suspend").status_code == 403          # owner protected
    r = _access(volt, volt["god"], admin.id, "suspend")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "last_admin"
    second = _second_admin(volt)
    assert _access(volt, admin, second.id, "suspend").json()["detail"]["code"] == "admin_needs_owner"
    assert _access(volt, volt["god"], second.id, "suspend").json()["access"] == "suspended"
    assert _access(volt, volt["god"], admin.id, "suspend").json()["detail"]["code"] == "last_admin"
    # Managers and viewers can look, not change; applicants and other tenants can't look.
    mgr = _user(db, "advisor", volt["org"], "manager")
    body, tok, _ = volt["register"](first="App", last="Licant")
    uid = db.query(User).filter(User.email == body["email"]).one().id
    assert _access(volt, mgr, uid, "suspend").status_code == 403
    assert c.get("/volt/ops/%s/people" % org, headers=H(db, mgr)).json()["can_manage"] is False
    assert c.get("/volt/ops/%s/people" % org, headers=tok).status_code == 403
    assert c.get("/volt/ops/%s/people" % org, headers=H(db, volt["other_staff"])).status_code == 403
    assert c.post("/volt/ops/%s/people/%s/access" % (org, uid), headers=tok, json={"action": "suspend"}).status_code == 403
    # Another tenant's person is not reachable through this tenant.
    assert _access(volt, admin, volt["other_staff"].id, "suspend").status_code == 404
    # The owner still sees everything.
    assert c.get("/volt/ops/%s/people" % org, headers=H(db, volt["god"])).json()["actor_level"] == "owner"


def test_people_list_roles_filters_totals_and_history(volt):
    c, db = volt["c"], volt["db"]
    org = volt["org"].id
    body, _, _ = volt["register"](first="Jordan", last="Listed")
    uid = db.query(User).filter(User.email == body["email"]).one().id
    _access(volt, volt["staff"], uid, "suspend", "Checking")
    sh = H(db, volt["staff"])
    p = c.get("/volt/ops/%s/people" % org, headers=sh, params={"include_test": 1}).json()
    row = [x for x in p["people"] if x["user_id"] == uid][0]
    assert row["roles"] == ["Applicant"] and row["access"] == "suspended" and row["applications"] == 1
    assert any(x["user_id"] == volt["staff"].id and "Org admin" in x["roles"] for x in p["people"])
    assert all(x["user_id"] != volt["god"].id for x in p["people"])                   # owner not listed
    assert p["totals"]["suspended"] == 1
    only = c.get("/volt/ops/%s/people" % org, headers=sh, params={"include_test": 1, "access": "suspended", "q": "listed"}).json()
    assert [x["user_id"] for x in only["people"]] == [uid]
    staff_only = c.get("/volt/ops/%s/people" % org, headers=sh, params={"include_test": 1, "role": "staff"}).json()
    assert all(x["staff_role"] for x in staff_only["people"])
    h = c.get("/volt/ops/%s/people/history" % org, headers=sh).json()["items"]
    assert h[0]["action"] == "access_suspend" and h[0]["reason"] == "Checking" and h[0]["scope"] == "VOLT Marketing"


def test_seed_grants_named_existing_admin_once_and_respects_removal(volt, monkeypatch):
    from app.services.volt import seed
    c, db = volt["c"], volt["db"]
    dee = User(email="dee.admin@example.com", full_name="Dee Admin", password_hash="x", role="org_admin",
               is_active=True, must_change_password=False)
    db.add(dee)
    db.commit()
    monkeypatch.setenv("VOLT_ORG_ADMIN_EMAILS", "Dee.Admin@example.com, nobody@example.com")
    assert seed.ensure(db)["admins_granted"] == 1
    db.commit()
    assert seed.ensure(db)["admins_granted"] == 0
    m = db.query(Membership).filter(Membership.user_id == dee.id, Membership.scope_id == volt["org"].id).one()
    assert m.role == "org_admin" and m.is_active
    assert c.get("/volt/ops/%s" % volt["org"].id, headers=H(db, dee)).status_code == 200
    assert db.query(User).filter(User.email == "nobody@example.com").count() == 0      # never creates accounts
    # The owner removes Dee; the next deploy does not quietly put Dee back.
    assert _access(volt, volt["god"], dee.id, "remove").json()["access"] == "removed"
    db.commit()
    assert seed.ensure(db)["admins_granted"] == 0
    db.refresh(m)
    assert m.is_active is False


def test_withdraw_is_distinct_from_access_and_reopens_where_it_was(volt):
    c, db = volt["c"], volt["db"]
    org = volt["org"].id
    tok, out = _submitted(volt)
    sh = H(db, volt["staff"])
    r = c.post("/volt/ops/%s/applications/%s/status" % (org, out["application_id"]), headers=sh,
               json={"status": "withdrawn", "reason": "Applicant asked"})
    assert r.status_code == 200 and r.json()["status"] == "withdrawn"
    me = c.get("/volt/rep/%s/me" % SLUG, headers=tok)
    assert me.status_code == 200 and me.json()["applications"][0]["next_action"]["label"] == "Application closed"
    assert c.post("/volt/ops/%s/applications/%s/status" % (org, out["application_id"]), headers=sh,
                  json={"status": "approved"}).status_code == 409
    f = c.get("/volt/ops/%s/applications/%s" % (org, out["application_id"]), headers=sh).json()
    assert f["next_actions"] == ["submitted"]
    assert c.post("/volt/ops/%s/applications/%s/status" % (org, out["application_id"]), headers=sh,
                  json={"status": "submitted"}).json()["status"] == "submitted"


# ═══════════════════════════════════════════════════════════ ZOOM BOOKING ══

def test_staff_eligibility_controls_scheduling_and_join_link_privacy(volt):
    c, db = volt["c"], volt["db"]
    tok, out = _submitted(volt)
    s = _session(volt)
    _session(volt, kind="orientation")                                  # wrong stage
    _session(volt, project="fiber")                                     # wrong project
    t = c.get("/volt/rep/%s/applications/%s/zoom-times" % (SLUG, out["application_id"]), headers=tok).json()
    assert t["state"]["state"] == "waiting" and t["times"] == []
    r = _book(volt, tok, out["application_id"], s["id"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_eligible"
    _eligible(volt, out["application_id"])
    t = c.get("/volt/rep/%s/applications/%s/zoom-times" % (SLUG, out["application_id"]), headers=tok).json()
    assert t["state"]["state"] == "choose" and [x["id"] for x in t["times"]] == [s["id"]]
    assert "join_url" not in json.dumps(t)
    me = c.get("/volt/rep/%s/me" % SLUG, headers=tok).json()
    assert me["sessions"] == [] and "zoom.us" not in json.dumps(me)
    r = _book(volt, tok, out["application_id"], s["id"])
    assert r.status_code == 200 and r.json()["created"] is True
    assert r.json()["session"]["join_url"] == JOIN
    a = db.get(VoltApplication, out["application_id"])
    assert a.status == "submitted"                                      # booking never closes the application
    # Another eligible applicant never sees this join link.
    tok2, out2 = _submitted(volt, first="Other", last="Person")
    _eligible(volt, out2["application_id"])
    assert "zoom.us" not in json.dumps(c.get("/volt/rep/%s/me" % SLUG, headers=tok2).json())
    assert "zoom.us" not in c.get("/volt/rep/%s/applications/%s/zoom-times" % (SLUG, out2["application_id"]), headers=tok2).text
    # Another applicant cannot book through someone else's application.
    assert _book(volt, tok2, out["application_id"], s["id"]).status_code == 404


def test_capacity_one_duplicates_and_cancel_releases(volt):
    c, db = volt["c"], volt["db"]
    tok, out = _submitted(volt, first="First", last="Booker")
    tok2, out2 = _submitted(volt, first="Second", last="Booker")
    for o in (out, out2):
        _eligible(volt, o["application_id"])
    s = _session(volt, capacity=1)
    s2 = _session(volt, capacity=3, starts=_future(9))
    assert _book(volt, tok, out["application_id"], s["id"]).status_code == 200
    again = _book(volt, tok, out["application_id"], s["id"])                       # repeated click / retry
    assert again.status_code == 200 and again.json()["created"] is False
    assert db.query(VoltSessionAttendee).filter(VoltSessionAttendee.session_id == s["id"]).count() == 1
    assert db.query(VoltEmail).filter(VoltEmail.kind == "zoom_confirmed").count() == 1
    full = _book(volt, tok2, out2["application_id"], s["id"])
    assert full.status_code == 409 and full.json()["detail"]["code"] == "slot_unavailable"
    t2 = c.get("/volt/rep/%s/applications/%s/zoom-times" % (SLUG, out2["application_id"]), headers=tok2).json()
    assert [x["id"] for x in t2["times"]] == [s2["id"]]                            # refreshed options
    assert _book(volt, tok, out["application_id"], s2["id"]).json()["detail"]["code"] == "already_booked"
    cx = c.post("/volt/rep/%s/applications/%s/zoom-booking/cancel" % (SLUG, out["application_id"]), headers=tok)
    assert cx.status_code == 200 and cx.json()["zoom"]["state"] == "choose"
    assert _book(volt, tok2, out2["application_id"], s["id"]).status_code == 200    # seat released
    # Rebooking the same session after a cancel reuses the row (history kept).
    assert _book(volt, tok, out["application_id"], s2["id"]).status_code == 200
    staff = c.get("/volt/ops/%s/sessions" % volt["org"].id, headers=H(db, volt["staff"])).json()["items"]
    by = {x["id"]: x for x in staff}
    assert by[s["id"]]["booked"] == 1 and by[s["id"]]["spots_left"] == 0 and by[s["id"]]["availability"] == "full"
    assert by[s2["id"]]["booked"] == 1 and by[s2["id"]]["spots_left"] == 2


def test_group_capacity_and_lifecycle_closed_full_canceled_past(volt):
    c, db = volt["c"], volt["db"]
    org = volt["org"].id
    sh = H(db, volt["staff"])
    apps = []
    for i in range(3):
        tok, out = _submitted(volt, first="G%d" % i, last="Group")
        _eligible(volt, out["application_id"])
        apps.append((tok, out["application_id"]))
    g = _session(volt, capacity=2)
    assert _book(volt, *apps[0], g["id"]).status_code == 200
    assert _book(volt, *apps[1], g["id"]).status_code == 200
    assert _book(volt, *apps[2], g["id"]).json()["detail"]["reason"] == "full"
    # Capacity can't go below booked; reopening a full session doesn't bypass it.
    assert c.patch("/volt/ops/%s/sessions/%s" % (org, g["id"]), headers=sh, json={"capacity": 1}).status_code == 422
    c.post("/volt/ops/%s/sessions/%s/booking" % (org, g["id"]), headers=sh, json={"open": False})
    c.post("/volt/ops/%s/sessions/%s/booking" % (org, g["id"]), headers=sh, json={"open": True})
    assert _book(volt, *apps[2], g["id"]).json()["detail"]["reason"] == "full"
    # Closed: existing bookings stay, no new ones.
    cl = _session(volt, capacity=5, starts=_future(10))
    assert _book(volt, *apps[2], cl["id"]).status_code == 200
    c.post("/volt/rep/%s/applications/%s/zoom-booking/cancel" % (SLUG, apps[2][1]), headers=apps[2][0])
    closed = c.post("/volt/ops/%s/sessions/%s/booking" % (org, cl["id"]), headers=sh, json={"open": False}).json()
    assert closed["availability"] == "closed"
    assert _book(volt, *apps[2], cl["id"]).json()["detail"]["reason"] == "closed"
    # Canceled: join access gone, not bookable, reason recorded, attendees told.
    c2 = _session(volt, capacity=5, starts=_future(11))
    assert _book(volt, *apps[2], c2["id"]).status_code == 200
    r = c.post("/volt/ops/%s/sessions/%s/cancel" % (org, c2["id"]), headers=sh, json={"reason": "Host out sick"}).json()
    assert r["status"] == "canceled" and r["cancel_reason"] == "Host out sick"
    me = c.get("/volt/rep/%s/me" % SLUG, headers=apps[2][0]).json()
    mine = [x for x in me["sessions"] if x["id"] == c2["id"]][0]
    assert mine["status"] == "canceled" and mine["join_url"] is None
    assert db.query(VoltEmail).filter(VoltEmail.kind == "zoom_canceled").count() >= 1
    assert me["applications"][0]["zoom"]["state"] == "choose"                    # can choose again
    # Past: not bookable even if open.
    p = _session(volt, capacity=5, starts=_future(12))
    sess = db.get(VoltSession, p["id"])
    sess.starts_at = datetime.utcnow() - timedelta(hours=1)
    db.commit()
    assert _book(volt, *apps[2], p["id"]).json()["detail"]["reason"] == "past"
    # New sessions can't start in the past.
    assert c.post("/volt/ops/%s/sessions" % org, headers=sh, json={
        "kind": "interview", "project_id": volt["project"]("aca").id, "starts_local": "2020-01-01T10:00",
        "timezone": "America/Chicago", "duration_minutes": 30, "join_url": JOIN, "capacity": 1}).status_code == 422


def test_reschedule_updates_attendees_and_history(volt):
    c, db = volt["c"], volt["db"]
    org = volt["org"].id
    tok, out = _submitted(volt)
    _eligible(volt, out["application_id"])
    s = _session(volt, capacity=2)
    _book(volt, tok, out["application_id"], s["id"])
    new = _future(14, 15)
    r = c.patch("/volt/ops/%s/sessions/%s" % (org, s["id"]), headers=H(db, volt["staff"]), json={"starts_local": new})
    assert r.status_code == 200 and r.json()["starts_local"] == new and r.json()["rescheduled_at"]
    me = c.get("/volt/rep/%s/me" % SLUG, headers=tok).json()
    assert me["sessions"][0]["starts_local"] == new
    assert db.query(VoltEmail).filter(VoltEmail.kind == "zoom_rescheduled").count() == 1
    assert db.query(VoltAuditEvent).filter(VoltAuditEvent.action == "session_rescheduled").count() == 1


def test_timezone_and_daylight_saving(volt):
    """10:00 in Chicago is 16:00 UTC in winter (CST) and 15:00 UTC in summer (CDT)."""
    winter = _session(volt, starts="2027-03-13T10:00")
    summer = _session(volt, starts="2027-03-15T10:00")
    assert winter["starts_at"].startswith("2027-03-13T16:00") and summer["starts_at"].startswith("2027-03-15T15:00")
    assert winter["starts_local"] == "2027-03-13T10:00" and summer["starts_local"] == "2027-03-15T10:00"
    from app.services.volt.mail import _when
    s = volt["db"].get(VoltSession, summer["id"])
    assert "10:00 AM CDT (America/Chicago)" in _when(s)
    w = volt["db"].get(VoltSession, winter["id"])
    assert "10:00 AM CST (America/Chicago)" in _when(w)


def test_host_links_rejected_and_withdraw_releases_the_seat(volt):
    c, db = volt["c"], volt["db"]
    org = volt["org"].id
    sh = H(db, volt["staff"])
    for bad in ("https://zoom.us/s/81234567890?zak=abc", "https://zoom.us/j/81234567890?zak=xyz", "http://zoom.us/j/81234567890",
                "https://evil.example.com/j/81234567890"):
        r = c.post("/volt/ops/%s/sessions" % org, headers=sh, json={
            "kind": "interview", "project_id": volt["project"]("aca").id, "starts_local": _future(),
            "timezone": "America/Chicago", "duration_minutes": 30, "join_url": bad, "capacity": 1})
        assert r.status_code == 422, bad
    tok, out = _submitted(volt)
    _eligible(volt, out["application_id"])
    s = _session(volt, capacity=1)
    _book(volt, tok, out["application_id"], s["id"])
    c.post("/volt/ops/%s/applications/%s/status" % (org, out["application_id"]), headers=sh, json={"status": "withdrawn"})
    staff = [x for x in c.get("/volt/ops/%s/sessions" % org, headers=sh).json()["items"] if x["id"] == s["id"]][0]
    assert staff["booked"] == 0 and staff["availability"] == "open"


def test_applicant_cancel_respects_cutoff_and_staff_can_cancel_one_booking(volt):
    c, db = volt["c"], volt["db"]
    org = volt["org"].id
    tok, out = _submitted(volt)
    _eligible(volt, out["application_id"])
    s = _session(volt, capacity=1, booking_cutoff_minutes=60)
    _book(volt, tok, out["application_id"], s["id"])
    sess = db.get(VoltSession, s["id"])
    sess.starts_at = datetime.utcnow() + timedelta(minutes=30)                 # inside the cutoff now
    db.commit()
    r = c.post("/volt/rep/%s/applications/%s/zoom-booking/cancel" % (SLUG, out["application_id"]), headers=tok)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "past_cutoff" and "support@" in r.text
    rep_id = db.get(VoltApplication, out["application_id"]).rep_serial
    r = c.post("/volt/ops/%s/sessions/%s/bookings/%s/cancel" % (org, s["id"], rep_id), headers=H(db, volt["staff"]),
               json={"reason": "Asked by phone"})
    assert r.status_code == 200 and r.json()["booked"] == 0
    att = db.query(VoltSessionAttendee).filter(VoltSessionAttendee.session_id == s["id"]).one()
    assert att.booking_status == "canceled" and att.cancel_reason == "Asked by phone"


def test_multiple_applications_keep_their_own_zoom_state(volt):
    c, db = volt["c"], volt["db"]
    tok, out = _submitted(volt)
    fib = c.post("/volt/rep/%s/applications" % SLUG, headers=tok, json={"project_key": "fiber"}).json()
    _eligible(volt, out["application_id"])
    s = _session(volt, capacity=1)
    _book(volt, tok, out["application_id"], s["id"])
    me = c.get("/volt/rep/%s/me" % SLUG, headers=tok).json()
    by = {a["id"]: a for a in me["applications"]}
    assert by[out["application_id"]]["zoom"]["state"] == "booked" and by[fib["id"]]["zoom"]["state"] == "waiting"
    assert by[out["application_id"]]["reference"] != by[fib["id"]]["reference"]
    assert by[out["application_id"]]["reference"].startswith("VOLT-") and "REP-" not in by[out["application_id"]]["reference"]
    assert me["sessions"][0]["application_id"] == out["application_id"]
    t = c.get("/volt/rep/%s/applications/%s/zoom-times" % (SLUG, fib["id"]), headers=tok).json()
    assert t["times"] == []


def test_bootstrap_admin_setting_never_undoes_a_revocation(volt, monkeypatch):
    """revoke -> rerun provisioning (a redeploy) -> still revoked, for every
    way access can be taken away."""
    from app.services.volt import seed
    c, db = volt["c"], volt["db"]
    org = volt["org"]
    people = {}
    for key in ("suspended", "removed", "switched_off"):
        u = User(email="boot.%s@example.com" % key, full_name="Boot %s" % key, password_hash="x", role="org_admin",
                 is_active=True, must_change_password=False)
        db.add(u)
        people[key] = u
    db.commit()
    monkeypatch.setenv("VOLT_ORG_ADMIN_EMAILS", ",".join(u.email for u in people.values()))
    assert seed.ensure(db)["admins_granted"] == 3
    db.commit()
    assert _access(volt, volt["god"], people["suspended"].id, "suspend").json()["access"] == "suspended"
    assert _access(volt, volt["god"], people["removed"].id, "remove").json()["access"] == "removed"
    # Switched off outside People & Access (God Mode Manage Access): no volt_access row at all.
    m = db.query(Membership).filter(Membership.user_id == people["switched_off"].id, Membership.scope_id == org.id).one()
    m.is_active = False
    db.commit()
    for _ in range(2):                                   # two later deploys
        assert seed.ensure(db)["admins_granted"] == 0
        db.commit()
    db.expire_all()
    from app.models.volt_models import VoltAccess
    st = {a.user_id: a.state for a in db.query(VoltAccess).all()}
    assert st[people["suspended"].id] == "suspended" and st[people["removed"].id] == "removed"
    mems = {mm.user_id: mm.is_active for mm in db.query(Membership).filter(Membership.scope_id == org.id).all()}
    assert mems[people["removed"].id] is False and mems[people["switched_off"].id] is False
    for u in people.values():
        assert c.get("/volt/ops/%s" % org.id, headers=H(db, u)).status_code == 403


def test_waiting_zoom_notices_never_send_obsolete_or_duplicate_messages(volt, monkeypatch):
    """Mailbox not connected: notices wait. When it connects, only notices that
    still describe the current booking go out, each exactly once."""
    from app.services.volt import mail
    c, db = volt["c"], volt["db"]
    org = volt["org"].id
    sent = []

    class _R:
        status_code = 202
        text = ""
    tok, out = _submitted(volt, email="notice.one+volt-test@example.com")
    _eligible(volt, out["application_id"])
    s1 = _session(volt, capacity=2)
    s2 = _session(volt, capacity=2, starts=_future(12))
    _book(volt, tok, out["application_id"], s1["id"])                                  # confirmed #1 (will be obsolete)
    c.post("/volt/rep/%s/applications/%s/zoom-booking/cancel" % (SLUG, out["application_id"]), headers=tok)  # cancel notice
    _book(volt, tok, out["application_id"], s1["id"])                                  # rebooked: cancel notice obsolete
    sh = H(db, volt["staff"])
    c.patch("/volt/ops/%s/sessions/%s" % (org, s1["id"]), headers=sh, json={"starts_local": _future(8, 14)})  # resched #1
    c.patch("/volt/ops/%s/sessions/%s" % (org, s1["id"]), headers=sh, json={"starts_local": _future(8, 16)})  # resched #2
    tok2, out2 = _submitted(volt, first="Two", last="Session", email="notice.two+volt-test@example.com")
    _eligible(volt, out2["application_id"])
    _book(volt, tok2, out2["application_id"], s2["id"])
    c.post("/volt/ops/%s/sessions/%s/cancel" % (org, s2["id"]), headers=sh, json={"reason": "Host out"})
    rows = db.query(VoltEmail).filter(VoltEmail.kind.like("zoom_%")).all()
    assert rows and all(r.state in ("queued", "failed") for r in rows)                  # nothing sent yet
    # The mailbox connects.
    monkeypatch.setattr(mail, "mailbox", lambda db_, org_id: object())
    monkeypatch.setattr(mail, "_token", lambda db_, box: "t")
    monkeypatch.setattr(mail, "_send_graph", lambda token, prog, to, subject, html, save: sent.append(subject) or _R())
    for r in rows:
        r.next_attempt_at = None
        r.state = "queued"
    db.commit()
    for _ in range(3):                                                                  # poller runs repeatedly
        mail.retry_due(db)
    db.expire_all()
    by = {}
    for r in db.query(VoltEmail).filter(VoltEmail.kind.like("zoom_%")).all():
        by.setdefault((r.kind, r.state), []).append(r)
    # Exactly: the latest reschedule for applicant 1, and the session cancel for applicant 2.
    assert sent.count("Your VOLT Zoom interview has a new time") == 1
    assert sent.count("Your VOLT Zoom interview was canceled") == 1
    assert sent.count("Your VOLT Zoom interview is scheduled") == 0      # both confirmations are superseded
    skipped = [r for (k, st), rs in by.items() if st == "skipped" for r in rs]
    assert all(r.last_error.startswith("Not sent:") for r in skipped) and len(skipped) == len(rows) - 2
    n = len(sent)
    mail.retry_due(db)
    assert len(sent) == n                                                               # never twice


def test_cleanup_removes_test_records_only_and_keeps_live_availability(volt):
    c, db = volt["c"], volt["db"]
    org = volt["org"].id
    from app.models.volt_models import VoltRep
    real_body, real_tok, real = volt["register"](first="Real", last="Person", email="real.person@example.com")
    tok, out = _submitted(volt, first="Test", last="Booker")
    _eligible(volt, out["application_id"])
    live = _session(volt, capacity=2)
    gone = _session(volt, capacity=1, starts=_future(13))
    _book(volt, tok, out["application_id"], live["id"])
    tok2, out2 = _submitted(volt, first="Test", last="Canceled")
    _eligible(volt, out2["application_id"])
    _book(volt, tok2, out2["application_id"], gone["id"])
    c.post("/volt/ops/%s/sessions/%s/cancel" % (org, gone["id"]), headers=H(db, volt["staff"]), json={"reason": "QA"})
    test_uid = db.get(VoltRep, db.get(VoltApplication, out["application_id"]).rep_serial).user_id
    _access(volt, volt["staff"], test_uid, "suspend", "QA")
    r = c.post("/volt/ops/%s/test-records/purge" % org, headers=H(db, volt["god"])).json()
    assert r["reps"] == 2 and r["applications"] == 2 and r["sessions"] == 1
    db.expire_all()
    assert db.get(VoltSession, gone["id"]) is None                    # test-only, canceled: removed
    s = db.get(VoltSession, live["id"])
    assert s is not None                                              # live availability kept ...
    staff = [x for x in c.get("/volt/ops/%s/sessions" % org, headers=H(db, volt["staff"])).json()["items"] if x["id"] == live["id"]][0]
    assert staff["booked"] == 0                                       # ... with its seat freed
    assert db.query(VoltAccess).filter(VoltAccess.user_id == test_uid).count() == 0
    assert db.query(VoltAuditEvent).filter(VoltAuditEvent.entity_type == "access", VoltAuditEvent.entity_id == test_uid).count() == 0
    assert db.get(VoltApplication, real["application_id"]) is not None   # real applicant preserved
    assert c.post("/volt/ops/%s/test-records/purge" % org, headers=H(db, volt["god"])).json()["reps"] == 0
