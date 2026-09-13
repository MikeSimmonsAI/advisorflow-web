/**
 * WHAT THIS CUSTOMER'S BUSINESS CALLS THINGS.
 *
 * THE DEFECT THIS CLOSES. Four customer screens each kept their own industry
 * vocabulary in a literal at the top of the file, and every one of them fell
 * back to FUNERAL when it did not recognise the organization:
 *
 *   Overview.jsx   `const industry = branding?.industry || 'funeral'`
 *                  — and `branding` never carried an `industry` key at all, so
 *                    the fallback was not a fallback. It was the only path.
 *                    Every tenant on this platform, in every industry, was
 *                    shown ARRANGEMENTS, ARRANGEMENT RATE and RECORDED VISITS.
 *   Leads.jsx      TIER_OPTIONS / TIER_FILTER_OPTIONS hard-coded Pre-Need and
 *                  At-Need as though they were universal.
 *   CRM.jsx        FALLBACK_STAGES was the funeral pipeline, rendered before
 *                  and instead of whatever the server would have said.
 *   EmailQueue.jsx placeholder copy naming pre-need planning.
 *
 * An energy customer opened a product full of a funeral home's language. None
 * of it was their configuration; it was another vertical's defaults wearing
 * the word "default".
 *
 * THE SERVER ALREADY KNEW THE ANSWER. `app/services/industry_templates.py` is
 * the platform's one industry registry — tiers, CRM stages, appointment types
 * and vocabulary per business type, with a GENERIC fallback and explicitly no
 * funeral fallback — and `GET /org-settings/` already resolves it for the
 * caller's own organization. Nothing here decides anything: this module
 * fetches that answer, caches it per workspace, and hands it to the screens.
 *
 * THE LOCAL DEFAULTS BELOW ARE NEUTRAL, AND THAT IS THE ENTIRE POINT. They are
 * what renders in the moment before the fetch returns, or if it fails. A
 * vertical's words must never appear there, because a first-paint default is
 * indistinguishable from a decision to the person reading it.
 *
 * DEATHCARE IS NOT BEING REMOVED. A funeral or cemetery organization
 * configured as one still receives Pre-Need, At-Need, Arrangements and
 * Aftercare — from its own configuration, which is where they were always
 * supposed to come from.
 */
import { useEffect, useState } from 'react'
import { api, getWorkspaceContext } from './api/client'

/* ── neutral starting point ───────────────────────────────────────────────── */

export const NEUTRAL_TIERS = [
  { value: 'new_lead', label: 'New Lead' },
  { value: 'contacted', label: 'Contacted' },
  { value: 'quoted', label: 'Quoted' },
  { value: 'won', label: 'Won' },
  { value: 'email_only', label: 'Email Only' },
]

export const NEUTRAL_STAGES = [
  { key: 'new_lead', label: 'New Lead', color: '#64748b' },
  { key: 'contacted', label: 'Contacted', color: '#6366f1' },
  { key: 'qualified', label: 'Qualified', color: '#f59e0b' },
  { key: 'proposal', label: 'Proposal', color: '#f97316' },
  { key: 'won', label: 'Won', color: '#10b981' },
  { key: 'lost', label: 'Lost', color: '#374151' },
]

export const NEUTRAL_VOCABULARY = {
  lead: 'lead', leads: 'leads', appointment: 'appointment',
  appointments: 'Appointments', customer: 'customer', customers: 'customers',
}

export const NEUTRAL = {
  orgName: '',
  industry: 'generic',
  industryLabel: 'General service business',
  matched: false,
  tiers: NEUTRAL_TIERS,
  crmStages: NEUTRAL_STAGES,
  vocabulary: NEUTRAL_VOCABULARY,
  loaded: false,
}

/* ── derivation ───────────────────────────────────────────────────────────── */

function singular(plural) {
  const word = String(plural || '').trim()
  if (!word) return 'appointment'
  if (/ies$/i.test(word)) return word.slice(0, -3) + 'y'
  if (/ses$/i.test(word)) return word.slice(0, -2)
  if (/s$/i.test(word)) return word.slice(0, -1)
  return word
}

function lower(word) {
  // Only the first letter: "Rate Reviews" must not become "rate reviews" in
  // the middle of a sentence if the business capitalises its own term, but a
  // plain "Appointments" should read as "appointments this week".
  const text = String(word || '')
  return text.charAt(0).toLowerCase() + text.slice(1)
}

