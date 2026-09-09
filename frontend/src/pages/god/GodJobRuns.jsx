/**
 * GodJobRuns — background job run history.
 *
 * Surfaces GET /god/job-runs and GET /god/job-runs/latest.
 * GOD-10 built the endpoints; GOD-07 wires them to a screen.
 *
 * Top section: pulse card per job (latest run + status).
 * Bottom section: filterable run history table.
 */
import { useState, useEffect } from 'react'
import { api } from '../../api/client'

const JOB_LABELS = {
  cadence_loop:          'Cadence Loop',
  ai_conversation_loop:  'AI Conversation',
  review_request_loop:   'Review Requests',
}

const STATUS_COLORS = {
  success:   { bg: '#f0fdf4', text: '#166534', border: '#86efac' },
  error:     { bg: '#fef2f2', text: '#991b1b', border: '#fca5a5' },
  running:   { bg: '#eff6ff', text: '#1d4ed8', border: '#93c5fd' },
  never_run: { bg: '#f9fafb', text: '#6b7280', border: '#e5e7eb' },
}

function PulseCard({ jobName, info }) {
  const s = info?.status || 'never_run'
  const colors = STATUS_COLORS[s] || STATUS_COLORS.never_run
  const label = JOB_LABELS[jobName] || jobName

  return (
    <div style={{
      border: `1px solid ${colors.border}`,
      borderRadius: 10, padding: '16px 20px',
      background: colors.bg, flex: '1 1 200px', minWidth: 180,
    }}>
      <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
                    letterSpacing: '.07em', color: colors.text, marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ fontSize: 20, fontWeight: 700, color: colors.text, marginBottom: 4 }}>
        {s}
      </div>
      {info?.started_at && (
        <div style={{ fontSize: 11, color: colors.text, opacity: .8 }}>
          {new Date(info.started_at).toLocaleString()}
        </div>
      )}
      {info?.duration_ms != null && (
        <div style={{ fontSize: 11, color: colors.text, opacity: .8 }}>
          {info.duration_ms} ms
        </div>
      )}
      {info?.error_summary && (
        <div style={{ marginTop: 8, fontSize: 11, background: 'rgba(0,0,0,.05)',
                      borderRadius: 4, padding: '4px 6px', fontFamily: 'monospace',
                      color: colors.text, wordBreak: 'break-word' }}>
          {info.error_summary}
        </div>
      )}
    </div>
  )
}

