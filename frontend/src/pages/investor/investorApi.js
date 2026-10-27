// PRIVATE BUYER DEAL ROOM - the investor's own API client.
//
// Deliberately separate from api/client.js: an investor login is fenced on the
// server to /investor/* (app/deps.py) and must never share a token slot, a
// workspace header or a redirect with a staff session in the same browser.
import { API_BASE } from '../../api/client'

const KEY = 'af_investor_token'

export function investorToken() {
  try { return localStorage.getItem(KEY) } catch { return null }
}
export function setInvestorToken(t) {
  try { if (t) localStorage.setItem(KEY, t); else localStorage.removeItem(KEY) } catch { /* private window */ }
}

function toLogin() {
  setInvestorToken(null)
  const next = encodeURIComponent(window.location.pathname + window.location.search)
  if (!window.location.pathname.startsWith('/investors/login')) window.location.assign(`/investors/login?next=${next}`)
}

async function call(path, { method = 'GET', body, form, auth = true, blob = false } = {}) {
  const headers = {}
  const t = investorToken()
  if (auth && t) headers.Authorization = `Bearer ${t}`
  let payload
  if (form) payload = form
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body) }
  let res
  try {
    res = await fetch(`${API_BASE}${path}`, { method, headers, body: payload })
  } catch {
    const e = new Error('We could not reach the Deal Room. Check your connection and try again.')
    e.status = 0
    throw e
  }
  if (res.status === 401 && auth) { toLogin(); const e = new Error('Please sign in again.'); e.status = 401; throw e }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    const d = data.detail
    const e = new Error(typeof d === 'string' ? d : Array.isArray(d) ? (d[0]?.msg || 'Please check the form.') : 'Something went wrong.')
    e.status = res.status
    throw e
  }
  if (blob) return res.blob()
  return res.status === 204 ? null : res.json()
}

export const inv = {
  get: (p) => call(p),
  post: (p, body) => call(p, { method: 'POST', body: body ?? {} }),
  put: (p, body) => call(p, { method: 'PUT', body }),
  patch: (p, body) => call(p, { method: 'PATCH', body }),
  upload: (p, form) => call(p, { method: 'POST', form }),
  blob: (p) => call(p, { blob: true }),
  pub: {
    get: (p) => call(p, { auth: false }),
    post: (p, body) => call(p, { method: 'POST', body, auth: false }),
  },
}

// Photos and documents need the Authorization header, which an <img src> cannot
// send - fetch the bytes and hand back an object URL (caller revokes it).
export async function objectUrl(path) {
  const b = await inv.blob(path)
  return URL.createObjectURL(b)
}

export const usd = (v) => (v === null || v === undefined || v === '' ? '—'
  : '$' + Math.round(Number(v)).toLocaleString('en-US'))
export const words = (s) => (s || '').toString().replace(/_/g, ' ')
export const cap = (s) => { const w = words(s); return w ? w[0].toUpperCase() + w.slice(1) : '' }
export const when = (iso) => (iso ? new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric',
  hour: 'numeric', minute: '2-digit' }) : '')
export const fileSize = (n) => (n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB')
