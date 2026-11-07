# My Deal Room — official logo installation (evidence)

Owner-supplied files (Drive › Wholesale): `MyDealRoom-logo.svg`, `MyDealRoom-logo.png`, `MyDealRoom-logo-assets.zip`.

Tracked assets (`frontend/public/brands/`):

| File | What it is |
|---|---|
| `my-deal-room-logo.svg` | The owner's SVG, byte-for-byte. |
| `my-deal-room-mark.svg` | The same file, cropped to its house tile (`viewBox="30 24 280 280"`, text removed). This is not new artwork; `tests/test_my_deal_room_logo.py` checks that the tile is identical to the original. |

Where it is installed (existing slots only):

| Slot | Before | After |
|---|---|---|
| Wholesale sidebar logo (EvoSys / My Deal Room), desktop and mobile drawer | EvoSys Pro mark, 180×72 | Official logo on a white plate filling the same 180×72 slot. The navy lettering is unreadable on the navy rail without it. |
| Investor portal brand mark (My Deal Room rooms; EvoSys-host sign-in) | Generic blue glyph, 30×30, "Deal Room" | Official house mark, 30×30, "My Deal Room" |
| Browser tab icon on My Deal Room screens and the My Deal Room investor portal | EvoSys Pro "E" | Official house mark. The "E" comes back when you leave Wholesale. |

Unchanged (pixel-identical before vs after): BookaBoost Wholesale sidebar (desktop and mobile), BookaBoost login, BookaBoost investor sign-in, the BookaBoost tab icon, EvoSys Pro login and its "E" tab icon, the general login, and the investor portal on mobile (it has no brand-mark slot).

## Pixel comparison (before → after, same build harness, same synthetic data)

| Screen | Changed pixels | Where |
|---|---|---|
| 01 login | 0 | — |
| 02 EvoSys Deal Operations | 14,799 | Logo slot (22,24)–(202,96), plus the live "As of" clock chip |
| 03 EvoSys Team / 04 Buyers / 12 home | 12,909 each | Logo slot only (22,24)–(202,96) |
| 05 EvoSys mobile (drawer closed) | 363 | "As of" clock chip only |
| 05b EvoSys mobile, drawer open | 12,909 | Logo slot only |
| 06 BookaBoost Deal Operations | 2,004 | "As of" clock chip only |
| 07 / 07b BookaBoost Team / mobile drawer | 0 | — |
| 08 Investor portal (My Deal Room room) | 874 | 30×30 mark only (14,26)–(44,56) |
| 09 Investor portal mobile | 0 | — |
| 10 Investor sign-in, evosyspro.live | 1,962 | Mark and name only |
| 10b Investor sign-in, bookaboost.com | 0 | — |
| 13 EvoSys Pro login / 14 BookaBoost login | 0 | — |
| 11 / 11b tab icons | — | My Deal Room rows now show the house mark; BookaBoost and EvoSys Pro rows are unchanged |

Side-by-side images are in `compare/`, and the full screenshots in `before/` and `after/`. Headless Chromium draws no tab strip, so `11-favicons.png` and `11b-favicons-investor.png` show each page's actual favicon file next to its title, at 16 px and enlarged.

Regenerate: build `frontend/`, then run `BRAND_SHOTS=/tmp/x BRAND_LABEL=after pytest tests/test_brand_shots_mdr.py`.
