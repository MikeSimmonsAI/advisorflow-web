"""SCI x the Intelligent Lead Importer.

Steps 1-5 (Upload, Map, Analyze, Review) run on the universal importer's
engine through /program/intake; the final write is SCI's own loader
(/program/leads/import/batches/{id}/sci-preview and /load). The CRM commit
never runs for an SCI batch. All data is synthetic; the mobile/landline line
check is mocked, nothing reaches a provider.
"""
import io
import json

import pandas as pd
import pytest

from app.models.import_models import ImportBatch
from app.models.intake_models import OrgContact
from app.models.models import CadenceState, EmailMessage, Lead, Message, Organization, User
from app.models.program_models import ProgramSourceRecord
from app.services.auth_service import create_access_token, hash_password
from app.services.programs import setup
from app.services.workspace_access import grant_workspace_membership
from app.services.programs.line_check import run_for_org as REAL_RUN_FOR_ORG

HEADER = ["Lead ID", "Lead Owner: Manager", "Lead Owner", "First Name", "Last Name", "Email",
          "Lead Status", "Last Activity", "Last Activity Date", "Phone", "Primary Campaign",
          "Campaign Channel", "Create Date", "Location Friendly Name"]
EG, SH = "Eastern Gate Memorial Gardens", "Striffler-Hamby Mortuary"
CAMPAIGN = "Direct Mail> Survey> General"


def _row(lid, first, last, email, phone, loc, status="Qualified"):
    return [lid, "Mgr", "Owner", first, last, email, status, "Call", "1/5/2026", phone,
            CAMPAIGN, "Direct Mail", "12/27/2025", loc]


def _csv(rows, header=HEADER):
    buf = io.StringIO()
    import csv
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


# Families already in SCI before the new file arrives.
EXISTING = [
    _row("E001", "Ann", "Optedout", "ann.e@example.com", "2145550301", EG),   # will be DNC
    _row("E002", "Bob", "Heldfam", "bob.e@example.com", "2145550302", "Nowhere Chapel"),  # held
    _row("E003", "Cara", "Existing", "cara.e@example.com", "2145550303", SH),
]
# The new file: clean rows, a misspelled location, missing / invalid phone and
# email, the three existing families again, and a row the reviewer skips.
NEW = [
    _row("N001", "Dana", "Newfam", "dana.n@example.com", "2145550401", EG),
    _row("N002", "Eli", "Newfam", "eli.n@example.com", "2145550402", SH),
    _row("N003", "Fay", "Typo", "fay.n@example.com", "2145550403", "Eastern Gait Memorial"),
    _row("N004", "Gus", "Nophone", "gus.n@example.com", "", SH),
    _row("N005", "Hal", "Badphone", "not-an-email", "555", EG),
    _row("E001", "Ann", "Optedout", "ann.e@example.com", "2145550301", EG),
    _row("E002", "Bob", "Heldfam", "bob.e@example.com", "2145550302", "Nowhere Chapel"),
    _row("E003", "Cara", "Existing", "cara.e@example.com", "2145550303", SH),
    _row("N008", "Ivy", "Skipme", "ivy.n@example.com", "2145550408", EG),
]


def _h(db, user, org_id=None):
    h = {"Authorization": "Bearer %s" % create_access_token(user, db)}
    if org_id:
        h["X-Workspace-Id"] = org_id
    return h


