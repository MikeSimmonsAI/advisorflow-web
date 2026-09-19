/**
 * WHAT A VERTICAL'S WORKSPACE LOOKS LIKE — configuration, not a second app.
 *
 * ===========================================================================
 * WHY THIS EXISTS
 * ===========================================================================
 *
 * A retail-energy operator opened this product and saw a generic CRM with
 * their company picked from a dropdown: platform vocabulary in the rail
 * ("Leads", "Replies", "My Work"), the platform's own dark command-centre
 * palette, and a dashboard built around reply rates and booking rates. Every
 * one of those is the right screen underneath. None of them is how the work
 * is named or looked at in that business.
 *
 * So this file holds PRESENTATION for a vertical and nothing else:
 *
 *   - what the rail's groups and labels are called, and which EXISTING route
 *     each one opens,
 *   - which configured workflow screen (`/view/<key>`) sits where,
 *   - which skin the workspace wears.
 *
 * ===========================================================================
 * THREE RULES
 * ===========================================================================
 *
 * 1. NO NEW FUNCTIONALITY. Every `to:` below is a route that already exists
 *    and already enforces its own permissions. Every `view:` is a key the
 *    server has to confirm through GET /workspace-views before it renders —
 *    a vertical cannot conjure a screen its workspace has not configured.
 *
 * 2. NO CUSTOMER NAMED HERE. The key is the INDUSTRY, which is a column on
 *    the organization and the same value app/services/industry_templates.py
 *    is keyed by. Every customer in the vertical gets this presentation; the
 *    name in the rail comes from their own branding row.
 *
 * 3. NOTHING GLOBAL CHANGES. A workspace whose industry has no entry here
 *    gets exactly what it got before: NAV_GROUPS and the platform skin. The
 *    lookup below returns null and every call site falls through.
 */

export const VERTICAL_ENERGY = 'energy'

/**
 * RETAIL ENERGY.
 *
 * Labels, groups and order are the approved back-office design. The mapping
 * to the right of each one is the existing capability it opens:
 *
 *   Leads & Customers   /leads           the lead + contact list
 *   Rate Requests       /view/<key>      configured workflow screen (leads)
 *   Sales Pipeline      /pipeline        the existing pipeline board
 *   Move Concierge      /view/<key>      configured workflow screen (leads)
 *   Communications      /replies         the reply inbox and conversation
 *   Tasks & Follow-Up   /workqueue       the existing work queue
 *   Renewals            /view/<key>      configured workflow screen (leads)
 *   Reports             /reports         the existing reporting surface
 *   Integrations        /crm-connectors  the existing connector surface
 *   Team & Access       /users           the existing team surface
 *   Launch Center       /launch          the existing Launch Experience
 *
 * The visibility keys (`featureKey`, `adminOnly`, `launchOnly`) are carried
 * over from the platform rail UNCHANGED. Renaming a door must not open one.
 */
const ENERGY = {
  key: VERTICAL_ENERGY,
  // Drives `data-workspace-vertical` on <html>; the skin is CSS keyed on it.
  skin: 'energy',
  navGroups: [
    {
      label: 'Operate',
      items: [
        { to: '/', label: 'Overview', icon: 'grid' },
        { to: '/leads', label: 'Leads & Customers', icon: 'users', featureKey: 'leads' },
        { view: 'rate-requests', label: 'Rate Requests', icon: 'zap' },
        { to: '/pipeline', label: 'Sales Pipeline', icon: 'trending-up' },
        { view: 'move-concierge', label: 'Move Concierge', icon: 'truck' },
      ],
    },
    {
      label: 'Work',
      items: [
        { to: '/replies', label: 'Communications', icon: 'message' },
        { to: '/workqueue', label: 'Tasks & Follow-Up', icon: 'check-square' },
        { view: 'renewals', label: 'Renewals', icon: 'refresh' },
        { to: '/reports', label: 'Reports', icon: 'activity', adminOnly: true, featureKey: 'reports' },
      ],
    },
    {
      label: 'System',
      items: [
        { to: '/crm-connectors', label: 'Integrations', icon: 'link', adminOnly: true, featureKey: 'crm_connectors' },
        { to: '/users', label: 'Team & Access', icon: 'user-plus', adminOnly: true, featureKey: 'users' },
        // `launchOnly` is kept, not dropped to force the item into the rail.
        // A workspace with no open implementation has no Launch Center to
        // open, and a permanent link to a 404 is worse than an honest gap.
        { to: '/launch', label: 'Launch Center', icon: 'zap', launchOnly: true },
      ],
    },
  ],
  // Where a configured view the design does not name is appended, so moving
  // a workspace onto this presentation can never LOSE a screen its operator
  // configured. Keeps the rail complete without inventing a group.
  overflowGroup: 'Operate',
}

const BY_INDUSTRY = {
  [VERTICAL_ENERGY]: ENERGY,
}

/**
 * The presentation for a workspace, or null for "the platform's own".
 *
 * Reads `branding.industry`, which GET /branding/org resolves for the ACTIVE
 * workspace — so an operator standing inside a customer gets the customer's
 * vertical, and stepping back out returns the platform rail.
 */
export function verticalFor(branding) {
  const industry = branding && branding.industry
  if (!industry) return null
  return BY_INDUSTRY[String(industry).trim().toLowerCase()] || null
}

/**
 * The rail for a vertical, with `view:` items resolved against the screens
 * the SERVER says this workspace has configured.
 *
 * A `view:` the workspace has not configured is dropped rather than rendered
 * as a dead link, and any configured screen the design does not name is
 * appended to `overflowGroup` in configured order. Both rules exist so the
 * rail describes the workspace that is actually there.
 */
export function navGroupsFor(vertical, configuredViews) {
  if (!vertical) return null
  const views = Array.isArray(configuredViews) ? configuredViews : []
  const byKey = new Map(views.map(v => [v.key, v]))
  const placed = new Set()

  const groups = vertical.navGroups.map(group => ({
    label: group.label,
    items: group.items.reduce((items, item) => {
      if (!item.view) { items.push(item); return items }
      const configured = byKey.get(item.view)
      if (!configured) return items
      placed.add(item.view)
      items.push({
        to: `/view/${configured.key}`,
        // The design's word wins over the configuration's for a screen it
        // names, because the design IS the agreed vocabulary. Anything it
        // does not name keeps whatever the workspace called it.
        label: item.label || configured.label,
        icon: item.icon || configured.icon,
      })
      return items
    }, []),
  }))

  const overflow = groups.find(g => g.label === vertical.overflowGroup) || groups[0]
  views.forEach(configured => {
    if (placed.has(configured.key)) return
    overflow.items.push({
      to: `/view/${configured.key}`,
      label: configured.label,
      icon: configured.icon,
    })
  })

  return groups
}

/**
 * The two lines of the workspace's own name for the rail's brand block.
 *
 * "Northwind Light & Power" -> ["Northwind", "Light & Power"]; a single-word
 * name returns one line. Derived from the name the server gave, never from a
 * table of customers in this file.
 */
export function brandLines(name) {
  const text = String(name || '').trim().replace(/\s+/g, ' ')
  if (!text) return []
  const space = text.indexOf(' ')
  if (space === -1) return [text]
  return [text.slice(0, space), text.slice(space + 1)]
}
