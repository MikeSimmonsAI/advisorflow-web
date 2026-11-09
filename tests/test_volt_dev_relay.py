# -*- coding: utf-8 -*-
"""VOLT development relay (#571): principals, keys, runner identity, the job
lifecycle, merge approval, deploy proof, rollback and notices."""
import json
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.models.models import Organization, User
from app.models.sales_models import Membership
from app.models.volt_models import VoltDevEvent, VoltDevJob, VoltEmail
from app.services.volt import devrelay as R
from tests.test_volt_recruiting import H, _user, volt  # noqa: F401  (fixture import)

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
WF = "MikeSimmonsAI/advisorflow-web/.github/workflows/volt-dev.yml@refs/heads/main"


class _JWKS:
    def get_signing_key_from_jwt(self, token):
        class K:
            key = KEY.public_key()
        return K()


def runner_token(**over):
    now = int(time.time())
    claims = {"iss": R.ISSUER, "aud": R.AUDIENCE, "iat": now, "exp": now + 300, "jti": uuid.uuid4().hex,
              "repository": "MikeSimmonsAI/advisorflow-web", "job_workflow_ref": WF, "ref": "refs/heads/main",
              "event_name": "schedule", "run_id": "777"}
    claims.update(over)
    return {"Authorization": "Bearer " + jwt.encode(claims, KEY, algorithm="RS256")}


@pytest.fixture()
def dev(volt, monkeypatch):
    db = volt["db"]
    monkeypatch.setattr(R, "_JWKS", _JWKS())
    collab = User(email="collab.dev@example.com", full_name="Dee Collaborator", password_hash="x", role="org_admin",
                  is_active=True, must_change_password=False)
    db.add(collab)
    db.commit()
    db.add(Membership(user_id=collab.id, scope_type="customer_org", scope_id=volt["org"].id, role="org_admin", is_active=True))
    db.commit()
    monkeypatch.setenv("VOLT_DEV_PRINCIPALS", "%s,%s" % (volt["staff"].email, collab.email))
    monkeypatch.setenv("VOLT_DEV_NOTIFY", "mike+volt-test@example.com,dee+volt-test@example.com")
    return dict(volt, collab=collab)


def _base(dev):
    return "/volt/dev/%s" % dev["org"].id


def _submit(dev, who, **body):
    b = dict({"title": "Add a FAQ line", "request": "Add a line about outreach hours to the ACA page."}, **body)
    return dev["c"].post(_base(dev) + "/jobs", headers=H(dev["db"], who), json=b)


def _next(dev, **over):
    return dev["c"].post("/volt/dev/runner/next", headers=runner_token(**over))


def _report(dev, job_id, run_id="777", **body):
    return dev["c"].post("/volt/dev/runner/jobs/%s/report" % job_id, headers=runner_token(run_id=run_id), json=body)


def test_only_the_two_principals_and_the_owner(dev):
    c, db = dev["c"], dev["db"]
    assert _submit(dev, dev["collab"]).status_code == 200
    assert _submit(dev, dev["staff"]).status_code == 200
    assert _submit(dev, dev["god"]).status_code == 200                               # owner oversight
    # Another VOLT org admin who is NOT named: refused.
    other_admin = _user(db, "advisor", dev["org"], "org_admin")
    assert _submit(dev, other_admin).status_code == 403
    # Named, but in another tenant only: refused here.
    assert c.get("/volt/dev/%s/jobs" % dev["other"].id, headers=H(db, dev["collab"])).status_code in (403, 404)
    # Named, but VOLT access suspended: refused.
    from app.services.volt import access
    access.change(db, dev["org"].id, dev["god"], dev["collab"], "suspend", "test")
    db.commit()
    assert c.get(_base(dev) + "/jobs", headers=H(db, dev["collab"])).status_code == 403
    # Equal capabilities: the staff principal sees every job, whoever asked.
    items = c.get(_base(dev) + "/jobs", headers=H(db, dev["staff"])).json()["items"]
    assert len(items) == 3 and {i["requested_by"] for i in items} >= {"Dee Collaborator"}


