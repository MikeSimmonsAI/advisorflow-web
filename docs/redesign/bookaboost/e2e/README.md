# BookaBoost Program Access - local screenshots (local only)

1. Backend on a throwaway SQLite file with synthetic data, from this folder:
   `python -I run_local.py /path/to/checkout 8010`
   (to show the Authorized preview card, the checkout must include feature/countryside-preview-grant;
   the script then adds one grant to the throwaway database only).
2. Build the frontend with `VITE_API_BASE_URL=http://127.0.0.1:8010` and serve `dist/` on localhost:5173
   (`python -I serve_spa.py dist 5173`); origin/main's build on localhost:5174 for the EvoSys before/after.
3. `python -I mockups.py ./out` - writes the PNGs and prints PASS/FAIL lines.
