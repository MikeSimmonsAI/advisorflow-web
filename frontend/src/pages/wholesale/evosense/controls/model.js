/* Providers & Controls — the display model.
 *
 * Pure functions over the server's own payloads (/providers, /sources,
 * /capabilities, /controls). Nothing here invents a state: every label is
 * derived from a field the server sent, and when the server did not say,
 * the label says "not yet tested" / "never called" rather than guessing.
 *
 * The dimensions are kept apart on purpose:
 *   KIND           what the provider is (live connector, manual, file import,
 *                  sandbox, paid vendor not purchased)
 *   CONFIGURATION  does the platform have what it needs to call it
 *   ENABLED        is it switched on for THIS workspace
 *   LATEST RESULT  what the most recent attempt did - a newer success beats
 *                  an older failure and vice versa; an older failure is kept
 *                  visible as "recovered", never hidden
 *   SCOPE          where it covers (county / city only / nationwide / any)
 *   COST           free / paid / simulated
 */

export const DAY_MS = 24 * 3600 * 1000
/** A failure that recovered within this window is still listed as a recent issue. */
export const RECENT_FAILURE_DAYS = 7

const ts = (v) => {
  if (!v) return null
  const t = Date.parse(v)
  return Number.isFinite(t) ? t : null
}

export const isPaid = (p) => !!p && (p.connector_kind === 'interface_only' || (p.connector_kind === 'real' && /^paid/i.test(p.cost || '')))
/** EVALUATION ONLY vendors: callable by the provider-evaluation harness alone;
 *  the engine never routes to them. The server says so in `state` (when on) or
 *  at the start of `why` (always). */
export const isEvaluationOnly = (p) => !!p && (p.state === 'EVALUATION ONLY' || /^EVALUATION ONLY/i.test(p.why || ''))
export const isManual = (p) => !!p && (p.connector_kind === 'manual' || p.connector_kind === 'import')

/** Shorter display name: drops a trailing "(...)" qualifier. The full label stays in the drawer. */
export function shortLabel(label) {
  const s = String(label || '').replace(/\s*\((?:[^()]*)\)\s*$/, '').trim()
  return s || String(label || '')
}

export const KIND_LABEL = {
  real: 'Connector', sandbox: 'Sandbox', manual: 'Manual entry', import: 'File import',
  interface_only: 'Paid vendor', not_built: 'Not built',
}

/** Kind in words: a free public-record connector, a paid API, a vendor not
 *  purchased, manual entry, a file import or sandbox. */
export function kindLabel(p) {
  if (!p) return ''
  if (p.connector_kind === 'real') return isPaid(p) ? 'Paid API' : 'Public-record connector'
  if (p.connector_kind === 'interface_only') return 'Paid vendor'
  return KIND_LABEL[p.connector_kind] || p.connector_kind
}

/** Configuration dimension. */
export function configOf(p) {
  if (!p) return { key: 'unknown', label: 'Unknown', tone: 'quiet' }
  if (isManual(p)) return { key: 'na', label: 'Nothing to configure', short: 'Not needed', tone: 'quiet' }
  if (p.connector_kind === 'interface_only') return { key: 'not_purchased', label: 'Not purchased', tone: 'quiet' }
  if (p.state === 'NOT CONFIGURED' || p.configured === false) {
    const n = p.missing_env_count || 0
    return { key: 'unconfigured', label: n ? `Not configured · ${n} credential${n === 1 ? '' : 's'} missing` : 'Not configured',
             short: 'Needs credentials', tone: 'quiet' }
  }
  return { key: 'configured', label: 'Configured', tone: 'good' }
}

/** Enabled dimension (this workspace's switch). */
export function enabledOf(p) {
  if (!p) return { key: 'unknown', label: 'Unknown', tone: 'quiet' }
  if (isManual(p)) return { key: 'always', label: 'Always available', short: 'Always', tone: 'quiet' }
  if (p.connector_kind === 'interface_only') return { key: 'na', label: 'Not available', tone: 'quiet' }
  return p.enabled ? { key: 'on', label: 'On', tone: 'good' } : { key: 'off', label: 'Off', tone: 'quiet' }
}

/** Latest result: what the most recent attempt did, with its time and evidence.
 *  An older failure that a newer success superseded is returned as `earlier`. */
