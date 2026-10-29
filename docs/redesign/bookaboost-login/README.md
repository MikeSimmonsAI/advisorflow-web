# BookaBoost private sign-in redesign (draft: awaiting owner review)

Real React render of `/login` for the BookaBoost theme (`app.bookaboost.live`). This is the private
application sign-in, not the public bookaboost.live website. EvoSys Pro, Harmony Hustle and AdvisorFlow
keep their own sign-in pages; in the browser they render pixel-identical to `main`.

## How it is built
- `frontend/src/pages/login-bookaboost/BookaBoostLogin.jsx`: presentation only. `Login.jsx` still owns
  the credential call, tokens, the expired-session flag, the safe next-path and the server-decided landing.
  It passes them in exactly as it does for the EvoSys Pro page.
- `frontend/src/pages/login-bookaboost/bookaboost-login.css`: every selector is scoped under `.bbl`.
- `Login.jsx`: one `if (theme === THEMES.BOOKABOOST)` branch. The old BookaBoost panel copy is removed
  (pre-need funeral wording, "4.8x", "72%").

## Logo and imagery
- Logo: the official `frontend/public/brands/bookaboost-logo.svg` (mark and wordmark copied from the live
  bookaboost.live header). The AI-drawn "BR" mark in the mockup is not used.
- Background: `img/bg-city.webp`, made from `invest.jpg`, which is already licensed in the repo (Unsplash
  License). It is colour-graded locally. See `img/CREDITS.md`.

## Content rules
- Only what the product does today: Property Intelligence, Market Research, Buyer Matching, Deal Room.
  No numbers, coverage ("nationwide"), ROI or results.
- No Google or other third-party sign-in. Google is connected only for calendars, not for signing in.
- God Mode, the Executive Suite and the back office are never named. Where a person lands is decided by
  the server after sign-in.

## Phone
The logo and the sign-in form come first. At 390 px and 320 px, the email and password fields sit on
the first screen. After the form comes a short introduction and the four capabilities.

## Screens
`desktop-1920.jpg`, `desktop-1440.jpg`, `tablet-820.jpg`, `mobile-390.jpg`, `mobile-375.jpg`,
`mobile-320.jpg`, `mobile-390-full.jpg`, plus these states:
- required fields;
- wrong password;
- loading;
- session expired.

`compare-mockup-vs-implemented.jpg` puts the mockup next to the built page.
