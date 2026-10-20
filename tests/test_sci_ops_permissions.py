"""SCI ops (Oct 20): every member of the SCI team works the replies they see in
SCI Live (opened / responded / closed / handled), adds notes to program
families, reads the Appointments tab, and takes unassigned families. Moving a
family that belongs to someone else stays a manager's call. Spectators stay
view-only; everything stays inside the workspace."""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.models.models import AuditLogEntry, BookingLink, Lead, Organization
from app.models.program_models import ProgramResponse, ProgramUnmatchedReply
from app.models.work_models import LeadNote
from app.services.programs import responses as R
from app.services.workspace_access import grant_workspace_membership
from tests.test_outreach_program import _h, _lead_for, hash_password, program  # noqa: F401 (fixture)
from tests.test_spectator import _login


def _in(db, lead, text="Can I schedule a visit this week?", minutes_ago=5):
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        return R.on_inbound(db, lead, text, "sms", now=datetime.utcnow() - timedelta(minutes=minutes_ago),
                            run_setter=False)


@pytest.fixture
def team(program, sample_advisor, second_advisor):
    """A family of the program assigned to ANOTHER advisor, with a new reply."""
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, second_advisor, "L001", first="Lester")
    resp = _in(db, lead)
    return dict(program, lead=lead, resp=resp, adv=sample_advisor, other=second_advisor,
                h=_h(db, sample_advisor))


def _spectator_h(db, org):
    u = _login(db, "view.only@example.com")
    grant_workspace_membership(db, u.id, org.id, role="spectator", check_capacity=False)
    return dict(_h(db, u), **{"X-Workspace-Id": org.id})


# ── 1. replies: opened / responded / closed / viewed / handled ───────────────

def test_advisor_marks_opened_responded_and_closed_with_audit(client, team):
    db, h, resp = team["db"], team["h"], team["resp"]
    for state in ("opened", "responded", "closed"):
        r = client.post("/program/responses/%s/mark" % resp.id, headers=h, json={"state": state})
        assert r.status_code == 200, (state, r.text)
        assert r.json()["status"] == state
    db.refresh(resp)
    assert resp.handling_status == "closed"
    entry = db.query(AuditLogEntry).filter_by(action="sci.response_closed").one()
    assert entry.target_id == resp.id and entry.actor_user_id == team["adv"].id
    assert team["adv"].id in (entry.details or "")


def test_advisor_viewed_is_allowed_and_only_the_primary_contact_opens(client, team):
    db, resp = team["db"], team["resp"]
    r = client.post("/program/responses/%s/viewed" % resp.id, headers=team["h"], json={})
    assert r.status_code == 200 and r.json()["status"] == "new"       # not the primary contact
    team["prog"].primary_contact_user_id = team["adv"].id
    db.commit()
    r = client.post("/program/responses/%s/viewed" % resp.id, headers=team["h"], json={})
    assert r.status_code == 200 and r.json()["status"] == "opened"


def test_advisor_never_reaches_a_test_contacts_reply(client, team, sample_advisor):
    db, org = team["db"], team["org"]
    t = _lead_for(db, org, sample_advisor, "L002", phone="2145550102", first="Test", email="t@example.com")
    t.is_test = True
    db.commit()
    tr = _in(db, t)
    r = client.post("/program/responses/%s/mark" % tr.id, headers=team["h"], json={"state": "closed"})
    assert r.status_code == 404
    db.refresh(tr)
    assert tr.handling_status != "closed"


def test_reply_in_another_workspace_is_not_found(client, team):
    db = team["db"]
    other = Organization(name="Other Co", slug="other-co-ops")
    db.add(other)
    db.flush()
    lead = Lead(organization_id=other.id, first_name="X", last_name="Y", phone="12145559999", status="sent")
    db.add(lead)
    db.flush()
    foreign = ProgramResponse(organization_id=other.id, lead_id=lead.id, channel="sms", response_class="hot",
                              handling_status="new", received_at=datetime.utcnow())
    db.add(foreign)
    db.commit()
    for path, body in (("/program/responses/%s/mark" % foreign.id, {"state": "closed"}),
                       ("/program/responses/%s/viewed" % foreign.id, {})):
        assert client.post(path, headers=team["h"], json=body).status_code == 404
        # a manager of the program cannot either
        assert client.post(path, headers=_h(db, team["admin"]), json=body).status_code == 404
    db.refresh(foreign)
    assert foreign.handling_status == "new"


def _unmatched(db, org_id):
    u = ProgramUnmatchedReply(organization_id=org_id, alias="eg@example.com", from_address="who@example.com",
                              subject="Question", body_excerpt="Hello", received_at=datetime.utcnow(),
                              reason="no_lead", status="open")
    db.add(u)
    db.commit()
    return u


def test_advisor_marks_an_unmatched_reply_handled(client, team):
    db = team["db"]
    u = _unmatched(db, team["org"].id)
    r = client.post("/program/unmatched-replies/%s/handled" % u.id, headers=team["h"], json={})
    assert r.status_code == 200 and r.json()["status"] == "handled"
    db.refresh(u)
    assert u.handled_by == team["adv"].id and u.handled_at is not None


def test_spectator_still_cannot_mark_handle_or_note(client, team):
    db = team["db"]
    view = _spectator_h(db, team["org"])
    u = _unmatched(db, team["org"].id)
    for path, body in (("/program/responses/%s/mark" % team["resp"].id, {"state": "closed"}),
                       ("/program/unmatched-replies/%s/handled" % u.id, {}),
                       ("/work/leads/%s/notes" % team["lead"].id, {"body": "hi"})):
        r = client.post(path, headers=view, json=body)
        assert r.status_code == 403, (path, r.status_code, r.text[:200])
    db.refresh(team["resp"])
    db.refresh(u)
    assert team["resp"].handling_status == "new" and u.status == "open"
    assert db.query(LeadNote).count() == 0


