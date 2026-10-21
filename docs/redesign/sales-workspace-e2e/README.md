# Sales Workspace — local end-to-end check (Lane F, #281)

Runs the real app on this machine against throwaway synthetic data. Nothing
reaches production or a customer:

- `run_local.py` — SQLite file next to the script, startup hooks off
  (`lifespan="off"`), and the video meeting, calendar sync, prospect invitation,
  email and Twilio paths replaced by a recorder that writes to `outbox.log`.
  Synthetic people only (`@example.test`, 555-01xx numbers).
- The frontend must be built with `VITE_API_BASE_URL=http://127.0.0.1:8000`
  (its default is the production backend) and served on port 4173.
- `e2e.mjs` / `shots.mjs` — headless Chromium (`@sparticuz/chromium` +
  `puppeteer-core` + `axe-core`). Every request to a host other than
  127.0.0.1/localhost is aborted and reported.

```bash
python -I run_local.py /path/to/advisorflow-web &          # backend :8000
VITE_API_BASE_URL=http://127.0.0.1:8000 npx vite build --outDir /tmp/sw-dist
npx vite preview --outDir /tmp/sw-dist --port 4173 &         # frontend :4173
# log in once as rep@example.test / LocalTest!1, save the token to rep.tok,
# set availability (PUT /sales/availability/me), then:
node e2e.mjs ./out ./outbox.log
node shots.mjs ./shots ./rep.tok
```

Result on 2026-10-21: 42/42 checks pass (rep journey, server-side permission
refusals, keyboard, manager view, zero serious/critical axe issues on all seven
screens), no console errors, only blocked host `fonts.googleapis.com`.
