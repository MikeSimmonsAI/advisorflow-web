const API_BASE = import.meta.env.VITE_API_BASE_URL || 'https://advisorflow-backend.onrender.com'

function getToken() {
  return localStorage.getItem('bookaboost_token')
}

export function setToken(token) {
  localStorage.setItem('bookaboost_token', token)
}

export function clearToken() {
  localStorage.removeItem('bookaboost_token')
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
 *  - Only retries TypeError (network-level failure — no response from server)
 *  - Does NOT retry HTTP errors (401, 403, 404, 500, etc.) — those are real
 *  - Up to MAX_RETRIES attempts with RETRY_DELAY_MS between each
 *  - Auth errors (401) redirect to login immediately, no retry
 */
const MAX_RETRIES = 2
const RETRY_DELAY_MS = 3000

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

async function request(path, options = {}, attempt = 0) {
  const token = getToken()
  const headers = { ...options.headers }
  if (token) headers['Authorization'] = `Bearer ${token}`
  const orgCtx = getOrgContext()
  if (orgCtx) headers['X-Org-Override'] = orgCtx.orgId
  if (!(options.body instanceof FormData) && options.body) {
    headers['Content-Type'] = 'application/json'
  }

  let res
  try {
    res = await fetch(`${API_BASE}${path}`, { ...options, headers })
  } catch (networkErr) {
    // Network-level failure (server unreachable, CORS preflight blocked, etc.)
    // Retry up to MAX_RETRIES times — handles Render cold start wake-up delay
    if (attempt < MAX_RETRIES) {
      await sleep(RETRY_DELAY_MS)
      return request(path, options, attempt + 1)
    }
    // Exhausted retries — surface a cleaner message than the raw browser error
    throw new Error('Unable to reach the server. Please check your connection or try again in a moment.')
  }

  if (res.status === 401) {
    clearToken()
    window.location.href = '/login'
    throw new Error('Session expired')
  }

  if (!res.ok) {
    let detail = 'Request failed'
    try {
      const data = await res.json()
      detail = data.detail || detail
    } catch {}
    throw new Error(detail)
  }

  const contentType = res.headers.get('content-type') || ''
  if (contentType.includes('application/json')) return res.json()
  return res.text()
}

export const api = {
  get: (path) => request(path, { method: 'GET' }),
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
  localStorage.setItem('bookaboost_user', JSON.stringify({
    full_name: data.full_name, role: data.role, organization_id: data.organization_id,
    must_change_password: data.must_change_password,
  }))
  return data
}

export function setMustChangePassword(value) {
  const user = getCurrentUser()
  if (!user) return
  user.must_change_password = value
  localStorage.setItem('bookaboost_user', JSON.stringify(user))
}

export function getCurrentUser() {
  const raw = localStorage.getItem('bookaboost_user')
  return raw ? JSON.parse(raw) : null
}

export function logout() {
  clearToken()
  localStorage.removeItem('bookaboost_user')
  localStorage.removeItem('bb_branding')
}

// ── Keep-alive ────────────────────────────────────────────────────────────────
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
      // Silent — this is best-effort, not critical
    }
  }, 14 * 60 * 1000) // 14 minutes
}

export function stopKeepAlive() {
  if (_keepAliveInterval) {
    clearInterval(_keepAliveInterval)
    _keepAliveInterval = null
  }
}

// ── Branding ─────────────────────────────────────────────────────────────────

export async function fetchAndStoreBranding() {
  try {
    const data = await api.get('/org-settings/')
    const branding = {
      brand_name: data.brand_name || data.name || 'BookaBoost',
      brand_logo_url: data.brand_logo_url || null,
      brand_color_primary: data.brand_color_primary || null,
      brand_color_accent: data.brand_color_accent || null,
      industry: data.industry || 'funeral',
      enabled_features: data.enabled_features || null,
      member_label: data.member_label || null,
      members_label: data.members_label || null,
    }
    localStorage.setItem('bb_branding', JSON.stringify(branding))
    applyBrandingCSS(branding)
    return branding
  } catch { return null }
}

export function getBranding() {
  const raw = localStorage.getItem('bb_branding')
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

function hexToRgba(hex, alpha) {
  let h = hex.replace('#', '')
  if (h.length === 3) h = h.split('').map(c => c + c).join('')
  const r = parseInt(h.substring(0, 2), 16)
  const g = parseInt(h.substring(2, 4), 16)
  const b = parseInt(h.substring(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

// ── Org Context (super admin only) ───────────────────────────────────────────

const ORG_CONTEXT_KEY = 'bb_org_context'

export function setOrgContext(orgId, orgName) {
  localStorage.setItem(ORG_CONTEXT_KEY, JSON.stringify({ orgId, orgName }))
}

export function getOrgContext() {
  const raw = localStorage.getItem(ORG_CONTEXT_KEY)
  return raw ? JSON.parse(raw) : null
}

export function clearOrgContext() {
  localStorage.removeItem(ORG_CONTEXT_KEY)
}