def _user(db, email, *, org_id=None, role="advisor"):
    u = User(organization_id=org_id, email=email, password_hash=hash_password("Pass12345!"),
             full_name=email.split("@")[0].title(), role=role, is_active=True,
             must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _make_program(db, org, admin_email):
    admin = _user(db, admin_email, org_id=org.id, role="org_admin")
    setup.ensure_program(db, org, name="Service Corporation International",
                         primary_contact_name="Kerry Allan")
    setup.ensure_locations(db, org, admin, [EG, SH])
    setup.ensure_campaign_families(db, org)
    db.commit()
    return admin


@pytest.fixture(autouse=True)
def _no_line_check(monkeypatch):
    calls = []
    from app.services.programs import line_check

    def fake(db, org_id, limit=100, adapter=None, **kw):
        calls.append(org_id)
        return {"checked": 0, "results": {}, "remaining": 0, "mocked": True}
    monkeypatch.setattr(line_check, "run_for_org", fake)
    monkeypatch.setenv("INTAKE_INLINE_JOBS", "1")
    return calls


@pytest.fixture()
def sci(client, db_session, sample_org):
    db = db_session
    admin = _make_program(db, sample_org, "sci-imp-admin@example.com")
    ah = _h(db, admin)
    # Load the existing families through SCI's own path.
    r = client.post("/program/leads/import", headers=ah,
                    files={"file": ("existing.csv", _csv(EXISTING), "text/csv")},
                    data={"apply": "true"})
    assert r.status_code == 200, r.text
    assert r.json()["promotion"]["created"] == 2          # E001, E003; E002 held
    e1 = db.query(Lead).filter_by(organization_id=sample_org.id, first_name="Ann").one()
    e1.status = "dnc"                                       # the family opted out
    e1.sms_consent = False
    db.commit()
    mgr = _user(db, "sci-imp-mgr@example.com")              # program-only login
    grant_workspace_membership(db, mgr.id, sample_org.id, role="manager", check_capacity=False)
    db.commit()
    return {"db": db, "org": sample_org, "admin": admin, "ah": ah,
            "mgr": mgr, "mh": _h(db, mgr, sample_org.id), "dnc_lead": e1}


def _upload(client, h, content, name="sci-new.csv", ctype="text/csv"):
    return client.post("/program/intake/batches", headers=h,
                       files={"file": (name, content, ctype)}, data={"list_name": "October list"})


def _counts(db, org_id):
    return {
        "leads": db.query(Lead).filter_by(organization_id=org_id).count(),
        "contacts": db.query(OrgContact).filter_by(organization_id=org_id).count(),
        "records": db.query(ProgramSourceRecord).filter_by(organization_id=org_id).count(),
        "messages": db.query(Message).count(),
        "emails": db.query(EmailMessage).count(),
        "cadences": db.query(CadenceState).count(),
    }


def _through_review(client, h, content, name="sci-new.csv", ctype="text/csv", skip=("N008",)):
    r = _upload(client, h, content, name, ctype)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["source_system"] == "program" and b["destination"] == "sci"
    bid = b["id"]
    pv = client.get("/program/intake/batches/%s/preview" % bid, headers=h)
    assert pv.status_code == 200, pv.text
    mapping = {c["header"]: {"kind": c["mapping"]["kind"], "target": c["mapping"]["target"]}
               for c in pv.json()["columns"]}
    r = client.put("/program/intake/batches/%s/mapping" % bid, headers=h, json={"mapping": mapping})
    assert r.status_code == 200, r.text
    r = client.post("/program/intake/batches/%s/analyze" % bid, headers=h)
    assert r.status_code == 202, r.text
    b = client.get("/program/intake/batches/%s" % bid, headers=h).json()
    assert b["status"] == "ready_for_review", b
    rows = client.get("/program/intake/batches/%s/rows?per_page=200" % bid, headers=h).json()["rows"]
    for row in rows:
        if row.get("source_record_id") in skip:
            r = client.patch("/program/intake/batches/%s/rows/%s" % (bid, row["id"]), headers=h,
                             json={"skip": True})
            assert r.status_code == 200, r.text
    rows = client.get("/program/intake/batches/%s/rows?per_page=200" % bid, headers=h).json()["rows"]
    return bid, pv.json(), rows


# ── mapping ─────────────────────────────────────────────────────────────────

def test_sci_headers_get_sci_mapping_and_crm_suggestions_are_unchanged():
    from app.services.intake import fields as F
    sci = F.suggest_mapping(HEADER, profile="sci")
    assert sci["Lead ID"] == {"kind": "standard", "target": "source_record_id", "confidence": "high"}
    assert sci["Location Friendly Name"]["target"] == "location_friendly_name"
    assert sci["Location Friendly Name"]["kind"] == "source"
    assert sci["Primary Campaign"] == {"kind": "source", "target": "primary_campaign", "confidence": "high"}
    assert sci["Lead Status"]["kind"] == "source" and sci["Lead Status"]["target"] == "lead_status"
    assert sci["Last Activity Date"]["target"] == "last_activity_date"
    assert sci["Last Activity"]["kind"] == "source"
    assert sci["First Name"]["target"] == "first_name" and sci["Phone"]["target"] == "phone"
    alt = F.suggest_mapping(["Lead ID", "Location", "First Name"], profile="sci")
    assert alt["Location"]["target"] == "location_friendly_name"
    # A CRM import of the same headers: exactly as before (Lead Status is a
    # classification column there, "Last Activity" claims the date field).
    crm = F.suggest_mapping(HEADER)
    assert crm["Lead Status"]["target"] == "classification"
    assert crm["Last Activity"]["target"] == "last_activity_date"
    assert crm == F.suggest_mapping(HEADER, None)
    assert F.suggest_mapping(["Location"])["Location"]["target"] == "location"


# ── the full flow ───────────────────────────────────────────────────────────

def test_full_flow_program_only_manager(client, sci, _no_line_check):
    db, org, h = sci["db"], sci["org"], sci["mh"]
    before = _counts(db, org.id)
    dnc = sci["dnc_lead"]
    bid, pv, rows = _through_review(client, h, _csv(NEW))

    cols = {c["header"]: c["mapping"] for c in pv["columns"]}
    assert cols["Location Friendly Name"]["target"] == "location_friendly_name"
    assert cols["Lead Status"]["kind"] == "source"
    by_id = {r["source_record_id"]: r for r in rows}
    assert by_id["N001"]["intake_status"] == "ready" and by_id["N002"]["intake_status"] == "ready"
    assert by_id["E001"]["intake_status"] == "blocked"          # DNC family, matched by Lead ID
    assert by_id["E003"]["intake_status"] == "existing_match"
    assert by_id["N008"]["intake_status"] == "skipped"
    # Nothing written to SCI or the CRM yet.
    assert _counts(db, org.id) == before

    # The CRM commit is refused for an SCI batch, on the /intake API too.
    sci["org"].enabled_features = None
    db.commit()
    ah = sci["ah"]
    r = client.post("/intake/batches/%s/commit" % bid, headers=ah, json={"mode": "ready_only",
                    "confirm_organization_name": org.name})
    assert r.status_code == 409 and r.json()["detail"] == "SCI files are loaded through SCI"
    r = client.get("/intake/batches/%s/commit-preview" % bid, headers=ah)
    assert r.status_code == 409 and r.json()["detail"] == "SCI files are loaded through SCI"

    # SCI check: changes nothing.
    r = client.get("/program/leads/import/batches/%s/sci-preview" % bid, headers=h)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["can_load"] is True and p["blockers"] == []
    # #385: every source record goes to SCI; only what a PERSON skipped stays
    # out. The do-not-contact match (E001) is kept and held, never contacted.
    assert p["rows_in_file"] == 9 and p["rows_to_sci"] == 8
    reasons = {x["reason"]: x["rows"] for x in p["excluded"]["by_reason"]}
    assert reasons == {"skipped": 1} and p["excluded"]["total"] == 1
    assert p["existing"] == {"lead_ids": 8, "already_in_sci": 3, "already_live": 2, "on_hold": 1, "new": 5}
    st = p["stage"]
    assert st["source_rows"] == 8 and st["staged"] == 0
    assert st["import_flags"] == {"do_not_contact_match": 1}
    assert st["location_mapped"] == 6 and st["location_review"] == 2
    assert st["bad_phone_rows"] == 1 and st["bad_phones"][0]["row"] == 6     # the file's own row
    assert st["no_phone_rows"] == 1
    assert p["sends_nothing"] is True
    assert _counts(db, org.id) == before

    # STAGE ONLY (the safe default): staging rows, no family made live.
    r = client.post("/program/leads/import/batches/%s/load" % bid, headers=h, json={})
    assert r.status_code == 200, r.text
    rep = r.json()["report"]
    assert rep["mode"] == "stage" and rep["staged"] == 8 and rep["families_created"] == 0
    assert r.json()["batch"]["status"] == "staged" and r.json()["batch"]["commit_mode"] == "program_stage"
    after_stage = _counts(db, org.id)
    assert after_stage["records"] == before["records"] + 5       # N001-N005; E002/E003 updated
    assert after_stage["leads"] == before["leads"] and after_stage["contacts"] == before["contacts"]
    assert _no_line_check == [org.id]                            # only the fixture's load
    # Staging again is a no-op returning the stored report.
    again = client.post("/program/leads/import/batches/%s/load" % bid, headers=h, json={"mode": "stage"})
    assert again.status_code == 200 and again.json()["repeat"] is True

    # LOAD: SCI's own stage -> hold -> promote -> (mocked) line check.
    r = client.post("/program/leads/import/batches/%s/load" % bid, headers=h, json={"mode": "load"})
    assert r.status_code == 200, r.text
    rep = r.json()["report"]
    assert rep["mode"] == "load" and rep["staged"] == 8
    assert rep["families_created"] == 4                          # N001, N002, N004, N005
    assert rep["held"] == 1                                      # N003 (E002 was already held)
    assert rep["already_live"] == 2 and rep["location_review"] == 2
    assert rep["excluded"]["total"] == 1
    assert rep["texts_sent"] == 0 and rep["emails_sent"] == 0 and rep["enrolled"] == 0
    assert rep["line_check"]["mocked"] is True
    b = db.query(ImportBatch).filter_by(id=bid).one()
    db.refresh(b)
    assert b.status == "committed" and b.commit_mode == "program_load" and b.committed_at
    assert b.committed_by_id == sci["mgr"].id
    after = _counts(db, org.id)
    assert after["leads"] == before["leads"] + 4 and after["records"] == before["records"] + 5
    # Nothing sent, nobody enrolled.
    assert after["messages"] == before["messages"] and after["emails"] == before["emails"]
    assert after["cadences"] == before["cadences"]
    recs = {r.source_lead_id: r for r in db.query(ProgramSourceRecord).filter_by(organization_id=org.id)}
    assert recs["N003"].on_hold and "LOCATION REVIEW" in recs["N003"].hold_reason
    assert recs["E002"].on_hold and recs["E002"].lead_id is None   # still held, untouched
    assert recs["E001"].on_hold and "do-not-contact" in recs["E001"].hold_reason   # kept, never contacted
    assert not any(r.approved_to_send_at for r in recs.values() if hasattr(r, "approved_to_send_at"))
    # ORIGINAL VALUES kept as supplied.
    raw = json.loads(recs["N005"].raw_json)
    assert raw["Phone"] == "555" and raw["Email"] == "not-an-email" and raw["Last Activity"] == "Call"
    # The opted-out family is untouched.
    db.refresh(dnc)
    assert dnc.status == "dnc" and dnc.sms_consent is False
    new_leads = db.query(Lead).filter(Lead.organization_id == org.id,
                                      Lead.last_name.in_(["Newfam", "Nophone", "Badphone"])).all()
    assert len(new_leads) == 4 and all(l.sms_consent is False for l in new_leads)

    # Visible in SCI Leads and in the family record.
    lst = client.get("/program/send/contacts?limit=300", headers=h)
    assert lst.status_code == 200, lst.text
    names = {i["source_lead_id"]: i for i in lst.json()["items"]}
    assert {"N001", "N002", "N004", "N005", "E001", "E003"} <= set(names)
    assert "N003" not in names and "N008" not in names
    assert names["N001"]["location"] == EG and names["N001"]["approved"] is False
    fam = client.get("/program/command/family/%s" % names["N001"]["lead_id"], headers=h)
    assert fam.status_code == 200, fam.text

    # A second load returns the stored report and changes nothing.
    again = client.post("/program/leads/import/batches/%s/load" % bid, headers=h, json={"mode": "load"})
    assert again.status_code == 200 and again.json()["repeat"] is True
    assert again.json()["report"]["families_created"] == 4
    assert _counts(db, org.id) == after
    # Audit trail on the batch.
    ev = client.get("/program/intake/batches/%s/audit" % bid, headers=h).json()["events"]
    actions = [e["action"] for e in ev]
    assert "intake.file_uploaded" in actions and "intake.sci_staged" in actions
    assert "intake.sci_loaded" in actions

    # The same file uploaded again -> no duplicates.
    bid2, _pv, _rows = _through_review(client, h, _csv(NEW), skip=())
    r = client.post("/program/leads/import/batches/%s/load" % bid2, headers=h, json={"mode": "load"})
    assert r.status_code == 200, r.text
    assert r.json()["report"]["families_created"] == 1          # only N008 (skipped the first time)
    final = _counts(db, org.id)
    assert final["leads"] == after["leads"] + 1
    assert final["records"] == after["records"] + 1
    assert db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id="N001").count() == 1
    assert final["messages"] == before["messages"] and final["cadences"] == before["cadences"]


