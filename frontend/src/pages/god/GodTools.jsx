/**
 * GOD TOOLS — privileged actions, never exposed at organization level.
 *
 * EVERY TILE HERE OPENS A SCREEN THAT ALREADY EXISTS. None of them is a second
 * implementation of anything: "Create organization" is the provisioning screen
 * the customer flow uses, "Package assignment" is the brand's own pricing
 * editor, "Audit" is the control-plane audit. A tool that duplicated one of
 * those would be a second thing to keep correct and a second thing to secure.
 *
 * There are no disabled tiles. A capability that does not exist yet is not a
 * greyed-out button here — it is a chip in COMING NEXT, where it reads as a
 * roadmap item instead of as something broken.
 */
import { T } from './godTheme'

/**
 * `to` is a route that is registered in App.jsx today, or a `#anchor` on this
 * page. `endpoint` is shown on hover so the owner can see what a tool actually
 * calls.
 */
export const TOOLS = [
  {
    key: 'enter', gold: true, name: 'Enter Organization',
    desc: "Assume any customer's context to diagnose in place. Audited, and it "
        + 'creates no membership — you stay yourself the whole time.',
    cta: 'Choose an organization →', to: '/god/organizations',
    endpoint: 'POST /god/platform/context/customer/{id}',
  },
  {
    key: 'create-org', gold: true, name: 'Create Organization',
    desc: 'Stand up a new customer: brand, industry, locations, first admin and '
        + 'the activation checks that gate going live.',
    cta: 'Create customer →', to: '/god/customers/new',
    endpoint: 'POST /god/customers',
  },
  {
    key: 'create-user', gold: true, name: 'Create User',
    desc: 'Add a person to a customer. Email first — one human is one identity, '
        + 'so an address that already exists anywhere is reused, never duplicated.',
    cta: 'Pick a customer →', to: '/god/customers',
    endpoint: 'POST /god/customers/{id}/users',
  },
  {
    key: 'scraper', gold: true, name: 'Lead Scraper',
    desc: 'Scrape Google Places by ZIP and business type, validate phone type, '
        + 'and import the result into an organization you choose.',
    cta: 'Launch scraper →', to: '/scraper',
    endpoint: '/scraper/*',
  },
  {
    key: 'package', name: 'Package Assignment',
    desc: "Set what a brand's packages cost, then attach one to a customer's "
        + 'implementation as its commercial structure.',
    cta: 'Open brand pricing →', to: '/god/sales-operations',
    endpoint: 'PATCH /god/ops/packages/{id}/pricing',
  },
  {
    key: 'suspend', name: 'Suspend / Reactivate',
    desc: 'Block or restore an organization immediately across the whole '
        + 'platform. Reversible, and recorded either way.',
    cta: 'Manage access →', to: '/god/organizations',
    endpoint: 'POST /god/orgs/{id}/suspend · /reactivate',
  },
  {
    key: 'billing', name: 'Billing Review',
    desc: 'Customers with no package, no payment method, or a Stripe status that '
        + 'needs a decision. Opens the command table already filtered.',
    cta: 'Review billing →', to: '/god/organizations?filter=unpriced',
    endpoint: 'GET /billing/all + /god/ops/customer-organizations',
  },
  {
    key: 'health', name: 'System Health',
    desc: 'Delivery receipts, integration credentials, customer activity and '
        + 'privileged access — with the subsystems that have no telemetry named.',
    cta: 'Jump to health →', to: '#platform-health',
    endpoint: 'GET /god/platform-health',
  },
  {
    key: 'audit', name: 'Audit & Security',
    desc: 'Every privileged action: who did it, to which record, from what to '
        + 'what, and when. No secrets are ever recorded.',
    cta: 'View audit →', to: '/god/audit',
    endpoint: 'GET /god/ops/audit',
  },
]

export default function GodTools({ onLaunch }) {
  return (
    <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fit,minmax(240px,1fr))' }}>
      {TOOLS.map(t => (
        <button
          key={t.key} type="button"
          className={`gm-tool gm-live ${t.gold ? 'gm-gold' : ''}`}
          onClick={() => onLaunch && onLaunch(t)}
          title={t.endpoint}
        >
          <b style={{ display: 'block', fontSize: 13.5, color: '#eaf4ff', marginBottom: 7, fontWeight: 600 }}>
            {t.name}
          </b>
          <small style={{ display: 'block', fontSize: 9.5, color: '#5e7796', lineHeight: 1.65 }}>
            {t.desc}
          </small>
          <span style={{
            display: 'inline-block', marginTop: 13, fontSize: 8.5, letterSpacing: '.05em',
            fontWeight: 800, color: t.gold ? T.gold : '#7cc0ff',
          }}>{t.cta}</span>
        </button>
      ))}
    </div>
  )
}
