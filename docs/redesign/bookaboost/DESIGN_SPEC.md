# BookaBoost Program Access — design spec

**Source of truth:** the owner-approved mockup `BookaBoost_Approved_4-Panel_Mockup_2026-10-28.png`
(panel 1 is Program Access, panel 2 is the Wholesale look). It is kept in the owner's private
BookaBoost folder, not in this public repo. Owner decision (#272): premium **black, charcoal and gold**
with **photographic** cards. Not EvoSys blue, not the cream/walnut theme, not the cyan/mint splash.

The screenshots in this folder are the real built UI running locally on synthetic data
(`e2e/`, results in `RESULTS-2026-10-28.txt`: 55 checks, 0 failed).

## When it shows

`/auth/my-contexts` returns `brand`. The BookaBoost launcher is used only when `brand.theme == "bookaboost"`.
The server decides, never the browser. God, super admin and mixed-brand logins get the EvoSys launcher.
Cards come only from `programCards()` (the server's lists); the look adds, hides and authorizes nothing.

## Layout (matches mockup panel 1)

- **Hero**: full-width photo (`estate.jpg`, a lit house at dusk), dark scrim fading left-to-right and into black at the bottom.
  Logo top-left; "Welcome back, *name*" + round initials avatar + chevron top-right (the avatar opens a menu with **Sign out**).
- **Headline**: "Choose Your **Business**" (serif, *Business* in gold), then "Access your BookaBoost workspaces".
- **Cards**: one row that rises into the bottom of the hero. Operational destinations first (Wholesale, Back Office),
  then authorized previews (Countryside). Each card: full photo, gold hairline border, 12px radius,
  round gold badge with icon, serif title, one line, gold ring arrow + "Open *name*".
- **Authorized preview**: green-tinted scrim, green badge (leaf), green **PREVIEW** tag top-right — as in the mockup.

## Tokens (scoped under `.bbpa`)

| Token | Value | Use |
|---|---|---|
| `--bb-black` | `#0a0a0b` | page |
| `--bb-charcoal` / `-2` | `#141416` / `#1c1c1f` | menus, messages, skeletons |
| `--bb-gold` | `#c9973d` | brand gold (same as the live bookaboost.live site) |
| `--bb-gold-light` | `#e2b866` | "Business", focus ring, menu text |
| `--bb-gold-deep` | `#a07828` | badge shading, Wholesale active item |
| `--bb-gold-hair` | `rgba(201,151,61,.55)` | card and avatar borders |
| `--bb-green` | `#2f7a3a` | preview tag and badge |
| text / muted | `#f6f1e7` / `#c9c1b1` | |

## Type

- Display: **Playfair Display** 500 (self-hosted via `@fontsource/playfair-display`, no CDN), Georgia fallback.
  Headline clamp(40px, 5.4vw, 66px); card titles 30px (26px on phones).
- Body: **Inter**.

## Logo (`frontend/public/brands/`)

Copied exactly from the header of the live BookaBoost website (bookaboost.live, `.nav-logo-mark` + wordmark):
gold `#c9973d` square mark with white bars and a dark check badge; wordmark "Booka" + gold "Boost", weight 800.

| File | Use |
|---|---|
| `bookaboost-mark.svg` | mark only (38×38): Program Access, Back Office brand mark |
| `bookaboost-logo.svg` | mark + white "Booka": dark backgrounds (Wholesale rail) |
| `bookaboost-logo-light.svg` | mark + near-black "Booka": light backgrounds |

The mockup shows a stylised gold "B" monogram with "REAL ESTATE SOLUTIONS". That artwork does not exist as a
file anywhere in the repo, Drive folder or live site, so it was **not** invented; the live-site logo is used
until the owner provides the monogram file.

## Photos

Existing repo photos (`pages/wholesale/ds/photos/`): hero `estate.jpg`, Wholesale `aerial.jpg`,
Back Office `contract.jpg`, Executive `skyline.jpg`, Authorized preview `home.jpg` (house on open land at sunset).
The mockup's photos are renders; swap these files when the owner supplies originals.

## States

- Loading: hero + three shimmering cards (BookaBoost address only; elsewhere blank, as before).
- Error: "We couldn't load your workspaces." + **Try again**.
- Empty: "No workspaces are assigned to this login yet. Ask your administrator to give you access."
- Browser tab: **BookaBoost · Choose Your Business**.

## Breakpoints

- Desktop: 3 across (4 when four cards), cards 420px tall.
- ≤1024px: 2 across, 360px tall.
- ≤640px: 1 column, 280px tall, welcome text hidden (avatar stays), hero shorter.
- No horizontal scrolling at 1600, 820 and 390 (checked).

## Wholesale (mockup panel 2) — colour only

Charcoal rail with a solid gold active item; same navigation and features as EvoSys Wholesale
(checked: identical nav list). On tablets and phones the BookaBoost top bar gives the search box its own
row so nothing is squeezed or pushed sideways. EvoSys Wholesale is unchanged (it still squeezes on phones on
main — a separate, owner-approved change can give it the same rule).

## Back Office

Owner decision: keep its approved layout. Only the brand changes (BookaBoost name and mark).
