# Wholesale Field Team - handoff, Oct 11-12 2026 (overnight)

Follows `WHOLESALE_FIELD_TEAM_2026-10-11.md`. Its "Waiting on Mike" list is
superseded by the one below. Checklist: Google Sheet "Field Acquisition Team -
Build Plan" (Build List tab; the # in column A is the task number, row = #).

## Shipped tonight (all live and checked on the real site)

| PR | What |
|----|------|
| #66 | Phone alerts (web push). Server makes and keeps its own VAPID keys (table `web_push_vapid_keys`, private key encrypted); env keys still win; `WEB_PUSH_AUTO_KEYS=0` turns auto keys off (tests default it off in conftest). Wholesale bell alerts also push with a generic title ("Appointment update", "Offer update", "New seller lead") and a /wholesale link - never the address. Loop `web_push_loop` (20 s, backend role, ledger interval 60). Appointments page: "Alerts on this phone" card. |
| #67 | Works with weak or no signal (#28). `frontend/src/pages/wholesale/appts/outbox.js`: tries now; no answer in 20 s (photos 90 s) or no signal -> kept in IndexedDB and sent in order on `online`, app reopen, every 30 s. Drafts of visit notes kept in localStorage as typed. Last screens opened kept for offline reading (`evosys-kept:` keys, cleared by `clearToken`). `OutboxBar.jsx` = the signal bar. Server: same photo twice -> saved once (checksum); visit results carry `client_ref` -> `wholesale_field_appointments.outcome_ref` so a repeat never makes a second call-back. |
| #68 | Raise rule: a rep's first standing offer on a visit is their opening offer; on their own they can raise it up to `wholesale_field_rules.raise_limit_pct` (10). More goes to Mike. The overall limit (% of max offer) still applies. |
| #69 | Commission on the Team Board: `wholesale_field_rules.commission_pct` (5) of the assignment fee. Earned = fee collected on closed deals; coming = assignment fee on signed, not closed. One deal counts once (latest visit's rep). |
| #70, #71 | Android app (#29): `android/` is a Trusted Web Activity (`live.evosyspro.field`) opening /wholesale/appointments. `.github/workflows/android-field.yml` builds a signed APK + AAB on GitHub (repo is public, Actions free) and publishes the APK as the latest release. Rep install page: `app.evosyspro.live/field-app.html`. `/.well-known/assetlinks.json` holds the signing key's public fingerprint. Privacy policy: `app.evosyspro.live/privacy.html` (incl. account deletion). |

## Live settings Mike chose (EVO Integrated Solutions LLC workspace)

- Transaction costs 0% (Wholesale Settings). Max offer = ARV x 70% - repairs - $10,000 fee.
- Default fee stays $10,000 "until we get some wins".
- Rep offer limit 90% of the max offer; raise limit 10% above the opening offer.
- Commission 5% of the assignment fee, everyone.
- Name owners see = EVO Integrated Properties (was already set).
- E-signing: EvoSys's own built-in e-signing - nothing to connect.
- Attorney review: dropped by Mike. The training guide still has a "DRAFT pending attorney review" line Mike can delete.

## Signing key (do not lose)

`C:\Dev\keys\evosys-field-upload.p12` + password file + fingerprint file on Mike's PC; also in repo Actions secrets
`ANDROID_KEYSTORE_B64` / `ANDROID_KEYSTORE_PASSWORD`. A new key would force every rep to uninstall/reinstall.
When the app goes on Google Play, add Play's app-signing fingerprint to `assetlinks.json`.

## PAUSED by Mike (Oct 12, 1 AM): app store work

"Pause on this app until we get the Apple dev and the Google dev accounts straightened out."
- Apple Developer: enrolled as the company, waiting on Apple's review.
- Google Play: signed up as the company (info.evosyspro@gmail.com), waiting on Google's review.
- When approved: #33 iPhone TestFlight (Apple is strict about website-wrapper apps - build iPhone with real phone features), Play internal testing with `evosys-field.aab`, #34 screenshots. Store text and form answers: Google Doc "EvoSys Field - App Store and Google Play listing text".
- Mike installed the home-screen app on his iPhone. Android APK not yet tried on a real phone.

## Waiting on Mike

- Test field login (Wholesale > Team > Add someone > New login > Field specialist) -> unlocks #37 launch check (Claude) and store screenshots.
- #22 test run, #23 first rep live (target Wed Oct 14, no later than Fri Oct 16).
- Store approvals (above).

## Gotchas learned tonight

- Sheet rows: row number = task # (header in row 1; #2 was deleted, so #1 is row 2 and #3 is row 3). An off-by-one write was made and fixed - read the row before writing.
- `gh pr create --body-file` with an old file name reuses old text - always write a fresh body file per PR.
- Local preview: `C:\Dev\overnight\preview_start.bat` starts the API (8765) and Vite with a throwaway `frontend/vite.preview.local.mjs` (delete after; never commit). Set `af_token` from `preview_session.json` and navigate in the same script (the login page clears it). Cut the signal in the page with a `window.fetch` override for /__api.
