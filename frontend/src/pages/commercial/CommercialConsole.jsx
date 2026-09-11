/**
 * THE INTERNAL COMMERCIAL CONSOLE.
 *
 * WHAT AN OPERATOR HAS TO BE ABLE TO SEE HERE
 * -------------------------------------------
 * Not "incomplete". WHY. Every gated action on this page renders the server's
 * own reasons — "Settlement frequency has not been answered", "The
 * percentages total 97%, not 100%" — because an operator who is told only
 * that something is blocked has to go and find out what, and usually guesses.
 *
 * THE SPLIT IS NOT JSON ON A SCREEN
 * ---------------------------------
 * Parties, percentages, what is known and what is not are rendered as a table
 * with UNKNOWN shown as unknown rather than as an empty cell that reads like
 * zero. That distinction is the whole point of the underlying model and it
 * survives all the way to the pixel.
 *
 * NOTHING ON THIS PAGE MOVES MONEY. The settlement panel calculates and shows
 * a statement; there is no pay button, because there is no payout rail behind
 * one.
 */
import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../../api/client'

function Pill({ tone = 'neutral', children }) {
  const tones = {
    good: { bg: 'rgba(30,240,168,0.12)', border: 'rgba(30,240,168,0.35)', fg: '#1ef0a8' },
    warn: { bg: 'rgba(240,192,64,0.12)', border: 'rgba(240,192,64,0.35)', fg: '#f0c040' },
    bad: { bg: 'rgba(239,68,68,0.12)', border: 'rgba(239,68,68,0.35)', fg: '#ef4444' },
    neutral: { bg: 'rgba(255,255,255,0.06)', border: 'rgba(255,255,255,0.16)', fg: 'inherit' },
  }
  const t = tones[tone] || tones.neutral
  return (
    <span style={{
      padding: '2px 9px', borderRadius: 999, fontSize: 12, fontWeight: 600,
      background: t.bg, border: `1px solid ${t.border}`, color: t.fg,
      whiteSpace: 'nowrap',
    }}>{children}</span>
  )
}

