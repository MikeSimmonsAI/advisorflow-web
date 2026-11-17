/* PROJECT OVERDRIVE — WHOLESALE NAVIGATION (Design A, owner approved and
 * locked Oct 29, 2026 — issue #439, docs/wholesale/OVERDRIVE_OWNER_LOCKED.md).
 *
 * Acquisition Command is pinned above five collapsible groups:
 *   Acquisition · Buyers & Deals · Operations · Team & Field · Tools & Settings
 *
 * Like brand/olpNav.js, this module only REARRANGES the items Layout already
 * declares: the same objects, the same routes and the same featureKey /
 * adminOnly / workspaceAdminOnly / bookaboostOnly / olpOnly flags, filtered
 * afterwards by Layout's own visible() test. So it cannot open a screen a
 * role or plan has closed, cannot drop a destination and cannot list one
 * twice. Anything it does not name still appears (in FALLBACK), so a future
 * Wholesale screen is never lost from the rail.
 *
 * The one entry it builds is Multifamily, and it is built FROM the Discovery
 * Inbox item (same flags) — it is the inbox filtered server-side to the
 * multifamily property category (`ptype=multifamily`, see
 * evosense_router.inbox). If the inbox is not on the rail, neither is
 * Multifamily, so it never points at a screen the person cannot open.
 *
 * The OLP workspace keeps its own owner-approved three-group rail
 * (brand/olpNav.js); this module is used everywhere else in Wholesale.
 */

export const PINNED_ROUTE = '/wholesale/evosense'
export const INBOX_ROUTE = '/wholesale/evosense/inbox'
export const MULTIFAMILY_ROUTE = '/wholesale/evosense/inbox?ptype=multifamily'

export const WHOLESALE_NAV = Object.freeze([
  {
    key: 'acquisition',
    label: 'Acquisition',
    routes: ['/wholesale/foreclosures', INBOX_ROUTE, '/wholesale/portfolios', '/wholesale/properties', '/wholesale/evosense/strategies',
      MULTIFAMILY_ROUTE,
      // Not named in the approved list; they are acquisition screens, so they
      // stay discoverable here (each still carries its own brand flag).
      '/wholesale/evosense/overlooked', '/wholesale/land-partners', '/wholesale/farm-ranch'],
  },
  {
    key: 'buyers',
    label: 'Buyers & Deals',
    routes: ['/wholesale/buyers', '/wholesale/deal-room', '/wholesale/deal-room/investors',
      '/wholesale/buyers/buy-boxes', '/wholesale/dispositions'],
  },
  {
    key: 'operations',
    label: 'Operations',
    routes: ['/wholesale', '/wholesale/appointments', '/wholesale/closing', '/wholesale/funding', '/wholesale/calculator'],
  },
  {
    key: 'team',
    label: 'Team & Field',
    routes: ['/wholesale/board', '/wholesale/canvass', '/wholesale/callbacks', '/wholesale/team'],
  },
  {
    key: 'tools',
    label: 'Tools & Settings',
    routes: ['/wholesale/evosense/controls', '/wholesale/exceptions', '/wholesale/pilot', '/wholesale/settings'],
  },
])

export const FALLBACK_GROUP = 'operations'

/* MY DEAL ROOM WORKSPACE (redesign, Nov 2026). The same items, the same flags
 * and the same visible() filter as Design A; only the grouping, two labels and
 * the default open state differ. Used only on the My Deal Room shell (EvoSys
 * Pro brand); BookaBoost keeps Design A above, and OLP keeps olpNav.js.
 *
 * Deal Operations (/wholesale) is the transaction board the Buyers & Deals
 * screens act on, so it sits with them; Contracts & Closing and Dispositions
 * remain focused views of the same deals. */
