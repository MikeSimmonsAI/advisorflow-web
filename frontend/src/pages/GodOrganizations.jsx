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
// SectionLabel is no longer used here: the approved design leads with the page
// title and the summary row rather than an all-caps band above the table.
import OrgCommandTable, { buildRows } from './god/OrgCommandTable'
import { Page, Strip, ActionLink, ErrorState } from './god/ui/GodKit'
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
    <>
      <GodStyles />
      {/* Owner Desk design language (#411): shared page header + summary
          strip. The table below and every action in it are unchanged. */}
      <Page eyebrow="Organizations" title="Organizations"
        sub="Every customer tenant across every brand, with the owner controls that act on them. The platform's own account is not a customer and is excluded."
        actions={<>
          <ActionLink to="/god/customers/new" primary small={false}>+ New organization</ActionLink>
          <ActionLink onClick={load} disabled={loading} small={false}>{loading ? 'Refreshing…' : 'Refresh'}</ActionLink>
        </>}>

        {err ? <ErrorState onRetry={load}>{err}</ErrorState> : null}

        <div style={{ marginBottom: 18 }}>
          <Strip cols={5} items={[
            { label: 'Organizations', value: metrics.total, sub: 'all brands' },
            { label: 'Active', value: metrics.active, sub: 'can sign in' },
            { label: 'Onboarding', value: metrics.onboarding, sub: 'not live yet' },
            { label: 'Suspended', value: metrics.suspended, sub: 'switched off on purpose', tone: metrics.suspended ? 'red' : undefined },
            { label: 'Needs attention', value: metrics.attention,
              sub: metrics.unscored ? metrics.unscored + ' not yet scored' : 'from the health score' },
          ]} />
        </div>

        <OrgCommandTable
          key={params.get('filter') || 'all'}
          orgs={orgs} customers={customers} billingRows={billingRows}
          loading={loading} busyId={busy} initialFilter={params.get('filter')}
          onEnter={askEnter} onSuspend={askSuspend} onGo={go}
        />

        <p className="gk-row-sub" style={{ marginTop: 16, lineHeight: 1.7, maxWidth: '90ch' }}>
          Health is computed by the backend (<code>_compute_health_score</code>), not here, so this
          table and the Command Center cannot disagree about it. Billing state reads the
          organization's own Stripe columns — there is no invoice model, so no amount is shown
          anywhere on this screen.
        </p>
      </Page>

      {confirm && (
        <ConfirmDialog
          tone={confirm.tone} eyebrow={confirm.eyebrow} title={confirm.title}
          body={confirm.body} confirmLabel={confirm.confirmLabel}
          busy={!!busy} onConfirm={runConfirm} onCancel={() => setConfirm(null)}
        />
      )}
    </>
  )
}
