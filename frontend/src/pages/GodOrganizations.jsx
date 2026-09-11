/**
 * GOD MODE — ORGANIZATIONS.
 *
 * ── THE BUG THIS FILE EXISTED WITH ────────────────────────────────────────
 * Every read here was written as `res.data.orgs`, but `api.get` in
 * src/api/client.js returns the PARSED JSON, not an axios envelope. `res.data`
 * was therefore always undefined, so this screen rendered "No organizations
 * found" and five "—" KPI cards no matter how many customers existed, and
 * Enter Organization read `r.data.session_id` off nothing. It looked like an
 * empty platform rather than a broken read.
 *
 * ── WHAT REPLACED IT ──────────────────────────────────────────────────────
 * The same OrgCommandTable the Command Center uses. There is now one
 * organization table in the product with one set of columns, one definition of
 * each state, and one set of actions — rather than two that could disagree.
 *
 * `?filter=` seeds the state filter so Billing Review in God Tools can link
 * straight to the rows it is about.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'

import GodStyles from './god/GodStyles'
import { T } from './god/godTheme'
// SectionLabel is no longer used here: the approved design leads with the page
// title and the summary row rather than an all-caps band above the table.
import OrgCommandTable, { buildRows } from './god/OrgCommandTable'
import ConfirmDialog from './god/ConfirmDialog'
import { enterCustomer } from './god/enterCustomer'

export default function GodOrganizations() {
  const navigate = useNavigate()
  const [params] = useSearchParams()

  const [orgs, setOrgs] = useState([])
  const [customers, setCustomers] = useState([])
  const [billingRows, setBillingRows] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')
  const [confirm, setConfirm] = useState(null)

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    const [o, c, b] = await Promise.allSettled([
      api.get('/god/orgs?limit=200'),
      api.get('/god/ops/customer-organizations'),
      api.get('/billing/all'),
    ])
    if (o.status === 'fulfilled') setOrgs(o.value?.orgs || [])
    if (c.status === 'fulfilled') setCustomers(c.value?.organizations || [])
    setBillingRows(b.status === 'fulfilled' && Array.isArray(b.value?.orgs) ? b.value.orgs : null)
    if (o.status === 'rejected' && c.status === 'rejected') {
      setErr('Could not load organizations. Check that the backend is awake.')
    }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  function go(to) {
    if (!to) return
    if (to.startsWith('#')) return
    navigate(to)
  }

  function askEnter(org) {
    if (!org?.id) return
    setConfirm({
      kind: 'enter', org, tone: 'gold',
      eyebrow: '⚡ ENTER ORGANIZATION — AUDITED',
      title: org.name,
      body: 'You will operate inside this customer until you leave. The server '
          + 'writes an audit row naming you, the customer and the time, and it '
          + 'verifies that no membership was created — you stay yourself.',
      confirmLabel: 'ENTER',
    })
  }

  function askSuspend(org, mode) {
    setConfirm({
      kind: mode, org,
      tone: mode === 'suspend' ? 'danger' : 'blue',
      eyebrow: mode === 'suspend' ? '⚠ SUSPEND ORGANIZATION' : '✓ REACTIVATE ORGANIZATION',
      title: org.name,
      body: mode === 'suspend'
        ? 'Everyone in this organization will be unable to sign in. Their data is '
        + 'untouched and this is reversible from the same table.'
        : 'Access is restored for every user in this organization.',
      confirmLabel: mode === 'suspend' ? 'SUSPEND' : 'REACTIVATE',
    })
  }

  async function runConfirm() {
    if (!confirm) return
    const { kind, org } = confirm
    setBusy(org.id); setErr('')
    try {
      if (kind === 'enter') {
        await enterCustomer(org.id, org.name)
        setConfirm(null)
        navigate('/')
        return
      }
      await api.post(`/god/orgs/${org.id}/${kind}`, {})
      setConfirm(null)
      await load()
    } catch (e) {
      setErr(e?.message || 'The action was refused.')
      setConfirm(null)
    } finally { setBusy('') }
  }

  // ── THE SUMMARY ROW ──────────────────────────────────────────────────────
  // Every tile is a count of the SAME rows the table below renders, which come
  // from /god/ops/customer-organizations joined to /god/orgs. Nothing here is
  // estimated, and nothing is invented to fill a tile.
  //
  // UNKNOWN IS NOT ZERO. Health is computed by the backend and is absent for an
  // organization it has never scored; those rows are excluded from the
  // attention count and reported beside it rather than being silently counted
  // as healthy. If NOTHING has a score the tile reads "—", because "0 need
  // attention" would be a claim we cannot make.
  const metrics = useMemo(() => {
    const rows = buildRows({ orgs, customers, billingRows })
    const scored = rows.filter(r => typeof r.health_score === 'number')
    return {
      total: rows.length,
      active: rows.filter(r => r.is_active).length,
      onboarding: rows.filter(r => r.implementation && !r.implementation.is_live).length,
      suspended: rows.filter(r => !r.is_active).length,
      attention: scored.length ? scored.filter(r => r.health_score < 80).length : null,
      unscored: rows.length - scored.length,
    }
  }, [orgs, customers, billingRows])

  return (
    <div className="gm-scope" style={{ minHeight: '100%' }}>
      <GodStyles />
      <div style={{ position: 'relative', zIndex: 1, maxWidth: 1560, margin: '0 auto', padding: '24px 26px 60px' }}>

        <div className="gm-pagehead">
          <div style={{ minWidth: 0 }}>
            <h1 className="gm-h1">Organizations</h1>
            <p className="gm-lede">
              Every customer tenant across every brand, with the owner controls
              that act on them. The platform's own account is not a customer and
              is excluded.
            </p>
          </div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            {/* The God action wears the gold. It is the one creation path on
                this screen and it is never in doubt. */}
            <button className="gm-btn gm-gold-btn" onClick={() => navigate('/god/customers/new')}>
              <PlusIcon /> Create Organization
            </button>
            <button className="gm-btn" onClick={load} disabled={loading}>
              <RefreshIcon /> {loading ? 'Refreshing…' : 'Refresh'}
            </button>
          </div>
        </div>

        {err && (
          <div className="gm-card" style={{ padding: '13px 15px', marginBottom: 16,
                                            borderColor: 'var(--gm-pill-red-bd)',
                                            background: 'var(--gm-pill-red-bg)',
                                            color: 'var(--gm-pill-red-fg)', fontSize: 13 }}>
            {err}
          </div>
        )}

        <div className="gm-metrics">
          <Metric tone="" value={metrics.total} label="Total Organizations" icon={ICO.building} />
          <Metric tone="ok" value={metrics.active} label="Active" icon={ICO.check} />
          <Metric tone="warn" value={metrics.onboarding} label="Onboarding" icon={ICO.branch} />
          <Metric tone="bad" value={metrics.suspended} label="Suspended" icon={ICO.pause} />
          <Metric tone="off" value={metrics.attention} label="Attention"
                  icon={ICO.alert}
                  note={metrics.unscored
                    ? metrics.unscored + ' not yet scored'
                    : null} />
        </div>

        <OrgCommandTable
          key={params.get('filter') || 'all'}
          orgs={orgs} customers={customers} billingRows={billingRows}
          loading={loading} busyId={busy} initialFilter={params.get('filter')}
          onEnter={askEnter} onSuspend={askSuspend} onGo={go}
        />

        <p style={{ marginTop: 16, fontSize: 12, color: T.dim, lineHeight: 1.7, maxWidth: '90ch' }}>
          Health is computed by the backend (<code>_compute_health_score</code>), not here, so this
          table and the Command Center cannot disagree about it. Billing state reads the
          organization's own Stripe columns — there is no invoice model, so no amount is shown
          anywhere on this screen.
        </p>
      </div>

      {confirm && (
        <ConfirmDialog
          tone={confirm.tone} eyebrow={confirm.eyebrow} title={confirm.title}
          body={confirm.body} confirmLabel={confirm.confirmLabel}
          busy={!!busy} onConfirm={runConfirm} onCancel={() => setConfirm(null)}
        />
      )}
    </div>
  )
}

