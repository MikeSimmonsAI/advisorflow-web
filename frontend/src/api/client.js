// One canonical API base for the whole frontend — authenticated pages and the
// public customer portal alike. Anything that talks to the backend imports this
// rather than reading an env var of its own; a page with its own copy is a page
// that keeps calling the old host after this one moves.
export const API_BASE = import.meta.env.VITE_API_BASE_URL || 'https://advisorflow-backend.onrender.com'

// â”€â”€ Brand-neutral localStorage keys â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// All keys use the "af_" prefix â€” no white-label brand name ever appears in
// storage. Migration helpers read the old "bookaboost_*" / "bb_*" keys once,
// copy the value to the new key, and delete the old one so existing sessions
// survive the rename without being logged out.

import { orgOverrideFor } from '../auth/routeAuthority'
import { applyWorkspaceTheme } from '../theme.js'
const KEY_TOKEN    = 'af_token'
const KEY_USER     = 'af_user'
const KEY_BRANDING = 'af_branding'

function _migrate(newKey, ...oldKeys) {
  if (localStorage.getItem(newKey) !== null) return
  for (var i = 0; i < oldKeys.length; i++) {
    var val = localStorage.getItem(oldKeys[i])
    if (val !== null) {
      localStorage.setItem(newKey, val)
      oldKeys.forEach(function(k) { localStorage.removeItem(k) })
      return
    }
  }
}

function getToken() {
  _migrate(KEY_TOKEN, 'bookaboost_token')
  return localStorage.getItem(KEY_TOKEN)
}

export function setToken(token) {
  localStorage.setItem(KEY_TOKEN, token)
}

export function clearToken() {
  localStorage.removeItem(KEY_TOKEN)
  localStorage.removeItem('bookaboost_token') // clean up legacy key if present
}

/**
 * Core fetch wrapper with retry logic for Render cold starts.
 *
 * Render free-tier services sleep after 15 minutes of inactivity. When the
 * backend wakes from sleep, the first 1-2 requests can fail at the network
 * layer (no response at all) before the server is ready. Without retries,
 * this surfaces as a hard "Failed to fetch" error on every page.
 *
 * Retry policy:
 *  - Only retries TypeError (network-level failure â€” no response from server)
 *  - Does NOT retry HTTP errors (401, 403, 404, 500, etc.) â€” those are real
 *  - Up to MAX_RETRIES attempts with RETRY_DELAY_MS between each
 *  - Auth errors (401) redirect to login immediately, no retry
 */
const MAX_RETRIES = 2
const RETRY_DELAY_MS = 3000

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

// THE OPTIONS THAT ARE OURS, NOT `fetch`'s. Everything else in `options` is
// spread into fetch() verbatim, so anything we invent has to be named here or
// it silently becomes a no-op property on the request init object.
const CLIENT_ONLY_OPTIONS = ['params', 'noOrgContext', 'skipRedirect', 'asCustomer']

/**
 * Serialise a `params` object onto a path as a query string.
 *
 * WHY THIS EXISTS. Five call sites were written axios-style:
 *
 *     api.get('/god/job-runs', { params: { status, limit: 100 } })
 *
 * `params` means nothing to `fetch`. It was spread into the request init,
 * ignored, and the request went out UNFILTERED — with no error, no warning and
 * a perfectly successful 200 response. That is the worst shape a bug can take:
 * the God Mode job-run and revenue-history filters appeared to work, returned
 * data, and were simply answering a different question than the one asked.
 *
 * Empty string, null and undefined are DROPPED rather than sent as blanks: the
 * call sites use `|| undefined` to mean "no filter", and `?status=` is not the
 * same request as one with no status at all.
 */
function withQuery(path, params) {
  if (!params || typeof params !== 'object') return path
  const qs = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) {
      for (const v of value) {
        if (v !== undefined && v !== null && v !== '') qs.append(key, String(v))
      }
    } else {
      qs.append(key, String(value))
    }
  }
  const query = qs.toString()
  if (!query) return path
  return path + (path.includes('?') ? '&' : '?') + query
}

// WHAT KIND OF FAILURE THIS WAS — decided in a module that imports nothing,
// so the rule can be executed under plain node. See api/httpErrors.js.
import { httpErrorKind as _httpErrorKind,
         fallbackHttpMessage as _fallbackHttpMessage } from './httpErrors'