def test_duplicate_submit_is_one_job_and_events_are_audited(dev):
    r1 = _submit(dev, dev["collab"], idempotency_key="abc-1")
    r2 = _submit(dev, dev["collab"], idempotency_key="abc-1")
    assert r1.json()["ref"] == r2.json()["ref"] == "DEV-1" and r2.json()["created"] is False
    assert dev["db"].query(VoltDevJob).count() == 1
    ev = r1.json()["events"][0]
    assert ev["action"] == "submitted" and ev["actor"] == "Dee Collaborator"
    assert _submit(dev, dev["collab"], title="").status_code == 422


def test_personal_key_for_chatgpt_action(dev):
    c, db = dev["c"], dev["db"]
    k = c.post(_base(dev) + "/tokens", headers=H(db, dev["collab"]), json={"name": "My GPT"}).json()
    assert k["token"].startswith("vdt_")
    hdr = {"Authorization": "Bearer " + k["token"], "Idempotency-Key": "gpt-1"}
    r = c.post("/volt/dev/api/jobs", headers=hdr, json={"title": "From ChatGPT", "request": "Change the button text on My Application."})
    assert r.status_code == 200 and r.json()["via"] == "api"
    assert c.post("/volt/dev/api/jobs", headers=hdr, json={"title": "From ChatGPT", "request": "Change the button text on My Application."}).json()["created"] is False
    assert c.get("/volt/dev/api/jobs/DEV-1", headers=hdr).json()["status"] == "queued"
    assert c.get("/volt/dev/api/jobs", headers={"Authorization": "Bearer vdt_wrong"}).status_code == 401
    assert c.get("/volt/dev/api/jobs").status_code == 401
    # The key is stored only as a hash.
    from app.models.volt_models import VoltDevToken
    assert k["token"] not in json.dumps([t.token_hash for t in db.query(VoltDevToken).all()])
    # Revoked key: refused. Owner loses principal status: key refused too.
    c.delete(_base(dev) + "/tokens/%s" % k["id"], headers=H(db, dev["collab"]))
    assert c.get("/volt/dev/api/jobs", headers=hdr).status_code == 401
    schema = c.get("/volt/dev/api/openapi.json").json()
    assert "/volt/dev/api/jobs" in schema["paths"] and schema["components"]["securitySchemes"]["key"]["scheme"] == "bearer"


def test_runner_identity_is_verified(dev):
    _submit(dev, dev["collab"])
    assert dev["c"].post("/volt/dev/runner/next").status_code == 401
    assert _next(dev, repository="someone/else").status_code == 403
    assert _next(dev, job_workflow_ref=WF.replace("volt-dev.yml", "evil.yml")).status_code == 403
    assert _next(dev, ref="refs/heads/feature").status_code == 403
    assert _next(dev, event_name="pull_request").status_code == 403
    assert _next(dev, aud="other").status_code == 401
    assert _next(dev, exp=int(time.time()) - 10).status_code == 401
    forged = jwt.encode({"iss": R.ISSUER, "aud": R.AUDIENCE, "exp": int(time.time()) + 60, "iat": int(time.time()),
                         "jti": "x", "repository": "MikeSimmonsAI/advisorflow-web", "job_workflow_ref": WF},
                        rsa.generate_private_key(public_exponent=65537, key_size=2048), algorithm="RS256")
    assert dev["c"].post("/volt/dev/runner/next", headers={"Authorization": "Bearer " + forged}).status_code == 401
    hdr = runner_token()
    assert dev["c"].post("/volt/dev/runner/next", headers=hdr).status_code == 200
    assert dev["c"].post("/volt/dev/runner/next", headers=hdr).status_code == 409    # replay


