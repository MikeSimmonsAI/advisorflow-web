# -*- coding: utf-8 -*-
"""VOLT Google Sheets mirror (#571, PR #620): capture in the same transaction,
durable outbox, idempotent upsert by stable id, retries/recovery, isolation,
security, status/controls and the Consumer Leads CSV download.
No real Google API, no real personal data."""
import json
import logging
from datetime import datetime, timedelta

import pytest

from app.models.volt_leads_models import VoltLead, VoltLeadSheetOutbox, VoltSheetSyncState
from app.models.volt_models import VoltApplication, VoltEmail, VoltRep
from app.services.volt import sheets_export as X
from app.services.volt import sheets_outbox as SO
from tests.test_volt_leads import SLUG, enable, lead, post, team
from tests.test_volt_recruiting import H, _complete, _user, volt  # noqa: F401  (fixture)


# ── fake Google Sheets ──────────────────────────────────────────────────────

class HttpErr(Exception):
    def __init__(self, status):
        super().__init__("HTTP %s with cell text 214-555-0199 jane@example.test" % status)   # bodies can echo data
        self.resp = type("R", (), {"status": status})()


class Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeBook:
    """One spreadsheet tab: rows[0] is the header."""

    def __init__(self, business):
        self.business = business
        self.rows = [list(X.SCHEMAS[business])]
        self.fail = []           # statuses to raise on the next calls, in order
        self.calls = 0

    def _maybe_fail(self):
        self.calls += 1
        if self.fail:
            st = self.fail.pop(0)
            if st == "net":
                raise ConnectionError("network down")
            raise HttpErr(st)

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId, range):
        def run():
            self._maybe_fail()
            a1 = range.split("!")[1]
            if a1.startswith("A1:"):
                return {"values": [self.rows[0]]}
            return {"values": [[r[0]] for r in self.rows[1:]]}
        return Call(run)

    def batchUpdate(self, spreadsheetId, body):
        def run():
            self._maybe_fail()
            for d in body["data"]:
                n = int(d["range"].split("!A")[1].split(":")[0])
                self.rows[n - 1] = list(d["values"][0])
            return {}
        return Call(run)

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        def run():
            self._maybe_fail()
            assert valueInputOption == "RAW"
            self.rows.extend([list(r) for r in body["values"]])
            return {}
        return Call(run)

    def data(self):
        return self.rows[1:]

    def by_id(self, rid):
        hits = [r for r in self.rows[1:] if r[0] == rid]
        assert len(hits) <= 1, "duplicate rows for %s" % rid
        return hits[0] if hits else None


@pytest.fixture()
def gs(volt, monkeypatch):
    books = {"consumer": FakeBook("consumer"), "recruiting": FakeBook("recruiting")}
    monkeypatch.setenv("VOLT_SHEETS_ORG_ID", volt["org"].id)
    monkeypatch.setenv("VOLT_SHEETS_SYNC_ENABLED", "on")
    monkeypatch.setenv("VOLT_CONSUMER_SHEETS_ID", "consumer-sheet")
    monkeypatch.setenv("VOLT_RECRUITING_SHEETS_ID", "recruiting-sheet")
    monkeypatch.setenv("VOLT_SHEETS_SERVICE_ACCOUNT_JSON", json.dumps(
        {"type": "service_account", "client_email": "volt-sheets@example-project.iam.gserviceaccount.com",
         "private_key": "-----BEGIN PRIVATE KEY-----\nNOT-A-REAL-KEY\n-----END PRIVATE KEY-----\n"}))
    current = {"b": None}

    def factory():
        return books[current["b"]]
    real_upsert, real_verify = X.upsert, X.verify

    def upsert(business, rows, client=None):
        return real_upsert(business, rows, books[business])

    def verify(business, client=None):
        return real_verify(business, books[business])
    monkeypatch.setattr(X, "upsert", upsert)
    monkeypatch.setattr(X, "verify", verify)
    monkeypatch.setattr(X, "api", lambda: object())
    volt["books"] = books
    return volt


def run(v):
    out = SO.sync_pass(v["db"])
    v["db"].commit()
    return out


