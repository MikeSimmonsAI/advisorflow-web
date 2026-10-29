# Sign in with Google (issue #430)

**Status: built, tested, OFF.** The login page shows no Google button, and every
`/auth/google/*` endpoint answers `404` / `{"enabled": false}`, until the owner
finishes the setup below. Password sign-in is unchanged.

This is not the Google Calendar / Gmail / Contacts integration. Those grant the
app access to a person's Google data. This only proves who is signing in. It
requests `openid email profile` and grants nothing on Google.

## How it works
1. **Start.** On https://app.evosyspro.live/login, "Continue with Google" opens
   `GET /auth/google/start`. The backend:
   - writes a single-use transaction (10 minutes) holding the OIDC nonce and the PKCE verifier;
   - sets an HttpOnly cookie that binds the round trip to this browser;
   - sends the person to Google.
2. **Callback.** Google returns to `GET /auth/google/callback`. The backend:
   - spends the transaction (replays are refused);
   - checks the browser cookie (stops login CSRF);
   - exchanges the code server-side with the PKCE verifier;
   - verifies the ID token with Google's published keys: signature, issuer, audience = our client id, expiry;
   - then checks the nonce and `email_verified`.
3. **Who it is.** The Google account (`sub`) must already be linked to an EvoSys user
   (see Linking).
   - An email match never signs anyone in and never links anything.
   - Unknown, unlinked or inactive accounts get the same message, so nothing is revealed about which accounts exist.
4. **Session hand-off.** The browser receives a 2-minute, single-use ticket in the URL
   fragment. The app removes it from the address bar and POSTs it to
   `/auth/google/exchange` from the allowed origin. The response is the same
   session as the password login. The session token never appears in a URL.
5. **Same checks as the password login.**
   - The account must be active.
   - The account must belong to the platform (brand) it signs in on.
   - Post-login routing is the server's `/auth/my-contexts`, the same code path as the password form.
   - Google never changes a role.
   - Logout, session lists and "sign out everywhere" work as before.
6. **Platform owner (god_admin).** Password only, unless `GOOGLE_SIGNIN_ALLOW_GOD=1` is set.

## Linking (the only way a Google account gets attached)
1. Sign in with email and password.
2. Go to Settings → "Sign in with Google" → **Connect Google account**, and pick the account at Google.
3. The link is stored against the signed-in user only.

Other rules:
- One Google account per login, and one login per Google account.
- **Disconnect Google** removes the link; password sign-in keeps working.

## Owner setup (Google Cloud), required before it can be turned on
1. **Pick the OAuth client.** In Google Cloud Console → APIs & Services → Credentials, either:
   - use the existing **Web application** OAuth client (the calendar one), or
   - create a new one (recommended, so sign-in and calendar access can be managed separately).
2. **Authorized redirect URI.** Add this exactly:
   `https://advisorflow-backend.onrender.com/auth/google/callback`
   (or the backend's custom domain, if one is used; it must match `GOOGLE_SIGNIN_REDIRECT_URI` exactly).
3. **Authorized JavaScript origins.** None needed (the flow is server-side).
4. **OAuth consent screen.**
   - App name "EvoSys Pro", support email, app logo optional.
   - Authorized domain `evosyspro.live` (and `onrender.com` if the backend URL is used).
   - Scopes: `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile` only. These are non-sensitive, so no Google verification review is needed for them.
   - Publishing status **In production**, or users must be listed as test users.
5. **Render backend environment** (service `advisorflow-backend`). Set:
   - `GOOGLE_SIGNIN_CLIENT_ID` = the client id
   - `GOOGLE_SIGNIN_CLIENT_SECRET` = the client secret (Render secret; never in GitHub or chat)
   - `GOOGLE_SIGNIN_REDIRECT_URI` = the exact URI from step 2
   - `GOOGLE_SIGNIN_ALLOWED_ORIGINS` = `https://app.evosyspro.live` (the default)
   - `GOOGLE_SIGNIN_ENABLED` = `1`. Set this last.
6. **First check after deploy.**
   - Sign in with a password, connect Google in Settings, sign out, then use "Continue with Google".
   - Expected: you land where the password login lands.

To turn it off instantly, unset `GOOGLE_SIGNIN_ENABLED`. The button disappears; links are kept.

## Tables (created automatically at deploy; empty until used)
- `google_signin_transactions`: started round trips; spent once; swept after 24 h.
- `user_google_identities`: user ↔ Google `sub` links.
- `google_login_tickets`: 2-minute session hand-off tickets.

## Tests
- `tests/test_google_signin.py`: 20 backend tests using real RS256 ID tokens signed by a test key. Covers:
  - off by default; origin gating;
  - linked sign-in, session and logout; password login still works;
  - unlinked account refused even with a matching email;
  - unverified email; wrong audience, wrong issuer, expired token, forged signature, wrong nonce;
  - state replay and tampering; callback from another browser;
  - Google cancel and provider errors; open-redirect attempts;
  - brand isolation; inactive users;
  - explicit linking, unlinking, duplicate links;
  - god_admin policy; no role change.
- Browser E2E (harness, real backend, simulated Google account chooser): 13/13.
