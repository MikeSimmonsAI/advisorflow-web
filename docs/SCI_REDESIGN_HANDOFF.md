# SCI Command Center Redesign — Handoff (2026-10-12)

**Branch:** `feature/sci-command-center-redesign` (based on `main` @ 7485c0a, PR #93)
**Status:** all ten pages rebuilt; NOT merged, NOT deployed. Production untouched.

## What changed (frontend only — zero backend changes)
- `frontend/src/pages/program/ProgramCenter.jsx` — shell: collapsible sidebar (remembers state), dark top bar, global search → Review, location filter (39 facilities + the review bucket labeled "not a facility"), identity chip = signed-in person (refreshed from `/settings/profile`) + "SCI Family Service Center" + role. Tab changes are history entries (Back/Forward work).
- `frontend/src/pages/program/ProgramCenter.css` — full EvoSys Pro navy/gold design system (tokens, KPI tiles, panels, tables, drawer, modal, responsive 390→1920, WCAG AA text).
- `frontend/src/pages/program/sci/ui.jsx` — shared components: PageHead band, Kpi, Panel, MoreLink, FilterChips, Tabs, SearchBox, Drawer, TypedConfirm, Notice, BarRow, KV.
- All ten pages: Dashboard, Send, Responses, Review, Locations, Campaigns, Assets, Launch, Health, Settings (+ Surveys compact mode).

## Behavior changes worth knowing
1. **Choose & Send** now requires the person to type `SEND N` (previously the page auto-filled it). Server max per approval read from the server (`max_per_approval`), not hard-coded.
2. **Campaign activation** is an in-page typed-name dialog (was `window.prompt`); server enforcement unchanged.
3. **Review** exposes the existing audited `POST /program/records/{id}/review` (mark data note / duplicate reviewed with a note). Hold/release stays separate.
4. **Health** shows bounces/complaints as "Not tracked" when delivery events (webhook) are off.
5. **39 vs 40:** 39 mapped facilities + the "Location review" bucket (contacts with no location). Dashboard and Locations show the bucket as its own labeled row.

## Booking / calendar compatibility
- No booking or calendar code touched. Settings → Integrations shows `booking_user_id` read-only ("Linked / Not set"). `appointment_link` still edited on Locations via the existing PATCH. Appointments KPI still deep-links to `/availability`.
- Rebased cleanly over PR #92 and #93 (booking links / location subdomain links).

## Verification done (local, synthetic data only)
- Frontend build: pass. Frontend node tests: 2/3 pass; `godTheme.test.mjs` fails identically on untouched `main` (pre-existing, God Mode files).
- Backend SCI tests (outreach program, choose & send, booking, readiness console, email hold): 134 passed.
- Click-through QA: 36/36 (no approve, activation or settings save made).
- No horizontal page overflow at 1920×1080, 1366×768, 1024×768, 390×844.
- axe-core: 0 serious/critical on all ten pages.
- Local run: SQLite + synthetic "Testfamily" contacts across the 39 real location names; no provider keys, so nothing could send. Screenshots are from that running app, not production data.

## Not verified / outstanding
- Not run against production or shared staging data (no access from this session; `sci-program` staging is ~170 commits behind main).
- Inter font is loaded from Google Fonts; the sandbox blocked it, so screenshots use the fallback font. Real browsers will show Inter.
- Real role check: QA ran as a workspace manager; read-only (team member) states were coded but only spot-checked by reading.

## To ship (owner GO required)
1. Review the draft PR + screenshots.
2. Merge to `main` → Render builds the frontend (`cd frontend && npm ci && npm run build`).
3. Open `/program` on production and check: identity chip, 39 vs bucket, Launch verdict, Health facts.