export function latestOf(p, now = Date.now()) {
  if (!p) return { key: 'unknown', label: 'Unknown', tone: 'quiet' }
  if (isManual(p)) return { key: 'manual', label: 'Nothing is called', tone: 'quiet', detail: 'Fed by a person or a file' }
  if (p.connector_kind === 'interface_only') return { key: 'na', label: 'Never called', tone: 'quiet', detail: 'Not purchased' }
  const ok = ts(p.last_success_at)
  const bad = ts(p.last_failure_at)
  if (p.state === 'BLOCKED') {
    return { key: 'blocked', label: 'Blocked by the source', tone: 'danger', at: bad, detail: p.last_failure_reason || p.why }
  }
  if (p.state === 'RATE LIMITED') {
    return { key: 'rate_limited', label: 'Rate limited', tone: 'attention', at: bad, detail: p.why }
  }
  if (bad && (!ok || bad > ok)) {
    return { key: 'failed', label: 'Latest attempt failed', tone: 'danger', at: bad, detail: p.last_failure_reason || 'error',
             streak: p.consecutive_failures || 0 }
  }
  if (ok) {
    const earlier = bad ? { at: bad, detail: p.last_failure_reason || 'error', recent: now - bad < RECENT_FAILURE_DAYS * DAY_MS } : null
    return { key: 'ok', label: 'Latest attempt succeeded', tone: 'good', at: ok, earlier, records: p.last_record_count }
  }
  if (p.state === 'NOT CONFIGURED') return { key: 'na', label: 'Never called', tone: 'quiet', detail: 'Not configured' }
  if (!p.enabled) return { key: 'never', label: 'Never called', tone: 'quiet', detail: 'Switched off' }
  return { key: 'untested', label: 'Not yet tested', tone: 'info', detail: 'Enabled; no call has been made yet' }
}

/** One group per provider, for filtering and ordering. Precedence: a real
 *  problem first, then what works, then what is off. */
export const GROUPS = [
  ['attention', 'Needs attention'],
  ['live', 'Live'],
  ['untested', 'Not yet tested'],
  ['evaluation', 'Evaluation only'],
  ['manual', 'Manual / import'],
  ['disabled', 'Switched off'],
  ['unconfigured', 'Not configured / not purchased'],
  ['sandbox', 'Sandbox'],
]
export const GROUP_LABEL = Object.fromEntries(GROUPS)
const GROUP_ORDER = Object.fromEntries(GROUPS.map(([k], i) => [k, i]))

export function groupOf(p, now = Date.now()) {
  if (!p) return 'unconfigured'
  if (p.connector_kind === 'sandbox') return 'sandbox'
  if (isManual(p)) return 'manual'
  if (p.connector_kind === 'interface_only' || p.state === 'NOT CONFIGURED' || p.state === 'NOT PURCHASED') return 'unconfigured'
  if (isEvaluationOnly(p)) return 'evaluation'
  if (!p.enabled) return 'disabled'
  if (['FAILED', 'BLOCKED', 'DEGRADED', 'RATE LIMITED'].includes(p.state) || latestOf(p, now).key === 'failed') return 'attention'
  if (p.operational) return 'live'
  return 'untested'
}

/** Recovered-but-recent failures: worth a look, not a current outage. */
export function recentlyRecovered(p, now = Date.now()) {
  const l = latestOf(p, now)
  return !!(l.key === 'ok' && l.earlier && l.earlier.recent && p.enabled)
}

export function sortProviders(list, now = Date.now()) {
  return [...list].sort((a, b) => (GROUP_ORDER[groupOf(a, now)] - GROUP_ORDER[groupOf(b, now)])
    || String(a.label).localeCompare(String(b.label)))
}

/** Scope dimension in words. A city connector is never called county-wide. */
export function scopeOf(p) {
  const a = (p && p.coverage_area) || {}
  const counties = a.counties || []
  if (a.scope === 'city') return { key: 'city', label: `${a.scope_name || 'City'} only`, counties }
  if (a.scope === 'county') {
    return { key: 'county', label: counties.length === 1 ? `${counties[0]} County` : `${counties.length} counties`, counties }
  }
  if (a.scope === 'state') return { key: 'state', label: `${a.scope_name || 'State'} statewide`, counties }
  if (a.scope === 'national') return { key: 'national', label: 'Nationwide', counties }
  if (a.scope === 'sandbox') return { key: 'sandbox', label: 'Sandbox records only', counties }
  if (a.scope === 'any') return { key: 'any', label: 'Any property', counties }
  return { key: 'unknown', label: '—', counties }
}