def test_xlsx_file_and_org_admin(client, sci):
    db, org, h = sci["db"], sci["org"], sci["ah"]
    frame = pd.read_csv(io.BytesIO(_csv(NEW[:2])), dtype=str).fillna("")
    buf = io.BytesIO()
    frame.to_excel(buf, index=False)
    bid, pv, rows = _through_review(
        client, h, buf.getvalue(), "list.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", skip=())
    assert len(rows) == 2
    r = client.get("/program/leads/import/batches/%s/sci-preview" % bid, headers=h)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["rows_to_sci"] == 2 and p["stage"]["location_mapped"] == 2
    r = client.post("/program/leads/import/batches/%s/load" % bid, headers=h, json={"mode": "load"})
    assert r.status_code == 200, r.text
    assert r.json()["report"]["families_created"] == 2


def test_variant_headers_map_and_load(client, sci):
    db, org, h = sci["db"], sci["org"], sci["mh"]
    header = ["Lead ID", "First Name", "Last Name", "Phone", "Email", "Location", "Campaign", "Status"]
    rows = [["V001", "Vera", "Variant", "2145550501", "vera@example.com", SH, CAMPAIGN, "Open"]]
    bid, pv, _rows = _through_review(client, h, _csv(rows, header), skip=())
    cols = {c["header"]: c["mapping"] for c in pv["columns"]}
    assert cols["Location"]["target"] == "location_friendly_name"
    assert cols["Campaign"]["target"] == "primary_campaign"
    r = client.post("/program/leads/import/batches/%s/load" % bid, headers=h, json={"mode": "load"})
    assert r.status_code == 200, r.text
    rec = db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id="V001").one()
    assert rec.location_status == "mapped" and rec.source_status == "Open"
    assert json.loads(rec.raw_json)["Location Friendly Name"] == SH