function HistoryRow({ run }) {
  const s = run.status || 'unknown'
  const colors = STATUS_COLORS[s] || STATUS_COLORS.never_run
  const label = JOB_LABELS[run.job_name] || run.job_name

  return (
    <tr>
      <td style={{ padding: '9px 12px', fontSize: 12, color: '#6b7280' }}>
        {run.id}
      </td>
      <td style={{ padding: '9px 12px', fontSize: 13, fontWeight: 500 }}>
        {label}
      </td>
      <td style={{ padding: '9px 12px' }}>
        <span style={{ fontSize: 12, fontWeight: 600, padding: '2px 8px', borderRadius: 100,
                       background: colors.bg, color: colors.text, border: `1px solid ${colors.border}` }}>
          {s}
        </span>
      </td>
      <td style={{ padding: '9px 12px', fontSize: 12, color: '#6b7280', whiteSpace: 'nowrap' }}>
        {run.started_at ? new Date(run.started_at).toLocaleString() : '—'}
      </td>
      <td style={{ padding: '9px 12px', fontSize: 12, color: '#6b7280', textAlign: 'right' }}>
        {run.duration_ms != null ? `${run.duration_ms} ms` : '—'}
      </td>
      <td style={{ padding: '9px 12px', fontSize: 11, color: '#9ca3af', fontFamily: 'monospace',
                   maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {run.error_summary || (run.metrics ? JSON.stringify(run.metrics) : '—')}
      </td>
    </tr>
  )
}

export default function GodJobRuns() {
  const [pulse, setPulse] = useState(null)
  const [runs, setRuns] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [refreshed, setRefreshed] = useState(null)
  const [jobFilter, setJobFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  const load = async () => {
    setLoading(true); setError(null)
    try {
      const [pulseRes, runsRes] = await Promise.all([
        api.get('/god/job-runs/latest'),
        api.get('/god/job-runs', {
          params: {
            job_name: jobFilter || undefined,
            status: statusFilter || undefined,
            limit: 100,
          },
        }),
      ])
      setPulse(pulseRes.data)
      setRuns(runsRes.data.runs || [])
      setTotal(runsRes.data.total || 0)
      setRefreshed(new Date())
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load job runs')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [jobFilter, statusFilter])

  const pageStyle = {
    padding: '24px 32px',
    maxWidth: 960,
    fontFamily: 'var(--god-font, system-ui, sans-serif)',
    color: 'var(--god-text, #1f2937)',
  }

  const cardStyle = {
    background: 'var(--god-card, #ffffff)',
    border: '1px solid var(--god-border, #e5e7eb)',
    borderRadius: 10,
    padding: '20px 24px',
    marginBottom: 20,
  }

  const KNOWN_JOBS = ['cadence_loop', 'ai_conversation_loop', 'review_request_loop']

  return (
    <div style={pageStyle}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
                    marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>Background Jobs</h1>
          <p style={{ margin: '6px 0 0', color: 'var(--god-muted, #6b7280)', fontSize: 14 }}>
            Run history for cadence, AI conversation, and review request loops
          </p>
        </div>
        <button onClick={load} disabled={loading} style={{
          fontSize: 12, padding: '6px 14px', borderRadius: 6,
          border: '1px solid var(--god-border, #e5e7eb)',
          background: 'var(--god-card, #fff)', cursor: 'pointer',
          color: 'var(--god-text, #1f2937)',
        }}>
          ↻ Refresh
        </button>
      </div>

      {error && (
        <div style={{ background: '#fef2f2', border: '1px solid #fca5a5', borderRadius: 8,
                      padding: 16, color: '#dc2626', marginBottom: 20 }}>
          {error}
        </div>
      )}

      {/* Pulse cards */}
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 24 }}>
        {KNOWN_JOBS.map(name => (
          <PulseCard key={name} jobName={name}
                     info={pulse?.jobs?.[name] || null} />
        ))}
      </div>

      {/* History table */}
      <div style={cardStyle}>
        <div style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap',
                      alignItems: 'center' }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>Run History</span>
          <span style={{ fontSize: 12, color: '#9ca3af', flex: 1 }}>
            {loading ? 'loading…' : `${total} total`}
          </span>
          <select value={jobFilter} onChange={e => setJobFilter(e.target.value)}
                  style={{ fontSize: 12, padding: '4px 8px', borderRadius: 6,
                           border: '1px solid #e5e7eb', background: '#fff' }}>
            <option value="">All jobs</option>
            {KNOWN_JOBS.map(j => (
              <option key={j} value={j}>{JOB_LABELS[j] || j}</option>
            ))}
          </select>
          <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
                  style={{ fontSize: 12, padding: '4px 8px', borderRadius: 6,
                           border: '1px solid #e5e7eb', background: '#fff' }}>
            <option value="">All statuses</option>
            <option value="success">success</option>
            <option value="error">error</option>
            <option value="running">running</option>
          </select>
        </div>

        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: '2px solid #e5e7eb' }}>
                {['ID', 'Job', 'Status', 'Started', 'Duration', 'Details'].map(h => (
                  <th key={h} style={{ padding: '8px 12px', textAlign: 'left', fontSize: 11,
                                       fontWeight: 700, textTransform: 'uppercase',
                                       letterSpacing: '.06em', color: '#9ca3af' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runs.map(r => <HistoryRow key={r.id} run={r} />)}
              {runs.length === 0 && !loading && (
                <tr>
                  <td colSpan={6} style={{ padding: '24px 12px', textAlign: 'center',
                                           color: '#9ca3af', fontSize: 13 }}>
                    No runs recorded yet
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {refreshed && (
          <div style={{ marginTop: 12, fontSize: 11, color: '#9ca3af' }}>
            As of {refreshed.toLocaleTimeString()}
          </div>
        )}
      </div>
    </div>
  )
}
