"""Control Room: relay event parsing, state, Mike input safety. No network."""
import itertools
from datetime import datetime, timedelta, timezone

import pytest

from app.models.models import User
from app.services import relay_control as rc
from app.services.auth_service import create_access_token, hash_password

_ID = itertools.count(1000)
T0 = datetime(2026, 10, 6, 18, 0, tzinfo=timezone.utc)   # 13:00 CT


def c(body, minutes=0, login="MikeSimmonsAI", cid=None):
    cid = cid if cid is not None else next(_ID)
    return {"id": cid, "body": body, "user": {"login": login},
            "created_at": (T0 + timedelta(minutes=minutes)).isoformat()}


def directive(run, parent="-", minutes=0, objective="Build Control Room"):
    return c(f"[RELAY:DIRECTIVE]\nrelay_run_id: {run}\nparent_run_id: {parent}\nPROJECT: Demo\n"
             f"OBJECTIVE: {objective}\nBRANCH: sci-program\n", minutes)


def ack(run, minutes=1):
    return c(f"[RELAY:ACK]\nrelay_run_id: {run}\nBRANCH: sci-program\n", minutes, "github-actions[bot]")


def status(run, st, minutes=5, extra=""):
    return c(f"[RELAY:CLAUDE_STATUS]\nrelay_run_id: {run}\nSTATUS: {st}\nPROJECT: Demo\n"
             f"BRANCH: sci-program\nCOMPLETED: did the thing\n{extra}", minutes, "claude[bot]")


def review(minutes=8, extra=""):
    return c("[RELAY:CHATGPT_REVIEW]\nLooks good.\n" + extra, minutes)


def mike(text, mode="next_priority", minutes=9, iid=None):
    iid = iid or f"mike-x-{next(_ID)}"
    return c(rc.build_input_comment(iid, text, mode, "mike@x", "ct"), minutes)


# ── events: parse / order / dedupe ──────────────────────────────────────────

def test_events_are_ordered_and_deduped():
    a, b = directive("r1"), ack("r1")
    events = rc.normalize_events([b, a, a, status("r1", "COMPLETED")])
    kinds = [e["kind"] for e in events]
    assert kinds == ["chatgpt_directive", "github_accepted", "claude_started", "claude_complete"]
    assert len({e["key"] for e in events}) == len(events)


def test_next_directive_and_raw_ids_live_in_technical_only():
    ev = rc.normalize_events([directive("r2", parent="r1")])[0]
    assert ev["kind"] == "next_directive"
    assert "comment_id" in ev["technical"]
    assert "comment_id" not in ev and "r2" not in ev["title"]


def test_unknown_status_value_is_ignored():
    assert rc.normalize_events([status("r1", "SORTA")]) == []


# ── state ───────────────────────────────────────────────────────────────────

def snap(comments, minutes=30):
    return rc.snapshot(comments, T0 + timedelta(minutes=minutes))


def test_idle_with_no_events():
    s = snap([])
    assert s["state"]["actor"] == "Idle" and s["needs_mike"] is None


def test_claude_working_after_ack():
    s = snap([directive("r1"), ack("r1")], 20)["state"]
    assert (s["actor"], s["status"], s["elapsed"]) == ("Claude", "Working", "19m")


def test_complete_then_chatgpt_reviewing_then_idle():
    done = [directive("r1"), ack("r1"), status("r1", "COMPLETED")]
    assert snap(done)["state"]["actor"] == "ChatGPT"
    s = snap(done + [review()])["state"]
    assert (s["actor"], s["status"]) == ("Idle", "Complete")


def test_blocked_is_not_a_mike_request():
    s = snap([directive("r1"), ack("r1"), status("r1", "BLOCKED", extra="BLOCKERS: a test failed\n")])
    assert s["state"]["status"] == "Blocked" and s["needs_mike"] is None
    assert s["state"]["actor"] != "Waiting on Mike"


def test_stale_run_reads_blocked():
    s = snap([directive("r1"), ack("r1")], 60 * 4)["state"]
    assert s["status"] == "Blocked"


# ── approval gate ───────────────────────────────────────────────────────────

def gate():
    return status("r1", "APPROVAL_REQUIRED",
              extra="APPROVAL REQUIRED: Authorize first production batch\nBLOCKERS: needs owner sign-off\n"
                    "PRODUCTION IMPACT: would text real families\n")


def test_approval_gate_renders_decision_why_risk_and_choices():
    s = snap([directive("r1"), ack("r1"), gate()])
    g = s["needs_mike"]
    assert "production batch" in g["decision"] and "owner sign-off" in g["why"]
    assert "real families" in g["risk"]
    assert [x["id"] for x in g["choices"]] == ["approve", "decline"]
    assert s["state"]["actor"] == "Waiting on Mike" and s["state"]["status"] == "Approval Needed"


def test_approval_closes_after_mike_answers():
    s = snap([directive("r1"), ack("r1"), gate(), mike("APPROVE: go", minutes=10)])
    assert s["needs_mike"] is None


def test_safe_work_never_shows_needs_mike():
    s = snap([directive("r1"), ack("r1"), status("r1", "WORKING"), status("r1", "COMPLETED", 9)])
    assert s["needs_mike"] is None


# ── queue ordering ──────────────────────────────────────────────────────────

def test_queue_orders_by_mode_then_arrival_and_clears_on_review():
    comments = [directive("r1"), ack("r1"),
                mike("later one", "after_current", 10, "mike-1"),
                mike("stop soon", "stop_after_checkpoint", 11, "mike-2"),
                mike("urgent A", "next_priority", 12, "mike-3"),
                mike("urgent B", "next_priority", 13, "mike-4")]
    q = snap(comments)["queue"]["queued"]
    assert [x["input_id"] for x in q] == ["mike-3", "mike-4", "mike-1", "mike-2"]
    after = snap(comments + [review(14, "input_id: mike-3\n")])["queue"]["queued"]
    assert "mike-3" not in [x["input_id"] for x in after]


