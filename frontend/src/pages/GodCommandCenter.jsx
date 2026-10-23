/**
 * GOD MODE › DASHBOARD — the owner's one-screen view of the business.
 *
 * Oct 22 2026 redesign: a card dashboard instead of five stacked bands.
 *
 *   KPIs              GET /god/stats, /god/dashboard/trends
 *   Needs you         buildExceptions() over /god/orgs, /god/ops/customer-organizations,
 *                     /billing/all — top 5, "View all" opens the full list
 *   Platform health   GET /god/platform-health + AI Workforce from
 *                     GET /god/ai-workforce/control
 *   Watchlist         lowest-health organizations, Enter / Open / Suspend
 *   Activity          GET /god/dashboard/trends — real and test counted apart
 *
 * RULES KEPT FROM THE OLD PAGE. A value with no source renders "—", never a
 * plausible number. Entering an organization is confirmed and goes through the
 * one shared helper, enterCustomer(). Every request is independent: one
 * failing narrows the page, it never blanks it.
 *
 * REMOVED FROM THIS PAGE, NOT FROM THE PRODUCT. The full organization table is
 * Organizations › Organizations. The Sep 12 roadmap board is Launch Board ›
 * Roadmap archive. God Tools are the six sections of the rail.
 */
import { useEffect, useState, useCallback } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { api } from '../api/client'

import GodStyles from './god/GodStyles'
import { buildExceptions } from './god/ExceptionQueue'
import { buildRows } from './god/OrgCommandTable'
import ConfirmDialog from './god/ConfirmDialog'
import { enterCustomer } from './god/enterCustomer'
import {
  Page, Card, Kpi, Pill, Loading, ErrorState, Empty, ActionLink, ActionQueue,
  HealthRow, Spark, ago,
} from './god/ui/GodKit'

const SEV_ORDER = { critical: 0, high: 1, medium: 2, info: 3 }
const fmt = n => (n === null || n === undefined ? null : Number(n).toLocaleString('en-US'))

function aiHealth (ai) {
  if (!ai) return null
  let status = 'ok'
  if (ai.stopped) status = 'bad'
  else if (!ai.worker?.running || (ai.problems || []).length) status = 'warn'
  else if (!ai.accounts_on) status = 'off'
  const proven = (ai.jobs || []).filter(j => j.proven).length
  return {
    key: 'ai', label: 'AI Workforce', status,
    statusLabel: { ok: 'Working', warn: 'Needs attention', bad: 'Stopped', off: 'Off' }[status],
    headline: ai.headline + (ai.jobs ? ` ${proven} of ${ai.jobs.length} jobs proven in test mode.` : ''),
    to: '/god/ai',
  }
}

function TrendCard ({ title, series, color, unit = '' }) {
  if (!series) return <Card title={title}><Loading /></Card>
  if (series.unavailable) return <Card title={title}><Empty>Unable to count right now.</Empty></Card>
  const ch = series.vs_previous
  return (
    <Card title={title} note={`last ${series.real.length} days`}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span className="gk-kpi-value">{fmt(series.real_total)}{unit}</span>
        {ch ? (
          <Pill tone={ch.pct >= 0 ? 'good' : 'warn'} title={`Previous period: ${ch.previous}`}>
            {ch.pct >= 0 ? '+' : ''}{ch.pct}% vs previous
          </Pill>
        ) : <span className="gk-row-meta">no previous period to compare</span>}
      </div>
      <Spark values={series.real} label={`${title}, real, daily`} color={color} />
      <div className="gk-row-sub">
        Real only. {series.test_total ? `${fmt(series.test_total)} test/practice not included.` : 'No test activity in this window.'}
      </div>
    </Card>
  )
}

