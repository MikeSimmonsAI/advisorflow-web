import { useEffect, useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
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

export default function Admin() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [metrics, setMetrics] = useState(null)
  const [funnel, setFunnel] = useState(null)
  const [allLeads, setAllLeads] = useState([])
  const [unassignedLeads, setUnassignedLeads] = useState([])
  const [loading, setLoading] = useState(true)
  const [metricsLoading, setMetricsLoading] = useState(false)
  const [leadsLoading, setLeadsLoading] = useState(true)
  const [unassignedLoading, setUnassignedLoading] = useState(true)
  const [view, setView] = useState('advisors') // 'advisors' | 'leads' | 'unassigned' | 'metrics'
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
    if (view === 'unassigned') {
      loadUnassigned()
    }
    if (view === 'metrics' && (!metrics || !funnel)) {
      loadMetrics()
    }
  }, [view])

  function loadUnassigned() {
    setUnassignedLoading(true)
    api.get('/admin/leads/unassigned').then(setUnassignedLeads).finally(() => setUnassignedLoading(false))
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

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Master dashboard</h1>
          <p className="page-subtitle">Every advisor, one view.</p>
        </div>
        <SignalPulse color="blue" label="Org-wide" />
      </header>

      <div className="stat-grid">
        <StatCard label="Total leads" value={loading ? '—' : data?.total_leads} accent="blue" />
        <StatCard
          label="Duplicates prevented"
          value={loading ? '—' : data?.total_duplicates_prevented}
          accent="green"
          sublabel="No double-contact across advisors"
        />
        <StatCard label="Advisors active" value={loading ? '—' : data?.advisors?.length} accent="neutral" />
      </div>

      <div className="admin-tabs">
        <button className={`tab ${view === 'advisors' ? 'tab--active' : ''}`} onClick={() => setView('advisors')}>
          By advisor
        </button>
        <button className={`tab ${view === 'leads' ? 'tab--active' : ''}`} onClick={() => setView('leads')}>
          All leads <span className="mono">{allLeads.length || ''}</span>
        </button>
        <button className={`tab ${view === 'unassigned' ? 'tab--active' : ''}`} onClick={() => setView('unassigned')}>
          Unassigned pool <span className="mono">{unassignedLeads.length || ''}</span>
        </button>
        <button className={`tab ${view === 'metrics' ? 'tab--active' : ''}`} onClick={() => setView('metrics')}>
          Metrics
        </button>
      </div>

      {view === 'advisors' && (
        <section className="panel">
          {loading ? (
            <div className="empty-state">Loading…</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Advisor</th>
                  <th>Leads owned</th>
                  <th>Messages sent</th>
                  <th>Hot replies</th>
                </tr>
              </thead>
              <tbody>
                {data?.advisors?.map((a) => (
                  <tr key={a.advisor_id}>
                    <td>{a.advisor_name}</td>
                    <td className="mono">{a.leads_owned}</td>
                    <td className="mono">{a.messages_sent}</td>
                    <td className="mono" style={{ color: a.hot_replies > 0 ? 'var(--signal-red)' : undefined }}>
                      {a.hot_replies}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {view === 'metrics' && (
        <>
          {metricsLoading ? (
            <section className="panel"><div className="empty-state">Loading quality metrics…</div></section>
          ) : (
            <>
              <div className="metrics-summary-grid">
                <div className="metric-card">
                  <span>Reply rate</span>
                  <strong>{formatPercent(metrics?.totals?.reply_rate)}</strong>
                  <small>{metrics?.totals?.replies || 0} replies / {metrics?.totals?.messages_sent || 0} sent</small>
                </div>
                <div className="metric-card metric-card--hot">
                  <span>Hot-reply rate</span>
                  <strong>{formatPercent(metrics?.totals?.hot_reply_rate)}</strong>
                  <small>{metrics?.totals?.hot_replies || 0} interested/callback replies</small>
                </div>
                <div className="metric-card metric-card--booked">
                  <span>Booking rate</span>
                  <strong>{formatPercent(metrics?.totals?.booking_rate)}</strong>
                  <small>{metrics?.totals?.booked_leads || 0} booked / {metrics?.totals?.leads_owned || 0} leads</small>
                </div>
                <div className="metric-card metric-card--dnc">
                  <span>DNC rate</span>
                  <strong>{formatPercent(metrics?.totals?.dnc_rate)}</strong>
                  <small>{metrics?.totals?.dnc_leads || 0} DNC leads</small>
                </div>
              </div>

              <section className="panel metrics-panel">
                <div className="panel-header">
                  <h2 className="panel-title">Advisor quality breakdown</h2>
                  <span className="panel-count">{metrics?.advisors?.length || 0} advisors</span>
                </div>
                <table className="data-table metrics-table">
                  <thead>
                    <tr>
                      <th>Advisor</th>
                      <th>Leads</th>
                      <th>Sent</th>
                      <th>Replies</th>
                      <th>Reply rate</th>
                      <th>Hot rate</th>
                      <th>Booking rate</th>
                      <th>DNC rate</th>
                      <th>Dupes stopped</th>
                    </tr>
                  </thead>
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
                    <div className="funnel-row" key={stage.key}>
                      <div className="funnel-row-label">
                        <span>{stage.label}</span>
                        <strong className="mono">{stage.count}</strong>
                      </div>
                      <div className="funnel-track">
                        <div
                          className="funnel-fill"
                          style={{ width: `${Math.max((stage.count / funnelMax) * 100, stage.count > 0 ? 6 : 0)}%` }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            </>
          )}
        </>
      )}

      {view === 'leads' && (
        <>
          <div className="filter-bar">
            <input
              type="text"
              placeholder="Search by name or phone, across all advisors…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="search-input"
            />
            <span className="filter-count mono">{filteredLeads.length} shown</span>
          </div>
          <section className="panel">
            {leadsLoading ? (
              <div className="empty-state">Loading every lead across the organization…</div>
            ) : filteredLeads.length === 0 ? (
              <div className="empty-state">No leads match your search.</div>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Phone</th>
                    <th>Tier</th>
                    <th>Status</th>
                    <th>Assigned to</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredLeads.slice(0, 300).map((lead) => (
                    <tr key={lead.id} onClick={() => navigate(`/leads/${lead.id}`)} style={{ cursor: 'pointer' }}>
                      <td>{lead.first_name} {lead.last_name}</td>
                      <td className="mono">{lead.phone || '—'}</td>
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

      {view === 'unassigned' && (
        <>
          <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 14, lineHeight: 1.5 }}>
            Leads land here when imported without a specific advisor assignment — the pool you route
            out manually, by lead type, judgment, or whatever the situation calls for.
          </p>

          {selectedPoolIds.length > 0 && (
            <div className="bulk-bar">
              <span className="bulk-bar-count">{selectedPoolIds.length} selected</span>
              <select
                className="filter-select"
                value={assignTarget}
                onChange={(e) => setAssignTarget(e.target.value)}
              >
                <option value="">Assign to advisor…</option>
                {data?.advisors?.map((a) => (
                  <option key={a.advisor_id} value={a.advisor_id}>{a.advisor_name}</option>
                ))}
              </select>
              <button className="btn btn--primary" onClick={handleReassign} disabled={!assignTarget || reassigning}>
                {reassigning ? 'Assigning…' : 'Assign selected'}
              </button>
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
                <thead>
                  <tr>
                    <th><input type="checkbox" disabled /></th>
                    <th>Name</th>
                    <th>Phone</th>
                    <th>Tier</th>
                    <th>Engagement</th>
                    <th>Added</th>
                  </tr>
                </thead>
                <tbody>
                  {unassignedLeads.map((lead) => (
                    <tr key={lead.id} style={{ cursor: 'pointer' }} onClick={() => togglePoolSelect(lead.id)}>
                      <td><input type="checkbox" checked={selectedPoolIds.includes(lead.id)} onChange={() => togglePoolSelect(lead.id)} /></td>
                      <td>{lead.first_name} {lead.last_name}</td>
                      <td className="mono">{lead.phone || '—'}</td>
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
    </div>
  )
}
