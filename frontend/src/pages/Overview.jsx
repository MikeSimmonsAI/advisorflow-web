import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, getCurrentUser } from '../api/client'
import './Overview.css'

export default function Overview() {
  const user = getCurrentUser()
  const navigate = useNavigate()
  const [leads, setLeads] = useState([])
  const [replies, setReplies] = useState([])
  const [dailyBriefing, setDailyBriefing] = useState(null)
  const [replyActivity, setReplyActivity] = useState([])
  const [statusFunnel, setStatusFunnel] = useState([])
  const [outcomesSummary, setOutcomesSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [time, setTime] = useState(new Date())
  const [pipelineForecast, setPipelineForecast] = useState(null)

  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    Promise.all([
      api.get('/leads/').catch(() => []),
      api.get('/sms/replies?needs_attention=true').catch(() => []),
      api.get('/leads/daily-briefing').catch(() => null),
      api.get('/sms/replies/activity-by-day?days=14').catch(() => []),
      api.get('/leads/status-funnel').catch(() => []),
      api.get('/outcomes/summary').catch(() => null),
      api.get('/pipeline/forecast').catch(() => null),
    ]).then(([leadsData, repliesData, briefingData, activityData, funnelData, outcomesData, forecastData]) => {
      setLeads(leadsData || [])
      setReplies(repliesData || [])
      setDailyBriefing(briefingData)
      setReplyActivity(activityData || [])
      setStatusFunnel(funnelData || [])
      setOutcomesSummary(outcomesData)
      setPipelineForecast(forecastData)
      setLoading(false)
    })
  }, [])

  const totalLeads   = leads.length
  const newLeads     = leads.filter(l => l.status === 'new').length
  const sentLeads    = leads.filter(l => l.status === 'sent').length
  const bookedLeads  = leads.filter(l => l.status === 'booked').length
  const hotReplies   = replies.length
  const dncLeads     = leads.filter(l => l.status === 'dnc').length
  const replyRate    = sentLeads > 0 ? Math.round((hotReplies / sentLeads) * 100) : 0
  const bookingRate  = sentLeads > 0 ? Math.round((bookedLeads / sentLeads) * 100) : 0

  const firstName = user?.full_name?.split(' ')[0] || 'Advisor'
  const hour = time.getHours()
  const greeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'

  const timeStr = time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  const dateStr = time.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })

  // 14-day sparkline
  const maxActivity = Math.max(1, ...replyActivity.map(d => d.count || 0))
  const maxFunnel   = Math.max(1, ...statusFunnel.map(s => s.count || 0))

  return (
    <div className="ov-page">

      {/* ── HERO HEADER ── */}
      <div className="ov-hero">
        <div className="ov-hero-left">
          <div className="ov-greeting">{greeting}, {firstName}.</div>
          <div className="ov-date">{dateStr}</div>
        </div>
        <div className="ov-clock">{timeStr}</div>
      </div>

      {/* ── KPI ROW ── */}
      <div className="ov-kpi-row">
        {[
          { label: 'Total leads',   value: totalLeads,  accent: '#2fb6ff', path: '/leads',   icon: '👥' },
          { label: 'New — uncontacted', value: newLeads, accent: '#2fb6ff', path: '/leads', icon: '📋' },
          { label: 'Hot replies',   value: hotReplies,  accent: '#ff4d4d', path: '/replies', icon: '🔥' },
          { label: 'Appointments',  value: bookedLeads, accent: '#1ef0a8', path: '/leads',   icon: '📅' },
          { label: 'Reply rate',    value: `${replyRate}%`, accent: '#f0c040', path: '/reports', icon: '📊' },
          { label: 'Booking rate',  value: `${bookingRate}%`, accent: '#a78bfa', path: '/reports', icon: '🎯' },
        ].map(card => (
          <button key={card.label} className="ov-kpi-card" onClick={() => navigate(card.path)}>
            <span className="ov-kpi-icon">{card.icon}</span>
            <strong className="ov-kpi-value" style={{ color: card.accent }}>
              {loading ? '—' : card.value}
            </strong>
            <span className="ov-kpi-label">{card.label}</span>
          </button>
        ))}
      </div>

      {/* ── MAIN GRID ── */}
      <div className="ov-main-grid">

        {/* TODAY'S BRIEFING */}
        <section className="panel ov-panel">
          <div className="panel-header">
            <h2 className="panel-title">⚡ Today's action items</h2>
          </div>
          {loading ? (
            <div className="empty-state">Loading…</div>
          ) : dailyBriefing ? (
            <div className="ov-action-list">
              {[
                { count: dailyBriefing.replies_needing_attention, label: 'hot replies need your response', path: '/replies', accent: '#ff4d4d', urgent: true },
                { count: dailyBriefing.cadence_touches_due_today, label: 'cadence touches due today', path: '/cadence', accent: '#2fb6ff' },
                { count: dailyBriefing.certified_appointments_waiting, label: 'appointments confirmed', path: '/leads', accent: '#1ef0a8' },
                { count: dailyBriefing.leads_imported_last_24h, label: 'leads imported in the last 24h', path: '/leads', accent: '#f0c040' },
                { count: dailyBriefing.bookings_last_7_days, label: 'bookings this week', path: '/leads', accent: '#a78bfa' },
              ].map((item, i) => (
                <button key={i} className={`ov-action-row ${item.urgent && item.count > 0 ? 'ov-action-row--urgent' : ''}`} onClick={() => navigate(item.path)}>
                  <span className="ov-action-count" style={{ color: item.accent }}>{item.count}</span>
                  <span className="ov-action-label">{item.label}</span>
                  <span className="ov-action-arrow">→</span>
                </button>
              ))}
            </div>
          ) : (
            <div className="empty-state">No briefing data yet.</div>
          )}
        </section>

        {/* HOT REPLIES */}
        <section className="panel ov-panel">
          <div className="panel-header">
            <h2 className="panel-title">🔥 Hot replies</h2>
            <span className="panel-count">{replies.length}</span>
          </div>
          {replies.length === 0 ? (
            <div className="empty-state">No hot replies right now. All clear.</div>
          ) : (
            <div className="ov-reply-list">
              {replies.slice(0, 6).map(r => (
                <button key={r.id} className="ov-reply-row" onClick={() => r.lead_id && navigate(`/leads/${r.lead_id}`)}>
                  <span className="ov-reply-dot" />
                  <span className="ov-reply-body">{r.body}</span>
                  <span className="ov-reply-arrow">→</span>
                </button>
              ))}
              {replies.length > 6 && (
                <button className="ov-see-all" onClick={() => navigate('/replies')}>
                  See all {replies.length} replies →
                </button>
              )}
            </div>
          )}
        </section>

        {/* REPLY ACTIVITY SPARKLINE */}
        <section className="panel ov-panel">
          <div className="panel-header">
            <h2 className="panel-title">📈 Reply activity</h2>
            <span className="panel-count">14 days</span>
          </div>
          {replyActivity.length === 0 ? (
            <div className="empty-state">No reply data yet.</div>
          ) : (
            <div className="ov-sparkline">
              {replyActivity.map((d, i) => (
                <div key={i} className="ov-spark-col">
                  <div
                    className="ov-spark-bar"
                    style={{ height: `${Math.max(4, Math.round((d.count / maxActivity) * 80))}px` }}
                    title={`${d.date}: ${d.count} replies`}
                  />
                  {i % 7 === 0 && (
                    <span className="ov-spark-label">
                      {new Date(d.date + 'T00:00:00').toLocaleDateString([], { month: 'short', day: 'numeric' })}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>

        {/* STATUS FUNNEL */}
        <section className="panel ov-panel">
          <div className="panel-header">
            <h2 className="panel-title">🏆 Pipeline funnel</h2>
          </div>
          {statusFunnel.length === 0 ? (
            <div className="empty-state">No funnel data yet.</div>
          ) : (
            <div className="ov-funnel">
              {statusFunnel.map(stage => (
                <div key={stage.status} className="ov-funnel-row">
                  <span className="ov-funnel-label">{stage.label}</span>
                  <div className="ov-funnel-track">
                    <div
                      className="ov-funnel-fill"
                      style={{ width: `${Math.max(2, Math.round((stage.count / maxFunnel) * 100))}%` }}
                    />
                  </div>
                  <strong className="ov-funnel-count">{stage.count}</strong>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* QUICK ACTIONS */}
        <section className="panel ov-panel">
          <div className="panel-header">
            <h2 className="panel-title">⚙️ Quick actions</h2>
          </div>
          <div className="ov-quick-grid">
            {[
              { label: 'Import leads',     icon: '📥', path: '/leads',          desc: 'Upload CSV or Excel' },
              { label: 'Send campaign',    icon: '📣', path: '/campaigns',      desc: 'AI-powered outreach' },
              { label: 'Review replies',   icon: '💬', path: '/replies',        desc: `${hotReplies} waiting` },
              { label: 'Email queue',      icon: '📧', path: '/email-queue',    desc: 'Draft & send emails' },
              { label: 'Work queue',       icon: '✅', path: '/work-queue',     desc: 'Today\'s action items' },
              { label: 'Lead cleanup',     icon: '🧹', path: '/lead-cleanup',   desc: 'Merge duplicates' },
            ].map(item => (
              <button key={item.label} className="ov-quick-btn" onClick={() => navigate(item.path)}>
                <span className="ov-quick-icon">{item.icon}</span>
                <span className="ov-quick-label">{item.label}</span>
                <span className="ov-quick-desc">{item.desc}</span>
              </button>
            ))}
          </div>
        </section>

        {/* REVENUE SUMMARY */}
        <section className="panel ov-panel">
          <div className="panel-header">
            <h2 className="panel-title">💰 Revenue activity</h2>
          </div>
          <div className="ov-revenue-grid">
            {[
              { label: 'In pipeline',  value: bookedLeads,                             sub: 'Booked appointments',     color: '#2fb6ff' },
              { label: 'Outcomes',     value: outcomesSummary?.total_appointments ?? 0, sub: 'Recorded visits',        color: '#1ef0a8' },
              { label: 'Sales',        value: outcomesSummary?.sales_count ?? 0,        sub: outcomesSummary?.conversion_rate != null ? `${outcomesSummary.conversion_rate}% close rate` : 'No outcomes yet', color: '#a78bfa' },
              { label: 'DNC',          value: dncLeads,                                sub: 'Opted out — suppressed',  color: '#ff4d4d' },
            ].map(item => (
              <div key={item.label} className="ov-revenue-cell">
                <strong className="ov-revenue-value" style={{ color: item.color }}>
                  {loading ? '—' : item.value}
                </strong>
                <span className="ov-revenue-label">{item.label}</span>
                <span className="ov-revenue-sub">{item.sub}</span>
              </div>
            ))}
          </div>
        </section>

      </div>
      {/* AI Forecast + Pipeline Summary */}
      {pipelineForecast && (
        <div className="panel" style={{ marginTop: 0 }}>
          <div className="panel-header">
            <h2 className="panel-title">🤖 AI Forecast</h2>
            <button className="btn btn--secondary" style={{ fontSize: 12, padding: '4px 12px' }}
              onClick={() => window.location.href = '/pipeline'}>
              Open pipeline →
            </button>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12, marginBottom: 16 }}>
            {[
              { label: 'Active conversations', value: pipelineForecast.active_conversations, color: '#2fb6ff' },
              { label: 'Reply rate', value: `${pipelineForecast.reply_rate}%`, color: '#1ef0a8' },
              { label: 'Need your review', value: pipelineForecast.flagged_count, color: '#ff4d4d' },
              { label: 'Projected bookings', value: pipelineForecast.projected_bookings_this_week, color: '#ffd700' },
            ].map(item => (
              <div key={item.label} className="ov-revenue-cell">
                <strong className="ov-revenue-value" style={{ color: item.color }}>{item.value}</strong>
                <span className="ov-revenue-label">{item.label}</span>
              </div>
            ))}
          </div>
          {pipelineForecast.alerts?.map((alert, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px',
              borderRadius: 10, marginBottom: 6, cursor: 'pointer',
              background: alert.type === 'urgent' ? 'rgba(255,77,77,0.07)' : 'rgba(47,182,255,0.05)',
              border: `1px solid ${alert.type === 'urgent' ? 'rgba(255,77,77,0.18)' : 'rgba(47,182,255,0.12)'}`,
            }} onClick={() => window.location.href = alert.path}>
              <span>{alert.type === 'urgent' ? '⚠️' : '💡'}</span>
              <span style={{ flex: 1, fontSize: 13 }}>{alert.message}</span>
              <span style={{ fontSize: 12, color: 'var(--accent)', fontWeight: 600 }}>{alert.action} →</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
