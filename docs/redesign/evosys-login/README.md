# EvoSys Pro sign-in redesign (draft: awaiting owner review)

Real React render of `/login` on an EvoSys Pro host (`*.evosyspro.*`). Other brands (BookaBoost, Harmony Hustle,
AdvisorFlow) keep their existing sign-in, verified in the browser.

## Logo
The official EvoSys Pro mark is `public-site/assets/evosyspro-logo.png`, the same art the public website at
evosyspro.live uses for its navigation logo.
- `frontend/public/brands/evosyspro-logo.png`: byte-identical copy (md5 a14f407c…, checked by `tests/evosysLogin.test.mjs`).
- `frontend/public/brands/evosyspro-logo-on-dark-440.png`: the same mark scaled to 440 px. Its black ground is converted to
  transparency (colour / max-channel), so it sits on navy exactly as it sits on black. Composited back over black, it matches
  the original within 1/255. Nothing is redrawn or recoloured.

The AI-generated "E" tile in the mockup is not used anywhere.

## Imagery
Only photographs already licensed in the repo (Unsplash License; see `frontend/src/pages/login-evosys/img/CREDITS.md`).

## Screens
`desktop-1920.jpg`, `desktop-1440.jpg`, `tablet-820.jpg`, `mobile-390.jpg`, `mobile-375.jpg`, failed-credentials and
session-expired states, and `compare-reference-vs-implemented.jpg`.