/** All 254 Texas counties (Texas Association of Counties list). */
export const TEXAS_COUNTIES = Object.freeze(['Anderson', 'Andrews', 'Angelina', 'Aransas', 'Archer', 'Armstrong', 'Atascosa', 'Austin', 'Bailey', 'Bandera', 'Bastrop', 'Baylor', 'Bee', 'Bell', 'Bexar', 'Blanco', 'Borden', 'Bosque', 'Bowie', 'Brazoria', 'Brazos', 'Brewster', 'Briscoe', 'Brooks', 'Brown', 'Burleson', 'Burnet', 'Caldwell', 'Calhoun', 'Callahan', 'Cameron', 'Camp', 'Carson', 'Cass', 'Castro', 'Chambers', 'Cherokee', 'Childress', 'Clay', 'Cochran', 'Coke', 'Coleman', 'Collin', 'Collingsworth', 'Colorado', 'Comal', 'Comanche', 'Concho', 'Cooke', 'Coryell', 'Cottle', 'Crane', 'Crockett', 'Crosby', 'Culberson', 'Dallam', 'Dallas', 'Dawson', 'Deaf Smith', 'Delta', 'Denton', 'DeWitt', 'Dickens', 'Dimmit', 'Donley', 'Duval', 'Eastland', 'Ector', 'Edwards', 'Ellis', 'El Paso', 'Erath', 'Falls', 'Fannin', 'Fayette', 'Fisher', 'Floyd', 'Foard', 'Fort Bend', 'Franklin', 'Freestone', 'Frio', 'Gaines', 'Galveston', 'Garza', 'Gillespie', 'Glasscock', 'Goliad', 'Gonzales', 'Gray', 'Grayson', 'Gregg', 'Grimes', 'Guadalupe', 'Hale', 'Hall', 'Hamilton', 'Hansford', 'Hardeman', 'Hardin', 'Harris', 'Harrison', 'Hartley', 'Haskell', 'Hays', 'Hemphill', 'Henderson', 'Hidalgo', 'Hill', 'Hockley', 'Hood', 'Hopkins', 'Houston', 'Howard', 'Hudspeth', 'Hunt', 'Hutchinson', 'Irion', 'Jack', 'Jackson', 'Jasper', 'Jeff Davis', 'Jefferson', 'Jim Hogg', 'Jim Wells', 'Johnson', 'Jones', 'Karnes', 'Kaufman', 'Kendall', 'Kenedy', 'Kent', 'Kerr', 'Kimble', 'King', 'Kinney', 'Kleberg', 'Knox', 'Lamar', 'Lamb', 'Lampasas', 'La Salle', 'Lavaca', 'Lee', 'Leon', 'Liberty', 'Limestone', 'Lipscomb', 'Live Oak', 'Llano', 'Loving', 'Lubbock', 'Lynn', 'McCulloch', 'McLennan', 'McMullen', 'Madison', 'Marion', 'Martin', 'Mason', 'Matagorda', 'Maverick', 'Medina', 'Menard', 'Midland', 'Milam', 'Mills', 'Mitchell', 'Montague', 'Montgomery', 'Moore', 'Morris', 'Motley', 'Nacogdoches', 'Navarro', 'Newton', 'Nolan', 'Nueces', 'Ochiltree', 'Oldham', 'Orange', 'Palo Pinto', 'Panola', 'Parker', 'Parmer', 'Pecos', 'Polk', 'Potter', 'Presidio', 'Rains', 'Randall', 'Reagan', 'Real', 'Red River', 'Reeves', 'Refugio', 'Roberts', 'Robertson', 'Rockwall', 'Runnels', 'Rusk', 'Sabine', 'San Augustine', 'San Jacinto', 'San Patricio', 'San Saba', 'Schleicher', 'Scurry', 'Shackelford', 'Shelby', 'Sherman', 'Smith', 'Somervell', 'Starr', 'Stephens', 'Sterling', 'Stonewall', 'Sutton', 'Swisher', 'Tarrant', 'Taylor', 'Terrell', 'Terry', 'Throckmorton', 'Titus', 'Tom Green', 'Travis', 'Trinity', 'Tyler', 'Upshur', 'Upton', 'Uvalde', 'Val Verde', 'Van Zandt', 'Victoria', 'Walker', 'Waller', 'Ward', 'Washington', 'Webb', 'Wharton', 'Wheeler', 'Wichita', 'Wilbarger', 'Willacy', 'Williamson', 'Wilson', 'Winkler', 'Wise', 'Wood', 'Yoakum', 'Young', 'Zapata', 'Zavala'])

const isTexasWide = (p) => p.connector_kind !== 'sandbox' && scopeOf(p).key === 'state'
  && String(((p.coverage_area || {}).scope_name) || '').toLowerCase() === 'texas'

/** The counties this workspace's sources talk about, in a stable order
 *  (the widest multi-county source first, then any others as they appear).
 *  With a Texas-wide source, every Texas county is in the universe: the
 *  counties with their own sources first, then the rest A-Z. */
