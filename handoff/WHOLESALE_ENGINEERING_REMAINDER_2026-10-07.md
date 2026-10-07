# Engineering remainder — Wholesale, 2026-10-07

## Update (relay wholesale-ai-comms-safety-20261007-0002)

SYNTHETIC LOGIC evidence only (`python3 scripts/wholesale_ai_comms_proof.py`, 33/33):

- `wholesale_ai._deterministic_read`: "Who is this?" no longer maps to wrong_person
  (which auto-killed the deal); it now routes to a person. Bare "end"/"quit" count
  as STOP only as the whole message, so "we can end up closing in 30 days" no
  longer writes a real DNC suppression. STOP phrases, "quit texting me", etc. still do.
- `apply_seller_reply`: seller Lead lookup is now scoped to the org.
- Compose/history: the wholesale module has no compose path; AI compose is
  `/ai-conversation/preview` (Leads.jsx reads `reply`, already fixed). Not re-proven
  against a DB or live provider.
- Still unproven (needs pytest/DB): wrong-person does not suppress the phone
  (deal goes dead and cadence stops, but the number is not suppressed);
  idempotency, capacity gates and meaningful-reply cadence pause were not re-proven.

Safe, executable next packets:
1. With pytest installed: run tests/test_wholesale_*.py and tests/test_universal_intake*.py; confirm 470 test functions / 487 baseline still pass.
2. DB-integration re-import test: import synthetic CSV, re-import, assert zero new contacts/properties (extends scripts/wholesale_stdlib_proof.py, which only proves key determinism).
3. Re-prove the Step-3 intake loop is gone (frontend flow).
4. Reproduce "AI returned no message" and missing-history defects against wholesale_ai.py with a stubbed model.
5. UX audit: nav, internal enum text, mobile and light theme, vanity links.
6. Staging manifest for the dedicated Wholesale environment (docs only, no provisioning).
7. Full synthetic end-to-end chain test, tagged by proof level.

## Update — relay wholesale-continue-after-protocol-fix-20261006-2359
- CORRECTION: the earlier scripts/wholesale_stdlib_proof.py phone checks were vacuous (555-010-xxxx is an invalid NANP area code, normalizes to None, None == None). Fixtures now use valid numbers plus a non-vacuous guard; 7/7 pass.
- NEW scripts/wholesale_matching_proof.py (SYNTHETIC LOGIC, stdlib only, sqlalchemy stubbed) drives the real intake matching rules. 15/15 pass: exact re-import with formatting drift gives all EXACT / zero NEW; new person NEW; shared email + different surname POSSIBLE; shared phone + different surname NEW (shared line); source-id exact; within-file first-occurrence-wins; colleagues not duplicates; determinism.
- Not yet done: DB-integration re-import, Step-3 frontend loop re-proof, seller comms safety dependency-free tests, buyer flows, UX audit. Leads.jsx bulk AI generate already reads `reply` (root cause of "AI returned no message"); wholesale_ai.py compose/history still to inspect.
- Run: python3 -I scripts/wholesale_stdlib_proof.py ; python3 -I scripts/wholesale_matching_proof.py

## Update — relay wholesale-suppression-idempotency-20261007-0007
- DEFECT FIXED: a wrong-person reply killed the deal and stopped the cadence but left the phone contactable (re-import, other deal, bulk path). apply_seller_reply now writes an org-scoped suppression entry (reason "Wrong number reported by recipient", idempotent, audit event seller.wrong_number); lead is not marked DNC; other tenants untouched.
- pytest/DB environment is installable (pip install -r requirements.txt pytest). NEW tests/test_wholesale_comms_safety.py: 8 pass (REAL service logic, in-memory SQLite, stubbed provider): wrong-number suppression + cadence stop + replay idempotency + send-boundary refusal for 4 sources; tenant isolation; repeated STOP x3 gives one suppression row, DNC, cadence stopped_dnc, 5 send sources refused; noise after STOP does not resume; wrong-number suppression holds over repeated cadence runs; capacity hold survives 4 retries with provider never reached; replayed cadence touch sent once; reply stops cadence before next step.
- Regression: 183 passed across comms-safety, cadence engine, wholesale cadence/sms program/flow/guards/seller progress/cross-tenant. Full 487-test baseline not re-run.
- Not proven: bulk-path and AI-compose bypass beyond the shared send_sms boundary; live provider; deployed auth.

