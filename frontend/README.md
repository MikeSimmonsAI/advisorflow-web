# AdvisorFlow Web — Frontend

A dark, console-style operations dashboard for SMS lead outreach. Built with
React + Vite. Talks to the FastAPI backend in `../backend`.

## What's here

- **Login** — JWT auth against `/auth/login`
- **Overview** — live KPIs (new/sent/hot/booked leads), hot reply feed, cadence health
- **Leads** — Excel upload with dry-run preview (tier breakdown, dedup count, compliance
  flags) before committing, plus a tier-review queue for untyped leads from the real
  Restland export
- **Replies** — full reply feed with a hot-only filter
- **Cadence** — 9-touch re-engagement status, manual "run due touches" trigger for admins
- **Email Queue** — leads with no phone number, batch-send via the email-only channel
- **Master Dashboard** (admin/super_admin only) — cross-advisor KPIs

## Design direction

Dark command-console aesthetic per Mike's "Tesla dashboard" brief — near-black base
(`#0a0e14`), a cool blue signal color for primary actions, green/red/amber signal colors
for state (hot, booked, DNC), monospace (JetBrains Mono) for all data — phone numbers,
counts, statuses — so it reads like telemetry rather than a generic SaaS template. The
signature element is the `SignalPulse` component: a small radar-ping animation used
anywhere something is live (a hot lead, an active connection) — ties back to the product's
real job, which is surfacing the moment someone responds.

## Running locally

```bash
cd frontend
npm install
npm run dev
```

Visit `http://localhost:5173`. By default it points at `http://localhost:8000` for the
API — set `VITE_API_BASE_URL` in a `.env` file to point elsewhere:

```
VITE_API_BASE_URL=https://your-backend.onrender.com
```

## Building for production

```bash
npm run build
```

Outputs to `dist/`. This is a static site — deploy `dist/` to Render Static Site, Vercel,
Netlify, or any static host. Set `VITE_API_BASE_URL` as a build-time environment variable
on whichever platform you use, pointed at your deployed backend.

## Deploying to Render (matches the backend's deployment guide)

1. Push this `frontend/` folder to the same GitHub repo as the backend (or a separate one)
2. In Render: **New > Static Site**, connect the repo, set root directory to `frontend`
3. Build command: `npm install && npm run build`
4. Publish directory: `dist`
5. Add environment variable: `VITE_API_BASE_URL` = your backend's Render URL
6. Deploy

## Notes on what's NOT wired up yet

- Google Calendar OAuth connect button isn't in the UI yet (backend route exists at
  `/calendar/connect` but needs the full OAuth redirect flow wired to a real page)
- No password reset / forgot-password flow — advisors log in with the temp password from
  `app/seed.py` and there's currently no in-app way to change it (worth prioritizing before
  handing this to your 5 advisors)
- Send/compose UI for SMS isn't built yet — leads can be imported and viewed, but actually
  triggering a send from the Leads screen (vs. via the cadence engine automatically) needs
  a compose modal
