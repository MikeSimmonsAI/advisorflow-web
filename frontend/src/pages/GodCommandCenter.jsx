/**
 * GodCommandCenter — AdvisorFlow owner control plane.
 *
 * Visual baseline: AdvisorFlow_GOD_MODE_Command_Center_V2.html (approved Aug 25 2026).
 * Wrapped by GodModeLayout (GodShell) in App.jsx, which supplies `onEnterOrg`.
 *
 * ── Data contracts actually used (all pre-existing, none invented) ──
 *   GET  /god/stats                      platform + org + user + lead totals
 *   GET  /god/platforms                  per-platform rollup
 *   GET  /god/orgs?limit=200             _enrich_org records incl. real health_score
 *   GET  /billing/all                    god-only; fails until Stripe is configured
 *   POST /god/orgs/{id}/impersonate      enter org  → hands orgSession to GodShell
 *   POST /god/orgs/{id}/suspend          suspend
 *   POST /god/orgs/{id}/reactivate       reactivate
 *
 * Rule: if a value has no backend source it renders "no source". Never a placeholder.
 */
import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'

import GodStyles from './god/GodStyles'
import { T } from './god/godTheme'
import { SectionLabel } from './god/StatusBadge'
import PlatformHealthStrip from './god/PlatformHealthStrip'
import RevenueMetrics from './god/RevenueMetrics'
import ExceptionQueue, { buildExceptions } from './god/ExceptionQueue'
import HierarchyTree from './god/HierarchyTree'
import GodTools from './god/GodTools'

export default function GodCommandCenter({ onEnterOrg }) {
  const navigate = useNavigate()

  const [stats, setStats]         = useState(null)
  const [platforms, setPlatforms] = useState([])
  const [orgs, setOrgs]           = useState([])
  // /billing/all returns { orgs: [...] } with NO monetary figures, and succeeds
  // even without Stripe. null = never reached it; [] = reached, nothing billable.
  const [billingRows, setBillingRows] = useState(null)
  const [loading, setLoading]     = useState(true)
  const [err, setErr]             = useState('')
  const [busy, setBusy]           = useState('')

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    // Each call is independent — one failing must not blank the whole page.
    const [s, p, o] = await Promise.allSettled([
      api.get('/god/stats'),
      api.get('/god/platforms'),
      api.get('/god/orgs?limit=200'),
    ])
    if (s.status === 'fulfilled') setStats(s.value)
    if (p.status === 'fulfilled') setPlatforms(Array.isArray(p.value) ? p.value : [])
    if (o.status === 'fulfilled') setOrgs(o.value?.orgs || [])
    if (s.status === 'rejected' && o.status === 'rejected') {
      setErr('Could not reach the God Mode API. Check that the backend is awake.')
    }
    // Reports payment-method coverage per org. Carries no amounts, so a success
    // here is NOT evidence that billing works.
    try {
      const b = await api.get('/billing/all')
      setBillingRows(Array.isArray(b?.orgs) ? b.orgs : [])
    } catch {
      setBillingRows(null)
    }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const exceptions = buildExceptions({ orgs, billingRows })
  const criticalCount = exceptions.filter(x => x.sev === 'critical').length

  async function enterOrg(org) {
    if (!org?.id) return
    setBusy(org.id)
    try {
      const session = await api.post(`/god/orgs/${org.id}/impersonate`)
      onEnterOrg?.(session)                    // GodShell renders the persistent banner
      navigate('/')                            // land in the tenant view
    } catch (e) {
      setErr(e?.message || 'Could not enter organization.')
    } finally { setBusy('') }
  }

  function handleException(x) {
    if (x.org) return enterOrg(x.org)
    if (x.to) return navigate(x.to)
  }

  function handleTool(t) {
    if (t.key === 'scraper') return navigate('/scraper')
    if (t.key === 'create')  return navigate('/god/organizations')
    if (t.key === 'suspend') return navigate('/god/organizations')
    if (t.key === 'enter')   return navigate('/god/organizations')
  }

  return (
    <div className="gm-scope" style={{ minHeight: '100%' }}>
      <GodStyles />
      <div className="gm-grid-overlay" />

      <div style={{ position: 'relative', zIndex: 1, maxWidth: 1540, margin: '0 auto', padding: '24px 26px 80px' }}>

        {/* ── Executive header ── */}
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 24, alignItems: 'flex-start', padding: '8px 2px 22px', flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 9, letterSpacing: '.19em', color: '#6083a5', fontWeight: 800, marginBottom: 8 }}>
              ADVISORFLOW / OWNER CONTROL PLANE
            </div>
            <h1 style={{ margin: 0, color: '#fff', fontSize: 30, letterSpacing: '-.04em', lineHeight: 1 }}>
              God Mode Command Center
            </h1>
            <p style={{ margin: '9px 0 0', color: '#758ba4', fontSize: 12, maxWidth: 760 }}>
              System-wide operations, revenue health, organization control and privileged actions from one surface.
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
            <button className="gm-btn gm-gold-btn" onClick={() => navigate('/god/organizations')}>+ NEW ORG</button>
            <button className="gm-btn" onClick={load} disabled={loading}>{loading ? '…' : '↻ REFRESH'}</button>
          </div>
        </div>

        {err && (
          <div className="gm-card" style={{ padding: '12px 14px', marginBottom: 16, borderColor: 'rgba(255,93,125,.35)', color: '#ff8299', fontSize: 11 }}>
            {err}
          </div>
        )}

        <PlatformHealthStrip stats={stats} orgs={orgs} criticalCount={criticalCount} loading={loading} />

        {/* ── BAND 1 — Revenue & Accounts ── */}
        <div style={{ marginBottom: 30 }}>
          <SectionLabel>REVENUE &amp; ACCOUNTS</SectionLabel>
          <RevenueMetrics
            orgs={orgs} billingRows={billingRows}
            loading={loading} onDrill={() => navigate('/god/organizations')}
          />
          <ExceptionQueue items={exceptions} onAction={handleException} loading={loading} />
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 10, color: T.dim, marginTop: 14 }}>
            <span><i style={{ width: 9, height: 9, borderRadius: 2, background: T.teal, display: 'inline-block', marginRight: 6 }} />live from your database</span>
            <span><i style={{ width: 9, height: 9, borderRadius: 2, background: '#243a52', display: 'inline-block', marginRight: 6 }} />no source yet — needs the billing build</span>
            <span><i style={{ width: 9, height: 9, borderRadius: 2, background: T.amber, display: 'inline-block', marginRight: 6 }} />real, and needs your decision</span>
          </div>
        </div>

        {/* ── BAND 2 — Hierarchy ── */}
        <div style={{ marginBottom: 30 }}>
          <SectionLabel note={loading ? '' : `· click an organization to enter it · ${platforms.length} platforms · ${orgs.filter(o => o.id !== 'org-god-platform').length} orgs · ${stats?.total_users ?? '—'} users`}>
            HIERARCHY
          </SectionLabel>
          <HierarchyTree
            platforms={platforms} orgs={orgs} stats={stats}
            onOpenOrg={enterOrg} loading={loading}
          />
          {busy && <div style={{ marginTop: 10, fontSize: 11, color: T.amber }}>Opening organization session…</div>}
        </div>

        {/* ── BAND 3 — God Tools ── */}
        <div>
          <SectionLabel note="· never exposed at organization level">GOD TOOLS</SectionLabel>
          <GodTools onLaunch={handleTool} />
        </div>

      </div>
    </div>
  )
}