/**
 * One summary tile.
 *
 * `value === null` renders an em dash rather than a zero. The distinction
 * matters on a control plane: "no organization needs attention" and "nothing
 * has been scored yet" are different statements and only one of them is
 * reassuring.
 */
function Metric({ value, label, icon, tone = '', note = null }) {
  return (
    <div className="gm-card gm-metric">
      <span className={'gm-metric-ico ' + tone} aria-hidden="true">
        <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
          <path d={icon} />
        </svg>
      </span>
      <span style={{ minWidth: 0 }}>
        <span className="gm-metric-v">{value === null || value === undefined ? '—' : value}</span>
        <span className="gm-metric-k">{label}</span>
        {note ? <span className="gm-metric-k" style={{ fontSize: 11 }}>{note}</span> : null}
      </span>
    </div>
  )
}

const ICO = {
  building: 'M3 21h18M5 21V7l7-4 7 4v14M9 9h1M9 13h1M9 17h1M14 9h1M14 13h1M14 17h1',
  check: 'M20 6 9 17l-5-5',
  branch: 'M6 3v12M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a9 9 0 0 1-9 9',
  pause: 'M10 4v16M14 4v16',
  alert: 'M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z',
}

function PlusIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
      <path d="M12 5v14M5 12h14" />
    </svg>
  )
}

function RefreshIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 12a9 9 0 1 1-2.6-6.4M21 3v6h-6" />
    </svg>
  )
}