export function countyUniverse(providers) {
  const real = (providers || []).filter((p) => p.connector_kind !== 'sandbox')
  const n = (p) => ((p.coverage_area || {}).counties || []).length
  const ordered = [...real].sort((a, b) => n(b) - n(a))
  const out = []
  ordered.forEach((p) => ((p.coverage_area || {}).counties || []).forEach((c) => { if (c && !out.includes(c)) out.push(c) }))
  if (real.some(isTexasWide)) TEXAS_COUNTIES.forEach((c) => { if (!out.includes(c)) out.push(c) })
  return out
}

// County cell ranks (lower = better).
const CELL = {
  live: [0, 'Live', 'good'],
  live_city: [1, 'Live · city only', 'good'],
  attention: [2, 'Needs attention', 'danger'],
  untested: [3, 'On · not yet tested', 'info'],
  manual: [4, 'Manual / import', 'quiet'],
  off: [5, 'Connector switched off', 'quiet'],
  unconfigured: [6, 'Not configured', 'quiet'],
  none: [9, 'No source', 'none'],
}
export const CELL_LABEL = Object.fromEntries(Object.entries(CELL).map(([k, v]) => [k, v[1]]))
export const CELL_TONE = Object.fromEntries(Object.entries(CELL).map(([k, v]) => [k, v[2]]))

function cellFor(p, now) {
  const g = groupOf(p, now)
  if (g === 'live') return scopeOf(p).key === 'city' ? 'live_city' : 'live'
  if (g === 'attention') return 'attention'
  if (g === 'untested' || g === 'evaluation') return 'untested'
  if (g === 'manual') return 'manual'
  if (g === 'disabled') return 'off'
  if (g === 'unconfigured') return 'unconfigured'
  return null
}

/** Per county, the best source for one capability and every source behind it.
 *  Sandbox never counts as coverage. A manual entry that works anywhere is
 *  reported once (anyManual), not as a "source" in every county. */
export function coverageFor(capability, providers, counties, now = Date.now()) {
  const serving = (providers || []).filter((p) => (p.capabilities || []).includes(capability) && p.connector_kind !== 'sandbox')
  const anyManual = serving.filter((p) => isManual(p) && scopeOf(p).key === 'any')
  const rows = (counties || []).map((county) => {
    const here = serving.filter((p) => {
      const s = scopeOf(p)
      if (s.key === 'any') return false
      if (s.key === 'national') return true
      if (s.key === 'state') return isTexasWide(p) && TEXAS_COUNTIES.includes(county)
      return s.counties.includes(county)
    }).map((p) => ({ p, cell: cellFor(p, now) })).filter((x) => x.cell)
    here.sort((a, b) => CELL[a.cell][0] - CELL[b.cell][0])
    const best = here[0] || null
    return { county, cell: best ? best.cell : 'none', best: best ? best.p : null, sources: here }
  })
  const live = rows.filter((r) => r.cell === 'live').length
  const cityOnly = rows.filter((r) => r.cell === 'live_city').length
  return { rows, live, cityOnly, total: (counties || []).length, anyManual, serving }
}

/** Card status for one engine capability (from /providers capabilities + groups). */
export function capabilityStatus(cap, providers, now = Date.now()) {
  const serving = (providers || []).filter((p) => (p.capabilities || []).includes(cap.capability))
  const groups = serving.map((p) => groupOf(p, now))
  const issues = serving.filter((p) => groupOf(p, now) === 'attention')
  if (groups.includes('live')) return { key: 'live', label: 'Live', tone: 'good', issues }
  if (issues.length) return { key: 'attention', label: 'Needs attention', tone: 'danger', issues }
  if (groups.includes('untested')) return { key: 'untested', label: 'Not yet tested', tone: 'info', issues }
  if (cap.kind === 'manual' || cap.kind === 'import' || groups.includes('manual')) return { key: 'manual', label: 'Manual / import', tone: 'quiet', issues }
  if (cap.kind === 'sandbox') return { key: 'sandbox', label: 'Sandbox only', tone: 'attention', issues }
  if (groups.includes('disabled')) return { key: 'disabled', label: 'Switched off', tone: 'quiet', issues }
  return { key: 'none', label: 'Not available', tone: 'quiet', issues }
}

