import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, getCurrentUser } from '../api/client'
import { TierBadge } from '../components/StatusBadge'
import '../styles/shared.css'
import './Cadence.css'

const TOUCH_DAYS = [1, 3, 7, 10, 14, 21, 30, 45, 60]

function formatDate(value) {
  if (!value) return '—'
  try { return new Date(value).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) }
  catch { return value }
}

function isOverdue(dateStr) {
  if (!dateStr) return false
  return new Date(dateStr) < new Date()
}

export default function Cadence() {
  const navigate = useNavigate()
  const [summary, setSummary] = useState({})
  const [active, setActive] = useState([])
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [lastRunResult, setLastRunResult] = useState(null)
  const [controlling, setControlling] = useState(null)
  const user = getCurrentUser()
  const isAdmin = user?.role === 'org_admin' || user?.role === 'super_admin'
  const [templates, setTemplates] = useState([])
  const [selectedTemplate, setSelectedTemplate] = useState('')

  function load() {
    setLoading(true)
    Promise.all([
      api.get('/cadence/summary').catch(() => ({})),
      api.get('/cadence/active').catch(() => []),
      api.get('/cadence-templates/').catch(() => []),
    ]).then(([summaryData, activeData, templateData]) => {
      setSummary(summaryData || {})
      setActive(activeData || [])
      setTemplates(templateData || [])
      if (templateData?.length > 0 && !selectedTemplate) {
        setSelectedTemplate(templateData[0].id)
      }
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

  async function handleControl(cadenceStateId, action) {
    setControlling(cadenceStateId)
    try {
      await api.post(`/cadence/${cadenceStateId}/control`, { action })
      load()
    } catch (err) {
      alert(`${action} failed: ${err.message}`)
    } finally {
      setControlling(null)
    }
  }

  const activeCount = active.filter((a) => a.status === 'active').length
  const pausedCount = active.filter((a) => a.status === 'paused').length
  const overdueCount = active.filter((a) => a.status === 'active' && isOverdue(a.next_touch_due_at)).length

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Cadence</h1>
          <p className="page-subtitle">Automated re-engagement sequences — select a template or build your own rhythm.</p>
        </div>
        {isAdmin && (
          <button className="btn btn--primary" onClick={handleRunDue} disabled={running}>
            {running ? 'Running…' : 'Run due touches now'}
          </button>
        )}
      </header>

      {/* Template selector */}
      {templates.length > 0 && (
        <div className="panel" style={{ marginBottom: 16, padding: '16px 20px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>📋 Cadence template:</span>
            <select
              className="filter-select"
              style={{ minWidth: 260 }}
              value={selectedTemplate}
              onChange={e => setSelectedTemplate(e.target.value)}
            >
              {templates.map(t => (
                <option key={t.id} value={t.id}>
                  {t.name} — {t.touch_count} touches{t.is_default ? ' ★' : ''}
                </option>
              ))}
            </select>
            {selectedTemplate && templates.find(t => t.id === selectedTemplate) && (
              <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                {templates.find(t => t.id === selectedTemplate)?.description}
              </span>
            )}
            <a href="/cadence-templates" style={{ fontSize: 12, color: 'var(--accent)', marginLeft: 'auto' }}>
              Manage templates →
            </a>
          </div>
          {selectedTemplate && templates.find(t => t.id === selectedTemplate) && (
            <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {templates.find(t => t.id === selectedTemplate)?.touches?.map(touch => (
                <span
                  key={touch.touch_number}
                  style={{
                    fontSize: 11,
                    padding: '3px 10px',
                    borderRadius: 20,
                    background: touch.channel === 'email' ? 'rgba(47,182,255,0.12)' : 'rgba(30,240,168,0.12)',
                    color: touch.channel === 'email' ? 'var(--signal-blue)' : 'var(--signal-green)',
                    border: '1px solid currentColor',
                  }}
                >
                  Day {touch.day_offset} — {touch.channel}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
      {templates.length === 0 && !loading && (
        <div className="panel" style={{ marginBottom: 16, padding: '16px 20px', display: 'flex', alignItems: 'center', gap: 16 }}>
          <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>No cadence templates yet.</span>
          <a href="/cadence-templates" className="btn btn--secondary" style={{ fontSize: 13 }}>
            ⚡ Set up templates
          </a>
        </div>
      )}

      <div className="cadence-kpi-row">
        <div className="panel cadence-kpi-card">
          <span className="cadence-kpi-label">Active</span>
          <strong className="cadence-kpi-value" style={{ color: 'var(--signal-blue)' }}>{loading ? '—' : activeCount}</strong>
          <span className="cadence-kpi-sub">Leads in sequence</span>
        </div>
        <div className="panel cadence-kpi-card">
          <span className="cadence-kpi-label">Paused</span>
          <strong className="cadence-kpi-value" style={{ color: 'var(--signal-amber)' }}>{loading ? '—' : pausedCount}</strong>
          <span className="cadence-kpi-sub">Manually held</span>
        </div>
        <div className="panel cadence-kpi-card">
          <span className="cadence-kpi-label">Overdue touches</span>
          <strong className="cadence-kpi-value" style={{ color: overdueCount > 0 ? 'var(--signal-red)' : 'var(--signal-green)' }}>{loading ? '—' : overdueCount}</strong>
          <span className="cadence-kpi-sub">Past due date</span>
        </div>
        <div className="panel cadence-kpi-card">
          <span className="cadence-kpi-label">Completed</span>
          <strong className="cadence-kpi-value" style={{ color: 'var(--signal-green)' }}>{loading ? '—' : (summary.completed || 0)}</strong>
          <span className="cadence-kpi-sub">All 9 touches sent</span>
        </div>
        <div className="panel cadence-kpi-card">
          <span className="cadence-kpi-label">Stopped — replied</span>
          <strong className="cadence-kpi-value" style={{ color: 'var(--signal-green)' }}>{loading ? '—' : (summary.stopped_replied || 0)}</strong>
          <span className="cadence-kpi-sub">Replied, cadence ended</span>
        </div>
        <div className="panel cadence-kpi-card">
          <span className="cadence-kpi-label">DNC stops</span>
          <strong className="cadence-kpi-value" style={{ color: 'var(--signal-red)' }}>{loading ? '—' : (summary.stopped_dnc || 0)}</strong>
          <span className="cadence-kpi-sub">Opted out</span>
        </div>
      </div>

      {lastRunResult && (
        <section className="panel cadence-run-result">
          <div className="panel-header"><h2 className="panel-title">Last run result</h2></div>
          <div className="cadence-run-grid">
            {[
              { label: 'Evaluated', value: lastRunResult.evaluated, color: 'var(--text-primary)' },
              { label: 'Sent', value: lastRunResult.sent, color: 'var(--signal-green)' },
              { label: 'Completed', value: lastRunResult.completed, color: 'var(--signal-blue)' },
              { label: 'Errors', value: lastRunResult.errors, color: lastRunResult.errors > 0 ? 'var(--signal-red)' : 'var(--text-secondary)' },
            ].map((item) => (
              <div key={item.label} className="cadence-run-stat">
                <span className="cadence-run-label">{item.label}</span>
                <strong className="cadence-run-value" style={{ color: item.color }}>{item.value}</strong>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-header">
          <h2 className="panel-title">Active cadences</h2>
          <span className="panel-count">{active.length}</span>
        </div>
        {loading ? (
          <div className="empty-state">Loading cadences…</div>
        ) : active.length === 0 ? (
          <div className="empty-state">No leads are currently in an active or paused cadence.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Lead</th>
                <th>Tier</th>
                <th>Status</th>
                <th>Progress</th>
                <th>Next touch</th>
                <th>Controls</th>
              </tr>
            </thead>
            <tbody>
              {active.map((a) => {
                const overdue = a.status === 'active' && isOverdue(a.next_touch_due_at)
                const busy = controlling === a.cadence_state_id
                return (
                  <tr key={a.cadence_state_id}>
                    <td>
                      <span className="cadence-lead-link" onClick={() => navigate(`/leads/${a.lead_id}`)}>
                        {a.lead_name || '—'}
                      </span>
                      {a.phone && <span className="cadence-phone mono">{a.phone}</span>}
                    </td>
                    <td>{a.tier && <TierBadge tier={a.tier} />}</td>
                    <td>
                      <span className={`cadence-status-pill cadence-status-pill--${a.status}`}>
                        {a.status}
                      </span>
                    </td>
                    <td>
                      <div className="cadence-progress">
                        <div className="cadence-touch-dots">
                          {TOUCH_DAYS.map((day, i) => (
                            <span
                              key={day}
                              className={`cadence-dot ${i < a.current_touch_number ? 'cadence-dot--done' : i === a.current_touch_number ? 'cadence-dot--next' : 'cadence-dot--pending'}`}
                              title={`Day ${day}`}
                            />
                          ))}
                        </div>
                        <span className="cadence-progress-label mono">{a.current_touch_number} / {a.total_touches}</span>
                      </div>
                    </td>
                    <td className="mono" style={{ color: overdue ? 'var(--signal-red)' : undefined }}>
                      {overdue && '⚠ '}{formatDate(a.next_touch_due_at)}
                    </td>
                    <td>
                      <div className="cadence-controls">
                        {a.status === 'active' && (
                          <button
                            className="btn btn--secondary cadence-ctrl-btn"
                            onClick={() => handleControl(a.cadence_state_id, 'pause')}
                            disabled={busy}
                            title="Pause this cadence"
                          >
                            ⏸ Pause
                          </button>
                        )}
                        {a.status === 'paused' && (
                          <button
                            className="btn btn--secondary cadence-ctrl-btn"
                            onClick={() => handleControl(a.cadence_state_id, 'resume')}
                            disabled={busy}
                            title="Resume this cadence"
                          >
                            ▶ Resume
                          </button>
                        )}
                        <button
                          className="btn btn--danger cadence-ctrl-btn"
                          onClick={() => {
                            if (window.confirm(`Cancel cadence for ${a.lead_name}? This cannot be undone.`)) {
                              handleControl(a.cadence_state_id, 'cancel')
                            }
                          }}
                          disabled={busy}
                          title="Cancel this cadence permanently"
                        >
                          ✕ Cancel
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel cadence-schedule-panel">
        <div className="panel-header"><h2 className="panel-title">Sequence schedule</h2></div>
        <div className="cadence-schedule-grid">
          {TOUCH_DAYS.map((day, i) => (
            <div key={day} className="cadence-schedule-card">
              <span className="cadence-schedule-touch">Touch {i + 1}</span>
              <strong className="cadence-schedule-day">Day {day}</strong>
              <span className="cadence-schedule-desc">
                {i === 0 && 'First contact'}
                {i === 1 && 'Follow-up'}
                {i === 2 && '1-week check-in'}
                {i === 3 && '10-day touch'}
                {i === 4 && '2-week push'}
                {i === 5 && '3-week outreach'}
                {i === 6 && '30-day re-engage'}
                {i === 7 && '45-day long-term'}
                {i === 8 && '60-day final'}
              </span>
            </div>
          ))}
        </div>
        <p className="cadence-schedule-note">
          Cadence stops automatically when a lead replies, books, or opts out. Pause or cancel individual sequences using the controls above.
        </p>
      </section>
    </div>
  )
}
