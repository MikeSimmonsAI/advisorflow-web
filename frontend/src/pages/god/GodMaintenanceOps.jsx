/**
 * GOD-08: Maintenance Ops
 *
 * Two read-safe tabs:
 *   1. Phone Audit   — POST /god/maintenance/phone-audit (read-only, changes nothing)
 *   2. Booking Cleanup — POST /god/maintenance/booking-cleanup
 *        Step 1: dry-run (apply=false) — shows found record + plan
 *        Step 2: confirmation dialog
 *        Step 3: apply (apply=true) — marks cancelled, deletes calendar event, no SMS/email
 *
 * Per GOD-08 rule: the APPLY step is behind a hard confirmation boundary.
 * The backend defaults apply=false, so a network glitch never silently commits.
 */

import React, { useState } from 'react'
import { api } from '../../api/client'

// ─── tiny shared primitives ────────────────────────────────────────────────

function Label({ children }) {
  return (
    <label style={{ display: 'block', fontSize: 12, fontWeight: 600,
                    color: 'var(--text-muted, #888)', textTransform: 'uppercase',
                    letterSpacing: '0.06em', marginBottom: 4 }}>
      {children}
    </label>
  )
}

function Input({ value, onChange, placeholder, style = {} }) {
  return (
    <input
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      style={{
        width: '100%', boxSizing: 'border-box',
        padding: '8px 12px', borderRadius: 6,
        border: '1px solid var(--border, #d1d5db)',
        background: 'var(--input-bg, #fff)',
        color: 'var(--text, #111)',
        fontSize: 13, fontFamily: 'monospace',
        ...style,
      }}
    />
  )
}

function Btn({ onClick, disabled, children, variant = 'default' }) {
  const bg = variant === 'danger'  ? '#dc2626'
           : variant === 'primary' ? '#2563eb'
           : '#374151'
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: '8px 18px', borderRadius: 6, border: 'none',
        background: disabled ? '#9ca3af' : bg,
        color: '#fff', fontSize: 13, fontWeight: 600,
        cursor: disabled ? 'not-allowed' : 'pointer',
      }}
    >
      {children}
    </button>
  )
}

function Well({ children, color = 'default' }) {
  const border = color === 'green'  ? '#16a34a'
               : color === 'yellow' ? '#d97706'
               : color === 'red'    ? '#dc2626'
               : 'var(--border, #d1d5db)'
  const bg     = color === 'green'  ? 'rgba(22,163,74,.07)'
               : color === 'yellow' ? 'rgba(217,119,6,.07)'
               : color === 'red'    ? 'rgba(220,38,38,.07)'
               : 'var(--surface-2, #f9fafb)'
  return (
    <div style={{
      border: `1px solid ${border}`, borderRadius: 8,
      background: bg, padding: '14px 16px',
      fontSize: 13, lineHeight: 1.6,
    }}>
      {children}
    </div>
  )
}

function Spinner() {
  return <span style={{ opacity: 0.6, fontStyle: 'italic' }}>Working…</span>
}

function ErrorMsg({ msg }) {
  if (!msg) return null
  return <Well color="red"><strong>Error:</strong> {msg}</Well>
}

function JsonTree({ data }) {
  return (
    <pre style={{
      margin: 0, fontSize: 12, lineHeight: 1.5,
      whiteSpace: 'pre-wrap', wordBreak: 'break-word',
      color: 'var(--text, #111)',
    }}>
      {JSON.stringify(data, null, 2)}
    </pre>
  )
}

// ─── Tab: Phone Audit ──────────────────────────────────────────────────────

