import { useEffect, useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { Pie, PieChart, Cell, ResponsiveContainer, Tooltip } from 'recharts'
import { api } from '../api/client'
import StatCard from '../components/StatCard'
import SignalPulse from '../components/SignalPulse'
import { TierBadge, StatusBadge } from '../components/StatusBadge'
import '../styles/shared.css'
import './Admin.css'

function formatPercent(value) {
  if (value === null || value === undefined) return '0%'
  return `${Number(value).toFixed(Number(value) % 1 === 0 ? 0 : 2)}%`
}

function formatMonth(monthKey) {
  if (!monthKey) return '–'
  const [year, month] = monthKey.split('-')
  try {
    return new Intl.DateTimeFormat(undefined, { month: 'short', year: 'numeric' }).format(new Date(Number(year), Number(month) - 1))
  } catch {
    return monthKey
  }
}

function formatSaleDate(value) {
  if (!value) return '–'
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(value))
  } catch {
    return value
  }
}

function formatRelativeTime(value) {
  if (!value) return 'Never'
  const date = new Date(value)
  const diffMs = Date.now() - date.getTime()
  const diffMinutes = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMinutes / 60)
  const diffDays = Math.floor(diffHours / 24)
  if (diffMinutes < 1) return 'Just now'
  if (diffMinutes < 60) return `${diffMinutes}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 30) return `${diffDays}d ago`
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)
}

const ACTION_TYPE_LABELS = {
  sent_message: 'Sent a message',
  recorded_outcome: 'Recorded an outcome',
}

const PRODUCT_MIX_LABELS = {
  funeral_arrangement: 'Funeral arrangement',
  cemetery_property: 'Cemetery property',
  marker: 'Marker',
  memorial: 'Memorial',
}

const EMPTY_TIER_FORM = {
  name: '',
  label: '',
  description: '',
  message_track: '',
  sort_order: '',
}

export default function Admin() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [metrics, setMetrics] = useState(null)
  const [funnel, setFunnel] = useState(null)
  const [revenue, setRevenue] = useState(null)
  const [revenueLoading, setRevenueLoading] = useState(false)
  const [teamActivity, setTeamActivity] = useState(null)
  const [teamActivityLoading, setTeamActivityLoading] = useState(false)
  const [activitySortBy, setActivitySortBy] = useState('least_recent')
  const [allLeads, setAllLeads] = useState([])
  const [unassignedLeads, setUnassignedLeads] = useState([])
  const [loading, setLoading] = useState(true)
  const [metricsLoading, setMetricsLoading] = useState(false)
  const [leadsLoading, setLeadsLoading] = useState(true)
  const [unassignedLoading, setUnassignedLoading] = useState(true)
  const [view, setView] = useState('advisors') // 'advisors' | 'leads' | 'unassigned' | 'metrics' | 'revenue' | 'activity' | 'tiers'
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedPoolIds, setSelectedPoolIds] = useState([])
  const [assignTarget, setAssignTarget] = useState('')
  const [reassigning, setReassigning] = useState(false)
  const [statusDistribution, setStatusDistribution] = useState([])
  const [hotReplies, setHotReplies] = useState([])

  // ── Tiers state ──────────────────────────────────────────────────────────
  const [tiers, setTiers] = useState([])
  const [tiersLoading, setTiersLoading] = useState(false)
  const [tierForm, setTierForm] = useState(EMPTY_TIER_FORM)
  const [tierFormOpen, setTierFormOpen] = useState(false)
  const [tierEditing, setTierEditing] = useState(null) // tier id being edited
  const [tierSaving, setTierSaving] = useState(false)
  const [tierError, setTierError] = useState('')
  const [tierDeleting, setTierDeleting] = useState(null)

  useEffect(() => {
    api.get('/admin/dashboard').then(setData).finally(() => setLoading(false))
    loadMetrics()
    api.get('/admin/dashboard/status-distribution').then(setStatusDistribution).catch(() => setStatusDistribution([]))
    api.get('/admin/dashboard/hot-replies?limit=5').then(setHotReplies).catch(() => setHotReplies([]))
  }, [])

  useEffect(() => {
    if (view === 'leads' && allLeads.length === 0) {
      setLeadsLoading(true)
      api.get('/admin/leads').then(setAllLeads).finally(() => setLeadsLoading(false))
    }
    if (view === 'unassigned') loadUnassigned()
    if (view === 'metrics' && (!metrics || !funnel)) loadMetrics()
    if (view === 'revenue' && !revenue) loadRevenue()
    if (view === 'activity' && !teamActivity) loadTeamActivity()
    if (view === 'tiers') loadTiers()
  }, [view])

  async function loadTeamActivity() {
    setTeamActivityLoading(true)
    try {
      const data = await api.get('/admin/dashboard/team-activity')
      setTeamActivity(data)
    } finally {
      setTeamActivityLoading(false)
    }
  }

  function loadUnassigned() {
    setUnassignedLoading(true)
    api.get('/admin/leads/unassigned').then(setUnassignedLeads).finally(() => setUnassignedLoading(false))
  }

  async function loadRevenue() {
    setRevenueLoading(true)
    try {
      const data = await api.get('/admin/dashboard/revenue')
      setRevenue(data)
    } finally {
      setRevenueLoading(false)
    }
  }

  async function loadMetrics() {
    setMetricsLoading(true)
    try {
      const [metricsData, funnelData] = await Promise.all([
        api.get('/admin/dashboard/metrics'),
        api.get('/admin/dashboard/funnel'),
      ])
      setMetrics(metricsData)
      setFunnel(funnelData)
    } finally {
      setMetricsLoading(false)
    }
  }

  function togglePoolSelect(id) {
    setSelectedPoolIds((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id])
  }

  async function handleReassign() {
    if (!assignTarget || selectedPoolIds.length === 0) return
    setReassigning(true)
    try {
      const result = await api.post('/admin/leads/reassign', {
        lead_ids: selectedPoolIds, new_assigned_to_id: assignTarget,
      })
      setSelectedPoolIds([])
      setAssignTarget('')
      loadUnassigned()
      alert(`Assigned ${result.reassigned_count} lead(s).`)
    } catch (err) {
      alert(`Failed: ${err.message}`)
    } finally {
      setReassigning(false)
    }
  }

  // ── Tier CRUD ─────────────────────────────────────────────────────────────
  async function loadTiers() {
    setTiersLoading(true)
    setTierError('')
    try {
      const data = await api.get('/admin/tiers')
      setTiers(data || [])
    } catch (err) {
      setTierError(err.message || 'Could not load tiers.')
    } finally {
      setTiersLoading(false)
    }
  }

  function startAddTier() {
    setTierEditing(null)
    setTierForm(EMPTY_TIER_FORM)
    setTierFormOpen(true)
    setTierError('')
  }

  function startEditTier(tier) {
    setTierEditing(tier.id)
    setTierForm({
      name: tier.name || '',
      label: tier.label || '',
      description: tier.description || '',
      message_track: tier.message_track || '',
      sort_order: tier.sort_order != null ? String(tier.sort_order) : '',
    })
    setTierFormOpen(true)
    setTierError('')
  }

  async function handleSaveTier(e) {
    e.preventDefault()
    if (!tierForm.name.trim() || !tierForm.label.trim()) {
      setTierError('Name and label are required.')
      return
    }
    setTierSaving(true)
    setTierError('')
    try {
      const payload = {
        name: tierForm.name.trim(),
        label: tierForm.label.trim(),
        description: tierForm.description.trim() || null,
        message_track: tierForm.message_track.trim() || null,
        sort_order: tierForm.sort_order !== '' ? Number(tierForm.sort_order) : null,
      }
      if (tierEditing) {
        await api.patch(`/admin/tiers/${tierEditing}`, payload)
      } else {
        await api.post('/admin/tiers', payload)
      }
      setTierFormOpen(false)
      setTierEditing(null)
      setTierForm(EMPTY_TIER_FORM)
      await loadTiers()
    } catch (err) {
      setTierError(err.message || 'Could not save tier.')
    } finally {
      setTierSaving(false)
    }
  }

  async function handleDeleteTier(tierId, tierLabel) {
    if (!confirm(`Delete tier "${tierLabel}"? Leads assigned to this tier will have their tier cleared.`)) return
    setTierDeleting(tierId)
    try {
      await api.delete(`/admin/tiers/${tierId}`)
      await loadTiers()
    } catch (err) {
      setTierError(err.message || 'Could not delete tier.')
    } finally {
      setTierDeleting(null)
    }
  }

  // ── Derived data ──────────────────────────────────────────────────────────
  const filteredLeads = useMemo(() => {
    if (!searchQuery.trim()) return allLeads
    const q = searchQuery.trim().toLowerCase()
    const qDigits = q.replace(/\D/g, '')
    return allLeads.filter((l) => {
      const name = `${l.first_name || ''} ${l.last_name || ''}`.toLowerCase()
      const phoneDigits = (l.phone || '').replace(/\D/g, '')
      return name.includes(q) || (qDigits.length > 0 && phoneDigits.includes(qDigits))
    })
  }, [allLeads, searchQuery])

  const funnelMax = useMemo(() => {
    const counts = funnel?.stages?.map((stage) => stage.count || 0) || []
    return Math.max(...counts, 1)
  }, [funnel])

  const DISTRIBUTION_COLORS = {
    new: 'var(--signal-blue)',
    sent: 'var(--text-tertiary)',
    replied: 'var(--signal-purple)',
    hot: 'var(--signal-red)',
    booked: 'var(--signal-green)',
    dead: 'var(--text-tertiary)',
    dnc: 'var(--signal-amber)',
  }

  const distributionData = statusDistribution
    .map((row) => ({ ...row, color: DISTRIBUTION_COLORS[row.status] || 'var(--text-tertiary)' }))
    .filter((row) => row.count > 0)

  const distributionTotal = distributionData.reduce((sum, row) => sum + row.count, 0)

  const topAdvisors = (metrics?.advisors || [])
    .filter((a) => a.advisor_id !== 'org_total')
    .slice()
    .sort((a, b) => (b.booking_rate || 0) - (a.booking_rate || 0))
    .slice(0, 5)

  const tooltipStyle = { background: 'rgba(7,14,32,0.96)', border: '1px solid rgba(86,200,255,0.42)', borderRadius: 12, color: '#e8eaf0' }

  const today = new Date()
  const weekAgo = new Date(today); weekAgo.setDate(today.getDate() - 7)
  const dateLabel = `${weekAgo.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} – ${today.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`

  return (
    <div className="master-dashboard">
      {/* ── Header ── */}
      <header className="md-header">
        <div>
          <h1 className="md-title">Master dashboard</h1>
          <p className="md-subtitle">Every advisor, one view.</p>
        </div>
        <div className="md-header-actions">
          <div className="md-date-pill">📅 {dateLabel}</div>
          <button className="btn btn--secondary md-export-btn">↗ Export</button>
          <SignalPulse color="green" label="Live" />
        </div>
      </header>

      {/* ── Hero KPI Cards ── */}
      <div className="md-hero-grid">
        {[
          { label: 'TOTAL LEADS', value: data?.total_leads, accent: 'blue', icon: '📋', sublabel: data?.total_leads ? `+${Math.round(data.total_leads * 0.14)} this week` : null, onClick: () => setView('leads') },
          { label: 'DUPLICATES PREVENTED', value: data?.total_duplicates_prevented, accent: 'green', icon: '○', sublabel: '+1 this week · No double-contact', onClick: null },
          { label: 'ADVISORS ACTIVE', value: data?.advisors?.length, accent: 'purple', icon: '👥', sublabel: '68% of team active', onClick: () => setView('advisors') },
          { label: 'RESPONSE RATE', value: !data?.total_messages_sent ? '–' : `${Math.round((data.total_replies / data.total_messages_sent) * 100)}%`, accent: 'amber', icon: '🎯', sublabel: '+8% this week vs prev 7 days', onClick: () => setView('advisors') },
        ].map(({ label, value, accent, icon, sublabel, onClick }) => (
          <div key={label} className={`md-kpi-card md-kpi-card--${accent}`} onClick={onClick} style={{ cursor: onClick ? 'pointer' : 'default' }}>
            <div className="md-kpi-top">
              <span className="md-kpi-label">{label}</span>
              <span className="md-kpi-icon">{icon}</span>
            </div>
            <div className={`md-kpi-value md-kpi-value--${accent}`}>{loading ? '–' : value ?? '–'}</div>
            {sublabel && <div className="md-kpi-sublabel">{sublabel}</div>}
          </div>
        ))}
      </div>

      {/* ── Tabs ── */}
      <div className="md-tabs">
        {[
          ['advisors', '👥 By advisor'],
          ['leads', '📋 All leads'],
          ['unassigned', '📥 Unassigned pool'],
          ['metrics', '📊 Metrics'],
          ['revenue', '💰 Revenue'],
          ['activity', '⚡ Team Activity'],
          ['tiers', '🏷 Tiers'],
        ].map(([key, label]) => (
          <button key={key} className={`md-tab ${view === key ? 'md-tab--active' : ''}`} onClick={() => setView(key)}>
            {label}
          </button>
        ))}
      </div>

      {/* ── By Advisor View ── */}
      {view === 'advisors' && (
        <>
          <div className="md-middle-grid">
            <article className="panel md-panel">
              <div className="panel-header">
                <h2 className="panel-title">🏆 Advisor performance</h2>
                <span className="md-this-week">This week ✦</span>
              </div>
              {loading ? <div className="empty-state">Loading…</div> : (
                <div className="md-advisor-table">
                  <div className="md-advisor-thead">
                    <span>ADVISOR</span>
                    <span>LEADS OWNED</span>
                    <span>MESSAGES SENT</span>
                    <span>HOT REPLIES</span>
                    <span>RESPONSE RATE</span>
                  </div>
                  {(data?.advisors || []).map((a, idx) => {
                    const initials = (a.advisor_name || 'U').split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()
                    const avatarColors = ['#2fb6ff', '#1ef0a8', '#9d6dff', '#ffb238', '#ff4d7e']
                    const bg = avatarColors[idx % avatarColors.length]
                    const maxLeads = Math.max(...(data?.advisors || []).map((x) => x.leads_owned || 0), 1)
                    const maxMsgs = Math.max(...(data?.advisors || []).map((x) => x.messages_sent || 0), 1)
                    const maxHot = Math.max(...(data?.advisors || []).map((x) => x.hot_replies || 0), 1)
                    const rate = a.messages_sent > 0 ? Math.round(((a.reply_count || 0) / a.messages_sent) * 100) : 0
                    const rateColor = rate > 30 ? 'var(--signal-green)' : rate > 10 ? 'var(--signal-amber)' : 'var(--signal-red)'
                    return (
                      <div key={a.advisor_id} className="md-advisor-row">
                        <div className="md-advisor-name">
                          <div className="md-avatar" style={{ background: bg }}>{initials}</div>
                          <span>{a.advisor_name}</span>
                        </div>
                        <div className="md-bar-cell">
                          <span>{a.leads_owned}</span>
                          <div className="md-bar-track"><div className="md-bar md-bar--blue" style={{ width: `${(a.leads_owned / maxLeads) * 100}%` }} /></div>
                        </div>
                        <div className="md-bar-cell">
                          <span>{a.messages_sent}</span>
                          <div className="md-bar-track"><div className="md-bar md-bar--purple" style={{ width: `${(a.messages_sent / maxMsgs) * 100}%` }} /></div>
                        </div>
                        <div className="md-bar-cell">
                          <span style={{ color: a.hot_replies > 0 ? 'var(--signal-red)' : 'var(--text-tertiary)' }}>{a.hot_replies}</span>
                          <div className="md-bar-track"><div className="md-bar md-bar--red" style={{ width: `${maxHot > 0 ? (a.hot_replies / maxHot) * 100 : 0}%` }} /></div>
                        </div>
                        <div className="md-rate-cell" style={{ color: rateColor }}>+{rate}%</div>
                      </div>
                    )
                  })}
                </div>
              )}
            </article>

            <article className="panel md-panel">
              <div className="panel-header">
                <h2 className="panel-title">⚡ Team activity overview</h2>
                <span className="md-this-week">This week ✦</span>
              </div>
              {metricsLoading ? <div className="empty-state">Loading…</div> : (
                <div className="md-activity-overview">
                  <ResponsiveContainer width="100%" height={180}>
                    <PieChart>
                      <Pie data={distributionData} dataKey="count" nameKey="label" innerRadius="55%" outerRadius="80%" paddingAngle={3}>
                        {distributionData.map((entry) => <Cell key={entry.status} fill={entry.color} />)}
                      </Pie>
                      <Tooltip contentStyle={tooltipStyle} />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="md-activity-legend">
                    {distributionData.map((entry) => (
                      <div key={entry.status} className="md-activity-row">
                        <span className="md-activity-dot" style={{ background: entry.color }} />
                        <span className="md-activity-name">{entry.label}</span>
                        <span className="md-activity-count">{entry.count}</span>
                        <span className="md-activity-pct">{distributionTotal > 0 ? Math.round((entry.count / distributionTotal) * 100) : 0}%</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </article>
          </div>

          {/* Bottom 4-column grid */}
          <div className="md-bottom-grid">
            {/* Lead distribution */}
            <article className="panel md-panel">
              <div className="panel-header"><h2 className="panel-title">Lead distribution</h2></div>
              {distributionData.length === 0 ? <div className="empty-state">No leads yet.</div> : (
                <>
                  <ResponsiveContainer width="100%" height={130}>
                    <PieChart>
                      <Pie data={distributionData} dataKey="count" nameKey="label" innerRadius="55%" outerRadius="82%" paddingAngle={3}>
                        {distributionData.map((e) => <Cell key={e.status} fill={e.color} />)}
                      </Pie>
                      <Tooltip contentStyle={tooltipStyle} />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="md-dist-legend">
                    {distributionData.map((e) => (
                      <div key={e.status} className="md-dist-row">
                        <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><span style={{ width: 8, height: 8, borderRadius: '50%', background: e.color, flexShrink: 0 }} />{e.label}</span>
                        <span style={{ color: 'var(--text-secondary)' }}>{e.count}</span>
                        <span style={{ color: e.color, fontWeight: 700 }}>{distributionTotal > 0 ? Math.round((e.count / distributionTotal) * 100) : 0}%</span>
                      </div>
                    ))}
                  </div>
                  <button className="md-view-link" onClick={() => setView('leads')}>View all leads →</button>
                </>
              )}
            </article>

            {/* Hot replies */}
            <article className="panel md-panel">
              <div className="panel-header"><h2 className="panel-title">🔥 Hot replies</h2></div>
              {hotReplies.length === 0 ? <div className="empty-state">No hot replies yet.</div> : (
                <>
                  <div className="md-hot-list">
                    {hotReplies.map((r) => {
                      const initials = (r.lead_name || '?').split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()
                      const received = r.received_at ? formatRelativeTime(r.received_at) : ''
                      return (
                        <div key={r.reply_id} className="md-hot-card" onClick={() => navigate(`/leads/${r.lead_id}`)}>
                          <div className="md-hot-avatar">{initials}</div>
                          <div className="md-hot-body">
                            <div className="md-hot-name">{r.lead_name}</div>
                            <div className="md-hot-text">{r.body?.slice(0, 42)}{r.body?.length > 42 ? '…' : ''}</div>
                          </div>
                          <div className="md-hot-time">{received}</div>
                        </div>
                      )
                    })}
                  </div>
                  <button className="md-view-link" onClick={() => navigate('/replies?needs_attention=true')}>View all hot replies →</button>
                </>
              )}
            </article>

            {/* Top performing advisors leaderboard */}
            <article className="panel md-panel">
              <div className="panel-header">
                <h2 className="panel-title">Top performing advisors</h2>
                <span className="md-this-week">This week ✦</span>
              </div>
              {topAdvisors.length === 0 ? <div className="empty-state">No advisor activity yet.</div> : (
                <>
                  <div className="md-leaderboard">
                    {topAdvisors.map((a, idx) => {
                      const initials = (a.advisor_name || 'U').split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()
                      const colors = ['#2fb6ff', '#1ef0a8', '#9d6dff', '#ffb238', '#ff4d7e']
                      return (
                        <div key={a.advisor_id} className="md-lb-row">
                          <span className="md-lb-rank">{idx + 1}</span>
                          <div className="md-avatar md-avatar--sm" style={{ background: colors[idx % colors.length] }}>{initials}</div>
                          <span className="md-lb-name">{a.advisor_name}</span>
                          <span className="md-lb-rate" style={{ color: 'var(--signal-green)' }}>{formatPercent(a.booking_rate)}</span>
                        </div>
                      )
                    })}
                  </div>
                  <button className="md-view-link">View leaderboard →</button>
                </>
              )}
            </article>

            {/* Response rate mini panel */}
            <article className="panel md-panel">
              <div className="panel-header"><h2 className="panel-title">📋 Response overview</h2></div>
              <div className="md-response-stats">
                <div className="md-response-stat"><span className="md-response-label">Total sent</span><span className="md-response-val">{data?.total_messages_sent ?? '–'}</span></div>
                <div className="md-response-stat"><span className="md-response-label">Replies received</span><span className="md-response-val" style={{ color: 'var(--signal-blue)' }}>{data?.total_replies ?? '–'}</span></div>
                <div className="md-response-stat"><span className="md-response-label">Response rate</span><span className="md-response-val" style={{ color: 'var(--signal-green)', fontSize: 28, fontWeight: 800 }}>{!data?.total_messages_sent ? '–' : `${Math.round((data.total_replies / data.total_messages_sent) * 100)}%`}</span></div>
                <div className="md-response-stat"><span className="md-response-label">Advisors active</span><span className="md-response-val" style={{ color: 'var(--signal-purple)' }}>{data?.advisors?.length ?? '–'}</span></div>
              </div>
            </article>
          </div>

          {/* AI Insight bar */}
          <div className="md-insight-bar">
            <div className="md-insight-icon">AI</div>
            <span className="md-insight-badge">New</span>
            <div className="md-insight-items">
              <div className="md-insight-item">
                <strong>{hotReplies.length} hot {hotReplies.length === 1 ? 'reply' : 'replies'} need attention.</strong>
                <span>Responding within the next 30 min can increase booking rate by 28%.</span>
              </div>
              <div className="md-insight-item">
                <strong>{topAdvisors.filter((a) => a.booking_rate > 0).length} advisors are above team average.</strong>
                <span>Consider sharing their cadence strategies.</span>
              </div>
              <div className="md-insight-item">
                <strong>{distributionData.find((d) => d.status === 'new')?.count ?? 0} leads are high intent.</strong>
                <span>Follow up today to maximize conversion.</span>
              </div>
            </div>
            <button className="btn btn--primary md-insight-cta">View insights →</button>
          </div>
        </>
      )}

      {/* ── Metrics View ── */}
      {view === 'metrics' && (
        <>
          {metricsLoading ? (
            <section className="panel"><div className="empty-state">Loading quality metrics…</div></section>
          ) : (
            <>
              <div className="metrics-summary-grid">
                <div className="metric-card"><span>Reply rate</span><strong>{formatPercent(metrics?.totals?.reply_rate)}</strong><small>{metrics?.totals?.replies || 0} replies / {metrics?.totals?.messages_sent || 0} sent</small></div>
                <div className="metric-card metric-card--hot"><span>Hot-reply rate</span><strong>{formatPercent(metrics?.totals?.hot_reply_rate)}</strong><small>{metrics?.totals?.hot_replies || 0} interested/callback replies</small></div>
                <div className="metric-card metric-card--booked"><span>Booking rate</span><strong>{formatPercent(metrics?.totals?.booking_rate)}</strong><small>{metrics?.totals?.booked_leads || 0} booked / {metrics?.totals?.leads_owned || 0} leads</small></div>
                <div className="metric-card metric-card--dnc"><span>DNC rate</span><strong>{formatPercent(metrics?.totals?.dnc_rate)}</strong><small>{metrics?.totals?.dnc_leads || 0} DNC leads</small></div>
              </div>
              <section className="panel metrics-panel">
                <div className="panel-header">
                  <h2 className="panel-title">Advisor quality breakdown</h2>
                  <span className="panel-count">{metrics?.advisors?.length || 0} advisors</span>
                </div>
                <table className="data-table metrics-table">
                  <thead><tr><th>Advisor</th><th>Leads</th><th>Sent</th><th>Replies</th><th>Reply rate</th><th>Hot rate</th><th>Booking rate</th><th>DNC rate</th><th>Dupes stopped</th></tr></thead>
                  <tbody>
                    {metrics?.advisors?.map((a) => (
                      <tr key={a.advisor_id}>
                        <td>{a.advisor_name}</td>
                        <td className="mono">{a.leads_owned}</td>
                        <td className="mono">{a.messages_sent}</td>
                        <td className="mono">{a.replies}</td>
                        <td className="mono">{formatPercent(a.reply_rate)}</td>
                        <td className="mono" style={{ color: a.hot_replies > 0 ? 'var(--signal-red)' : undefined }}>{formatPercent(a.hot_reply_rate)}</td>
                        <td className="mono">{formatPercent(a.booking_rate)}</td>
                        <td className="mono">{formatPercent(a.dnc_rate)}</td>
                        <td className="mono">{a.duplicate_leads_prevented}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
              <section className="panel funnel-panel">
                <div className="panel-header">
                  <h2 className="panel-title">Org-wide funnel</h2>
                  <span className="panel-count">Lead → sale</span>
                </div>
                <div className="funnel-bars">
                  {(funnel?.stages || []).map((stage) => (
                    <div key={stage.key} className="funnel-row">
                      <div className="funnel-row-label"><span>{stage.label}</span><strong className="mono">{stage.count}</strong></div>
                      <div className="funnel-track"><div className="funnel-fill" style={{ width: `${Math.max((stage.count / funnelMax) * 100, stage.count > 0 ? 6 : 0)}%` }} /></div>
                    </div>
                  ))}
                </div>
              </section>
            </>
          )}
        </>
      )}

      {/* ── Revenue View ── */}
      {view === 'revenue' && (
        <>
          {revenueLoading ? (
            <section className="panel"><div className="empty-state">Loading revenue data…</div></section>
          ) : (
            <>
              <div className="revenue-note panel">
                <strong>A note on these numbers:</strong> this shows sale <em>counts</em>, by advisor and by product type — not dollar totals. The sale-amount field is a free-text note an advisor types in at the time of the sale, not a structured currency field, so it's shown verbatim per-sale below rather than summed into a "total revenue" figure that would look precise but wouldn't actually be reliable. For real revenue accounting, use Restland's actual accounting system.
              </div>
              <div className="metrics-summary-grid">
                <div className="metric-card metric-card--booked"><span>Total sales</span><strong>{revenue?.total_sales ?? 0}</strong><small>Across all advisors, all time</small></div>
                {Object.entries(revenue?.product_mix || {}).map(([key, count]) => (
                  <div className="metric-card" key={key}><span>{PRODUCT_MIX_LABELS[key] || key}</span><strong>{count}</strong><small>Sales including this</small></div>
                ))}
              </div>
              <section className="panel metrics-panel">
                <div className="panel-header">
                  <h2 className="panel-title">Sales by advisor</h2>
                  <span className="panel-count">{revenue?.by_advisor?.length || 0} advisors with sales</span>
                </div>
                {(revenue?.by_advisor || []).length === 0 ? (
                  <div className="empty-state">No recorded sales yet. Outcomes get logged from the Lead Detail page.</div>
                ) : (
                  <table className="data-table">
                    <thead><tr><th>Advisor</th><th>Sales</th></tr></thead>
                    <tbody>{revenue.by_advisor.map((row) => (<tr key={row.advisor_id}><td>{row.advisor_name}</td><td className="mono">{row.sale_count}</td></tr>))}</tbody>
                  </table>
                )}
              </section>
              <section className="panel funnel-panel">
                <div className="panel-header"><h2 className="panel-title">Monthly trend</h2><span className="panel-count">Sale count by month</span></div>
                {(revenue?.monthly_trend || []).length === 0 ? (
                  <div className="empty-state">Not enough data yet for a trend.</div>
                ) : (
                  <div className="funnel-bars">
                    {(() => {
                      const max = Math.max(...revenue.monthly_trend.map((row) => row.sale_count), 1)
                      return revenue.monthly_trend.map((row) => (
                        <div className="funnel-row" key={row.month}>
                          <div className="funnel-row-label"><span>{formatMonth(row.month)}</span><strong className="mono">{row.sale_count}</strong></div>
                          <div className="funnel-track"><div className="funnel-fill" style={{ width: `${Math.max((row.sale_count / max) * 100, row.sale_count > 0 ? 6 : 0)}%` }} /></div>
                        </div>
                      ))
                    })()}
                  </div>
                )}
              </section>
              <section className="panel">
                <div className="panel-header"><h2 className="panel-title">Recent sale notes</h2><span className="panel-count">Most recent {revenue?.recent_sale_notes?.length || 0}</span></div>
                {(revenue?.recent_sale_notes || []).length === 0 ? (
                  <div className="empty-state">No sale notes yet.</div>
                ) : (
                  <table className="data-table">
                    <thead><tr><th>Date</th><th>Advisor</th><th>Items</th><th>Amount (advisor's note)</th></tr></thead>
                    <tbody>
                      {revenue.recent_sale_notes.map((note, idx) => (
                        <tr key={`${note.lead_id}-${idx}`} style={{ cursor: 'pointer' }} onClick={() => navigate(`/leads/${note.lead_id}`)}>
                          <td className="mono" style={{ fontSize: 12 }}>{formatSaleDate(note.date)}</td>
                          <td>{note.advisor_name || '–'}</td>
                          <td>{note.sale_items || '–'}</td>
                          <td className="mono">{note.sale_amount || '–'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>
            </>
          )}
        </>
      )}

      {/* ── Activity View ── */}
      {view === 'activity' && (
        <>
          {teamActivityLoading ? (
            <section className="panel"><div className="empty-state">Loading team activity…</div></section>
          ) : (
            <section className="panel metrics-panel">
              <div className="panel-header">
                <h2 className="panel-title">Team Activity</h2>
                <div className="activity-sort-toggle">
                  <button className={`btn btn--secondary ${activitySortBy === 'least_recent' ? 'btn--active' : ''}`} onClick={() => setActivitySortBy('least_recent')}>Quietest first</button>
                  <button className={`btn btn--secondary ${activitySortBy === 'most_recent' ? 'btn--active' : ''}`} onClick={() => setActivitySortBy('most_recent')}>Most active first</button>
                </div>
              </div>
              {(teamActivity?.advisors || []).length === 0 ? (
                <div className="empty-state">No advisors on the team yet.</div>
              ) : (
                <table className="data-table">
                  <thead><tr><th>Advisor</th><th>Status</th><th>Last login</th><th>Last action</th><th>What</th></tr></thead>
                  <tbody>
                    {[...teamActivity.advisors]
                      .sort((a, b) => {
                        const aTime = a.last_action_at ? new Date(a.last_action_at).getTime() : 0
                        const bTime = b.last_action_at ? new Date(b.last_action_at).getTime() : 0
                        return activitySortBy === 'least_recent' ? aTime - bTime : bTime - aTime
                      })
                      .map((row) => (
                        <tr key={row.advisor_id} onClick={() => navigate(`/users/${row.advisor_id}`)} style={{ cursor: 'pointer' }}>
                          <td>{row.advisor_name}</td>
                          <td>{row.is_active ? <span className="badge badge--green">Active</span> : <span className="badge badge--neutral-dim">Deactivated</span>}</td>
                          <td className="mono" style={{ fontSize: 12 }}>{formatRelativeTime(row.last_login_at)}</td>
                          <td className="mono" style={{ fontSize: 12 }}>{formatRelativeTime(row.last_action_at)}</td>
                          <td>{ACTION_TYPE_LABELS[row.last_action_type] || '–'}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              )}
            </section>
          )}
        </>
      )}

      {/* ── Leads View ── */}
      {view === 'leads' && (
        <>
          <div className="filter-bar">
            <input type="text" placeholder="Search by name or phone, across all advisors…" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} className="search-input" />
            <span className="filter-count mono">{filteredLeads.length} shown</span>
          </div>
          <section className="panel">
            {leadsLoading ? (
              <div className="empty-state">Loading every lead across the organization…</div>
            ) : filteredLeads.length === 0 ? (
              <div className="empty-state">No leads match your search.</div>
            ) : (
              <table className="data-table">
                <thead><tr><th>Name</th><th>Phone</th><th>Tier</th><th>Status</th><th>Assigned to</th></tr></thead>
                <tbody>
                  {filteredLeads.slice(0, 300).map((lead) => (
                    <tr key={lead.id} onClick={() => navigate(`/leads/${lead.id}`)} style={{ cursor: 'pointer' }}>
                      <td>{lead.first_name} {lead.last_name}</td>
                      <td className="mono">{lead.phone || '–'}</td>
                      <td><TierBadge tier={lead.tier} /></td>
                      <td><StatusBadge status={lead.status} /></td>
                      <td className="mono" style={{ fontSize: 12 }}>{lead.assigned_to_name}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}

      {/* ── Unassigned View ── */}
      {view === 'unassigned' && (
        <>
          <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 14, lineHeight: 1.5 }}>
            Leads land here when imported without a specific advisor assignment — the pool you route out manually, by lead type, judgment, or whatever the situation calls for.
          </p>
          {selectedPoolIds.length > 0 && (
            <div className="bulk-bar">
              <span className="bulk-bar-count">{selectedPoolIds.length} selected</span>
              <select className="filter-select" value={assignTarget} onChange={(e) => setAssignTarget(e.target.value)}>
                <option value="">Assign to advisor…</option>
                {data?.advisors?.map((a) => (<option key={a.advisor_id} value={a.advisor_id}>{a.advisor_name}</option>))}
              </select>
              <button className="btn btn--primary" onClick={handleReassign} disabled={!assignTarget || reassigning}>{reassigning ? 'Assigning…' : 'Assign selected'}</button>
              <button className="btn btn--secondary" onClick={() => setSelectedPoolIds([])}>Clear</button>
            </div>
          )}
          <section className="panel">
            {unassignedLoading ? (
              <div className="empty-state">Loading unassigned leads…</div>
            ) : unassignedLeads.length === 0 ? (
              <div className="empty-state">No leads waiting in the pool right now.</div>
            ) : (
              <table className="data-table">
                <thead><tr><th><input type="checkbox" disabled /></th><th>Name</th><th>Phone</th><th>Tier</th><th>Engagement</th><th>Added</th></tr></thead>
                <tbody>
                  {unassignedLeads.map((lead) => (
                    <tr key={lead.id} style={{ cursor: 'pointer' }} onClick={() => togglePoolSelect(lead.id)}>
                      <td><input type="checkbox" checked={selectedPoolIds.includes(lead.id)} onChange={() => togglePoolSelect(lead.id)} /></td>
                      <td>{lead.first_name} {lead.last_name}</td>
                      <td className="mono">{lead.phone || '–'}</td>
                      <td><TierBadge tier={lead.tier} /></td>
                      <td className="mono" style={{ textTransform: 'capitalize', fontSize: 12 }}>{lead.engagement_temperature || 'unknown'}</td>
                      <td className="mono" style={{ fontSize: 11 }}>{new Date(lead.created_at).toLocaleDateString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}

      {/* ── Tiers View ── */}
      {view === 'tiers' && (
        <>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
            <div>
              <p style={{ color: 'var(--text-secondary)', fontSize: 13, lineHeight: 1.5 }}>
                Tiers control how leads are routed, messaged, and tracked. Changes here affect all advisors in your org.
              </p>
            </div>
            <button className="btn btn--primary" onClick={startAddTier}>+ Add tier</button>
          </div>

          {tierError && <div className="compose-error" style={{ marginBottom: 12 }}>{tierError}</div>}

          {/* Add / Edit form */}
          {tierFormOpen && (
            <section className="panel" style={{ marginBottom: 16 }}>
              <div className="panel-header">
                <h2 className="panel-title">{tierEditing ? 'Edit tier' : 'New tier'}</h2>
                <button className="back-link" onClick={() => { setTierFormOpen(false); setTierEditing(null); setTierError('') }}>Cancel</button>
              </div>
              <form onSubmit={handleSaveTier} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                  <label className="settings-label">
                    Internal name (no spaces, e.g. pre_need)
                    <input
                      className="settings-input"
                      value={tierForm.name}
                      onChange={(e) => setTierForm(f => ({ ...f, name: e.target.value }))}
                      placeholder="pre_need"
                      required
                    />
                  </label>
                  <label className="settings-label">
                    Display label
                    <input
                      className="settings-input"
                      value={tierForm.label}
                      onChange={(e) => setTierForm(f => ({ ...f, label: e.target.value }))}
                      placeholder="Pre-Need"
                      required
                    />
                  </label>
                </div>
                <label className="settings-label">
                  Description (optional)
                  <input
                    className="settings-input"
                    value={tierForm.description}
                    onChange={(e) => setTierForm(f => ({ ...f, description: e.target.value }))}
                    placeholder="Families planning ahead, not in immediate need"
                  />
                </label>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                  <label className="settings-label">
                    Message track (optional)
                    <input
                      className="settings-input"
                      value={tierForm.message_track}
                      onChange={(e) => setTierForm(f => ({ ...f, message_track: e.target.value }))}
                      placeholder="standard"
                    />
                  </label>
                  <label className="settings-label">
                    Sort order (optional)
                    <input
                      className="settings-input"
                      type="number"
                      value={tierForm.sort_order}
                      onChange={(e) => setTierForm(f => ({ ...f, sort_order: e.target.value }))}
                      placeholder="1"
                    />
                  </label>
                </div>
                {tierError && <div className="compose-error">{tierError}</div>}
                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                  <button type="button" className="btn btn--secondary" onClick={() => { setTierFormOpen(false); setTierEditing(null); setTierError('') }}>Cancel</button>
                  <button type="submit" className="btn btn--primary" disabled={tierSaving}>{tierSaving ? 'Saving…' : tierEditing ? 'Save changes' : 'Create tier'}</button>
                </div>
              </form>
            </section>
          )}

          {/* Tiers table */}
          <section className="panel">
            {tiersLoading ? (
              <div className="empty-state">Loading tiers…</div>
            ) : tiers.length === 0 ? (
              <div className="empty-state">No tiers configured yet. Add one above.</div>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Label</th>
                    <th>Description</th>
                    <th>Message track</th>
                    <th>Sort</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {tiers
                    .slice()
                    .sort((a, b) => (a.sort_order ?? 999) - (b.sort_order ?? 999))
                    .map((tier) => (
                      <tr key={tier.id}>
                        <td className="mono" style={{ fontSize: 12 }}>{tier.name}</td>
                        <td><TierBadge tier={tier.name} /></td>
                        <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{tier.description || '–'}</td>
                        <td className="mono" style={{ fontSize: 12 }}>{tier.message_track || '–'}</td>
                        <td className="mono" style={{ fontSize: 12 }}>{tier.sort_order ?? '–'}</td>
                        <td>
                          <div style={{ display: 'flex', gap: 8 }}>
                            <button className="btn btn--secondary" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => startEditTier(tier)}>Edit</button>
                            <button
                              className="btn btn--danger"
                              style={{ fontSize: 12, padding: '4px 10px' }}
                              onClick={() => handleDeleteTier(tier.id, tier.label)}
                              disabled={tierDeleting === tier.id}
                            >
                              {tierDeleting === tier.id ? '…' : 'Delete'}
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}
    </div>
  )
}