# ── 1. notes on program families ─────────────────────────────────────────────

def test_advisor_adds_and_reads_notes_on_a_program_family_assigned_to_someone_else(client, team):
    db, h, lead = team["db"], team["h"], team["lead"]
    r = client.post("/work/leads/%s/notes" % lead.id, headers=h, json={"body": "Left a voicemail."})
    assert r.status_code == 201, r.text
    note = db.query(LeadNote).one()
    assert note.organization_id == team["org"].id and note.author_user_id == team["adv"].id
    items = client.get("/work/leads/%s/notes" % lead.id, headers=h).json()["items"]
    assert [i["body"] for i in items] == ["Left a voicemail."]


def test_notes_rule_is_unchanged_for_non_program_and_test_leads(client, team, sample_advisor):
    db, h, org = team["db"], team["h"], team["org"]
    plain = Lead(organization_id=org.id, assigned_to_id=team["other"].id, first_name="Not", last_name="Program",
                 phone="12145558888", status="new")
    db.add(plain)
    db.commit()
    assert client.post("/work/leads/%s/notes" % plain.id, headers=h, json={"body": "x"}).status_code == 404
    assert client.get("/work/leads/%s/notes" % plain.id, headers=h).status_code == 404
    t = _lead_for(db, org, team["other"], "L002", phone="2145550102", first="Test", email="t@example.com")
    t.is_test = True
    db.commit()
    assert client.post("/work/leads/%s/notes" % t.id, headers=h, json={"body": "x"}).status_code == 404
    # own leads keep working exactly as before
    mine = Lead(organization_id=org.id, assigned_to_id=sample_advisor.id, first_name="Mine", phone="12145557777",
                status="new")
    db.add(mine)
    db.commit()
    assert client.post("/work/leads/%s/notes" % mine.id, headers=h, json={"body": "ok"}).status_code == 201


def test_notes_never_reach_a_program_family_of_another_workspace(client, team):
    db = team["db"]
    other = Organization(name="Elsewhere", slug="elsewhere-ops")
    db.add(other)
    db.flush()
    u = _login(db, "adv.elsewhere@example.com", org_id=other.id)
    r = client.post("/work/leads/%s/notes" % team["lead"].id, headers=_h(db, u), json={"body": "x"})
    assert r.status_code in (403, 404)
    assert db.query(LeadNote).count() == 0


# ── 1. appointments, read-only ───────────────────────────────────────────────

def test_advisor_reads_the_appointments_tab(client, team):
    db, lead = team["db"], team["lead"]
    db.add(BookingLink(lead_id=lead.id, user_id=team["other"].id, status="booked", token="tok-ops-1",
                       booked_time=datetime.utcnow() + timedelta(days=3)))
    db.commit()
    r = client.get("/program/appointments?when=upcoming", headers=team["h"])
    assert r.status_code == 200, r.text
    assert [i["lead_id"] for i in r.json()["items"]] == [lead.id]
    r = client.get("/program/appointments?when=all&lead_id=%s" % lead.id, headers=team["h"])
    assert r.status_code == 200 and len(r.json()["items"]) == 1


def test_advisor_outside_any_program_still_gets_no_appointments(client, team):
    db = team["db"]
    other = Organization(name="No Program", slug="no-program-ops")
    db.add(other)
    db.flush()
    u = _login(db, "adv.noprog@example.com", org_id=other.id)
    assert client.get("/program/appointments", headers=_h(db, u)).status_code == 404


# ── 2. reassignment ──────────────────────────────────────────────────────────

def _assign(client, h, lead_id, user_id):
    return client.post("/program/command/assign", headers=h, json={"lead_id": lead_id, "user_id": user_id})


def test_advisor_takes_an_unassigned_family_and_passes_on_their_own(client, team):
    db, h, adv, other = team["db"], team["h"], team["adv"], team["other"]
    lead = team["lead"]
    lead.assigned_to_id = None
    db.commit()
    assert _assign(client, h, lead.id, other.id).status_code == 403     # only to themselves
    r = _assign(client, h, lead.id, adv.id)
    assert r.status_code == 200 and r.json()["assigned_to_id"] == adv.id
    r = _assign(client, h, lead.id, other.id)                           # their own family: may pass it on
    assert r.status_code == 200 and r.json()["assigned_to_id"] == other.id
    entries = db.query(AuditLogEntry).filter_by(action="sci.assign", target_id=lead.id).all()
    assert len(entries) == 2 and all(e.actor_user_id == adv.id for e in entries)


def test_advisor_cannot_take_a_family_that_belongs_to_someone_else(client, team):
    db, lead = team["db"], team["lead"]
    for user_id in (team["adv"].id, None):
        r = _assign(client, team["h"], lead.id, user_id)
        assert r.status_code == 403 and "manager" in r.json()["detail"]
    db.refresh(lead)
    assert lead.assigned_to_id == team["other"].id
    assert not db.query(AuditLogEntry).filter_by(action="sci.assign").all()


def test_manager_reassigns_any_family(client, team):
    db, lead = team["db"], team["lead"]
    r = _assign(client, _h(db, team["admin"]), lead.id, team["adv"].id)
    assert r.status_code == 200 and r.json()["assigned_to_id"] == team["adv"].id
    assert db.query(AuditLogEntry).filter_by(action="sci.assign", target_id=lead.id).one().actor_user_id == team["admin"].id