def test_blank_and_repeated_lead_ids_never_refuse_the_file(client, sci):
    """#385: a normal export with no Lead IDs (or a repeated one) loads - SCI
    generates an ID (marked as generated) and keeps + flags a repeat."""
    db, org, h = sci["db"], sci["org"], sci["mh"]
    rows = [_row("R001", "Rae", "One", "r1@example.com", "2145550601", EG),
            _row("R001", "Rob", "Two", "r2@example.com", "2145550602", EG),
            _row("", "Rex", "Blank", "r3@example.com", "2145550603", EG)]
    bid, _pv, _rows = _through_review(client, h, _csv(rows), skip=())
    p = client.get("/program/leads/import/batches/%s/sci-preview" % bid, headers=h).json()
    assert p["can_load"] is True and p["blockers"] == [] and p["rows_to_sci"] == 3
    assert p["lead_id_problems"]["blank"] == 1 and p["lead_id_problems"]["repeated"] == [{"lead_id": "R001", "rows": [2, 3]}]
    r = client.post("/program/leads/import/batches/%s/load" % bid, headers=h, json={"mode": "stage"})
    assert r.status_code == 200, r.text
    recs = {x.source_lead_id: x for x in db.query(ProgramSourceRecord).filter_by(organization_id=org.id)
            .filter(ProgramSourceRecord.first_name.in_(["Rae", "Rob", "Rex"]))}
    assert set(recs) >= {"R001", "R001-R2"}
    gen = [k for k in recs if k.startswith("GEN-")]
    assert len(gen) == 1 and recs[gen[0]].source_id_generated is True
    assert recs["R001"].source_id_generated in (False, None)
    assert "repeats Lead ID R001" in (recs["R001-R2"].duplicate_review_reason or "")


