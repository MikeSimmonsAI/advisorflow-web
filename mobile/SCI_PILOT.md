# SCI Live — Expo Go mobile pilot

**Status:** Source-code preview for limited internal testers, not a store release.
**Business isolation:** Do not create a new app or copy the SCI database. The
backend returns `is_program: true` only for an authorized outreach-program
workspace in `GET /auth/my-contexts`. That flag maps to the new **Program**
experience. Other businesses keep their existing experiences.

## What the pilot shows

- **Overview:** needs attention, today's replies, booked visits, contacted
  today, urgent family replies, work items, and outreach-health snapshot.
- **Inbox:** new replies and all conversations, with a tap-through to the
  authorized family's recent text/email thread.
- **Visits:** upcoming appointments (manager permission required).
- **More:** workspace details, authorized experience switcher, signed-in devices,
  and sign out.

All SCI data is loaded from the **existing** `/program/command`,
`/program/command/pipeline`, `/program/appointments`, and
`/program/command/family/{lead_id}` routes. No customer records or metrics are
invented; loading/errors appear explicitly. No send/book/update methods exist
in this module. Existing backend authorization remains authoritative.

## Expo Go, Android or iPhone

On a development computer with Node.js:

1. Check out the pilot branch and run `cd mobile`.
2. Run `npm ci --include=dev`.
3. Start `npx expo start --tunnel --clear` (requires internet access).
4. **Physical iPhone requirement (Expo SDK 57, September 2026):** the Expo CLI process **and** Expo Go on the iPhone must be authenticated as the **same Expo account**. A tunnel can deliver HTTP 200 manifests and still be refused by the iOS client if either login is missing. For GitHub Actions, configure the repository's encrypted `EXPO_TOKEN` secret (generated in the Expo project's account settings); sign in to that exact Expo user inside the iPhone Expo Go app. Never put this token in a source file, PR comment, or chat. A GH runner with no token MUST NOT advertise its QR as a working iPhone preview.
5. Scan the authorized QR shown by Metro or open its authenticated Expo Go link.
6. Sign in using an **existing authorized SCI workspace account**. The preview
   uses the verified EvoSys backend URL; override `EXPO_PUBLIC_API_URL`
   during start if testing against staging instead.

**Expo Go is a testing container, not a standalone SCI app.** It cannot
receive production push notifications. Push initialization is intentionally
disabled in Expo Go; the same native module remains available to future
independent builds. Keep the Metro server running for testers.

## Before distributing

- Execute `npm run typecheck` and `npm run test:ci -- --runInBand`.
- Run a device smoke test with a SCI program-only account: login, overview,
  inbox, family conversation, visit listing, refresh, sign-out.
- Run a device smoke test with a non-SCI account: SCI program navigation
  must not appear.
- Confirm scoped membership enforcement, no data leaks on switch,
  and appropriate visit-list 403 display for non-manager SCI staff.
- Create the Expo Go QR in a live Metro/tunnel process and verify one
  Android and one iOS phone if both are in the pilot.

**Not in this pilot:** outbound replies, new bookings, home-guide delivery,
offline sync, notifications, or distribution through app stores.