def outbox(v, business=None):
    q = v["db"].query(VoltLeadSheetOutbox)
    if business:
        q = q.filter(VoltLeadSheetOutbox.business == business)
    return q.all()


def _ops(v, who, path, method="get", **kw):
    return getattr(v["c"], method)("/volt/ops/%s%s" % (v["org"].id, path), headers=H(v["db"], v[who]), **kw)


# ── A. Consumer Leads ───────────────────────────────────────────────────────

def test_real_lead_queued_in_same_transaction_and_written_once(gs):
    enable(gs)
    assert post(gs, first_name="Jane", last_name="=HYPERLINK(\"x\")").status_code == 200
    rows = outbox(gs, "consumer")
    assert len(rows) == 1 and rows[0].status == "pending"
    lid = rows[0].record_id
    assert json.dumps([r.__dict__.get(k) for r in rows for k in ("record_id", "last_error")]).count("Jane") == 0
    assert run(gs)["synced"] == 1
    row = gs["books"]["consumer"].by_id(lid)
    assert row[2] == "'Jane =HYPERLINK(\"x\")" or row[2].startswith("Jane")      # name text
    assert row[9] == "New" and isinstance(row[1], float)                           # label, real date number
    # Repeat submission: same row updated, never a second row.
    post(gs, first_name="Jane", last_name="=HYPERLINK(\"x\")")
    assert len(outbox(gs, "consumer")) == 1 and outbox(gs, "consumer")[0].version == 2
    run(gs)
    assert len(gs["books"]["consumer"].data()) == 1


def test_assignment_status_and_follow_up_update_the_same_row(gs):
    enable(gs)
    post(gs)
    lid = gs["db"].query(VoltLead).one().id
    run(gs)
    agent = _user(gs["db"], "advisor", gs["org"], "member")
    gs["db"].commit()
    gs["agent"] = agent
    team(gs, "agent")
    assert _ops(gs, "god", "/leads/%s/assign" % lid, "post", json={"user_id": agent.id}).status_code == 200
    assert _ops(gs, "god", "/leads/" + lid, "patch", json={"status": "qualified"}).status_code == 200
    assert _ops(gs, "god", "/leads/" + lid, "patch", json={"event": "follow_up_scheduled",
                                                          "follow_up_at": "2026-11-20T15:00:00Z"}).status_code == 200
    run(gs)
    book = gs["books"]["consumer"]
    assert len(book.data()) == 1
    row = book.by_id(lid)
    assert row[9] == "Qualified" and row[10] == (agent.full_name or agent.email)
    assert abs(row[13] - X.central_serial(datetime(2026, 11, 20, 15, 0))) < 1e-6   # 9:00 AM CST


