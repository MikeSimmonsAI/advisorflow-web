# Sales Workspace — local end-to-end check (Lane F, #281)

Runs the real app on this machine against throwaway synthetic data. Nothing
reaches production or a customer:

- `run_local.py` — SQLite file next to the script, startup hooks off
  (`lifespan="off"`), and the video meeting, calendar sync, prospect invitation,
  email and Twilio paths replaced by a recorder that writes to `outbox.log`.
  Synthetic people only (`@example.test`, 555-01xx numbers).
- The frontend must be built with `VITE_API_BASE_URL=http://127.0.0.1:8000`
  (its default is the production backend) and served at **`http://localhost:5173`**.
  That address is already on the backend's built-in allowed list; the backend
  ignores any extra origin that is not `https://`, so `127.0.0.1:4173` (used
  before 2026-10-21 12:15) is refused and nothing can log in. Override with
  `SW_APP` (and `SW_APP_BEFORE`, default `http://localhost:5174`, for
  `before.mjs`) only with another allowed origin.
- `e2e.mjs` / `shots.mjs` / `before.mjs` — headless Chromium via `puppeteer-core`
  + `axe-core`. Every request to a host other than 127.0.0.1/localhost is aborted
  and reported. Browser: set `SW_CHROME` to a **headless-shell** build (e.g.
  Playwright's `chromium_headless_shell-*/chrome-linux/headless_shell`); without
  it, `@sparticuz/chromium` is used. **Do not use full Chrome in headless mode:**
  after a Call button opens a `tel:` link it stops delivering keystrokes to the
  page, so every later typing step fails (false failures 19d, K1, K2, M1).

```bash
python -I run_local.py /path/to/advisorflow-web &                  # backend :8000, seeds synthetic people
cd frontend && VITE_API_BASE_URL=http://127.0.0.1:8000 npx vite build --outDir /tmp/sw-dist
npx vite preview --outDir /tmp/sw-dist --port 5173 --strictPort --host localhost &
# the rep needs bookable time once:
TOK=$(curl -s -X POST http://127.0.0.1:8000/auth/login -H 'Content-Type: application/x-www-form-urlencoded' \
      --data 'username=rep%40example.test&password=LocalTest%211' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
echo -n "$TOK" > rep.tok
curl -X PUT http://127.0.0.1:8000/sales/availability/me -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
     -d '{"timezone":"America/Chicago","accepts_bookings":true,"min_notice_minutes":60,"booking_horizon_days":30,
          "windows":[{"day_of_week":0,"start_minute":0,"end_minute":1440},{"day_of_week":1,"start_minute":0,"end_minute":1440},
                     {"day_of_week":2,"start_minute":0,"end_minute":1440},{"day_of_week":3,"start_minute":0,"end_minute":1440},
                     {"day_of_week":4,"start_minute":0,"end_minute":1440},{"day_of_week":5,"start_minute":0,"end_minute":1440},
                     {"day_of_week":6,"start_minute":0,"end_minute":1440}]}'
python grant_demo.py ./local.db      # rep2 may present the Demo Suite (phone More check)
npm i puppeteer-core axe-core          # next to the scripts
SW_CHROME=/path/to/headless_shell node e2e.mjs ./out ./outbox.log
SW_CHROME=/path/to/headless_shell node shots.mjs ./shots ./rep.tok
```

Restart `run_local.py` before each full run (it rebuilds the throwaway database).

**Closeout run 2026-10-21 13:20 CDT** (phone More menu, phone call-result dialog, Do Not
Contact), on the branch after merging the "Demo & Closing" rename: **154/154 pass**, no console
errors, only blocked host `fonts.googleapis.com`. New checks: MN-* (phone More for a rep, a rep
with the Demo Suite grant and a manager — exact allowed pages, keyboard, Escape, outside tap,
44px, every destination opens), MD-* (longest Not Interested form at 390x844, 390x600 and
375x480 with a 2,000-character note — header and Save/Cancel stay on screen, a missing-date error
is shown in view without losing the note, saves in full), DNC-* (record from a call, duplicate by
phone format, follow-ups cleared, server 409s, booking refused, nothing sent, manager-only lift
with reason, history kept, nothing rescheduled).

**Independent re-run 2026-10-21 12:14 CDT** on head `c399da9` (a separate session, different
machine, steps above): **81/81 pass**, no console errors, only blocked host
`fonts.googleapis.com`.

Result on 2026-10-21 (after merging main 564806b): 49/49 checks pass — rep
journey to Won → Sold / Onboarding, Completed with the activity feed flooded,
manager personal vs team numbers, server-side permission refusals, keyboard,
zero serious/critical axe issues on all seven screens; no console errors; only
blocked host `fonts.googleapis.com`.

## Other Workspaces (#300)

After `run_local.py` has seeded, add the four workspace cases and run the walkthrough:

```
DATABASE_URL=sqlite:///$(pwd)/local.db JWT_SECRET='local-only-secret-not-used-anywhere-32chars!!' \
  APP_ROOT=/path/to/advisorflow-web python fixture_other_workspaces.py
SW_CHROME=/path/to/headless_shell node switcher.mjs ./out-switcher          # after
SW_CHROME=/path/to/headless_shell node switcher.mjs ./out-switcher before   # main, for comparison
```

Result on 2026-10-21 13:55 CDT (`fix/sales-workspace-switcher`): **108/108 pass**, no console
errors, only blocked host `fonts.googleapis.com`. Rep (no other workspace), manager with one,
manager with six + Executive Suite, owner (god_admin) at 1440, 1280, 820, 390 and 375 px; launcher
cards, entering a workspace and returning to Sales, phone More, `/auth/my-contexts` failure +
retry, keyboard, axe.