/**
 * The Overview KPI labels, all seven derived from ONE noun.
 *
 * They used to be seven hand-written strings per industry in a map in
 * Overview.jsx — seventy strings to keep consistent, which is why the map had
 * industries the backend registry has never heard of (`solar`, `sales`) and
 * lacked ones it has (`energy`, `dental`, `generic`). One noun per business
 * type lives in the registry; the rest is grammar and belongs here.
 */
export function metricLabels(appointmentsPlural) {
  const p = String(appointmentsPlural || NEUTRAL_VOCABULARY.appointments).trim()
    || NEUTRAL_VOCABULARY.appointments
  const s = singular(p)
  const pl = lower(p)
  return {
    appointments: p,
    bookingRate: `${s} rate`,
    bookedSub: `Booked ${pl}`,
    projectedBookings: `Projected ${pl}`,
    confirmLabel: `${pl} confirmed`,
    weeklyLabel: `${pl} this week`,
    recordedVisits: `Completed ${pl}`,
  }
}

/* ── cache ────────────────────────────────────────────────────────────────── */

// KEYED BY WORKSPACE. A person who holds two customer workspaces switches
// between them in one session, and serving the first one's vocabulary inside
// the second is a tenant leak of exactly the kind this module exists to stop.
const CACHE_PREFIX = 'af_terminology:'

function cacheKey() {
  return CACHE_PREFIX + (getWorkspaceContext() || 'default')
}

let memo = { key: null, value: null }

function readCache() {
  const key = cacheKey()
  if (memo.key === key && memo.value) return memo.value
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    memo = { key, value: parsed }
    return parsed
  } catch (e) {
    return null
  }
}

function writeCache(value) {
  const key = cacheKey()
  memo = { key, value }
  try { localStorage.setItem(key, JSON.stringify(value)) } catch (e) { /* private mode */ }
}

export function clearTerminology() {
  memo = { key: null, value: null }
  try {
    const drop = []
    for (let i = 0; i < localStorage.length; i += 1) {
      const k = localStorage.key(i)
      if (k && k.startsWith(CACHE_PREFIX)) drop.push(k)
    }
    drop.forEach(k => localStorage.removeItem(k))
  } catch (e) { /* nothing to clear */ }
}

/* ── fetch ────────────────────────────────────────────────────────────────── */

function shape(data) {
  const vocabulary = { ...NEUTRAL_VOCABULARY, ...(data.vocabulary || {}) }
  const tiers = Array.isArray(data.tier_config) && data.tier_config.length
    ? data.tier_config.map(t => ({
        value: t.value, label: t.label, color: t.color,
        description: t.description,
      }))
    : NEUTRAL_TIERS
  const crmStages = Array.isArray(data.crm_stages) && data.crm_stages.length
    ? data.crm_stages
    : NEUTRAL_STAGES
  return {
    // THE BUSINESS'S OWN NAME, for copy that addresses its customers. Composed
    // email subjects used to name one real cemetery customer in every tenant's
    // outbound mail; a subject that cannot name this business omits the name
    // rather than borrowing somebody else's.
    orgName: data.brand_name || data.name || '',
    industry: data.industry || NEUTRAL.industry,
    industryLabel: data.industry_label || NEUTRAL.industryLabel,
    matched: Boolean(data.industry_matched),
    tiers,
    crmStages,
    vocabulary,
    loaded: true,
  }
}

let inflight = null

/**
 * This workspace's vocabulary. One request per workspace per session.
 *
 * A failure resolves to the NEUTRAL set rather than rejecting: a screen that
 * cannot reach settings should render in plain English, not break and not
 * guess a vertical.
 */
export async function fetchTerminology({ force = false } = {}) {
  if (!force) {
    const cached = readCache()
    if (cached) return cached
  }
  if (inflight) return inflight
  inflight = (async () => {
    try {
      const data = await api.get('/org-settings/', { skipRedirect: true })
      const value = shape(data || {})
      writeCache(value)
      return value
    } catch (e) {
      return NEUTRAL
    } finally {
      inflight = null
    }
  })()
  return inflight
}

export function getTerminology() {
  return readCache() || NEUTRAL
}

/**
 * React binding. Renders immediately with whatever is cached (or NEUTRAL), then
 * again once the server answers — so no screen blocks on this and none of them
 * flashes another industry's words on the way.
 */
export function useTerminology() {
  const [term, setTerm] = useState(() => getTerminology())
  useEffect(() => {
    let alive = true
    fetchTerminology().then(value => { if (alive) setTerm(value) })
    return () => { alive = false }
  }, [])
  return term
}
