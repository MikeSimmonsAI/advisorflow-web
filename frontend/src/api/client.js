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
  const orgCtx = options.noOrgContext ? null : getOrgContext()
  if (orgCtx) headers['X-Org-Override'] = orgCtx.orgId
  // THE BRAND TRAVELS WITH THE REQUEST TOO.
  //
  // A brand used to be inferred from whichever customer was selected, so
  // standing in a brand with no customer inside it was not expressible -
  // which is why /sales returned every brand's pipeline at once. Sent
  // even on noOrgContext reads: those are the platform-wide God Mode
  // views, and the brand is exactly what should narrow them.
  const brandCtx = getBrandContext()
  if (brandCtx) headers['X-Brand-Override'] = brandCtx.platformId
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

  let res
  try {
    res = await fetch(`${API_BASE}${path}`, { ...options, headers })
  } catch (networkErr) {
    if (attempt < retriesAllowed) {
      await sleep(RETRY_DELAY_MS)
      return request(path, options, attempt + 1)
    }
    throw new Error(
      isUpload
        ? 'The upload did not complete. Nothing was imported — check your connection and try again.'
        : 'Unable to reach the server. Please check your connection or try again in a moment.'
    )
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
    const err = new Error(typeof detail === 'string' ? detail
                          : (detail && detail.message) || 'Request failed')
    err.detail = detail
    err.status = res.status
    throw err
  }

  const contentType = res.headers.get('content-type') || ''
  if (contentType.includes('application/json')) return res.json()
  return res.text()
}

export const api = {
  get: (path, opts = {}) => request(path, { method: 'GET', ...opts }, 0, opts.skipRedirect || false),
  post: (path, body) => request(path, { method: 'POST', body: body instanceof FormData ? body : JSON.stringify(body) }),
  put: (path, body) => request(path, { method: 'PUT', body: body instanceof FormData ? body : JSON.stringify(body) }),
  patch: (path, body) => request(path, { method: 'PATCH', body: body instanceof FormData ? body : JSON.stringify(body) }),
  delete: (path) => request(path, { method: 'DELETE' }),
  upload: (path, formData) => request(path, { method: 'POST', body: formData }),
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
    try {
      await fetch(`${API_BASE}/auth/logout`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
    } catch { /* silent â€” we're logging out regardless */ }
  }
  clearToken()
  localStorage.removeItem(KEY_USER)
  localStorage.removeItem('bookaboost_user') // clean up legacy key
  localStorage.removeItem(KEY_BRANDING)
  localStorage.removeItem('bb_branding')     // clean up legacy key
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

export async function fetchAndStoreBranding() {
  try {
    // Primary source: per-org branding set by god_admin in Command Center
    const data = await api.get('/branding/org', { skipRedirect: true })
    const branding = {
      brand_name: data.brand_name || null,
      brand_logo_url: data.brand_logo_url || null,
      brand_color_primary: data.brand_color_primary || null,
      brand_color_accent: data.brand_color_accent || null,
      favicon_url: data.favicon_url || null,
      tagline: data.tagline || null,
      support_email: data.support_email || null,
      email_sender_name: data.email_sender_name || null,
    }
    localStorage.setItem(KEY_BRANDING, JSON.stringify(branding))
    applyBrandingCSS(branding)
    applyBrandingDOM(branding)
    return branding
  } catch {
    // Fall back to org-settings for backward compat
    try {
      const data = await api.get('/org-settings/', { skipRedirect: true })
      const branding = {
        brand_name: data.brand_name || data.name || null,
        brand_logo_url: data.brand_logo_url || null,
        brand_color_primary: data.brand_color_primary || null,
        brand_color_accent: data.brand_color_accent || null,
        favicon_url: null,
        tagline: null,
        support_email: null,
        email_sender_name: null,
      }
      localStorage.setItem(KEY_BRANDING, JSON.stringify(branding))
      applyBrandingCSS(branding)
      applyBrandingDOM(branding)
      return branding
    } catch { return null }
  }
}

export function getBranding() {
  _migrate(KEY_BRANDING, 'bb_branding')
  const raw = localStorage.getItem(KEY_BRANDING)
  return raw ? JSON.parse(raw) : null
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

// Leaving everything means leaving the brand as well - a stale brand under a
// cleared customer would render a trail the server does not agree with.
export function clearAllContext() {
  clearOrgContext()
  clearBrandContext()
}



