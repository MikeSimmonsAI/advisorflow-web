# EvoSys Pro — native mobile app

Expo + React Native (TypeScript), living in `mobile/` inside the
`feat/evosys-mobile` worktree. One application whose experience changes with the
signed-in user's **server-authorized contexts** — not five applications, and not
the web app in a phone-shaped window.

---

## The standard this is built against

> A salesperson should be able to leave the laptop at home and run their normal
> sales day from the phone.

Everything below is measured against that sentence.

---

## Running it

```bash
cd mobile
npm install --legacy-peer-deps --include=dev
EXPO_PUBLIC_API_URL=http://<your-lan-ip>:8000 npm start
```

`EXPO_PUBLIC_API_URL` has **no production default**, deliberately. A debug build
that silently reaches live customer data is worse than one that will not start.
`localhost` is the fallback, and `localhost` on a phone is the phone.

```bash
npm run typecheck    # tsc --noEmit
npm test             # jest
```

### Two install flags this machine needs

* `--legacy-peer-deps` — the Expo 57 template resolves `react-dom@19.2.8`
  against `react@19.2.3`, which npm refuses. A pre-existing conflict in the
  template, not something this app introduced.
* `--include=dev` — this machine's npm config sets `omit=dev`, so a plain
  `npm install` silently skips TypeScript, Jest and the testing library and then
  reports "up to date".

---

## Authority

```
POST /auth/login          → token (backed by a per-device session ROW)
GET  /auth/my-contexts    → every context this caller may enter
                            ↓
                   experience list (server-decided)
                            ↓
              tab set · screen set · action set
```

Rules carried over verbatim from the web client, enforced in
`src/experience/resolve.ts` and asserted in `src/__tests__/experience.test.ts`:

* No `if (email === …)`. No hard-coded person.
* No `if (role === 'executive') → all organisations`. Executive scope is
  **portfolio assignment**, resolved server-side. Same brand does not imply
  access; zero assignments means zero organisations, and that empty state is
  rendered as an answer rather than an error.
* An experience absent from `contexts` does not exist to this app — not hidden,
  absent.
* Navigation is presentation. **Every request is authorised again, independently,
  on the server.**

The switcher selects among scopes already granted. It cannot widen one.

---

## Multi-device sessions

The backend change that made a phone possible at all lives in the same branch:

| | before | now |
|---|---|---|
| a session | `users.session_token`, one column | a row in `user_sessions`, one per device |
| second login | signed the other device out | both stay live |
| web refresh (every 30 min) | rotated the single token | rotates that device's row only |
| logout | ended every session | ends **this** device (`/auth/logout-all` ends all) |
| force-logout / password change / reset | cleared the column | revokes **every row** *and* clears the column |

`deps.get_current_user` trusts the row whenever one exists for the presented
`jti`, and only falls back to the legacy column when none does — which is a
token minted before the table shipped. A revoked row is a refusal with no
fallback; that ordering is what keeps revocation real.

New endpoints: `POST /auth/logout-all`, `GET /auth/sessions`,
`DELETE /auth/sessions/{id}` (all scoped to the caller; `jti` is never returned).

---

## Layout

```
mobile/
  app/                       expo-router routes
    _layout.tsx              providers + the auth gate
    index.tsx                redirect to the server's default_context
    (sales) (manager) (advisor) (exec) (owner)/    one tab group each
    lead/ opportunity/ appointment/ proposal/ org/ customer/   detail screens
    switch.tsx  sessions.tsx
  src/
    api/        typed client + every endpoint, named after the router that owns it
    auth/       session, Keychain storage, biometrics
    experience/ resolver + switcher, fed by /auth/my-contexts
    vocab/      stages, appointment status, severity — MIRRORED, never re-defined
    comms/      the one place a prospect is contacted
    offline/    the draft queue
    components/ ui primitives, tabs, sign-in, lock, more
```

---

## What is deliberately not here

* **Destructive owner actions.** Suspend, request cancellation, offboard,
  archive, permanent delete, role change, impersonate. The routes exist and are
  authorised; this app never calls them. A phone is the wrong place to end a
  customer.
* **A revenue trend line.** There is no historical series in the platform
  (Phase 0 GAP-5), so a chart would be a line through one point. The Executive
  Revenue screen says so.
* **Photo capture, until storage is durable.** `/tmp/bookaboost_media` does not
  survive a Render restart. `GET /me/upload-capability` reports the truth and
  the app hides capture when the answer is `ephemeral`; an upload into ephemeral
  storage is refused (503) rather than accepted and lost.
* **A month calendar view.** Tap targets smaller than a fingertip, telling a rep
  nothing they can act on.
* **An offline sync engine.** The outbox replays unsent requests. It allocates
  no ids, resolves no conflicts and decides nothing about what a record means.

---

## The honest gap: prospect messaging

There is no brand-sales prospect messaging on the server. No `sales*` router
imports `sms_service` or `email_service`; the tenant messaging routers are
`require_tenant_user`, and a brand salesperson has `organization_id = NULL` by
positive architectural assertion.

So V1 hands off to the device — `tel:`, `sms:`, `mailto:` — from **one module**,
`src/comms/communications.ts`. No screen imports `Linking` to dial a number.
Every handoff with an opportunity behind it writes a real note through
`POST /sales/opportunities/{id}/notes`, so the activity trail records that the
contact happened. It does not record what was said, there is no thread, and
`isFullyLogged` is `false` — which the UI states in plain words, because a rep
who believes a text was logged will stop writing notes.

When the communications layer gains a brand-sales path, `contactProspect` starts
calling it and no screen changes.

**The advisor experience is different and fully backed**: `POST /sms/send` from
`app/lead/[id].tsx` is a real send, logged on the lead's timeline.

---

## Deployment dependencies (configuration, not code)

| | env | effect until set |
|---|---|---|
| Push | `EXPO_PUSH_ENABLED=1` | tokens register; nothing is sent |
| Uploads | `MEDIA_STORAGE_BACKEND=s3`, `MEDIA_S3_BUCKET`, AWS keys | capability reports `ephemeral`; uploads refused |
| Deep links | `apple-app-site-association` + `assetlinks.json` on the brand hosts | links open on the web |
| Splash screen | an asset + the `expo-splash-screen` plugin | plain ground colour |

No credentials are invented anywhere in this branch.
