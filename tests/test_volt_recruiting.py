# -*- coding: utf-8 -*-
"""VOLT Marketing recruiting program (#535) - the release verification list as tests.

Synthetic people only (example.test). Dangelo Simmons is the acceptance-test
fixture recruiter and exists only inside these tests.
"""
import itertools
import json
import uuid

import pytest

from app.models.models import Organization, User
from app.models.sales_models import Membership
from app.models.volt_models import VoltApplication, VoltAuditEvent, VoltProgram, VoltProject, VoltRep
from app.services.auth_service import create_access_token, hash_password
from app.services.volt import seed

SLUG = "volt-marketing"
_SEQ = itertools.count(1)
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def H(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


def _user(db, role, org=None, member_role=None):
    u = User(organization_id=None, email="volt-staff-%d@example.com" % next(_SEQ), full_name="Staff %d" % next(_SEQ),
             password_hash=hash_password("Pass12345!"), role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    if org is not None and member_role:
        db.add(Membership(user_id=u.id, scope_type="customer_org", scope_id=org.id, role=member_role, is_active=True))
        db.commit()
    return u


def person(first="Pat", last="Applicant", **kw):
    n = next(_SEQ)
    body = {"first_name": first, "last_name": last, "email": "applicant-%d-%s@example.test" % (n, uuid.uuid4().hex[:5]),
            "phone": "(555) 010-%04d" % n, "date_of_birth": "1990-02-28", "password": "Volt-Test-2026!",
            "project_key": "aca"}
    body.update(kw)
    return body


@pytest.fixture()
def volt(db_session, client):
    db = db_session
    res = seed.ensure(db)
    db.commit()
    again = seed.ensure(db)            # idempotent
    db.commit()
    assert again["organization"] is False and again["projects"] == []
    org = db.get(Organization, res["organization_id"])
    god = _user(db, "god_admin")
    staff = _user(db, "advisor", org, "org_admin")
    viewer = _user(db, "advisor", org, "spectator")
    outsider = _user(db, "org_admin")
    # A second, unrelated recruiting tenant.
    other = Organization(name="Other Recruiting", slug="other-rec-%s" % uuid.uuid4().hex[:6], plan="enterprise",
                         is_active=True, enabled_features=json.dumps(["volt_recruiting"]))
    db.add(other)
    db.flush()
    db.add(VoltProgram(organization_id=other.id, slug="other-recruiting", name="Other", profile_required_docs="[]"))
    db.add(VoltProject(organization_id=other.id, key="aca", name="Other ACA", status="open",
                       required_docs=json.dumps([{"key": "experience_summary", "label": "Experience summary"}])))
    db.commit()
    other_staff = _user(db, "advisor", other, "org_admin")
    c = client

    def register(**kw):
        b = person(**kw)
        r = c.post("/volt/public/%s/register" % SLUG, json=b)
        assert r.status_code == 200, r.text
        tok = {"Authorization": "Bearer " + r.json()["access_token"]}
        return b, tok, r.json()

    def project(key):
        return db.query(VoltProject).filter(VoltProject.organization_id == org.id, VoltProject.key == key).one()

    return dict(db=db, c=c, org=org, god=god, staff=staff, viewer=viewer, outsider=outsider, other=other,
                other_staff=other_staff, register=register, project=project)


def _upload(c, tok, app_id, key=None, data=PDF, name="experience.pdf"):
    return c.post("/volt/rep/%s/documents" % SLUG, headers=tok,
                  data={"doc_key": "experience_summary", "upload_key": key or uuid.uuid4().hex,
                        "application_id": app_id or ""},
                  files={"file": (name, data, "application/pdf")})


def _complete(c, tok, app_id):
    assert c.put("/volt/rep/%s/applications/%s/answers" % (SLUG, app_id), headers=tok,
                 json={"answers": {"experience": "Two years of community outreach."}}).status_code == 200
    assert _upload(c, tok, app_id).status_code == 200
    r = c.post("/volt/rep/%s/applications/%s/submit" % (SLUG, app_id), headers=tok)
    assert r.status_code == 200 and r.json()["status"] == "submitted", r.text


# ── 1. Fresh applicant: two open projects, one serial, draft, resume ─────────

def test_public_portal_two_open_projects_and_midterms_closed(volt):
    r = volt["c"].get("/volt/public/%s" % SLUG).json()
    assert [p["name"] for p in r["open_projects"]] == ["ACA (Obamacare) Health Insurance", "Fiber Internet Outreach"]
    assert [p["name"] for p in r["past_projects"]] == ["Midterms Flyer Distribution"]
    assert r["past_projects"][0]["recruiting"] is False


def test_register_serial_draft_signin_resume(volt):
    c, db = volt["c"], volt["db"]
    body, tok, out = volt["register"](first="Riley", last="Lane")
    serial = out["rep"]["rep_id"]
    assert out["rep"]["rep_code"] == "REP-%d" % (serial + 1000)
    rep = db.get(VoltRep, serial)
    assert rep.date_of_birth.isoformat() == "1990-02-28" and rep.is_test is True
    app_id = out["application_id"]
    assert c.put("/volt/rep/%s/applications/%s/answers" % (SLUG, app_id), headers=tok,
                 json={"answers": {"experience": "Half done"}}).status_code == 200
    # Sign back in from a new device.
    r = c.post("/volt/public/%s/login" % SLUG, json={"email": body["email"].upper(), "password": body["password"]})
    assert r.status_code == 200 and r.json()["has_profile"] is True and r.json()["rep"]["rep_id"] == serial
    t2 = {"Authorization": "Bearer " + r.json()["access_token"]}
    me = c.get("/volt/rep/%s/me" % SLUG, headers=t2).json()
    a = me["applications"][0]
    assert a["status"] == "draft" and a["answers"]["experience"] == "Half done"
    assert a["next_action"]["label"] == "Finish your application"
    assert "Upload: Experience summary" in a["next_action"]["detail"]
    assert me["profile"]["date_of_birth"] == "1990-02-28"


def test_field_level_errors(volt):
    r = volt["c"].post("/volt/public/%s/register" % SLUG,
                       json=person(first="", last="", email="nope", phone="12", date_of_birth="2999-01-01"))
    assert r.status_code == 422
    f = r.json()["detail"]["fields"]
    assert set(f) >= {"first_name", "last_name", "email", "phone", "date_of_birth"}


def test_existing_email_routes_to_signin_and_changes_nothing(volt):
    c, db = volt["c"], volt["db"]
    body, tok, out = volt["register"](first="Same", last="Person")
    again = dict(person(first="Other", last="Name"), email=body["email"])
    r = c.post("/volt/public/%s/register" % SLUG, json=again)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "account_exists"
    rep = db.get(VoltRep, out["rep"]["rep_id"])
    assert rep.first_name == "Same" and db.query(VoltRep).filter(VoltRep.search_name == "other name").count() == 0


def test_matching_name_alone_never_merges(volt):
    _, _, a = volt["register"](first="Chris", last="Twin")
    _, _, b = volt["register"](first="Chris", last="Twin")
    assert a["rep"]["rep_id"] != b["rep"]["rep_id"]


# ── 2. Referrals: click-to-confirm ───────────────────────────────────────────

def test_dangelo_search_select_submit_saves_id(volt):
    c, db = volt["c"], volt["db"]
    _, _, d = volt["register"](first="Dangelo", last="Simmons")
    dangelo = d["rep"]["rep_id"]
    for q in ("Dangelo", "dangelo", "D'Angelo", "D’Angelo", "  dangelo   simmons ", "Simmons"):
        res = c.get("/volt/public/%s/recruiters" % SLUG, params={"q": q, "test": 1}).json()["results"]
        assert any(x["rep_id"] == dangelo for x in res), q
        row = [x for x in res if x["rep_id"] == dangelo][0]
        assert row == {"rep_id": dangelo, "rep_code": "REP-%d" % (dangelo + 1000), "display_name": "Dangelo Simmons"}
    assert c.get("/volt/public/%s/recruiters" % SLUG, params={"q": "d", "test": 1}).json()["results"] == []
    _, tok, out = volt["register"](first="New", last="Recruit", referrer_rep_id=dangelo, referral_source="search")
    a = db.get(VoltApplication, out["application_id"])
    assert a.referrer_serial == dangelo and a.referral_source == "search"
    assert db.get(VoltRep, out["rep"]["rep_id"]).original_recruiter_serial == dangelo
    me = c.get("/volt/rep/%s/me" % SLUG, headers=tok).json()        # reload
    assert me["applications"][0]["referrer"]["rep_id"] == dangelo
    assert me["original_recruiter"]["display_name"] == "Dangelo Simmons"


def test_typed_name_without_selection_earns_nothing(volt):
    c, db = volt["c"], volt["db"]
    volt["register"](first="Dangelo", last="Simmons")
    # A typed name is not a field the server reads for credit.
    _, _, out = volt["register"](first="Typed", last="Only", referral_name="Dangelo", referrer="Dangelo")
    a = db.get(VoltApplication, out["application_id"])
    assert a.referrer_serial is None and db.get(VoltRep, out["rep"]["rep_id"]).original_recruiter_serial is None
    r = c.post("/volt/public/%s/register" % SLUG, json=person(referrer_rep_id="Dangelo"))
    assert r.status_code == 422 and "referrer" in r.json()["detail"]["fields"]


def test_duplicate_names_are_separate_results(volt):
    c = volt["c"]
    _, _, a = volt["register"](first="Dangelo", last="Simmons")
    _, _, b = volt["register"](first="Dangelo", last="Simmons")
    res = c.get("/volt/public/%s/recruiters" % SLUG, params={"q": "dangelo", "test": 1}).json()["results"]
    ids = {x["rep_id"] for x in res}
    assert {a["rep"]["rep_id"], b["rep"]["rep_id"]} <= ids
    assert len({x["rep_code"] for x in res}) == len(res)
    for x in res:
        assert set(x) == {"rep_id", "rep_code", "display_name"}        # nothing private


def test_test_records_hidden_from_public_directory_by_default(volt):
    volt["register"](first="Hidden", last="Tester")
    assert volt["c"].get("/volt/public/%s/recruiters" % SLUG, params={"q": "hidden"}).json()["results"] == []


def test_invites_invalid_inactive_other_tenant_and_self(volt):
    c, db = volt["c"], volt["db"]
    _, tok, d = volt["register"](first="Dangelo", last="Simmons")
    code = d["rep"]["rep_code"]
    ok = c.get("/volt/public/%s/invite" % SLUG, params={"ref": code, "project": "fiber"}).json()
    assert ok["status"] == "valid" and ok["recruiter"]["rep_id"] == d["rep"]["rep_id"] and ok["project"]["key"] == "fiber"
    assert c.get("/volt/public/%s/invite" % SLUG, params={"ref": "REP-999999"}).json()["status"] == "invalid"
    assert c.get("/volt/public/%s/invite" % SLUG, params={"ref": "garbage"}).json()["status"] == "invalid"
    rep = db.get(VoltRep, d["rep"]["rep_id"])
    rep.recruiter_active = False
    db.commit()
    assert c.get("/volt/public/%s/invite" % SLUG, params={"ref": code}).json()["status"] == "inactive"
    r = c.post("/volt/public/%s/register" % SLUG, json=person(referrer_rep_id=rep.serial, referral_source="invite"))
    assert r.status_code == 422
    rep.recruiter_active = True
    db.commit()
    # Out of scope: a recruiter in another tenant is not a recruiter here.
    other_rep = VoltRep(organization_id=volt["other"].id, user_id=volt["other_staff"].id, first_name="Out",
                        last_name="Scope", search_name="out scope")
    db.add(other_rep)
    db.commit()
    assert c.get("/volt/public/%s/invite" % SLUG, params={"ref": "REP-%d" % (other_rep.serial + 1000)}).json()["status"] == "invalid"
    assert c.post("/volt/public/%s/register" % SLUG, json=person(referrer_rep_id=other_rep.serial)).status_code == 422
    # Self-referral on a second project.
    r = c.post("/volt/rep/%s/applications" % SLUG, headers=tok, json={"project_key": "fiber", "referrer_rep_id": rep.serial})
    assert r.status_code == 422 and "yourself" in json.dumps(r.json())
    assert c.get("/volt/rep/%s/recruiters" % SLUG, headers=tok, params={"q": "dangelo"}).json()["results"] == []


# ── 3. Second project keeps serial and original recruiter; no duplicates ─────

def test_second_project_same_serial_original_recruiter_kept(volt):
    c, db = volt["c"], volt["db"]
    _, _, d = volt["register"](first="Dangelo", last="Simmons")
    _, _, m = volt["register"](first="Maria", last="Reed")
    _, tok, out = volt["register"](first="Two", last="Projects", referrer_rep_id=d["rep"]["rep_id"])
    serial = out["rep"]["rep_id"]
    for _ in range(3):   # double click / retry / second tab
        r = c.post("/volt/rep/%s/applications" % SLUG, headers=tok,
                   json={"project_key": "fiber", "referrer_rep_id": m["rep"]["rep_id"], "referral_source": "invite"})
        assert r.status_code == 200
    apps = db.query(VoltApplication).filter(VoltApplication.rep_serial == serial).all()
    assert len(apps) == 2 and {a.project_id for a in apps} == {volt["project"]("aca").id, volt["project"]("fiber").id}
    fiber = [a for a in apps if a.project_id == volt["project"]("fiber").id][0]
    assert fiber.referrer_serial == m["rep"]["rep_id"]
    assert db.get(VoltRep, serial).original_recruiter_serial == d["rep"]["rep_id"]
    # Repeat registration of the same project does not create another.
    assert c.post("/volt/rep/%s/applications" % SLUG, headers=tok, json={"project_key": "aca"}).json()["id"] == out["application_id"]


def test_concurrent_insert_race_returns_the_winner(volt, monkeypatch):
    """Two tabs: the other tab's row commits between our check and our insert.
    The unique constraint decides, and the request returns the winner's row."""
    from app.services.volt import core
    c, db = volt["c"], volt["db"]
    _, tok, out = volt["register"](first="Race", last="Tabs")
    rep = db.get(VoltRep, out["rep"]["rep_id"])
    fiber = volt["project"]("fiber")
    winner = VoltApplication(organization_id=rep.organization_id, rep_serial=rep.serial, project_id=fiber.id,
                             status="draft")
    db.add(winner)
    db.commit()
    monkeypatch.setattr(core, "_existing", lambda *a, **k: None)     # the check ran before the other tab committed
    r = c.post("/volt/rep/%s/applications" % SLUG, headers=tok, json={"project_key": "fiber"})
    assert r.status_code == 200 and r.json()["id"] == winner.id
    assert db.query(VoltApplication).filter(VoltApplication.rep_serial == rep.serial,
                                            VoltApplication.project_id == fiber.id).count() == 1


def test_submit_retry_is_idempotent(volt):
    c = volt["c"]
    _, tok, out = volt["register"]()
    _complete(c, tok, out["application_id"])
    for _ in range(2):
        r = c.post("/volt/rep/%s/applications/%s/submit" % (SLUG, out["application_id"]), headers=tok)
        assert r.status_code == 200 and r.json()["status"] == "submitted"


# ── 4. Midterms closed on the server ─────────────────────────────────────────

def test_midterms_rejected_through_every_door(volt):
    c = volt["c"]
    r = c.post("/volt/public/%s/register" % SLUG, json=person(project_key="midterms"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "recruiting_closed"
    _, tok, _ = volt["register"]()
    r = c.post("/volt/rep/%s/applications" % SLUG, headers=tok, json={"project_key": "midterms"})
    assert r.status_code == 409


def test_staff_close_project_without_deploy_and_history_kept(volt):
    c, db = volt["c"], volt["db"]
    _, tok, out = volt["register"]()
    aca = volt["project"]("aca")
    sh = H(db, volt["staff"])
    assert c.patch("/volt/ops/%s/projects/%s" % (volt["org"].id, aca.id), headers=sh, json={"status": "closed"}).json()["status"] == "closed"
    r = c.post("/volt/rep/%s/applications/%s/submit" % (SLUG, out["application_id"]), headers=tok)
    assert r.status_code == 409          # a draft of a closed project cannot be submitted
    assert c.get("/volt/ops/%s/applications" % volt["org"].id, headers=sh, params={"include_test": 1}).json()["count"] == 1
    seed.ensure(db)
    db.commit()
    db.refresh(aca)
    assert aca.status == "closed"        # a redeploy never re-opens it


# ── 5. Documents ─────────────────────────────────────────────────────────────

def test_document_upload_review_replace_accept_and_isolation(volt):
    c, db = volt["c"], volt["db"]
    org = volt["org"].id
    _, tok, out = volt["register"]()
    app_id = out["application_id"]
    key = uuid.uuid4().hex
    r1 = _upload(c, tok, app_id, key=key)
    r2 = _upload(c, tok, app_id, key=key)                       # interrupted-and-retried
    assert r1.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    assert _upload(c, tok, app_id, data=b"MZ\x90\x00not a document", name="x.exe").status_code == 400
    assert c.put("/volt/rep/%s/applications/%s/answers" % (SLUG, app_id), headers=tok,
                 json={"answers": {"experience": "Yes"}}).status_code == 200
    assert c.post("/volt/rep/%s/applications/%s/submit" % (SLUG, app_id), headers=tok).status_code == 200
    doc_id = r1.json()["id"]
    sh = H(db, volt["staff"])
    f = c.get("/volt/ops/%s/applications/%s" % (org, app_id), headers=sh).json()
    assert f["profile"]["date_of_birth"] == "1990-02-28" and f["document_versions"][0]["id"] == doc_id
    got = c.get("/volt/ops/%s/documents/%s/file" % (org, doc_id), headers=sh)
    assert got.status_code == 200 and got.content == PDF and "no-store" in got.headers["cache-control"]
    assert c.post("/volt/ops/%s/documents/%s/review" % (org, doc_id), headers=sh, json={"status": "replace"}).status_code == 422
    assert c.post("/volt/ops/%s/documents/%s/review" % (org, doc_id), headers=sh,
                  json={"status": "replace", "reason": "Please upload a signed copy."}).status_code == 200
    assert c.post("/volt/ops/%s/applications/%s/status" % (org, app_id), headers=sh,
                  json={"status": "needs_update", "reason": "Replace your experience summary."}).status_code == 200
    me = c.get("/volt/rep/%s/me" % SLUG, headers=tok).json()
    d = me["applications"][0]["documents"][0]
    assert d["state"] == "replace" and d["reason"] == "Please upload a signed copy."
    assert me["applications"][0]["next_action"]["detail"] == "Replace your experience summary."
    assert me["document_requests"][0]["state"] == "replace"
    v2 = _upload(c, tok, app_id, data=PDF + b"v2")
    assert v2.json()["version"] == 2
    assert c.post("/volt/rep/%s/applications/%s/submit" % (SLUG, app_id), headers=tok).json()["status"] == "submitted"
    assert c.post("/volt/ops/%s/documents/%s/review" % (org, doc_id), headers=sh, json={"status": "accepted"}).status_code == 409
    assert c.post("/volt/ops/%s/documents/%s/review" % (org, v2.json()["id"]), headers=sh, json={"status": "accepted"}).status_code == 200
    hist = c.get("/volt/ops/%s/applications/%s" % (org, app_id), headers=sh).json()
    assert [v["version"] for v in hist["document_versions"]] == [2, 1]
    assert any(h["action"] == "reviewed" and h["new"] == "replace" for h in hist["history"])
    # Another applicant, another tenant's staff, an outsider, nobody.
    _, tok2, _ = volt["register"]()
    assert c.get("/volt/rep/%s/documents/%s/file" % (SLUG, doc_id), headers=tok2).status_code == 404
    assert c.get("/volt/ops/%s/documents/%s/file" % (org, doc_id), headers=H(db, volt["other_staff"])).status_code == 403
    assert c.get("/volt/ops/%s/documents/%s/file" % (volt["other"].id, doc_id), headers=H(db, volt["other_staff"])).status_code == 404
    assert c.get("/volt/ops/%s/documents/%s/file" % (org, doc_id), headers=H(db, volt["outsider"])).status_code == 403
    assert c.get("/volt/ops/%s/documents/%s/file" % (org, doc_id)).status_code == 401
    assert c.get("/volt/rep/%s/documents/%s/file" % (SLUG, doc_id), headers=tok).status_code == 200


# ── 6. Zoom sessions ─────────────────────────────────────────────────────────

def test_zoom_session_lifecycle_and_access(volt):
    c, db = volt["c"], volt["db"]
    org = volt["org"].id
    _, tok, a = volt["register"](first="Invited", last="One")
    _, tok2, _ = volt["register"](first="Not", last="Invited")
    sh = H(db, volt["staff"])
    base = {"kind": "orientation", "project_id": volt["project"]("aca").id, "starts_local": "2026-12-01T18:30",
            "timezone": "America/Chicago", "duration_minutes": 45, "instructions": "Bring a pen.",
            "rep_ids": [a["rep"]["rep_id"]]}
    for bad in ("http://zoom.us/j/12345678901", "https://zoom.us.evil.com/j/12345678901",
                "https://us02web.zoom.us/s/12345678901?zak=abc", "https://zoom.us/j/12345678901?zak=abc",
                "https://example.com/j/12345678901"):
        r = c.post("/volt/ops/%s/sessions" % org, headers=sh, json=dict(base, join_url=bad))
        assert r.status_code == 422, bad
    link = "https://us02web.zoom.us/j/12345678901?pwd=abcDEF"
    r = c.post("/volt/ops/%s/sessions" % org, headers=sh, json=dict(base, join_url=link))
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["starts_at"] == "2026-12-02T00:30:00Z" and s["starts_local"] == "2026-12-01T18:30"
    mine = c.get("/volt/rep/%s/me" % SLUG, headers=tok).json()["sessions"]
    assert mine[0]["join_url"] == link and mine[0]["timezone"] == "America/Chicago"
    assert c.get("/volt/rep/%s/me" % SLUG, headers=tok2).json()["sessions"] == []
    assert c.get("/volt/ops/%s/sessions" % org, headers=H(db, volt["outsider"])).status_code == 403
    assert c.get("/volt/ops/%s/sessions" % org, headers=tok).status_code == 403          # applicant fence
    r = c.patch("/volt/ops/%s/sessions/%s" % (org, s["id"]), headers=sh, json={"starts_local": "2026-12-03T09:00"})
    assert r.json()["starts_at"] == "2026-12-03T15:00:00Z"
    assert c.get("/volt/rep/%s/me" % SLUG, headers=tok).json()["sessions"][0]["starts_at"] == "2026-12-03T15:00:00Z"
    r = c.post("/volt/ops/%s/sessions/%s/attendance" % (org, s["id"]), headers=sh,
               json={"rep_id": a["rep"]["rep_id"], "attendance": "attended"})
    assert r.json()["attendees"][0]["attendance"] == "attended"
    assert c.post("/volt/ops/%s/sessions/%s/cancel" % (org, s["id"]), headers=sh).json()["status"] == "canceled"
    after = c.get("/volt/rep/%s/me" % SLUG, headers=tok).json()["sessions"][0]
    assert after["status"] == "canceled" and after["join_url"] is None
    assert c.patch("/volt/ops/%s/sessions/%s" % (org, s["id"]), headers=sh, json={"duration_minutes": 30}).status_code == 409


# ── 7. Started, leaderboard, report and CSV reconcile ────────────────────────

def test_started_leaderboard_report_csv_reconcile_no_double_count(volt):
    c, db = volt["c"], volt["db"]
    org = volt["org"].id
    _, _, d = volt["register"](first="Dangelo", last="Simmons")
    dangelo = d["rep"]["rep_id"]
    _, tok, out = volt["register"](first="Started", last="Twice", referrer_rep_id=dangelo)
    fiber = c.post("/volt/rep/%s/applications" % SLUG, headers=tok,
                   json={"project_key": "fiber", "referrer_rep_id": dangelo}).json()
    sh = H(db, volt["staff"])
    for app_id in (out["application_id"], fiber["id"]):
        _complete(c, tok, app_id)
        assert c.post("/volt/ops/%s/applications/%s/status" % (org, app_id), headers=sh, json={"status": "started"}).status_code == 409
        assert c.post("/volt/ops/%s/applications/%s/status" % (org, app_id), headers=sh, json={"status": "approved"}).status_code == 200
        for _ in range(2):    # repeating the action
            assert c.post("/volt/ops/%s/applications/%s/status" % (org, app_id), headers=sh,
                          json={"status": "started"}).status_code == 200
    aca = volt["project"]("aca").id
    q = {"include_test": 1}
    home = c.get("/volt/ops/%s" % org, headers=sh, params=dict(q, project_id=aca)).json()
    assert home["overview"]["starts"] == 1
    row = [r for r in home["leaderboard"]["rows"] if r["rep_id"] == dangelo][0]
    assert row["starts"] == 1 and row["rank"] == 1
    every = c.get("/volt/ops/%s" % org, headers=sh, params=q).json()
    assert every["overview"]["starts"] == 2 and every["overview"]["unique_people"] == 2
    row = [r for r in every["leaderboard"]["rows"] if r["rep_id"] == dangelo][0]
    assert row["starts"] == 1, "a person who started two projects counts once in the all-project view"
    lst = c.get("/volt/ops/%s/applications" % org, headers=sh, params=dict(q, project_id=aca, reached="started")).json()
    assert lst["count"] == home["overview"]["starts"]
    csv_text = c.get("/volt/ops/%s/export.csv" % org, headers=sh, params=dict(q, project_id=aca, reached="started")).text
    lines = [x for x in csv_text.strip().splitlines()]
    assert len(lines) - 1 == lst["count"] and "REP-%d" % (dangelo + 1000) in lines[1]
    assert "1990" not in csv_text and "@example.test" not in csv_text and "/documents/" not in csv_text
    # Test records are excluded unless asked for.
    assert c.get("/volt/ops/%s" % org, headers=sh).json()["overview"]["starts"] == 0
    # Rep dashboard leaderboard: names and counts only.
    b = c.get("/volt/rep/%s/leaderboard" % SLUG, headers=tok).json()
    assert all(set(r) <= {"rep_id", "rep_code", "display_name", "registrations", "submissions", "approvals", "starts", "rank"} for r in b["rows"])


def test_needs_update_requires_reason_and_audit_corrections(volt):
    c, db = volt["c"], volt["db"]
    org = volt["org"].id
    _, _, d = volt["register"](first="Dangelo", last="Simmons")
    _, _, m = volt["register"](first="Maria", last="Reed")
    _, tok, out = volt["register"](referrer_rep_id=d["rep"]["rep_id"])
    _complete(c, tok, out["application_id"])
    sh = H(db, volt["staff"])
    assert c.post("/volt/ops/%s/applications/%s/status" % (org, out["application_id"]), headers=sh,
                  json={"status": "needs_update"}).status_code == 422
    assert c.post("/volt/ops/%s/applications/%s/referral" % (org, out["application_id"]), headers=sh,
                  json={"referrer_rep_id": m["rep"]["rep_id"]}).status_code == 422
    r = c.post("/volt/ops/%s/applications/%s/referral" % (org, out["application_id"]), headers=sh,
               json={"referrer_rep_id": m["rep"]["rep_id"], "reason": "Applicant clicked the wrong recruiter."})
    assert r.json()["referrer"]["rep_id"] == m["rep"]["rep_id"]
    ev = db.query(VoltAuditEvent).filter(VoltAuditEvent.action == "referral_corrected").one()
    assert ev.old_value == str(d["rep"]["rep_id"]) and ev.new_value == str(m["rep"]["rep_id"])
    assert ev.actor_user_id == volt["staff"].id and ev.reason
    # The original recruiter is a different fact and did not move.
    assert db.get(VoltRep, out["rep"]["rep_id"]).original_recruiter_serial == d["rep"]["rep_id"]


# ── 8. Program Access and isolation ──────────────────────────────────────────

def test_program_access_cards(volt):
    c, db = volt["c"], volt["db"]
    for who, access in ((volt["god"], "owner"), (volt["staff"], "staff"), (volt["viewer"], "viewer")):
        ctx = c.get("/auth/my-contexts", headers=H(db, who)).json()
        cards = [x for x in ctx["volt_contexts"] if x["organization_id"] == volt["org"].id]
        assert cards and cards[0]["access"] == access and cards[0]["path"] == "/volt-ops/%s" % volt["org"].id
    assert c.get("/auth/my-contexts", headers=H(db, volt["outsider"])).json()["volt_contexts"] == []
    ids = [x["organization_id"] for x in c.get("/auth/my-contexts", headers=H(db, volt["other_staff"])).json()["volt_contexts"]]
    assert ids == [volt["other"].id]


def test_unentitled_and_applicants_cannot_enter_operations(volt):
    c, db = volt["c"], volt["db"]
    org = volt["org"].id
    _, tok, out = volt["register"]()
    _, tok2, out2 = volt["register"]()
    for h in (H(db, volt["outsider"]), H(db, volt["other_staff"]), tok):
        assert c.get("/volt/ops/%s" % org, headers=h).status_code == 403
        assert c.get("/volt/ops/%s/applications" % org, headers=h).status_code == 403
        assert c.get("/volt/ops/%s/export.csv" % org, headers=h).status_code == 403
        assert c.get("/volt/ops/%s/applications/%s" % (org, out["application_id"]), headers=h).status_code == 403
    # Applicant login is fenced away from the rest of the platform.
    assert c.get("/auth/my-contexts", headers=tok).status_code == 403
    # A real protected route: a control account with a workspace reaches it,
    # the VOLT applicant is refused by the fence (403, not a 404 for both).
    ctrl_org = Organization(name="Control Org", slug="ctrl-%s" % uuid.uuid4().hex[:6], plan="enterprise", is_active=True)
    db.add(ctrl_org)
    db.commit()
    ctrl = User(organization_id=ctrl_org.id, email="control-%d@example.com" % next(_SEQ), full_name="Control",
                password_hash=hash_password("Pass12345!"), role="org_admin", is_active=True, must_change_password=False)
    db.add(ctrl)
    db.commit()
    assert c.get("/campaigns", headers=H(db, ctrl)).status_code == 200
    r = c.get("/campaigns", headers=tok)
    assert r.status_code == 403 and "VOLT" in r.json()["detail"]
    assert c.get("/auth/my-contexts", headers=H(db, ctrl)).status_code == 200
    assert c.get("/volt/ops/%s/sessions" % org, headers=tok).status_code == 403
    # Another applicant's application is not reachable by id.
    assert c.get("/volt/rep/%s/applications/%s" % (SLUG, out2["application_id"]), headers=tok).status_code == 404
    # Viewer reads but never writes.
    vh = H(db, volt["viewer"])
    assert c.get("/volt/ops/%s" % org, headers=vh).status_code == 200
    assert c.post("/volt/ops/%s/applications/%s/status" % (org, out["application_id"]), headers=vh,
                  json={"status": "approved"}).status_code == 403
    # Owner oversight works and cannot be gained by a header.
    assert c.get("/volt/ops/%s" % org, headers=H(db, volt["god"])).json()["access"] == "owner"
    spoof = dict(H(db, volt["outsider"]), **{"X-Workspace-Id": org, "X-Org-Override": org})
    assert c.get("/volt/ops/%s" % org, headers=spoof).status_code == 403


def test_existing_platform_account_can_start_profile(volt):
    c, db = volt["c"], volt["db"]
    u = volt["outsider"]
    r = c.post("/volt/public/%s/login" % SLUG, json={"email": u.email, "password": "Pass12345!"})
    assert r.status_code == 200 and r.json()["has_profile"] is False
    t = {"Authorization": "Bearer " + r.json()["access_token"]}
    body = person()
    body.pop("email")
    r = c.post("/volt/rep/%s/profile" % SLUG, headers=t, json=body)
    assert r.status_code == 200
    assert c.post("/volt/rep/%s/profile" % SLUG, headers=t, json=body).status_code == 409


def test_purge_test_records_owner_only(volt):
    c, db = volt["c"], volt["db"]
    org = volt["org"].id
    _, tok, out = volt["register"]()
    _upload(c, tok, out["application_id"])
    sh = H(db, volt["staff"])
    sess = c.post("/volt/ops/%s/sessions" % org, headers=sh, json={
        "kind": "interview", "project_id": volt["project"]("aca").id, "starts_local": "2026-12-01T10:00",
        "timezone": "America/Chicago", "join_url": "https://zoom.us/j/12345678901", "rep_ids": [out["rep"]["rep_id"]]}).json()
    assert c.post("/volt/ops/%s/test-records/purge" % org, headers=sh).status_code == 403
    r = c.post("/volt/ops/%s/test-records/purge" % org, headers=H(db, volt["god"]))
    assert r.status_code == 200 and r.json()["reps"] >= 1 and r.json()["documents"] >= 1 and r.json()["sessions"] == 1
    from app.models.volt_models import VoltSession
    assert db.get(VoltSession, sess["id"]) is None
    assert db.query(VoltRep).filter(VoltRep.organization_id == org, VoltRep.is_test.is_(True)).count() == 0
