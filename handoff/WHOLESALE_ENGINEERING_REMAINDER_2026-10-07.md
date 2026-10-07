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

SAFE WHOLESALE WORK REMAINS
