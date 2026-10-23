"""God Mode redesign backend: dashboard trends and the GitHub launch status.

Trends: real and test activity are counted apart, and a comparison is never
computed against an empty previous period. Launch status: read-only GitHub
data mapped to projects, separate delivery states, "unknown" when it cannot
be checked, and no completion percentages.
"""
from datetime import datetime, timedelta, timezone

from app.models.models import Lead, Organization
from app.services import god_dashboard, launch_status


def _lead(db, org, days_ago, now, is_test=False):
    lead = Lead(organization_id=org.id, first_name="Synthetic", last_name="Lead",
                is_test=is_test, created_at=now - timedelta(days=days_ago))
    db.add(lead)
    return lead


def test_trends_separate_real_and_test_and_skip_zero_comparisons(db_session, sample_org):
    now = datetime(2026, 10, 22, 12, 0, 0)
    demo = Organization(name="Synthetic Demo Co", slug="synthetic-demo", plan="standard",
                        is_demo=True)
    db_session.add(demo)
    db_session.commit()
    _lead(db_session, sample_org, 1, now)
    _lead(db_session, sample_org, 2, now)
    _lead(db_session, sample_org, 2, now, is_test=True)
    _lead(db_session, demo, 3, now)
    db_session.commit()

    out = god_dashboard.trends(db_session, days=14, now=now)
    leads = out["leads"]
    assert len(out["days"]) == 14 and len(leads["real"]) == 14
    assert leads["real_total"] == 2
    assert leads["test_total"] == 2          # the test lead + the demo-org lead
    # Nothing in the previous 14 days -> no percentage invented against zero.
    assert leads["vs_previous"] is None
    # Fewer than the minimum messages -> no delivery rate.
    assert out["delivery_rate"]["pct"] is None


def test_trends_comparison_when_previous_period_has_data(db_session, sample_org):
    now = datetime(2026, 10, 22, 12, 0, 0)
    _lead(db_session, sample_org, 1, now)
    _lead(db_session, sample_org, 2, now)
    _lead(db_session, sample_org, 20, now)   # previous window
    db_session.commit()
    change = god_dashboard.trends(db_session, days=14, now=now)["leads"]["vs_previous"]
    assert change == {"previous": 1, "pct": 100}


def test_trends_endpoint_is_god_only(client, auth_headers):
    assert client.get("/god/dashboard/trends", headers=auth_headers).status_code in (401, 403)


# ── launch status ──────────────────────────────────────────────────────────

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def _pr(n, branch, title, *, merged=None, draft=False, sha=None, base="main"):
    return {"number": n, "title": title, "html_url": "https://github.com/x/y/pull/%d" % n,
            "head": {"ref": branch, "sha": (sha or "h%d" % n)}, "base": {"ref": base},
            "draft": draft, "state": "closed" if merged else "open",
            "merged_at": merged, "merge_commit_sha": ("m%d" % n) if merged else None,
            "updated_at": "2026-10-21T10:00:00Z", "user": {"login": "dev"}}


def _fake_github(open_prs, closed, checks=None, compare=None):
    calls = []

    def get(path):
        calls.append(path)
        if path.startswith("/pulls?state=open"):
            return open_prs
        if path.startswith("/pulls?state=closed"):
            return closed
        if path.startswith("/commits/"):
            sha = path.split("/")[2]
            return {"check_runs": (checks or {}).get(sha, [])}
        if path.startswith("/compare/"):
            base = path.split("/compare/")[1].split("...")[0]
            return {"status": (compare or {}).get(base, "behind")}
        raise AssertionError("unexpected GitHub call " + path)
    get.calls = calls
    return get


def test_project_mapping_uses_whole_words():
    assert launch_status._project_for("claude/sci-staging-fix", "") == "sci"
    assert launch_status._project_for("fix/discipline-check", "") == "platform"
    assert launch_status._project_for("bookaboost-wholesale-intake", "") == "bookaboost"
    assert launch_status._project_for("feat/aiwf-jobs", "") == "ai_workforce"
    assert launch_status._project_for("x", "Roofing estimate page") == "roofing"


def test_board_states_are_separate_and_never_percentages():
    get = _fake_github(
        open_prs=[_pr(10, "sci-fix", "SCI fix", sha="s10"),
                  _pr(11, "redesign/god-mode-v1", "God Mode redesign", draft=True)],
        closed=[_pr(5, "ai-workforce-jobs", "AI jobs", merged="2026-10-20T10:00:00Z"),
                _pr(4, "old-roof", "roof", merged="2026-09-01T10:00:00Z")],  # outside window
        checks={"s10": [{"status": "completed", "conclusion": "failure"}]},
        compare={"m5": "ahead"})
    out = launch_status.build(get, now=NOW, deployed="deadbeef")
    assert out["available"] is True
    by = {p["key"]: p for p in out["projects"]}
    assert [p["label"] for p in out["projects"]] == [
        "AI Workforce", "SCI", "BookaBoost", "Wholesale", "Atlantis", "Roofing", "Platform Core"]

    ladder = {s["key"]: s["state"] for s in by["ai_workforce"]["ladder"]}
    assert ladder["pr_merged"] == "yes" and ladder["production"] == "yes"
    assert ladder["staging"] == "unknown" and ladder["verified"] == "unknown"
    assert by["ai_workforce"]["status"] == "released"

    assert by["sci"]["status"] == "blocked"
    assert "failing" in by["sci"]["blocker"]
    assert {s["key"]: s["state"] for s in by["sci"]["ladder"]}["pr_merged"] == "no"

    assert by["platform"]["status"] == "needs_owner"
    assert by["roofing"]["status"] == "quiet"          # its merge is older than the window

    assert "%" not in repr(out)
    assert not any(c.split("?")[0].startswith(("/merges", "/git/")) for c in get.calls)


def test_merged_but_not_in_production_is_reported_as_deploying():
    get = _fake_github([], [_pr(7, "atlantis-x", "Atlantis", merged="2026-10-21T10:00:00Z")],
                       compare={"m7": "behind"})
    by = {p["key"]: p for p in launch_status.build(get, now=NOW, deployed="abc")["projects"]}
    assert by["atlantis"]["status"] == "deploying"
    assert {s["key"]: s["state"] for s in by["atlantis"]["ladder"]}["production"] == "no"


def test_unknown_deploy_commit_is_unable_to_verify():
    get = _fake_github([], [_pr(7, "atlantis-x", "Atlantis", merged="2026-10-21T10:00:00Z")])
    by = {p["key"]: p for p in launch_status.build(get, now=NOW, deployed="")["projects"]}
    assert {s["key"]: s["state"] for s in by["atlantis"]["ladder"]}["production"] == "unknown"


def test_missing_credentials_and_github_failure_fail_closed(monkeypatch):
    for k in ("RELAY_READ_TOKEN", "RELAY_GITHUB_READ_TOKEN", "RELAY_GITHUB_WRITE_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    out = launch_status.build(now=NOW, deployed="x")
    assert out["available"] is False and "not configured" in out["reason"]
    assert all(s["state"] == "unknown" for p in out["projects"] for s in p["ladder"])

    def boom(path):
        raise RuntimeError("down")
    out = launch_status.build(boom, now=NOW, deployed="x")
    assert out["available"] is False and len(out["projects"]) == 7


def test_github_endpoint_is_god_only(client, auth_headers):
    assert client.get("/god/launch-board/github", headers=auth_headers).status_code in (401, 403)
