/**
 * GOD MODE NAVIGATION — six destinations, every existing screen inside one.
 *
 * The sidebar shows the six SECTIONS. Each section's TABS are the pages that
 * used to be separate sidebar entries; the tab bar under the header (GodShell)
 * shows the active section's tabs. Every old URL still works: it is either a
 * tab here, or `match`ed into a tab (detail pages, retired duplicates), or
 * redirected in App.jsx (Control Room, Platform Overview).
 *
 * WHERE EVERY RETIRED SIDEBAR ENTRY WENT (owner redesign, Oct 22 2026):
 *   Control Room ............ Launch Board › Relay & project board  (/god/control-room)
 *   Platform Overview ....... Organizations › Brands & workspaces   (/god/platform matched)
 *   Customer Launches ....... Organizations › Go-live checklist
 *   Training (staff, not AI)  Organizations › Staff training
 *   Sales Operations ........ Revenue & Billing › Sales pipeline
 *   Pricing & Compensation .. Revenue & Billing › Pricing
 *   Sales Compensation ...... Revenue & Billing › Sales team pay
 *   Revenue History ......... Revenue & Billing › Revenue history
 *   Lead Intelligence (+4) .. Revenue & Billing › Lead intelligence
 *   Access & Permissions .... Settings & Security › Access check
 *   System Health ........... Dashboard (Platform health card)
 *   Diagnostics, Voice,
 *   Maintenance, Support,
 *   Background Jobs, Audit .. Settings & Security tabs
 *   Roadmap ................. Launch Board › Roadmap archive
 */

export const SECTIONS = [
  {
    key: 'dashboard', label: 'Dashboard', icon: 'command', path: '/god',
    hint: 'The whole business at a glance',
    tabs: [{ label: 'Command center', path: '/god', exact: true }],
  },
  {
    key: 'orgs', label: 'Organizations', icon: 'building', path: '/god/organizations',
    hint: 'Customers, workspaces, people, access and features',
    tabs: [
      { label: 'Organizations', path: '/god/organizations', hint: 'Organization records and administration: enter, open, suspend' },
      { label: 'Customers', path: '/god/customers', match: ['/god/customers/'], hint: 'The commercial relationship: lifecycle and Customer 360' },
      { label: 'Brands & workspaces', path: '/god/workspaces', match: ['/god/platform', '/god/brands/'], hint: 'Live tenant environments, entered as their brand or customer' },
      { label: 'People & access', path: '/god/access', match: ['/god/access/'] },
      { label: 'All users', path: '/god/users-all' },
      { label: 'SCI & Wholesale access', path: '/god/access-setup' },
      { label: 'Features', path: '/god/entitlements' },
      { label: 'Onboarding', path: '/god/implementations', match: ['/god/implementations/', '/god/provision/'] },
      { label: 'Go-live checklist', path: '/god/launches' },
      { label: 'Demo suite', path: '/god/demo-suite' },
      { label: 'Staff training', path: '/god/training' },
      { label: 'Website intake', path: '/god/public-intake' },
    ],
  },
  {
    key: 'ai', label: 'AI Workforce', icon: 'cpu', path: '/god/ai',
    hint: 'AI workers, test mode, emergency stop, activity',
    tabs: [
      { label: 'Overview', path: '/god/ai' },
      { label: 'Advanced: engine & jobs', path: '/god/workforce' },
      { label: 'Advanced: messaging', path: '/god/ai-operations' },
      { label: 'Advanced: brand terms', path: '/god/ai-deployment' },
    ],
  },
  {
    key: 'launch', label: 'Launch Board', icon: 'flag', path: '/god/launch-board',
    hint: 'What is built, merged, deployed and blocked — from GitHub',
    tabs: [
      { label: 'Projects', path: '/god/launch-board' },
      { label: 'Relay & project board', path: '/god/control-room' },
      { label: 'Roadmap archive', path: '/god/roadmap' },
    ],
  },
  {
    key: 'revenue', label: 'Revenue & Billing', icon: 'dollar', path: '/god/billing',
    hint: 'Billing, revenue, pricing, sales pipeline and leads',
    tabs: [
      { label: 'Billing', path: '/god/billing' },
      { label: 'Revenue history', path: '/god/revenue-history' },
      { label: 'Pricing', path: '/god/pricing' },
      { label: 'Sales team pay', path: '/god/compensation' },
      { label: 'Sales pipeline', path: '/god/sales-operations', match: ['/god/opportunities', '/god/proposals', '/god/meetings'] },
      { label: 'Lead intelligence', path: '/god/lead-intelligence', match: ['/god/lead-scraper', '/god/lead-browser'] },
    ],
  },
  {
    key: 'settings', label: 'Settings & Security', icon: 'settings', path: '/god/settings',
    hint: 'Access, audit, jobs, diagnostics, support',
    tabs: [
      { label: 'Overview', path: '/god/settings' },
      { label: 'Audit log', path: '/god/audit' },
      { label: 'Background jobs', path: '/god/diagnostics/job-runs' },
      { label: 'Access check', path: '/god/diagnostics/user-access' },
      { label: 'Executive access', path: '/god/executive-access' },
      { label: 'Support', path: '/god/support' },
      { label: 'Maintenance', path: '/god/maintenance' },
      { label: 'Texting check', path: '/god/diagnostics/twilio' },
      { label: 'Email check', path: '/god/diagnostics/email' },
      { label: 'Voice', path: '/god/voice' },
      { label: 'Lead rules check', path: '/god/diagnostics/qualification' },
    ],
  },
]

