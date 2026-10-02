import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { useWorkspaceAuthority } from '../auth/workspaceAuthority'
// WHICH REPORTS SCREEN THIS WORKSPACE GETS. Same one decision the Overview
// route already takes, for the same reason: the generic reporting component
// exposes every metric the platform can compute, and most of them mean
// nothing to a commercial cleaning company. See verticals/workspaceVertical.js.
import { verticalFor, VERTICAL_CLEANING } from '../verticals/workspaceVertical'
import CleaningReports from './vertical/CleaningReports'
import '../styles/shared.css'
import './Reports.css'

function pct(v) {
  const n = Number(v || 0)
  return `${n % 1 === 0 ? n : n.toFixed(1)}%`
}
function num(v) { return Number(v || 0).toLocaleString() }

/* A LIST OF ROWS, WHATEVER THE SERVER SENT.
 *
 * `crm-summary.stage_counts` was a `{stage: count}` OBJECT and this page read
 * it as an array: `(stage_counts || []).slice(0, 4)`. An object is truthy, so
 * the `|| []` guard never fired, `.slice` is not a function on it, and the
 * TypeError escaped render — which unmounts React's whole tree, navigation
 * rail included. The application was a blank page until a full reload.
 *
 * The server now sends rows. This stays because the lesson is not "that one
 * endpoint was wrong": a reporting payload is data from over the network, and
 * a page that assumes its shape can take the entire app down when it changes.
 * Anything unrecognisable becomes an empty list rather than an exception.
 */
function rows(value, keyName = 'stage', valueName = 'count') {
  if (Array.isArray(value)) return value
  if (value && typeof value === 'object') {
    return Object.entries(value).map(([k, v]) => ({ [keyName]: k, [valueName]: Number(v) || 0 }))
  }
  return []
}

/* Object.entries() over a LIST OF PAIRS yields ["0", [value, count]], which
 * renders as an index beside both halves. `value_counts` was exactly that for
 * a while. Same treatment: accept either, render neither wrongly. */
function pairs(value) {
  if (Array.isArray(value)) {
    return value.map(entry => (Array.isArray(entry) ? entry : [entry, '']))
  }
  if (value && typeof value === 'object') return Object.entries(value)
  return []
}

function KpiCard({ label, value, sub, color, icon }) {
  return (
    <div className="panel rpt-kpi-card">
      <div className="rpt-kpi-icon">{icon}</div>
      <strong className="rpt-kpi-value" style={{ color }}>{value}</strong>
      <span className="rpt-kpi-label">{label}</span>
      {sub && <span className="rpt-kpi-sub">{sub}</span>}
    </div>
  )
}

function Bar({ value, max = 100, color = 'var(--signal-blue)' }) {
  const w = Math.min((Number(value) / Math.max(max, 1)) * 100, 100)
  return (
    <div className="rpt-bar-track">
      <div className="rpt-bar-fill" style={{ width: `${w}%`, background: color }} />
    </div>
  )
}

const STAGE_LABELS = {
  outreach_sent: 'Outreach sent',
  replied: 'Replied',
  ai_responding: 'AI responding',
  booking_sent: 'Booking sent',
  booked: 'Booked',
  confirmed: 'Confirmed',
  kept: 'Kept appointment',
  sale: 'Sale',
  stopped: 'Stopped',
  dnc: 'DNC',
}
const STAGE_COLORS = {
  outreach_sent: 'var(--color-primary)', replied: 'var(--color-warning)', ai_responding: 'var(--signal-purple)',
  booking_sent: '#c2560c', booked: 'var(--color-success)', confirmed: 'var(--color-success)',
  kept: 'var(--color-success)', sale: '#a8650f', stopped: '#6b7280', dnc: 'var(--color-danger)',
}

