import { useEffect, useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import StatCard from '../components/StatCard'
import SignalPulse from '../components/SignalPulse'
import { TierBadge, StatusBadge } from '../components/StatusBadge'
import '../styles/shared.css'
import './Admin.css'

export default function Admin() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [allLeads, setAllLeads] = useState([])
  const [unassignedLeads, setUnassignedLeads] = useState([])
  const [loading, setLoading] = useState(true)
  const [leadsLoading, setLeadsLoading] = useState(true)
  const [unassignedLoading, setUnassignedLoading] = useState(true)
  const [view, setView] = useState('advisors') // 'advisors' | 'leads' | 'unassigned'
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
  }, [view])

  function loadUnassigned() {
    setUnassignedLoading(true)
    api.get('/admin/leads/unassigned').then(setUnassignedLeads).finally(() => setUnassignedLoading(false))
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
