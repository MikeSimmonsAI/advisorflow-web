/**
 * God Mode — Customer Launches.
 *
 * Every implementation with the standing of its onboarding intake, and a way
 * into any one of them.
 *
 * IT LISTS CUSTOMERS WHO HAVE NOT STARTED. That is the whole point of the
 * screen. A list of customers who are making progress is a list that hides the
 * ones who opened the link in March and never came back — which are exactly
 * the ones somebody needs to call. `not_started` is sorted and labelled, not
 * filtered away.
 *
 * NO SECOND HIERARCHY. This reads GET /god/launch, which reports the existing
 * Implementation records. It does not define a status, own a customer, or
 * decide who may see one — require_god does that, server-side.
 */
import { useEffect, useState } from 'react'
import { api, API_BASE } from '../../api/client'

/** Fetch a protected file with the session's token and save it. */
async function downloadFile(orgId, f) {
  const token = localStorage.getItem('af_token')
    || localStorage.getItem('bookaboost_token')
  const res = await fetch(
    API_BASE + '/god/launch/' + orgId + '/files/' + f.id + '/download',
    { headers: token ? { Authorization: 'Bearer ' + token } : {} })
  if (!res.ok) { window.alert('Could not download that file.'); return }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = f.filename || 'download'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

const STATE_STYLE = {
  submitted:   { bg: '#eff6ff', text: '#1d4ed8', border: '#93c5fd', label: 'Submitted — needs review' },
  reviewed:    { bg: '#f0fdf4', text: '#166534', border: '#86efac', label: 'Reviewed' },
  in_progress: { bg: '#fffbeb', text: '#92400e', border: '#fcd34d', label: 'In progress' },
  not_started: { bg: '#f9fafb', text: '#6b7280', border: '#e5e7eb', label: 'Not started' },
}

function Pill({ state }) {
  const s = STATE_STYLE[state] || STATE_STYLE.not_started
  return (
    <span style={{ background: s.bg, color: s.text, border: '1px solid ' + s.border,
                   borderRadius: 999, padding: '3px 10px', fontSize: 11,
                   fontWeight: 700, whiteSpace: 'nowrap' }}>
      {s.label}
    </span>
  )
}

function Bar({ pct }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 130 }}>
      <div style={{ flex: 1, height: 6, borderRadius: 999,
                    background: 'var(--god-border, #e5e7eb)', overflow: 'hidden' }}>
        <i style={{ display: 'block', height: '100%', width: pct + '%',
                    background: pct >= 100 ? '#22c55e' : '#3b82f6' }} />
      </div>
      <b style={{ fontSize: 12, width: 34, textAlign: 'right' }}>{pct}%</b>
    </div>
  )
}