def test_full_lifecycle_merge_approval_deploy_and_notices(dev):
    c, db = dev["c"], dev["db"]
    job = _submit(dev, dev["collab"]).json()
    n = _next(dev).json()
    assert n["action"] == "code" and n["job"]["ref"] == "DEV-1" and n["job"]["branch"] == "volt-dev/dev-1-a1"
    assert _next(dev).json()["action"] == "none"                                    # leased, not handed out twice
    # Only the leaseholder run may move it.
    assert _report(dev, job["id"], run_id="999", status="coding_completed").status_code == 409
    assert _report(dev, job["id"], status="coding_completed").status_code == 200
    # Out-of-scope changes can never be "ready".
    bad = _report(dev, job["id"], status="ready_to_test", changed_files=[".github/workflows/x.yml"], tests_passed=True,
                  pr_number=5, head_sha="a" * 40)
    assert bad.status_code == 409 and bad.json()["detail"]["code"] == "out_of_scope"
    ok = _report(dev, job["id"], status="ready_to_test", changed_files=["frontend/src/pages/volt/VoltPortal.jsx", "tests/test_volt_x.py"],
                 tests_passed=True, tests_summary="66 passed; build ok", pr_number=5, pr_url="https://github.com/x/pull/5",
                 head_sha="a" * 40, branch="volt-dev/dev-1-a1")
    assert ok.status_code == 200
    assert _report(dev, job["id"], status="ready_to_test").json()["ok"]             # duplicate: no-op
    assert db.query(VoltEmail).filter(VoltEmail.kind == "dev_ready_to_test").count() == 2   # both principals, once
    # Either principal approves - here the collaborator; the approval pins the tested commit.
    ap = c.post(_base(dev) + "/jobs/DEV-1/approve-merge", headers=H(db, dev["collab"])).json()
    assert ap["status"] == "merge_requested" and ap["merge_approved_by"] == "Dee Collaborator"
    m = _next(dev, run_id="778").json()
    assert m["action"] == "merge" and m["job"]["approved_sha"] == "a" * 40
    assert _report(dev, job["id"], run_id="778", status="merged", merge_sha="b" * 40, merged_head_sha="c" * 40).status_code == 409
    assert _report(dev, job["id"], run_id="778", status="merged", merge_sha="b" * 40, merged_head_sha="a" * 40).status_code == 200
    w = _next(dev, run_id="779").json()
    assert w["deploy_watch"] == [{"id": job["id"], "ref": "DEV-1", "merge_sha": "b" * 40, "targets": "frontend"}]
    d = _report(dev, job["id"], run_id="779", status="deployed", deploy_detail="frontend runs bbbbbbb")
    assert d.status_code == 200 and d.json()["status"] == "deployed"
    j = c.get(_base(dev) + "/jobs/DEV-1", headers=H(db, dev["staff"])).json()
    assert j["deployed_at"] and [e["status"] for e in j["events"] if e["status"]][-1] == "deployed"
    assert db.query(VoltEmail).filter(VoltEmail.kind == "dev_deployed").count() == 2
    # Rollback is its own job, through the same checks.
    rb = c.post(_base(dev) + "/jobs/DEV-1/rollback", headers=H(db, dev["staff"])).json()
    assert rb["kind"] == "revert" and rb["ref"] == "DEV-2"
    n2 = _next(dev, run_id="780").json()
    assert n2["action"] == "revert" and n2["job"]["revert_sha"] == "b" * 40


def test_failures_retry_cancel_and_lease_expiry(dev):
    c, db = dev["c"], dev["db"]
    job = _submit(dev, dev["staff"]).json()
    _next(dev)
    _report(dev, job["id"], status="tests_failed", tests_passed=False, tests_summary="2 failed")
    assert c.post(_base(dev) + "/jobs/DEV-1/approve-merge", headers=H(db, dev["staff"])).status_code == 409
    r = c.post(_base(dev) + "/jobs/DEV-1/retry", headers=H(db, dev["collab"])).json()
    assert r["status"] == "queued" and r["attempt"] == 2
    assert _next(dev, run_id="801").json()["job"]["branch"] == "volt-dev/dev-1-a2"
    # The runner disappears: the lease expires and the job fails, retryable.
    j = db.query(VoltDevJob).one()
    from datetime import datetime, timedelta
    j.lease_expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    _next(dev, run_id="802")
    db.refresh(j)
    assert j.status == "failed" and "lease expired" in j.status_detail
    job2 = _submit(dev, dev["staff"], title="Second", request="Another small change to the page.").json()
    assert c.post(_base(dev) + "/jobs/%s/cancel" % job2["ref"], headers=H(db, dev["collab"])).json()["status"] == "canceled"
    assert _next(dev, run_id="803").json()["action"] == "none"                         # canceled is not picked up