async function request(path, options = {}, attempt = 0, skipRedirect = false) {
  const token = getToken()
  const headers = { ...options.headers }
  if (token) headers['Authorization'] = `Bearer ${token}`
  // The selected customer scopes every request BY DEFAULT — that is the whole
  // point of it. `noOrgContext` is the deliberate exception, for God Mode's own
  // platform-wide reads: the Command Center's executive summary shows platforms,
  // organizations, users and leads across the whole estate, and a tile that
  // silently narrowed to one customer while the ones beside it did not would be
  // a wrong number sitting in a row of right ones.
  //
  // AND ONLY ON CUSTOMER-SPACE ROUTES. THE LEAK THIS CLOSES:
  //
  // The override was previously sent from wherever the user happened to be
  // standing. On the server `deps.get_current_user` answers it with
  // `user.organization_id = org_override` for a god_admin, and ~179 routers
  // read that attribute at face value — so after entering Restland, every
  // later request, including ones to platform tools, arrived claiming to BE
  // Restland, and the `_god_all_orgs` flag meaning "no customer selected"
  // was silently off.
  //
  // That is not just a misleading banner. A back-office tool whose target
  // defaults to `current_user.organization_id` would act on whichever
  // customer the owner last looked at.
  //
  // The selection is still REMEMBERED across the trip — returning to the
  // customer app does not mean picking them again — it is simply not SENT
  // where it does not belong. `noOrgContext` remains the explicit opt-out for
  // platform-wide reads made from inside customer space.
  //
  // AND `asCustomer` IS THE ONE WAY TO SAY IT EXPLICITLY.
  //
  // THE DEFECT THAT NAMES THIS OPTION. Deciding the header from
  // `window.location.pathname` is right for every request a SCREEN makes —
  // the screen you are on is what the request is about. It is wrong for the
  // one request made DURING a transition, before the browser has moved:
  // `enterCustomer` clears the branding cache and re-reads `/branding/org`
  // while the address bar still says `/god`, which classifies as PLATFORM, so
  // the header was stripped from the very call whose entire purpose was to
  // read the customer being entered. The server answered for the neutral
  // owner — `industry: null` — that answer was cached, and the app then
  // navigated to a workspace whose dashboard, rail and vocabulary are all
  // chosen from `industry`. The customer got the platform's generic shell.
  //
  // This is a per-call, caller-supplied organization: it reopens nothing,
  // because the leak that the route rule closed was the header being sent
  // IMPLICITLY from wherever somebody happened to be standing. The server
  // authorizes it exactly as before.
  const orgOverride = orgOverrideFor(
    typeof window !== 'undefined' ? window.location.pathname : '/',
    { orgId: getOrgContext()?.orgId || null,
      noOrgContext: !!options.noOrgContext,
      asCustomer: options.asCustomer || null })
  if (orgOverride) headers['X-Org-Override'] = orgOverride
  // THE BRAND TRAVELS WITH THE REQUEST TOO.
  //
  // A brand used to be inferred from whichever customer was selected, so
  // standing in a brand with no customer inside it was not expressible -
  // which is why /sales returned every brand's pipeline at once. Sent
  // even on noOrgContext reads: those are the platform-wide God Mode
  // views, and the brand is exactly what should narrow them.
  const brandCtx = getBrandContext()
  if (brandCtx) headers['X-Brand-Override'] = brandCtx.platformId
  // THE SELECTED WORKSPACE TRAVELS TOO — as a REQUEST, never as a grant.
  //
  // A person can hold memberships in several customer workspaces, so which one
  // they are standing in has to reach the server on every request. It is stored
  // locally only as the current UI selection: the server re-derives it against
  // an active customer_org membership on arrival and ignores it otherwise, so
  // editing this value in devtools changes which workspace you ASK for and
  // never which one you get.
  const wsId = getWorkspaceContext()
  if (wsId) headers['X-Workspace-Id'] = wsId
  // Executive Observation Mode: inject org context header so the server can
  // pass require_tenant_user while still marking the session read-only.
  // Only sent when observation context is active (ExecObserveShell mounted).
  if (_observationOrgId) headers['X-Executive-Observe'] = _observationOrgId
  if (!(options.body instanceof FormData) && options.body) {
    headers['Content-Type'] = 'application/json'
  }

  // A FILE UPLOAD IS NOT SAFE TO REPLAY.
  //
  // A transport failure does not tell you whether the server processed the
  // request - only that the answer never came back. Replaying a GET is free;
  // replaying `POST /leads/upload/confirm` after the import already committed
  // imports the whole batch a second time. The retry loop did exactly that,
  // three times, for every FormData upload.
  //
  // A FormData body is also single-use once its File stream has been read, so
  // the retry was frequently replaying a body the browser had already
  // consumed - failing again for a different reason than the original.
  const isUpload = options.body instanceof FormData
  const retriesAllowed = isUpload ? 0 : MAX_RETRIES

  // Strip our own options before they reach fetch(), and fold `params` into
  // the URL. Doing it here rather than in api.get() means every verb gets it.
  const fetchOptions = { ...options, headers }
  for (const key of CLIENT_ONLY_OPTIONS) delete fetchOptions[key]
  const url = `${API_BASE}${withQuery(path, options.params)}`

  let res
  try {
    res = await fetch(url, fetchOptions)
  } catch (networkErr) {
    if (attempt < retriesAllowed) {
      await sleep(RETRY_DELAY_MS)
      return request(path, options, attempt + 1)
    }
    // NO RESPONSE AT ALL — and only this, never an HTTP status.
    //
    // A rejected fetch means the browser was handed nothing it may read: the
    // connection dropped, the host did not answer, or a proxy replied without
    // CORS headers (Render's own 502/503 while an instance restarts looks
    // exactly like that). Every HTTP answer from the application itself,
    // including a 500, carries CORS headers — see the exception handler in
    // app/main.py — and is reported below with its status, never here.
    //
    // The old wording told the reader to "check your connection". In the
    // production incident that named this, the reader's connection was fine
    // and the server was restarting; the sentence sent people to look at
    // their wifi. It now says what is actually known.
    //
    // `kind` lets a caller tell this apart without parsing prose. There is
    // deliberately NO `status`: auth/workspaceGuard.js reads a missing status
    // as "no refusal occurred", which is exactly true here.
    const err = new Error(
      isUpload
        ? 'The upload did not complete. Nothing was imported — the server did not respond. Try again in a moment.'
        : 'Unable to reach the server. It did not respond — it may be restarting. Try again in a moment.'
    )
    err.kind = 'network'
    throw err
  }


  if (res.status === 401) {
    // Retry once after 2 s before giving up. Absorbs cold-start/token-race 401s.
    if (attempt === 0) {
      await sleep(2000)
      const freshToken = getToken()
      if (freshToken) return request(path, options, 1, skipRedirect)
    }
    // Only redirect once -- prevent parallel 401s queuing multiple redirects.
    if (!skipRedirect && !window._af_redirecting) {
      window._af_redirecting = true
      clearToken()
      window.location.href = '/login'
    }
    throw new Error('Session expired')
  }

  if (!res.ok) {
    let detail = 'Request failed'
    try {
      const data = await res.json()
      detail = data.detail || detail
    } catch {}
    // `detail` is a string on almost every route, but FastAPI lets it be an
    // object and a few routes use that to return structured refusals - the
    // Checkpoint 6 launch route returns a message plus a list of warnings.
    // `new Error(object)` produces the message "[object Object]", throwing that
    // information away at exactly the moment somebody needs to read it, so the
    // raw value is carried alongside. Existing callers reading `err.message`
    // are unaffected: a string detail still becomes the message.
    const err = new Error(typeof detail === 'string' && detail !== 'Request failed'
                          ? detail
                          : (detail && detail.message) || _fallbackHttpMessage(res.status, detail))
    err.detail = detail
    err.status = res.status
    err.kind = _httpErrorKind(res.status)
    throw err
  }

  const contentType = res.headers.get('content-type') || ''
  if (contentType.includes('application/json')) return res.json()
  return res.text()
}

