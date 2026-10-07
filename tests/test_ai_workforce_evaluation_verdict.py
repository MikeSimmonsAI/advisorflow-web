"""Dependency-free tests: an evaluation that executed zero scenarios must not
produce the verdict "PASSED".

Run: python3 -m unittest tests.test_ai_workforce_evaluation_verdict

Evidence level: BEHAVIOURAL on the real evaluation._verdict with stubbed
sqlalchemy/model/simulator imports. NOT proven: DB persistence, HTTP.
"""
import importlib.util
import os
import sys
import types
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
WF = os.path.join(ROOT, "app", "services", "workforce")


def _load():
    names = ["sqlalchemy", "sqlalchemy.orm", "app", "app.utils",
             "app.utils.time_fmt", "app.models", "app.models.workforce_models",
             "app.services", "app.services.workforce"]
    saved = {k: sys.modules.get(k) for k in names}
    for n in names:
        sys.modules[n] = types.ModuleType(n)
    sys.modules["sqlalchemy.orm"].Session = object
    sys.modules["app.utils.time_fmt"].iso_utc = lambda d: d
    wm = sys.modules["app.models.workforce_models"]
    wm.AIEvaluationResult = wm.AIEvaluationRun = object
    sys.modules["app.services.workforce"].simulator = types.ModuleType("sim")
    try:
        spec = importlib.util.spec_from_file_location(
            "wf_evaluation_t", os.path.join(WF, "evaluation.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


E = _load()


class Verdict(unittest.TestCase):
    def test_zero_scenarios_is_not_passed(self):
        v = E._verdict({"total": 0, "passed": 0, "failed": 0}, [])
        self.assertNotEqual(v, "PASSED")
        self.assertTrue(v.startswith("NOT RUN"))

    def test_real_results_unchanged(self):
        self.assertEqual(E._verdict({"total": 3, "passed": 3, "failed": 0}, []),
                         "PASSED")
        self.assertTrue(E._verdict({"total": 3, "passed": 2, "failed": 1},
                                   []).startswith("PASSED WITH FAILURES"))
        self.assertTrue(E._verdict({"total": 3, "passed": 2, "failed": 1},
                                   [{"key": "k"}]).startswith("BLOCKED"))


if __name__ == "__main__":
    unittest.main()
