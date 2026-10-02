"""READINESS - components reported separately; optional providers never make the app unhealthy."""
from app.services import readiness


def test_health_stays_a_bare_liveness_probe(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_ready_reports_components_and_no_provider_detail_publicly(client):
    r = client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert body["database"]["status"] == "ok" and "latency_ms" in body["database"]
    assert body["app"]["status"] == "ok"
    assert "providers" not in body                      # public: no capability inventory


def test_missing_optional_providers_do_not_change_overall(db_session, monkeypatch):
    for k in ("OPENAI_API_KEY", "RESEND_API_KEY", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
              "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "STRIPE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    rep = readiness.report(db_session, detail=True)
    assert rep["status"] == "ok"
    p = {x["key"]: x for x in rep["providers"]}
    assert p["sms"]["status"] == "not_configured" and p["push"]["status"] == "not_configured"
    assert all(x["optional"] for x in rep["providers"])
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "x")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "y")
    p = {x["key"]: x for x in readiness.report(db_session, detail=True)["providers"]}
    assert p["push"]["status"] == "configured"
    assert "x" not in str(p["push"]) and "y" != p["push"].get("note")   # never a secret value


def test_a_failed_job_degrades_but_does_not_fail(db_session):
    from app.models.job_models import JobRun
    db_session.add(JobRun(job_name="cadence_loop", status="error"))
    db_session.commit()
    rep = readiness.report(db_session, detail=True)
    assert rep["status"] == "degraded" and rep["jobs"]["failed"] == 1
    assert any(j["job"] == "cadence_loop" and j["last_status"] == "error" for j in rep["jobs"]["items"])


def test_system_health_is_owner_only(client, auth_headers):
    assert client.get("/god/system-health", headers=auth_headers).status_code in (401, 403)
