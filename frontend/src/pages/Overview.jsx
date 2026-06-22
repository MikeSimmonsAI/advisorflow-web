import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  PolarAngleAxis,
  RadialBar,
  RadialBarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from '../api/client'
import { getCurrentUser } from '../api/client'
import StatCard from '../components/StatCard'
import SignalPulse from '../components/SignalPulse'
import './Overview.css'

export default function Overview() {
  const user = getCurrentUser()
  const navigate = useNavigate()
  const [leads, setLeads] = useState([])
  const [replies, setReplies] = useState([])
  const [cadenceSummary, setCadenceSummary] = useState({})
  const [dailyBriefing, setDailyBriefing] = useState(null)
  const [replyActivity, setReplyActivity] = useState([])
  const [engagementBreakdown, setEngagementBreakdown] = useState(null)
  const [cadenceHealth, setCadenceHealth] = useState(null)
  const [statusFunnel, setStatusFunnel] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      api.get('/leads/').catch(() => []),
      api.get('/sms/replies?needs_attention=true').catch(() => []),
      api.get('/cadence/summary').catch(() => ({})),
      api.get('/leads/daily-briefing').catch(() => null),
      api.get('/sms/replies/activity-by-day?days=14').catch(() => []),
      api.get('/leads/engagement-breakdown').catch(() => null),
      api.get('/cadence/health-summary').catch(() => null),
      api.get('/leads/status-funnel').catch(() => []),
    ]).then(([leadsData, repliesData, cadenceData, briefingData, replyActivityData, engagementData, cadenceHealthData, statusFunnelData]) => {
      setLeads(leadsData)
      setReplies(repliesData)
      setCadenceSummary(cadenceData)
      setDailyBriefing(briefingData)
      setReplyActivity(replyActivityData || [])
      setEngagementBreakdown(engagementData)
      setCadenceHealth(cadenceHealthData)
      setStatusFunnel(statusFunnelData || [])
      setLoading(false)
    })
  }, [])

  const newCount = leads.filter((l) => l.status === 'new').length
  const sentCount = leads.filter((l) => l.status === 'sent').length
  const hotCount = leads.filter((l) => l.status === 'hot').length
  const bookedCount = leads.filter((l) => l.status === 'booked').length
  const needsReviewCount = leads.filter((l) => l.status === 'needs_tier_review').length

  const chartTooltipStyle = {
    background: 'rgba(7, 14, 32, 0.96)',
    border: '1px solid rgba(86, 200, 255, 0.42)',
    borderRadius: 12,
    color: '#eef5ff',
    boxShadow: '0 20px 60px rgba(0, 0, 0, 0.45)',
  }

  const formatActivityDate = (value) => {
    const date = new Date(`${value}T00:00:00`)
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  }

  const engagementChartData = engagementBreakdown ? [
    { name: 'Hot', key: 'hot', value: engagementBreakdown.hot || 0, color: 'var(--signal-red)' },
    { name: 'Warm', key: 'warm', value: engagementBreakdown.warm || 0, color: 'var(--signal-amber)' },
    { name: 'Cold', key: 'cold', value: engagementBreakdown.cold || 0, color: 'var(--signal-blue)' },
    { name: 'Unknown', key: 'unknown', value: engagementBreakdown.unknown || 0, color: 'var(--text-tertiary)' },
  ] : []

  const cadenceGaugeData = [{
    name: 'Healthy active cadences',
    value: cadenceHealth?.health_score || 0,
    fill: 'var(--signal-green)',
  }]

  const maxFunnelCount = Math.max(1, ...statusFunnel.map((stage) => stage.count || 0))

  const briefingLines = dailyBriefing ? [
    { key: 'replies', count: dailyBriefing.replies_needing_attention, text: `${dailyBriefing.replies_needing_attention} ${dailyBriefing.replies_needing_attention === 1 ? 'reply needs' : 'replies need'} your attention`, path: '/replies?needs_attention=true', accent: 'red' },
    { key: 'cadence', count: dailyBriefing.cadence_touches_due_today, text: `${dailyBriefing.cadence_touches_due_today} cadence ${dailyBriefing.cadence_touches_due_today === 1 ? 'touch is' : 'touches are'} due today`, path: '/cadence', accent: 'blue' },
    { key: 'imports', count: dailyBriefing.leads_imported_last_24h, text: `${dailyBriefing.leads_imported_last_24h} ${dailyBriefing.leads_imported_last_24h === 1 ? 'lead was' : 'leads were'} imported in the last 24 hours`, path: '/leads', accent: 'green' },
    { key: 'bookings', count: dailyBriefing.bookings_last_7_days, text: `${dailyBriefing.bookings_last_7_days} ${dailyBriefing.bookings_last_7_days === 1 ? 'booking was' : 'bookings were'} created in the last 7 days`, path: '/leads', accent: 'amber' },
  ] : []

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Overview</h1>
          <p className="page-subtitle">Welcome back, {user?.full_name?.split(' ')[0] || 'advisor'}.</p>
        </div>
        <SignalPulse color="green" label="Live" />
      </header>

      <div className="stat-grid overview-command-stats">
        <button className="stat-card-link" onClick={() => navigate('/leads')}>
          <StatCard label="New leads" value={loading ? '—' : newCount} accent="blue" sublabel="Ready to contact" />
        </button>
        <button className="stat-card-link" onClick={() => navigate('/leads')}>
          <StatCard label="Sent" value={loading ? '—' : sentCount} accent="neutral" sublabel="Awaiting reply" />
        </button>
        <button className="stat-card-link" onClick={() => navigate('/replies?needs_attention=true')}>
          <StatCard label="Hot replies" value={loading ? '—' : hotCount} accent="red" sublabel="Needs your attention" />
        </button>
        <button className="stat-card-link" onClick={() => navigate('/leads')}>
          <StatCard label="Booked" value={loading ? '—' : bookedCount} accent="green" sublabel="Appointments set" />
        </button>
        <button className="stat-card-link" onClick={() => navigate('/leads')}>
          <StatCard label="Needs tier review" value={loading ? '—' : needsReviewCount} accent="amber" sublabel="Untyped leads" />
        </button>
      </div>

      <section className="panel today-briefing-panel">
        <div className="panel-header">
          <div>
            <h2 className="panel-title">Today</h2>
            <p className="today-briefing-subtitle">Your live daily briefing from current lead, reply, cadence, and booking data.</p>
          </div>
        </div>
        {loading ? (
          <div className="empty-state">Building your daily briefing...</div>
        ) : dailyBriefing ? (
          <div className="today-briefing-list">
            {briefingLines.map((item) => (
              <button key={item.key} className={`today-briefing-line today-briefing-line--${item.accent}`} onClick={() => navigate(item.path)}>
                <span className="today-briefing-dot" aria-hidden="true" />
                <span>{item.text}.</span>
              </button>
            ))}
          </div>
        ) : (
          <div className="empty-state">Daily briefing is unavailable right now.</div>
        )}
      </section>

      <section className="overview-chart-grid" aria-label="Real-time advisor charts">
        <article className="panel overview-chart-panel overview-chart-panel--wide">
          <div className="panel-header">
            <div>
              <h2 className="panel-title">Reply activity</h2>
              <p className="chart-subtitle">Inbound replies over the last 14 days.</p>
            </div>
          </div>
          <div className="chart-frame chart-frame--line">
            {replyActivity.length === 0 ? (
              <div className="empty-state">No reply activity data available yet.</div>
            ) : (
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={replyActivity} margin={{ top: 16, right: 18, left: -18, bottom: 2 }}>
                  <CartesianGrid stroke="var(--border-subtle)" strokeDasharray="4 8" vertical={false} />
                  <XAxis dataKey="date" tickFormatter={formatActivityDate} stroke="var(--text-tertiary)" tickLine={false} axisLine={false} minTickGap={18} />
                  <YAxis allowDecimals={false} stroke="var(--text-tertiary)" tickLine={false} axisLine={false} width={36} />
                  <Tooltip contentStyle={chartTooltipStyle} labelFormatter={formatActivityDate} />
                  <Line type="monotone" dataKey="count" name="Replies" stroke="var(--signal-blue)" strokeWidth={3} dot={{ r: 3, strokeWidth: 2 }} activeDot={{ r: 6 }} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </article>

        <article className="panel overview-chart-panel">
          <div className="panel-header">
            <div>
              <h2 className="panel-title">Engagement temperature</h2>
              <p className="chart-subtitle">Current temperature split across your leads.</p>
            </div>
          </div>
          <div className="chart-frame chart-frame--donut">
            {engagementChartData.length === 0 ? (
              <div className="empty-state">No engagement data available yet.</div>
            ) : (
              <>
                <ResponsiveContainer width="100%" height={230}>
                  <PieChart>
                    <Pie data={engagementChartData} dataKey="value" nameKey="name" innerRadius="62%" outerRadius="84%" paddingAngle={4}>
                      {engagementChartData.map((entry) => <Cell key={entry.key} fill={entry.color} />)}
                    </Pie>
                    <Tooltip contentStyle={chartTooltipStyle} />
                  </PieChart>
                </ResponsiveContainer>
                <div className="chart-legend">
                  {engagementChartData.map((entry) => (
                    <span key={entry.key} className="chart-legend-item">
                      <span className="chart-legend-dot" style={{ background: entry.color }} />
                      {entry.name}: {entry.value}
                    </span>
                  ))}
                </div>
              </>
            )}
          </div>
        </article>

        <article className="panel overview-chart-panel">
          <div className="panel-header">
            <div>
              <h2 className="panel-title">Cadence health score</h2>
              <p className="chart-subtitle">% of active cadences not overdue.</p>
            </div>
          </div>
          <div className="chart-frame chart-frame--gauge">
            {cadenceHealth ? (
              <>
                <ResponsiveContainer width="100%" height={220}>
                  <RadialBarChart innerRadius="72%" outerRadius="100%" data={cadenceGaugeData} startAngle={180} endAngle={0}>
                    <PolarAngleAxis type="number" domain={[0, 100]} angleAxisId={0} tick={false} />
                    <RadialBar dataKey="value" cornerRadius={14} background={{ fill: 'rgba(47, 182, 255, 0.12)' }} />
                  </RadialBarChart>
                </ResponsiveContainer>
                <div className="gauge-readout">
                  <strong>{cadenceHealth.health_score}%</strong>
                  <span>{cadenceHealth.healthy_active_count} of {cadenceHealth.active_count} active cadences healthy</span>
                </div>
              </>
            ) : (
              <div className="empty-state">No cadence health data available yet.</div>
            )}
          </div>
        </article>

        <article className="panel overview-chart-panel overview-chart-panel--wide">
          <div className="panel-header">
            <div>
              <h2 className="panel-title">Status funnel</h2>
              <p className="chart-subtitle">Current lead counts by real status stage.</p>
            </div>
          </div>
          <div className="funnel-list">
            {statusFunnel.length === 0 ? (
              <div className="empty-state">No status funnel data available yet.</div>
            ) : statusFunnel.map((stage) => (
              <div key={stage.status} className={`funnel-row funnel-row--${stage.status}`}>
                <div className="funnel-row-meta">
                  <span>{stage.label}</span>
                  <strong>{stage.count}</strong>
                </div>
                <div className="funnel-track" aria-hidden="true">
                  <div className="funnel-bar" style={{ width: `${Math.max(6, Math.round(((stage.count || 0) / maxFunnelCount) * 100))}%` }} />
                </div>
              </div>
            ))}
          </div>
        </article>
      </section>


      <div className="overview-grid">
        <section className="panel">
          <div className="panel-header">
            <h2 className="panel-title">Hot replies</h2>
            <span className="panel-count">{replies.length}</span>
          </div>
          {replies.length === 0 ? (
            <div className="empty-state">No hot replies yet. They'll show up here the moment someone responds with interest.</div>
          ) : (
            <ul className="reply-list">
              {replies.slice(0, 6).map((r) => (
                <li key={r.id} className="reply-item reply-item--clickable" onClick={() => r.lead_id && navigate(`/leads/${r.lead_id}`)}>
                  <SignalPulse color="red" size={6} />
                  <span className="reply-body">{r.body}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2 className="panel-title">Cadence health</h2>
          </div>
          {Object.keys(cadenceSummary).length === 0 ? (
            <div className="empty-state">No active cadences yet. Start one from the Leads screen.</div>
          ) : (
            <ul className="cadence-list">
              {Object.entries(cadenceSummary).map(([status, count]) => (
                <li key={status} className="cadence-row cadence-row--clickable" onClick={() => navigate('/cadence')}>
                  <span className="cadence-status">{status.replace(/_/g, ' ')}</span>
                  <span className="cadence-count">{count}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  )
}
