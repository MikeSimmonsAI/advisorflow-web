# Restland BookIT

**Book It. Close It. Track It.** — Restland's family service advisor platform inside EvoSys Pro
(owner directive v2.0, Nov 25 2026). Powered by EvoSys Pro.

* App: `/program/restland-bookit` (frontend `frontend/src/pages/restlandBookit/`)
* API: `/restland-bookit/*` (`app/routers/restland_bookit_router.py`)
* Services: `app/services/bookit/` (core, sales, workflow, network, insights)
* Tables: `app/models/bookit_models.py` (+ `program_team_members`, `handoff_pending_edits`)
* Tests: `tests/test_restland_bookit.py`, `tests/test_restland_team_board.py`

## Status (see the PR for exact commits)

| Area | State |
|---|---|
| Data model, permissions, APIs | Implemented, tested |
| Advisor workspace (My Day, Appointments, Close Ticket, Follow-Ups, Families, Service Requests, Sales, Performance, Notifications) | Implemented |
| Evo 6 Block network (radial + list), contacts, enrollment requests | Implemented |
| ADD Lead Center (intake, case flow, family network) | Implemented |
| Command Center, advisor drilldown, team access controls, audit | Implemented |
| BookIT Copilot | Read-only answers from the caller's own records + one draft template. No LLM, no sends. |
| Mini-campaign sending | **Not active.** Enrollment stops at management approval until templates are approved. |
| Advisor logins | **Not created.** Profiles exist (synced from the Team tab); logins wait on the owner. |
| Opened to the team | **No.** Owner preview only (`bookit.live` off). |

## Who sees it now
Only the owner's logins (see `permissions.md`). Opening it to the team is the owner's decision.
