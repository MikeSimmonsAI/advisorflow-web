# OLP integrated review (Oct 29): temporary branch, not for main

The branch `integration/olp-review` is today's `main` plus three draft PRs, all of which merged without conflict:
- #435 `redesign/olp-radar`, which includes #428 `feature/bookaboost-overlooked-radar`;
- #434 `redesign/olp-login`.

**No PR is merged into main and nothing is deployed.** OLP is the visual direction for continued development, not yet
the production brand.

## 1. Results (all run on this branch)

| Check | Result |
|---|---|
| Frontend build: OLP, and rollback (`VITE_BOOKABOOST_PRODUCT_BRAND=bookaboost`) | both pass |
| Frontend unit tests | 159 / 164. The 5 failures are the God-theme tests that fail identically on `main` |
| Backend: auth, brand isolation, host resolution, BookaBoost isolation, tenant and context boundaries, cross-tenant attack list, radar, buyer matching (27 files) | **451 passed, 0 failed** |
| Browser: full signed-in walk-through, desktop 1440 and phone 390, plus another brand's user and the rollback build | **53 / 53** |
| Browser: OLP sign-in (6 widths, every state, auth, routing, permissions; other brands pixel-identical to `main`) | **98 / 98** |
| Browser: Opportunity Radar (filters, pagination, drawer, buyer matches, states, phone, EvoSys user, OLP identity) | **46 / 46** |

### The walk-through, screen by screen (`integrated/`)
1. **OLP sign-in.**
2. **Workspace selection.** Only this login's programs are offered. The Wholesale tile carries the OLP icon, and BookaBoost stays the platform logo at the top.
3. **Wholesale workspace.** The OLP rail logo shows, and the identity reads "BookaBoost Wholesale · Org Admin".
4. **Overlooked Properties (OLP radar).** It shows the real counts (15), no other tenant's record, and labelled representative photos.
5. **Property research.** The drawer shows sources and dates. The full research page opens on the same record.
6. **Back to the radar.** The buyer-matches view lists this workspace's GA buyers only and is read-only.
7. **Buyer Intelligence, then the Deal Room.**
8. **Identity and permissions.** Every server request carried the same single workspace. God Mode and the Executive Suite were refused. No JavaScript errors.

Also:
- **Another brand's user:** an EvoSys operator, naming BookaBoost's workspace directly, gets none of BookaBoost's records.
- **Rollback build:** BookaBoost sign-in, BookaBoost rail logo and the #428 radar look. Everything still works.

## 2. "Powered by BookaBoost": proposal (originals untouched)
In the supplied files, the endorsement's word gap is about 68 units where a normal word space is about 7 (52 vs 7 in the
horizontal file). The line is also small: on the sign-in card its capitals are about 4 px tall.

`proposed/*_endorsement_proposal.svg` keeps every other element byte-for-byte. It wraps the two endorsement groups so that:
- the gap is half a capital height, a normal word space;
- the line is 1.5x larger on the stacked logo and 1.3x on the horizontal logo;
- the line is re-centred (stacked) or left-aligned (horizontal).

The roofline, OLP letters and "OVERLOOKED PROPERTIES" are unchanged.

See `proposed/before-after-endorsement.png` (at the real UI sizes) and `proposed/signin-before-after-*.jpg` (the
sign-in page, local build only). To adopt it: approve, then replace the four `frontend/public/brands/olp/` files.

## 3. Consistency across the 24 OLP Wholesale screens
See `integrated/consistency-all-24-wholesale-screens.jpg`. Measured in the browser at 1440 px.

| # | Finding | Screens |
|---|---|---|
| C1 | **Two canvases.** Only Overlooked Properties is dark (dark canvas and dark top bar); the other 23 are light. Moving from the radar to research, buyers or the Deal Room flips dark to light. | all |
| C2 | **Blue accents in an OLP gold workspace.** Deal Room has 22 blue elements: the blue hero, the blue "Jordan", blue buttons. Investor Outreach has 6 (blue tab). Buyer Intelligence has a blue "Open Deal Room" card. Buy Boxes has 1. Everything else uses gold. | Deal Room, Investor Outreach, Buyer Intelligence, Buy Boxes |
| C3 | **Three header styles.** (a) Photo or navy banner with Playfair title: 15 screens. (b) Plain eyebrow + Playfair title on white, no banner: Foreclosures, Appointments, Team Board, Canvassing, Deal Calculator, Team. (c) Deal Room: blue gradient banner with an Inter title. | as listed |
| C4 | **Title sizes and fonts vary.** Playfair at 26 px (Providers & Controls), 34 px (6 screens), 37 px (most), 54 px (radar). Deal Room is Inter 38 px. Investor Outreach has no real page title (its only heading is a 13 px breadcrumb). | as listed |
| C5 | **The sidebar changes shape in the Buyers section.** Buyer Intelligence, Deal Room, Investor Outreach and Buy Boxes switch to the grouped, collapsible "deal room" rail. Every other screen uses the flat rail. | 4 screens |
| C6 | **Eyebrow labels don't share a pattern.** Only the radar says "OLP · …". Others say "Acquisition Intelligence", "Opportunity Discovery", "Wholesale · Acquisition", "System Intelligence", "Buyers", "Owner Deal Room". | all |
| C7 | **Naming.** The menu item is "Overlooked Properties" while the page's working title is "Opportunity Radar". The OLP mockup names the menu item "Opportunity Radar". | radar |
| C8 | **Platform name in body text.** Funding Partners says "BookaBoost does not lend…". That is correct as the platform, but worth a decision under OLP. | Funding Partners |

Already consistent: the charcoal rail with gold active item, the OLP rail logo, the identity chip, the top bar layout, gold primary buttons (outside C2), Playfair titles (outside C4), no sideways scroll, and no page errors on all 24 screens.

### Proposals: OLP only (behind the same switch, so the rollback look never changes)
- **P1 (C2): gold instead of blue** on Deal Room, Investor Outreach, Buyer Intelligence and Buy Boxes. Scoped to OLP. Small and low risk. *Recommended first.*
- **P2 (C3/C4/C6): one header pattern.** Navy/photo banner, "OLP · SECTION" eyebrow and a 36–38 px Playfair title on every screen. Add the six missing banners, convert the Deal Room title, and give Investor Outreach a real title.
- **P3 (C1): pick one canvas.**
  - *(A)* Dark OLP canvas on every Wholesale screen. This is the biggest job: each screen's cards, tables and forms need dark styles and a check.
  - *(B)* Light working canvas everywhere, with the radar keeping its dark hero and cards but sitting on the light canvas.
  
  B is quick and consistent; A matches the dashboard mockup everywhere. **Owner decision.**
- **P4 (C5): one rail shape** across Wholesale. Optionally regroup it to the mockup's "Opportunity Intelligence / Market Operations / Account & Settings". This changes navigation, so it's an **owner decision**.
- **P5 (C7/C8): naming.** Decide whether the menu says "Opportunity Radar" or "Overlooked Properties", and how body text names the platform under OLP. **Owner decision.**

Nothing in P1–P5 is built on this branch. This branch is for review only.

## 4. Local preview
- The OLP build and the rollback build are served from this branch against a throwaway local database: synthetic `@example.com` data, refused against any other database.
- Test login: `bb-admin@example.com` / `LocalTest!1`.
- No production data and no live accounts are involved.
