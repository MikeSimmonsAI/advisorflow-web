# Engineering remainder — Wholesale, 2026-10-07

Safe, executable next packets:
1. With pytest installed: run tests/test_wholesale_*.py and tests/test_universal_intake*.py; confirm 470 test functions / 487 baseline still pass.
2. DB-integration re-import test: import synthetic CSV, re-import, assert zero new contacts/properties (extends scripts/wholesale_stdlib_proof.py, which only proves key determinism).
3. Re-prove the Step-3 intake loop is gone (frontend flow).
4. Reproduce "AI returned no message" and missing-history defects against wholesale_ai.py with a stubbed model.
5. UX audit: nav, internal enum text, mobile and light theme, vanity links.
6. Staging manifest for the dedicated Wholesale environment (docs only, no provisioning).
7. Full synthetic end-to-end chain test, tagged by proof level.

SAFE WHOLESALE WORK REMAINS