function tabMatches (tab, pathname) {
  if (tab.exact) return pathname === tab.path
  if (pathname === tab.path || pathname.startsWith(tab.path + '/')) return true
  return (tab.match || []).some(m => pathname === m || pathname.startsWith(m.endsWith('/') ? m : m + '/') || pathname.startsWith(m))
}

/** The section and tab for a pathname. Longest matching tab wins. */
export function locate (pathname) {
  let best = null
  for (const section of SECTIONS) {
    for (const tab of section.tabs) {
      if (!tabMatches(tab, pathname)) continue
      const score = tab.exact ? 1000 : tab.path.length
      if (!best || score > best.score) best = { section, tab, score }
    }
  }
  if (best) return { section: best.section, tab: best.tab }
  // Any other God page (and the /god/* catch-all) belongs to the Dashboard.
  return { section: SECTIONS[0], tab: SECTIONS[0].tabs[0] }
}

/**
 * THE RAIL ORDER (approved Owner Desk mockup, Oct 27 2026). The six SECTIONS
 * plus five shortcuts to pages that already live inside a section. A shortcut
 * is a link, not a new section: it adds no route and no permission, and the
 * tab bar still shows the section it belongs to. A shortcut is lit only when
 * the URL is exactly its own page; otherwise its section is lit as before.
 */
export const RAIL_SHORTCUTS = {
  programs:     { key: 'programs', label: 'Program Access', icon: 'layers', path: '/workspaces',
                  hint: 'The program launcher — every workspace and product you can open' },
  users:        { key: 'users', label: 'Users & Permissions', icon: 'users', path: '/god/access',
                  hint: 'People & access (Organizations › People & access)' },
  integrations: { key: 'integrations', label: 'Integrations', icon: 'link', path: '/god/settings', hash: '#connection-checks',
                  hint: 'Texting, email, voice and lead-rule checks (Settings & Security)' },
  health:       { key: 'health', label: 'System Health', icon: 'activity', path: '/god', hash: '#platform-health',
                  hint: 'Platform status on the Owner Desk' },
  audit:        { key: 'audit', label: 'Audit Log', icon: 'file', path: '/god/audit',
                  hint: 'Every sensitive action (Settings & Security › Audit log)' },
}
export const RAIL_ORDER = ['dashboard', 'programs', 'orgs', 'ai', 'launch', 'revenue', 'users', 'settings', 'integrations', 'health', 'audit']

/** Which shortcut, if any, the current URL is exactly. */
export function shortcutHere (pathname, hash = '') {
  for (const s of Object.values(RAIL_SHORTCUTS)) {
    if (s.hash ? (pathname === s.path && hash === s.hash) : (pathname === s.path || pathname.startsWith(s.path + '/'))) return s.key
  }
  return null
}
