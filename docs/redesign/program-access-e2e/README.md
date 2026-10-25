# Program Access walkthrough (local only)

1. Start the real backend on a throwaway SQLite database, from this folder:
   `python -I run_local.py /path/to/advisorflow-web`
   Outbound email and SMS are replaced by a recorder. Demo links point at the local app (`PUBLIC_BASE_URL`).
2. Load the six accounts:
   `DATABASE_URL=sqlite:///$(pwd)/local.db JWT_SECRET='local-only-secret-not-used-anywhere-32chars!!' APP_ROOT=/path/to/advisorflow-web python fixture_program_access.py`
3. Build the frontend with `VITE_API_BASE_URL=http://127.0.0.1:8000` and serve it on 127.0.0.1:4173.
4. Run `node program_access.mjs ./out`. To also rehearse the prepared approval script, set
   `PA_GRANT_CMD="DATABASE_URL=… JWT_SECRET=… python /path/to/scripts/grant_wholesale_ccb_workspace.py"`.

Accounts covered:
- wholesale@ (A)
- salesperson (B)
- multi-company manager (C)
- single-workspace owner (D)
- executive (E)
- God admin (F)

For each account, the walkthrough checks:
- that the expected cards appear and nothing else does, with no duplicates;
- every card's destination, including after a refresh;
- the way back to Program Access, and sign out;
- the phone layout;
- previews opening the prospect's demo page in a new tab;
- data isolation, using one marker record per company (a login must never receive another company's marker, whatever header it sends);
- that God Mode is limited to the owner.

Result on 2026-10-25: **49/49 pass**, no console errors, only blocked host `fonts.googleapis.com` (see `RESULTS-2026-10-25.txt`).
