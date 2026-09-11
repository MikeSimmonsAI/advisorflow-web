/**
 * GodTwilioDiagnostics — Twilio delivery-receipt diagnostic screen.
 *
 * Surfaces GET /god/twilio-diagnostics — previously curl-only.
 * Shows: callback URL config, env vars present/absent, message delivery
 * breakdown, and a plain-English verdict about why receipts are or are not
 * arriving. Read-only; no mutations.
 *
 * GOD-07: reachability — was curl-only, now has a screen.
 */
import { useState, useEffect } from 'react'
import { api } from '../../api/client'

function StatusDot({ ok }) {
  return (
    <span style={{
      display: 'inline-block', width: 10, height: 10, borderRadius: '50%',
      background: ok ? 'var(--god-green, #22c55e)' : 'var(--god-red, #ef4444)',
      marginRight: 6, flexShrink: 0,
    }} />
  )
}

function EnvRow({ name, present }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8,
                  padding: '5px 0', borderBottom: '1px solid var(--god-border, var(--gm-card-line))',
                  fontSize: 13, fontFamily: 'monospace' }}>
      <StatusDot ok={present} />
      <span style={{ flex: 1 }}>{name}</span>
      <span style={{ color: present ? 'var(--god-green, #22c55e)' : 'var(--god-muted, #9ca3af)',
                     fontWeight: 500 }}>
        {present ? 'SET' : 'NOT SET'}
      </span>
    </div>
  )
}

function StatusCount({ label, value, total, color }) {
  const pct = total > 0 ? Math.round((value / total) * 100) : 0
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '6px 0', borderBottom: '1px solid var(--god-border, var(--gm-card-line))' }}>
      <span style={{ fontSize: 13, color: 'var(--god-text, var(--gm-blue))' }}>{label}</span>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
        <span style={{ fontSize: 13, fontWeight: 600, color }}>{value.toLocaleString()}</span>
        <span style={{ fontSize: 11, color: 'var(--god-muted, var(--gm-text))', minWidth: 36, textAlign: 'right' }}>
          {pct}%
        </span>
      </div>
    </div>
  )
}