export default function GodCommandCenter () {
  const navigate = useNavigate()
  const location = useLocation()

  const [stats, setStats] = useState(null)
  const [health, setHealth] = useState(null)
  const [healthErr, setHealthErr] = useState('')
  const [orgs, setOrgs] = useState([])
  const [customers, setCustomers] = useState([])
  const [billingRows, setBillingRows] = useState(null)
  const [trends, setTrends] = useState(null)
  const [ai, setAi] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')
  const [confirm, setConfirm] = useState(null)
  const [showAll, setShowAll] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setErr(''); setHealthErr('')
    const opt = { noOrgContext: true }
    const [s, h, o, c, b, t, a] = await Promise.allSettled([
      api.get('/god/stats', opt),
      api.get('/god/platform-health', opt),
      api.get('/god/orgs?limit=200', opt),
      api.get('/god/ops/customer-organizations', opt),
      api.get('/billing/all', opt),
      api.get('/god/dashboard/trends?days=14', opt),
      api.get('/god/ai-workforce/control', opt),
    ])
    if (s.status === 'fulfilled') setStats(s.value)
    if (h.status === 'fulfilled') setHealth(h.value)
    else setHealthErr(h.reason?.message || 'unavailable')
    if (o.status === 'fulfilled') setOrgs(o.value?.orgs || [])
    if (c.status === 'fulfilled') setCustomers(c.value?.organizations || [])
    setBillingRows(b.status === 'fulfilled' && Array.isArray(b.value?.orgs) ? b.value.orgs : null)
    setTrends(t.status === 'fulfilled' ? t.value : { failed: true })
    setAi(a.status === 'fulfilled' ? a.value : null)
    if (s.status === 'rejected' && o.status === 'rejected') {
      setErr('Could not reach the God Mode API. Check that the backend is awake.')
    }
    setLoading(false)
  }, [])
  useEffect(() => { load() }, [load])

  // "/god#platform-health" (old System Health link) still lands on the card.
  useEffect(() => {
    if (!location.hash) return
    const tm = setTimeout(() => {
      const el = document.getElementById(location.hash.slice(1))
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 80)
    return () => clearTimeout(tm)
  }, [location.hash, loading])

  // Demonstration tenants are not customers: they never raise an attention
  // item and never appear on the watchlist.
  const realCustomers = customers.filter(c => !c.is_demo)
  const exceptions = buildExceptions({ orgs, customers: realCustomers, billingRows })
    .slice().sort((x, y) => (SEV_ORDER[x.sev] ?? 9) - (SEV_ORDER[y.sev] ?? 9))
  const decisions = exceptions.filter(x => x.sev === 'critical' || x.sev === 'high').length

  const rows = buildRows({ orgs, customers: realCustomers, billingRows })
  const watch = rows
    .filter(r => !r.is_demo && !r._org?.is_demo)
    .slice()
    .sort((x, y) => (x.health_score ?? 101) - (y.health_score ?? 101))
    .slice(0, 6)

  const healthRows = [...(health?.sections || [])]
  const aiRow = aiHealth(ai)
  if (aiRow) healthRows.push(aiRow)

  // ── confirmed actions (unchanged behaviour) ──────────────────────────────
  function askEnter (org) {
    if (!org?.id) return
    setConfirm({
      kind: 'enter', org, tone: 'gold',
      eyebrow: '⚡ ENTER ORGANIZATION — AUDITED',
      title: org.name,
      body: 'You will operate inside this customer until you leave. The server '
          + 'writes an audit row naming you, the customer and the time, and it '
          + 'verifies that no membership was created — you stay yourself. Every '
          + 'screen will carry a banner saying whose records you are changing.',
      confirmLabel: 'ENTER',
    })
  }
  function askSuspend (org, mode) {
    setConfirm({
      kind: mode, org,
      tone: mode === 'suspend' ? 'danger' : 'blue',
      eyebrow: mode === 'suspend' ? '⚠ SUSPEND ORGANIZATION' : '✓ REACTIVATE ORGANIZATION',
      title: org.name,
      body: mode === 'suspend'
        ? 'Everyone in this organization will be unable to sign in. Their data is '
        + 'untouched and this is reversible from the same place.'
        : 'Access is restored for every user in this organization.',
      confirmLabel: mode === 'suspend' ? 'SUSPEND' : 'REACTIVATE',
    })
  }
  async function runConfirm () {
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
    } finally {
      setBusy('')
    }
  }
  function handleAttention (x) {
    if (x.org) return askEnter(x.org)
    if (x.to) navigate(x.to)
  }

  const t = trends && !trends.failed ? trends : null
  const realOrgsWithLeads = stats?.active_orgs

  return (
    <>
      <GodStyles />
      <Page eyebrow="Dashboard" title="Command center"
        sub="What needs you, how the platform is running, and what the business did — real numbers only."
        actions={<>
          <ActionLink to="/god/customers/new" primary>+ New organization</ActionLink>
          <ActionLink onClick={load} disabled={loading}>{loading ? 'Loading…' : 'Refresh'}</ActionLink>
        </>}>

        {err ? <ErrorState onRetry={load}>{err}</ErrorState> : null}

        {/* ── KPIs ── */}
        <div className="gk-kpis">
          <Kpi label="Brands" value={fmt(stats?.total_platforms)} sub="platforms on the system"
            onClick={() => navigate('/god/workspaces')} />
          <Kpi label="Organizations" value={fmt(stats?.total_orgs)}
            sub={realOrgsWithLeads !== undefined ? `${fmt(realOrgsWithLeads)} with leads · demos excluded` : 'demos excluded'}
            onClick={() => navigate('/god/organizations')} />
          <Kpi label="Users" value={fmt(stats?.total_users)} sub="all accounts"
            onClick={() => navigate('/god/users-all')} />
          <Kpi label="New leads" value={fmt(t?.leads?.real_total)} sub="real, last 14 days" />
          <Kpi label="Appointments" value={fmt(t?.appointments_next_7_days)} sub="booked, next 7 days" />
          <Kpi label="Needs your decision" value={loading ? null : decisions}
            tone={decisions ? 'red' : undefined} sub="critical and high items"
            onClick={() => document.getElementById('owner-action-queue')?.scrollIntoView({ behavior: 'smooth' })} />
        </div>

        <div className="gk-two" style={{ marginTop: 14 }}>
          {/* ── Needs you ── */}
          <div id="owner-action-queue">
            <Card title="Needs you"
              note={loading ? null : `${exceptions.length} open · most urgent first`}
              action={exceptions.length > 5
                ? <button type="button" className="gk-link" onClick={() => setShowAll(v => !v)}>
                    {showAll ? 'Show top 5' : `View all ${exceptions.length}`}
                  </button>
                : null}>
              {loading ? <Loading />
                : <ActionQueue items={exceptions} limit={showAll ? 0 : 5} onAction={handleAttention}
                    empty="Nothing needs you right now." />}
            </Card>
          </div>

          {/* ── Platform health ── */}
          <div id="platform-health">
            <Card title="Platform health"
              note={health?.overall ? `${health.overall.overall_label} · worst condition wins` : 'Checked by the server'}>
              {healthErr && !healthRows.length ? <ErrorState onRetry={load}>Health could not be checked: {healthErr}</ErrorState>
                : loading && !healthRows.length ? <Loading />
                  : <ul className="gk-list">{healthRows.map(r => (
                    <HealthRow key={r.key} label={r.label} status={r.status}
                      statusLabel={r.statusLabel || r.severity_label} headline={r.headline} to={r.to} />))}</ul>}
            </Card>
          </div>
        </div>

        {/* ── Watchlist ── */}
        <div style={{ marginTop: 14 }}>
          <Card title="Organization watchlist" note="lowest health first · demonstration accounts left out"
            action={<ActionLink to="/god/organizations">All organizations</ActionLink>}>
            {loading ? <Loading /> : !watch.length ? <Empty>No organizations yet.</Empty> : (
              <ul className="gk-list">
                {watch.map(r => (
                  <li key={r.id} className="gk-row">
                    <div className="gk-row-main">
                      <div className="gk-row-title">
                        {r.name}{' '}
                        {!r.is_active ? <Pill tone="bad">Suspended</Pill> : null}
                        {r._orphan ? <Pill tone="warn">Not in customer list</Pill> : null}
                      </div>
                      <div className="gk-row-sub">
                        {[r.platform_name, r.package_name || 'no package',
                          `${fmt(r.lead_count) ?? '—'} leads`, `${fmt(r.messages_30d) ?? '—'} messages in 30 days`,
                          r.last_activity ? `active ${ago(r.last_activity)}` : 'no recorded activity']
                          .filter(Boolean).join(' · ')}
                      </div>
                    </div>
                    {r.health_score !== undefined && r.health_score !== null ? (
                      <Pill tone={r.health_score >= 80 ? 'good' : r.health_score >= 60 ? 'warn' : 'bad'}
                        title="Health score, computed by the server">Health {r.health_score}</Pill>
                    ) : null}
                    <ActionLink to={`/god/customers/${r.id}`}>Open</ActionLink>
                    {r.is_active
                      ? <ActionLink primary onClick={() => askEnter(r._org)} disabled={busy === r.id}>Enter</ActionLink>
                      : <ActionLink onClick={() => askSuspend(r._org, 'reactivate')} disabled={busy === r.id}>Reactivate</ActionLink>}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        {/* ── Activity ── */}
        <div className="gk-eyebrow" style={{ marginTop: 22 }}>Activity · real accounts only</div>
        {trends?.failed ? <ErrorState onRetry={load}>Activity could not be counted right now.</ErrorState> : (
          <div className="gk-four">
            <TrendCard title="New leads" series={t?.leads} color="var(--gm-blue)" />
            <TrendCard title="Messages sent" series={t?.messages_sent} color="var(--gm-teal)" />
            <TrendCard title="Replies" series={t?.replies} color="var(--gm-gold)" />
            <TrendCard title="Appointments booked" series={t?.appointments_booked} color="var(--gm-teal)" />
          </div>
        )}
        {t ? (
          <div className="gk-two" style={{ marginTop: 14 }}>
            <Card title="Message delivery">
              {t.delivery_rate?.pct !== null && t.delivery_rate?.pct !== undefined ? (
                <>
                  <span className="gk-kpi-value">{t.delivery_rate.pct}%</span>
                  <div className="gk-row-sub">{fmt(t.delivery_rate.delivered)} delivered of {fmt(t.delivery_rate.sent)} sent (real, 14 days).</div>
                </>
              ) : (
                <Empty>Not enough to measure. {t.delivery_rate?.why} {fmt(t.delivery_rate?.sent) ?? 0} sent so far.</Empty>
              )}
            </Card>
            <Card title="AI work, last 14 days" action={<ActionLink to="/god/ai">AI Workforce</ActionLink>}>
              {t.ai_runs?.unavailable ? <Empty>Unable to count AI runs right now.</Empty> : (
                <ul className="gk-list">
                  <li className="gk-row"><div className="gk-row-main"><div className="gk-row-title">Real customers</div>
                    <div className="gk-row-sub">{t.ai_runs.real.completed} finished · {t.ai_runs.real.failed} failed</div></div>
                    <Pill tone="info">Live</Pill></li>
                  <li className="gk-row"><div className="gk-row-main"><div className="gk-row-title">Practice (test mode)</div>
                    <div className="gk-row-sub">{t.ai_runs.practice.completed} finished · {t.ai_runs.practice.failed} failed · nothing sent</div></div>
                    <Pill tone="test">Test</Pill></li>
                </ul>
              )}
            </Card>
          </div>
        ) : null}
        {t?.note ? <p className="gk-row-sub" style={{ marginTop: 10 }}>{t.note}</p> : null}
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