function PhoneAuditTab() {
  const [numbers, setNumbers] = useState('')
  const [orgId,   setOrgId]   = useState('')
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState('')
  const [result,  setResult]  = useState(null)

  async function runAudit() {
    setError(''); setResult(null)
    const nums = numbers.split(/[\n,]+/).map(s => s.trim()).filter(Boolean)
    if (!nums.length) { setError('Enter at least one phone number.'); return }
    setLoading(true)
    try {
      const body = { numbers: nums }
      if (orgId.trim()) body.organization_id = orgId.trim()
      const res = await api.post('/god/maintenance/phone-audit', body)
      setResult(res.data)
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Well color="yellow">
        <strong>Read-only.</strong> This endpoint changes nothing. It reports which leads
        and users own the given numbers (last-10-digit matching).
      </Well>

      <div>
        <Label>Phone numbers (one per line or comma-separated)</Label>
        <textarea
          value={numbers}
          onChange={e => setNumbers(e.target.value)}
          placeholder="+14695537417&#10;+12125551234"
          rows={4}
          style={{
            width: '100%', boxSizing: 'border-box',
            padding: '8px 12px', borderRadius: 6,
            border: '1px solid var(--border, #d1d5db)',
            background: 'var(--input-bg, #fff)',
            color: 'var(--text, #111)',
            fontSize: 13, fontFamily: 'monospace', resize: 'vertical',
          }}
        />
      </div>

      <div>
        <Label>Organization ID (optional — narrows lead search)</Label>
        <Input value={orgId} onChange={setOrgId} placeholder="org_…" />
      </div>

      <div>
        <Btn onClick={runAudit} disabled={loading} variant="primary">
          {loading ? 'Searching…' : 'Run Audit'}
        </Btn>
      </div>

      <ErrorMsg msg={error} />

      {loading && <Spinner />}

      {result && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {Object.entries(result.results || {}).map(([num, match]) => (
            <Well key={num}>
              <div style={{ fontWeight: 700, marginBottom: 8, fontSize: 14 }}>
                {num}
              </div>

              <div style={{ marginBottom: 8 }}>
                <span style={{ fontWeight: 600 }}>Leads ({match.leads?.length ?? 0})</span>
                {match.leads?.length ? (
                  <table style={{ width: '100%', borderCollapse: 'collapse',
                                  marginTop: 6, fontSize: 12 }}>
                    <thead>
                      <tr style={{ background: 'var(--surface-3, #f3f4f6)' }}>
                        {['Name','Phone','Status','Org','Assigned To','Duplicate?','Source','Created'].map(h => (
                          <th key={h} style={{ padding: '4px 8px', textAlign: 'left',
                                               borderBottom: '1px solid var(--border, #e5e7eb)' }}>
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {match.leads.map(l => (
                        <tr key={l.id}>
                          <td style={{ padding: '4px 8px' }}>{l.name || '—'}</td>
                          <td style={{ padding: '4px 8px', fontFamily: 'monospace' }}>{l.phone}</td>
                          <td style={{ padding: '4px 8px' }}>{l.status}</td>
                          <td style={{ padding: '4px 8px', fontFamily: 'monospace', fontSize: 11 }}>{l.organization_id}</td>
                          <td style={{ padding: '4px 8px', fontFamily: 'monospace', fontSize: 11 }}>{l.assigned_to_id || '—'}</td>
                          <td style={{ padding: '4px 8px' }}>{l.is_duplicate ? '⚠ yes' : 'no'}</td>
                          <td style={{ padding: '4px 8px' }}>{l.source_file || '—'}</td>
                          <td style={{ padding: '4px 8px', fontSize: 11 }}>{l.created_at?.slice(0,10) || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <span style={{ marginLeft: 8, color: 'var(--text-muted, #888)' }}>none found</span>
                )}
              </div>

              <div>
                <span style={{ fontWeight: 600 }}>
                  Users sending from this number ({match.users_sending_from_it?.length ?? 0})
                </span>
                {match.users_sending_from_it?.length ? (
                  <ul style={{ margin: '6px 0 0 16px', padding: 0 }}>
                    {match.users_sending_from_it.map(u => (
                      <li key={u.id} style={{ fontSize: 12, marginBottom: 4 }}>
                        <strong>{u.full_name}</strong> ({u.email}) — role: {u.role} —
                        org: <code style={{ fontSize: 11 }}>{u.organization_id}</code> —
                        twilio: <code style={{ fontSize: 11 }}>{u.twilio_phone_number || '—'}</code>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <span style={{ marginLeft: 8, color: 'var(--text-muted, #888)' }}>none found</span>
                )}
              </div>
            </Well>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Tab: Booking Cleanup ──────────────────────────────────────────────────

const CLEANUP_IDLE     = 'idle'
const CLEANUP_DRYRUN   = 'dryrun'       // waiting for dry-run response
const CLEANUP_PREVIEW  = 'preview'      // showing dry-run result, awaiting confirm
const CLEANUP_CONFIRM  = 'confirm'      // confirmation dialog open
const CLEANUP_APPLYING = 'applying'     // waiting for apply response
const CLEANUP_DONE     = 'done'         // apply result shown

function BookingCleanupTab() {
  const [bookingId, setBookingId] = useState('')
  const [orgId,     setOrgId]     = useState('')
  const [reason,    setReason]    = useState('')
  const [phase,     setPhase]     = useState(CLEANUP_IDLE)
  const [error,     setError]     = useState('')
  const [dryResult, setDryResult] = useState(null)
  const [applyResult, setApplyResult] = useState(null)

  function reset() {
    setPhase(CLEANUP_IDLE); setError('')
    setDryResult(null); setApplyResult(null)
  }

  async function runDryRun() {
    setError(''); setDryResult(null)
    if (!bookingId.trim()) { setError('Booking ID is required.'); return }
    if (!orgId.trim())     { setError('Organization ID is required.'); return }
    setPhase(CLEANUP_DRYRUN)
    try {
      const res = await api.post('/god/maintenance/booking-cleanup', {
        booking_id: bookingId.trim(),
        organization_id: orgId.trim(),
        apply: false,
        reason: reason.trim(),
      })
      setDryResult(res.data)
      setPhase(CLEANUP_PREVIEW)
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Unknown error')
      setPhase(CLEANUP_IDLE)
    }
  }

  async function runApply() {
    setPhase(CLEANUP_APPLYING)
    try {
      const res = await api.post('/god/maintenance/booking-cleanup', {
        booking_id: bookingId.trim(),
        organization_id: orgId.trim(),
        apply: true,
        reason: reason.trim(),
      })
      setApplyResult(res.data)
      setPhase(CLEANUP_DONE)
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Unknown error')
      setPhase(CLEANUP_PREVIEW)
    }
  }

  const busy = phase === CLEANUP_DRYRUN || phase === CLEANUP_APPLYING

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Well color="yellow">
        <strong>Dry-run by default.</strong> The first request previews what would change —
        nothing is written. Apply only after reviewing the plan. No SMS, no email, no cadence
        restart. All rows are marked, never deleted.
      </Well>

      {/* ── inputs ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <Label>Booking ID</Label>
          <Input value={bookingId} onChange={setBookingId}
                 placeholder="booking_…" style={{ opacity: phase !== CLEANUP_IDLE ? 0.5 : 1 }} />
        </div>
        <div>
          <Label>Organization ID</Label>
          <Input value={orgId} onChange={setOrgId}
                 placeholder="org_…" style={{ opacity: phase !== CLEANUP_IDLE ? 0.5 : 1 }} />
        </div>
      </div>

      <div>
        <Label>Reason (written to audit log)</Label>
        <Input value={reason} onChange={setReason} placeholder="e.g. test booking during voice call 2026-09-09" />
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        {phase === CLEANUP_IDLE && (
          <Btn onClick={runDryRun} disabled={busy} variant="primary">
            Preview (dry run)
          </Btn>
        )}
        {(phase === CLEANUP_PREVIEW || phase === CLEANUP_CONFIRM) && (
          <>
            <Btn onClick={() => setPhase(CLEANUP_CONFIRM)} disabled={busy} variant="danger">
              Apply cleanup…
            </Btn>
            <Btn onClick={reset} disabled={busy}>Reset</Btn>
          </>
        )}
        {phase === CLEANUP_DONE && (
          <Btn onClick={reset}>Clean up another</Btn>
        )}
        {busy && <Spinner />}
      </div>

      <ErrorMsg msg={error} />

      {/* ── dry-run preview ── */}
      {dryResult && phase !== CLEANUP_DONE && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Well>
            <div style={{ fontWeight: 700, marginBottom: 10 }}>What was found</div>
            <JsonTree data={dryResult.found} />
          </Well>

          <Well color={dryResult.would_do?.includes?.('nothing') ? 'green' : 'yellow'}>
            <div style={{ fontWeight: 700, marginBottom: 8 }}>Would do</div>
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {(dryResult.would_do || []).map((s, i) => <li key={i}>{s}</li>)}
            </ul>
          </Well>

          <Well>
            <div style={{ fontWeight: 700, marginBottom: 8 }}>Will never do</div>
            <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--text-muted, #666)' }}>
              {(dryResult.will_never || []).map((s, i) => <li key={i}>{s}</li>)}
            </ul>
          </Well>
        </div>
      )}

      {/* ── apply result ── */}
      {applyResult && phase === CLEANUP_DONE && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Well color="green">
            <div style={{ fontWeight: 700, marginBottom: 8 }}>✓ Applied — actions taken</div>
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {(applyResult.did || []).map((s, i) => <li key={i}>{s}</li>)}
            </ul>
          </Well>
          <Well>
            <div style={{ fontWeight: 700, marginBottom: 8 }}>Never did</div>
            <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--text-muted, #666)' }}>
              {(applyResult.never_did || []).map((s, i) => <li key={i}>{s}</li>)}
            </ul>
          </Well>
          <Well>
            <div style={{ fontWeight: 700, marginBottom: 8 }}>Full record</div>
            <JsonTree data={applyResult.found} />
          </Well>
        </div>
      )}

      {/* ── confirmation dialog ── */}
      {phase === CLEANUP_CONFIRM && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          zIndex: 9999,
        }}>
          <div style={{
            background: 'var(--surface, #fff)', borderRadius: 10,
            padding: 28, maxWidth: 520, width: '90%',
            boxShadow: '0 8px 32px rgba(0,0,0,0.25)',
          }}>
            <div style={{ fontWeight: 700, fontSize: 18, marginBottom: 12, color: '#dc2626' }}>
              Apply booking cleanup?
            </div>
            <p style={{ fontSize: 13, lineHeight: 1.6, margin: '0 0 16px' }}>
              This will execute the plan shown in the preview. The booking will be marked
              cancelled, the calendar event deleted from the correct provider, and any case
              files voided. <strong>No SMS or email will be sent.</strong> All changes are
              permanent (rows are marked, not deleted).
            </p>
            {reason.trim() && (
              <Well style={{ marginBottom: 12 }}>
                <strong>Reason logged:</strong> {reason}
              </Well>
            )}
            <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end' }}>
              <Btn onClick={() => setPhase(CLEANUP_PREVIEW)}>Cancel</Btn>
              <Btn onClick={runApply} variant="danger">Yes, apply now</Btn>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Root component ────────────────────────────────────────────────────────

const TABS = [
  { key: 'phone',   label: 'Phone Audit'     },
  { key: 'booking', label: 'Booking Cleanup' },
]

export default function GodMaintenanceOps() {
  const [tab, setTab] = useState('phone')

  return (
    <div style={{ padding: '28px 32px', maxWidth: 900 }}>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700,
                     color: 'var(--text, #111)' }}>
          Maintenance Ops
        </h1>
        <p style={{ margin: '6px 0 0', fontSize: 13,
                    color: 'var(--text-muted, #666)' }}>
          Targeted, auditable cleanup tools. Silent by design — no SMS, no email,
          no cadence restart. Everything defaults to dry-run.
        </p>
      </div>

      {/* tab bar */}
      <div style={{
        display: 'flex', gap: 0, marginBottom: 24,
        borderBottom: '2px solid var(--border, #e5e7eb)',
      }}>
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            style={{
              padding: '8px 20px', border: 'none', background: 'none',
              fontSize: 13, fontWeight: 600, cursor: 'pointer',
              color: tab === t.key ? '#2563eb' : 'var(--text-muted, #888)',
              borderBottom: tab === t.key ? '2px solid #2563eb' : '2px solid transparent',
              marginBottom: -2,
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'phone'   && <PhoneAuditTab />}
      {tab === 'booking' && <BookingCleanupTab />}
    </div>
  )
}