// ── ONE DASHBOARD LOAD, FORTY-FOUR ROUND TRIPS ─────────────────────────────
//
// MEASURED, in the production backend logs, from a single client IP inside one
// four-second window: 22 GETs, each preceded by its own CORS preflight. Two of
// those GETs were `/settings/profile` and two were `/settings/my-capabilities`
// - the same question, asked twice, in the same instant, because two
// components each fetch it on mount and neither knows about the other.
//
// `useWorkspaceAuthority()` and `Layout.jsx` both ask what the signed-in person
// may administer. Both are right to ask. Neither should be the one made
// responsible for knowing that the other exists, because that is the coupling
// that breaks the next time somebody adds a third caller.
//
// SO THIS COALESCES IN FLIGHT, AND ONLY IN FLIGHT.
//
// While a GET is outstanding, an identical GET joins it instead of opening a
// second connection. The moment it settles, the entry is dropped - so the next
// call goes to the network like any other. There is no TTL, no stored response
// and no cache to invalidate.
//
// THAT DISTINCTION IS THE WHOLE SAFETY ARGUMENT, and it is why this is not a
// response cache. A cache with a TTL would mean a capability list, an
// entitlement or a workspace membership could be read after it changed - the
// exact failure the comments in workspaceAuthority.js and Layout.jsx are
// written to prevent, where a nav item renders a door the server will refuse.
// Coalescing cannot do that: every caller receives the answer to a request
// that was already in flight when they asked, which is no staler than the
// answer they would have received from their own request issued in the same
// millisecond. Nothing is ever served from a previous moment.
//
// A FAILURE IS NOT SHARED FORWARD. The entry is dropped in `finally`, so a
// transient error is never held and re-handed to a later caller. Callers that
// were already joined do see the same rejection - which is what their own
// request would have done too.
//
// THE KEY CARRIES THE SCOPE. Every header that narrows a request - the org
// override, the brand, the selected workspace, observation mode - is part of
// the key, along with the options that change how the response is handled. Two
// reads of the same path in two different customer contexts are two different
// questions and must never be merged. `_inFlightGets` is cleared on login and
// logout for the same reason `_contextsPromise` is.
const _inFlightGets = new Map()

