"""Where a batch is in the 7-step wizard, decided from persisted state only
(pure, no DB) so a refresh, a Back, or opening it from the ledger lands on the
same step. Steps: 1 Upload, 2 Map, 3 Analyze, 4 Classify, 5 Review Problems,
6 Approve, 7 Results."""
from __future__ import annotations

DONE = ("committing", "committed", "partially_committed", "rolled_back",
        "partially_rolled_back")


def workflow(status: str, analyzed: bool, classified: bool) -> dict:
    if status in DONE:
        step = 7
    elif status == "staged":
        step = 6
    elif status in ("processing", "interrupted"):
        # The wizard shows progress on whichever step asked for the run; the
        # step it resumes on afterwards is the one below.
        step = 5 if classified else 3
    elif status in ("mapping", "uploading") or (status == "failed" and not analyzed):
        step = 4 if (classified and analyzed) else 2
    elif analyzed:
        step = 5 if classified else 3
    else:
        step = 2
    return {"step": step, "analyzed": analyzed, "classified": classified}