function PlatformReports() {
  const [data, setData]         = useState(null)
  const [pipeline, setPipeline] = useState(null)
  const [outcomes, setOutcomes] = useState(null)
  const [crmSummary, setCrmSummary] = useState(null)
  const [loading, setLoading]   = useState(true)
  // Which sources did NOT load. A failed request must never render as a real
  // zero ("0 leads, 0% reply rate" on a backend hiccup reads as an empty
  // business), so the numbers it would have fed are withheld and named.
  const [failed, setFailed]     = useState([])
  const [activeTab, setActiveTab] = useState('performance')

  useEffect(() => {
    Promise.all([
      api.get('/admin/dashboard/metrics').catch(() => undefined),
      api.get('/pipeline/stats').catch(() => undefined),
      api.get('/outcomes/summary').catch(() => undefined),
      api.get('/reports/crm-summary').catch(() => undefined),
    ]).then(([metricsData, pipelineData, outcomesData, crmData]) => {
      const names = ['performance metrics', 'pipeline', 'outcomes', 'CRM summary']
      setFailed([metricsData, pipelineData, outcomesData, crmData]
        .map((v, i) => (v === undefined ? names[i] : null)).filter(Boolean))
      setData(metricsData)
      setPipeline(pipelineData)
      setOutcomes(outcomesData)
      setCrmSummary(crmData)
      setLoading(false)
    })
  }, [])

  const totals   = data?.totals || {}
  const advisors = (data?.advisors || []).filter(a => a.advisor_id !== 'org_total')
  const maxBook  = Math.max(...advisors.map(a => Number(a.booking_rate || 0)), 1)
  const maxReply = Math.max(...advisors.map(a => Number(a.reply_rate || 0)), 1)
  const pipeStages = pipeline?.by_stage || {}
  const totalPipe  = pipeline?.total_in_pipeline || 0
  const stageRows  = rows(crmSummary?.stage_counts)
  const stageMax   = Math.max(...stageRows.map(s => Number(s.count) || 0), 1)

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Reports</h1>
          <p className="page-subtitle">Performance, pipeline, and revenue — all in one place.</p>
        </div>
        {data?.is_god_view && (
          <div style={{
            background: 'linear-gradient(135deg, #7c3aed, #4f46e5)',
            color: '#fff', borderRadius: 8, padding: '6px 14px', fontSize: 12, fontWeight: 600,
          }}>
            ⚡ God View — All Organizations
          </div>
        )}
      </header>

      {!loading && failed.length > 0 && (
        <div className="empty-state" role="alert" style={{ color: 'var(--signal-red, #b42318)', textAlign: 'left', marginBottom: 16 }}>
          Some report data could not be loaded ({failed.join(', ')}). Figures that depend on it are not shown as zero -
          refresh to try again.
        </div>
      )}

      {/* KPI ROW */}
      <div className="rpt-kpi-row">
        <KpiCard icon="👥" label="Total leads"       value={loading || data === undefined ? '—' : num(totals.leads_owned)}       sub="Org-wide"                    color="var(--text-primary)" />
        <KpiCard icon="📤" label="Messages sent"     value={loading || data === undefined ? '—' : num(totals.messages_sent)}     sub="All time"                   color="var(--color-primary)" />
        <KpiCard icon="💬" label="Replies received"  value={loading || data === undefined ? '—' : num(totals.replies)}           sub={`${num(totals.hot_replies)} hot`}  color="var(--signal-purple)" />
        <KpiCard icon="📊" label="Reply rate"        value={loading || data === undefined ? '—' : pct(totals.reply_rate)}        sub="Replies / sent"             color="var(--color-warning)" />
        <KpiCard icon="📅" label="Appointments"      value={loading || data === undefined ? '—' : num(totals.booked_leads)}      sub="Booked all time"            color="var(--color-success)" />
        <KpiCard icon="🎯" label="Booking rate"      value={loading || data === undefined ? '—' : pct(totals.booking_rate)}      sub="Bookings / sent"            color="var(--color-success)" />
        <KpiCard icon="🤖" label="AI auto-sent"      value={loading || pipeline === undefined ? '—' : num(pipeline?.ai_auto_sent)}   sub="Pipeline responses"         color="var(--signal-purple)" />
        <KpiCard icon="💰" label="Sales"             value={loading || outcomes === undefined ? '—' : num(outcomes?.sales_count)}    sub={outcomes === undefined ? 'Not loaded' : outcomes?.conversion_rate != null ? `${outcomes.conversion_rate}% close rate` : 'No outcomes yet'} color="var(--color-warning)" />
      </div>

      {/* TABS */}
      <div className="rpt-tabs">
        {[
          { key: 'performance', label: '📊 Performance' },
          { key: 'pipeline',    label: '🚀 Pipeline' },
          { key: 'engagement',  label: '📈 Engagement' },
          { key: 'outcomes',    label: '💰 Outcomes' },
          { key: 'crm',         label: '🗂 CRM' },
        ].map(t => (
          <button key={t.key} className={`rpt-tab ${activeTab === t.key ? 'rpt-tab--active' : ''}`}
            onClick={() => setActiveTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>

      {/* PERFORMANCE TAB */}
      {activeTab === 'performance' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {loading ? (
            <div className="empty-state">Loading…</div>
          ) : data === undefined ? (
            <div className="empty-state">Performance data could not be loaded. Refresh to try again.</div>
          ) : advisors.length === 0 ? (
            <div className="empty-state">No advisor data yet. Performance by advisor appears here once your team sends messages to leads; import or add leads to get started.</div>
          ) : (
            <>
              {/* Advisor table */}
              <section className="panel">
                <div className="panel-header">
                  <h2 className="panel-title">Advisor breakdown</h2>
                  <span className="panel-count">{advisors.length} advisors</span>
                </div>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Advisor</th>
                      {data?.is_god_view && <th>Organization</th>}
                      <th>Leads</th>
                      <th>Sent</th>
                      <th>Replies</th>
                      <th>Reply rate</th>
                      <th>Hot rate</th>
                      <th>Booking rate</th>
                      <th>DNC</th>
                    </tr>
                  </thead>
                  <tbody>
                    {advisors.map(a => (
                      <tr key={a.advisor_id}>
                        <td>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                            <div className="rpt-avatar">{(a.advisor_name || 'A').charAt(0)}</div>
                            <span style={{ fontWeight: 600 }}>{a.advisor_name}</span>
                          </div>
                        </td>
                        {data?.is_god_view && (
                          <td style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                            {a.organization_name || '—'}
                          </td>
                        )}
                        <td className="mono">{num(a.leads_owned)}</td>
                        <td className="mono">{num(a.messages_sent)}</td>
                        <td className="mono">{num(a.replies)}</td>
                        <td>
                          <div className="rpt-bar-cell">
                            <Bar value={a.reply_rate} max={maxReply} color="var(--color-primary)" />
                            <span className="mono">{pct(a.reply_rate)}</span>
                          </div>
                        </td>
                        <td>
                          <div className="rpt-bar-cell">
                            <Bar value={a.hot_reply_rate} max={maxReply} color="var(--color-danger)" />
                            <span className="mono" style={{ color: Number(a.hot_replies) > 0 ? 'var(--color-danger)' : undefined }}>{pct(a.hot_reply_rate)}</span>
                          </div>
                        </td>
                        <td>
                          <div className="rpt-bar-cell">
                            <Bar value={a.booking_rate} max={maxBook} color="var(--color-success)" />
                            <span className="mono" style={{ color: Number(a.booked_leads) > 0 ? 'var(--color-success)' : undefined }}>{pct(a.booking_rate)}</span>
                          </div>
                        </td>
                        <td className="mono" style={{ color: Number(a.dnc_rate) > 5 ? 'var(--color-danger)' : 'var(--text-secondary)' }}>
                          {pct(a.dnc_rate)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>

              {/* Top performers + Leaderboard */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                <section className="panel">
                  <div className="panel-header"><h2 className="panel-title">🏆 Top performers</h2></div>
                  {[...advisors].sort((a, b) => Number(b.booking_rate) - Number(a.booking_rate)).slice(0, 5).map((a, i) => (
                    <div key={a.advisor_id} className="rpt-top-row">
                      <span style={{ fontSize: 16, fontWeight: 900, color: i === 0 ? '#a8650f' : i === 1 ? '#6b7d94' : i === 2 ? '#9a5b23' : 'var(--text-tertiary)', minWidth: 28 }}>#{i + 1}</span>
                      <div className="rpt-avatar">{(a.advisor_name || 'A').charAt(0)}</div>
                      <span style={{ flex: 1, fontWeight: 600 }}>{a.advisor_name}</span>
                      <span style={{ color: 'var(--color-success)', fontWeight: 700, fontSize: 14 }}>{pct(a.booking_rate)} booked</span>
                    </div>
                  ))}
                </section>

                <section className="panel">
                  <div className="panel-header"><h2 className="panel-title">📋 Engagement summary</h2></div>
                  {[
                    { label: 'Total messages sent',    value: num(totals.messages_sent),              color: 'var(--color-primary)' },
                    { label: 'Total replies',          value: num(totals.replies),                    color: 'var(--signal-purple)' },
                    { label: 'Hot / callback replies', value: num(totals.hot_replies),                color: 'var(--color-danger)' },
                    { label: 'Appointments booked',   value: num(totals.booked_leads),               color: 'var(--color-success)' },
                    { label: 'DNC leads',              value: num(totals.dnc_leads),                  color: 'var(--color-warning)' },
                    { label: 'Dupes prevented',       value: num(totals.duplicate_leads_prevented),  color: 'var(--color-success)' },
                  ].map(item => (
                    <div key={item.label} className="rpt-summary-row">
                      <span className="rpt-summary-label">{item.label}</span>
                      <span style={{ color: item.color, fontWeight: 700, fontSize: 15 }}>{item.value}</span>
                    </div>
                  ))}
                </section>
              </div>
            </>
          )}
        </div>
      )}

      {/* PIPELINE TAB */}
      {activeTab === 'pipeline' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12 }}>
            {[
              { label: 'Total in pipeline', value: num(totalPipe),                       color: 'var(--color-primary)' },
              { label: 'AI auto-responses', value: num(pipeline?.ai_auto_sent),          color: 'var(--signal-purple)' },
              { label: 'Flagged for review',value: num(pipeline?.flagged_count),         color: 'var(--color-danger)' },
              { label: 'Total booked',      value: num(pipeline?.total_booked),          color: 'var(--color-success)' },
            ].map(item => (
              <div key={item.label} className="panel" style={{ textAlign: 'center', padding: '24px 16px' }}>
                <strong style={{ fontSize: 36, fontWeight: 900, color: item.color, display: 'block', lineHeight: 1 }}>{loading ? '—' : item.value}</strong>
                <span style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 8, display: 'block' }}>{item.label}</span>
              </div>
            ))}
          </div>

          <section className="panel">
            <div className="panel-header"><h2 className="panel-title">Pipeline stage breakdown</h2></div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '4px 0' }}>
              {Object.entries(STAGE_LABELS).map(([stage, label]) => {
                const count = pipeStages[stage] || 0
                const maxCount = Math.max(...Object.values(pipeStages), 1)
                return (
                  <div key={stage} style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                    <span style={{ fontSize: 13, color: 'var(--text-secondary)', minWidth: 150 }}>{label}</span>
                    <div style={{ flex: 1, height: 10, background: 'var(--bg-hover)', borderRadius: 5, overflow: 'hidden' }}>
                      <div style={{ height: '100%', width: `${Math.max(2, (count/maxCount)*100)}%`, background: STAGE_COLORS[stage] || 'var(--color-primary)', borderRadius: 5, transition: 'width 0.4s' }} />
                    </div>
                    <span style={{ fontSize: 14, fontWeight: 700, color: STAGE_COLORS[stage] || 'var(--color-primary)', minWidth: 36, textAlign: 'right' }}>{count}</span>
                  </div>
                )
              })}
            </div>
          </section>
        </div>
      )}

      {/* ENGAGEMENT TAB */}
      {activeTab === 'engagement' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <section className="panel">
            <div className="panel-header"><h2 className="panel-title">📤 Outreach metrics</h2></div>
            {[
              { label: 'Messages sent',       value: num(totals.messages_sent),   color: 'var(--color-primary)' },
              { label: 'Replies received',    value: num(totals.replies),         color: 'var(--signal-purple)' },
              { label: 'Hot leads',           value: num(totals.hot_replies),     color: 'var(--color-danger)' },
              { label: 'Callbacks requested', value: num(totals.callback_count),  color: 'var(--color-warning)' },
              { label: 'DNC',                 value: num(totals.dnc_leads),       color: '#6b7280' },
            ].map(item => (
              <div key={item.label} className="rpt-summary-row">
                <span className="rpt-summary-label">{item.label}</span>
                <span style={{ color: item.color, fontWeight: 700, fontSize: 16 }}>{item.value}</span>
              </div>
            ))}
          </section>

          <section className="panel">
            <div className="panel-header"><h2 className="panel-title">🤖 AI pipeline metrics</h2></div>
            {[
              { label: 'Total in pipeline',     value: num(totalPipe),                color: 'var(--color-primary)' },
              { label: 'AI responses sent',     value: num(pipeline?.ai_auto_sent),   color: 'var(--signal-purple)' },
              { label: 'Flagged for review',    value: num(pipeline?.flagged_count),  color: 'var(--color-danger)' },
              { label: 'Total replies received',value: num(pipeline?.total_replies_received), color: 'var(--color-warning)' },
              { label: 'Total booked',          value: num(pipeline?.total_booked),   color: 'var(--color-success)' },
            ].map(item => (
              <div key={item.label} className="rpt-summary-row">
                <span className="rpt-summary-label">{item.label}</span>
                <span style={{ color: item.color, fontWeight: 700, fontSize: 16 }}>{item.value}</span>
              </div>
            ))}
          </section>

          <section className="panel" style={{ gridColumn: '1 / -1' }}>
            <div className="panel-header"><h2 className="panel-title">📊 Conversion funnel</h2></div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5,1fr)', gap: 2, marginTop: 8 }}>
              {[
                { label: 'Leads',        value: totals.leads_owned || 0,    color: 'var(--color-primary)' },
                { label: 'Contacted',    value: totals.messages_sent || 0,  color: 'var(--signal-purple)' },
                { label: 'Replied',      value: totals.replies || 0,        color: 'var(--color-warning)' },
                { label: 'Hot',          value: totals.hot_replies || 0,    color: 'var(--color-danger)' },
                { label: 'Booked',       value: totals.booked_leads || 0,   color: 'var(--color-success)' },
              ].map((stage, i, arr) => {
                const maxV = Math.max(...arr.map(s => s.value), 1)
                const h = Math.max(20, Math.round((stage.value / maxV) * 120))
                return (
                  <div key={stage.label} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 18, fontWeight: 900, color: stage.color }}>{num(stage.value)}</span>
                    <div style={{ width: '100%', height: h, background: stage.color, borderRadius: 6, opacity: 0.85, transition: 'height 0.4s' }} />
                    <span style={{ fontSize: 12, color: 'var(--text-secondary)', textAlign: 'center' }}>{stage.label}</span>
                  </div>
                )
              })}
            </div>
          </section>
        </div>
      )}

      {/* OUTCOMES TAB */}
      {activeTab === 'outcomes' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {!outcomes ? (
            <div className="empty-state">No outcome data yet. Record visits after appointments to see revenue data here.</div>
          ) : (
            <>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12 }}>
                {[
                  { label: 'Total appointments', value: num(outcomes.total_appointments), color: 'var(--color-primary)', icon: '📅' },
                  { label: 'Sales closed',        value: num(outcomes.sales_count),        color: '#a8650f', icon: '💰' },
                  { label: 'Close rate',          value: pct(outcomes.conversion_rate),    color: 'var(--color-success)', icon: '🎯' },
                  { label: 'No-shows',            value: num(outcomes.no_show_count),      color: 'var(--color-danger)', icon: '❌' },
                ].map(item => (
                  <div key={item.label} className="panel" style={{ textAlign: 'center', padding: '24px 16px' }}>
                    <div style={{ fontSize: 28, marginBottom: 8 }}>{item.icon}</div>
                    <strong style={{ fontSize: 36, fontWeight: 900, color: item.color, display: 'block', lineHeight: 1 }}>{item.value}</strong>
                    <span style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 8, display: 'block' }}>{item.label}</span>
                  </div>
                ))}
              </div>

              <section className="panel">
                <div className="panel-header"><h2 className="panel-title">Outcome breakdown</h2></div>
                {[
                  { label: 'Total appointments recorded', value: num(outcomes.total_appointments),   color: 'var(--color-primary)' },
                  { label: 'Sales',                       value: num(outcomes.sales_count),           color: '#a8650f' },
                  { label: 'Not interested',              value: num(outcomes.not_interested_count),  color: '#6b7280' },
                  { label: 'No-shows',                    value: num(outcomes.no_show_count),         color: 'var(--color-danger)' },
                  { label: 'Follow-up needed',            value: num(outcomes.follow_up_count),       color: 'var(--color-warning)' },
                  { label: 'Close rate',                  value: pct(outcomes.conversion_rate),       color: 'var(--color-success)' },
                ].map(item => (
                  <div key={item.label} className="rpt-summary-row">
                    <span className="rpt-summary-label">{item.label}</span>
                    <span style={{ color: item.color, fontWeight: 700, fontSize: 16 }}>{item.value}</span>
                  </div>
                ))}
              </section>
            </>
          )}
        </div>
      )}

      {/* CRM TAB */}
      {activeTab === 'crm' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {loading || !crmSummary ? (
            <div className="empty-state">{loading ? 'Loading…' : 'No CRM data yet.'}</div>
          ) : (
            <>
              {/* Summary KPIs */}
              <div className="rpt-kpi-row">
                <KpiCard icon="👤" label="Total contacts"  value={num(crmSummary.total_contacts)} color="var(--text-primary)" />
                {stageRows.slice(0, 4).map(s => (
                  <KpiCard key={s.stage} icon="📌" label={s.stage || 'Unknown'} value={num(s.count)} color="var(--signal-blue)" />
                ))}
              </div>

              {/* Stage breakdown */}
              {stageRows.length > 0 && (
                <section className="panel">
                  <div className="panel-header"><h2 className="panel-title">Stage breakdown</h2></div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '4px 0' }}>
                    {stageRows.map(s => (
                      <div key={s.stage} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                        <span style={{ width: 140, fontSize: 13, color: 'var(--text-secondary)', flexShrink: 0 }}>{s.stage || '—'}</span>
                        <Bar value={s.count} max={stageMax} color="var(--signal-blue)" />
                        <span style={{ width: 48, textAlign: 'right', fontSize: 13, fontWeight: 700 }}>{num(s.count)}</span>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* Custom field fill rates */}
              {crmSummary.custom_field_stats?.length > 0 && (
                <section className="panel">
                  <div className="panel-header">
                    <h2 className="panel-title">Custom field completion</h2>
                    <span className="panel-count">{crmSummary.custom_field_stats.length} fields</span>
                  </div>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Field</th>
                        <th>Type</th>
                        <th>Filled</th>
                        <th>Fill rate</th>
                        <th style={{ width: 160 }}>Top values</th>
                      </tr>
                    </thead>
                    <tbody>
                      {crmSummary.custom_field_stats.map(f => (
                        <tr key={f.key}>
                          <td style={{ fontWeight: 600 }}>{f.label}</td>
                          <td style={{ fontSize: 11, color: 'var(--text-secondary)', textTransform: 'capitalize' }}>{f.type}</td>
                          <td>{num(f.filled)} / {num(crmSummary.total_contacts)}</td>
                          <td>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                              <Bar value={f.fill_rate_pct} max={100} color={f.fill_rate_pct > 60 ? 'var(--signal-green)' : f.fill_rate_pct > 30 ? 'var(--signal-amber)' : 'var(--signal-red)'} />
                              <span style={{ width: 44, fontSize: 12, fontWeight: 700 }}>{pct(f.fill_rate_pct)}</span>
                            </div>
                          </td>
                          <td style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                            {pairs(f.value_counts).slice(0, 3).map(([k, v]) => `${k}: ${v}`).join(' · ') || '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </section>
              )}

              {(!crmSummary.custom_field_stats || crmSummary.custom_field_stats.length === 0) && (
                <div className="empty-state">No custom fields configured yet. Add them in Admin → CRM settings.</div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

/* ═══════════════════════════════════════════════════════════════════════════
   ONE DECISION, TAKEN FROM THE ACTIVE WORKSPACE'S INDUSTRY.

   The screen above reports on the platform's own vocabulary — AI auto-
   responses, booking links sent, brand-sales opportunity value. A commercial
   cleaning company using CCB does not run any of those; it sources prospects,
   works them, books walkthroughs and finds out what happened. Rendering the
   platform's metric set to that customer is not a smaller version of their
   report, it is somebody else's report.

   A workspace whose industry has no configured reporting presentation falls
   straight through to PlatformReports above, unchanged.
   ═══════════════════════════════════════════════════════════════════════════ */
export default function Reports() {
  // Same hook as Overview, for the same reason: on a first paint the branding
  // row may not be stored yet, and this re-renders when the server answers —
  // so the vertical is picked up rather than decided once against an empty
  // cache and then never revisited.
  const { branding } = useWorkspaceAuthority()
  const vertical = verticalFor(branding)
  if (vertical && vertical.key === VERTICAL_CLEANING) return <CleaningReports />
  return <PlatformReports />
}