export default function GodTwilioDiagnostics() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [refreshed, setRefreshed] = useState(null)

  const load = async () => {
    setLoading(true); setError(null)
    try {
      const res = await api.get('/god/twilio-diagnostics')
      setData(res)
      setRefreshed(new Date())
    } catch (e) {
      setError(e.detail || e.message || 'Failed to load diagnostics')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const pageStyle = {
    padding: '24px 32px',
    maxWidth: 860,
    fontFamily: 'var(--god-font, system-ui, sans-serif)',
    color: 'var(--god-text, var(--gm-blue))',
  }

  const cardStyle = {
    background: 'var(--god-card, var(--gm-panel))',
    border: '1px solid var(--god-border, var(--gm-card-line))',
    borderRadius: 10,
    padding: '20px 24px',
    marginBottom: 20,
  }

  const headingStyle = {
    fontSize: 13,
    fontWeight: 700,
    textTransform: 'uppercase',
    letterSpacing: '.07em',
    color: 'var(--god-muted, var(--gm-dim))',
    marginBottom: 14,
  }

  if (loading) return (
    <div style={{ ...pageStyle, color: 'var(--god-muted, var(--gm-dim))', padding: 48 }}>
      Loading Twilio diagnostics…
    </div>
  )
  if (error) return (
    <div style={{ ...pageStyle }}>
      <div style={{ background: 'var(--gm-pill-red-bg)', border: '1px solid var(--gm-pill-red-bd)', borderRadius: 8,
                    padding: 16, color: 'var(--gm-red)' }}>
        <strong>Error:</strong> {error}
      </div>
    </div>
  )
  if (!data) return null

  const statusMap = data.messages_by_delivery_status || {}
  const total = data.messages_total || 0
  const statusOrder = ['delivered', 'sent', 'pending', 'null', 'failed', 'undelivered']
  const statusColors = {
    delivered: 'var(--gm-teal)', sent: 'var(--gm-blue)', pending: 'var(--gm-amber)',
    null: 'var(--gm-text)', failed: 'var(--gm-red)', undelivered: 'var(--gm-red)',
  }
  const allStatuses = [...new Set([...statusOrder, ...Object.keys(statusMap)])]

  return (
    <div style={pageStyle}>
      <div style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>Twilio Diagnostics</h1>
            <p style={{ margin: '6px 0 0', color: 'var(--god-muted, var(--gm-dim))', fontSize: 14 }}>
              Why delivery receipts are or are not arriving — read-only
            </p>
          </div>
          <button onClick={load} disabled={loading} style={{
            fontSize: 12, padding: '6px 14px', borderRadius: 6,
            border: '1px solid var(--god-border, var(--gm-card-line))',
            background: 'var(--god-card, var(--gm-panel))', cursor: 'pointer',
            color: 'var(--god-text, var(--gm-blue))',
          }}>
            ↻ Refresh
          </button>
        </div>
        {refreshed && (
          <p style={{ margin: '8px 0 0', fontSize: 11, color: 'var(--god-muted, var(--gm-text))' }}>
            As of {refreshed.toLocaleTimeString()}
          </p>
        )}
      </div>

      {/* Verdict banner */}
      <div style={{
        ...cardStyle,
        background: data.can_receive_receipts ? 'var(--gm-pill-teal-bg)' : 'var(--gm-pill-red-bg)',
        border: `1px solid ${data.can_receive_receipts ? 'var(--gm-teal)' : 'var(--gm-pill-red-bd)'}`,
        marginBottom: 20,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <StatusDot ok={data.can_receive_receipts} />
          <strong style={{ fontSize: 14,
                           color: data.can_receive_receipts ? 'var(--gm-teal)' : 'var(--gm-red)' }}>
            {data.can_receive_receipts ? 'Delivery receipts can arrive' : 'Delivery receipts CANNOT arrive'}
          </strong>
        </div>
        <p style={{ margin: '10px 0 0', fontSize: 13,
                    color: data.can_receive_receipts ? 'var(--gm-teal)' : 'var(--gm-red)',
                    lineHeight: 1.6 }}>
          {data.verdict}
        </p>
      </div>

      {/* Callback URL */}
      <div style={cardStyle}>
        <div style={headingStyle}>Callback URL</div>
        <div style={{ fontSize: 13, marginBottom: 10 }}>
          <span style={{ color: 'var(--god-muted, var(--gm-dim))' }}>Source env var: </span>
          <code style={{ background: 'var(--god-bg, var(--gm-pill-blue-bg))', padding: '2px 6px',
                         borderRadius: 4, fontSize: 12 }}>
            {data.callback_url_source || '(none found)'}
          </code>
        </div>
        <div style={{ fontSize: 13, marginBottom: 10 }}>
          <span style={{ color: 'var(--god-muted, var(--gm-dim))' }}>Callback URL: </span>
          {data.callback_url
            ? <code style={{ background: 'var(--god-bg, var(--gm-pill-blue-bg))', padding: '2px 6px',
                             borderRadius: 4, fontSize: 12, wordBreak: 'break-all' }}>
                {data.callback_url}
              </code>
            : <span style={{ color: 'var(--gm-red)', fontWeight: 500 }}>NOT SET</span>
          }
        </div>
        <div style={{ fontSize: 13 }}>
          <span style={{ color: 'var(--god-muted, var(--gm-dim))' }}>Signature validation: </span>
          <span style={{
            fontWeight: 500,
            color: data.signature_validation?.startsWith('enforced') ? 'var(--gm-teal)' : 'var(--gm-amber)'
          }}>
            {data.signature_validation}
          </span>
        </div>
      </div>

      {/* Env vars */}
      <div style={cardStyle}>
        <div style={headingStyle}>Environment Variables</div>
        {data.env_present && Object.entries(data.env_present).map(([k, v]) => (
          <EnvRow key={k} name={k} present={v} />
        ))}
      </div>

      {/* Message delivery breakdown */}
      <div style={cardStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      marginBottom: 14 }}>
          <div style={headingStyle}>Message Delivery Breakdown</div>
          <span style={{ fontSize: 13, fontWeight: 600 }}>
            {total.toLocaleString()} total
          </span>
        </div>
        {allStatuses
          .filter(s => statusMap[s] !== undefined)
          .map(s => (
            <StatusCount
              key={s}
              label={s === 'null' ? 'no status recorded' : s}
              value={typeof statusMap[s] === 'number' ? statusMap[s] : 0}
              total={total}
              color={statusColors[s] || 'var(--gm-dim)'}
            />
          ))
        }
        <div style={{ marginTop: 12, fontSize: 12, color: 'var(--god-muted, var(--gm-dim))' }}>
          <span>Messages with a receipt: </span>
          <strong>{(data.messages_with_a_receipt || 0).toLocaleString()}</strong>
          {data.newest_receipt_at && (
            <span> · Last receipt: {new Date(data.newest_receipt_at).toLocaleString()}</span>
          )}
        </div>
      </div>
    </div>
  )
}
