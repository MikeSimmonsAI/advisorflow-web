/**
 * GOD MODE › DASHBOARD — the OWNER DESK.
 *
 * Oct 26 2026 (UI-REDESIGN, issue #411): the approved Owner Desk layout —
 * Your Programs · Needs Your Attention · Quick Actions · Platform Status ·
 * Platform Tools · Recent Activity — on a light page with a navy rail. Same
 * data, same confirmed actions; the activity charts moved into a closed
 * "Activity trends" panel at the bottom instead of leading the page.
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
 *   Recent activity   GET /god/ops/audit?limit=6 — the owner-action audit trail
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
import { Link, useNavigate, useLocation } from 'react-router-dom'
import { api } from '../api/client'

import GodStyles from './god/GodStyles'
import { buildExceptions } from './god/ExceptionQueue'
import { buildRows } from './god/OrgCommandTable'
import ConfirmDialog from './god/ConfirmDialog'
import { enterCustomer } from './god/enterCustomer'
import {
  Page, Card, Pill, Loading, ErrorState, Empty, ActionLink, ActionQueue,
  Spark, ago, Expandable, Section, Strip, ToolCard, ProgramTile, QuickActions, StatusStrip, Icon,
} from './god/ui/GodKit'
import { getCurrentUser } from '../api/client'

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

function ActionLinkInline ({ to }) {
  return <Link className="gk-viewlink gk-headlink" to={to}>View all <Icon name="arrowRight" size={14} /></Link>
}

function TrendCard ({ title, series, color, unit = '' }) {
  if (!series) return <Card title={title}><Loading /></Card>
  if (series.unavailable) return <Card title={title}><Empty>Unable to count right now.</Empty></Card>
  const ch = series.vs_previous
  return (
    <Card title={title} note={`last ${series.real.length} days`}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span className="gk-kpi-value">{fmt(series.real_total)}{unit}</span>
        {ch && ch.pct !== null && ch.pct !== undefined ? (
          <Pill tone={ch.pct >= 0 ? 'good' : 'warn'} title={`Previous period: ${ch.previous}`}>
            {ch.pct >= 0 ? '+' : ''}{ch.pct}% vs previous
          </Pill>
        ) : ch ? (
          <span className="gk-row-meta" title="Too few in the previous period for a meaningful percentage">
            previous period: {fmt(ch.previous)}
          </span>
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
  const [audit, setAudit] = useState(null)
  const [revenue, setRevenue] = useState(null)

  const load = useCallback(async () => {
    setLoading(true); setErr(''); setHealthErr('')
    const opt = { noOrgContext: true }
    const [s, h, o, c, b, t, a, au, rv] = await Promise.allSettled([
      api.get('/god/stats', opt),
      api.get('/god/platform-health', opt),
      api.get('/god/orgs?limit=200', opt),
      api.get('/god/ops/customer-organizations', opt),
      api.get('/billing/all', opt),
      api.get('/god/dashboard/trends?days=14', opt),
      api.get('/god/ai-workforce/control', opt),
      api.get('/god/ops/audit?limit=8', opt),
      api.get('/god/billing/revenue', opt),
    ])
    if (s.status === 'fulfilled') setStats(s.value)
    if (h.status === 'fulfilled') setHealth(h.value)
    else setHealthErr(h.reason?.message || 'unavailable')
    if (o.status === 'fulfilled') setOrgs(o.value?.orgs || [])
    if (c.status === 'fulfilled') setCustomers(c.value?.organizations || [])
    setBillingRows(b.status === 'fulfilled' && Array.isArray(b.value?.orgs) ? b.value.orgs : null)
    setTrends(t.status === 'fulfilled' ? t.value : { failed: true })
    setAi(a.status === 'fulfilled' ? a.value : null)
    setAudit(au.status === 'fulfilled' && Array.isArray(au.value?.entries) ? au.value.entries : { failed: true })
    setRevenue(rv.status === 'fulfilled' ? rv.value : { failed: true })
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

  // ── Your Programs: every organization the owner runs, real ones first ──
  const byId = Object.fromEntries(rows.map(r => [r.id, r]))
  const programs = orgs.slice().sort((x, y) =>
    (x.is_active === false) - (y.is_active === false) ||
    (!!x.is_demo) - (!!y.is_demo) ||
    String(x.name).localeCompare(String(y.name)))
  const PROGRAM_LIMIT = 8
  const orgName = Object.fromEntries(orgs.map(o => [o.id, o.name]))

  const healthByKey = Object.fromEntries((health?.sections || []).map(x => [x.key, x]))
  const okCount = healthRows.filter(r => ['ok', 'healthy', 'good'].includes(String(r.status).toLowerCase())).length
  const mrr = revenue && !revenue.failed ? revenue.mrr : null
  const mrrValue = mrr && mrr.value_cents !== null && mrr.value_cents !== undefined
    ? '$' + Math.round(mrr.value_cents / 100).toLocaleString('en-US') : null
  const aiJobs = ai?.jobs || []
  const today = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric', year: 'numeric' })

  // Activity dots: colour by what kind of owner action it was.
  const dotOf = a => /delete|suspend|stop|revoke|fail/.test(a) ? 'red'
    : /enter|login|impersonat/.test(a) ? 'orange'
      : /ai|worker|workforce/.test(a) ? 'purple'
        : /bill|price|stripe|invoice/.test(a) ? 'green' : 'blue'

  return (
    <>
      <GodStyles />
      <Page hero eyebrow={<><Icon name="crown" size={16} /> GOD MODE</>} title="Owner Desk"
        sub="Everything you control. All programs, tools and platform operations — simple and powerful."
        actions={<>
          <span className="gk-datechip"><Icon name="calendar" size={16} />{today}</span>
          <ActionLink onClick={load} disabled={loading} small={false}>{loading ? 'Loading…' : 'Refresh'}</ActionLink>
        </>}>

        {err ? <ErrorState onRetry={load}>{err}</ErrorState> : null}

        <Strip cols={6} items={[
          { label: 'Organizations', icon: 'building', color: 'blue', value: fmt(stats?.total_orgs),
            onClick: () => navigate('/god/organizations'),
            sub: stats?.active_orgs !== undefined ? `${fmt(stats.active_orgs)} with leads · demos excluded` : 'demos excluded' },
          { label: 'Total leads', icon: 'chart', color: 'green', value: fmt(stats?.total_leads),
            sub: stats?.new_leads_30d !== undefined ? `+${fmt(stats.new_leads_30d)} in the last 30 days` : null },
          { label: 'AI jobs', icon: 'cpu', color: 'purple', value: ai ? fmt(aiJobs.length) : null,
            onClick: () => navigate('/god/ai'),
            sub: ai ? `${fmt(ai.accounts_on ?? 0)} accounts on · worker ${ai.worker?.running ? 'running' : 'not running'}` : 'unavailable' },
          { label: 'Appointments', icon: 'calendar', color: 'orange', value: fmt(t?.appointments_booked?.real_total),
            sub: 'booked, real, last 14 days' },
          { label: 'Monthly revenue', icon: 'dollar', color: 'red', value: revenue === null ? null : (mrrValue ?? 'Not tracked'),
            onClick: () => navigate('/god/billing'),
            sub: mrrValue ? `${fmt(mrr.priced_organizations ?? 0)} priced subscriptions` : 'no priced subscriptions yet' },
          { label: 'System health', icon: 'pulse', color: 'teal',
            value: health?.overall?.overall_label || (healthRows.length ? `${okCount} / ${healthRows.length}` : null),
            onClick: () => document.getElementById('platform-health')?.scrollIntoView({ behavior: 'smooth' }),
            sub: healthRows.length ? `${okCount} of ${healthRows.length} areas healthy` : null },
        ]} />

        {/* ── Your Programs ── */}
        <div className="gk-section gk-panel">
          <Section icon="file" title="Your Programs" sub="Open any program to manage it and get to work."
            action={<ActionLink to="/god/organizations"><Icon name="grid" size={14} /> View all programs</ActionLink>}>
            {loading && !orgs.length ? <Loading />
              : !programs.length ? <Empty>No organizations yet.</Empty> : (
              <div className="gk-ptiles">
                {programs.slice(0, PROGRAM_LIMIT).map(o => {
                  const r = byId[o.id] || {}
                  const pill = o.is_active === false ? <Pill tone="bad">Suspended</Pill>
                    : o.is_demo ? <Pill tone="test">Demo</Pill>
                      : (r.health_score !== undefined && r.health_score !== null && r.health_score < 60)
                        ? <Pill tone="warn" title="Health score, computed by the server">Needs attention</Pill>
                        : <Pill tone="good">Active</Pill>
                  const n = (v, one, many) => v === undefined || v === null ? null : `${fmt(v)} ${Number(v) === 1 ? one : many}`
                  const line = [n(o.lead_count, 'lead', 'leads'), n(o.user_count, 'person', 'people')].filter(Boolean).join(' · ')
                  return (
                    <ProgramTile key={o.id} name={o.name} industry={o.industry} line={line} pill={pill}
                      to={`/god/customers/${o.id}`}
                      extra={o.is_active === false
                        ? <button type="button" className="gk-viewlink" onClick={() => askSuspend(o, 'reactivate')} disabled={busy === o.id}>Reactivate</button>
                        : <button type="button" className="gk-viewlink" title="Enter this organization (audited, asks first)"
                            onClick={() => askEnter(o)} disabled={busy === o.id}>Enter</button>} />
                  )
                })}
              </div>
            )}
            {programs.length > PROGRAM_LIMIT ? (
              <p className="gk-row-sub" style={{ marginTop: 10 }}>
                Showing {PROGRAM_LIMIT} of {programs.length}. <ActionLink to="/god/organizations">See all {programs.length}</ActionLink>
              </p>
            ) : null}
          </Section>
        </div>

        {/* ── Platform Tools ── */}
        <div className="gk-section gk-panel">
          <Section icon="briefcase" title="Platform Tools" sub="Manage the full platform.">
            <div className="gk-tiles gk-tiles--6">
              <ToolCard icon="building" color="blue" title="Organizations" to="/god/organizations"
                description="Customers, portfolio health and details." cta="Open Organizations" />
              <ToolCard icon="cpu" color="purple" title="AI Workforce" to="/god/ai"
                description="Agents, automations and deployment." cta="Open AI Workforce" />
              <ToolCard icon="rocket" color="slate" title="Launch Board" to="/god/launch-board"
                description="Projects, deployments and system status." cta="Open Launch Board" />
              <ToolCard icon="dollar" color="green" title="Revenue & Billing" to="/god/billing"
                description="Billing, pricing, sales pay and revenue." cta="Open Billing" />
              <ToolCard icon="settings" color="orange" title="Settings & Security" to="/god/settings"
                description="Access, audit, jobs, diagnostics and support." cta="Open Settings" />
              <ToolCard icon="briefcase" color="blue" title="Back Office" to="/sales"
                description="My Day, follow-ups, pipeline and tasks." cta="Open Back Office" />
            </div>
          </Section>
        </div>

        <div className="gk-section gk-desk3">
          {/* ── Needs Your Attention ── */}
          <div id="owner-action-queue">
            <div className="gk-card gk-attn-card">
              <div className="gk-attn-head">
                <span style={{ color: 'var(--gm-ic-red)', display: 'inline-grid' }}><Icon name="alert" size={22} /></span>
                <h2 className="gk-card-title">Needs Your Attention</h2>
                {!loading && exceptions.length ? <span className="gk-count">{exceptions.length}</span> : null}
                {exceptions.length > 5
                  ? <button type="button" className="gk-viewlink gk-headlink" onClick={() => setShowAll(v => !v)}>
                      {showAll ? 'Show top 5' : 'View all'} <Icon name="arrowRight" size={14} />
                    </button>
                  : null}
              </div>
              {loading ? <Loading />
                : <ActionQueue compact items={exceptions} limit={showAll ? 0 : 5} onAction={handleAttention}
                    empty="Nothing needs you right now." />}
            </div>
          </div>

          {/* ── Quick Actions ── */}
          <div className="gk-card">
            <div className="gk-attn-head">
              <span style={{ color: 'var(--gm-ic-blue)', display: 'inline-grid' }}><Icon name="bolt" size={22} /></span>
              <h2 className="gk-card-title" style={{ fontSize: 16 }}>Quick Actions</h2>
            </div>
            <QuickActions items={[
              { label: 'New organization', icon: 'plus', color: 'blue', to: '/god/customers/new' },
              { label: 'Enter a customer', icon: 'login', color: 'blue', to: '/god/customers' },
              { label: 'View reports', icon: 'chart', color: 'blue', to: '/god/revenue-history' },
              { label: 'Manage users', icon: 'users', color: 'blue', to: '/god/access' },
              { label: 'AI Workforce', icon: 'cpu', color: 'blue', to: '/god/ai' },
              { label: 'Launch Board', icon: 'rocket', color: 'blue', to: '/god/launch-board' },
              { label: 'Billing', icon: 'dollar', color: 'green', to: '/god/billing' },
              { label: 'Audit log', icon: 'file', color: 'blue', to: '/god/audit' },
            ]} />
          </div>

          {/* ── Recent Activity ── */}
          <div className="gk-card">
            <div className="gk-attn-head">
              <span style={{ color: 'var(--gm-ic-green)', display: 'inline-grid' }}><Icon name="clock" size={22} /></span>
              <h2 className="gk-card-title" style={{ fontSize: 16 }}>Recent Activity</h2>
              <ActionLinkInline to="/god/audit" />
            </div>
            {audit === null ? <Loading />
              : audit.failed ? <Empty>Recent activity is unavailable right now.</Empty>
                : !audit.length ? <Empty>No owner actions recorded yet. They appear here as you work.</Empty> : (
                  <ul className="gk-activity">
                    {audit.map(e => {
                      const a = String(e.action || '').toLowerCase()
                      return (
                        <li key={e.id}>
                          <span className={`gk-activity-dot gk-activity-dot--${dotOf(a)}`} aria-hidden="true" />
                          <span style={{ minWidth: 0 }}>
                            {String(e.action || '').replace(/[._]/g, ' ')}
                            {e.organization_id && orgName[e.organization_id] ? <> · {orgName[e.organization_id]}</> : null}
                          </span>
                          <span className="gk-activity-when">{ago(e.at)}</span>
                        </li>
                      )
                    })}
                  </ul>
                )}
          </div>
        </div>

        {/* ── Platform Status ── */}
        <div className="gk-section" id="platform-health">
          {healthErr && !healthRows.length ? <ErrorState onRetry={load}>Status could not be checked: {healthErr}</ErrorState>
            : loading && !healthRows.length ? <Card><Loading /></Card>
              : <StatusStrip title="Platform Status" sub="Key services and integrations. Click one for details."
                  items={healthRows.map(r => ({ key: r.key, label: r.label, status: r.status,
                    statusLabel: r.statusLabel || r.severity_label, headline: r.headline, to: r.to }))} />}
        </div>

        {/* ── Activity trends (kept, folded away) ── */}
        <div className="gk-section">
          <Expandable id="activity-trends" header={
            <div>
              <div className="gk-card-title">Activity trends</div>
              <div className="gk-card-note">Leads, messages, replies, bookings and AI work — real accounts only, last 14 days</div>
            </div>}>
            {trends?.failed ? <ErrorState onRetry={load}>Activity could not be counted right now.</ErrorState> : (
              <div className="gk-four">
                <TrendCard title="New leads" series={t?.leads} color="var(--gm-blue)" />
                <TrendCard title="Messages sent" series={t?.messages_sent} color="var(--gm-teal)" />
                <TrendCard title="Replies" series={t?.replies} color="var(--gm-gold)" />
                <TrendCard title="Appointments booked" series={t?.appointments_booked} color="var(--gm-teal)" />
              </div>
            )}
            {t ? (
              <div className="gk-two" style={{ marginTop: 14, marginBottom: 0 }}>
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
          </Expandable>
        </div>
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
