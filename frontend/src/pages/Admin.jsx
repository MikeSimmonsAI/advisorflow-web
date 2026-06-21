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
  const [loading, setLoading] = useState(true)
  const [leadsLoading, setLeadsLoading] = useState(true)
  const [view, setView] = useState('advisors') // 'advisors' | 'leads'
  const [searchQuery, setSearchQuery] = useState('')

  useEffect(() => {
    api.get('/admin/dashboard').then(setData).finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (view === 'leads' && allLeads.length === 0) {
      setLeadsLoading(true)
      api.get('/admin/leads').then(setAllLeads).finally(() => setLeadsLoading(false))
    }
  }, [view])

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
      </div>

      {view === 'advisors' ? (
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
      ) : (
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
    </div>
  )
}
