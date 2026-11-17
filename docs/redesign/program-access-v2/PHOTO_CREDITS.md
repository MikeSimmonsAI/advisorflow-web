# Program Access V2 - photo credits

Every photo on the Program Access cards, featured banner and preview thumbnails is bundled with the app
(`frontend/src/pages/program/v2/img/`). Nothing is hotlinked. Each file is `<key>-card.webp` (720x450)
and, where the source is wide enough, `<key>-wide.webp` (1600x440, the featured banner). Cropped and
re-encoded locally with Pillow (WebP).

## New photos (Unsplash License)

Downloaded 2026-11-16 from `images.unsplash.com` at 1800 px wide, each from an Unsplash search filtered to
the free license (`?license=free`; no Unsplash+ images). The Unsplash License (https://unsplash.com/license)
allows commercial use without attribution; photographers are credited here anyway. The people shown are not
customers and are never named or tied to a result.

| Key | What it shows | Unsplash page | Photographer | Original sha256 |
|---|---|---|---|---|
| `restland` | Fountain in a memorial garden | https://unsplash.com/photos/a-fountain-in-the-middle-of-a-grassy-field-Wu4X1r9oGko | @marjoriepear | `747dded959cd89e90500d22e869a59e0a67e158f2bd15557f163294465c83010` |
| `sci` | Memorial colonnade and reflecting pool | https://unsplash.com/photos/a-large-building-with-a-pond-in-front-of-it-2hFe3yxWfX4 | @datingjungle | `56105fcbab9d469ca2d3eebc554068273033713fabb079ad9cd05740f4a59990` |
| `energy` | Transmission towers at sunset | https://unsplash.com/photos/photo-of-truss-towers-yETqkLnhsUI | @matthewhenry | `b62928b31e332001c280e3eadf04dc903090605f294b27ab923465f3d5c39b8d` |
| `roofing` | Shingle roof with dormer | https://unsplash.com/photos/a-house-roof-with-a-dormer-window-and-chimney-nU-AAbQ_TdI | @sigmund | `4dc7d9f0e6a9ad037c2f7b6ee61d9eb1105940c3f1c7d9d291007b432ec556af` |
| `cleaning` | Office floor being vacuumed | https://unsplash.com/photos/a-man-in-yellow-gloves-vacuums-a-carpeted-office-floor-CVcIMnH_7so | @webseoweb | `669b820a44617af805c1728c76032667fb0d00a64e7c8291e3b8f242d2358cf6` |
| `gold` | Gold chain bracelet | https://unsplash.com/photos/a-gold-chain-bracelet-sitting-on-top-of-a-magazine-oO0JAOJhquk | @natinati | `f36dbcd6cd5c06e205d7af05d133d92ff29aea6e74a96f4ce1612c8e77141360` |
| `insurance` | Handshake over a house model and keys | https://unsplash.com/photos/two-men-shaking-hands-over-a-house-model-and-keys-69K2AGQc_Yw | @bdstuan123 | `d71d0ea0e9ea0d7b3701a3c38144f4a4350c6f562fa390456ff7bbd1c8cead2b` |
| `agency` | Two advisers reviewing charts | https://unsplash.com/photos/two-businessmen-discussing-charts-on-a-laptop-YR4zsA7QwRk | @silverkblack | `28e550bb292cc8f8dd55aca330b79da7e9bf0a64839c98b903e09b8aefaa0e50` |
| `acquisition` | Analytics dashboard on a laptop | https://unsplash.com/photos/graphs-of-performance-analytics-on-a-laptop-screen-JKUTrJ4vK00 | @lukechesser | `5b31c5e6a50b9843cbb428c3ca944ce57bb12ee973e16d15130411bf6f27fc2e` |
| `backoffice` | Paperwork beside a laptop | https://unsplash.com/photos/person-holding-pencil-near-laptop-computer-5fNmWej4tAA | @amstram | `2be11813f2b9c48f3a27e113afb644f0de52c88d2e670714e64f39fc305cad6d` |
| `exec-builder` | Boardroom presentation | https://unsplash.com/photos/man-standing-in-front-of-people-sitting-beside-table-with-laptop-computers-gMsnXqILjp4 | @campaign_creators | `1699b15e401a837ea2cdceb9fa3f3a56f2ce2299b3336f304a6bc41e448446c5` |
| `team` | Team working together | https://unsplash.com/photos/diverse-team-collaborating-on-a-project-in-an-office-PSksbOVDhWk | @silverkblack | `92e20412726a672c3424ade78e695b4470f18f5878a2e421390a7aad2bc6f6ff` |
| `onboarding` | People at a table with pens | https://unsplash.com/photos/people-sitting-on-chair-in-front-of-table-while-holding-pens-during-daytime-KdeqA3aTnBY | @mainermedia | `0921c910c0dd32ec4f6d9819d691033b54e39338f267f0f98ecb33ccaac0f527` |
| `evosys-sales` | Presenter at a screen | https://unsplash.com/photos/man-presenting-data-on-a-large-screen-to-colleagues-6CfjB_bQ_FY | @silverkblack | `0cd5f2359d248eed02aabb934625f48f77dca8cff28dc0230576679c16db1a19` |
| `executive` | Office towers | https://unsplash.com/photos/low-angle-photo-of-city-high-rise-buildings-during-daytime-PhYq704ffdA | @seanpollock | `e60db99a192425a3675159b464d5d9a60456f94f842f67d224ae3e7cfc354eb5` |
| `wraps` | Film being applied to a car | https://unsplash.com/photos/a-man-is-working-on-an-orange-car-8MXNZCgAah0 | @andredantan19 | `d48609900aa07ecaf51b53ee31408cb70a176f8e87531dd59016fd2873260c07` |

## Photos already licensed in this repository

| Key | Source |
|---|---|
| `wholesale` | `frontend/src/pages/wholesale/ds/photos/estate.jpg` (Unsplash photo-1568605114967-8130f3a36994, see that folder's CREDITS.md) |
| `sales-builder` | `frontend/src/pages/wholesale/ds/photos/capital.jpg` (Unsplash photo-1521791136064-7986c2920216) |
| `land` | `frontend/src/pages/wholesale/ds/photos/land-field.jpg` (Unsplash photo-1500382017468-9049fed747ef) |
| `realestate` | `frontend/src/pages/wholesale/ds/photos/house02.jpg` (Unsplash photo-1568605114967-8130f3a36994) |
| `business` | `frontend/src/pages/wholesale/ds/photos/skyline.jpg` (Unsplash photo-1514565131-fce0801e5785) |
| `volt` | `frontend/public/brands/volt/people/banner-1800.webp` - VOLT Marketing's own homepage imagery from the owner-approved VOLT mockup (illustrative; see commit 81223e9c and docs/volt/535-recruiting) |
| `consumer` | `frontend/public/brands/coveragepath/family-laptop-1100.webp` - Pexels License, Kampus Production (see docs/volt/design/consumer_site_photos.md) |

## Rules

- A card's photo is chosen from the program and the industry the server recorded (`commandCenterModel.js`,
  `photoKeyFor`), never from a client's name; a new client gets a suitable photo by itself.
- No official third-party logo is drawn or invented. Cards show the existing program symbol.
- To replace a photo: drop a new `<key>-card.webp` (and `-wide.webp`) in the folder and update this table.