def test_tests_or_docs_only_change_needs_no_deploy(dev):
    job = _submit(dev, dev["staff"]).json()
    _next(dev)
    _report(dev, job["id"], status="ready_to_test", changed_files=["docs/volt/notes.md"], tests_passed=True,
            pr_number=9, head_sha="d" * 40)
    dev["c"].post(_base(dev) + "/jobs/DEV-1/approve-merge", headers=H(dev["db"], dev["staff"]))
    _next(dev, run_id="900")
    r = _report(dev, job["id"], run_id="900", status="merged", merge_sha="e" * 40, merged_head_sha="d" * 40)
    assert r.json()["status"] == "deployed"


# ── event-driven dispatch (2026-11-09) ─────────────────────────────────────

class _GH:
    def __init__(self, status=204):
        self.calls, self.status = [], status

    def __call__(self, method, path, body=None, timeout=15):
        self.calls.append((method, path, body))
        if path.endswith("/dispatches"):
            return self.status, None, (None if self.status == 204 else "GitHub answered %d" % self.status)
        if "/compare/" in path:
            return 200, {"status": "ahead"}, None
        return 404, None, "nope"


@pytest.fixture()
def gh(dev, monkeypatch):
    from app.services.volt import devdispatch as D
    fake = _GH()
    monkeypatch.setattr(D, "_gh", fake)
    monkeypatch.setenv("VOLT_DEV_GITHUB_TOKEN", "ghp_secret_should_never_leak")
    return fake


def _dispatches(fake):
    return [c for c in fake.calls if c[1].endswith("/dispatches")]


def test_submit_dispatches_once_and_never_leaks_the_token(dev, gh):
    r = _submit(dev, dev["collab"], idempotency_key="k1")
    j = r.json()
    assert j["dispatch"]["state"] == "dispatched" and "dispatched" in j["status_label"]
    calls = _dispatches(gh)
    assert len(calls) == 1 and calls[0][1] == "/repos/MikeSimmonsAI/advisorflow-web/actions/workflows/volt-dev.yml/dispatches"
    assert calls[0][2]["ref"] == "main" and "DEV-1 start" in calls[0][2]["inputs"]["reason"]
    _submit(dev, dev["collab"], idempotency_key="k1")                                   # duplicate submit
    assert len(_dispatches(gh)) == 1
    # A fresh request is not re-sent on every look.
    dev["c"].get(_base(dev) + "/jobs", headers=H(dev["db"], dev["staff"]))
    assert len(_dispatches(gh)) == 1
    assert "ghp_secret" not in r.text and "ghp_secret" not in dev["c"].get(_base(dev) + "/jobs/DEV-1", headers=H(dev["db"], dev["staff"])).text
    ev = [e["action"] for e in j["events"]]
    assert ev[:2] == ["submitted", "dispatched"]
    # "Running" appears only when the runner really claims it.
    _next(dev)
    assert dev["c"].get(_base(dev) + "/jobs/DEV-1", headers=H(dev["db"], dev["staff"])).json()["status_label"].startswith("Running")