# ── who may ─────────────────────────────────────────────────────────────────

def test_spectator_advisor_and_other_tenant_refused(client, sci, sample_advisor):
    db, org = sci["db"], sci["org"]
    r = _upload(client, sci["mh"], _csv(NEW[:1]))
    bid = r.json()["id"]
    spec = _user(db, "sci-imp-spec@example.com")
    grant_workspace_membership(db, spec.id, org.id, role="spectator", check_capacity=False)
    db.commit()
    sh = _h(db, spec, org.id)
    adv = _h(db, sample_advisor)
    for h in (sh, adv):
        assert client.get("/program/intake/context", headers=h).status_code == 403
        assert client.get("/program/intake/batches/%s" % bid, headers=h).status_code == 403
        assert client.get("/program/leads/import/batches/%s/sci-preview" % bid,
                          headers=h).status_code == 403
        assert client.post("/program/leads/import/batches/%s/load" % bid, headers=h,
                           json={"mode": "load"}).status_code == 403
        assert _upload(client, h, _csv(NEW[:1])).status_code == 403
    # Another customer with its own program cannot see or load this batch.
    other = Organization(name="Other Memorial Co", slug="other-memorial", plan="standard",
                         industry="funeral")
    db.add(other)
    db.commit()
    oadmin = _make_program(db, other, "other-admin@example.com")
    oh = _h(db, oadmin)
    assert client.get("/program/intake/batches/%s" % bid, headers=oh).status_code == 404
    assert client.get("/program/leads/import/batches/%s/sci-preview" % bid, headers=oh).status_code == 404
    assert client.post("/program/leads/import/batches/%s/load" % bid, headers=oh,
                       json={"mode": "load"}).status_code == 404
    # A workspace with no program is refused.
    plain = Organization(name="Plain Co", slug="plain-co", plan="standard", industry="funeral")
    db.add(plain)
    db.commit()
    padmin = _user(db, "plain-admin@example.com", org_id=plain.id, role="org_admin")
    assert client.get("/program/intake/context", headers=_h(db, padmin)).status_code == 404


def test_crm_batches_are_not_reachable_through_sci_routes(client, sci, monkeypatch):
    """A CRM (non-SCI) batch of the same workspace is 'not found' here, and a
    CRM upload stays a CRM batch whatever source it names."""
    db, org, ah = sci["db"], sci["org"], sci["ah"]
    org.enabled_features = None
    db.commit()
    r = client.post("/intake/batches", headers=ah,
                    files={"file": ("crm.csv", _csv(NEW[:1]), "text/csv")}, data={"source": "program"})
    assert r.status_code == 200, r.text
    crm = r.json()
    assert crm["source_system"] == "other" and "destination" not in crm
    assert client.get("/program/intake/batches/%s" % crm["id"], headers=ah).status_code == 404
    assert client.get("/program/leads/import/batches/%s/sci-preview" % crm["id"],
                      headers=ah).status_code == 404
    # The CRM batch's commit preview is not refused.
    client.post("/intake/batches/%s/analyze" % crm["id"], headers=ah)
    assert client.get("/intake/batches/%s/commit-preview" % crm["id"], headers=ah).status_code == 200


def test_load_source_existing_callers_unchanged(client, db_session, sample_org):
    """stage_only defaults off: /program/leads/import still stages, holds and
    promotes in one go."""
    admin = _make_program(db_session, sample_org, "unchanged@example.com")
    r = client.post("/program/leads/import", headers=_h(db_session, admin),
                    files={"file": ("x.csv", _csv(NEW[:2]), "text/csv")}, data={"apply": "true"})
    assert r.status_code == 200 and r.json()["promotion"]["created"] == 2
    assert "stage_only" not in r.json()


# ── review fixes ────────────────────────────────────────────────────────────

