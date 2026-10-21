"""The T6 queue worker exists, is dark, and is not wired into the deploy."""

import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOB = os.path.join(ROOT, "app", "jobs", "run_ai_workforce_worker.py")


def _load():
    spec = importlib.util.spec_from_file_location("wf_worker_job", JOB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_worker_does_nothing_unless_operations_are_enabled(monkeypatch):
    monkeypatch.delenv("AI_OPERATIONS_ENABLED", raising=False)
    assert _load().main() == 0


def test_worker_does_nothing_when_a_kill_switch_is_engaged(monkeypatch):
    monkeypatch.setenv("AI_OPERATIONS_ENABLED", "1")
    monkeypatch.setenv("AI_WORKFORCE_KILL", "1")
    assert _load().main() == 0


def test_worker_is_not_wired_into_render_or_main():
    for rel in ("render.yaml", os.path.join("app", "main.py")):
        text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        assert "run_ai_workforce_worker" not in text


def test_a_pass_with_no_employees_claims_nothing(db_session):
    out = _load().run_pass(db_session)
    assert out == {"organizations": 0, "employees": 0, "claimed": 0,
                   "ran": 0, "errors": 0}
