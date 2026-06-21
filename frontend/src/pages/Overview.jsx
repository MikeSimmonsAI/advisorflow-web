import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
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
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      api.get('/leads/').catch(() => []),
      api.get('/sms/replies?hot_only=true').catch(() => []),
      api.get('/cadence/summary').catch(() => ({})),
    ]).then(([leadsData, repliesData, cadenceData]) => {
      setLeads(leadsData)
      setReplies(repliesData)
      setCadenceSummary(cadenceData)
      setLoading(false)
    })
  }, [])

  const newCount = leads.filter((l) => l.status === 'new').length
  const sentCount = leads.filter((l) => l.status === 'sent').length
  const hotCount = leads.filter((l) => l.status === 'hot').length
  const bookedCount = leads.filter((l) => l.status === 'booked').length
  const needsReviewCount = leads.filter((l) => l.status === 'needs_tier_review').length

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Overview</h1>
          <p className="page-subtitle">Welcome back, {user?.full_name?.split(' ')[0] || 'advisor'}.</p>
        </div>
        <SignalPulse color="green" label="Live" />
      </header>

      <div className="stat-grid">
        <button className="stat-card-link" onClick={() => navigate('/leads')}>
          <StatCard label="New leads" value={loading ? '—' : newCount} accent="blue" sublabel="Ready to contact" />
        </button>
        <button className="stat-card-link" onClick={() => navigate('/leads')}>
          <StatCard label="Sent" value={loading ? '—' : sentCount} accent="neutral" sublabel="Awaiting reply" />
        </button>
        <button className="stat-card-link" onClick={() => navigate('/replies?hot_only=true')}>
          <StatCard label="Hot replies" value={loading ? '—' : hotCount} accent="red" sublabel="Needs your attention" />
        </button>
        <button className="stat-card-link" onClick={() => navigate('/leads')}>
          <StatCard label="Booked" value={loading ? '—' : bookedCount} accent="green" sublabel="Appointments set" />
        </button>
        <button className="stat-card-link" onClick={() => navigate('/leads')}>
          <StatCard label="Needs tier review" value={loading ? '—' : needsReviewCount} accent="amber" sublabel="Untyped leads" />
        </button>
      </div>

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
