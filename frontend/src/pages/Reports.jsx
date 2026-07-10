import { useEffect, useState } from 'react'
import { api } from '../api/client'
import '../styles/shared.css'
import './Reports.css'

function pct(v) {
  const n = Number(v || 0)
  return `${n % 1 === 0 ? n : n.toFixed(1)}%`
}
function num(v) { return Number(v || 0).toLocaleString() }

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
  outreach_sent: '#2fb6ff', replied: '#f0c040', ai_responding: '#a78bfa',
  booking_sent: '#fb923c', booked: '#1ef0a8', confirmed: '#1ef0a8',
  kept: '#1ef0a8', sale: '#ffd700', stopped: '#6b7280', dnc: '#ff4d4d',
}

export default function Reports() {
  const [data, setData]         = useState(null)
  const [pipeline, setPipeline] = useState(null)
  const [outcomes, setOutcomes] = useState(null)
  const [loading, setLoading]   = useState(true)
  const [activeTab, setActiveTab] = useState('performance')

  useEffect(() => {
    Promise.all([
      api.get('/admin/dashboard/metrics').catch(() => null),
      api.get('/pipeline/stats').catch(() => null),
      api.get('/outcomes/summary').catch(() => null),
    ]).then(([metricsData, pipelineData, outcomesData]) => {
      setData(metricsData)
      setPipeline(pipelineData)
      setOutcomes(outcomesData)
      setLoading(false)
    })
  }, [])

  const totals   = data?.totals || {}
  const advisors = (data?.advisors || []).filter(a => a.advisor_id !== 'org_total')
  const maxBook  = Math.max(...advisors.map(a => Number(a.booking_rate || 0)), 1)
  const maxReply = Math.max(...advisors.map(a => Number(a.reply_rate || 0)), 1)
  const pipeStages = pipeline?.by_stage || {}
  const totalPipe  = pipeline?.total_in_pipeline || 0

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Reports</h1>
          <p className="page-subtitle">Performance, pipeline, and revenue — all in one place.</p>
        </div>
      </header>

      {/* KPI ROW */}
      <div className="rpt-kpi-row">
        <KpiCard icon="👥" label="Total leads"       value={loading ? '—' : num(totals.leads_owned)}       sub="Org-wide"                    color="var(--text-primary)" />
        <KpiCard icon="📤" label="Messages sent"     value={loading ? '—' : num(totals.messages_sent)}     sub="All time"                   color="#2fb6ff" />
        <KpiCard icon="💬" label="Replies received"  value={loading ? '—' : num(totals.replies)}           sub={`${num(totals.hot_replies)} hot`}  color="#a78bfa" />
        <KpiCard icon="📊" label="Reply rate"        value={loading ? '—' : pct(totals.reply_rate)}        sub="Replies / sent"             color="#f0c040" />
        <KpiCard icon="📅" label="Appointments"      value={loading ? '—' : num(totals.booked_leads)}      sub="Booked all time"            color="#1ef0a8" />
        <KpiCard icon="🎯" label="Booking rate"      value={loading ? '—' : pct(totals.booking_rate)}      sub="Bookings / sent"            color="#1ef0a8" />
        <KpiCard icon="🤖" label="AI auto-sent"      value={loading ? '—' : num(pipeline?.ai_auto_sent)}   sub="Pipeline responses"         color="#a78bfa" />
        <KpiCard icon="💰" label="Sales"             value={loading ? '—' : num(outcomes?.sales_count)}    sub={outcomes?.conversion_rate != null ? `${outcomes.conversion_rate}% close rate` : 'No outcomes yet'} color="#ffd700" />
      </div>

      {/* TABS */}
      <div className="rpt-tabs">
        {[
          { key: 'performance', label: '📊 Performance' },
          { key: 'pipeline',    label: '🚀 Pipeline' },
          { key: 'engagement',  label: '📈 Engagement' },
          { key: 'outcomes',    label: '💰 Outcomes' },
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
          ) : advisors.length === 0 ? (
            <div className="empty-state">No advisor data yet. Send messages to see performance here.</div>
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
                        <td className="mono">{num(a.leads_owned)}</td>
                        <td className="mono">{num(a.messages_sent)}</td>
                        <td className="mono">{num(a.replies)}</td>
                        <td>
                          <div className="rpt-bar-cell">
                            <Bar value={a.reply_rate} max={maxReply} color="#2fb6ff" />
                            <span className="mono">{pct(a.reply_rate)}</span>
                          </div>
                        </td>
                        <td>
                          <div className="rpt-bar-cell">
                            <Bar value={a.hot_reply_rate} max={maxReply} color="#ff4d4d" />
                            <span className="mono" style={{ color: Number(a.hot_replies) > 0 ? '#ff4d4d' : undefined }}>{pct(a.hot_reply_rate)}</span>
                          </div>
                        </td>
                        <td>
                          <div className="rpt-bar-cell">
                            <Bar value={a.booking_rate} max={maxBook} color="#1ef0a8" />
                            <span className="mono" style={{ color: Number(a.booked_leads) > 0 ? '#1ef0a8' : undefined }}>{pct(a.booking_rate)}</span>
                          </div>
                        </td>
                        <td className="mono" style={{ color: Number(a.dnc_rate) > 5 ? '#ff4d4d' : 'var(--text-secondary)' }}>
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
                      <span style={{ fontSize: 16, fontWeight: 900, color: i === 0 ? '#ffd700' : i === 1 ? '#c0c0c0' : i === 2 ? '#cd7f32' : 'var(--text-tertiary)', minWidth: 28 }}>#{i + 1}</span>
                      <div className="rpt-avatar">{(a.advisor_name || 'A').charAt(0)}</div>
                      <span style={{ flex: 1, fontWeight: 600 }}>{a.advisor_name}</span>
                      <span style={{ color: '#1ef0a8', fontWeight: 700, fontSize: 14 }}>{pct(a.booking_rate)} booked</span>
                    </div>
                  ))}
                </section>

                <section className="panel">
                  <div className="panel-header"><h2 className="panel-title">📋 Engagement summary</h2></div>
                  {[
                    { label: 'Total messages sent',    value: num(totals.messages_sent),              color: '#2fb6ff' },
                    { label: 'Total replies',          value: num(totals.replies),                    color: '#a78bfa' },
                    { label: 'Hot / callback replies', value: num(totals.hot_replies),                color: '#ff4d4d' },
                    { label: 'Appointments booked',   value: num(totals.booked_leads),               color: '#1ef0a8' },
                    { label: 'DNC leads',              value: num(totals.dnc_leads),                  color: '#f0c040' },
                    { label: 'Dupes prevented',       value: num(totals.duplicate_leads_prevented),  color: '#1ef0a8' },
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
              { label: 'Total in pipeline', value: num(totalPipe),                       color: '#2fb6ff' },
              { label: 'AI auto-responses', value: num(pipeline?.ai_auto_sent),          color: '#a78bfa' },
              { label: 'Flagged for review',value: num(pipeline?.flagged_count),         color: '#ff4d4d' },
              { label: 'Total booked',      value: num(pipeline?.total_booked),          color: '#1ef0a8' },
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
                      <div style={{ height: '100%', width: `${Math.max(2, (count/maxCount)*100)}%`, background: STAGE_COLORS[stage] || '#2fb6ff', borderRadius: 5, transition: 'width 0.4s' }} />
                    </div>
                    <span style={{ fontSize: 14, fontWeight: 700, color: STAGE_COLORS[stage] || '#2fb6ff', minWidth: 36, textAlign: 'right' }}>{count}</span>
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
              { label: 'Messages sent',       value: num(totals.messages_sent),   color: '#2fb6ff' },
              { label: 'Replies received',    value: num(totals.replies),         color: '#a78bfa' },
              { label: 'Hot leads',           value: num(totals.hot_replies),     color: '#ff4d4d' },
              { label: 'Callbacks requested', value: num(totals.callback_count),  color: '#f0c040' },
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
              { label: 'Total in pipeline',     value: num(totalPipe),                color: '#2fb6ff' },
              { label: 'AI responses sent',     value: num(pipeline?.ai_auto_sent),   color: '#a78bfa' },
              { label: 'Flagged for review',    value: num(pipeline?.flagged_count),  color: '#ff4d4d' },
              { label: 'Total replies received',value: num(pipeline?.total_replies_received), color: '#f0c040' },
              { label: 'Total booked',          value: num(pipeline?.total_booked),   color: '#1ef0a8' },
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
                { label: 'Leads',        value: totals.leads_owned || 0,    color: '#2fb6ff' },
                { label: 'Contacted',    value: totals.messages_sent || 0,  color: '#a78bfa' },
                { label: 'Replied',      value: totals.replies || 0,        color: '#f0c040' },
                { label: 'Hot',          value: totals.hot_replies || 0,    color: '#ff4d4d' },
                { label: 'Booked',       value: totals.booked_leads || 0,   color: '#1ef0a8' },
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
                  { label: 'Total appointments', value: num(outcomes.total_appointments), color: '#2fb6ff', icon: '📅' },
                  { label: 'Sales closed',        value: num(outcomes.sales_count),        color: '#ffd700', icon: '💰' },
                  { label: 'Close rate',          value: pct(outcomes.conversion_rate),    color: '#1ef0a8', icon: '🎯' },
                  { label: 'No-shows',            value: num(outcomes.no_show_count),      color: '#ff4d4d', icon: '❌' },
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
                  { label: 'Total appointments recorded', value: num(outcomes.total_appointments),   color: '#2fb6ff' },
                  { label: 'Sales',                       value: num(outcomes.sales_count),           color: '#ffd700' },
                  { label: 'Not interested',              value: num(outcomes.not_interested_count),  color: '#6b7280' },
                  { label: 'No-shows',                    value: num(outcomes.no_show_count),         color: '#ff4d4d' },
                  { label: 'Follow-up needed',            value: num(outcomes.follow_up_count),       color: '#f0c040' },
                  { label: 'Close rate',                  value: pct(outcomes.conversion_rate),       color: '#1ef0a8' },
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
    </div>
  )
}