function _getDedupeKey(path, opts) {
  // Read the same scoping values `request()` reads, so the key cannot drift
  // from the headers actually sent.
  // `asCustomer` is a SCOPING value, so it belongs in the key like the rest.
  // Left out, the entry-time read of /branding/org could be merged with a
  // platform-context read of the same path already in flight — which is the
  // same wrong answer this option exists to stop, arriving by another route.
  const org = opts.asCustomer
    || (opts.noOrgContext ? '' : (getOrgContext()?.orgId || ''))
  const brand = getBrandContext()?.platformId || ''
  const ws = getWorkspaceContext() || ''
  const obs = _observationOrgId || ''
  const flags = [opts.noOrgContext ? 1 : 0, opts.skipRedirect ? 1 : 0,
                 opts.asCustomer ? 1 : 0].join('')
  const route = typeof window !== 'undefined' ? window.location.pathname : '/'
  return [path, org, brand, ws, obs, flags, route].join('\u0000')
}

export function resetInFlightGets() {
  _inFlightGets.clear()
}

function dedupedGet(path, opts = {}) {
  const key = _getDedupeKey(path, opts)
  const existing = _inFlightGets.get(key)
  if (existing) return existing
  const promise = request(path, { method: 'GET', ...opts }, 0, opts.skipRedirect || false)
    .finally(() => {
      // Drop it whether it resolved or rejected. Holding either would turn
      // coalescing into caching.
      if (_inFlightGets.get(key) === promise) _inFlightGets.delete(key)
    })
  _inFlightGets.set(key, promise)
  return promise
}

export const api = {
  get: (path, opts = {}) => dedupedGet(path, opts),
  post: (path, body) => request(path, { method: 'POST', body: body instanceof FormData ? body : JSON.stringify(body) }),
  put: (path, body) => request(path, { method: 'PUT', body: body instanceof FormData ? body : JSON.stringify(body) }),
  patch: (path, body) => request(path, { method: 'PATCH', body: body instanceof FormData ? body : JSON.stringify(body) }),
  delete: (path) => request(path, { method: 'DELETE' }),
  upload: (path, formData) => request(path, { method: 'POST', body: formData }),
}

