/**
 * GOD MODE COMMAND CENTER — the AdvisorFlow owner control plane.
 *
 * Visual target: AdvisorFlow_God_Mode_Redesign.html (approved Aug 27 2026).
 * Wrapped by GodModeLayout (GodShell) in App.jsx.
 *
 * ── Data contracts actually used ───────────────────────────────────────────
 *   GET  /god/stats                        platform / org / user / lead totals
 *   GET  /god/platform-health              six real subsystem conditions
 *   GET  /god/orgs?limit=200               health, activity, message volume
 *   GET  /god/ops/customer-organizations   package, implementation, brand
 *   GET  /billing/all                      payment-method coverage per org
 *   GET  /admin/dashboard/funnel           booked / sold across every org
 *   POST /god/platform/context/customer/{id}   ENTER ORGANIZATION
 *   POST /god/orgs/{id}/suspend · /reactivate
 *
 * ── Two rules this file is built around ───────────────────────────────────
 * 1. A VALUE WITH NO BACKEND SOURCE RENDERS "no source". Never a placeholder,
 *    never a plausible-looking number. MRR is blank on purpose.
 * 2. ENTERING AN ORGANIZATION USES THE ONE SHARED HELPER. This screen used to
 *    call POST /god/orgs/{id}/impersonate, which sets no client context — so
 *    "Enter" landed the owner in the tenant app holding no organization at
 *    all. It now calls enterCustomer(), the same function Customer 360 uses.
 *
 * Every request is independent: one failing narrows the page, it never blanks
 * it.
 */
import { useEffect, useState, useCallback } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { api } from '../api/client'

import GodStyles from './god/GodStyles'
import { T } from './god/godTheme'
import { SectionLabel } from './god/StatusBadge'
import ExecutiveSummary from './god/ExecutiveSummary'
import PlatformHealth from './god/PlatformHealth'
import ExceptionQueue, { buildExceptions } from './god/ExceptionQueue'
import OrgCommandTable from './god/OrgCommandTable'
import RevenueMetrics from './god/RevenueMetrics'
import GodTools from './god/GodTools'
import ProductStatus from './god/ProductStatus'
import ConfirmDialog from './god/ConfirmDialog'
import { enterCustomer } from './god/enterCustomer'