def test_load_promotes_only_this_batchs_families(client, sci):
    """Batch A staged only; loading batch B must not make A's rows live."""
    db, org, h = sci["db"], sci["org"], sci["mh"]
    a, _pv, _rows = _through_review(client, h, _csv(NEW[:2]), skip=())          # N001, N002
    assert client.post("/program/leads/import/batches/%s/load" % a, headers=h,
                       json={"mode": "stage"}).json()["report"]["staged"] == 2
    b, _pv, _rows = _through_review(client, h, _csv([NEW[3]]), skip=())         # N004
    r = client.post("/program/leads/import/batches/%s/load" % b, headers=h, json={"mode": "load"})
    assert r.status_code == 200, r.text
    assert r.json()["report"]["families_created"] == 1
    recs = {x.source_lead_id: x for x in db.query(ProgramSourceRecord).filter_by(organization_id=org.id)}
    assert recs["N004"].lead_id and not recs["N001"].lead_id and not recs["N002"].lead_id
    r = client.post("/program/leads/import/batches/%s/load" % a, headers=h, json={"mode": "load"})
    assert r.status_code == 200 and r.json()["report"]["families_created"] == 2


def test_staged_batch_is_frozen(client, sci):
    db, org, h = sci["db"], sci["org"], sci["mh"]
    bid, pv, rows = _through_review(client, h, _csv(NEW[:2]), skip=())
    client.post("/program/leads/import/batches/%s/load" % bid, headers=h, json={"mode": "stage"})
    msg = "already staged in SCI - upload again"
    calls = [
        client.patch("/program/intake/batches/%s/rows/%s" % (bid, rows[0]["id"]), headers=h, json={"skip": True}),
        client.post("/program/intake/batches/%s/rows/bulk" % bid, headers=h,
                    json={"category": "status:ready", "skip": True}),
        client.put("/program/intake/batches/%s/mapping" % bid, headers=h, json={"mapping": {}}),
        client.post("/program/intake/batches/%s/analyze" % bid, headers=h),
        client.post("/program/intake/batches/%s/cancel" % bid, headers=h),
    ]
    for r in calls:
        assert r.status_code == 409 and msg in r.json()["detail"], r.text
    # The rows are as staged; loading makes exactly those live.
    r = client.post("/program/leads/import/batches/%s/load" % bid, headers=h, json={"mode": "load"})
    assert r.status_code == 200 and r.json()["report"]["families_created"] == 2


def test_crm_intake_routes_refuse_sci_batches(client, sci):
    db, org, ah = sci["db"], sci["org"], sci["ah"]
    org.enabled_features = None
    db.commit()
    bid, _pv, rows = _through_review(client, ah, _csv(NEW), skip=())
    dnc_row = next(r for r in rows if r["source_record_id"] == "E001")
    assert dnc_row["intake_status"] == "blocked"
    listed = client.get("/intake/batches", headers=ah).json()
    assert bid not in {b["id"] for b in listed["batches"]}
    refusal = "SCI files are loaded through SCI"
    for r in (client.get("/intake/batches/%s" % bid, headers=ah),
              client.get("/intake/batches/%s/preview" % bid, headers=ah),
              client.get("/intake/batches/%s/rows" % bid, headers=ah),
              client.patch("/intake/batches/%s/rows/%s" % (bid, dnc_row["id"]), headers=ah,
                           json={"approve": True}),
              client.post("/intake/batches/%s/rows/bulk" % bid, headers=ah,
                          json={"category": "status:blocked", "approve": True}),
              client.put("/intake/batches/%s/mapping" % bid, headers=ah, json={"mapping": {}}),
              client.post("/intake/batches/%s/analyze" % bid, headers=ah),
              client.get("/intake/batches/%s/rollback-plan" % bid, headers=ah),
              client.post("/intake/batches/%s/cancel" % bid, headers=ah),
              client.get("/intake/batches/%s/audit" % bid, headers=ah)):
        assert r.status_code == 409 and r.json()["detail"] == refusal, r.text
    row = client.get("/program/intake/batches/%s/rows?category=status:blocked" % bid,
                     headers=ah).json()["rows"][0]
    assert row["intake_status"] == "blocked"                       # the DNC row is unchanged
    b = db.query(ImportBatch).filter_by(id=bid).one()
    assert b.status == "ready_for_review"
    # The CRM ledger still lists CRM batches.
    r = client.post("/intake/batches", headers=ah,
                    files={"file": ("crm.csv", _csv(NEW[:1]), "text/csv")}, data={"source": "csv"})
    assert r.status_code == 200
    assert r.json()["id"] in {b["id"] for b in client.get("/intake/batches", headers=ah).json()["batches"]}


