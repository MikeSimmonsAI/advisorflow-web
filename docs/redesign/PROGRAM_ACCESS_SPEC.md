# Program Access redesign — spec and record (#275)

**Screen:** `/workspaces` (`frontend/src/pages/program/ProgramAccessPortal.jsx`, mounted by `WorkspaceSelector` in `App.jsx` — App.jsx unchanged).
**Branch:** `redesign/program-access-v1` from `main` @ `9aca9dcaac4ebe5467aa56c58cfd065f98731504`, started 2026-10-21 00:35 CDT.

## What it shows
- Top bar: EvoSys Pro mark, **"Authorized workspaces only"** indicator, the signed-in person's real name/email (from the stored session user), Sign out.
- Hero: "Choose your business." + "One login. Separate operations. Open a program to work inside its own dedicated environment." Three points: one secure login, separate business environments, organized data and permissions. Right side: platform illustration (one secured hub, five business tiles).
- "Your workspaces": one branded card per authorized program — hero art, category, name, (org name when different), summary, info strip, colored **Open workspace** button. Grid/List toggle (both work; choice remembered per browser). "Other authorized workspaces" list for workspaces that aren't a named program.

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
- Where cards go: `onChoose` in `App.jsx` is untouched. Verified destinations equal `main`: SCI → `/program/live`, Atlantis → `/workspace/<id>`, Roofing → `/program/roofing`, Wholesale → `/workspace/<host>?product=wholesale`, Insurance → `/workspace/<id>`.
- Single-workspace users still go straight in; signed-out visitors still go to `/login`. No backend, auth, God Mode or permission code touched.

## Verification
- `node --test frontend/tests/*.test.mjs`: 59 tests, 54 pass; the 5 failures are in `godTheme.test.mjs` and fail identically on unmodified `main` (inherited).
- New `frontend/tests/programAccessRedesign.test.mjs` (9 tests): five identities/order, server-only cards, no hard-coded businesses or placeholder names, routing via `onChoose`, art + accent per variant, no external images, tokens scoped, toggle wired, AA contrast on buttons.
- `npm run build` succeeds.
- Headless Chromium against the built app with stubbed API: 45/45 checks — desktop 1600/1366, tablet 820, mobile 390; no overflow; art renders; no console errors; navigation parity; keyboard Tab reaches every card action and Enter opens; a two-business user sees only those two.
