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
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'

import GodStyles from './god/GodStyles'
import { T } from './god/godTheme'
import { SectionLabel } from './god/StatusBadge'
import OrgCommandTable from './god/OrgCommandTable'
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

  return (
    <div className="gm-scope" style={{ minHeight: '100%' }}>
      <GodStyles />
      <div className="gm-grid-overlay" />
      <div style={{ position: 'relative', zIndex: 1, maxWidth: 1560, margin: '0 auto', padding: '24px 26px 60px' }}>

        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 20,
                      alignItems: 'flex-start', padding: '8px 2px 20px', flexWrap: 'wrap' }}>
          <div>
            <button className="gm-btn" style={{ marginBottom: 12 }} onClick={() => navigate('/god')}>
              ← COMMAND CENTER
            </button>
            <h1 style={{ margin: 0, color: '#fff', fontSize: 27, letterSpacing: '-.04em', lineHeight: 1 }}>
              Organizations
            </h1>
            <p style={{ margin: '9px 0 0', color: '#758ba4', fontSize: 12, maxWidth: 720 }}>
              Every customer tenant across every brand, with the owner controls
              that act on them. The platform's own account is not a customer and
              is excluded.
            </p>
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button className="gm-btn gm-gold-btn" onClick={() => navigate('/god/customers/new')}>
              + CREATE ORGANIZATION
            </button>
            <button className="gm-btn" onClick={load} disabled={loading}>
              {loading ? '…' : '↻ REFRESH'}
            </button>
          </div>
        </div>

        {err && (
          <div className="gm-card" style={{ padding: '12px 14px', marginBottom: 16,
                                            borderColor: 'rgba(255,93,125,.35)', color: '#ff8299', fontSize: 11 }}>
            {err}
          </div>
        )}

        <SectionLabel note="· every action below is authorized by the server, not by this screen">
          ORGANIZATION COMMAND TABLE
        </SectionLabel>

        <OrgCommandTable
          key={params.get('filter') || 'all'}
          orgs={orgs} customers={customers} billingRows={billingRows}
          loading={loading} busyId={busy} initialFilter={params.get('filter')}
          onEnter={askEnter} onSuspend={askSuspend} onGo={go}
        />

        <p style={{ marginTop: 16, fontSize: 10, color: T.dim, lineHeight: 1.7 }}>
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
