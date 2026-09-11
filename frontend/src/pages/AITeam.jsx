/**
 * YOUR AI TEAM — the customer's own screen.
 *
 * WHAT A CUSTOMER READS HERE: who is on the team, what each one does, what it
 * is doing right now, and whether it is switched on. What they never read is a
 * system prompt, a model name, a temperature, a tool schema or an agent graph
 * — the server sends human labels and this screen renders them (section 51).
 *
 * THE FIRST THING ON THE PAGE IS WHETHER ANYTHING IS RUNNING. "Why is nothing
 * happening" is the question this product will be asked most often, and the
 * honest answer — switched off, in simulation, watching, or working — belongs
 * at the top rather than three clicks in.
 *
 * Hiring is a two-step flow ON PURPOSE. An AI employee is created switched
 * off, and switching it on is a separate decision with its own confirmation.
 * A one-click "add employee" that immediately started messaging people would
 * be the wrong shape however carefully it was worded.
 *
 * Data:
 *   GET  /workforce/team
 *   GET  /workforce/catalogue
 *   POST /workforce/team
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import PageShell from '../components/PageShell'
import '../styles/shared.css'

const STAGE_TONE = {
  off: 'neutral',
  simulation: 'blue',
  shadow: 'purple',
  controlled: 'amber',
  active: 'green',
}

const STAGE_WORDS = {
  off: 'Switched off',
  simulation: 'Simulation',
  shadow: 'Watching',
  controlled: 'Controlled launch',
  active: 'Working',
}

function StageBadge ({ state, killed }) {
  if (killed) return <span className="badge badge--red">Stopped</span>
  const tone = STAGE_TONE[state] || 'neutral'
  return <span className={`badge badge--${tone}`}>{STAGE_WORDS[state] || state}</span>
}

function QueueChips ({ queue }) {
  const order = ['working', 'waiting', 'needs_review', 'qualified',
    'appointments', 'handoffs', 'closed', 'paused']
  const labels = {
    working: 'Working',
    waiting: 'Waiting',
    needs_review: 'Needs review',
    qualified: 'Qualified',
    appointments: 'Appointments',
    handoffs: 'Handoffs',
    closed: 'Closed',
    paused: 'Paused',
  }
  const shown = order.filter(k => (queue?.[k] || 0) > 0)
  if (!shown.length) return <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>No work assigned yet</span>
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
      {shown.map(k => (
        <span key={k} className="badge badge--neutral">
          {labels[k]} {queue[k]}
        </span>
      ))}
    </div>
  )
}

export default function AITeam () {
  const navigate = useNavigate()
  const [team, setTeam] = useState(null)
  const [catalogue, setCatalogue] = useState([])
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [hiring, setHiring] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const [a, b] = await Promise.all([
        api.get('/workforce/team'),
        api.get('/workforce/catalogue'),
      ])
      setTeam(a)
      setCatalogue(b.available || [])
    } catch (e) {
      setErr(e?.message || 'Could not load your AI team.')
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => { load() }, [load])

  const available = useMemo(
    () => catalogue.filter(c => c.available), [catalogue])
  const unavailable = useMemo(
    () => catalogue.filter(c => !c.available), [catalogue])

  async function hire (templateKey, name) {
    setBusy(true); setErr('')
    try {
      await api.post('/workforce/team', { template_key: templateKey, name })
      setHiring(null)
      await load()
    } catch (e) {
      setErr(e?.message || 'That employee could not be added.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <PageShell
      eyebrow="AI Workforce"
      title="Your AI Team"
      subtitle="People-shaped help that works your records: qualifying, following up, booking, and handing anything difficult to one of your team."
      action={<button className="btn btn--secondary btn--sm" onClick={load} disabled={loading}>Refresh</button>}
    >
      {err && (
        <div className="panel" style={{ borderColor: 'rgba(255,77,126,.35)' }}>
          <p style={{ margin: 0, color: 'var(--signal-red)', fontSize: 13 }}>{err}</p>
        </div>
      )}

      {/* ── THE HEADLINE. Is anything running, and if not, why not. ───── */}
      <div className="panel" style={{ marginBottom: 18 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <StageBadge state={team?.activation?.state} killed={team?.activation?.killed} />
          <p style={{ margin: 0, fontSize: 14 }}>{team?.status_line || (loading ? 'Loading…' : '')}</p>
        </div>
        {team?.activation?.killed && (
          <p style={{ margin: '10px 0 0', fontSize: 12, color: 'var(--text-secondary)' }}>
            An operator stopped the AI workforce. Nothing is being sent and no
            records are being worked. Contact support if this is unexpected.
          </p>
        )}
      </div>

      <div className="stat-grid" style={{ marginBottom: 18 }}>
        {(team?.queue || []).map(g => (
          <div className="stat-card" key={g.key}>
            <div className="stat-card__label">{g.label}</div>
            <div className="stat-card__value">{g.count}</div>
          </div>
        ))}
      </div>

      {/* ── THE TEAM ──────────────────────────────────────────────────── */}
      <div className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Employees</h2>
          <span className="panel-count">{team?.employees?.length || 0}</span>
        </div>

        {!loading && !(team?.employees || []).length && (
          <div className="empty-state">
            <p>You have not added any AI employees yet.</p>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              Every one starts switched off. Nothing is contacted until you and
              AdvisorFlow agree to switch it on.
            </p>
          </div>
        )}

        <div className="stagger-children">
          {(team?.employees || []).map(emp => (
            <div
              key={emp.id}
              className="glass-card"
              style={{ marginBottom: 10, cursor: 'pointer' }}
              onClick={() => navigate(`/ai-team/${emp.id}`)}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 15 }}>{emp.name}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>
                    {(emp.channels || []).join(' · ') || 'No channels enabled'}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  {emp.paused && <span className="badge badge--amber">Paused</span>}
                  <StageBadge state={emp.activation_state} />
                </div>
              </div>
              <div style={{ marginTop: 10 }}>
                <QueueChips queue={emp.queue} />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── HIRING ────────────────────────────────────────────────────── */}
      <div className="panel" style={{ marginTop: 18 }}>
        <div className="panel-header">
          <h2 className="panel-title">Add an AI employee</h2>
        </div>

        {!available.length && !unavailable.length && !loading && (
          <div className="empty-state">
            <p>No AI employees are offered on your plan yet.</p>
          </div>
        )}

        {available.map(item => (
          <div className="glass-card" key={item.template_key} style={{ marginBottom: 10 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <div style={{ maxWidth: 620 }}>
                <div style={{ fontWeight: 600 }}>{item.display_name}</div>
                <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--text-secondary)' }}>
                  {item.description}
                </p>
              </div>
              <button
                className="btn btn--primary btn--sm"
                disabled={busy}
                onClick={() => setHiring(item)}
              >
                Add
              </button>
            </div>
          </div>
        ))}

        {unavailable.length > 0 && (
          <>
            <div className="divider" />
            <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '0 0 10px' }}>
              Not available on your plan yet — talk to us if one of these would help.
            </p>
            {unavailable.map(item => (
              <div className="glass-card" key={item.template_key}
                   style={{ marginBottom: 8, opacity: 0.62 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                  <div style={{ maxWidth: 620 }}>
                    <div style={{ fontWeight: 600 }}>{item.display_name}</div>
                    <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--text-secondary)' }}>
                      {item.description}
                    </p>
                  </div>
                  <span className="badge badge--neutral" style={{ alignSelf: 'flex-start' }}>
                    {item.entitlement?.catalogue_present ? 'Not purchased' : 'Not available yet'}
                  </span>
                </div>
              </div>
            ))}
          </>
        )}
      </div>

      {/* ── CONFIRM. Adding is not switching on, and it says so. ──────── */}
      {hiring && (
        <div className="panel" style={{ marginTop: 18, borderColor: 'rgba(47,182,255,.35)' }}>
          <h3 style={{ margin: '0 0 8px' }}>Add {hiring.display_name}?</h3>
          <p style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
            It will be added <strong>switched off</strong>. Nobody is contacted
            and no records are worked until you set it up and AdvisorFlow
            switches it on with you.
          </p>
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            <button className="btn btn--primary btn--sm" disabled={busy}
                    onClick={() => hire(hiring.template_key, hiring.display_name)}>
              {busy ? 'Adding…' : 'Add, switched off'}
            </button>
            <button className="btn btn--secondary btn--sm" disabled={busy}
                    onClick={() => setHiring(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </PageShell>
  )
}