def test_undecided_duplicates_no_longer_block_the_load(client, sci):
    """#385: SCI links the same person itself and holds a shared phone/email
    under a different name for review - an undecided possible duplicate is
    reported, never a reason to refuse the whole list."""
    from app.models.import_models import ImportStagedRow
    db, org, h = sci["db"], sci["org"], sci["mh"]
    bid, _pv, rows = _through_review(client, h, _csv(NEW[:2]), skip=())
    row = db.query(ImportStagedRow).filter_by(batch_id=bid, source_record_id="N002").one()
    row.intake_status, row.duplicate_resolution = "needs_review", "review"
    db.commit()
    p = client.get("/program/leads/import/batches/%s/sci-preview" % bid, headers=h).json()
    assert p["can_load"] is True and p["rows_to_sci"] == 2
    r = client.post("/program/leads/import/batches/%s/load" % bid, headers=h, json={"mode": "stage"})
    assert r.status_code == 200, r.text

def test_sci_upload_capped_at_10mb(client, sci):
    big = _csv(NEW[:1]) + b"x" * (10 * 1024 * 1024)
    r = _upload(client, sci["mh"], big)
    assert r.status_code == 413


def test_stale_committing_load_can_be_retried(client, sci):
    from datetime import datetime, timedelta
    db, org, h = sci["db"], sci["org"], sci["mh"]
    bid, _pv, _rows = _through_review(client, h, _csv(NEW[:2]), skip=())
    b = db.query(ImportBatch).filter_by(id=bid).one()
    b.status, b.heartbeat_at = "committing", datetime.utcnow() - timedelta(minutes=2)
    db.commit()
    r = client.post("/program/leads/import/batches/%s/load" % bid, headers=h, json={"mode": "load"})
    assert r.status_code == 409 and "already being loaded" in r.json()["detail"]
    b.heartbeat_at = datetime.utcnow() - timedelta(minutes=20)        # the load was killed
    db.commit()
    r = client.post("/program/leads/import/batches/%s/load" % bid, headers=h, json={"mode": "load"})
    assert r.status_code == 200, r.text
    assert r.json()["report"]["families_created"] == 2
    db.refresh(b)
    assert b.status == "committed"


# ── walkthrough fixes ───────────────────────────────────────────────────────

def test_result_counts_describe_this_file_only(client, sci):
    """'Already in SCI Leads' / held-not-promoted are this file's families,
    not the whole program (E001 and E003 are live but not in these files)."""
    db, org, h = sci["db"], sci["org"], sci["mh"]
    a, _pv, _rows = _through_review(client, h, _csv(NEW[:2]), skip=())          # N001, N002
    rep = client.post("/program/leads/import/batches/%s/load" % a, headers=h,
                      json={"mode": "stage"}).json()["report"]
    assert rep["already_live"] == 0 and rep["held_not_promoted"] == 0
    assert rep["sci"]["promotion_plan"]["to_create"] == 2
    b, _pv, _rows = _through_review(client, h, _csv([NEW[2], NEW[3]]), skip=())  # N003 (held), N004
    rep = client.post("/program/leads/import/batches/%s/load" % b, headers=h,
                      json={"mode": "load"}).json()["report"]
    assert rep["families_created"] == 1 and rep["already_live"] == 0
    assert rep["held_not_promoted"] == 1                         # N003 only
    assert rep["sci"]["promotion"]["records"] == 2


def test_line_check_only_this_files_families_never_opted_out(client, sci, monkeypatch):
    """The paid lookup runs for this file's families only, and never for a
    family that opted out - not for E003 (live, unchecked, not in the file)."""
    from app.services.programs import line_check
    real = REAL_RUN_FOR_ORG                                   # undo the autouse mock
    looked = []

    class FakeLookup:
        def validate_phone(self, d):
            looked.append(d)
            return {"line_type": "mobile"}
    monkeypatch.setattr("app.services.evosense.vendors.TwilioLookupLineType", FakeLookup)
    monkeypatch.setattr(line_check, "run_for_org", real)
    db, org, h = sci["db"], sci["org"], sci["mh"]
    bid, _pv, rows = _through_review(client, h, _csv([NEW[0], NEW[5], NEW[1]]), skip=())  # N001, E001, N002
    e1 = next(r for r in rows if r["source_record_id"] == "E001")
    assert e1["intake_status"] == "blocked"
    r = client.patch("/program/intake/batches/%s/rows/%s" % (bid, e1["id"]), headers=h, json={"approve": True})
    assert r.status_code == 200, r.text
    r = client.post("/program/leads/import/batches/%s/load" % bid, headers=h, json={"mode": "load"})
    assert r.status_code == 200, r.text
    lc = r.json()["report"]["line_check"]
    assert sorted(looked) == ["2145550401", "2145550402"]
    assert lc["checked"] == 2 and lc["results"] == {"mobile": 2} and lc["skipped_opted_out"] == 1
    db.refresh(sci["dnc_lead"])
    assert sci["dnc_lead"].status == "dnc" and sci["dnc_lead"].phone_line_type is None
    e3 = db.query(Lead).filter_by(organization_id=org.id, first_name="Cara").one()
    assert e3.phone_line_type is None                          # outside the file: not looked up