## Update — relay wholesale-dependency-free-buyers-ux-20261007-0018
Evidence level for everything below: SYNTHETIC LOGIC / STATIC SOURCE (stdlib only, no pip, no DB, no provider). The new Python edits were py_compile'd but NOT exercised by pytest in this slice; run the DB suite once the full toolchain is available (gate: full-toolchain).
- DEFECT FIXED (send path): buyer deal-sheet SMS (`wholesale_disposition._send_sms`, which calls Twilio directly) never consulted the org suppression list, so a number that had replied STOP/wrong-number could be texted once filed as a buyer. `preflight` now refuses with code `suppressed`. Every other `messages.create` site is inventoried and registered in scripts/wholesale_send_path_contract.py (55 checks); an unregistered new provider call fails the run.
- Preview shape: /ai-conversation/preview returns reply/should_stop/reason/source/error_kind and Leads.jsx reads exactly those; no `result.message` read remains. No drift.
- DEFECT FIXED (matching): a strategy-only buy box scored 100 on every deal (strategy was "matched" unconditionally); strategy is now context-only (unscored). A box with nothing scoreable now says "not enough information" instead of a silent 0. Ranking ties are broken by name then id so order no longer depends on DB row order. 32 checks in scripts/wholesale_buyer_matching_proof.py (incl. all 120 input permutations rank identically).
- DEFENSE IN DEPTH (tenant): added org predicates to the contract fill sheet (property/seller/buyer), the approvals list, and recompute_matches' property lookup. scripts/wholesale_tenant_query_audit.py: 251 Wholesale ORM queries; 21 lacked an inline org predicate; 7 hardened, 14 reviewed and recorded with reasons (token- or parent-scoped). None was shown exploitable.
- DEFECT FIXED (closing): /close could be POSTed twice (overwriting closed_at, fee, payment reference, funded_at with no trace) and could "close" a dead deal. Both now 409; negative fee/amount now 400. CLOSED vs PAID stay distinct (no `paid` stage; funded only when a person records a fee; close without a fee reads "payment pending"). 49 checks in scripts/wholesale_closing_proof.py, including all 44 authenticated deal routes being org-scoped and e-sign `signed` requiring an attached executed copy.
- OPEN (owner decision, not changed): the stage machine allows moving to `closed` from any stage via /close (tests rely on this) and /fee-collected will mark a deal `funded` before it is closed. Recommend requiring contract + title record before close; needs Mike's call because existing tests close early-stage deals.
- UX: scripts/wholesale_ux_static_check.py (142 checks): every internal /wholesale destination resolves to a Route, every lazy import exists, no mojibake, no bare enum rendering. One real defect fixed (EvoEvaluation showed raw run mode/status keys). No browser/mobile/light-theme runtime proof; node cannot build here.
- Pre-existing: app/migrate_add_import_tables.py is committed truncated (SyntaxError); not imported, sends nothing, left as is.
- Run: python3 -I scripts/wholesale_send_path_contract.py ; wholesale_buyer_matching_proof.py ; wholesale_tenant_query_audit.py ; wholesale_closing_proof.py ; wholesale_ux_static_check.py

## Update — relay wholesale-regression-ux-proof-20261007-0025
Evidence: SYNTHETIC LOGIC / STATIC SOURCE. pytest, fastapi and sqlalchemy are not installed and the directive forbids installing; no DB-backed test, browser, or provider was run.
- NEW tests/test_wholesale_regression_20261007.py (21 cases, py_compile clean). 7 DB-free cases (strategy-only box, empty-box "not enough information", strategy adds no points, tie order across permutations, qualified-before-disqualified, nameless buyer) were EXECUTED through a pytest/model stub and passed 7/7. The other 14 need the DB suite and are UNEXECUTED: suppressed buyer SMS refused before `_send_sms` (and email channel unaffected); re-close and dead-close return 409 with the deal payload identical; negative close fee and negative /fee-collected amount return 400 with no mutation; later fee still accepted after close; matching ignores another org's buyers; another org gets 404 on match-buyers and matches. Fixtures were cross-checked against current routes, payloads and the `auth_headers`/`db_session` conftest fixtures.
- UX DEFECTS FIXED (frontend source, not rendered): (1) disposition results showed raw codes (`suppressed`, `opted_out`, `not_enabled`) in the pill; now plain words via `refusalLabel`, with the server's sentence beside it. (2) the closing panel offered "Close this deal" on a deal in Dead whenever deal_result was not set; now keyed on stage too (server returns 409). (3) close-fee and collected-amount inputs accepted negatives until the server rejected them; now blocked inline with an `aria-invalid`/`role="alert"` message. (4) buyer-match factor marks had no accessible name; now "Matched / Did not match / Not scored".
- scripts/wholesale_ux_static_check.py extended 142 -> 171 checks. Re-run of all harnesses: send paths 55/55, buyer matching 32/32, closing 49/49, UX 171/171, tenant audit 251 queries 0 unreviewed, stdlib 7/7, matching 15/15, ai comms 33/33.
- MIGRATION app/migrate_add_import_tables.py: DISPOSITION = dead/superseded, left unchanged. Evidence: one commit (b1ba6c9, Lead Import Intelligence Phase 1), 698 bytes, ends at `STEPS = [`; no import, alembic, render.yaml or deploy reference; import_batches/import_staged_rows are registered on Base (app/models/registry.py) and created by `create_all` in app/main.py; tests/test_import_intelligence.py GATE 21 documents it as redundant. The DDL steps are not recoverable, so it was not repaired or run. Side note: GATE 21's test checks app/migrations and app/patches, not app/, so it does not actually guard this file. Suggest deleting it (owner call; not done).
- Limits: no mobile-width or light-theme rendering (no frontend build/browser); the audit read source and CSS breakpoints only. Run `pytest tests/test_wholesale_regression_20261007.py tests/test_wholesale_disposition.py tests/test_wholesale_workflow.py tests/test_wholesale_matching.py` once the toolchain exists (gate: full-toolchain).
- OWNER GATE unchanged: whether close must require contract/title, and whether funding must require closed status. No policy changed.

SAFE WHOLESALE WORK REMAINS
