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
  { key: 'scraper',   label: 'Lead Scraper',      live: true, to: '/scraper',             group: 'OPERATIONS' },
  { key: 'audit',     label: 'Audit & Security',  live: true, to: '/god/audit',           group: 'PLATFORM' },

  // ── roadmap ──────────────────────────────────────────────────────────────
  { key: 'billing',    label: 'Billing & Invoicing', live: false,
    needs: 'invoices + payments tables — /billing/all returns no amounts' },
  { key: 'revenue',    label: 'Revenue Analytics',   live: false,
    needs: 'the billing model above; there is no monetary source to chart' },
  { key: 'godleads',   label: 'Cross-org Leads',     live: false,
    needs: 'a screen for GET /god/leads, which already returns the data' },
  { key: 'messaging',  label: 'Communications',      live: false,
    needs: 'a platform-wide message browser; delivery counts are already live in Platform Health' },
  { key: 'cadence',    label: 'Pipeline & Cadence',  live: false,
    needs: 'a god-level cadence designer; cadences exist per organization only' },
  { key: 'jobs',       label: 'Queue & Job Health',  live: false,
    needs: 'a job table — scheduled work currently leaves no durable record' },
  { key: 'flags',      label: 'Feature Flags',       live: false,
    needs: 'a flag store; per-customer feature toggles already exist on Customer 360' },
  { key: 'settings',   label: 'System Settings',     live: false,
    needs: 'a platform settings model' },
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
      <div className="gm-modbox">
        <h4 className="next">COMING NEXT</h4>
        <div className="gm-chips">
          {NEXT_MODULES.map(m => (
            <span key={m.key} className="gm-chip next" title={'Waiting on: ' + m.needs}>
              {m.label}
            </span>
          ))}
        </div>
        <p style={{ margin: '10px 0 0', fontSize: 8.5, color: '#4a637f', lineHeight: 1.6 }}>
          Hover any item to see exactly what it is waiting on. Nothing here is
          hidden behind a button that would do nothing if you pressed it.
        </p>
      </div>
    </div>
  )
}
