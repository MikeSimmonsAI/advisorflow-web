# SCI readiness: stdlib harness (workaround, not a replacement)

`python3 -I scripts/sci_readiness_harness.py --md handoff/SCI_READINESS_HARNESS_RESULTS.md`
runs 116 deterministic scenarios against the real decision modules, with no
pytest, FastAPI, SQLAlchemy or network. Per-scenario results:
`handoff/SCI_READINESS_HARNESS_RESULTS.md`.

Pure logic extracted (app and harness import the same code):
- `app/services/optout_parser.py` (from reply_classification_service)
- `app/services/programs/reply_rules.py` (from responses.py: classify, intents,
  urgency, draft reply, plus `cadence_action`, `alert_plan`, `staff_alert_gate`,
  `duplicate_inbound_result`, now used by responses.py and sms_router.py)
- `app/services/programs/alias_rules.py` (from aliases.py)

Defect found and fixed: "you have the wrong number" was classed WRONG_PERSON
instead of BAD_DATA.

## STILL UNRUN: remains a launch blocker until a prepared env or CI runs them
- tests/test_sci_readiness_matrix.py, test_sci_regional_pools.py, test_sci_campus_lock.py
- tests/test_reply_classification_service.py, test_outreach_program.py, test_sci_platinum.py
- all DB-backed integration: webhook -> Reply/ProgramResponse rows, CadenceState
  writes, PhoneNumber pool rows, Twilio signature guard, unique-index duplicate race,
  alert Notification rows, campuses.plan against a database.

## Controlled Test console (relay sci-controlled-proof-console-20261006-1954)
`app/services/programs/readiness_check.py` holds the ten synthetic checks and the
launch-gate list (mirror of `SCI_GO_NO_GO_CHECKLIST.md`). Endpoints:
`GET /program/readiness-test`, `POST /program/readiness-test/run` (manager only).
UI: Program Center tab "Launch Readiness" plus a verdict banner on the Dashboard.
The harness runs the same checks (group "console"; now 129 scenarios, all PASS).
Results are held in memory per workspace and reset on restart: no existing audit
table fits, so nothing is persisted. Synthetic data only; nothing is sent.
The JSX was not build-checked here (no node_modules on the runner).

## Signed webhook simulation (gate T4)

`app/services/programs/webhook_simulation.py` signs synthetic SMS/voice requests with a run-local random token (never stored, logged or returned) using the pure `app/utils/twilio_signature.py`, which `twilio_security.py` now delegates to. It verifies before deciding, then routes with the real `regional_pools` / `reply_rules`. 13 scenarios, shown in Launch Readiness as "Signed webhook proof". Outbound is always 0. The harness now runs 147 scenarios.

Still unrun (needs FastAPI/SQLAlchemy/pytest): the real routes (`/sms/webhook/inbound`, `/voice/*`), AccountSid to credential lookup, cross-org gates, PhoneNumber rows, DB side effects. The 844 and Oaklawn queue names in the simulation are simulation-level; confirm them in the real route test.
