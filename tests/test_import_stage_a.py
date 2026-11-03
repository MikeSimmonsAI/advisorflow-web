"""Unified import - Stage A: confirmed defect fixes on the existing paths.

Synthetic data only. Nothing is sent; no live import runs.
"""
import json
from datetime import datetime, timezone

import pytest

from app.models.import_models import (ImportBatch, ImportBatchStatus, ImportRowReviewStatus, ImportStagedRow,
                                      ImportValidationStatus, ImportDuplicateStatus)
from app.models.models import Lead, Organization, User
from app.services.auth_service import create_access_token, hash_password
from app.services.import_commit_service import TIER_TO_TRACK, commit_batch
from app.utils import csv_safe

uid = lambda: __import__("uuid").uuid4().hex   # noqa: E731


def _org(db, industry="cleaning", features=None, slug=None):
    o = Organization(name="Synth %s Co" % industry.title(), slug=slug or "synth-%s-%s" % (industry, uid()[:6]),
                     plan="standard", industry=industry,
                     enabled_features=json.dumps(features) if features is not None else None)
    db.add(o)
    db.commit()
    return o


def _user(db, org, role="org_admin"):
    u = User(organization_id=org.id, email="%s-%s@synth.test" % (role, uid()[:8]),
             password_hash=hash_password("Synth-Pass-123!"), full_name="Synth " + role, role=role,
             must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _hdr(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


def _batch(db, org, user, **kw):
    b = ImportBatch(id=uid(), organization_id=org.id, created_by_id=user.id, display_name="Synth batch",
                    source_type="csv", source_filename="synth.csv", status=ImportBatchStatus.READY_TO_COMMIT,
                    created_at=datetime.now(timezone.utc), **kw)
    db.add(b)
    db.commit()
    return b


def _row(db, b, n, **kw):
    kw.setdefault("review_status", ImportRowReviewStatus.ACCEPTED)
    r = ImportStagedRow(id=uid(), batch_id=b.id, organization_id=b.organization_id, row_number=n,
                        validation_status=ImportValidationStatus.VALID,
                        duplicate_status=ImportDuplicateStatus.NEW, **kw)
    db.add(r)
    db.commit()
    return r


# ── legacy commit: no funeral default, honest channel ───────────────────────

def test_legacy_commit_never_invents_a_funeral_tier(db_session):
    org = _org(db_session, "cleaning")
    u = _user(db_session, org)
    b = _batch(db_session, org, u)
    _row(db_session, b, 1, first_name="Pat", last_name="Synth", phone_normalized="+12145550101",
         phone_raw="214-555-0101")
    commit_batch(b.id, org.id, db_session, u.id)
    lead = db_session.query(Lead).filter(Lead.organization_id == org.id).one()
    assert lead.tier is None                       # was "pre_need" for every org
    assert lead.message_track == "needs_review"
    assert lead.sms_consent in (False, None)       # an import never grants consent


def test_legacy_commit_keeps_a_tier_the_file_stated(db_session):
    org = _org(db_session, "funeral")
    u = _user(db_session, org)
    b = _batch(db_session, org, u)
    _row(db_session, b, 1, first_name="Lee", last_name="Synth", phone_normalized="+12145550102", tier="at_need")
    commit_batch(b.id, org.id, db_session, u.id)
    lead = db_session.query(Lead).filter(Lead.organization_id == org.id).one()
    assert (lead.tier, lead.message_track) == ("at_need", "at_need_support")


def test_email_only_rows_are_not_routed_to_sms(db_session):
    org = _org(db_session, "cleaning")
    u = _user(db_session, org)
    b = _batch(db_session, org, u)
    _row(db_session, b, 1, first_name="Em", last_name="Only", email_normalized="em.only@synth.test")
    _row(db_session, b, 2, first_name="Ph", last_name="One", phone_normalized="+12145550103")
    commit_batch(b.id, org.id, db_session, u.id)
    by = {l.last_name: l for l in db_session.query(Lead).filter(Lead.organization_id == org.id)}
    assert by["Only"].contact_channel == "email_only"
    assert by["One"].contact_channel == "sms"


def test_force_new_inquiry_gets_the_same_track_as_the_other_importer(db_session):
    from app.services.import_service import TIER_TO_TRACK as IS_TRACK
    assert TIER_TO_TRACK["new_inquiry"] == IS_TRACK["new_inquiry"] == "new_inquiry_intro"
    org = _org(db_session, "cleaning")
    u = _user(db_session, org)
    b = _batch(db_session, org, u, force_new_inquiry=True)
    _row(db_session, b, 1, first_name="New", last_name="Ask", phone_normalized="+12145550104")
    commit_batch(b.id, org.id, db_session, u.id)
    lead = db_session.query(Lead).filter(Lead.organization_id == org.id).one()
    assert (lead.tier, lead.message_track) == ("new_inquiry", "new_inquiry_intro")


def test_legacy_commit_sends_nothing_and_starts_no_journey(db_session, monkeypatch):
    calls = []
    import app.services.cadence_service as CS
    for name in [n for n in dir(CS) if n.startswith(("start", "enroll", "enqueue", "send"))]:
        monkeypatch.setattr(CS, name, lambda *a, **k: calls.append(name))
    org = _org(db_session, "cleaning")
    u = _user(db_session, org)
    b = _batch(db_session, org, u)
    _row(db_session, b, 1, first_name="Quiet", last_name="Row", phone_normalized="+12145550105")
    commit_batch(b.id, org.id, db_session, u.id)
    assert calls == []


# ── legacy batch routes cannot change guided / SCI batches ──────────────────

@pytest.mark.parametrize("pipeline,source_system", [("universal", "file"), ("universal", "program")])
def test_legacy_routes_refuse_to_change_guided_batches(client, db_session, pipeline, source_system):
    org = _org(db_session, "cleaning")
    u = _user(db_session, org)
    h = _hdr(db_session, u)
    b = _batch(db_session, org, u, pipeline=pipeline, source_system=source_system)
    b.status = ImportBatchStatus.READY_FOR_REVIEW
    db_session.commit()
    r = _row(db_session, b, 1, first_name="G", last_name="Uided", review_status=ImportRowReviewStatus.PENDING)
    calls = [("patch", "/import-batches/%s/rows/%s" % (b.id, r.id), {"review_status": "accepted"}),
             ("post", "/import-batches/%s/rows/bulk-review" % b.id, {"review_status": "accepted", "row_ids": [r.id]}),
             ("post", "/import-batches/%s/ready" % b.id, None),
             ("post", "/import-batches/%s/commit" % b.id, None),
             ("post", "/import-batches/%s/archive" % b.id, None),
             ("delete", "/import-batches/%s" % b.id, None)]
    for method, path, body in calls:
        kw = {"headers": h}
        if body is not None:
            kw["json"] = body
        resp = getattr(client, method)(path, **kw)
        assert resp.status_code == 409, (method, path, resp.status_code, resp.text[:200])
    db_session.expire_all()
    assert db_session.query(ImportBatch).get(b.id) is not None
    assert db_session.query(ImportStagedRow).get(r.id).review_status == ImportRowReviewStatus.PENDING
    assert client.get("/import-batches/%s" % b.id, headers=h).status_code == 200    # reading is fine


# ── Google Contacts has the same feature gate as the guided importer ────────

def test_google_contacts_import_needs_the_imports_feature(client, db_session):
    org = _org(db_session, "cleaning", features=["leads"])        # leads, but no Import Center
    u = _user(db_session, org)
    r = client.post("/google-contacts/import", headers=_hdr(db_session, u))
    assert r.status_code in (402, 403), r.text[:200]
    assert db_session.query(ImportBatch).filter(ImportBatch.organization_id == org.id).count() == 0


# ── Acquisition hand entry: unknown state stays unknown ─────────────────────

def test_acquisition_hand_entry_does_not_invent_a_state(db_session):
    from app.services.acquisition import engine as AE
    org = _org(db_session, "cleaning")
    u = _user(db_session, org)
    opp = AE.add_prospect(db_session, org, u, {"business_name": "Synth Tower Offices", "city": "Plano",
                                               "website": "https://synth-tower.example"})
    lead = db_session.query(Lead).get(opp.lead_id)
    assert lead.state is None and lead.city == "Plano"
    opp2 = AE.add_prospect(db_session, org, u, {"business_name": "Synth Plaza", "state": "OK",
                                                "website": "https://synth-plaza.example"})
    assert db_session.query(Lead).get(opp2.lead_id).state == "OK"


# ── spreadsheet-safe report cells ───────────────────────────────────────────

@pytest.mark.parametrize("raw,out", [("=HYPERLINK(\"x\")", "'=HYPERLINK(\"x\")"), ("+1 214", "'+1 214"),
                                     ("-5", "'-5"), ("@SUM(A1)", "'@SUM(A1)"), ("\tx", "'\tx"),
                                     ("Pat Synth", "Pat Synth"), (None, ""), (214, "214")])
def test_csv_cells_cannot_run_as_formulas(raw, out):
    assert csv_safe.cell(raw) == out


def test_the_buyer_import_error_report_is_spreadsheet_safe(monkeypatch):
    from app.services import wholesale_buyer_network as NET

    class R:
        row_number, state, errors, match = 2, "error", json.dumps(["bad phone"]), None
        raw = json.dumps({"Name": "=cmd|' /C calc'!A0", "Phone": "+12145550199"})

    class B:
        headers = json.dumps(["Name", "Phone"])

    class Q:
        def all(self):
            return [R()]
    monkeypatch.setattr(NET, "_rows", lambda db, b: Q())
    out = NET.error_report_csv(None, B())
    assert "'=cmd" in out and "'+12145550199" in out
