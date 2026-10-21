# Program Access redesign — spec and record (#275)

**Screen:** `/workspaces` (`frontend/src/pages/program/ProgramAccessPortal.jsx`, mounted by `WorkspaceSelector` in `App.jsx` — App.jsx unchanged).
**Branch:** `redesign/program-access-v1` from `main` @ `9aca9dcaac4ebe5467aa56c58cfd065f98731504`, started 2026-10-21 00:35 CDT.

## What it shows
- Top bar: EvoSys Pro mark, **"Authorized workspaces only"** indicator, the signed-in person's real name/email (from the stored session user), Sign out.
- Hero: "Choose your business." + "One login. Separate operations. Open a program to work inside its own dedicated environment." Three points: one secure login, separate business environments, organized data and permissions. Right side: platform illustration (one secured hub, five business tiles).
- "Your workspaces": one branded card per authorized program — hero art, category, name, (org name when different), summary, info strip, colored **Open workspace** button. Grid/List toggle (both work; choice remembered per browser). A workspace whose kind of business has no identity yet (a new industry) gets a full card in the neutral EvoSys look (slate, network art, "Business operations") under its own name — decided by Mike 2026-10-21. Adding a dedicated identity later = one entry in `programAccessModel.js` + one palette/scene in `WorkspaceArt.jsx` + one `data-accent` in `evTokens.css`.

| Business | Accent | Art |
|---|---|---|
| SCI | Blue | Family + advisor, conversation bubble, appointment calendar, chapel |
| Atlantis Light & Power | Amber/Gold | Transmission tower, power lines to homes, bolt, enrollment card |
| Roofing | Orange | House with prominent roof, ladder, inspector with clipboard, passed badge |
| Wholesale | Green | Row of homes, key tag, signed contract, rising deal line, coin |
| Insurance | Teal | Shield with check, policy documents, protected home, advisor |
| Restland (funeral) / Back Office / Executive / other | Purple / Slate / Gray | Garden gateway / dashboard / network |

Layout: 5 across ≥1360px when there are five; auto-fill (min 258px) below; 2 columns on tablet; 1 on phones; no horizontal overflow.

## What did not change
- Which cards appear: still exactly `programCards(/auth/my-contexts)`. The model only gained display `facts`.
- Who sees what: unchanged — the neutral card only appears when the server listed that workspace for this login.
- Where cards go: `onChoose` in `App.jsx` is untouched. Verified destinations equal `main`: SCI → `/program/live`, Atlantis → `/workspace/<id>`, Roofing → `/program/roofing`, Wholesale → `/workspace/<host>?product=wholesale`, Insurance → `/workspace/<id>`.
- Single-workspace users still go straight in; signed-out visitors still go to `/login`. No backend, auth, God Mode or permission code touched.

## Verification
- `node --test frontend/tests/*.test.mjs`: 60 tests, 55 pass; the 5 failures are in `godTheme.test.mjs` and fail identically on unmodified `main` (inherited).
- New `frontend/tests/programAccessRedesign.test.mjs` (10 tests): five identities/order, server-only cards, no hard-coded businesses or placeholder names, routing via `onChoose`, art + accent per variant, no external images, tokens scoped, toggle wired, AA contrast on buttons.
- `npm run build` succeeds.
- Headless Chromium against the built app with stubbed API: 47/47 checks — desktop 1600/1366, tablet 820, mobile 390; no overflow; art renders; no console errors; navigation parity; keyboard Tab reaches every card action and Enter opens; a two-business user sees only those two; a new kind of business shows as a full card and opens its own workspace.

## Post-launch verification — 2026-10-21 10:30 CDT

**Not a live signed-in check.** The browsers available to the assistant were signed in to an Executive-only login, and the assistant cannot type passwords. What was done instead is a **controlled reproduction of the exact permissions**:

1. Read wholesale@'s and Michael Schlueter's real access rows from production (users, memberships, the 7 orgs they belong to, SCI program) — read-only.
2. Loaded those rows into a scratch database and ran the server's own `authorized_contexts()` (the code behind `/auth/my-contexts`) from production `main` @ `91b5fe0`.
3. Drove the production-built `/workspaces` page in Chromium with that exact answer, at desktop 1600, tablet 820 and mobile 390.

| Login | Cards | Result |
|---|---|---|
| wholesale@ (today's data) | SCI · Atlantis · Roofing (EvoSys Pro Roofing) · Wholesale · **Restland (own purple Cemetery & Funeral card)** · Insurance (Max Life) · Back Office | 19/19: no duplicates, all art renders, no sideways scroll, no console errors, real name shown, every card opens its own workspace (SCI → /program/live, Roofing → /program/roofing, Wholesale → EVO ?product=wholesale, Back Office → /sales, others → /workspace/<their id>) |
| Michael (today's data) | the same 7 + **WUPA** (Cemetery & Funeral, from his executive seat on that org) + Executive Suite | 20/20 |
| wholesale@ with Restland's 00:40 settings (Roofing + Insurance on) | **two "Roofing" cards** — Restland's opens the Roofing app | reproduces the reported bug on production code |
| same, with the identity fix (PR #290) | Restland back as its own card; one Roofing card | 19/19 |

Screenshots: `screenshots/program-access/live-repro-2026-10-21/`. Still open: one look as wholesale@ in a real browser session.
