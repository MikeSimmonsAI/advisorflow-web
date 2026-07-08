import { useEffect, useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import SignalPulse from '../components/SignalPulse'
import { TierBadge, StatusBadge } from '../components/StatusBadge'
import '../styles/shared.css'
import './Admin.css'

function pct(value) {
  if (value === null || value === undefined) return '0%'
  const n = Number(value)
  return `${n % 1 === 0 ? n : n.toFixed(1)}%`
}

function trend(value) {
  if (!value || value === 0) return null
  return value > 0 ? '↑' : '↓'
}

function formatSaleDate(value) {
  if (!value) return '—'
  try { return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(value)) }
  catch { return value }
}

function AdvisorScorecard({ advisor }) {
  const replyRate = Number(advisor.reply_rate || 0)
  const hotRate = Number(advisor.hot_reply_rate || 0)
  const bookingRate = Number(advisor.booking_rate || 0)
  const dncRate = Number(advisor.dnc_rate || 0)

  function barColor(val, max) {
    const ratio = max > 0 ? val / max : 0
    if (ratio > 0.6) return 'var(--signal-green)'
    if (ratio > 0.3) return 'var(--signal-amber)'
    return 'var(--signal-blue)'
  }

  return (
    <div className="advisor-scorecard panel">
      <div className="asc-header">
        <div className="asc-avatar">{(advisor.advisor_name || 'A').charAt(0).toUpperCase()}</div>
        <div className="asc-name-block">
          <span className="asc-name">{advisor.advisor_name}</span>
          <span className="asc-leads">{advisor.leads_owned} leads</span>
        </div>
        {advisor.hot_replies > 0 && (
          <span className="asc-hot-badge">🔥 {advisor.hot_replies} hot</span>
        )}
      </div>

      <div className="asc-kpi-row">
        <div className="asc-kpi">
          <span className="asc-kpi-label">Sent</span>
          <span className="asc-kpi-value">{advisor.messages_sent || 0}</span>
        </div>
        <div className="asc-kpi">
          <span className="asc-kpi-label">Replies</span>
          <span className="asc-kpi-value">{advisor.replies || 0}</span>
        </div>
        <div className="asc-kpi">
          <span className="asc-kpi-label">Booked</span>
          <span className="asc-kpi-value" style={{ color: 'var(--signal-green)' }}>{advisor.booked_leads || 0}</span>
        </div>
        <div className="asc-kpi">
          <span className="asc-kpi-label">DNC</span>
          <span className="asc-kpi-value" style={{ color: dncRate > 5 ? 'var(--signal-red)' : 'var(--text-secondary)' }}>{advisor.dnc_leads || 0}</span>
        </div>
      </div>

      <div className="asc-bars">
        <div className="asc-bar-row">
          <span className="asc-bar-label">Reply rate</span>
          <div className="asc-bar-track">
            <div className="asc-bar-fill" style={{ width: `${Math.min(replyRate, 100)}%`, background: barColor(replyRate, 100) }} />
          </div>
          <span className="asc-bar-pct">{pct(replyRate)}</span>
        </div>
        <div className="asc-bar-row">
          <span className="asc-bar-label">Hot rate</span>
          <div className="asc-bar-track">
            <div className="asc-bar-fill" style={{ width: `${Math.min(hotRate, 100)}%`, background: 'var(--signal-red)' }} />
          </div>
          <span className="asc-bar-pct" style={{ color: hotRate > 0 ? 'var(--signal-red)' : undefined }}>{pct(hotRate)}</span>
        </div>
        <div className="asc-bar-row">
          <span className="asc-bar-label">Booking rate</span>
          <div className="asc-bar-track">
            <div className="asc-bar-fill" style={{ width: `${Math.min(bookingRate, 100)}%`, background: 'var(--signal-green)' }} />
          </div>
          <span className="asc-bar-pct" style={{ color: bookingRate > 0 ? 'var(--signal-green)' : undefined }}>{pct(bookingRate)}</span>
        </div>
      </div>

      {advisor.duplicate_leads_prevented > 0 && (
        <div className="asc-dupes">
          <span className="asc-dupes-label">🛡 {advisor.duplicate_leads_prevented} dupes prevented</span>
        </div>
      )}
    </div>
  )
}

