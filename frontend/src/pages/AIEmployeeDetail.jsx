/**
 * ONE AI EMPLOYEE — what it is, what it may do, what it is doing.
 *
 * SECTION 25: a customer should be able to answer "what is my AI employee
 * doing?" without asking support. So this page leads with the CURRENT WORK and
 * the RECENT ACTIVITY, in plain sentences, and puts configuration below them
 * rather than above.
 *
 * WHAT IT MAY DO IS SHOWN AS HUMAN LABELS. "Send an SMS", "Book an
 * appointment", "Hand this to a person" — the server sends `can_do` already
 * rendered, and the raw tool keys stay on the God surface where somebody
 * reviewing authority actually needs them.
 *
 * THE ACTIVATION CONTROL OFFERS ONLY WHAT A CUSTOMER MAY SET: off, simulation,
 * watching. Live operation is switched on by AdvisorFlow, with the customer,
 * on an agreed group of records (section 49) — so the control says so rather
 * than offering a button that 403s.
 *
 * Data:
 *   GET   /workforce/employees/{id}
 *   PATCH /workforce/employees/{id}
 *   POST  /workforce/employees/{id}/activation | /pause | /resume
 *   GET   /workforce/queue?employee_id=…
 *   GET   /workforce/handoffs?employee_id=…
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import PageShell from '../components/PageShell'
import '../styles/shared.css'

const CUSTOMER_STAGES = [
  { key: 'off', label: 'Switched off', hint: 'Does nothing at all.' },
  { key: 'simulation', label: 'Simulation', hint: 'Works the real process against test messages. Nobody is contacted.' },
  { key: 'shadow', label: 'Watching', hint: 'Records what it would have done. Nobody is contacted.' },
]

function Rate ({ value }) {
  if (value === null || value === undefined) {
    return <span style={{ color: 'var(--text-secondary)' }}>—</span>
  }
  return <span>{Math.round(value * 100)}%</span>
}

export default function AIEmployeeDetail () {
  const { employeeId } = useParams()
  const navigate = useNavigate()
  const [emp, setEmp] = useState(null)
  const [queue, setQueue] = useState(null)
  const [handoffs, setHandoffs] = useState([])
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const [a, b, c] = await Promise.all([
        api.get(`/workforce/employees/${employeeId}`),
        api.get('/workforce/queue', { params: { employee_id: employeeId, limit: 25 } }),
        api.get('/workforce/handoffs', { params: { employee_id: employeeId } }),
      ])
      setEmp(a); setQueue(b); setHandoffs(c.handoffs || [])
    } catch (e) {
      setErr(e?.message || 'Could not load this AI employee.')
    } finally {
      setLoading(false)
    }
  }, [employeeId])
  useEffect(() => { load() }, [load])

  async function act (path, body) {
    setBusy(true); setErr(''); setMsg('')
    try {
      await api.post(`/workforce/employees/${employeeId}${path}`, body || {})
      await load()
      setMsg('Saved.')
    } catch (e) {
      setErr(e?.message || 'That change could not be saved.')
    } finally {
      setBusy(false)
    }
  }

  const counts = emp?.performance?.counts || {}
  const rates = emp?.performance?.rates || {}

  return (
    <PageShell
      eyebrow="Your AI Team"
      title={emp?.name || 'AI employee'}
      subtitle={emp?.what_it_does || ''}
      action={
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn--secondary btn--sm" onClick={() => navigate('/ai-team')}>
            ← Team
          </button>
          <button className="btn btn--secondary btn--sm" onClick={load} disabled={loading}>
            Refresh
          </button>
        </div>
      }
    >
      {err && (
        <div className="panel" style={{ borderColor: 'rgba(255,77,126,.35)' }}>
          <p style={{ margin: 0, color: 'var(--signal-red)', fontSize: 13 }}>{err}</p>
        </div>
      )}
      {msg && (
        <div className="panel" style={{ borderColor: 'rgba(30,240,168,.35)' }}>
          <p style={{ margin: 0, color: 'var(--signal-green)', fontSize: 13 }}>{msg}</p>
        </div>
      )}

      {/* ── STATE AND CONTROLS ────────────────────────────────────────── */}
      <div className="panel">
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <span className={`badge badge--${emp?.activation?.killed ? 'red'
            : emp?.activation?.may_execute ? 'green' : 'neutral'}`}>
            {emp?.activation?.killed ? 'Stopped'
              : emp?.paused ? 'Paused'
                : (emp?.activation?.state || '—')}
          </span>
          <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
            {emp?.job_title}
          </span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
            {emp?.paused ? (
              <button className="btn btn--primary btn--sm" disabled={busy}
                      onClick={() => act('/resume')}>Resume</button>
            ) : (
              <button className="btn btn--danger btn--sm" disabled={busy}
                      onClick={() => act('/pause', { reason: 'paused from the team screen' })}>
                Pause now
              </button>
            )}
          </div>
        </div>

        {emp?.pause_reason && (
          <p style={{ margin: '10px 0 0', fontSize: 12, color: 'var(--text-secondary)' }}>
            Paused: {emp.pause_reason}
          </p>
        )}

        <div className="divider" />

        <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '0 0 8px' }}>
          How far this employee may go. Live operation is switched on by
          AdvisorFlow, with you, on an agreed group of records.
        </p>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {CUSTOMER_STAGES.map(stage => (
            <button
              key={stage.key}
              className={`pill-tab ${emp?.activation?.state === stage.key ? 'pill-tab--active' : ''}`}
              disabled={busy}
              title={stage.hint}
              onClick={() => act('/activation', { state: stage.key, reason: 'set from the team screen' })}
            >
              {stage.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── WHAT IT IS DOING ──────────────────────────────────────────── */}
      <div className="panel" style={{ marginTop: 18 }}>
        <div className="panel-header">
          <h2 className="panel-title">Current work</h2>
        </div>
        <div className="stat-grid">
          {(emp?.queue || []).map(g => (
            <div className="stat-card" key={g.key}>
              <div className="stat-card__label">{g.label}</div>
              <div className="stat-card__value">{g.count}</div>
            </div>
          ))}
        </div>

        {(queue?.items || []).length > 0 && (
          <div style={{ marginTop: 14 }}>
            {(queue.items || []).map(item => (
              <div key={item.id} className="glass-card" style={{ marginBottom: 8 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 14 }}>{item.contact}</div>
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                      {item.why || '—'}
                    </div>
                  </div>
                  <span className="badge badge--neutral">{item.state_label}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── HANDOFFS ──────────────────────────────────────────────────── */}
      <div className="panel" style={{ marginTop: 18 }}>
        <div className="panel-header">
          <h2 className="panel-title">Waiting for a person</h2>
          <span className="panel-count">{handoffs.length}</span>
        </div>
        {!handoffs.length && (
          <div className="empty-state"><p>Nothing is waiting on a person.</p></div>
        )}
        {handoffs.map(h => (
          <div key={h.id} className="glass-card" style={{ marginBottom: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
              <div style={{ maxWidth: 640 }}>
                <div style={{ fontWeight: 600, fontSize: 14 }}>{h.reason_label}</div>
                <p style={{ margin: '4px 0 0', fontSize: 13 }}>{h.summary}</p>
                {h.recommended_action && (
                  <p style={{ margin: '6px 0 0', fontSize: 12, color: 'var(--signal-blue)' }}>
                    Suggested: {h.recommended_action}
                  </p>
                )}
              </div>
              <span className={`badge badge--${h.priority === 'urgent' ? 'red'
                : h.priority === 'high' ? 'amber' : 'neutral'}`}>
                {h.priority}
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* ── PERFORMANCE ───────────────────────────────────────────────── */}
      <div className="panel" style={{ marginTop: 18 }}>
        <div className="panel-header">
          <h2 className="panel-title">Performance</h2>
          <span className="panel-count">last 30 days</span>
        </div>
        <div className="stat-grid">
          <div className="stat-card">
            <div className="stat-card__label">Messages sent</div>
            <div className="stat-card__value">{counts.messages_sent ?? 0}</div>
          </div>
          <div className="stat-card">
            <div className="stat-card__label">Appointments</div>
            <div className="stat-card__value">{counts.appointments ?? 0}</div>
          </div>
          <div className="stat-card">
            <div className="stat-card__label">Qualified</div>
            <div className="stat-card__value">{counts.qualified ?? 0}</div>
          </div>
          <div className="stat-card">
            <div className="stat-card__label">Handed to a person</div>
            <div className="stat-card__value">{counts.handoffs ?? 0}</div>
          </div>
          <div className="stat-card">
            <div className="stat-card__label">Opt-outs</div>
            <div className="stat-card__value">{counts.opt_outs ?? 0}</div>
          </div>
          <div className="stat-card">
            <div className="stat-card__label">Not contactable</div>
            <div className="stat-card__value">{counts.records_denied ?? 0}</div>
          </div>
          <div className="stat-card">
            <div className="stat-card__label">Response rate</div>
            <div className="stat-card__value"><Rate value={rates.response_rate} /></div>
          </div>
          <div className="stat-card">
            <div className="stat-card__label">Appointment rate</div>
            <div className="stat-card__value"><Rate value={rates.appointment_rate} /></div>
          </div>
        </div>
        <p style={{ margin: '12px 0 0', fontSize: 12, color: 'var(--text-secondary)' }}>
          {emp?.performance?.revenue_note}
        </p>
      </div>

      {/* ── RECENT ACTIVITY ───────────────────────────────────────────── */}
      <div className="panel" style={{ marginTop: 18 }}>
        <div className="panel-header">
          <h2 className="panel-title">Recent activity</h2>
        </div>
        {!(emp?.recent_activity || []).length && (
          <div className="empty-state"><p>Nothing yet.</p></div>
        )}
        {(emp?.recent_activity || []).map((row, i) => (
          <div key={i} style={{ display: 'flex', gap: 12, padding: '7px 0',
                                borderBottom: '1px solid var(--border-subtle)' }}>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)', minWidth: 150 }}>
              {row.at ? new Date(row.at).toLocaleString() : '—'}
            </span>
            <span style={{ fontSize: 13, flex: 1 }}>{row.what}</span>
            <span className={`badge badge--${row.outcome === 'Done' ? 'green'
              : row.outcome === 'Not allowed' ? 'amber' : 'red'}`}>
              {row.outcome}
            </span>
            {row.simulated && <span className="badge badge--blue">simulated</span>}
          </div>
        ))}
        {(emp?.recent_activity || []).some(r => r.why_not) && (
          <p style={{ margin: '12px 0 0', fontSize: 12, color: 'var(--text-secondary)' }}>
            “Not allowed” means your rules stopped it — an opt-out, a channel
            that is switched off, or working hours. Nothing was sent.
          </p>
        )}
      </div>

      {/* ── WHAT IT MAY DO, AND WHAT IT KNOWS ─────────────────────────── */}
      <div className="panel" style={{ marginTop: 18 }}>
        <div className="panel-header">
          <h2 className="panel-title">What it may do</h2>
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {(emp?.can_do || []).map(t => (
            <span key={t.key} className="badge badge--neutral">{t.label}</span>
          ))}
        </div>
        <div className="divider" />
        <div style={{ fontSize: 13 }}>
          <div><strong>Channels:</strong> {(emp?.channels || []).join(', ') || 'none'}</div>
          <div style={{ marginTop: 4 }}>
            <strong>Hours:</strong>{' '}
            {emp?.operating_hours?.start
              ? `${emp.operating_hours.start}–${emp.operating_hours.end} (${emp.timezone})`
              : 'not set — it cannot contact anyone until these are set'}
          </div>
          <div style={{ marginTop: 4 }}>
            <strong>Daily limit:</strong> {emp?.daily_work_cap ?? 'not set'}
          </div>
          <div style={{ marginTop: 4 }}>
            <strong>Knowledge:</strong>{' '}
            {(emp?.knowledge?.sources || []).map(s => `${s.kind} (${s.count})`).join(', ') || 'none bound'}
          </div>
        </div>
      </div>
    </PageShell>
  )
}
