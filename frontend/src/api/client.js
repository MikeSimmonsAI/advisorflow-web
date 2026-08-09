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

async function request(path, options = {}) {
  const token = getToken()
  const headers = { ...options.headers }
  if (token) headers['Authorization'] = `Bearer ${token}`
  const orgCtx = getOrgContext()
  if (orgCtx) headers['X-Org-Override'] = orgCtx.orgId
  if (!(options.body instanceof FormData) && options.body) {
    headers['Content-Type'] = 'application/json'
  }

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers })

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
    // Set the brand variable AND override the signal-blue that's used throughout the UI
    root.style.setProperty('--accent', primary)
    root.style.setProperty('--brand-primary', primary)
    root.style.setProperty('--signal-blue', primary)
    // Derive a dim version at ~15% opacity using the hex color
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
  // Handles #rrggbb and #rgb
  let h = hex.replace('#', '')
  if (h.length === 3) h = h.split('').map(c => c + c).join('')
  const r = parseInt(h.substring(0, 2), 16)
  const g = parseInt(h.substring(2, 4), 16)
  const b = parseInt(h.substring(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

// ── Org Context (super admin only) ───────────────────────────────────────────
// Lets the super admin "enter" any org's context and see their data.
// The stored orgId is sent as X-Org-Override on every API request; deps.py
// reads this header and safely overrides the user's organization_id for
// that request only (via db.expunge + in-memory mutation, no DB write).

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
