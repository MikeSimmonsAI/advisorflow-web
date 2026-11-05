"""RESTLAND COMPLETION (Issue #507): every family findable, earlier work read in
place, appointment history and case files visible, analytics population as
SQL, Restland's own theme. Synthetic data only; nothing reaches a provider.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.models.models import BookingLink, Lead, Organization, Reply, User
from app.models.program_models import ProgramAlert, ProgramResponse, ProgramSourceRecord
from app.services.auth_service import create_access_token, hash_password
from app.services.programs import program_profile, sci_analytics, single_location_cutover as C


def _user(db, org, email, role="org_admin"):
    u = User(organization_id=org.id, email=email, password_hash=hash_password("Pass12345!"),
             full_name=email.split("@")[0], role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


NAMES = ["Avery", "Blake", "Casey", "Drew", "Emery", "Finley", "Gray", "Harper"]


@pytest.fixture()
def rl(db_session, sample_org):
    db = db_session
    leads = []
    for i in range(60):
        l = Lead(organization_id=sample_org.id, first_name=NAMES[i % 8], last_name="Synth%02d" % (i % 7),
                 phone="+1214555%04d" % i, email="rl%d@example.com" % i,
                 status="needs_tier_review" if i % 4 == 0 else ("dnc" if i % 9 == 0 else "new"))
        db.add(l)
        leads.append(l)
    test_lead = Lead(organization_id=sample_org.id, first_name="Test", last_name="Record", phone="+12145559999",
                     status="new", is_test=True)
    db.add(test_lead)
    db.commit()
    C.apply(db, sample_org, hero_title="Restland", contact_name="Synthetic Contact")
    admin = _user(db, sample_org, "rl-admin@example.com")
    advisor = _user(db, sample_org, "rl-advisor@example.com", role="advisor")
    return {"db": db, "org": sample_org, "leads": leads, "admin": admin, "advisor": advisor, "test": test_lead}


# ── 1. every family findable ────────────────────────────────────────────────

def test_empty_search_browses_every_real_family_with_total(client, rl):
    db = rl["db"]
    r = client.get("/program/command/search?limit=25", headers=_h(db, rl["admin"])).json()
    assert r["browse"] is True and r["total"] == 60 and len(r["items"]) == 25
    assert rl["test"].id not in {i["lead_id"] for i in r["items"]}


def test_pages_are_stable_and_disjoint(client, rl):
    db, h = rl["db"], _h(rl["db"], rl["admin"])
    seen = []
    for off in (0, 25, 50):
        seen += [i["lead_id"] for i in client.get("/program/command/search?limit=25&offset=%d" % off, headers=h).json()["items"]]
    assert len(seen) == 60 and len(set(seen)) == 60
    again = [i["lead_id"] for i in client.get("/program/command/search?limit=25&offset=0", headers=h).json()["items"]]
    assert again == seen[:25]


def test_filters_and_search(client, rl):
    db, h = rl["db"], _h(rl["db"], rl["admin"])
    nr = client.get("/program/command/search?show=needs_review&limit=50", headers=h).json()
    assert nr["total"] == 15 and all(i["status"] == "needs_tier_review" for i in nr["items"])
    assert client.get("/program/command/search?q=Avery&limit=50", headers=h).json()["total"] == 8
    assert client.get("/program/command/search?q=5550003", headers=h).json()["total"] == 1
    assert client.get("/program/command/search?q=rl5@example", headers=h).json()["total"] >= 1
    assert client.get("/program/command/search?q=a", headers=h).json()["items"] == []
    assert client.get("/program/command/search?show=bogus", headers=h).status_code == 400


def test_advisor_seat_can_browse_families(client, rl):
    r = client.get("/program/command/search?limit=5", headers=_h(rl["db"], rl["advisor"]))
    assert r.status_code == 200 and r.json()["total"] == 60


def test_other_workspace_never_sees_restland_families(client, rl):
    db = rl["db"]
    other = Organization(name="Other Synthetic Co", slug="other-synth", plan="standard", industry="generic")
    db.add(other)
    db.commit()
    stranger = _user(db, other, "stranger@example.com")
    for path in ("/program/command/search", "/program/command/earlier-conversations", "/program/command/case-files"):
        assert client.get(path, headers=_h(db, stranger)).status_code == 404


# ── 2. earlier replies, read in place, once ─────────────────────────────────

def _replies(db, lead, n=2):
    out = []
    for k in range(n):
        r = Reply(lead_id=lead.id, body="Earlier synthetic reply %d" % k, source="sms",
                  received_at=datetime(2026, 8, 10, 15, 0) + timedelta(hours=k))
        db.add(r)
        out.append(r)
    db.commit()
    return out


def test_family_thread_shows_earlier_replies_without_writing_anything(client, rl):
    db, lead = rl["db"], rl["leads"][1]
    _replies(db, lead)
    before = (db.query(ProgramResponse).count(), db.query(ProgramAlert).count())
    f = client.get("/program/command/family/%s" % lead.id, headers=_h(db, rl["admin"])).json()
    hist = [t for t in f["thread"] if t.get("historical")]
    assert [t["text"] for t in hist] == ["Earlier synthetic reply 0", "Earlier synthetic reply 1"]
    assert (db.query(ProgramResponse).count(), db.query(ProgramAlert).count()) == before
    assert f["status"] is None and f["human_handoff"] in (False, None)   # history is not new work


def test_reply_already_recorded_by_the_program_appears_once(client, rl):
    db, lead = rl["db"], rl["leads"][2]
    r1, r2 = _replies(db, lead)
    db.add(ProgramResponse(organization_id=rl["org"].id, lead_id=lead.id, reply_id=r1.id, channel="sms",
                           response_class="active", body_excerpt=r1.body, received_at=r1.received_at,
                           handling_status="closed"))
    db.commit()
    f = client.get("/program/command/family/%s" % lead.id, headers=_h(db, rl["admin"])).json()
    texts = [t["text"] for t in f["thread"]]
    assert texts.count(r1.body) == 1 and texts.count(r2.body) == 1


def test_earlier_conversations_list(client, rl):
    db = rl["db"]
    _replies(db, rl["leads"][3], 3)
    _replies(db, rl["leads"][4], 1)
    _replies(db, rl["test"], 1)                                # test record never listed
    r = client.get("/program/command/earlier-conversations", headers=_h(db, rl["admin"])).json()
    assert r["total"] == 2 and {i["replies"] for i in r["items"]} == {3, 1}
    assert all(i["historical"] for i in r["items"])


def test_long_history_is_paged_not_lost(client, rl):
    db, lead = rl["db"], rl["leads"][5]
    _replies(db, lead, 45)
    h = _h(db, rl["admin"])
    f = client.get("/program/command/family/%s" % lead.id, headers=h).json()
    assert len(f["thread"]) == 40 and f["thread_total"] == 45
    assert len(client.get("/program/command/family/%s?history=500" % lead.id, headers=h).json()["thread"]) == 45


# ── 3. appointments and case files ──────────────────────────────────────────

CASE_DDL = ("CREATE TABLE IF NOT EXISTS appointment_case_files (id VARCHAR PRIMARY KEY, lead_id VARCHAR, "
            "organization_id VARCHAR, recorded_by_id VARCHAR, booking_link_id VARCHAR, appointment_date TIMESTAMP, "
            "appointment_type VARCHAR, outcome_type VARCHAR, products_discussed TEXT, products_sold TEXT, "
            "advisor_notes TEXT, objections_raised TEXT, client_concerns TEXT, referral_potential BOOLEAN, "
            "case_status VARCHAR, next_action VARCHAR, next_action_date TIMESTAMP, next_action_notes TEXT, "
            "created_at TIMESTAMP, updated_at TIMESTAMP)")


def _booking(db, lead, user, when, status="booked"):
    b = BookingLink(lead_id=lead.id, user_id=user.id, status=status, booked_time=when)
    db.add(b)
    db.commit()
    return b


def test_past_and_upcoming_appointments_with_staff_and_case_file(client, rl):
    db, h = rl["db"], _h(rl["db"], rl["admin"])
    db.execute(text(CASE_DDL))
    past = _booking(db, rl["leads"][6], rl["admin"], datetime(2026, 8, 20, 10, 0))
    _booking(db, rl["leads"][7], rl["admin"], datetime.now() + timedelta(days=5))
    _booking(db, rl["leads"][8], rl["admin"], datetime(2026, 8, 22, 9, 0), status="cancelled")
    BookingLink.__table__  # noqa: B018
    db.add(BookingLink(lead_id=rl["leads"][9].id, user_id=rl["admin"].id, status="pending"))   # never used
    db.execute(text("INSERT INTO appointment_case_files (id, lead_id, organization_id, booking_link_id, "
                    "appointment_date, case_status) VALUES ('cf1', :l, :o, :b, :d, 'open')"),
               {"l": rl["leads"][6].id, "o": rl["org"].id, "b": past.id, "d": datetime(2026, 8, 20, 10, 0)})
    db.commit()
    up = client.get("/program/appointments?when=upcoming", headers=h).json()["items"]
    pa = client.get("/program/appointments?when=past", headers=h).json()["items"]
    al = client.get("/program/appointments?when=all", headers=h).json()["items"]
    assert len(up) == 1 and len(pa) == 2 and len(al) == 3          # pending link is not an appointment
    p = next(a for a in pa if a["id"] == past.id)
    assert p["staff"] and p["has_case_file"] is True
    assert {a["status"] for a in pa} == {"booked", "cancelled"}


def test_case_files_read_in_place_including_missing_family(client, rl):
    db, h = rl["db"], _h(rl["db"], rl["admin"])
    db.execute(text(CASE_DDL))
    b = _booking(db, rl["leads"][10], rl["admin"], datetime(2026, 8, 12, 14, 0))
    db.execute(text("INSERT INTO appointment_case_files (id, lead_id, organization_id, booking_link_id, appointment_date, "
                    "case_status, advisor_notes) VALUES ('cf2', :l, :o, :b, :d, 'follow_up_needed', 'Synthetic note')"),
               {"l": rl["leads"][10].id, "o": rl["org"].id, "b": b.id, "d": datetime(2026, 8, 12, 14, 0)})
    db.execute(text("INSERT INTO appointment_case_files (id, lead_id, organization_id, booking_link_id, appointment_date, "
                    "case_status) VALUES ('cf3', 'gone-lead', :o, 'gone-booking', :d, 'open')"),
               {"o": rl["org"].id, "d": datetime(2026, 8, 1, 9, 0)})
    db.commit()
    r = client.get("/program/command/case-files", headers=h).json()
    assert r["total"] == 2
    gone = next(i for i in r["items"] if i["id"] == "cf3")
    assert gone["family_missing"] is True and gone["booking_missing"] is True
    f = client.get("/program/command/family/%s" % rl["leads"][10].id, headers=h).json()
    assert [c["id"] for c in f["case_files"]] == ["cf2"] and f["case_files"][0]["booking_status"] == "booked"
    assert f["bookings"][0]["status"] == "booked"
    # An advisor seat sees the family, not the case-file outcomes.
    assert client.get("/program/command/case-files", headers=_h(db, rl["advisor"])).status_code == 403


# ── 4. analytics population ─────────────────────────────────────────────────

def test_ledger_population_is_the_same_families(rl):
    db, org = rl["db"], rl["org"]
    L = sci_analytics.Ledger(db, org.id)
    assert len(L.families) == 60 and rl["test"].id not in L.families


def test_ledger_locks_are_per_workspace():
    a, b = sci_analytics._org_lock("org-a"), sci_analytics._org_lock("org-b")
    assert a is not b and sci_analytics._org_lock("org-a") is a


def test_ledger_freshness_counts_from_build_end(rl, monkeypatch):
    db, org = rl["db"], rl["org"]
    sci_analytics._CACHE.pop(org.id, None)
    L1 = sci_analytics.ledger(db, org.id)
    assert getattr(L1, "ready_at", None) is not None
    assert sci_analytics.ledger(db, org.id) is L1               # warm hit


# ── 5. theme ────────────────────────────────────────────────────────────────

def test_restland_theme_only_for_restland(rl, monkeypatch):
    from app.models.program_models import OutreachProgram
    db, org = rl["db"], rl["org"]
    prog = db.query(OutreachProgram).filter_by(organization_id=org.id).one()
    assert program_profile.profile(db, prog)["theme"] is None          # funeral industry alone never themes
    monkeypatch.setitem(program_profile.THEME_BY_ORG, org.id, "restland")
    assert program_profile.profile(db, prog)["theme"] == "restland"


def test_staff_seat_gets_restland_name_not_sci(client, rl, monkeypatch):
    monkeypatch.setitem(program_profile.THEME_BY_ORG, rl["org"].id, "restland")
    body = client.get("/program/status", headers=_h(rl["db"], rl["advisor"])).json()
    assert body["profile"]["short_name"] == "Restland" and body["profile"]["theme"] == "restland"


def test_family_says_ai_setter_is_off(client, rl):
    f = client.get("/program/command/family/%s" % rl["leads"][1].id, headers=_h(rl["db"], rl["admin"])).json()
    assert f["program_ai_mode"] == "off"


def test_ledger_ignores_rows_of_leads_outside_the_population(rl):
    """Same population as before the SQL change: a lead with no program record,
    a test lead and a demo lead contribute no rows, even with messages."""
    from app.models.models import Message
    from app.services.sci_staging_demo import DEMO_SOURCE
    db, org = rl["db"], rl["org"]
    loose = Lead(organization_id=org.id, first_name="No", last_name="Record", phone="+12145550000", status="new")
    demo = Lead(organization_id=org.id, first_name="Demo", last_name="Lead", phone="+12145550001", status="new",
                source=DEMO_SOURCE)
    db.add_all([loose, demo])
    db.commit()
    db.add(ProgramSourceRecord(organization_id=org.id, source_lead_id="demo-1", raw_json="{}", lead_id=demo.id,
                               location_status="mapped"))
    for l in (loose, demo, rl["test"], rl["leads"][0]):
        db.add(Message(lead_id=l.id, sender_id=rl["admin"].id, body="synthetic"))
    db.commit()
    L = sci_analytics.Ledger(db, org.id)
    assert loose.id not in L.families and demo.id not in L.families and rl["test"].id not in L.families
    assert L.contacted_ever == {rl["leads"][0].id}