export const DEALS_ROUTE = '/wholesale'
export const MDR_NAV = Object.freeze([
  {
    key: 'acquire',
    label: 'Acquire',
    routes: [INBOX_ROUTE, '/wholesale/foreclosures', '/wholesale/portfolios', '/wholesale/properties',
      '/wholesale/evosense/strategies', MULTIFAMILY_ROUTE,
      '/wholesale/evosense/overlooked', '/wholesale/land-partners', '/wholesale/farm-ranch'],
  },
  {
    key: 'buyers',
    label: 'Buyers & Deals',
    routes: ['/wholesale/buyers', '/wholesale/buyers/buy-boxes', '/wholesale/deal-room', '/wholesale/deal-room/investors',
      '/wholesale/dispositions', DEALS_ROUTE],
  },
  {
    key: 'closing',
    label: 'Closing & Tools',
    routes: ['/wholesale/appointments', '/wholesale/closing', '/wholesale/funding', '/wholesale/calculator'],
  },
  {
    key: 'team',
    label: 'Team & Field',
    routes: ['/wholesale/board', '/wholesale/canvass', '/wholesale/callbacks', '/wholesale/team'],
  },
  {
    key: 'settings',
    label: 'Settings',
    routes: ['/wholesale/evosense/controls', '/wholesale/exceptions', '/wholesale/pilot', '/wholesale/settings'],
  },
])
export const MDR_FALLBACK_GROUP = 'closing'
/** Labels the My Deal Room rail says differently. Routes are unchanged. */
export const MDR_LABELS = Object.freeze({
  '/wholesale/portfolios': 'Seller Portfolios',
  [DEALS_ROUTE]: 'Deal Operations',
})
/** One line per group for its landing page. */
export const MDR_GROUP_BLURB = Object.freeze({
  acquire: 'Find and evaluate properties before a deal exists.',
  buyers: 'Match buyers, publish deals and move them to assignment.',
  closing: 'Book appointments, paper the contract, fund and run the numbers.',
  team: 'Who is working what, in the office and in the field.',
  settings: 'Providers, controls, exceptions and workspace configuration.',
})

/**
 * @param {Array<{label: string, items: Array<{to: string}>}>} wsGroups  Layout's Wholesale groups
 * @returns {{ pinned: object|null, groups: Array<{key: string, label: string, items: Array}> }}
 *   the same items regrouped (empty groups dropped), Acquisition Command pinned.
 */
export function wholesaleRail(wsGroups, { variant } = {}) {
  const mdr = variant === 'mdr'
  const NAV = mdr ? MDR_NAV : WHOLESALE_NAV
  const FALLBACK = mdr ? MDR_FALLBACK_GROUP : FALLBACK_GROUP
  const all = []
  const seen = new Set()
  for (const g of wsGroups || []) {
    for (const item of g.items || []) {
      if (!item || !item.to || seen.has(item.to)) continue
      seen.add(item.to)
      all.push(mdr && MDR_LABELS[item.to] ? { ...item, label: MDR_LABELS[item.to] } : item)
    }
  }
  const byRoute = new Map(all.map((item) => [item.to, item]))
  const inbox = byRoute.get(INBOX_ROUTE)
  if (inbox && !byRoute.has(MULTIFAMILY_ROUTE)) {
    byRoute.set(MULTIFAMILY_ROUTE, { ...inbox, to: MULTIFAMILY_ROUTE, label: 'Multifamily', icon: 'layers', end: false, derivedFrom: INBOX_ROUTE })
  }
  const pinned = byRoute.get(PINNED_ROUTE) || null
  const placed = new Set(pinned ? [PINNED_ROUTE] : [])
  const groups = NAV.map((g) => ({
    key: g.key,
    label: g.label,
    items: g.routes.filter((r) => byRoute.has(r) && !placed.has(r)).map((r) => { placed.add(r); return byRoute.get(r) }),
  }))
  const rest = all.filter((item) => !placed.has(item.to))
  if (rest.length) groups.find((g) => g.key === FALLBACK).items.push(...rest)
  return { pinned, groups: groups.filter((g) => g.items.length) }
}