function Blocked({ block }) {
  if (!block) return null
  return (
    <div style={{ display: 'grid', gap: 8 }}>
      {Object.entries(block).map(([key, row]) => (
        <div key={key} style={{
          padding: '8px 10px', borderRadius: 8,
          background: row.allowed ? 'rgba(30,240,168,0.06)' : 'rgba(255,255,255,0.03)',
          border: `1px solid ${row.allowed ? 'rgba(30,240,168,0.25)' : 'rgba(255,255,255,0.1)'}`,
        }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Pill tone={row.allowed ? 'good' : 'warn'}>
              {row.allowed ? 'Available' : 'Blocked'}
            </Pill>
            <strong style={{ fontSize: 13 }}>{row.label}</strong>
          </div>
          {/* THE REASONS, VERBATIM. Not a count, not a generic sentence. */}
          {!row.allowed && (
            <ul style={{ margin: '6px 0 0 18px', padding: 0, fontSize: 12, opacity: 0.85 }}>
              {row.reasons.map((reason, i) => <li key={i}>{reason}</li>)}
            </ul>
          )}
        </div>
      ))}
    </div>
  )
}

export default function CommercialConsole() {
  const { agreementId } = useParams()
  const [agreement, setAgreement] = useState(null)
  const [audit, setAudit] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([
      api.get(`/commercial/agreements/${agreementId}`),
      api.get(`/commercial/agreements/${agreementId}/audit?limit=40`).catch(() => ({ entries: [] })),
    ])
      .then(([body, log]) => { setAgreement(body); setAudit(log.entries || []) })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [agreementId])

  useEffect(() => { load() }, [load])

  async function act(path, body) {
    setBusy(true)
    setError('')
    try {
      await api.post(`/commercial/agreements/${agreementId}/${path}`, body || {})
      load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <div className="panel os-section">Loading…</div>
  if (!agreement) return <div className="panel os-section os-error">{error || 'Not found.'}</div>

  const can = new Set(agreement.capabilities || [])
  const allocation = agreement.allocation || {}
  const completeness = agreement.completeness || {}

  return (
    <div className="os-grid" style={{ gridTemplateColumns: '1fr' }}>
      {error && <div className="os-error">{error}</div>}

      <section className="panel os-section">
        <div className="panel-header">
          <h2 className="panel-title">{agreement.name || 'Commercial agreement'}</h2>
          <div style={{ display: 'flex', gap: 8 }}>
            <Pill>{agreement.agreement_type_label}</Pill>
            <Pill tone={agreement.status === 'active' ? 'good'
              : agreement.status === 'terms_required' ? 'warn' : 'neutral'}>
              {agreement.status_label}
            </Pill>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', fontSize: 13 }}>
          <span className="os-hint">Effective {agreement.effective_date || '—'}</span>
          <span className="os-hint">Currency {(agreement.currency || '').toUpperCase()}</span>
          <span className="os-hint">
            Terms {completeness.required_answered}/{completeness.required_total} answered
          </span>
          {agreement.references_t2_subscription && (
            <Pill>Also has a catalogue subscription</Pill>
          )}
        </div>
      </section>

      <section className="panel os-section">
        <div className="panel-header"><h2 className="panel-title">What this unblocks</h2></div>
        <Blocked block={agreement.blocking} />
      </section>

      <section className="panel os-section">
        <div className="panel-header">
          <h2 className="panel-title">Parties and split</h2>
          <Pill tone={allocation.valid ? 'good' : 'warn'}>
            {allocation.valid ? 'Reconciles' : 'Does not reconcile'}
          </Pill>
        </div>

        {(allocation.parties || []).length === 0 && (
          <p className="os-hint">No parties have been added yet.</p>
        )}

        {(allocation.parties || []).length > 0 && (
          <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ textAlign: 'left', opacity: 0.6 }}>
                <th style={{ padding: '6px 4px' }}>Party</th>
                <th style={{ padding: '6px 4px' }}>Type</th>
                <th style={{ padding: '6px 4px' }}>Share</th>
                <th style={{ padding: '6px 4px' }}>Receives settlement</th>
              </tr>
            </thead>
            <tbody>
              {allocation.parties.map((party) => (
                <tr key={party.party_id} style={{ borderTop: '1px solid rgba(255,255,255,0.07)' }}>
                  <td style={{ padding: '6px 4px' }}>{party.display_name}</td>
                  <td style={{ padding: '6px 4px', opacity: 0.7 }}>{party.party_type}</td>
                  <td style={{ padding: '6px 4px' }}>
                    {/* UNKNOWN IS PRINTED AS UNKNOWN. An empty cell here would
                        read as zero, which is the one thing this model refuses
                        to let a percentage mean. */}
                    {party.percent_known
                      ? `${Number(party.percent)}%`
                      : <Pill tone="warn">Not agreed</Pill>}
                  </td>
                  <td style={{ padding: '6px 4px', opacity: 0.8 }}>
                    {party.is_payee ? 'Yes' : 'No'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {!allocation.valid && (allocation.reasons || []).length > 0 && (
          <ul style={{ margin: '8px 0 0 18px', padding: 0, fontSize: 12, opacity: 0.85 }}>
            {allocation.reasons.map((reason, i) => <li key={i}>{reason}</li>)}
          </ul>
        )}
      </section>

      <section className="panel os-section">
        <div className="panel-header">
          <h2 className="panel-title">Terms</h2>
          <Pill tone={completeness.terms_complete ? 'good' : 'warn'}>
            {completeness.required_missing} still required
          </Pill>
        </div>
        <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
          <tbody>
            {(agreement.terms || []).map((term) => (
              <tr key={term.key} style={{ borderTop: '1px solid rgba(255,255,255,0.07)' }}>
                <td style={{ padding: '6px 4px', width: '45%' }}>
                  {term.label}
                  {term.audience === 'internal' && (
                    <span className="os-hint" style={{ marginLeft: 6 }}>internal</span>
                  )}
                </td>
                <td style={{ padding: '6px 4px' }}>
                  {term.state === 'answered'
                    ? (term.value_label ?? String(term.value))
                    : <Pill tone={term.state === 'required' ? 'warn' : 'neutral'}>
                        {term.state === 'required' ? 'Required' : 'Not stated'}
                      </Pill>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel os-section">
        <div className="panel-header"><h2 className="panel-title">Approval</h2></div>
        <input
          className="os-input"
          placeholder="Note or reason (required to suspend or end)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
          <button className="btn btn--primary" disabled={busy || !can.has('approve_agreement')}
            onClick={() => act('approve', { note })}>Approve terms</button>
          <button className="btn btn--secondary" disabled={busy || !can.has('activate_agreement')}
            onClick={() => act('activate', { note })}>Activate</button>
          <button className="btn btn--secondary" disabled={busy || !can.has('suspend_agreement')}
            onClick={() => act('suspend', { reason: note })}>Suspend</button>
          <button className="btn btn--secondary" disabled={busy || !can.has('end_agreement')}
            onClick={() => act('end', { reason: note })}>End</button>
        </div>
        {agreement.approval?.approved_at && (
          <p className="os-hint" style={{ marginTop: 8 }}>
            Approved {new Date(agreement.approval.approved_at).toLocaleString()}
            {agreement.approval.note ? ` — ${agreement.approval.note}` : ''}
          </p>
        )}
      </section>

      <section className="panel os-section">
        <div className="panel-header"><h2 className="panel-title">Agreement document</h2></div>
        <p className="os-hint">
          {agreement.document?.reference || agreement.document?.file_id
            ? (agreement.document.reference || 'A file is linked to this agreement.')
            : 'No document linked.'}
        </p>
        <p className="os-hint">
          A document records where the paper is. It does not approve anything —
          approval is the act above.
        </p>
      </section>

      <section className="panel os-section">
        <div className="panel-header"><h2 className="panel-title">History</h2></div>
        {audit.length === 0 && <p className="os-hint">Nothing recorded yet.</p>}
        <ul style={{ margin: 0, padding: 0, listStyle: 'none', fontSize: 12 }}>
          {audit.map((entry) => (
            <li key={entry.id} style={{
              padding: '6px 0', borderTop: '1px solid rgba(255,255,255,0.06)',
              display: 'flex', gap: 10, justifyContent: 'space-between',
            }}>
              <span>
                <strong>{entry.action.replace(/_/g, ' ')}</strong>
                {entry.note ? <span style={{ opacity: 0.75 }}> — {entry.note}</span> : null}
              </span>
              <span style={{ opacity: 0.55, whiteSpace: 'nowrap' }}>
                {entry.actor_name || 'system'} ·{' '}
                {entry.created_at ? new Date(entry.created_at).toLocaleString() : ''}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