def test_run_for_org_default_unchanged(db_session, sample_org, sci):
    """Existing callers (no source_lead_ids) still check the whole program."""
    real = REAL_RUN_FOR_ORG
    seen = []

    class A:
        def validate_phone(self, d):
            seen.append(d)
            return {"line_type": "landline"}
    out = real(db_session, sample_org.id, limit=100, adapter=A())
    assert out == {"checked": len(seen), "results": {"landline": len(seen)}, "remaining": 0}
    assert "2145550303" in seen                                  # E003, any family in the program


def test_a_decided_duplicate_counts_as_decided(client, sci):
    from app.models.import_models import ImportStagedRow
    db, org, h = sci["db"], sci["org"], sci["mh"]
    bid, _pv, _rows = _through_review(client, h, _csv(NEW[:2]), skip=())
    row = db.query(ImportStagedRow).filter_by(batch_id=bid, source_record_id="N002").one()
    row.intake_status, row.duplicate_resolution = "needs_review", "review"
    row.status_reasons = json.dumps([{"code": "possible_existing_match", "detail": "email"}])
    db.commit()
    client.post("/program/intake/batches/%s/rows/bulk" % bid, headers=h,
                json={"category": "status:ready", "approve": True})        # refresh the counts
    b = client.get("/program/intake/batches/%s" % bid, headers=h).json()
    assert b["analysis"]["status"]["needs_review"] == 1
    r = client.patch("/program/intake/batches/%s/rows/%s" % (bid, row.id), headers=h,
                     json={"duplicate_resolution": "keep_separate"})
    assert r.status_code == 200 and r.json()["intake_status"] == "approved", r.text
    b = client.get("/program/intake/batches/%s" % bid, headers=h).json()
    assert b["analysis"]["status"].get("needs_review", 0) == 0
    assert client.get("/program/leads/import/batches/%s/sci-preview" % bid,
                      headers=h).json()["can_load"] is True
    # "Decide later" again reopens it - reported, but no longer blocks the load (#385).
    r = client.patch("/program/intake/batches/%s/rows/%s" % (bid, row.id), headers=h,
                     json={"duplicate_resolution": "review"})
    assert r.json()["intake_status"] == "needs_review"
    assert client.get("/program/leads/import/batches/%s/sci-preview" % bid,
                      headers=h).json()["can_load"] is True
    # A row with another open question (an ambiguous date) stays in review.
    row.status_reasons = json.dumps([{"code": "possible_existing_match"}, {"code": "ambiguous_date"}])
    db.commit()
    r = client.patch("/program/intake/batches/%s/rows/%s" % (bid, row.id), headers=h,
                     json={"duplicate_resolution": "keep_separate"})
    assert r.json()["intake_status"] == "needs_review"


def test_any_row_can_be_skipped_before_staging(client, sci):
    db, org, h = sci["db"], sci["org"], sci["mh"]
    bid, _pv, rows = _through_review(client, h, _csv(NEW[:2]), skip=())
    ready = next(r for r in rows if r["intake_status"] == "ready")
    r = client.patch("/program/intake/batches/%s/rows/%s" % (bid, ready["id"]), headers=h, json={"skip": True})
    assert r.status_code == 200 and r.json()["intake_status"] == "skipped"
    p = client.get("/program/leads/import/batches/%s/sci-preview" % bid, headers=h).json()
    assert p["rows_to_sci"] == 1 and p["excluded"]["total"] == 1


def test_a_decided_exact_in_file_duplicate_is_settled(client, sci):
    """N009 is Dana Newfam again (same email) under a second Lead ID: an exact in-file duplicate. Once a
    person decides (keep separate), it leaves the Duplicates count; "decide
    later" puts it back."""
    db, org, h = sci["db"], sci["org"], sci["mh"]
    dup = _row("N009", "Dana", "Newfam", "dana.n@example.com", "2145550409", EG)
    bid, _pv, rows = _through_review(client, h, _csv([NEW[0], dup]), skip=())
    n9 = next(r for r in rows if r["source_record_id"] == "N009")
    assert n9["intake_status"] == "duplicate"
    assert client.get("/program/intake/batches/%s" % bid, headers=h).json()["analysis"]["status"]["duplicate"] == 1
    r = client.patch("/program/intake/batches/%s/rows/%s" % (bid, n9["id"]), headers=h,
                     json={"duplicate_resolution": "keep_separate"})
    assert r.status_code == 200 and r.json()["intake_status"] == "approved", r.text
    st = client.get("/program/intake/batches/%s" % bid, headers=h).json()["analysis"]["status"]
    assert st.get("duplicate", 0) == 0
    p = client.get("/program/leads/import/batches/%s/sci-preview" % bid, headers=h).json()
    assert p["can_load"] is True and p["rows_to_sci"] == 2
    r = client.patch("/program/intake/batches/%s/rows/%s" % (bid, n9["id"]), headers=h,
                     json={"duplicate_resolution": "review"})
    assert r.json()["intake_status"] == "duplicate", r.json()