function scrollToId(hash) {
  const el = document.getElementById(hash.replace('#', ''))
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

export default function GodCommandCenter() {
  const navigate = useNavigate()
  const location = useLocation()

  const [stats, setStats]         = useState(null)
  const [health, setHealth]       = useState(null)
  const [healthErr, setHealthErr] = useState('')
  const [orgs, setOrgs]           = useState([])
  const [customers, setCustomers] = useState([])
  const [funnel, setFunnel]       = useState(null)
  // /billing/all returns { orgs: [...] } with NO monetary figures and succeeds
  // even without Stripe. null = never reached it; [] = reached, nothing there.
  const [billingRows, setBillingRows] = useState(null)
  const [loading, setLoading]     = useState(true)
  const [err, setErr]             = useState('')
  const [busy, setBusy]           = useState('')
  const [confirm, setConfirm]     = useState(null)

  const load = useCallback(async () => {
    setLoading(true); setErr(''); setHealthErr('')
    const [s, h, o, c, f, b] = await Promise.allSettled([
      api.get('/god/stats'),
      api.get('/god/platform-health'),
      api.get('/god/orgs?limit=200'),
      api.get('/god/ops/customer-organizations'),
      // Platform-wide on purpose. Every other tile in the executive summary is,
      // and if the owner happens to be inside a customer this one would
      // otherwise report that customer's bookings next to the whole estate's
      // lead and user counts.
      api.get('/admin/dashboard/funnel', { noOrgContext: true }),
      api.get('/billing/all'),
    ])
    if (s.status === 'fulfilled') setStats(s.value)
    if (h.status === 'fulfilled') setHealth(h.value)
    else setHealthErr(h.reason?.message || 'unavailable')
    if (o.status === 'fulfilled') setOrgs(o.value?.orgs || [])
    if (c.status === 'fulfilled') setCustomers(c.value?.organizations || [])
    if (f.status === 'fulfilled') setFunnel(f.value)
    setBillingRows(b.status === 'fulfilled' && Array.isArray(b.value?.orgs) ? b.value.orgs : null)
    if (s.status === 'rejected' && o.status === 'rejected') {
      setErr('Could not reach the God Mode API. Check that the backend is awake.')
    }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  // The rail links to sections of THIS page ("/god#platform-health"). React
  // Router changes the hash but does not scroll, and on a cold load the target
  // does not exist yet — hence the dependency on `loading` as well as the hash.
  useEffect(() => {
    if (!location.hash) return
    const t = setTimeout(() => scrollToId(location.hash), 60)
    return () => clearTimeout(t)
  }, [location.hash, loading])

  const exceptions = buildExceptions({ orgs, customers, billingRows })
  const needsDecision = exceptions.filter(x => x.sev === 'critical' || x.sev === 'high').length
  const criticalCount = exceptions.filter(x => x.sev === 'critical').length

  // ── navigation, including in-page anchors from tiles and tools ──────────
  function go(to) {
    if (!to) return
    if (to.startsWith('#')) return scrollToId(to)
    navigate(to)
  }

  // ── ENTER ORGANIZATION — confirmed, then the one shared helper ──────────
  function askEnter(org) {
    if (!org?.id) return
    setConfirm({
      kind: 'enter', org,
      tone: 'gold',
      eyebrow: '⚡ ENTER ORGANIZATION — AUDITED',
      title: org.name,
      body: 'You will operate inside this customer until you leave. The server '
          + 'writes an audit row naming you, the customer and the time, and it '
          + 'verifies that no membership was created — you stay yourself. Every '
          + 'screen will carry a banner saying whose records you are changing.',
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
        navigate('/')                       // land in the tenant app, in context
        return
      }
      await api.post(`/god/orgs/${org.id}/${kind}`, {})
      setConfirm(null)
      await load()
    } catch (e) {
      setErr(e?.message || 'The action was refused.')
      setConfirm(null)
    } finally {
      setBusy('')
    }
  }

  function handleException(x) {
    if (x.org) return askEnter(x.org)
    if (x.to) return go(x.to)
  }

  return (
    <div className="gm-scope" style={{ minHeight: '100%' }}>
      <GodStyles />
      <div className="gm-grid-overlay" />

      <div style={{ position: 'relative', zIndex: 1, maxWidth: 1560, margin: '0 auto', padding: '24px 26px 80px' }}>

        {/* ── Executive header ── */}
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 24,
                      alignItems: 'flex-start', padding: '8px 2px 20px', flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 9, letterSpacing: '.19em', color: '#6083a5', fontWeight: 800, marginBottom: 8 }}>
              ADVISORFLOW / OWNER CONTROL PLANE
            </div>
            <h1 style={{ margin: 0, color: '#fff', fontSize: 31, letterSpacing: '-.04em', lineHeight: 1 }}>
              God Mode Command Center
            </h1>
            <p style={{ margin: '9px 0 0', color: '#758ba4', fontSize: 12, maxWidth: 780 }}>
              System-wide health, revenue risk, customer operations and owner
              interventions from one surface.
            </p>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <div style={{
              border: `1px solid ${criticalCount ? 'rgba(255,93,125,.3)' : 'rgba(35,239,178,.25)'}`,
              background: criticalCount ? 'rgba(63,12,24,.45)' : 'rgba(9,49,38,.40)',
              color: criticalCount ? '#ff8299' : '#63f2c6',
              padding: '7px 10px', borderRadius: 999, fontSize: 9, fontWeight: 800, letterSpacing: '.08em',
            }}>
              <span style={{
                width: 7, height: 7, borderRadius: '50%', display: 'inline-block', marginRight: 7,
                background: criticalCount ? T.red : T.teal,
                boxShadow: `0 0 12px ${criticalCount ? T.red : T.teal}`,
              }} />
              {loading ? 'LOADING' : criticalCount ? `${criticalCount} CRITICAL` : 'SYSTEM ONLINE'}
            </div>
            <button className="gm-btn" onClick={() => navigate('/god/audit')}>AUDIT LOG</button>
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

        {/* ── BAND 1 — executive summary ── */}
        <ExecutiveSummary
          stats={stats} orgs={orgs} funnel={funnel}
          criticalCount={needsDecision} loading={loading} onGo={go}
        />

        {/* ── BAND 2 — platform health + owner action queue ── */}
        <div id="platform-health" style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1.25fr) minmax(0,.75fr)',
                      gap: 14, marginBottom: 28 }}
             className="gm-band2">
          <div>
            <SectionLabel note="· live subsystem conditions, computed server-side">
              PLATFORM HEALTH
            </SectionLabel>
            <PlatformHealth data={health} loading={loading} error={healthErr} onGo={go} />
          </div>
          <div id="owner-action-queue">
            <SectionLabel note={loading ? '' : `· ${exceptions.length} open`}>
              OWNER ACTION QUEUE
            </SectionLabel>
            <ExceptionQueue
              items={exceptions} onAction={handleException}
              loading={loading} busyId={busy}
            />
          </div>
        </div>

        {/* ── BAND 3 — organization command table ── */}
        <div style={{ marginBottom: 28 }}>
          <SectionLabel note="· enter, inspect, price, suspend — every action is server-authorized">
            ORGANIZATION COMMAND TABLE
          </SectionLabel>
          <OrgCommandTable
            orgs={orgs} customers={customers} billingRows={billingRows}
            loading={loading} busyId={busy}
            onEnter={askEnter} onSuspend={askSuspend} onGo={go}
          />
        </div>

        {/* ── BAND 4 — revenue & accounts, and what is finished ── */}
        <div style={{ marginBottom: 28 }}>
          <SectionLabel>REVENUE &amp; ACCOUNTS</SectionLabel>
          <RevenueMetrics
            orgs={orgs} billingRows={billingRows}
            loading={loading} onDrill={() => navigate('/god/organizations')}
          />
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 10, color: T.dim, margin: '4px 0 22px' }}>
            <span><i style={{ width: 9, height: 9, borderRadius: 2, background: T.teal, display: 'inline-block', marginRight: 6 }} />live from your database</span>
            <span><i style={{ width: 9, height: 9, borderRadius: 2, background: '#243a52', display: 'inline-block', marginRight: 6 }} />no source yet — needs the billing build</span>
            <span><i style={{ width: 9, height: 9, borderRadius: 2, background: T.amber, display: 'inline-block', marginRight: 6 }} />real, and needs your decision</span>
          </div>
          <div id="product-status">
            <SectionLabel note="· what is finished, and what is next">PRODUCT STATUS</SectionLabel>
            <ProductStatus onGo={go} />
          </div>
        </div>

        {/* ── BAND 5 — God tools ── */}
        <div>
          <SectionLabel note="· never exposed at organization level">GOD TOOLS</SectionLabel>
          <GodTools onLaunch={t => go(t.to)} />
        </div>
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