def test_test_leads_and_other_businesses_never_reach_the_sheet(gs, monkeypatch):
    enable(gs)
    assert _ops(gs, "god", "/leads/preview", "post", json=lead(first_name="Synthetic")).status_code == 200
    assert outbox(gs) == []                                            # preview = test partition
    # A lead in another organization is never queued...
    other = VoltLead(organization_id=gs["other"].id, first_name="Other", phone="2145550111", zip_code="75201",
                     consent_version="x", consent_text_hash="x", consent_at=datetime.utcnow(), status="new",
                     dedup_key="other-1", created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    gs["db"].add(other)
    gs["db"].commit()
    assert outbox(gs) == []
    # ...and even a forged queue row for it is never exported.
    SO.enqueue(gs["db"], gs["other"].id, "consumer", other.id)
    gs["db"].commit()
    run(gs)
    assert gs["books"]["consumer"].data() == [] and gs["books"]["recruiting"].data() == []


# ── B. Recruiting ───────────────────────────────────────────────────────────

def _real(db, app_id):
    """Registrations on example.test addresses are marked as test applicants
    (and so never exported); this one plays a real applicant."""
    db.query(VoltRep).filter(VoltRep.serial == db.get(VoltApplication, app_id).rep_serial).update({"is_test": False})
    db.commit()


def test_recruiting_submissions_status_changes_and_two_applications(gs):
    c, db = gs["c"], gs["db"]
    _, tok, out = gs["register"](first="Ava", last="Applicant")
    _real(db, out["application_id"])
    assert outbox(gs, "recruiting") == []                              # drafts are not exported
    _complete(c, tok, out["application_id"])
    second = c.post("/volt/rep/%s/applications" % SLUG, headers=tok, json={"project_key": "fiber"}).json()["id"]
    _complete(c, tok, second)
    run(gs)
    book = gs["books"]["recruiting"]
    assert sorted(r[0] for r in book.data()) == sorted([out["application_id"], second])
    assert book.by_id(out["application_id"])[6] == "Submitted"
    assert book.by_id(out["application_id"])[7].startswith("REP-")
    sh = H(db, gs["staff"])
    assert c.post("/volt/ops/%s/applications/%s/status" % (gs["org"].id, out["application_id"]), headers=sh,
                  json={"status": "approved"}).status_code == 200
    rep = db.query(VoltRep).filter(VoltRep.serial == db.get(VoltApplication, out["application_id"]).rep_serial).one()
    rep.phone = "2145550142"                                           # profile change -> both rows refresh
    db.commit()
    run(gs)
    assert len(book.data()) == 2
    assert book.by_id(out["application_id"])[6] == "Approved"
    assert {book.by_id(a)[4] for a in (out["application_id"], second)} == {"2145550142"}
    assert gs["books"]["consumer"].data() == []                       # never in the Consumer Leads sheet


def test_test_applicants_are_excluded(gs):
    c, db = gs["c"], gs["db"]
    _, tok, out = gs["register"](first="Tess", last="Tester")
    assert db.query(VoltRep).filter(VoltRep.serial == db.get(VoltApplication, out["application_id"]).rep_serial
                                    ).one().is_test is True
    _complete(c, tok, out["application_id"])
    run(gs)
    assert gs["books"]["recruiting"].data() == []
    assert outbox(gs, "recruiting")[0].status == "skipped"


# ── C. Google API behaviour ─────────────────────────────────────────────────

def test_outages_rate_limits_and_eventual_recovery(gs, caplog):
    enable(gs)
    post(gs, first_name="Riley", phone="(214) 555-0199", email="jane@example.test")
    book = gs["books"]["consumer"]
    db = gs["db"]
    for status, cat in ((503, "google_unavailable"), (429, "rate_limited"), ("net", "network")):
        book.fail = [status]
        with caplog.at_level(logging.DEBUG):
            assert run(gs)["failed"] == 1
        r = outbox(gs, "consumer")[0]
        assert r.status == "failed" and r.last_error == cat and r.next_retry_at > datetime.utcnow()
        st = db.get(VoltSheetSyncState, (gs["org"].id, "consumer"))
        assert st.last_error_category == cat
        r.next_retry_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    assert "214-555-0199" not in caplog.text and "jane@example.test" not in caplog.text
    assert run(gs)["synced"] == 1 and len(book.data()) == 1
    st = db.get(VoltSheetSyncState, (gs["org"].id, "consumer"))
    assert st.consecutive_failures == 0 and st.last_success_at is not None


def test_setup_problems_are_named_and_do_not_burn_retries(gs):
    enable(gs)
    post(gs)
    book = gs["books"]["consumer"]
    db = gs["db"]
    for status, cat in ((401, "credential"), (403, "permission"), (404, "not_found")):
        book.fail = [status]
        run(gs)
        r = outbox(gs, "consumer")[0]
        assert r.last_error == cat and r.attempts == 0
        assert r.next_retry_at > datetime.utcnow() + timedelta(minutes=14)
        st = db.get(VoltSheetSyncState, (gs["org"].id, "consumer"))
        assert st.verified_ok is False and st.verify_detail == cat
        r.next_retry_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    # A changed header row: nothing is written.
    book.rows[0][3] = "Mobile"
    run(gs)
    assert outbox(gs, "consumer")[0].last_error == "sheet_layout" and book.data() == []
    book.rows[0][3] = "Phone"
    # Missing configuration fails closed with its own category.
    import os
    os.environ.pop("VOLT_SHEETS_SERVICE_ACCOUNT_JSON")
    r = outbox(gs, "consumer")[0]
    r.next_retry_at = None
    db.commit()
    run(gs)
    assert outbox(gs, "consumer")[0].last_error == "not_configured"


def test_worker_crash_is_recovered_and_fresh_claims_are_respected(gs):
    enable(gs)
    post(gs)
    db = gs["db"]
    r = outbox(gs, "consumer")[0]
    r.status, r.claim_token, r.claimed_version, r.claimed_at = "processing", "dead-worker", r.version, datetime.utcnow()
    db.commit()
    assert run(gs)["synced"] == 0                                    # someone is (still) on it
    r.claimed_at = datetime.utcnow() - SO.CLAIM_TTL - timedelta(seconds=1)
    db.commit()
    assert run(gs)["synced"] == 1 and len(gs["books"]["consumer"].data()) == 1
    # A late finish from the dead worker changes nothing.
    SO._finish(db, r.id, "dead-worker", False, "unknown")
    db.commit()
    assert outbox(gs, "consumer")[0].status == "synced"


def test_change_while_writing_is_not_lost_and_duplicates_collapse(gs):
    enable(gs)
    post(gs)
    db = gs["db"]
    lid = db.query(VoltLead).one().id
    rows = SO._claim(db, gs["org"].id, 10)
    token = rows[0].claim_token
    lead_row = db.get(VoltLead, lid)
    lead_row.status = "contacted"                                    # a change lands mid-write
    db.commit()
    assert outbox(gs, "consumer")[0].status == "processing"          # still owned by the worker
    SO._finish(db, rows[0].id, token, True)
    db.commit()
    assert outbox(gs, "consumer")[0].status == "pending"             # so the newer data is written next
    run(gs)
    assert gs["books"]["consumer"].by_id(lid)[9] == "Contacted"
    # The same event twice, and the same id twice in one batch: one row.
    SO.enqueue(db, gs["org"].id, "consumer", lid)
    SO.enqueue(db, gs["org"].id, "consumer", lid)
    db.commit()
    assert len(outbox(gs, "consumer")) == 1
    run(gs)
    X.upsert("consumer", [gs["books"]["consumer"].by_id(lid)] * 2)
    assert len(gs["books"]["consumer"].data()) == 1


def test_disabled_or_unapproved_means_nothing_leaves(gs, monkeypatch):
    enable(gs)
    post(gs)
    monkeypatch.setenv("VOLT_SHEETS_SYNC_ENABLED", "off")
    assert run(gs) == {"disabled": True, "reason": "sync_off"}
    monkeypatch.setenv("VOLT_SHEETS_SYNC_ENABLED", "on")
    monkeypatch.setenv("VOLT_SHEETS_ORG_ID", "")
    assert run(gs)["reason"] == "tenant_not_configured"
    assert gs["books"]["consumer"].data() == []


# ── D. Security ─────────────────────────────────────────────────────────────

def test_formula_injection_dates_and_phone():
    assert X.safe("=IMPORTXML(1,2)") == "'=IMPORTXML(1,2)" and X.safe("@cmd") == "'@cmd"
    assert X.safe("-1+2").startswith("'") and X.safe("Normal") == "Normal"
    assert X.safe_phone("+1 (214) 555-0100") == "1 (214) 555-0100"
    assert X.safe_phone("+SUM(1)") == "'+SUM(1)"
    assert abs(X.central_serial(datetime(2026, 11, 15, 16, 0)) - 46341.416666667) < 1e-6   # 10:00 AM CST


def test_status_and_controls_are_for_operators_only_and_never_show_secrets(gs, monkeypatch):
    enable(gs)
    post(gs)
    s = _ops(gs, "god", "/sheets/status")
    assert s.status_code == 200
    body = s.text
    assert "PRIVATE KEY" not in body and "NOT-A-REAL-KEY" not in body
    assert s.json()["service_account"] == "volt-sheets@example-project.iam.gserviceaccount.com"
    cons = [b for b in s.json()["businesses"] if b["business"] == "consumer"][0]
    assert cons["pending"] == 1 and cons["spreadsheet_url"].endswith("consumer-sheet/edit")
    for who in ("staff", "viewer", "outsider", "other_staff"):
        assert _ops(gs, who, "/sheets/status").status_code == 403
    monkeypatch.setenv("VOLT_DEV_PRINCIPALS", gs["staff"].email)            # D'Angelo's rule
    assert _ops(gs, "staff", "/sheets/status").status_code == 200
    assert _ops(gs, "staff", "/sheets/consumer/reconcile", "post", json={"full": True}).status_code == 403
    assert _ops(gs, "god", "/sheets/consumer/reconcile", "post", json={"full": True}).json()["queued"] == 1
    # Pause holds the business; resume releases it.
    _ops(gs, "staff", "/sheets/consumer/pause", "post", json={"paused": True})
    assert run(gs)["synced"] == 0
    _ops(gs, "staff", "/sheets/consumer/pause", "post", json={"paused": False})
    assert run(gs)["synced"] == 1
    # Manual retry and verify.
    gs["books"]["consumer"].fail = [403]
    _ops(gs, "god", "/sheets/consumer/reconcile", "post", json={"full": True})
    run(gs)
    assert _ops(gs, "god", "/sheets/consumer/retry", "post").json()["requeued"] == 1
    assert _ops(gs, "god", "/sheets/consumer/verify", "post").json()["ok"] is True
    # Another organization's id is refused.
    r = gs["c"].post("/volt/ops/%s/sheets/consumer/retry" % gs["other"].id, headers=H(gs["db"], gs["god"]))
    assert r.status_code == 409


def test_stuck_mirror_alerts_operators_once(gs, monkeypatch):
    monkeypatch.setenv("VOLT_DEV_NOTIFY", "mike+volt-test@example.com")
    enable(gs)
    post(gs)
    db = gs["db"]
    gs["books"]["consumer"].fail = [403]
    run(gs)
    r = outbox(gs, "consumer")[0]
    r.updated_at = datetime.utcnow() - timedelta(minutes=45)
    db.commit()
    SO.alert_if_stuck(db, gs["org"].id)
    SO.alert_if_stuck(db, gs["org"].id)
    db.commit()
    mails = db.query(VoltEmail).filter(VoltEmail.kind == "sheets_alert").all()
    assert len(mails) == 1 and "Consumer Leads" in mails[0].subject
    assert "214" not in mails[0].body_html and "@example.test" not in mails[0].body_html


# ── CSV ─────────────────────────────────────────────────────────────────────

def test_consumer_csv_scope_filters_and_safety(gs):
    enable(gs)
    post(gs, first_name="=cmd", phone="(214) 555-0101", email="a@example.test")
    post(gs, first_name="Bea", phone="(214) 555-0102", email="b@example.test")
    db = gs["db"]
    a, b = db.query(VoltLead).order_by(VoltLead.created_at).all()
    agent = _user(db, "advisor", gs["org"], "member")
    db.commit()
    gs["agent"] = agent
    team(gs, "agent")
    _ops(gs, "god", "/leads/%s/assign" % b.id, "post", json={"user_id": agent.id})
    r = _ops(gs, "god", "/leads/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    text = r.content.decode("utf-8")
    assert text.startswith("﻿Lead ID,") and "'=cmd" in text and text.count("\n") == 3
    mine = _ops(gs, "agent", "/leads/export.csv").content.decode("utf-8")
    assert "Bea" in mine and "'=cmd" not in mine                        # agents: only their own leads
    assert "Bea" not in _ops(gs, "god", "/leads/export.csv?status=contacted").content.decode("utf-8")
    assert _ops(gs, "god", "/leads/export.csv?from=2000-01-01&to=2000-01-02").content.decode("utf-8").count("\n") == 1
    assert _ops(gs, "god", "/leads/export.csv?from=bad").status_code == 422
    for who in ("staff", "outsider", "other_staff"):
        assert _ops(gs, who, "/leads/export.csv").status_code == 403
    from app.models.volt_models import VoltAuditEvent
    assert db.query(VoltAuditEvent).filter(VoltAuditEvent.action == "lead_csv_export").count() == 4   # one per successful download