/** The provider an operator would use first for a capability. */
export function bestProvider(cap, providers, now = Date.now()) {
  const serving = sortProviders((providers || []).filter((p) => (p.capabilities || []).includes(cap.capability)
    && p.connector_kind !== 'sandbox'), now)
  const reach = (p) => (scopeOf(p).key === 'national' ? 999 : scopeOf(p).key === 'state' ? 254
    : scopeOf(p).key === 'city' ? 0.5 : scopeOf(p).counties.length)
  const widest = (g) => serving.filter((p) => groupOf(p, now) === g).sort((a, b) => reach(b) - reach(a))[0]
  return widest('live')
    || serving.find((p) => groupOf(p, now) === 'attention')
    || serving.find((p) => groupOf(p, now) === 'untested')
    || serving.find((p) => isManual(p) && scopeOf(p).key !== 'any')
    || serving.find((p) => isManual(p))
    || null
}

/** Merge /sources rows (jurisdiction, access, refresh...) into /providers rows. */
export function mergeSources(providers, sources) {
  const byKey = Object.fromEntries(((sources || {}).sources || []).map((s) => [s.key, s]))
  return (providers || []).map((p) => (byKey[p.key] ? { ...byKey[p.key], ...p, source: byKey[p.key] } : p))
}

/** What needs a person: current problems first, then recent recovered failures. */
export function attentionList(providers, now = Date.now()) {
  const current = sortProviders((providers || []).filter((p) => groupOf(p, now) === 'attention'), now)
  const recovered = (providers || []).filter((p) => recentlyRecovered(p, now) && groupOf(p, now) !== 'attention')
  return { current, recovered }
}

/** Plain-language one-liners for each engine capability (UI copy, not data). */
export const CAP_PURPOSE = {
  PROPERTY_SEARCH: 'Find properties and their parcel records',
  PARCEL: 'Parcel boundaries and identifiers',
  ASSESSOR: 'Appraised values and building facts',
  OWNERSHIP: 'Owner of record and mailing address',
  TAX: 'Tax delinquency, tax suits and tax sales',
  FORECLOSURE: 'Foreclosure notices and filings',
  PROBATE: 'Probate filings and inherited property',
  CODE_VIOLATION: 'Open code cases and complaints',
  VACANCY: 'Signs a property is vacant',
  LISTING: 'Expired and failed listings',
  VALUATION: 'Value estimates',
  COMPS: 'Comparable sales',
  CONTACT_ENRICHMENT: 'Owner phone and email (skip trace)',
  PHONE_VALIDATION: 'Phone line type and validity',
  EMAIL_VALIDATION: 'Email deliverability',
  ENTITY_RESOLUTION: 'People behind an LLC owner',
  GEOCODING: 'Address, ZIP and county checks',
}

export const CAP_ICON = {
  PROPERTY_SEARCH: 'search', PARCEL: 'map', ASSESSOR: 'building', OWNERSHIP: 'key', TAX: 'dollar',
  FORECLOSURE: 'gavel', PROBATE: 'doc', CODE_VIOLATION: 'alert', VACANCY: 'home', LISTING: 'list',
  VALUATION: 'chart', COMPS: 'layers', CONTACT_ENRICHMENT: 'user', PHONE_VALIDATION: 'phone',
  EMAIL_VALIDATION: 'mail', ENTITY_RESOLUTION: 'users', GEOCODING: 'pin',
}

export function capName(key) {
  const special = { LISTING: 'Listing (MLS)', CODE_VIOLATION: 'Code violations', CONTACT_ENRICHMENT: 'Contact enrichment' }
  if (special[key]) return special[key]
  const s = String(key || '').toLowerCase().replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** Text a search box matches against for one provider. */
export function providerText(p) {
  const s = scopeOf(p)
  return [p.label, p.key, p.coverage, p.jurisdiction, p.source_type, s.label, ...(p.capabilities || []).map(capName),
          ...(s.counties || []), KIND_LABEL[p.connector_kind], p.state, p.why].filter(Boolean).join(' ').toLowerCase()
}

export function matchesProvider(p, { q, county, cap, status } = {}, now = Date.now()) {
  if (q && !providerText(p).includes(q.trim().toLowerCase())) return false
  if (cap && !(p.capabilities || []).includes(cap)) return false
  if (county) {
    const s = scopeOf(p)
    if (!(s.key === 'national' || s.key === 'any' || s.counties.includes(county))) return false
  }
  if (status && groupOf(p, now) !== status) return false
  return true
}

/** Paid exposure, from the server's own channel truth and provider rows. */
export function paidExposure(providers, controls) {
  const paid = (providers || []).filter(isPaid)
  const on = paid.filter((p) => p.connector_kind === 'real' && p.enabled)
  const usable = on.filter((p) => p.operational)
  const ch = ((controls || {}).channels || {}).paused_paid_data || null
  return { paid, on, usable, channel: ch, paused: !!(controls && controls.paused_paid_data) }
}
