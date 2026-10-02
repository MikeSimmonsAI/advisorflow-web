"""NO ROUTE IS SILENTLY UNREACHABLE.

Starlette matches routes in registration order. A literal path declared AFTER
a templated one that also matches it (POST /x/builder/send after
POST /x/{id}/send) never runs: the request goes to the earlier handler, which
answers with its own auth and body rules. Nothing errors at startup, so the
only symptom is a button that "doesn't work".

KNOWN, documented, deliberately not changed here:
  POST /campaigns/builder/send   shadowed by /campaigns/{campaign_id}/send.
        Turning it on enables a bulk sender never run in production - a
        decision for the owner (handoff, Decisions #8).
  GET  /sales/video/status       declared twice; the scheduling router's copy
        (registered first) is the one VideoStatus.jsx reads. The proposal
        router's copy is dead code.
Anything else that appears here is a new bug.
"""
from starlette.routing import Match

KNOWN = {("POST", "/campaigns/builder/send"), ("GET", "/sales/video/status")}


def test_no_new_shadowed_routes():
    from app.main import app
    routes = [r for r in app.routes if getattr(r, "methods", None)]
    shadowed = set()
    for i, r in enumerate(routes):
        if "{" in r.path:
            continue
        for m in r.methods - {"HEAD", "OPTIONS"}:
            scope = {"type": "http", "path": r.path, "method": m, "root_path": ""}
            for q in routes[:i]:
                if q.matches(scope)[0] == Match.FULL:
                    shadowed.add((m, r.path))
                    break
    assert shadowed - KNOWN == set(), "unreachable routes: %s" % sorted(shadowed - KNOWN)
