/**
 * PRODUCT STATUS — LIVE NOW / COMING NEXT.
 *
 * This exists so the primary navigation does not have to carry a wall of
 * NEEDS BUILD tags. Fourteen rail entries used to be marked unbuilt, which made
 * a product with a working control plane read as a prototype.
 *
 * The rule the tags were protecting is kept, not dropped: nothing is claimed to
 * work that does not. It is stated ONCE, here, in a section that is explicitly
 * about what is finished and what is next — instead of thirteen times inside
 * the navigation a person uses to get their work done.
 *
 * MODULES is the single source of truth for BOTH this panel and the rail
 * (GodShell imports it). Adding a route and flipping `live` in one place is
 * what promotes a module; there is nowhere else to remember.
 */

/**
 * live:true  → a registered route in App.jsx backed by real endpoints.
 * live:false → a roadmap item. `needs` says what it is actually waiting on,
 *              so "coming next" is never a vague promise.
 */
export const MODULES = [
  // ── shipped ──────────────────────────────────────────────────────────────
  { key: 'command',   label: 'Command Center',    live: true, to: '/god',                 group: 'COMMAND' },
  { key: 'platform',  label: 'Platform',          live: true, to: '/god/platform',        group: 'COMMAND' },
  { key: 'orgs',      label: 'Organizations',     live: true, to: '/god/organizations',   group: 'COMMAND' },
  { key: 'customers', label: 'Customers',         live: true, to: '/god/customers',       group: 'COMMAND' },
  { key: 'users',     label: 'Users & Identity',  live: true, to: '/god/users-all',       group: 'COMMAND' },
  { key: 'salesops',  label: 'Sales Operations',  live: true, to: '/god/sales-operations', group: 'OPERATIONS' },
  { key: 'impls',     label: 'Implementations',   live: true, to: '/god/implementations', group: 'OPERATIONS' },
  // POINTS AT THE REGISTERED ROUTE. `/scraper` still redirects here for old
  // links, but a status board should name the real destination.
  { key: 'scraper',   label: 'Lead Scraper',      live: true, to: '/god/lead-scraper',    group: 'OPERATIONS' },
  { key: 'pricing',   label: 'Pricing & Compensation', live: true, to: '/god/pricing',    group: 'OPERATIONS' },
  { key: 'comp',      label: 'Sales Compensation', live: true, to: '/god/compensation',   group: 'OPERATIONS' },
  { key: 'audit',     label: 'Audit & Security',  live: true, to: '/god/audit',           group: 'PLATFORM' },
  { key: 'execaccess', label: 'Executive Access', live: true, to: '/god/executive-access', group: 'PLATFORM' },

  // ── SHIPPED SINCE THIS LIST WAS LAST TRUE ────────────────────────────────
  // Billing was listed as waiting on "invoices + payments tables". Those
  // tables now exist (BillingInvoice, BillingPayment), the plan catalogue
  // prices customers per brand, and /god/billing is a registered screen. A
  // roadmap that still calls a shipped feature unbuilt is exactly as
  // misleading as a nav item that claims something works when it does not —
  // it is the same lie pointing the other way.
  { key: 'billing',   label: 'Billing & Revenue', live: true, to: '/god/billing',         group: 'PLATFORM' },
  // Support Intelligence. live:true because /god/support is a registered
  // route backed by real endpoints — the queue, the repair registry, the
  // incident correlation and the daily brief all read and write. The same
  // rule the comment above states: a board that omits a shipped capability
  // is the same lie as one that claims an unshipped one.
  { key: 'support',   label: 'Support',           live: true, to: '/god/support',         group: 'PLATFORM' },

  // GOD-04: roadmap items (live: false) removed from this list.
  // platformRoadmap.json is the authoritative source for feature status.
  // RoadmapBoard.jsx reads it directly — no duplication here.
]

export const LIVE_MODULES = MODULES.filter(m => m.live)
export const NEXT_MODULES = MODULES.filter(m => !m.live)

export default function ProductStatus({ onGo }) {
  return (
    <div className="gm-modules">
      <div className="gm-modbox">
        <h4 className="live">LIVE NOW</h4>
        <div className="gm-chips">
          {LIVE_MODULES.map(m => (
            <button key={m.key} type="button" className="gm-chip live"
                    onClick={() => onGo && onGo(m.to)} title={'Open ' + m.to}>
              {m.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
// GOD-04: COMING NEXT chips removed — feature-status detail is now in
// RoadmapBoard.jsx (reads platformRoadmap.json). No hardcoded roadmap content here.