export default function Admin() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [metrics, setMetrics] = useState(null)
  const [funnel, setFunnel] = useState(null)
  const [revenue, setRevenue] = useState(null)
  const [revenueLoading, setRevenueLoading] = useState(false)
  const [allLeads, setAllLeads] = useState([])
  const [unassignedLeads, setUnassignedLeads] = useState([])
  const [loading, setLoading] = useState(true)
  const [metricsLoading, setMetricsLoading] = useState(false)
  const [leadsLoading, setLeadsLoading] = useState(true)
  const [unassignedLoading, setUnassignedLoading] = useState(true)
  const [view, setView] = useState('advisors')
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedPoolIds, setSelectedPoolIds] = useState([])
  const [assignTarget, setAssignTarget] = useState('')
  const [reassigning, setReassigning] = useState(false)

  useEffect(() => {
    api.get('/admin/dashboard').then(setData).finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (view === 'leads' && allLeads.length === 0) {
      setLeadsLoading(true)
      api.get('/admin/leads').then(setAllLeads).finally(() => setLeadsLoading(false))
    }
    if (view === 'unassigned') loadUnassigned()
    if (view === 'metrics' && (!metrics || !funnel)) loadMetrics()
    if (view === 'revenue' && !revenue) loadRevenue()
  }, [view])

  function loadUnassigned() {
    setUnassignedLoading(true)
    api.get('/admin/leads/unassigned').then(setUnassignedLeads).finally(() => setUnassignedLoading(false))
  }

  async function loadRevenue() {
    setRevenueLoading(true)
    try { const d = await api.get('/admin/dashboard/revenue'); setRevenue(d) }
    finally { setRevenueLoading(false) }
  }

  async function loadMetrics() {
    setMetricsLoading(true)
    try {
      const [m, f] = await Promise.all([
        api.get('/admin/dashboard/metrics'),
        api.get('/admin/dashboard/funnel'),
      ])
      setMetrics(m); setFunnel(f)
    } finally { setMetricsLoading(false) }
  }

  function togglePoolSelect(id) {
    setSelectedPoolIds((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id])
  }

  async function handleReassign() {
    if (!assignTarget || selectedPoolIds.length === 0) return
    setReassigning(true)
    try {
      const result = await api.post('/admin/leads/reassign', { lead_ids: selectedPoolIds, new_assigned_to_id: assignTarget })
      setSelectedPoolIds([]); setAssignTarget(''); loadUnassigned()
      alert(`Assigned ${result.reassigned_count} lead(s).`)
    } catch (err) { alert(`Failed: ${err.message}`) }
    finally { setReassigning(false) }
  }

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
    const counts = funnel?.stages?.map((s) => s.count || 0) || []
    return Math.max(...counts, 1)
  }, [funnel])

  const advisors = data?.advisors || []
  const topAdvisor = [...advisors].sort((a, b) => (b.hot_replies || 0) - (a.hot_replies || 0))[0]

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Master dashboard</h1>
          <p className="page-subtitle">Every advisor, one view — trends, gaps, and performance at a glance.</p>
        </div>
        <SignalPulse color="blue" label="Org-wide" />
      </header>

      <div className="admin-hero-kpi">
        <div className="panel admin-hero-card admin-hero-card--blue">
          <span className="admin-hero-label">Total leads</span>
          <strong className="admin-hero-value">{loading ? '—' : (data?.total_leads || 0).toLocaleString()}</strong>
          <span className="admin-hero-sub">Across all advisors</span>
        </div>
        <div className="panel admin-hero-card admin-hero-card--green">
          <span className="admin-hero-label">Duplicates prevented</span>
          <strong className="admin-hero-value">{loading ? '—' : (data?.total_duplicates_prevented || 0).toLocaleString()}</strong>
          <span className="admin-hero-sub">No double-contact across advisors</span>
        </div>
        <div className="panel admin-hero-card admin-hero-card--purple">
          <span className="admin-hero-label">Advisors active</span>
          <strong className="admin-hero-value">{loading ? '—' : advisors.length}</strong>
          <span className="admin-hero-sub">
            {topAdvisor ? `Top: ${topAdvisor.advisor_name}` : 'All advisors'}
          </span>
        </div>
      </div>

      <div className="admin-tabs">
        {[
          { key: 'advisors', label: 'By advisor' },
          { key: 'metrics', label: 'Metrics' },
          { key: 'leads', label: `All leads${allLeads.length ? ` (${allLeads.length})` : ''}` },
          { key: 'unassigned', label: `Unassigned pool${unassignedLeads.length ? ` (${unassignedLeads.length})` : ''}` },
          { key: 'revenue', label: 'Revenue' },
        ].map(({ key, label }) => (
          <button key={key} className={`tab ${view === key ? 'tab--active' : ''}`} onClick={() => setView(key)}>
            {label}
          </button>
        ))}
      </div>

      {view === 'advisors' && (
        <>
          {loading ? (
            <div className="empty-state">Loading advisors…</div>
          ) : advisors.length === 0 ? (
            <div className="empty-state">No advisor data yet.</div>
          ) : (
            <div className="advisor-scorecard-grid">
              {advisors.filter((a) => a.advisor_id !== 'org_total').map((advisor) => (
                <AdvisorScorecard key={advisor.advisor_id} advisor={advisor} />
              ))}
            </div>
          )}
        </>
      )}

      {view === 'metrics' && (
        <>
          {metricsLoading ? (
            <div className="empty-state">Loading metrics…</div>
          ) : (
            <>
              <div className="admin-metrics-kpi">
                {[
                  { label: 'Reply rate', value: pct(metrics?.totals?.reply_rate), sub: `${metrics?.totals?.replies || 0} replies / ${metrics?.totals?.messages_sent || 0} sent`, color: 'var(--signal-blue)' },
                  { label: 'Hot reply rate', value: pct(metrics?.totals?.hot_reply_rate), sub: `${metrics?.totals?.hot_replies || 0} hot / callback replies`, color: 'var(--signal-red)' },
                  { label: 'Booking rate', value: pct(metrics?.totals?.booking_rate), sub: `${metrics?.totals?.booked_leads || 0} booked / ${metrics?.totals?.leads_owned || 0} leads`, color: 'var(--signal-green)' },
                  { label: 'DNC rate', value: pct(metrics?.totals?.dnc_rate), sub: `${metrics?.totals?.dnc_leads || 0} DNC leads`, color: 'var(--signal-amber)' },
                ].map((kpi) => (
                  <div key={kpi.label} className="panel admin-metric-card">
                    <span className="admin-metric-label">{kpi.label}</span>
                    <strong className="admin-metric-value" style={{ color: kpi.color }}>{kpi.value}</strong>
                    <span className="admin-metric-sub">{kpi.sub}</span>
                  </div>
                ))}
              </div>

              <section className="panel">
                <div className="panel-header">
                  <h2 className="panel-title">Advisor quality breakdown</h2>
                  <span className="panel-count">{metrics?.advisors?.length || 0} advisors</span>
                </div>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Advisor</th><th>Leads</th><th>Sent</th><th>Replies</th>
                      <th>Reply %</th><th>Hot %</th><th>Booking %</th><th>DNC %</th><th>Dupes stopped</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(metrics?.advisors || []).filter((a) => a.advisor_id !== 'org_total').map((a) => (
                      <tr key={a.advisor_id}>
                        <td>{a.advisor_name}</td>
                        <td className="mono">{a.leads_owned}</td>
                        <td className="mono">{a.messages_sent}</td>
                        <td className="mono">{a.replies}</td>
                        <td className="mono">{pct(a.reply_rate)}</td>
                        <td className="mono" style={{ color: a.hot_replies > 0 ? 'var(--signal-red)' : undefined }}>{pct(a.hot_reply_rate)}</td>
                        <td className="mono" style={{ color: a.booked_leads > 0 ? 'var(--signal-green)' : undefined }}>{pct(a.booking_rate)}</td>
                        <td className="mono">{pct(a.dnc_rate)}</td>
                        <td className="mono">{a.duplicate_leads_prevented || 0}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>

              {funnel?.stages?.length > 0 && (
                <section className="panel">
                  <div className="panel-header">
                    <h2 className="panel-title">Org-wide funnel</h2>
                    <span className="panel-count">Lead → sale</span>
                  </div>
                  <div className="admin-funnel">
                    {funnel.stages.map((stage) => (
                      <div key={stage.key} className="admin-funnel-row">
                        <span className="admin-funnel-label">{stage.label}</span>
                        <div className="admin-funnel-track">
                          <div
                            className="admin-funnel-fill"
                            style={{ width: `${Math.max((stage.count / funnelMax) * 100, stage.count > 0 ? 4 : 0)}%` }}
                          />
                        </div>
                        <span className="admin-funnel-count mono">{stage.count}</span>
                      </div>
                    ))}
                  </div>
                </section>
              )}
            </>
          )}
        </>
      )}

      {view === 'leads' && (
        <section className="panel">
          <div className="panel-header">
            <input
              type="text"
              placeholder="Search by name or phone…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="search-input"
              style={{ width: 280 }}
            />
            <span className="panel-count">{filteredLeads.length} leads</span>
          </div>
          {leadsLoading ? (
            <div className="empty-state">Loading leads…</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr><th>Name</th><th>Phone</th><th>Tier</th><th>Status</th><th>Advisor</th></tr>
              </thead>
              <tbody>
                {filteredLeads.slice(0, 200).map((lead) => (
                  <tr key={lead.id} onClick={() => navigate(`/leads/${lead.id}`)} style={{ cursor: 'pointer' }}>
                    <td>{`${lead.first_name || ''} ${lead.last_name || ''}`.trim() || '—'}</td>
                    <td className="mono">{lead.phone || '—'}</td>
                    <td><TierBadge tier={lead.tier} /></td>
                    <td><StatusBadge status={lead.status} /></td>
                    <td>{lead.assigned_to_name || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {view === 'unassigned' && (
        <section className="panel">
          <div className="panel-header">
            <h2 className="panel-title">Unassigned pool</h2>
            <span className="panel-count">{unassignedLeads.length} leads</span>
          </div>
          {unassignedLoading ? (
            <div className="empty-state">Loading…</div>
          ) : unassignedLeads.length === 0 ? (
            <div className="empty-state">No unassigned leads.</div>
          ) : (
            <>
              <div className="admin-reassign-bar">
                <select
                  value={assignTarget}
                  onChange={(e) => setAssignTarget(e.target.value)}
                  className="search-input"
                  style={{ width: 220 }}
                >
                  <option value="">Assign selected to…</option>
                  {(data?.advisors || []).filter((a) => a.advisor_id !== 'org_total').map((a) => (
                    <option key={a.advisor_id} value={a.advisor_id}>{a.advisor_name}</option>
                  ))}
                </select>
                <button
                  className="btn btn--primary"
                  onClick={handleReassign}
                  disabled={reassigning || !assignTarget || selectedPoolIds.length === 0}
                >
                  {reassigning ? 'Assigning…' : `Assign ${selectedPoolIds.length || ''} selected`}
                </button>
              </div>
              <table className="data-table">
                <thead>
                  <tr>
                    <th style={{ width: 40 }}>
                      <input type="checkbox"
                        checked={selectedPoolIds.length === unassignedLeads.length && unassignedLeads.length > 0}
                        onChange={() => setSelectedPoolIds(selectedPoolIds.length === unassignedLeads.length ? [] : unassignedLeads.map((l) => l.id))}
                      />
                    </th>
                    <th>Name</th><th>Phone</th><th>Tier</th><th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {unassignedLeads.map((lead) => (
                    <tr key={lead.id}>
                      <td><input type="checkbox" checked={selectedPoolIds.includes(lead.id)} onChange={() => togglePoolSelect(lead.id)} /></td>
                      <td>{`${lead.first_name || ''} ${lead.last_name || ''}`.trim() || '—'}</td>
                      <td className="mono">{lead.phone || '—'}</td>
                      <td><TierBadge tier={lead.tier} /></td>
                      <td><StatusBadge status={lead.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </section>
      )}

      {view === 'revenue' && (
        <>
          {revenueLoading ? (
            <div className="empty-state">Loading revenue data…</div>
          ) : !revenue ? (
            <div className="empty-state">No revenue data yet. Record appointment outcomes to see data here.</div>
          ) : (
            <>
              <div className="admin-metrics-kpi">
                <div className="panel admin-metric-card">
                  <span className="admin-metric-label">Total sales</span>
                  <strong className="admin-metric-value" style={{ color: 'var(--signal-green)' }}>{revenue.total_sales || 0}</strong>
                  <span className="admin-metric-sub">All time</span>
                </div>
                <div className="panel admin-metric-card">
                  <span className="admin-metric-label">This month</span>
                  <strong className="admin-metric-value" style={{ color: 'var(--signal-blue)' }}>{revenue.this_month_sales || 0}</strong>
                  <span className="admin-metric-sub">Sales recorded</span>
                </div>
                <div className="panel admin-metric-card">
                  <span className="admin-metric-label">Top advisor</span>
                  <strong className="admin-metric-value" style={{ color: 'var(--signal-amber)', fontSize: 22 }}>
                    {revenue.by_advisor?.[0]?.advisor_name || '—'}
                  </strong>
                  <span className="admin-metric-sub">{revenue.by_advisor?.[0]?.sale_count || 0} sales</span>
                </div>
              </div>
              {revenue.recent_sales?.length > 0 && (
                <section className="panel">
                  <div className="panel-header">
                    <h2 className="panel-title">Recent sales</h2>
                  </div>
                  <table className="data-table">
                    <thead>
                      <tr><th>Date</th><th>Lead</th><th>Advisor</th><th>Product</th><th>Notes</th></tr>
                    </thead>
                    <tbody>
                      {revenue.recent_sales.slice(0, 50).map((s) => (
                        <tr key={s.id}>
                          <td className="mono">{formatSaleDate(s.sale_date)}</td>
                          <td>{s.lead_name || '—'}</td>
                          <td>{s.advisor_name || '—'}</td>
                          <td>{s.product_type || '—'}</td>
                          <td style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{s.sale_amount || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </section>
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}
