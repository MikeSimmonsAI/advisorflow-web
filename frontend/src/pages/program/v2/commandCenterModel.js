/**
 * PROGRAM ACCESS V2 (Premium Command Center) - presentation rules only.
 *
 * Everything here works on cards that programCards()/salesToolCards() already
 * built from the server's /auth/my-contexts answer for THIS login. Nothing in
 * this file grants, infers or widens access: it only decides which category a
 * card is filed under, which licensed photo it wears, which card is featured
 * and how the summary numbers are counted.
 *
 * Pure (no React, no DOM) so node tests can check every rule.
 */

/* ── Categories ──────────────────────────────────────────────────────────
 * A maintainable mapping from the card's program (set by programCards from
 * server fields) to a filter. A program not listed here is a client business.
 */
export const CATEGORIES = [
  { key: 'all', label: 'All Workspaces' },
  { key: 'operations', label: 'Operations' },
  { key: 'sales', label: 'Sales & Growth' },
  { key: 'clients', label: 'Clients' },
  { key: 'admin', label: 'Administration' },
]

const PROGRAM_CATEGORY = {
  acquisition_ops: 'operations',
  volt: 'operations',
  consumer_leads: 'operations',
  evosys_sales: 'sales',
  back_office: 'admin',
  executive: 'admin',
}
const SALES_TOOL_KEYS = new Set(['executive', 'sales', 'my_team', 'onboarding'])

export function categoryOf(item) {
  if (!item) return 'clients'
  if (item.type === 'deal_builder' && SALES_TOOL_KEYS.has(item.key)) return 'sales'
  return PROGRAM_CATEGORY[item.program] || 'clients'
}

/** Categories that actually have something to show for this login. */
export function availableCategories(items) {
  const have = new Set(items.map(categoryOf))
  return CATEGORIES.filter(c => c.key === 'all' || have.has(c.key))
}

/* ── Photography ─────────────────────────────────────────────────────────
 * Every photo is bundled with the app (no hotlinks) - provenance in
 * docs/redesign/program-access-v2/PHOTO_CREDITS.md. A photo is chosen from the
 * card's program and the industry the SERVER recorded, never from a client's
 * name, so a new client gets an appropriate photo by itself.
 */
const PROGRAM_PHOTO = {
  sci: 'sci',
  funeral: 'restland',
  energy: 'energy',
  roofing: 'roofing',
  wholesale: 'wholesale',
  jewelry: 'gold',
  insurance: 'insurance',
  insurance_retention: 'agency',
  cleaning: 'cleaning',
  acquisition: 'cleaning',
  acquisition_ops: 'acquisition',
  volt: 'volt',
  consumer_leads: 'consumer',
  evosys_sales: 'evosys-sales',
  back_office: 'backoffice',
  executive: 'executive',
}
const SALES_TOOL_PHOTO = { executive: 'exec-builder', sales: 'sales-builder', my_team: 'team', onboarding: 'onboarding' }
// The same vocabulary of industries the drawn artwork uses (industryVariant.js).
const VARIANT_PHOTO = {
  gold: 'gold', insurance: 'insurance', wholesale: 'realestate', energy: 'energy',
  cleaning: 'cleaning', funeral: 'restland', roofing: 'roofing',
}
const INDUSTRY_PHOTO = [
  [/land|farm|ranch|acre/i, 'land'],
  [/auto|vehicle|wrap|car\b|detail/i, 'wraps'],
]

export function photoKeyFor(item) {
  if (!item) return 'business'
  if (item.type === 'deal_builder') return SALES_TOOL_PHOTO[item.key] || 'backoffice'
  if (item.program === 'preview') {
    const hint = [item.industry, item.display, item.company_name].filter(Boolean).join(' ')
    for (const [re, key] of INDUSTRY_PHOTO) if (re.test(hint)) return key
    return VARIANT_PHOTO[item.variant] || 'business'
  }
  if (item.program === 'business') {
    const ind = String(item.industry || '')
    for (const [re, key] of INDUSTRY_PHOTO) if (re.test(ind)) return key
    return VARIANT_PHOTO[item.variant] || 'business'
  }
  return PROGRAM_PHOTO[item.program] || VARIANT_PHOTO[item.variant] || 'business'
}

/* ── Featured workspace ──────────────────────────────────────────────────
 * 1. the person's first pinned card that the server still lists,
 * 2. otherwise the first card in the server-ordered list (deterministic).
 * There is no "recently opened" preference in this system, so that step is
 * not invented. Previews are never featured: they are not workspaces.
 */
export function chooseFeatured(cards, pins, keyOf) {
  if (!cards || !cards.length) return null
  for (const k of pins || []) {
    const hit = cards.find(c => keyOf(c) === k)
    if (hit) return hit
  }
  return cards[0]
}

/* ── Summary numbers ─────────────────────────────────────────────────────
 * workspaces: every destination card this login may open (grid cards +
 *   "other authorized workspaces"). Previews are NOT counted here.
 * clients: distinct client organizations behind those cards. Internal
 *   EvoSys destinations (Back Office, Executive Suite, Deal Builders, My
 *   Team, onboarding, the Acquisition hub) are not clients. VOLT Marketing and
 *   its Consumer Leads business are one organization and count once.
 * previews: prospect demo cards (separate from workspaces, never clients).
 */
export function summaryCounts(cards, others, previews) {
  const orgs = new Set()
  for (const c of cards || []) {
    if (c.type === 'deal_builder' || c.type === 'back_office' || c.type === 'executive') continue
    if (c.type === 'acquisition_ops') {
      if (!c.hub && c.client_organization_id) orgs.add(c.client_organization_id)
      continue
    }
    if (c.type === 'volt_ops' || c.type === 'consumer_leads') {
      if (c.volt_organization_id) orgs.add(c.volt_organization_id)
      continue
    }
    if (c.organization_id) orgs.add(c.organization_id)
  }
  for (const w of others || []) if (w.organization_id) orgs.add(w.organization_id)
  return {
    workspaces: (cards || []).length + (others || []).length,
    clients: orgs.size,
    previews: (previews || []).length,
  }
}

/** /health/ready -> a plain label, or null when there is no trustworthy answer. */
export function healthLabel(report) {
  const s = String(report?.status || '').toLowerCase()
  if (s === 'ok') return { tone: 'ok', label: 'All systems normal' }
  if (s === 'degraded') return { tone: 'warn', label: 'Running with issues' }
  if (s === 'failed') return { tone: 'bad', label: 'Service problem' }
  return null
}

/** Shown under the person's name. Server role values -> plain words. */
export function roleLabel(role) {
  const r = String(role || '').toLowerCase()
  const map = {
    god_admin: 'Platform owner', super_admin: 'Administrator', owner: 'Owner', admin: 'Administrator',
    manager: 'Manager', member: 'Team member', agent: 'Agent', viewer: 'Viewer', sales: 'Sales',
  }
  if (map[r]) return map[r]
  return r ? r.replace(/_/g, ' ').replace(/\b\w/g, m => m.toUpperCase()) : ''
}

export function firstName(user) {
  const n = String(user?.full_name || user?.name || '').trim()
  if (n) return n.split(/\s+/)[0]
  return ''
}

/** Search across what a person reads on the card. */
export function matchesQuery(item, q) {
  if (!q) return true
  return [item.display, item.summary, item.kind, item.orgLabel, item.organization_name, item.company_name]
    .some(t => t && String(t).toLowerCase().includes(q))
}