function splitTo(to) {
  const i = String(to).indexOf('?')
  return i < 0 ? [to, null] : [to.slice(0, i), new URLSearchParams(to.slice(i + 1))]
}

/** Screens with no rail entry of their own, and the entry that owns them. */
const OWNERS = [
  [/^\/wholesale\/evosense\/activity$/, PINNED_ROUTE],     // Acquisition Command's activity & system health view
  [/^\/wholesale\/evosense\/property\//, PINNED_ROUTE],   // the one-property Acquisition Command screen
  [/^\/wholesale\/deals\//, '/wholesale'],                  // a deal opens from Deal Operations
  [/^\/wholesale\/evosense\/lists\//, INBOX_ROUTE],
  [/^\/wholesale\/evosense\/strategies\//, '/wholesale/evosense/strategies'],
  [/^\/wholesale\/deal-room\//, '/wholesale/deal-room'],          // Deal Room views (offers, setup...)
  [/^\/wholesale\/buyers\//, '/wholesale/buyers'],
]

/**
 * Which ONE rail entry is the current page. Most specific wins: an entry
 * whose query matches (Multifamily) beats the bare path (Discovery Inbox), and
 * a longer path beats a shorter one, so exactly one entry is highlighted.
 * @returns {string|null} the `to` of the active item
 */
export function activeRoute(items, pathname, search = '') {
  const path = String(pathname || '').replace(/\/+$/, '') || '/'
  const params = new URLSearchParams(search || '')
  let best = null
  let bestRank = -1
  for (const item of items || []) {
    if (!item || !item.to) continue
    const [p, q] = splitTo(item.to)
    let ok
    if (q) ok = path === p && [...q.entries()].every(([k, v]) => params.get(k) === v)
    else if (item.end) ok = path === p
    else ok = path === p || path.startsWith(p + '/')
    if (!ok) continue
    const rank = p.length * 10 + (q ? 5 : 0)
    if (rank > bestRank) { best = item.to; bestRank = rank }
  }
  if (best) return best
  for (const [re, owner] of OWNERS) {
    if (re.test(path) && (items || []).some((i) => i && i.to === owner)) return owner
  }
  return null
}

/** The rail group that holds the current page, by key. */
export function groupOf(rail, activeTo) {
  if (!rail || !activeTo) return null
  const g = rail.groups.find((x) => x.items.some((i) => i.to === activeTo))
  return g ? g.key : null
}

export const COLLAPSE_KEY = 'af_ws_nav_closed'

/** Groups the person has closed, from storage. Never throws. */
export function readClosedGroups(storage) {
  try {
    const raw = storage && storage.getItem(COLLAPSE_KEY)
    const arr = raw ? JSON.parse(raw) : []
    const keys = new Set(WHOLESALE_NAV.map((g) => g.key))
    return new Set(Array.isArray(arr) ? arr.filter((k) => keys.has(k)) : [])
  } catch { return new Set() }
}

export function writeClosedGroups(storage, closed) {
  try { storage && storage.setItem(COLLAPSE_KEY, JSON.stringify([...closed])) } catch { /* storage unavailable */ }
}

/* My Deal Room: groups start CLOSED except the one holding the current page;
 * the person's explicit openings are remembered. (Design A remembers closings
 * instead, and opens everything by default.) */
export const MDR_OPEN_KEY = 'af_mdr_nav_open'

export function readOpenGroups(storage) {
  try {
    const raw = storage && storage.getItem(MDR_OPEN_KEY)
    const arr = raw ? JSON.parse(raw) : []
    const keys = new Set(MDR_NAV.map((g) => g.key))
    return new Set(Array.isArray(arr) ? arr.filter((k) => keys.has(k)) : [])
  } catch { return new Set() }
}

export function writeOpenGroups(storage, open) {
  try { storage && storage.setItem(MDR_OPEN_KEY, JSON.stringify([...open])) } catch { /* storage unavailable */ }
}

/** The landing route of a My Deal Room rail group. */
export const hubRoute = (key) => '/wholesale/hub/' + key