export default function GodLaunches() {
  const [rows, setRows] = useState([])
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = () => {
    setLoading(true)
    api.get('/god/launch')
      .then(d => { setRows(d.launches || []); setError(null) })
      .catch(e => setError(e?.detail || 'Could not load launches'))
      .finally(() => setLoading(false))
  }
  useEffect(load, [])

  const open = orgId => {
    setDetail({ loading: true, orgId })
    api.get('/god/launch/' + orgId)
      .then(d => setDetail({ ...d, orgId }))
      .catch(e => setDetail({ orgId, error: e?.detail || 'Could not load' }))
  }

  const markReviewed = async orgId => {
    await api.post('/god/launch/' + orgId + '/review', {})
    open(orgId)
    load()
  }

  const card = {
    background: 'var(--god-card, #fff)',
    border: '1px solid var(--god-border, #e5e7eb)',
    borderRadius: 10, padding: '20px 24px', marginBottom: 20,
  }

  return (
    <div style={{ padding: '28px 32px', color: 'var(--god-text, #1f2937)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between',
                    alignItems: 'flex-start', marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>Customer Launches</h1>
          <p style={{ margin: '6px 0 0', color: 'var(--god-muted, #6b7280)', fontSize: 14 }}>
            Onboarding intake for every implementation, including the ones nobody has opened
          </p>
        </div>
        <button onClick={load} disabled={loading} style={{
          fontSize: 12, padding: '6px 14px', borderRadius: 6,
          border: '1px solid var(--god-border, #e5e7eb)',
          background: 'var(--god-card, #fff)', cursor: 'pointer',
          color: 'var(--god-text, #1f2937)' }}>↻ Refresh</button>
      </div>

      {error && (
        <div style={{ background: '#fef2f2', border: '1px solid #fca5a5',
                      borderRadius: 8, padding: 16, color: '#dc2626',
                      marginBottom: 20 }}>{error}</div>
      )}

      <div style={card}>
        {loading ? <p style={{ color: '#9ca3af', fontSize: 13 }}>Loading…</p>
          : rows.length === 0
            ? <p style={{ color: '#9ca3af', fontSize: 13 }}>
                No implementations yet. A launch appears here as soon as a deal
                becomes a customer.
              </p>
            : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ textAlign: 'left', color: 'var(--god-muted, #6b7280)',
                                 fontSize: 11, textTransform: 'uppercase',
                                 letterSpacing: '.06em' }}>
                      <th style={{ padding: '8px 10px' }}>Customer</th>
                      <th style={{ padding: '8px 10px' }}>Intake</th>
                      <th style={{ padding: '8px 10px' }}>Progress</th>
                      <th style={{ padding: '8px 10px' }}>Sections</th>
                      <th style={{ padding: '8px 10px' }}>Files</th>
                      <th style={{ padding: '8px 10px' }}>Implementation</th>
                      <th style={{ padding: '8px 10px' }}>Target</th>
                      <th style={{ padding: '8px 10px' }} />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(r => (
                      <tr key={r.implementation_id}
                          style={{ borderTop: '1px solid var(--god-border, #e5e7eb)' }}>
                        <td style={{ padding: '10px' }}>
                          <b>{r.organization_name || r.organization_id}</b>
                        </td>
                        <td style={{ padding: '10px' }}><Pill state={r.intake_state} /></td>
                        <td style={{ padding: '10px' }}><Bar pct={r.overall_pct} /></td>
                        <td style={{ padding: '10px', color: '#6b7280' }}>
                          {r.complete_steps}/{r.total_steps}
                        </td>
                        <td style={{ padding: '10px', color: '#6b7280' }}>{r.file_count}</td>
                        <td style={{ padding: '10px', color: '#6b7280' }}>
                          {r.implementation_status}
                        </td>
                        <td style={{ padding: '10px', color: '#6b7280' }}>
                          {r.target_launch_date
                            ? new Date(r.target_launch_date).toLocaleDateString() : '—'}
                        </td>
                        <td style={{ padding: '10px', textAlign: 'right' }}>
                          <button onClick={() => open(r.organization_id)} style={{
                            fontSize: 12, padding: '4px 12px', borderRadius: 6,
                            border: '1px solid var(--god-border, #e5e7eb)',
                            background: 'var(--god-card, #fff)', cursor: 'pointer',
                            color: 'var(--god-text, #1f2937)' }}>Open</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
      </div>

      {detail && (
        <div style={card}>
          <div style={{ display: 'flex', justifyContent: 'space-between',
                        alignItems: 'center', marginBottom: 14 }}>
            <b style={{ fontSize: 14 }}>
              {detail.customer?.name || detail.orgId}
            </b>
            <div style={{ display: 'flex', gap: 8 }}>
              {detail.submission && !detail.submission.reviewed_at ? (
                <button onClick={() => markReviewed(detail.orgId)} style={{
                  fontSize: 12, padding: '5px 12px', borderRadius: 6,
                  border: '1px solid #86efac', background: '#f0fdf4',
                  color: '#166534', cursor: 'pointer', fontWeight: 600 }}>
                  Mark reviewed (reopens for edits)
                </button>
              ) : null}
              <button onClick={() => setDetail(null)} style={{
                fontSize: 12, padding: '5px 12px', borderRadius: 6,
                border: '1px solid var(--god-border, #e5e7eb)',
                background: 'var(--god-card, #fff)', cursor: 'pointer',
                color: 'var(--god-text, #1f2937)' }}>Close</button>
            </div>
          </div>

          {detail.loading ? <p style={{ color: '#9ca3af' }}>Loading…</p>
            : detail.error ? <p style={{ color: '#dc2626' }}>{detail.error}</p>
              : (
                <>
                  {detail.blockers?.length ? (
                    <div style={{ background: '#fffbeb', border: '1px solid #fcd34d',
                                  borderRadius: 8, padding: 12, marginBottom: 14,
                                  fontSize: 12, color: '#92400e' }}>
                      <b>Outstanding:</b>{' '}
                      {detail.blockers.map(b => b.step_label + ' → ' + b.label).join('; ')}
                    </div>
                  ) : null}

                  {Object.entries(detail.answers || {}).map(([key, step]) => {
                    const entries = Object.entries(step.answers || {})
                      .filter(([, v]) => v !== null && v !== '' && v !== false)
                    if (!entries.length && !step.secrets_set?.length) return null
                    return (
                      <div key={key} style={{ marginBottom: 14 }}>
                        <div style={{ fontSize: 11, fontWeight: 700,
                                      textTransform: 'uppercase', letterSpacing: '.06em',
                                      color: 'var(--god-muted, #6b7280)', marginBottom: 6 }}>
                          {key} — {step.pct}%
                        </div>
                        <div style={{ display: 'grid',
                                      gridTemplateColumns: 'repeat(auto-fill,minmax(240px,1fr))',
                                      gap: 8 }}>
                          {entries.map(([k, v]) => (
                            <div key={k} style={{ fontSize: 12 }}>
                              <span style={{ color: '#9ca3af' }}>{k}: </span>
                              <span>{String(v)}</span>
                            </div>
                          ))}
                          {(step.secrets_set || []).map(k => (
                            <div key={k} style={{ fontSize: 12 }}>
                              <span style={{ color: '#9ca3af' }}>{k}: </span>
                              {/* The value is not here and cannot be. Encrypted
                                  at rest with no read path in this app. */}
                              <span style={{ color: '#6b7280' }}>
                                stored securely — not viewable
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )
                  })}

                  <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
                                letterSpacing: '.06em', color: 'var(--god-muted, #6b7280)',
                                margin: '16px 0 6px' }}>
                    Files ({detail.files?.length || 0})
                  </div>
                  {(detail.files || []).length === 0
                    ? <p style={{ fontSize: 12, color: '#9ca3af' }}>Nothing uploaded yet.</p>
                    : (detail.files || []).map(f => (
                      <div key={f.id} style={{ fontSize: 12, marginBottom: 4 }}>
                        {/* A plain href cannot work here: the file lives on the
                            API origin and the route needs an Authorization
                            header, which a browser navigation will not send.
                            Fetch it with the client, then hand the bytes to a
                            blob URL that is revoked immediately after. */}
                        <a href="#" onClick={ev => {
                          ev.preventDefault()
                          downloadFile(detail.orgId, f)
                        }}>{f.filename}</a>
                        <span style={{ color: '#9ca3af' }}>
                          {' '}· {Math.round(f.file_size / 1024)} KB
                          {f.label ? ' · ' + f.label : ''}
                        </span>
                      </div>
                    ))}
                </>
              )}
        </div>
      )}
    </div>
  )
}
