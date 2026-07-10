import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  CartesianGrid, Cell, Line, LineChart, Pie, PieChart,
  PolarAngleAxis, RadialBar, RadialBarChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api, getCurrentUser } from '../api/client'
import SignalPulse from '../components/SignalPulse'
import './Overview.css'

const chartTooltipStyle = {
  background: 'var(--bg-panel)',
  border: '1px solid var(--border-strong)',
  borderRadius: 12,
  color: 'var(--text-primary)',
}

function formatDate(value) {
  const date = new Date(`${value}T00:00:00`)
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export default function Overview() {
  const user = getCurrentUser()
  const navigate = useNavigate()
  const [leads, setLeads] = useState([])
  const [replies, setReplies] = useState([])
  const [dailyBriefing, setDailyBriefing] = useState(null)
  const [replyActivity, setReplyActivity] = useState([])
  const [engagementBreakdown, setEngagementBreakdown] = useState(null)
  const [cadenceHealth, setCadenceHealth] = useState(null)
  const [statusFunnel, setStatusFunnel] = useState([])
  const [outcomesSummary, setOutcomesSummary] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      api.get('/leads/').catch(() => []),
      api.get('/sms/replies?needs_attention=true').catch(() => []),
      api.get('/leads/daily-briefing').catch(() => null),
      api.get('/sms/replies/activity-by-day?days=14').catch(() => []),
      api.get('/leads/engagement-breakdown').catch(() => null),
      api.get('/cadence/health-summary').catch(() => null),
      api.get('/leads/status-funnel').catch(() => []),
      api.get('/outcomes/summary').catch(() => null),
    ]).then(([leadsData, repliesData, briefingData, replyActivityData, engagementData, cadenceHealthData, statusFunnelData, outcomesData]) => {
      setLeads(leadsData || [])
      setReplies(repliesData || [])
      setDailyBriefing(briefingData)
      setReplyActivity(replyActivityData || [])
      setEngagementBreakdown(engagementData)
      setCadenceHealth(cadenceHealthData)
      setStatusFunnel(statusFunnelData || [])
      setOutcomesSummary(outcomesData)
      setLoading(false)
    })
  }, [])

  const newCount = leads.filter((l) => l.status === 'new').length
  const sentCount = leads.filter((l) => l.status === 'sent').length
  const hotCount = replies.length
  const bookedCount = leads.filter((l) => l.status === 'booked').length
  const needsReviewCount = leads.filter((l) => l.status === 'needs_tier_review').length
  const certifiedCount = dailyBriefing?.certified_appointments_waiting ?? 0

  const engagementChartData = engagementBreakdown ? [
    { name: 'Hot', value: engagementBreakdown.hot || 0, color: 'var(--signal-red)' },
    { name: 'Warm', value: engagementBreakdown.warm || 0, color: 'var(--signal-amber)' },
    { name: 'Cold', value: engagementBreakdown.cold || 0, color: 'var(--signal-blue)' },
    { name: 'Unknown', value: engagementBreakdown.unknown || 0, color: 'rgba(255,255,255,0.18)' },
  ] : []

  const cadenceGaugeData = [{ name: 'Health', value: cadenceHealth?.health_score || 0, fill: 'var(--signal-green)' }]
  const maxFunnelCount = Math.max(1, ...statusFunnel.map((s) => s.count || 0))

  const heroCards = [
    { label: 'Certified appts', value: certifiedCount, color: 'var(--signal-green)', glow: 'var(--glow-green-md)', path: '/leads', sub: 'Waiting to be worked' },
    { label: 'Hot replies', value: hotCount, color: 'var(--signal-red)', glow: 'var(--glow-red-md)', path: '/replies', sub: 'Needs your attention' },
    { label: 'New leads', value: newCount, color: 'var(--signal-blue)', glow: 'var(--glow-blue-md)', path: '/leads', sub: 'Ready to contact' },
    { label: 'Booked', value: bookedCount, color: 'var(--signal-amber)', glow: 'var(--glow-amber-md)', path: '/leads', sub: 'Appointments set' },
    { label: 'Sent', value: sentCount, color: 'var(--text-primary)', glow: 'none', path: '/leads', sub: 'Awaiting reply' },
    { label: 'Needs review', value: needsReviewCount, color: 'var(--signal-amber)', glow: 'var(--glow-amber-md)', path: '/leads', sub: 'Untyped leads' },
  ]

  return (
    <div className="overview-page">
      <header className="page-header">
        <div>
          <h1 className="page-title">Overview</h1>
          <p className="page-subtitle">Welcome back, {user?.full_name?.split(' ')[0] || 'advisor'}.</p>
        </div>
        <SignalPulse color="green" label="Live" />
      </header>

      <div className="overview-hero-grid">
        {heroCards.map((card) => (
          <button key={card.label} className="overview-kpi-card" onClick={() => navigate(card.path)}>
            <span className="overview-kpi-label">{card.label}</span>
            <strong className="overview-kpi-value" style={{ color: card.color, textShadow: card.glow }}>
              {loading ? '—' : card.value}
            </strong>
            <span className="overview-kpi-sub">{card.sub}</span>
          </button>
        ))}
      </div>

      <div className="overview-top-grid">
        <section className="panel today-briefing-panel">
          <div className="panel-header">
            <h2 className="panel-title">Today</h2>
          </div>
          {loading ? (
            <div className="empty-state">Building briefing…</div>
          ) : dailyBriefing ? (
            <div className="today-briefing-list">
              {[
                { key: 'certified', count: dailyBriefing.certified_appointments_waiting, text: `${dailyBriefing.certified_appointments_waiting} certified appointments waiting`, path: '/leads', accent: 'green' },
                { key: 'replies', count: dailyBriefing.replies_needing_attention, text: `${dailyBriefing.replies_needing_attention} replies need attention`, path: '/replies', accent: 'red' },
                { key: 'cadence', count: dailyBriefing.cadence_touches_due_today, text: `${dailyBriefing.cadence_touches_due_today} cadence touches due today`, path: '/cadence', accent: 'blue' },
                { key: 'imports', count: dailyBriefing.leads_imported_last_24h, text: `${dailyBriefing.leads_imported_last_24h} leads imported in 24h`, path: '/leads', accent: 'green' },
                { key: 'bookings', count: dailyBriefing.bookings_last_7_days, text: `${dailyBriefing.bookings_last_7_days} bookings in last 7 days`, path: '/leads', accent: 'amber' },
              ].map((item) => (
                <button key={item.key} className={`today-briefing-line today-briefing-line--${item.accent}`} onClick={() => navigate(item.path)}>
                  <span className="today-briefing-dot" />
                  <span>{item.text}</span>
                </button>
              ))}
            </div>
          ) : (
            <div className="empty-state">No briefing data available.</div>
          )}
        </section>

        <section className="panel overview-hot-replies-panel">
          <div className="panel-header">
            <h2 className="panel-title">Hot replies</h2>
            <span className="panel-count">{replies.length}</span>
          </div>
          {replies.length === 0 ? (
            <div className="empty-state">No hot replies right now.</div>
          ) : (
            <ul className="reply-list reply-list--compact">
              {replies.slice(0, 5).map((r) => (
                <li key={r.id} className="reply-item reply-item--clickable" onClick={() => r.lead_id && navigate(`/leads/${r.lead_id}`)}>
                  <span className="overview-hot-dot" />
                  <span className="reply-body">{r.body}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <div className="overview-chart-grid">
        <article className="panel overview-chart-panel overview-chart-panel--wide">
          <div className="panel-header">
            <h2 className="panel-title">Reply activity</h2>
            <span className="chart-subtitle-inline">Last 14 days</span>
          </div>
          {replyActivity.length === 0 ? (
            <div className="empty-state">No reply data yet.</div>
          ) : (
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={replyActivity} margin={{ top: 10, right: 16, left: -18, bottom: 2 }}>
                <CartesianGrid stroke="var(--border-subtle)" strokeDasharray="4 8" vertical={false} />
                <XAxis dataKey="date" tickFormatter={formatDate} stroke="var(--text-tertiary)" tickLine={false} axisLine={false} minTickGap={18} />
                <YAxis allowDecimals={false} stroke="var(--text-tertiary)" tickLine={false} axisLine={false} width={36} />
                <Tooltip contentStyle={chartTooltipStyle} labelFormatter={formatDate} />
                <Line type="monotone" dataKey="count" name="Replies" stroke="var(--signal-blue)" strokeWidth={3} dot={{ r: 3 }} activeDot={{ r: 6 }} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </article>

        <article className="panel overview-chart-panel">
          <div className="panel-header">
            <h2 className="panel-title">Engagement</h2>
          </div>
          {engagementChartData.length === 0 ? (
            <div className="empty-state">No engagement data yet.</div>
          ) : (
            <>
              <ResponsiveContainer width="100%" height={130}>
                <PieChart>
                  <Pie data={engagementChartData} dataKey="value" nameKey="name" innerRadius="58%" outerRadius="84%" paddingAngle={3}>
                    {engagementChartData.map((entry, i) => <Cell key={i} fill={entry.color} />)}
                  </Pie>
                  <Tooltip contentStyle={chartTooltipStyle} />
                </PieChart>
              </ResponsiveContainer>
              <div className="chart-legend chart-legend--compact">
                {engagementChartData.map((e) => (
                  <span key={e.name} className="chart-legend-item">
                    <span className="chart-legend-dot" style={{ background: e.color }} />
                    {e.name}: {e.value}
                  </span>
                ))}
              </div>
            </>
          )}
        </article>

        <article className="panel overview-chart-panel">
          <div className="panel-header">
            <h2 className="panel-title">Cadence health</h2>
          </div>
          {cadenceHealth ? (
            <>
              <ResponsiveContainer width="100%" height={130}>
                <RadialBarChart innerRadius="70%" outerRadius="100%" data={cadenceGaugeData} startAngle={180} endAngle={0}>
                  <PolarAngleAxis type="number" domain={[0, 100]} angleAxisId={0} tick={false} />
                  <RadialBar dataKey="value" cornerRadius={12} background={{ fill: 'rgba(47, 182, 255, 0.10)' }} />
                </RadialBarChart>
              </ResponsiveContainer>
              <div className="gauge-readout gauge-readout--compact">
                <strong>{cadenceHealth.health_score}%</strong>
                <span>{cadenceHealth.healthy_active_count}/{cadenceHealth.active_count} healthy</span>
              </div>
            </>
          ) : (
            <div className="empty-state">No cadence data.</div>
          )}
        </article>
      </div>

      <div className="overview-bottom-grid">
        <article className="panel">
          <div className="panel-header">
            <h2 className="panel-title">Status funnel</h2>
          </div>
          {statusFunnel.length === 0 ? (
            <div className="empty-state">No funnel data yet.</div>
          ) : statusFunnel.map((stage) => (
            <div key={stage.status} className="funnel-row">
              <div className="funnel-row-meta">
                <span>{stage.label}</span>
                <strong>{stage.count}</strong>
              </div>
              <div className="funnel-track">
                <div className="funnel-bar" style={{ width: `${Math.max(4, Math.round(((stage.count || 0) / maxFunnelCount) * 100))}%` }} />
              </div>
            </div>
          ))}
        </article>

        <article className="panel">
          <div className="panel-header">
            <h2 className="panel-title">Revenue activity</h2>
          </div>
          <div className="overview-revenue-cards">
            {[
              { label: 'Pipeline', value: bookedCount, color: 'var(--signal-blue)', sub: 'Booked appointments' },
              { label: 'Completed', value: outcomesSummary?.total_appointments ?? 0, color: 'var(--signal-green)', sub: 'Recorded outcomes' },
              { label: 'Sales', value: outcomesSummary?.sales_count ?? 0, color: 'var(--signal-purple)', sub: outcomesSummary?.conversion_rate != null ? `${outcomesSummary.conversion_rate}% conversion` : 'No outcomes yet' },
            ].map((item) => (
              <div key={item.label} className="overview-revenue-stat">
                <span className="overview-revenue-label">{item.label}</span>
                <strong className="overview-revenue-value" style={{ color: item.color }}>{loading ? '—' : item.value}</strong>
                <span className="overview-revenue-sub">{item.sub}</span>
              </div>
            ))}
          </div>
        </article>
      </div>
    </div>
  )
}
