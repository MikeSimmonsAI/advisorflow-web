"""Field appointments: book, assign, "my day", and who sees what.

The rules that matter: a rep sees only their own visits, a manager sees their
team's, a field login can mark a visit done but cannot move, reassign or
cancel it, and another company's appointment does not exist.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.models import User

CT = ZoneInfo("America/Chicago")


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:300])
    return r.json()


def _today_at(h, m=0):
    now = datetime.now(CT)
    return now.replace(hour=h, minute=m, second=0, microsecond=0).isoformat()


def _team(client, admin_h, rep, manager):
    ok(client.post("/wholesale/team/members", headers=admin_h,
                   json={"user_id": manager.id, "role": "manager", "confirm_lock": True}))
    ok(client.post("/wholesale/team/members", headers=admin_h,
                   json={"user_id": rep.id, "role": "field_specialist", "reports_to_user_id": manager.id}))


def _h(db, user):
    from app.services.auth_service import create_access_token
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


def test_book_assign_and_my_day(client, admin_auth_headers, auth_headers, sample_advisor, second_advisor, db_session):
    _team(client, admin_auth_headers, sample_advisor, second_advisor)
    reps = ok(client.get("/wholesale/appointments/reps", headers=admin_auth_headers))["items"]
    assert sample_advisor.id in {r["user_id"] for r in reps}
    a = ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json={
        "starts_at": _today_at(23, 30), "duration_minutes": 60, "assigned_to_id": sample_advisor.id,
        "address": "123 Main St, Dallas, TX 75201", "notes": "Gate code 4411"}))
    assert a["status"] == "scheduled" and a["assigned_to_name"]
    assert a["directions_url"].startswith("https://www.google.com/maps/dir/")
    assert a["local_time"] == "11:30 PM"                     # late evening Central is still today
    mine = ok(client.get("/wholesale/appointments?when=today", headers=auth_headers))
    assert [x["id"] for x in mine["items"]] == [a["id"]] and mine["is_field"] and not mine["can_book"]


def test_a_rep_sees_only_their_own_and_a_manager_sees_the_team(
        client, admin_auth_headers, auth_headers, sample_advisor, second_advisor, db_session):
    _team(client, admin_auth_headers, sample_advisor, second_advisor)
    admin = db_session.query(User).filter(User.role == "org_admin").first()
    theirs = ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json={
        "starts_at": _today_at(14), "assigned_to_id": sample_advisor.id, "address": "1 Rep Rd"}))
    other = ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json={
        "starts_at": _today_at(15), "assigned_to_id": admin.id, "address": "2 Boss Blvd"}))
    rep_ids = {x["id"] for x in ok(client.get("/wholesale/appointments?when=all", headers=auth_headers))["items"]}
    assert rep_ids == {theirs["id"]}
    assert client.patch("/wholesale/appointments/%s" % other["id"], headers=auth_headers,
                        json={"notes": "x"}).status_code == 404
    mgr = _h(db_session, second_advisor)
    mgr_ids = {x["id"] for x in ok(client.get("/wholesale/appointments?when=all", headers=mgr))["items"]}
    assert theirs["id"] in mgr_ids and other["id"] not in mgr_ids
    all_ids = {x["id"] for x in ok(client.get("/wholesale/appointments?when=all", headers=admin_auth_headers))["items"]}
    assert {theirs["id"], other["id"]} <= all_ids


def test_a_field_login_marks_done_but_cannot_move_or_book(
        client, admin_auth_headers, auth_headers, sample_advisor, second_advisor):
    _team(client, admin_auth_headers, sample_advisor, second_advisor)
    a = ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json={
        "starts_at": _today_at(10), "assigned_to_id": sample_advisor.id, "address": "9 Field Way"}))
    path = "/wholesale/appointments/%s" % a["id"]
    assert client.patch(path, headers=auth_headers, json={"assigned_to_id": ""}).status_code == 403
    assert client.patch(path, headers=auth_headers, json={"starts_at": _today_at(16)}).status_code == 403
    assert client.patch(path, headers=auth_headers, json={"status": "cancelled"}).status_code == 403
    done = ok(client.patch(path, headers=auth_headers, json={"status": "completed", "notes": "Met both owners"}))
    assert done["status"] == "completed" and done["completed_at"]
    assert client.post("/wholesale/appointments", headers=auth_headers, json={
        "starts_at": _today_at(11), "address": "x"}).status_code == 403


def test_reschedule_reassign_cancel_and_property_link(client, admin_auth_headers, sample_advisor, second_advisor):
    _team(client, admin_auth_headers, sample_advisor, second_advisor)
    prop = ok(client.post("/wholesale/properties", headers=admin_auth_headers,
                          json={"street_address": "77 Oak Ln", "city": "Fort Worth", "state": "TX",
                                "zip_code": "76104", "is_test": True}))
    pid = prop.get("id") or prop.get("property", {}).get("id")
    a = ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json={
        "starts_at": _today_at(9), "property_id": pid, "assigned_to_id": sample_advisor.id}))
    assert a["address"].startswith("77 Oak Ln, Fort Worth") and a["is_test"] is True
    tomorrow = (datetime.now(CT) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    moved = ok(client.patch("/wholesale/appointments/%s" % a["id"], headers=admin_auth_headers,
                            json={"starts_at": tomorrow.isoformat(), "assigned_to_id": second_advisor.id}))
    assert moved["local_date"] == tomorrow.date().isoformat() and moved["assigned_to_id"] == second_advisor.id
    gone = ok(client.patch("/wholesale/appointments/%s" % a["id"], headers=admin_auth_headers,
                           json={"status": "cancelled"}))
    assert gone["status"] == "cancelled"
    r = client.post("/wholesale/appointments", headers=admin_auth_headers, json={"starts_at": _today_at(9)})
    assert r.status_code == 422                                      # nowhere to go


def test_another_company_cannot_see_or_touch_it(client, admin_auth_headers, db_session):
    from app.models.models import Organization
    a = ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json={
        "starts_at": _today_at(12), "address": "5 Private Pl"}))
    org = Organization(name="Rival LLC", slug="rival-llc", plan="standard", industry="real_estate")
    db_session.add(org); db_session.commit()
    rival = User(email="boss@rival.test", full_name="Rival Boss", role="org_admin",
                 organization_id=org.id, password_hash="x", must_change_password=False)
    db_session.add(rival); db_session.commit()
    h = _h(db_session, rival)
    assert client.patch("/wholesale/appointments/%s" % a["id"], headers=h, json={"notes": "x"}).status_code == 404
    listed = client.get("/wholesale/appointments?when=all", headers=h)
    assert listed.status_code != 200 or a["id"] not in {x["id"] for x in listed.json()["items"]}


# ── build list #16: log the visit outcome ───────────────────────────────────

def test_rep_logs_signed_dead_or_follow_up_and_the_office_gets_the_call_back(
        client, admin_auth_headers, auth_headers, sample_advisor, second_advisor, sample_org, db_session):
    from app.models.evosense_models import EvoSenseFollowUp, EvoSenseProperty
    _team(client, admin_auth_headers, sample_advisor, second_advisor)
    admin = db_session.query(User).filter(User.role == "org_admin").first()
    prop = EvoSenseProperty(organization_id=sample_org.id, street_address="3 Outcome Ct", city="Dallas",
                            state="TX", zip_code="75201", is_test=True)
    db_session.add(prop); db_session.commit()
    book = lambda: ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json={
        "starts_at": _today_at(10), "evosense_property_id": prop.id, "assigned_to_id": sample_advisor.id}))

    signed = ok(client.patch("/wholesale/appointments/%s" % book()["id"], headers=auth_headers,
                             json={"outcome": "signed", "outcome_note": "Signed at 128k"}))
    assert signed["outcome"] == "signed" and signed["status"] == "completed" and signed["completed_at"]

    a = book()
    r = client.patch("/wholesale/appointments/%s" % a["id"], headers=auth_headers, json={"outcome": "follow_up"})
    assert r.status_code == 422                                    # a follow up needs a date
    fu = ok(client.patch("/wholesale/appointments/%s" % a["id"], headers=auth_headers, json={
        "outcome": "follow_up", "outcome_note": "Wants to talk to her brother",
        "follow_up_at": "2026-10-15T10:00:00-05:00"}))
    assert fu["outcome"] == "follow_up"
    call = db_session.query(EvoSenseFollowUp).filter(EvoSenseFollowUp.property_id == prop.id).one()
    assert call.assigned_to_id == admin.id and "brother" in call.note    # the office calls back

    dead = ok(client.patch("/wholesale/appointments/%s" % book()["id"], headers=auth_headers,
                           json={"outcome": "dead"}))
    assert dead["outcome"] == "dead"
    assert client.patch("/wholesale/appointments/%s" % book()["id"], headers=auth_headers,
                        json={"outcome": "maybe"}).status_code == 422



# ── the rep hears about it ───────────────────────────────────────────────────

def test_the_rep_is_told_when_booked_moved_handed_or_cancelled(
        client, admin_auth_headers, auth_headers, sample_advisor, second_advisor, db_session):
    from app.models.models import Notification, NotificationType
    _team(client, admin_auth_headers, sample_advisor, second_advisor)
    bells = lambda uid: [n.message for n in db_session.query(Notification).filter(
        Notification.user_id == uid, Notification.type == NotificationType.WHOLESALE_APPOINTMENT)
        .order_by(Notification.created_at.asc()).all()]
    a = ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json={
        "starts_at": _today_at(10), "address": "7 Bell St, Dallas, TX", "assigned_to_id": sample_advisor.id}))
    assert bells(sample_advisor.id)[-1].startswith("New appointment") and "7 Bell St" in bells(sample_advisor.id)[-1]
    ok(client.patch("/wholesale/appointments/%s" % a["id"], headers=admin_auth_headers, json={"starts_at": _today_at(15)}))
    assert bells(sample_advisor.id)[-1].startswith("Appointment moved")
    ok(client.patch("/wholesale/appointments/%s" % a["id"], headers=admin_auth_headers,
                    json={"assigned_to_id": second_advisor.id}))
    assert bells(second_advisor.id)[-1].startswith("Appointment handed to you")
    ok(client.patch("/wholesale/appointments/%s" % a["id"], headers=admin_auth_headers, json={"status": "cancelled"}))
    assert bells(second_advisor.id)[-1].startswith("Appointment cancelled")
    # the rep's own change does not ping themselves
    b = ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json={
        "starts_at": _today_at(11), "address": "8 Bell St", "assigned_to_id": sample_advisor.id}))
    n = len(bells(sample_advisor.id))
    ok(client.patch("/wholesale/appointments/%s" % b["id"], headers=auth_headers, json={"status": "completed"}))
    assert len(bells(sample_advisor.id)) == n
    offers = ok(client.get("/wholesale/appointments/%s/offers" % b["id"], headers=auth_headers))
    assert offers["items"] == [] and offers["arv_on_file"] is None      # no property, no ARV hint
