// VOLT Marketing applicant API client (#535).
//
// Separate from api/client.js on purpose: an applicant login (role volt_rep) is
// fenced on the server to /volt/rep/* and must never share a token slot, a
// workspace header or a redirect with a staff session in the same browser.
import { API_BASE } from '../../api/client'

export const SLUG = 'volt-marketing'
const KEY = 'af_volt_token'

export function voltToken() {
  try { return localStorage.getItem(KEY) } catch { return null }
}
export function setVoltToken(t) {
  try { if (t) localStorage.setItem(KEY, t); else localStorage.removeItem(KEY) } catch { /* private window */ }
}

export class VoltError extends Error {
  constructor(message, status, detail) { super(message); this.status = status; this.detail = detail }
}

async function call(path, { method = 'GET', body, form, auth = true, blob = false } = {}) {
  const headers = {}
  const t = voltToken()
  if (auth && t) headers.Authorization = `Bearer ${t}`
  let payload
  if (form) payload = form
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body) }
  let res
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      res = await fetch(`${API_BASE}${path}`, { method, headers, body: payload })
      break
    } catch {
      if (attempt === 2) throw new VoltError('We could not reach VOLT. Check your connection and try again.', 0)
      await new Promise((r) => setTimeout(r, 2500))
    }
  }
  if (res.status === 401 && auth) { setVoltToken(null) }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    const d = data.detail
    const msg = typeof d === 'string' ? d : (d && d.message) || (Array.isArray(d) ? 'Please check the form.' : 'Something went wrong. Please try again.')
    throw new VoltError(msg, res.status, typeof d === 'object' ? d : null)
  }
  if (blob) return res.blob()
  return res.status === 204 ? null : res.json()
}

const pub = (p) => `/volt/public/${SLUG}${p}`
const rep = (p) => `/volt/rep/${SLUG}${p}`

export const volt = {
  portal: () => call(pub(''), { auth: false }),
  searchPublic: (q, test) => call(pub(`/recruiters?q=${encodeURIComponent(q)}${test ? '&test=1' : ''}`), { auth: false }),
  invite: (ref, project) => call(pub(`/invite?ref=${encodeURIComponent(ref || '')}&project=${encodeURIComponent(project || '')}`), { auth: false }),
  register: (body) => call(pub('/register'), { method: 'POST', body, auth: false }),
  login: (body) => call(pub('/login'), { method: 'POST', body, auth: false }),
  me: () => call(rep('/me')),
  searchSignedIn: (q) => call(rep(`/recruiters?q=${encodeURIComponent(q)}`)),
  createProfile: (body) => call(rep('/profile'), { method: 'POST', body }),
  updateProfile: (body) => call(rep('/profile'), { method: 'PATCH', body }),
  startApplication: (body) => call(rep('/applications'), { method: 'POST', body }),
  application: (id) => call(rep(`/applications/${id}`)),
  saveAnswers: (id, answers) => call(rep(`/applications/${id}/answers`), { method: 'PUT', body: { answers } }),
  submit: (id) => call(rep(`/applications/${id}/submit`), { method: 'POST', body: {} }),
  upload: (form) => call(rep('/documents'), { method: 'POST', form }),
  file: (id) => call(rep(`/documents/${id}/file`), { blob: true }),
  leaderboard: (projectId) => call(rep(`/leaderboard?project_id=${encodeURIComponent(projectId || '')}`)),
  logout: () => call('/auth/logout', { method: 'POST', body: {} }).catch(() => null),
}

export function uploadKey() {
  const a = new Uint8Array(12)
  ;(window.crypto || window.msCrypto).getRandomValues(a)
  return Array.from(a, (b) => b.toString(16).padStart(2, '0')).join('')
}

export const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '')

/** "Tue, Dec 1, 6:30 PM CST" in the viewer's own zone, plus the session's zone if different. */
export function sessionTime(iso, tz) {
  if (!iso) return ''
  const d = new Date(iso)
  const mine = d.toLocaleString('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' })
  let theirs = ''
  try {
    const local = Intl.DateTimeFormat().resolvedOptions().timeZone
    if (tz && tz !== local) {
      theirs = d.toLocaleString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: tz, timeZoneName: 'short' })
    }
  } catch { /* old browser */ }
  return theirs ? `${mine} (${theirs})` : mine
}
