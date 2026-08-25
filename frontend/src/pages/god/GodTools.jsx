/**
 * GodTools — privileged actions, never exposed at organization level.
 *
 * A tile is only clickable when `status === 'live'`, which means a real backend
 * endpoint exists TODAY. Everything else is visibly disabled and labelled with
 * what it is waiting on. Do not promote a tile to 'live' without an endpoint.
 */
import { T } from './godTheme'

const TAG_STYLE = {
  live:    { color: T.gold,  bd: '#3a3216', bg: '#141005' },
  partial: { color: T.amber, bd: '#4a3a12', bg: '#161105' },
  pending: { color: '#4a637f', bd: '#243a52', bg: '#0a1220' },
}

/** status: 'live' → clickable · 'partial' / 'pending' → disabled, honestly labelled */
export const TOOLS = [
  { key: 'scraper', gold: true, status: 'live', tag: 'LIVE · GOD ONLY',
    name: 'Lead Scraper',
    desc: 'Scrape Google Places by ZIP and business type, validate phone type, import into a chosen organization.',
    endpoint: '/scraper/*' },
  { key: 'create', gold: true, status: 'live', tag: 'LIVE · GOD ONLY',
    name: 'Create Account',
    desc: 'Stand up a new organization or user under any platform, set role, assign package.',
    endpoint: 'POST /god/orgs · POST /god/users' },
  { key: 'suspend', gold: true, status: 'live', tag: 'LIVE · GOD ONLY',
    name: 'Suspend / Reactivate',
    desc: 'Block an organization or deactivate a user immediately across the whole platform.',
    endpoint: 'POST /god/orgs/{id}/suspend · /reactivate' },
  { key: 'enter', gold: true, status: 'live', tag: 'LIVE · GOD ONLY',
    name: 'Enter Organization',
    desc: "Assume any org's view to diagnose in place. Banner stays up so you never lose track of whose data you're in.",
    endpoint: 'POST /god/orgs/{id}/impersonate' },
  { key: 'pipeline', status: 'pending', tag: 'NEEDS BUILD',
    name: 'Pipeline & Cadence Design',
    desc: 'Build the outreach sequences each org runs — you design them, they run them.',
    endpoint: 'no god-level endpoint yet' },
  { key: 'package', status: 'pending', tag: 'NEEDS BILLING MODEL',
    name: 'Assign Package & Invoice',
    desc: 'Set an org’s plan, start their billing, raise a manual invoice for anyone paying outside Stripe.',
    endpoint: 'needs invoices + payments tables' },
  { key: 'brand', status: 'partial', tag: 'PARTIAL',
    name: 'Brand & Mobile Config',
    desc: 'Per-org branding, domain, sender identity and mobile app presentation.',
    endpoint: '/branding exists · no god-level editor' },
  { key: 'audit', status: 'pending', tag: 'TABLE EXISTS · NO UI',
    name: 'Audit Log',
    desc: 'Every privileged action, who did it, when, and against which tenant.',
    endpoint: 'audit_log_entries table · no god endpoint' },
]

export default function GodTools({ onLaunch }) {
  return (
    <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fit,minmax(230px,1fr))' }}>
      {TOOLS.map(t => {
        const live = t.status === 'live'
        const tag = TAG_STYLE[t.status] || TAG_STYLE.pending
        return (
          <button key={t.key} type="button"
            className={`gm-tool ${t.gold ? 'gm-gold' : ''} ${live ? 'gm-live' : 'gm-disabled'}`}
            disabled={!live}
            onClick={live ? () => onLaunch?.(t) : undefined}
            title={t.endpoint}
          >
            <b style={{ display: 'block', fontSize: 14, color: '#eaf4ff', marginBottom: 7, fontWeight: 600 }}>
              {t.name}
            </b>
            <small style={{ display: 'block', fontSize: 10, color: '#5e7796', lineHeight: 1.7 }}>
              {t.desc}
            </small>
            <span style={{
              display: 'inline-block', marginTop: 13, fontSize: 8, letterSpacing: '.08em',
              fontWeight: 800, borderRadius: 999, padding: '3px 7px',
              color: tag.color, border: `1px solid ${tag.bd}`, background: tag.bg,
            }}>{t.tag}</span>
          </button>
        )
      })}
    </div>
  )
}
