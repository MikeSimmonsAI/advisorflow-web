"""The relay guard decides what wakes Claude: authorized, new, deduped, never main."""
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "relay_guard", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts", "relay", "relay_guard.py"))
rg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rg)

DIRECTIVE = """[RELAY:DIRECTIVE]
relay_run_id: r-2026-10-06-01
PROJECT: SCI
PRIORITY: P0
OBJECTIVE: Return RELAY TEST PASS with current CT timestamp and branch name.
ENVIRONMENT: sci-program / staging
DO NOT TOUCH: main, production
"""
KW = dict(issue=1, actors=["MikeSimmonsAI"], branches=["sci-program"])


def ev(body, login="MikeSimmonsAI", assoc="OWNER", number=1, cid=101, action="created"):
    return {"action": action, "issue": {"number": number},
            "comment": {"id": cid, "body": body, "user": {"login": login}, "author_association": assoc}}


def test_an_authorized_new_directive_runs_on_the_named_branch():
    run, why, info = rg.decide(ev(DIRECTIVE), [], **KW)
    assert run and info["run_id"] == "r-2026-10-06-01" and info["branch"] == "sci-program"
    assert info["project"] == "SCI"


def test_strangers_and_untrusted_associations_never_run():
    assert not rg.decide(ev(DIRECTIVE, login="someone-else"), [], **KW)[0]
    assert not rg.decide(ev(DIRECTIVE, assoc="NONE"), [], **KW)[0]
    assert not rg.decide(ev(DIRECTIVE, assoc="CONTRIBUTOR"), [], **KW)[0]


def test_only_the_relay_issue_and_only_new_comments():
    assert not rg.decide(ev(DIRECTIVE, number=2), [], **KW)[0]
    assert not rg.decide(ev(DIRECTIVE, action="edited"), [], **KW)[0]


def test_claude_never_reacts_to_status_ack_review_or_plain_comments():
    for body in ("[RELAY:CLAUDE_STATUS]\nrelay_run_id: x1\nSTATUS: COMPLETED",
                 "[RELAY:ACK] relay_run_id: x1",
                 "[RELAY:CHATGPT_REVIEW]\nlooks good",
                 "[RELAY:APPROVAL_REQUIRED]\nneed Mike",
                 "CLAUDE STATUS: COMPLETED (re: ...)",          # legacy human-format status
                 DIRECTIVE + "\n[RELAY:CLAUDE_STATUS]"):        # a status quoting a directive
        assert not rg.decide(ev(body), [], **KW)[0], body


def test_the_same_directive_never_runs_twice():
    acked = [{"body": "[RELAY:ACK] relay_run_id: r-2026-10-06-01\nstatus: ACCEPTED"}]
    run, why, _ = rg.decide(ev(DIRECTIVE, cid=102), acked, **KW)
    assert not run and "duplicate" in why
    done = [{"body": "[RELAY:CLAUDE_STATUS]\nrelay_run_id: r-2026-10-06-01\nSTATUS: COMPLETED"}]
    assert not rg.decide(ev(DIRECTIVE), done, **KW)[0]


def test_a_directive_without_a_run_id_is_keyed_by_its_comment():
    body = DIRECTIVE.replace("relay_run_id: r-2026-10-06-01\n", "")
    run, _, info = rg.decide(ev(body, cid=555), [], **KW)
    assert run and info["run_id"] == "c555"
    assert not rg.decide(ev(body, cid=555), [{"body": "[RELAY:ACK] relay_run_id: c555"}], **KW)[0]


def test_main_is_never_a_relay_branch():
    body = DIRECTIVE.replace("ENVIRONMENT: sci-program / staging", "BRANCH: main")
    run, _, info = rg.decide(ev(body), [], issue=1, actors=["MikeSimmonsAI"], branches=["main", "sci-program"])
    assert run and info["branch"] == "sci-program"
    assert rg.decide(ev(body), [], issue=1, actors=["MikeSimmonsAI"], branches=["main"])[0] is False


def test_status_parsing_for_the_chatgpt_side():
    assert rg.status_of("[RELAY:CLAUDE_STATUS]\nrelay_run_id: a1\nSTATUS: COMPLETED") == "COMPLETED"
    assert rg.status_of("[RELAY:CLAUDE_STATUS]\nSTATUS: APPROVAL_REQUIRED") == "APPROVAL_REQUIRED"
    assert rg.status_of("[RELAY:CLAUDE_STATUS]\nSTATUS: WORKING") == "WORKING"
    assert rg.status_of("[RELAY:DIRECTIVE]\nSTATUS: COMPLETED") is None


def test_dry_run_of_the_workflow_guard_step(tmp_path, monkeypatch):
    """The 'decide' step end to end with the GitHub API faked: one ACK, the
    directive handed to Claude, outputs written - and a re-delivery skipped."""
    import json
    posted = []
    store = []
    monkeypatch.setattr(rg, "_all_comments", lambda issue: list(store))

    def fake_api(method, path, data=None):
        posted.append((method, path, data))
        store.append({"body": data["body"]})
    monkeypatch.setattr(rg, "_api", fake_api)
    evf = tmp_path / "event.json"
    evf.write_text(json.dumps(ev(DIRECTIVE.replace(
        "Return RELAY TEST PASS", "Return RELAY TEST PASS (dry run)"))))
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(evf))
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    assert rg.main(["x", "decide"]) == 0
    first = out.read_text()
    assert "run=true" in first and "branch=sci-program" in first
    assert len(posted) == 1 and posted[0][2]["body"].startswith("[RELAY:ACK] relay_run_id: r-2026-10-06-01")
    assert "RELAY TEST PASS" in (tmp_path / "relay-directive.md").read_text()
    out.write_text("")
    assert rg.main(["x", "decide"]) == 0            # GitHub re-delivers the same event
    assert "run=false" in out.read_text() and len(posted) == 1
