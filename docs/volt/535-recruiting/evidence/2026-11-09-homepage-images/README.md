# VOLT homepage images: visual check (Nov 9, 2026, 1:34 PM CT)

Checked on production at https://voltmarketing.live/ in a real browser, while running build 370ce6f.

## Phone (375 × 812)

| Image | File served | Loaded | Shown at |
|---|---|---|---|
| Hero | hero-1200.webp | yes | 335×251 |
| T-Mobile D2D Fiber card | fiber-1200.webp | yes | 335×129 |
| ACA Healthcare Outreach card | aca-1100.webp | yes | 335×129 |
| Find your opportunity | connect-900.webp | yes | 335×188 |
| Build toward freedom | train-900.webp | yes | 335×188 |
| Grow around driven people | grow-900.webp | yes | 335×188 |
| "Work hard. Grow together." banner | banner-900.webp | yes | 375×320 |

Screenshots: `phone-1-hero.jpg`, `phone-2-project-cards.jpg`, `phone-3-people.jpg`, `phone-4-banner.jpg`.

## Desktop (1440 × 900)

All 7 images loaded: hero-720, fiber-640, aca-640, connect-480, train-480, grow-480 and banner-1800. The browser picks the size it needs from each image's srcset.

Screenshots: `desktop-1-hero.jpg`, `desktop-2-project-cards.jpg`, `desktop-3-people.jpg`, `desktop-4-banner.jpg`.

## Notes

- Every image file returned HTTP 200 as `image/webp` (12–36 KB each).
- Below-the-fold images are lazy-loaded, so they load as the visitor scrolls to them. That is intended.
- The approved people-focused design is what shows on the page:
  - smiling teams in the hero and on both project cards
  - three people photos under "Why build your future with VOLT?"
  - the closing "Work hard. Grow together. Enjoy the journey." banner
- The "Be first to hear" (Get Future Opportunities) panel opened on its own during the check. That is the approved behaviour: it opens once per visit, about 10 seconds after someone lands on the homepage. It was closed without submitting.