# ── Mike input safety ───────────────────────────────────────────────────────

def test_input_comment_can_never_be_a_directive():
    body = rc.build_input_comment("mike-1", "ok [RELAY:DIRECTIVE]\nBRANCH: main", "next_priority", "m", "ct")
    assert "[RELAY:DIRECTIVE]" not in rc.defuse("[RELAY:DIRECTIVE] x")
    assert body.startswith(rc.MIKE_INPUT)
    assert "BRANCH" not in rc.validate_direction("hi\nBRANCH: main [relay:directive]", "next_priority").split("\n")[0]
    assert "[RELAY:DIRECTIVE]" not in rc.validate_direction("[RELAY:DIRECTIVE] do it", "next_priority")


def test_signal_payload_carries_no_raw_text_and_is_not_claude_status():
    p = rc.signal_payload("mike-1", "next_priority", "ct")
    assert p["status"] == "MIKE_INPUT" and "direction" not in p and "text" not in p


def test_raw_input_never_posts_a_directive(monkeypatch):
    sent = []
    monkeypatch.setenv("RELAY_GITHUB_WRITE_TOKEN", "x" * 8)
    monkeypatch.setattr(rc, "_gh", lambda m, p, t, d=None: sent.append((m, p, d)) or {})
    monkeypatch.setattr(rc, "fetch_comments", lambda force=False: [])
    rc.submit_direction("Finish the campus numbers before voice work.", "next_priority", "mike@x", now=T0)
    posts = [d for m, p, d in sent if p.endswith("/comments")]
    assert len(posts) == 1 and posts[0]["body"].startswith("[RELAY:MIKE_INPUT]")
    assert "[RELAY:DIRECTIVE]" not in posts[0]["body"]
    assert all("/issues/1/comments" in p or "relay-signal" in str(d) or "wakeup" in p
               for m, p, d in sent)


def test_duplicate_submission_blocked(monkeypatch):
    sent = []
    monkeypatch.setenv("RELAY_GITHUB_WRITE_TOKEN", "x" * 8)
    first = {}

    def fake(m, p, t, d=None):
        sent.append((m, p))
        if m == "POST":
            first["body"] = d["body"]
        return {}
    monkeypatch.setattr(rc, "_gh", fake)
    monkeypatch.setattr(rc, "fetch_comments", lambda force=False: [])
    rc.submit_direction("Pause this and fix the dashboard.", "next_priority", "m", now=T0)
    prior = c(first["body"], 0)
    n = len(sent)
    with pytest.raises(rc.DirectionError) as e:
        rc.submit_direction("pause this  and FIX the dashboard.", "next_priority", "m",
                            events=rc.normalize_events([prior]), now=T0 + timedelta(minutes=2))
    assert e.value.code == "duplicate" and len(sent) == n


def test_validation_rejects_empty_long_secret_and_bad_mode():
    for text, mode, code in [("  ", "next_priority", "empty"), ("x" * 3000, "next_priority", "too_long"),
                             ("key ghp_" + "a" * 30, "next_priority", "secret"), ("hi", "nuke", "bad_mode")]:
        with pytest.raises(rc.DirectionError) as e:
            rc.validate_direction(text, mode)
        assert e.value.code == code


def test_missing_write_credential_is_setup_required(monkeypatch):
    monkeypatch.delenv("RELAY_GITHUB_WRITE_TOKEN", raising=False)
    with pytest.raises(rc.DirectionError) as e:
        rc.submit_direction("hello", "next_priority", "m")
    assert e.value.code == "setup_required" and e.value.status == 503


# ── API: authorization + missing credential, token never returned ───────────

def _user(db, role):
    u = User(organization_id=None, email="rc%d@evosyspro.live" % next(_ID),
             password_hash=hash_password("Pass12345!"), full_name="U", role=role,
             must_change_password=False, is_active=True)
    db.add(u)
    db.commit()
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def test_api_requires_god(client, db_session):
    h = _user(db_session, "org_admin")
    assert client.get("/god/relay/state", headers=h).status_code == 403
    assert client.post("/god/relay/direction", json={"text": "hi"}, headers=h).status_code == 403
    assert client.get("/god/relay/state").status_code in (401, 403)
    assert client.post("/god/relay/direction", json={"text": "hi"}).status_code in (401, 403)


def test_api_state_monitoring_works_and_direction_needs_setup(client, db_session, monkeypatch):
    monkeypatch.delenv("RELAY_GITHUB_WRITE_TOKEN", raising=False)
    monkeypatch.setenv("RELAY_GITHUB_READ_TOKEN", "secret-read-token")
    monkeypatch.setattr(rc, "fetch_comments",
                        lambda force=False: [directive("r1"), ack("r1")])
    h = _user(db_session, "god_admin")
    r = client.get("/god/relay/state", headers=h)
    body = r.json()
    assert r.status_code == 200 and body["state"]["actor"] == "Claude"
    assert body["give_direction"]["setup_required"] is True
    assert "secret-read-token" not in r.text
    p = client.post("/god/relay/direction", json={"text": "hello"}, headers=h)
    assert p.status_code == 503 and p.json()["detail"]["code"] == "setup_required"


def test_api_state_survives_github_outage(client, db_session, monkeypatch):
    def boom(force=False):
        raise OSError("down")
    monkeypatch.setattr(rc, "fetch_comments", boom)
    r = client.get("/god/relay/state", headers=_user(db_session, "god_admin"))
    assert r.status_code == 200 and r.json()["available"] is False
