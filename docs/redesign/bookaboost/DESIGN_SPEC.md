# BookaBoost Program Access — design spec

Owner decision (#272): BookaBoost is **black, charcoal and gold** with **photographic** Program Access cards.
Not EvoSys blue, not the cream/walnut app theme, not the cyan/mint splash, not the navy marketing palette,
and not white square cards with floating icon circles.

The screenshots in this folder are the mockups for approval. They are the real built UI, running locally
on synthetic data (see `e2e/` and `RESULTS-2026-10-28.txt`).

## When it shows

`/auth/my-contexts` returns `brand`. The BookaBoost launcher is used only when `brand.theme == "bookaboost"`.
The server decides it, never per person:

- on a brand address (the request's Origin names a brand), that brand, if this login belongs to it;
- elsewhere, the login's explicit brand links (organizations with a platform set, brand sales teams,
  platform memberships), only when they name exactly one brand;
- God, super admin and mixed-brand logins get none, so they see the EvoSys launcher.

The cards are the same either way. They come from `programCards()`, which uses the server's lists.

## Tokens (scoped under `.bbpa`)

| Token | Hex | Use |
|---|---|---|
| `--bb-black` | `#0b0b0c` | page background |
| `--bb-charcoal` | `#151517` | panels, messages, skeletons |
| `--bb-charcoal-2` | `#1d1d20` | skeleton shimmer |
| `--bb-line` | `#2a2a2e` | dividers |
| `--bb-gold` | `#c9a45c` | eyebrows, card kind, hairlines |
| `--bb-gold-bright` | `#e3c58a` | "Open" action, Sign out text, focus ring |
| `--bb-gold-hair` | `rgba(201,164,92,.45)` | card border, header divider |
| `--bb-text` | `#f4efe6` | main text |
| `--bb-muted` | `#b3ad9f` | secondary text |
| Card title | `#ffffff` | on the photo |

Contrast on `#0b0b0c` (computed): text 17.2:1, muted 8.8:1, gold 8.4:1, bright gold 11.8:1 (all ≥ 7.8:1 on charcoal too).

The logo keeps its own colours: mark `#1d4ed8` with `#60a5fa` arrow, wordmark white "BOOKA" + `#60a5fa` "BOOST".
They are copied exactly from the marketing page (`index.html` nav). The logo is not recoloured.

## Type

- Display (headline, card titles, wordmark): **Space Grotesk** 700. Headline 48/40/34 px, card title 30/26 px.
- Body: **Inter** 400–600. Lead 17 px, card line 15 px, eyebrows 11.5–12 px uppercase, tracking .16–.18em.
- No new font source. Both fonts already load from the app's existing `frontend/index.html` link, with a system fallback.

## Logo files (`frontend/public/brands/`)

| File | Use |
|---|---|
| `bookaboost-mark.svg` | Mark only (36×36). Program Access header, Back Office brand mark |
| `bookaboost-logo.svg` | Mark + wordmark, white "BOOKA" for dark backgrounds. Wholesale rail |
| `bookaboost-logo-light.svg` | Same, near-black "BOOKA" for light backgrounds |

These replace the two broken 6-byte files. Renders on dark and light backgrounds: `logo/`.

## Card (editorial, horizontal, photographic)

- Full-bleed photo (`background-size: cover`), dark scrim: left-to-right `rgba(8,8,9,.94 → .12)` plus a bottom fade.
- 1px gold hairline border (`--bb-gold-hair`), 14px radius, a 64×2 px gold bar at the top-left.
- Content bottom-left: KIND (gold, uppercase), Title (Space Grotesk), optional org line, one-line description, gold **Open →**.
- Whole card is one control: `<button>` for workspaces, `<a href>` for a preview, with a full accessible name ("Open: Wholesale").
- Hover: border turns full gold, card lifts 2px, photo zooms 5%, arrow nudges. With reduced motion, these effects are off.
- Focus: 2px `#e3c58a` outline, 3px offset.
- Height 260px (230px on phones). Two columns on desktop, one column at 900px and below.
- Photos (existing repo photos, `pages/wholesale/ds/photos/`): Wholesale `estate.jpg`, Back Office `network.jpg`,
  Executive `skyline.jpg`, Authorized preview (Countryside) `home.jpg` (house on open land).
- Authorized preview cards use a dashed border and their own section, "Authorized preview".

## States

- **Loading**: BookaBoost header + two shimmering charcoal cards. Shown only on a BookaBoost address, before the answer arrives. Elsewhere it stays blank, as before.
- **Error**: "We couldn't load your businesses." + **Try again** (re-asks the server). Same rule as Loading.
- **Empty**: "No businesses are assigned to this login yet. Ask your administrator to give you access."
- Browser tab: **BookaBoost · Program Access**.
- Sign out: header button. It calls the existing `logout()`, then goes to `/login`.

## Breakpoints

- ≥ 901px: 2-column cards, 32px gutters, max width 1240px.
- 561–900px (tablet, checked at 820): 1 column, 24px gutters, headline 40px.
- ≤ 560px (phone, checked at 390): 1 column, 16px gutters, header shows logo + Sign out only, headline 34px, cards 230px.
- No horizontal overflow at 1600 / 820 / 390 (checked).

## Other BookaBoost screens (brand tokens only)

- **Wholesale**: same navigation and screens as EvoSys Wholesale (checked: identical nav list). The rail wears the BookaBoost
  logo (`bookaboost-logo.svg`), a charcoal-to-black background and a gold active item. Scoped to
  `html[data-theme="bookaboost"] .layout--wholesale .sidebar`.
- **Back Office**: the approved design is unchanged. It shows the BookaBoost mark next to the brand name.
- **EvoSys Program Access**: unchanged. The before and after screenshots are pixel-identical (`E-evosys-program-access-*.png`).

## Screenshots

| File | What |
|---|---|
| `A-program-access-desktop-1600.png`, `-tablet-820.png`, `-mobile-390.png` | BookaBoost Program Access |
| `A-program-access-keyboard-focus.png` | Keyboard focus on a card |
| `B-back-office-bookaboost-empty-desktop-1600.png`, `-mobile-390.png` | Back Office, BookaBoost, empty |
| `C-wholesale-bookaboost-desktop-1600.png` | Wholesale landing, BookaBoost |
| `D-countryside-authorized-preview-tile.png` | The Countryside tile in BookaBoost Program Access |
| `D-countryside-preview-open.png` | The preview opened: the stored demo, untouched, in the sandboxed frame |
| `S-program-access-empty/loading/error-1600.png` | States |
| `E-evosys-program-access-before-1440.png` / `-after-1440.png` | EvoSys launcher before / after (identical) |
| `E-evosys-wholesale-after-1600.png` | EvoSys Wholesale, for the navigation comparison |
| `logo/*.png` | Logo files rendered on dark and light |
