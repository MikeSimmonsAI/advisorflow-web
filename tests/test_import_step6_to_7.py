# -*- coding: utf-8 -*-
"""Universal importer Step 6 (Approve) -> Step 7 (Results): the server-side
commit gate the wizard reads (batch.commit_gate = {can_commit, reason}),
retry after a failed commit, "Keep staged" -> results, and the full API
lifecycle upload -> map -> analyze -> review -> commit -> ledger -> rollback,
with tenant isolation and no invented consent / leads. Sandbox data only."""
import io

import pytest

from app.models.import_models import ImportBatch
from app.models.intake_models import OrgContact
from app.models.models import Lead, Organization, User
from app.services.auth_service import create_access_token, hash_password

CSV = (b"First Name,Last Name,Phone,Email,DNC,Notes\n"
       b"Ava,Testrow,214-555-0101,ava.testrow@example.com,,new\n"
       b"Ben,Sample,214-555-0102,ben.sample@example.com,,new\n"
       b"Ava,Testrow,214-555-0101,ava.testrow@example.com,,exact duplicate\n"
       b"Cara,Fixture,214-555-0103,cara.fixture@example.com,,new\n"
       b"Dan,Donotcall,214-555-0104,,yes,dnc row\n"
       b",,214-555-0105,,,phone only\n")


@pytest.fixture(autouse=True)
def _inline(monkeypatch):
    monkeypatch.setenv("INTAKE_INLINE_JOBS", "1")


def _org(db, name, slug):
    o = Organization(name=name, slug=slug, plan="enterprise", industry="energy")
    db.add(o)
    db.commit()
    return o


def _admin(db, org, email):
    u = User(organization_id=org.id, email=email, password_hash=hash_password("Pass12345!"),
             full_name=email.split("@")[0], role="org_admin", must_change_password=False)
    db.add(u)
    db.commit()
    return {"Authorization": f"Bearer {create_access_token(u, db)}"}


@pytest.fixture()
def env(client, db_session):
    a = _org(db_session, "Step Six Energy", "step-six")
    b = _org(db_session, "Other Tenant Co", "step-six-other")
    return a, _admin(db_session, a, "admin@step-six.test"), b, _admin(db_session, b, "admin@other.test")


def _analyzed_batch(client, h, content=CSV):
    r = client.post("/intake/batches", headers=h, data={"source": "csv"},
                    files={"file": ("sandbox.csv", io.BytesIO(content), "text/csv")})
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    r = client.post(f"/intake/batches/{bid}/analyze", headers=h)
    assert r.status_code == 202, r.text
    got = client.get(f"/intake/batches/{bid}", headers=h).json()
    assert got["status"] == "ready_for_review"
    return bid, got


def test_ready_batch_reports_can_commit_and_commit_lands_on_step_7(client, db_session, env):
    a, h, _b, _hb = env
    bid, got = _analyzed_batch(client, h)
    assert got["commit_gate"]["can_commit"] is True
    assert got["commit_gate"]["reason"] is None
    r = client.post(f"/intake/batches/{bid}/commit", headers=h,
                    json={"mode": "ready_only", "confirm_organization_name": " step six energy "})
    assert r.status_code == 202, r.text
    after = client.get(f"/intake/batches/{bid}", headers=h).json()
    assert after["status"] in ("committed", "partially_committed")
    assert after["workflow"]["step"] == 7


def test_keep_staged_is_a_real_decision_with_results(client, db_session, env):
    a, h, _b, _hb = env
    bid, _ = _analyzed_batch(client, h)
    r = client.post(f"/intake/batches/{bid}/commit", headers=h, json={"mode": "stage_only"})
    assert r.status_code == 202, r.text
    got = client.get(f"/intake/batches/{bid}", headers=h).json()
    assert got["status"] == "staged"
    assert got["commit_gate"]["can_commit"] is True          # import later from Step 6
    assert db_session.query(OrgContact).filter(OrgContact.organization_id == a.id).count() == 0
    r = client.post(f"/intake/batches/{bid}/commit", headers=h,
                    json={"mode": "ready_only", "confirm_organization_name": "Step Six Energy"})
    assert r.status_code == 202, r.text


def test_failed_commit_is_retryable_not_a_dead_end(client, db_session, env):
    a, h, _b, _hb = env
    bid, _ = _analyzed_batch(client, h)
    b = db_session.query(ImportBatch).filter(ImportBatch.id == bid).one()
    b.status, b.commit_mode, b.error_message = "failed", "ready_only", "Commit failed: simulated"
    db_session.commit()
    got = client.get(f"/intake/batches/{bid}", headers=h).json()
    assert got["commit_gate"]["can_commit"] is True, got["commit_gate"]
    r = client.post(f"/intake/batches/{bid}/commit", headers=h,
                    json={"mode": "ready_only", "confirm_organization_name": "Step Six Energy"})
    assert r.status_code == 202, r.text
    after = client.get(f"/intake/batches/{bid}", headers=h).json()
    assert after["status"] in ("committed", "partially_committed")
    assert after["error"] is None
    # retrying again never duplicates contacts
    n = db_session.query(OrgContact).filter(OrgContact.organization_id == a.id).count()
    client.post(f"/intake/batches/{bid}/commit", headers=h,
                json={"mode": "ready_only", "confirm_organization_name": "Step Six Energy"})
    assert db_session.query(OrgContact).filter(OrgContact.organization_id == a.id).count() == n