def test_failed_or_missed_start_is_resent_on_the_next_look(dev, gh):
    from datetime import datetime, timedelta
    gh.status = 503
    j = _submit(dev, dev["staff"]).json()
    assert j["dispatch"]["state"] == "failed" and "re-sent" in j["status_label"]
    gh.status = 204
    dev["c"].get(_base(dev) + "/jobs", headers=H(dev["db"], dev["collab"]))
    assert len(_dispatches(gh)) == 2
    row = dev["db"].query(VoltDevJob).one()
    assert row.dispatch_state == "dispatched" and row.dispatch_purpose == "resend"
    # Accepted, but no runner ever came (missed event): re-sent after 10 minutes.
    row.dispatched_at = datetime.utcnow() - timedelta(minutes=11)
    dev["db"].commit()
    dev["c"].get(_base(dev) + "/jobs", headers=H(dev["db"], dev["collab"]))
    assert len(_dispatches(gh)) == 3
    # Manual "send again" exists too, and refuses jobs that aren't waiting.
    assert dev["c"].post(_base(dev) + "/jobs/DEV-1/dispatch", headers=H(dev["db"], dev["staff"])).status_code == 200
    _next(dev)
    assert dev["c"].post(_base(dev) + "/jobs/DEV-1/dispatch", headers=H(dev["db"], dev["staff"])).status_code == 409


def test_without_a_token_it_says_so_truthfully(dev, monkeypatch):
    monkeypatch.delenv("VOLT_DEV_GITHUB_TOKEN", raising=False)
    j = _submit(dev, dev["staff"]).json()
    assert j["dispatch"]["state"] == "not_configured" and "scheduled check" in j["status_label"]
    assert dev["c"].get(_base(dev) + "/jobs", headers=H(dev["db"], dev["staff"])).json()["instant_start"] is False


def test_approval_dispatches_the_merge_once_and_runner_reports_more_work(dev, gh):
    job = _submit(dev, dev["collab"]).json()
    _submit(dev, dev["staff"], title="Second", request="Another harmless VOLT wording change.")
    n = _next(dev).json()
    assert n["job"]["ref"] == "DEV-1" and n["more"] == 1
    _report(dev, job["id"], status="ready_to_test", changed_files=["app/services/volt/core.py"], tests_passed=True,
            pr_number=7, head_sha="f" * 40)
    before = len(_dispatches(gh))
    c, db = dev["c"], dev["db"]
    c.post(_base(dev) + "/jobs/DEV-1/approve-merge", headers=H(db, dev["collab"]))
    c.post(_base(dev) + "/jobs/DEV-1/approve-merge", headers=H(db, dev["staff"]))          # repeat: no-op
    merges = [x for x in _dispatches(gh)[before:] if "merge" in x[2]["inputs"]["reason"]]
    assert len(merges) == 1


def test_backend_verifies_the_deploy_itself(dev, gh, monkeypatch):
    from app.services.volt import devdispatch as D
    job = _submit(dev, dev["staff"]).json()
    _next(dev)
    _report(dev, job["id"], status="ready_to_test", changed_files=["app/services/volt/core.py", "frontend/src/pages/volt/VoltDev.jsx"],
            tests_passed=True, pr_number=8, head_sha="1" * 40)
    dev["c"].post(_base(dev) + "/jobs/DEV-1/approve-merge", headers=H(dev["db"], dev["staff"]))
    _next(dev, run_id="881")
    _report(dev, job["id"], run_id="881", status="merged", merge_sha="2" * 40, merged_head_sha="1" * 40)
    # Frontend not deployed yet: stays "Merged - deploying".
    monkeypatch.setattr(D, "_running_commits", lambda: {"backend": "2" * 40, "frontend": None})
    j = dev["c"].get(_base(dev) + "/jobs/DEV-1", headers=H(dev["db"], dev["staff"])).json()
    assert j["status"] == "merged"
    row = dev["db"].query(VoltDevJob).one()
    row.deploy_checked_at = None
    dev["db"].commit()
    monkeypatch.setattr(D, "_running_commits", lambda: {"backend": "2" * 40, "frontend": "3" * 40})
    j = dev["c"].get(_base(dev) + "/jobs/DEV-1", headers=H(dev["db"], dev["staff"])).json()
    assert j["status"] == "deployed" and "Verified running in production" in j["deploy_detail"]
    assert dev["db"].query(VoltEmail).filter(VoltEmail.kind == "dev_deployed").count() == 2
