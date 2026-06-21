import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { getCurrentUser } from '../api/client'
import StatCard from '../components/StatCard'
import { TierBadge } from '../components/StatusBadge'
import '../styles/shared.css'

export default function Cadence() {
  const navigate = useNavigate()
  const [summary, setSummary] = useState({})
  const [active, setActive] = useState([])
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [lastRunResult, setLastRunResult] = useState(null)
  const user = getCurrentUser()
  const isAdmin = user?.role === 'org_admin' || user?.role === 'super_admin'

  function load() {
    setLoading(true)
    Promise.all([
      api.get('/cadence/summary'),
      api.get('/cadence/active'),
    ]).then(([summaryData, activeData]) => {
      setSummary(summaryData)
      setActive(activeData)
      setLoading(false)
    })
  }

  useEffect(() => { load() }, [])

  async function handleRunDue() {
    setRunning(true)
    try {
      const result = await api.post('/cadence/run-due', {})
      setLastRunResult(result)
      load()
    } catch (err) {
      alert(`Run failed: ${err.message}`)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Cadence</h1>
          <p className="page-subtitle">The 9-touch re-engagement schedule — Day 1, 3, 7, 10, 14, 21, 30, 45, 60.</p>
        </div>
        {isAdmin && (
          <button className="btn btn--primary" onClick={handleRunDue} disabled={running}>
            {running ? 'Running…' : 'Run due touches now'}
          </button>
        )}
      </header>

      <div className="stat-grid">
        <StatCard label="Active" value={loading ? '—' : (summary.active || 0)} accent="blue" />
        <StatCard label="Completed" value={loading ? '—' : (summary.completed || 0)} accent="neutral" />
        <StatCard label="Stopped — replied" value={loading ? '—' : (summary.stopped_replied || 0)} accent="green" />
        <StatCard label="Stopped — booked" value={loading ? '—' : (summary.stopped_booked || 0)} accent="green" />
        <StatCard label="Stopped — DNC" value={loading ? '—' : (summary.stopped_dnc || 0)} accent="red" />
      </div>

      {lastRunResult && (
        <section className="panel" style={{ marginBottom: 16 }}>
          <div className="panel-header"><h2 className="panel-title">Last run</h2></div>
          <div className="run-result">
            <span>Evaluated <strong className="mono">{lastRunResult.evaluated}</strong></span>
            <span>Sent <strong className="mono">{lastRunResult.sent}</strong></span>
            <span>Completed <strong className="mono">{lastRunResult.completed}</strong></span>
            <span>Errors <strong className="mono">{lastRunResult.errors}</strong></span>
          </div>
        </section>
      )}

      <section className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-header">
          <h2 className="panel-title">Active cadences</h2>
          <span className="panel-count">{active.length}</span>
        </div>
        {loading ? (
          <div className="empty-state">Loading…</div>
        ) : active.length === 0 ? (
          <div className="empty-state">No leads are currently in an active cadence.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Lead</th>
                <th>Tier</th>
                <th>Touch progress</th>
                <th>Next touch due</th>
              </tr>
            </thead>
            <tbody>
              {active.map((a) => (
                <tr key={a.cadence_state_id} onClick={() => navigate(`/leads/${a.lead_id}`)} style={{ cursor: 'pointer' }}>
                  <td>{a.lead_name}</td>
                  <td>{a.tier && <TierBadge tier={a.tier} />}</td>
                  <td className="mono">{a.current_touch_number} / {a.total_touches}</td>
                  <td className="mono">{new Date(a.next_touch_due_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel">
        <div className="panel-header"><h2 className="panel-title">How the cadence works</h2></div>
        <p style={{ color: 'var(--text-secondary)', fontSize: 13, lineHeight: 1.6 }}>
          Every new lead that isn't DNC, a duplicate, email-only, or pending tier review enters
          the cadence automatically on import. Each touch picks tone and offer based on the
          lead's message track — Pre-Need gets the price-lock pitch, Contract Sold gets the
          upsell offer, and so on. The moment a lead replies, books, or opts out, the cadence
          stops for them — no more touches go out.
        </p>
      </section>
    </div>
  )
}