def test_remapped_or_failed_analysis_batch_explains_why_and_server_agrees(client, db_session, env):
    a, h, _b, _hb = env
    bid, _ = _analyzed_batch(client, h)
    b = db_session.query(ImportBatch).filter(ImportBatch.id == bid).one()
    b.status = "mapping"
    db_session.commit()
    got = client.get(f"/intake/batches/{bid}", headers=h).json()
    assert got["commit_gate"]["can_commit"] is False
    assert "analysis" in got["commit_gate"]["reason"]
    r = client.post(f"/intake/batches/{bid}/commit", headers=h,
                    json={"mode": "ready_only", "confirm_organization_name": "Step Six Energy"})
    assert r.status_code == 409
    assert r.json()["detail"] == got["commit_gate"]["reason"]
    # analysis failure (no commit attempted) stays blocked
    b.status, b.commit_mode = "failed", None
    db_session.commit()
    got = client.get(f"/intake/batches/{bid}", headers=h).json()
    assert got["commit_gate"]["can_commit"] is False


def test_full_lifecycle_counts_consent_leads_isolation_rollback(client, db_session, env):
    a, h, other, ho = env
    leads_before = db_session.query(Lead).count()
    bid, got = _analyzed_batch(client, h)
    assert got["rows_submitted"] == 6
    # another tenant cannot see or act on the batch
    for m, url, body in [("get", f"/intake/batches/{bid}", None),
                         ("get", f"/intake/batches/{bid}/commit-preview", None),
                         ("post", f"/intake/batches/{bid}/commit", {"mode": "stage_only"}),
                         ("get", f"/intake/batches/{bid}/rollback-plan", None)]:
        r = getattr(client, m)(url, headers=ho, **({"json": body} if body else {}))
        assert r.status_code == 404, (url, r.status_code)
    pv = client.get(f"/intake/batches/{bid}/commit-preview?mode=ready_only", headers=h).json()
    r = client.post(f"/intake/batches/{bid}/commit", headers=h,
                    json={"mode": "ready_only", "include_enrichment": True,
                          "confirm_organization_name": "Step Six Energy"})
    assert r.status_code == 202, r.text
    done = client.get(f"/intake/batches/{bid}", headers=h).json()
    rep = done["commit_report"]
    assert rep["contacts_created"] == pv["create_contacts"]
    assert rep["file_duplicates_merged"] == pv["merge_file_duplicates"] == 1
    assert (rep["contacts_created"] + rep["file_duplicates_merged"] + rep["skipped"]
            + rep["rows_still_staged"] + rep["failed"]) == 6
    assert rep["leads_created"] == 0
    assert db_session.query(Lead).count() == leads_before           # contacts, not leads
    contacts = db_session.query(OrgContact).filter(OrgContact.organization_id == a.id).all()
    assert len(contacts) == rep["contacts_created"]
    assert all(c.lead_id is None for c in contacts)
    assert not any(c.sms_status in ("opted_in", "consented", "subscribed") for c in contacts)
    assert db_session.query(OrgContact).filter(OrgContact.organization_id == other.id).count() == 0
    ledger = client.get("/intake/batches", headers=h).json()
    ids = [x["id"] for x in (ledger.get("items") or ledger.get("batches") or [])]
    assert bid in ids
    assert bid not in [x["id"] for x in (client.get("/intake/batches", headers=ho).json().get("items") or [])]
    plan = client.get(f"/intake/batches/{bid}/rollback-plan", headers=h).json()
    assert plan["items"]
    code = done["batch_code"]
    assert client.post(f"/intake/batches/{bid}/rollback", headers=ho,
                       json={"confirm_batch_code": code}).status_code == 404
    r = client.post(f"/intake/batches/{bid}/rollback", headers=h, json={"confirm_batch_code": code})
    assert r.status_code == 200, r.text
    assert r.json()["batch"]["status"] in ("rolled_back", "partially_rolled_back")
    db_session.expire_all()
    active = [c for c in db_session.query(OrgContact).filter(OrgContact.organization_id == a.id).all()
              if getattr(c, "lifecycle", None) not in ("archived",)]
    assert len(active) == 0


def test_confirmation_ignores_spacing_and_punctuation_but_refuses_other_names(client, db_session, env):
    """The page and the server normalise the typed name the same way."""
    a, h, _b, _hb = env
    bid, _got = _analyzed_batch(client, h)
    r = client.post(f"/intake/batches/{bid}/commit", headers=h,
                    json={"mode": "ready_only", "confirm_organization_name": "Step Six"})
    assert r.status_code == 400
    r = client.post(f"/intake/batches/{bid}/commit", headers=h,
                    json={"mode": "ready_only", "confirm_organization_name": ""})
    assert r.status_code == 400
    r = client.post(f"/intake/batches/{bid}/commit", headers=h,
                    json={"mode": "ready_only", "confirm_organization_name": "step  six energy."})
    assert r.status_code == 202, r.text
