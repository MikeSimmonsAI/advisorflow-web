/* OLP WORKSPACE NAVIGATION (owner decision 5 and 6, Oct 29).
 *
 * In OLP mode the Wholesale rail is regrouped into three plain groups. This
 * module only REARRANGES the items Layout already has: the same objects, the
 * same routes, the same featureKey / adminOnly / workspaceAdminOnly /
 * bookaboostOnly flags, filtered afterwards by the same visible() test. So it
 * cannot add a destination, remove one, duplicate one or show a restricted
 * item - and anything it does not list still appears (under Market
 * Operations), so a future Wholesale screen is never lost.
 *
 * Property research has no list route of its own: it opens from the radar
 * (and Discovery Inbox) at /wholesale/evosense/property/:id.
 */

export const OLP_NAV = Object.freeze([
  {
    label: 'Opportunity Intelligence',
    routes: ['/wholesale/evosense/overlooked', '/wholesale/buyers', '/wholesale/buyers/buy-boxes',
      '/wholesale/deal-room', '/wholesale/deal-room/investors'],
  },
  {
    label: 'Market Operations',
    routes: ['/wholesale/evosense', '/wholesale/foreclosures', '/wholesale/evosense/inbox', '/wholesale/evosense/strategies',
      '/wholesale', '/wholesale/properties', '/wholesale/appointments', '/wholesale/board', '/wholesale/canvass',
      '/wholesale/calculator', '/wholesale/funding', '/wholesale/exceptions', '/wholesale/callbacks',
      '/wholesale/closing', '/wholesale/dispositions'],
  },
  {
    label: 'Account & Settings',
    routes: ['/wholesale/evosense/controls', '/wholesale/pilot', '/wholesale/team', '/wholesale/settings'],
  },
])

// Menu names in OLP mode. The page titles are unchanged ("Overlooked
// Properties" stays the radar's page title).
export const OLP_LABELS = Object.freeze({
  '/wholesale/evosense/overlooked': 'Opportunity Radar',
  '/wholesale/settings': 'Workspace Settings',
})

const FALLBACK = 'Market Operations'

/**
 * @param {Array<{label: string, items: Array<{to: string}>}>} wsGroups  Layout's Wholesale groups
 * @returns {Array<{label: string, items: Array}>}  the same items, regrouped; empty groups dropped
 */
export function olpGroups(wsGroups) {
  const all = []
  const seen = new Set()
  for (const g of wsGroups || []) {
    for (const item of g.items || []) {
      if (!item || seen.has(item.to)) continue
      seen.add(item.to)
      all.push(item)
    }
  }
  const byRoute = new Map(all.map((item) => [item.to, item]))
  const placed = new Set()
  const out = OLP_NAV.map((g) => ({
    label: g.label,
    items: g.routes.filter((r) => byRoute.has(r)).map((r) => {
      placed.add(r)
      const item = byRoute.get(r)
      return OLP_LABELS[r] ? { ...item, label: OLP_LABELS[r] } : item
    }),
  }))
  const rest = all.filter((item) => !placed.has(item.to))
  if (rest.length) out.find((g) => g.label === FALLBACK).items.push(...rest)
  return out.filter((g) => g.items.length)
}

/* OLP HEADER SYSTEM (owner decision 3). Three variants, chosen by page:
 *   hero     primary dashboards and major workspaces - photo hero kept;
 *   detail   one property, deal, investor or record;
 *   compact  tables, workflows, operations and settings - no photo banner.
 * The pages keep their own markup; brand/olp-workspace.css styles the
 * existing headers (.evo-herobox, .evo-head, .drm-hero, .bbr-hero) per
 * variant, so every screen shares one type scale, eyebrow, border and
 * action placement. */
const HERO = new Set(['/wholesale/evosense/overlooked', '/wholesale/evosense', '/wholesale/buyers', '/wholesale/deal-room', '/wholesale'])
const DETAIL = [/^\/wholesale\/evosense\/property\//, /^\/wholesale\/deals\//, /^\/wholesale\/properties\/[^/]+/,
  /^\/wholesale\/deal-room\/investors\/[^/]+/, /^\/wholesale\/buyers\/(?!buy-boxes)[^/]+/]

export function olpHeaderFor(pathname) {
  const p = String(pathname || '').replace(/\/+$/, '') || '/'
  if (HERO.has(p)) return 'hero'
  if (DETAIL.some((re) => re.test(p))) return 'detail'
  return 'compact'
}