// ── Authenticated binary fetch ───────────────────────────────────────────────
//
// `request()` above parses JSON, which is right for every endpoint that returns
// any. A stored file does not: it returns bytes behind the same Authorization
// header as everything else.
//
// This exists because an <img src="/wholesale/files/..."> CANNOT send that
// header — the browser issues a plain unauthenticated GET — so a private
// document served that way would either 401 or, worse, have to be made public
// to render. Fetching the bytes here and handing back an object URL keeps the
// endpoint authenticated and keeps the token out of the URL, which is the one
// place it must never appear.
//
// Callers own the returned URL and must URL.revokeObjectURL() it when done.
export async function fetchObjectUrl(path) {
  const token = getToken()
  const res = await fetch(`${API_BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!res.ok) {
    const err = new Error(res.status === 404 ? 'File not found' : 'Could not load file')
    err.status = res.status
    throw err
  }
  return URL.createObjectURL(await res.blob())
}

export async function login(email, password) {
  const form = new URLSearchParams()
  form.append('username', email)
  form.append('password', password)
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form,
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || 'Login failed')
  }
  const data = await res.json()
  setToken(data.access_token)
  // A NEW SESSION GETS A NEW ANSWER. The shared context list belongs to
  // whoever was signed in when it was fetched; carrying it across a sign-in
  // would show one person the other's workspaces until something refetched.
  resetMyContexts()
  // Any GET still in flight was asked as the PREVIOUS token holder. Joining
  // one now would hand this person the last person's answer.
  resetInFlightGets()
  localStorage.setItem(KEY_USER, JSON.stringify({
    full_name: data.full_name, role: data.role, organization_id: data.organization_id,
    must_change_password: data.must_change_password,
  }))
  return data
}

export function setMustChangePassword(value) {
  const user = getCurrentUser()
  if (!user) return
  user.must_change_password = value
  localStorage.setItem(KEY_USER, JSON.stringify(user))
}

export function getCurrentUser() {
  _migrate(KEY_USER, 'bookaboost_user')
  const raw = localStorage.getItem(KEY_USER)
  return raw ? JSON.parse(raw) : null
}

export async function refreshCurrentUser() {
  try {
    const profile = await api.get('/settings/profile', { skipRedirect: true })
    const stored = getCurrentUser()
    if (stored && profile?.role) {
      stored.role = profile.role
      if (profile.full_name) stored.full_name = profile.full_name
      localStorage.setItem(KEY_USER, JSON.stringify(stored))
    }
    return profile || null
  } catch {
    return null
  }
}


export async function logout() {
  // Tell the server to invalidate the session immediately (clears session_token).
  // Best-effort â€” if the network call fails the local state is still cleared.
  const token = getToken()
  if (token) {
    // Fire and forget — never await. Awaiting caused logout to hang on cold
    // start, stranding the user on the executive shell indefinitely.
    fetch(`${API_BASE}/auth/logout`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    }).catch(() => {})
  }
  clearToken()
  localStorage.removeItem(KEY_USER)
  localStorage.removeItem('bookaboost_user') // clean up legacy key
  localStorage.removeItem(KEY_BRANDING)
  localStorage.removeItem('bb_branding')     // clean up legacy key
  // THE CONTEXT GOES TOO, AND IT DID NOT USED TO.
  //
  // Logging out cleared the token, the user and the branding, and left
  // af_org_context, af_brand_context and af_workspace_id sitting in
  // localStorage. So the NEXT person to sign in on that browser sent the
  // previous person's X-Org-Override, X-Brand-Override and X-Workspace-Id on
  // every request - most obviously after somebody used God Mode to enter a
  // customer and then handed the laptop over.
  //
  // The server refuses all three for a user who is not entitled to them, so
  // this was never a way IN. It is still wrong: a signed-out context is not
  // this person's context, and "it fails closed" is a property of today's
  // server rather than a promise the browser is keeping. clearAllContext()
  // already existed and did exactly this; logout simply never called it.
  clearAllContext()
  // The in-memory context list goes with them for the same reason.
  resetMyContexts()
  // And so does anything still in flight, for the same reason again.
  resetInFlightGets()
}

// â”€â”€ Keep-alive â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// Ping the backend every 14 minutes so Render free-tier never sleeps while
// an advisor has the app open. Call startKeepAlive() after login,
// stopKeepAlive() after logout.

let _keepAliveInterval = null

export function startKeepAlive() {
  if (_keepAliveInterval) return // already running
  _keepAliveInterval = setInterval(async () => {
    try {
      await fetch(`${API_BASE}/ping`)
    } catch {
      // Silent â€” this is best-effort, not critical
    }
  }, 14 * 60 * 1000) // 14 minutes
}

export function stopKeepAlive() {
  if (_keepAliveInterval) {
    clearInterval(_keepAliveInterval)
    _keepAliveInterval = null
  }
}

// â”€â”€ Token refresh loop â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// JWT lifetime is 2 hours. While the app is open, silently refresh every 30
// minutes so an active user is never kicked. If the refresh fails (401 = server
// kicked the session) the request() handler above will redirect to /login on the
// next real API call. Call startRefreshLoop() right after login and
// stopRefreshLoop() on logout.

let _refreshInterval = null
const REFRESH_INTERVAL_MS = 30 * 60 * 1000 // 30 minutes

export function startRefreshLoop() {
  if (_refreshInterval) return // already running
  _refreshInterval = setInterval(async () => {
    const token = getToken()
    if (!token) return
    try {
      const res = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.ok) {
        const data = await res.json()
        if (data.access_token) setToken(data.access_token)
      }
      // Non-2xx (401 = session was force-killed server-side): don't redirect here.
      // The next real API call will 401 and the request() handler redirects to /login.
    } catch {
      // Network error â€” silent. The user is still "using" the app; the 2-hr JWT
      // stays valid until the server rejects it.
    }
  }, REFRESH_INTERVAL_MS)
}

export function stopRefreshLoop() {
  if (_refreshInterval) {
    clearInterval(_refreshInterval)
    _refreshInterval = null
  }
}

// â”€â”€ Branding â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

/**
 * @param {object}  [opts]
 * @param {boolean} [opts.applyTheme=true]  Paint the page with this branding.
 *        An operator in customer-view passes false: they want the customer's
 *        ENTITLEMENTS, which is what the sidebar and dashboard render from,
 *        without God Mode silently taking on the customer's colours, favicon
 *        and document title.
 */
export async function fetchAndStoreBranding({ applyTheme = true,
                                              asCustomer = null } = {}) {
  try {
    // Primary source: per-org branding set by god_admin in Command Center.
    //
    // `asCustomer` NAMES THE WORKSPACE THIS ANSWER IS ABOUT, and the one
    // caller that passes it is the one that cannot rely on the address bar:
    // entering a customer reads this while still standing on /god. See the
    // `asCustomer` block in request().
    const data = await api.get('/branding/org', { skipRedirect: true, asCustomer })
    const branding = {
      brand_name: data.brand_name || null,
      brand_logo_url: data.brand_logo_url || null,
      brand_color_primary: data.brand_color_primary || null,
      brand_color_accent: data.brand_color_accent || null,
      favicon_url: data.favicon_url || null,
      tagline: data.tagline || null,
      support_email: data.support_email || null,
      email_sender_name: data.email_sender_name || null,
      // WHAT THIS WORKSPACE IS ENTITLED TO, AND WHO THIS PERSON IS IN IT.
      //
      // This object was an eight-key whitelist and these three were not in
      // it, so `Layout.jsx`'s `branding?.enabled_features ?? null` was always
      // null — which it reads as "no restriction". The sidebar could not hide
      // a module even when the server would refuse it, and `branding.industry`
      // being absent is why the industry-aware checks never fired either.
      //
      // `??` not `||`: an empty array means "no modules enabled", and `||`
      // would turn that into null, which is the exact opposite instruction.
      enabled_features: data.enabled_features ?? null,
      industry: data.industry ?? null,
      workspace_role: data.workspace_role ?? null,
      organization_id: data.organization_id ?? null,
      // The white-label platform this workspace belongs to. Used only where
      // the hostname is not a brand domain (see theme.js shellTheme).
      platform: data.platform ?? null,
    }
    localStorage.setItem(KEY_BRANDING, JSON.stringify(branding))
    if (applyTheme) { applyBrandingCSS(branding); applyBrandingDOM(branding) }
    return branding
  } catch {
    // Fall back to org-settings for backward compat
    try {
      const data = await api.get('/org-settings/', { skipRedirect: true, asCustomer })
      const branding = {
        brand_name: data.brand_name || data.name || null,
        brand_logo_url: data.brand_logo_url || null,
        brand_color_primary: data.brand_color_primary || null,
        brand_color_accent: data.brand_color_accent || null,
        favicon_url: null,
        tagline: null,
        support_email: null,
        email_sender_name: null,
        // The fallback path dropped these too, so a deployment that fell back
        // got the same fail-open sidebar. `/org-settings/` has always
        // returned both.
        enabled_features: data.enabled_features ?? null,
        industry: data.industry ?? null,
        workspace_role: null,
        organization_id: data.id ?? null,
      }
      localStorage.setItem(KEY_BRANDING, JSON.stringify(branding))
      if (applyTheme) { applyBrandingCSS(branding); applyBrandingDOM(branding) }
      return branding
    } catch { return null }
  }
}

export function getBranding() {
  _migrate(KEY_BRANDING, 'bb_branding')
  const raw = localStorage.getItem(KEY_BRANDING)
  return raw ? JSON.parse(raw) : null
}

/**
 * FORGET THE PREVIOUS WORKSPACE'S ANSWER.
 *
 * `af_branding` carries `enabled_features` — an ALLOW-LIST, keyed to one
 * organization. Entering a second customer without clearing it renders the
 * first customer's modules under the second customer's name until the fetch
 * comes back, which is a wrong answer with a confident banner over it. Called
 * on entering and on leaving a customer.
 */
export function clearBranding() {
  try { localStorage.removeItem(KEY_BRANDING) } catch { /* storage blocked */ }
}


export function applyBrandingCSS(branding) {
  if (!branding) return
  const root = document.documentElement
  const primary = branding.brand_color_primary
  const accent = branding.brand_color_accent

  if (primary) {
    root.style.setProperty('--accent', primary)
    root.style.setProperty('--brand-primary', primary)
    root.style.setProperty('--signal-blue', primary)
    root.style.setProperty('--signal-blue-dim', hexToRgba(primary, 0.15))
    root.style.setProperty('--border-subtle', hexToRgba(primary, 0.18))
    root.style.setProperty('--border-strong', hexToRgba(primary, 0.42))
    root.style.setProperty('--glow-blue-sm', `0 0 14px ${hexToRgba(primary, 0.28)}`)
    root.style.setProperty('--glow-blue-md', `0 0 24px ${hexToRgba(primary, 0.30)}`)
    root.style.setProperty('--glow-blue-lg', `0 0 52px ${hexToRgba(primary, 0.34)}`)
  }
  if (accent) {
    root.style.setProperty('--brand-accent', accent)
    root.style.setProperty('--signal-green', accent)
    root.style.setProperty('--signal-green-dim', hexToRgba(accent, 0.15))
    root.style.setProperty('--glow-green-sm', `0 0 14px ${hexToRgba(accent, 0.26)}`)
    root.style.setProperty('--glow-green-md', `0 0 30px ${hexToRgba(accent, 0.30)}`)
  }
}

export function applyBrandingDOM(branding) {
  if (!branding) return

  // A host that is not a brand domain (localhost) wears the workspace's own
  // platform brand instead of falling through to BookaBoost.
  applyWorkspaceTheme(branding)

  // Swap favicon if org has one set
  if (branding.favicon_url) {
    let link = document.querySelector("link[rel~='icon']")
    if (!link) {
      link = document.createElement('link')
      link.rel = 'icon'
      document.head.appendChild(link)
    }
    link.href = branding.favicon_url
  }

  // Store tagline and support email for components to read via getBranding()
  // (logo and brand_name are applied by Layout/Sidebar via getBranding())
}

function hexToRgba(hex, alpha) {
  let h = hex.replace('#', '')
  if (h.length === 3) h = h.split('').map(c => c + c).join('')
  const r = parseInt(h.substring(0, 2), 16)
  const g = parseInt(h.substring(2, 4), 16)
  const b = parseInt(h.substring(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

// â”€â”€ Org Context (super admin only) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const ORG_CONTEXT_KEY = 'af_org_context'
const BRAND_CONTEXT_KEY = 'af_brand_context'

export function setBrandContext(platformId, brandName) {
  try {
    localStorage.setItem(BRAND_CONTEXT_KEY,
                         JSON.stringify({ platformId, brandName }))
  } catch { /* storage blocked - the header simply will not be sent */ }
}

export function getBrandContext() {
  try {
    const raw = localStorage.getItem(BRAND_CONTEXT_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function clearBrandContext() {
  try { localStorage.removeItem(BRAND_CONTEXT_KEY) } catch { /* nothing to clear */ }
}

export function setOrgContext(orgId, orgName) {
  localStorage.setItem(ORG_CONTEXT_KEY, JSON.stringify({ orgId, orgName }))
}

export function getOrgContext() {
  // Migrate legacy key
  _migrate(ORG_CONTEXT_KEY, 'bb_org_context')
  const raw = localStorage.getItem(ORG_CONTEXT_KEY)
  return raw ? JSON.parse(raw) : null
}


export function clearOrgContext() {
  localStorage.removeItem(ORG_CONTEXT_KEY)
  localStorage.removeItem('bb_org_context') // clean up legacy key
}

// ── THE SELECTED CUSTOMER WORKSPACE ────────────────────────────────────────
//
// Stored, and worth being precise about what "stored" means here: this is the
// current UI SELECTION and nothing else. It is not a credential, it is not
// authorization, and changing it in devtools buys nothing - the server checks
// every request against an active customer_org membership and ignores an id
// the caller does not hold. It lives in localStorage so a refresh keeps you in
// the workspace you were working in rather than dumping you back at the door.
const WORKSPACE_CONTEXT_KEY = 'af_workspace_id'

export function setWorkspaceContext(organizationId) {
  if (organizationId) localStorage.setItem(WORKSPACE_CONTEXT_KEY, organizationId)
  else localStorage.removeItem(WORKSPACE_CONTEXT_KEY)
}

export function getWorkspaceContext() {
  try {
    return localStorage.getItem(WORKSPACE_CONTEXT_KEY) || null
  } catch (e) {
    return null
  }
}

export function clearWorkspaceContext() {
  localStorage.removeItem(WORKSPACE_CONTEXT_KEY)
}

// THE SERVER BUILDS THIS LIST. The browser renders it and invents nothing:
// no context is derived from a role label, from organization_id, or from
// anything cached locally.
//
// SHARED FOR THE SIGNED-IN SESSION, AND ONLY FOR IT.
//
// Landing on "/" and then entering a workspace used to ask the server this
// same question twice within a few hundred milliseconds, on a free-tier host
// where the second of two near-simultaneous cold requests is the one that
// fails. The answer cannot change between those two moments, so it is fetched
// once and shared.
//
// Two rules keep that from becoming a cached authorization:
//   1. A FAILURE IS NEVER CACHED - the promise is dropped so the next caller
//      re-asks. A transient error must not become a permanent one.
//   2. It is dropped on login and on logout, so it can never outlive the
//      session that earned it.
// And it is not the control in any case: every route behind every entry in
// this list re-checks membership server-side on its own.
let _contextsPromise = null

export function fetchMyContexts(opts = {}) {
  if (opts.force || !_contextsPromise) {
    _contextsPromise = api.get('/auth/my-contexts').catch(err => {
      _contextsPromise = null
      throw err
    })
  }
  return _contextsPromise
}

export function resetMyContexts() {
  _contextsPromise = null
}

// Leaving everything means leaving the brand as well - a stale brand under a
// cleared customer would render a trail the server does not agree with. The
// workspace goes with them for the same reason.
export function clearAllContext() {
  clearOrgContext()
  clearBrandContext()
  clearWorkspaceContext()
  clearObservationContext()
}

// ── Executive Observation Context ───────────────────────────────────────────
//
// When a brand executive enters a customer org in read-only observation mode,
// every API request carries X-Executive-Observe: <orgId>. The server uses this
// to inject the org context in-flight (not persisted) and mark the session as
// observation-only so all mutation endpoints refuse with 403.
//
// This is a module-level variable, NOT localStorage. Observation context is
// ephemeral: it lives only while ExecObserveShell is mounted. A page reload
// sends the executive back to the Executive Suite, not into the customer app.
let _observationOrgId = null

export function setObservationContext(orgId) {
  _observationOrgId = orgId || null
}

export function getObservationContext() {
  return _observationOrgId
}

export function clearObservationContext() {
  _observationOrgId = null
}



