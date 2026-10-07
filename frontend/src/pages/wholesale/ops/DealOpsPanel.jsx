/* The deal's human workflow: HOT/WARM/COLD (AI recommendation + reason + human
 * override + audit), Pause AI / Take over / Resume AI, click-to-call, callbacks
 * and notes. One GET /wholesale/ops/deals/{id}; every write goes to its own
 * endpoint and the panel reloads from the server - nothing is assumed.
 *
 * Nothing here sends a message. "Call" is the telephony click-to-call bridge
 * (components/telephony/CallButton): Twilio rings the operator's own phone
 * first, then the seller, who sees the organization's number. Its readiness
 * check runs the same compliance gate as the call itself, and it says
 * "Provider/config required" when no voice number or callback phone is set.
 */
import { useState } from 'react'
import { api } from '../../../api/client'
import { Alert, Panel, Tag } from '../ds/ds'
import { errText, fmtWhen } from '../wsShared'
import { useRecord } from '../wsRecordHook'
import RecoveryNote from '../wsRecoveryNote'
import { distributionLabel } from '../wsListState'
import CallButton from '../../../components/telephony/CallButton'
import './ops.css'

const TEMPS = ['HOT', 'WARM', 'COLD']

export function TemperaturePill({ temperature }) {
  if (!temperature) return null
  const t = temperature.effective || 'UNKNOWN'
  return (
    <span className={`wso-temp wso-temp--${t}`}
          title={temperature.effective_source === 'human'
            ? `Set by ${temperature.human?.by || 'a person'}: ${temperature.human?.reason || ''}`
            : `AI: ${temperature.ai?.reason || ''}`}>
      {t}<span className="wso-temp__src">· {temperature.effective_source === 'human' ? 'human' : 'AI'}</span>
    </span>
  )
}

/* The seller's temperature. A failed read says so (with retry); it never leaves
 * the chip blank as if the seller had no temperature, and a lead change never
 * shows the previous lead's value. */
export function LeadTemperatureChip({ leadId }) {
  const { rec, view, reload } = useRecord(
    () => api.get(`/wholesale/ops/leads/${leadId}/temperature`), String(leadId || ''), !!leadId)
  if (!leadId || view === 'loading') return null
  if (view === 'error') {
    return (
      <span role="alert" className="wso-small">
        Temperature unavailable{rec.supportCode ? ` (support code ${rec.supportCode})` : ''}{' '}
        <button type="button" className="wso-linkbtn" onClick={reload}>Try again</button>
      </span>
    )
  }
  return (
    <>
      <TemperaturePill temperature={rec.data} />
      {view === 'stale' ? (
        <span role="alert" className="wso-small wso-muted">
          {' '}may be out of date{rec.supportCode ? ` (support code ${rec.supportCode})` : ''}{' '}
          <button type="button" className="wso-linkbtn" onClick={reload}>Try again</button>
        </span>
      ) : null}
    </>
  )
}

/* Buyer / funding distribution status, read from the server's configuration. */
export function useDistribution() {
  const { rec, view, reload } = useRecord(async () => (await api.get('/wholesale/ops/pilot')).distribution ?? null)
  return { data: rec.data, rec, view, reload }
}

/* `state` is { data, rec, view, reload } (see useDistribution). Unknown is never
 * shown as "OFF": a failed read says the status could not be confirmed. */
export function DistributionNotice({ state, kind }) {
  if (!state || state.view === 'loading') return null
  const { data, rec, view, reload } = state
  if (view === 'error') {
    return (
      <div className="wso-banner wso-banner--warn" role="alert">
        <strong>{distributionLabel(null)}</strong> It could not be loaded: {rec.error}
        {rec.supportCode ? ` (support code ${rec.supportCode})` : ''}. Do not assume it is off.{' '}
        <button type="button" className="btn btn--secondary" onClick={reload}>Try again</button>
      </div>
    )
  }
  const d = data?.[kind]
  if (!d) return null
  return (
    <div className={`wso-banner${d.auto_distribution ? ' wso-banner--warn' : ''}`}>
      <strong>{distributionLabel(d)}</strong>{' '}
      {d.flow?.join(' → ')}. {d.note}
      {view === 'stale' ? (
        <span role="alert"> This could not be refreshed and may be out of date{rec.supportCode ? ` (support code ${rec.supportCode})` : ''}.{' '}
          <button type="button" className="btn btn--secondary" onClick={reload}>Try again</button></span>
      ) : null}
    </div>
  )
}

function TemperatureCard({ leadId, temp, reload }) {
  const [choice, setChoice] = useState('')
  const [reason, setReason] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  async function save() {
    setBusy(true); setErr('')
    try {
      await api.put(`/wholesale/ops/leads/${leadId}/temperature`, { temperature: choice, reason })
      setChoice(''); setReason(''); reload()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  async function clear() {
    setBusy(true); setErr('')
    try { await api.delete(`/wholesale/ops/leads/${leadId}/temperature`); reload() }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  return (
    <Panel title="Seller temperature">
      <div className="wso-row"><TemperaturePill temperature={temp} />
        {temp.ai_disagrees ? <Tag kind="info">AI now says {temp.ai.temperature}</Tag> : null}</div>
      <p className="wso-muted" style={{ margin: '8px 0' }}>
        <strong>AI recommendation:</strong> {temp.ai.temperature} — {temp.ai.reason}
      </p>
      {temp.human ? (
        <p className="wso-muted" style={{ margin: '0 0 8px' }}>
          <strong>Human override:</strong> {temp.human.temperature} by {temp.human.by || 'a person'} {fmtWhen(temp.human.at)} — {temp.human.reason}
        </p>
      ) : null}
      <div className="wso-form">
        <label className="wso-label">Set temperature
          <select className="wso-select" value={choice} onChange={e => setChoice(e.target.value)}>
            <option value="">— choose —</option>
            {TEMPS.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        <label className="wso-label">Why (kept in the audit history)
          <input className="wso-input" value={reason} onChange={e => setReason(e.target.value)} />
        </label>
        <div className="wso-row">
          <button className="btn btn--primary" disabled={busy || !choice || !reason.trim()} onClick={save}>Override</button>
          {temp.human ? <button className="btn btn--secondary" disabled={busy} onClick={clear}>Use AI again</button> : null}
        </div>
      </div>
      {err ? <Alert kind="warn">{err}</Alert> : null}
      {temp.history?.length ? (
        <details style={{ marginTop: 10 }}>
          <summary className="wso-small">Audit history ({temp.history.length})</summary>
          <ul className="wso-list" style={{ marginTop: 8 }}>
            {temp.history.map(h => (
              <li key={h.id} className="wso-small wso-muted">
                {fmtWhen(h.at)} · {h.actor_name || 'someone'} · {h.action === 'set'
                  ? `set ${h.human_temperature} (AI said ${h.ai_temperature})` : `cleared (AI: ${h.ai_temperature})`}
                {h.reason ? ` — ${h.reason}` : ''}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </Panel>
  )
}

function ControlCard({ leadId, control, reload }) {
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  async function go(action) {
    setBusy(true); setErr('')
    try { await api.post(`/wholesale/ops/leads/${leadId}/control/${action}`, {}); reload() }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const state = control.mode === 'human'
    ? `A person has taken over${control.taken_over_by_name ? ` (${control.taken_over_by_name})` : ''}. The AI will not send.`
    : control.paused_ai ? 'AI is paused. Only messages a person sends go out.' : 'AI may reply, within every compliance gate.'
  return (
    <Panel title="Conversation control">
      <div className="wso-row">
        <Tag kind={control.ai_may_send ? 'live' : 'danger'}>{control.ai_may_send ? 'AI active' : control.mode === 'human' ? 'Human' : 'AI paused'}</Tag>
        <span className="wso-muted">{state}</span>
      </div>
      <div className="wso-row" style={{ marginTop: 10 }}>
        {control.ai_may_send ? <button className="btn btn--secondary" disabled={busy} onClick={() => go('pause')}>Pause AI</button> : null}
        {control.mode !== 'human' ? <button className="btn btn--primary" disabled={busy} onClick={() => go('takeover')}>Take over</button> : null}
        {!control.ai_may_send ? <button className="btn btn--secondary" disabled={busy} onClick={() => go('resume')}>Resume AI</button> : null}
      </div>
      {err ? <Alert kind="warn">{err}</Alert> : null}
      {control.history?.length ? (
        <p className="wso-small wso-muted" style={{ marginTop: 8 }}>
          Last: {control.history[0].action} by {control.history[0].by || 'someone'} {fmtWhen(control.history[0].at)}
        </p>
      ) : null}
    </Panel>
  )
}

function CallCard({ leadId, calls, reload }) {
  const d = calls.dialer
  return (
    <Panel title="Call the seller">
      {d.eligible ? <CallButton leadId={leadId} label="Call seller" onDone={reload} /> : (
        <p className="wso-muted" style={{ margin: 0 }}>Calling is not available: {d.reasons?.join('; ') || d.state}</p>
      )}
      <p className="wso-small wso-muted">
        Voicemail: {calls.voicemail.outbound_voicemails_left} left by an approved voicemail message.
        Inbound voicemail from this seller appears in Communications.
      </p>
      {calls.calls?.length ? (
        <div className="wso-table-wrap">
          <table className="wso-table">
            <thead><tr><th>When</th><th>Direction</th><th>Status</th><th>Outcome</th><th>Answered by</th><th>Voicemail</th></tr></thead>
            <tbody>
              {calls.calls.map(c => (
                <tr key={c.id}><td>{fmtWhen(c.created_at)}</td><td>{c.direction}</td><td>{c.status}</td>
                  <td>{c.outcome ? c.outcome.replace(/_/g, ' ') : '—'}</td>
                  <td>{c.answered_by || '—'}</td><td>{c.voicemail_left ? 'left' : '—'}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="wso-small wso-muted">No calls recorded.</p>}
    </Panel>
  )
}

function CallbacksCard({ dealId, callbacks, reload }) {
  const [when, setWhen] = useState('')
  const [notes, setNotes] = useState('')
  const [err, setErr] = useState('')
  async function add() {
    setErr('')
    try {
      await api.post('/wholesale/ops/callbacks', { deal_id: dealId, due_at: new Date(when).toISOString(), notes })
      setWhen(''); setNotes(''); reload()
    } catch (e) { setErr(errText(e)) }
  }
  async function done(id) {
    setErr('')
    try { await api.post(`/wholesale/ops/callbacks/${id}/complete`, {}); reload() }
    catch (e) { setErr(errText(e)) }
  }
  return (
    <Panel title="Callbacks" count={callbacks.length}>
      <div className="wso-form">
        <label className="wso-label">When
          <input className="wso-input" type="datetime-local" value={when} onChange={e => setWhen(e.target.value)} />
        </label>
        <label className="wso-label">Note
          <input className="wso-input" value={notes} onChange={e => setNotes(e.target.value)} />
        </label>
        <button className="btn btn--primary" disabled={!when} onClick={add}>Schedule callback</button>
      </div>
      {err ? <Alert kind="warn">{err}</Alert> : null}
      <ul className="wso-list" style={{ marginTop: 10 }}>
        {callbacks.map(c => (
          <li key={c.id} className="wso-item">
            <div className="wso-row">
              <Tag kind={c.bucket === 'overdue' ? 'danger' : c.bucket === 'completed' ? 'live' : 'info'}>
                {c.status === 'cancelled' ? 'cancelled' : (c.bucket || c.status).replace('_', ' ')}</Tag>
              <span className="wso-small">{fmtWhen(c.due_at)}</span>
              {c.status === 'due' ? <button className="btn btn--secondary" onClick={() => done(c.id)}>Completed</button> : null}
            </div>
            {c.notes ? <p className="wso-item__body">{c.notes}</p> : null}
          </li>
        ))}
      </ul>
    </Panel>
  )
}

function NotesCard({ dealId, notes, reload }) {
  const [body, setBody] = useState('')
  const [editing, setEditing] = useState(null)
  const [draft, setDraft] = useState('')
  const [err, setErr] = useState('')
  async function add() {
    setErr('')
    try { await api.post(`/wholesale/ops/deals/${dealId}/notes`, { body }); setBody(''); reload() }
    catch (e) { setErr(errText(e)) }
  }
  async function save(id) {
    setErr('')
    try { await api.patch(`/wholesale/ops/notes/${id}`, { body: draft }); setEditing(null); reload() }
    catch (e) { setErr(errText(e)) }
  }
  async function remove(id) {
    setErr('')
    try { await api.delete(`/wholesale/ops/notes/${id}`); reload() }
    catch (e) { setErr(errText(e)) }
  }
  return (
    <Panel title="Notes" count={notes.length}>
      <textarea className="wso-textarea" placeholder="What did you learn? Who said what?" value={body}
                onChange={e => setBody(e.target.value)} aria-label="New note" />
      <div className="wso-row" style={{ marginTop: 8 }}>
        <button className="btn btn--primary" disabled={!body.trim()} onClick={add}>Add note</button>
      </div>
      {err ? <Alert kind="warn">{err}</Alert> : null}
      <ul className="wso-list" style={{ marginTop: 10 }}>
        {notes.map(n => (
          <li key={n.id} className="wso-item">
            <div className="wso-muted wso-small">
              {n.author_name || 'someone'} · {fmtWhen(n.created_at)}{n.edited_at ? ` · edited ${fmtWhen(n.edited_at)}` : ''}
            </div>
            {editing === n.id ? (
              <>
                <textarea className="wso-textarea" value={draft} onChange={e => setDraft(e.target.value)} aria-label="Edit note" />
                <div className="wso-row" style={{ marginTop: 6 }}>
                  <button className="btn btn--primary" onClick={() => save(n.id)}>Save</button>
                  <button className="btn btn--secondary" onClick={() => setEditing(null)}>Cancel</button>
                </div>
              </>
            ) : (
              <>
                <p className="wso-item__body">{n.body}</p>
                <div className="wso-row" style={{ marginTop: 6 }}>
                  <button className="btn btn--secondary" onClick={() => { setEditing(n.id); setDraft(n.body) }}>Edit</button>
                  <button className="btn btn--secondary" onClick={() => remove(n.id)}>Remove</button>
                </div>
              </>
            )}
          </li>
        ))}
      </ul>
    </Panel>
  )
}

/* Newest request wins, last good data survives a failed refresh, and a different
 * deal never shows the previous deal's data. */
export function useDealOps(dealId) {
  const { rec, view, reload } = useRecord(
    () => api.get(`/wholesale/ops/deals/${dealId}`), String(dealId || ''), !!dealId)
  return { data: rec.data, rec, view, load: reload }
}

export default function DealOpsPanel({ dealId }) {
  const { data, rec, view, load } = useDealOps(dealId)
  if (!data) {
    return view === 'error'
      ? <RecoveryNote what="Deal operations" rec={rec} view={view} onRetry={load} />
      : <p className="wso-muted" role="status">Loading…</p>
  }
  return (
    <div className="wso-grid">
      <RecoveryNote what="Deal operations" rec={rec} view={view} onRetry={load} />
      {data.lead_id ? (
        <>
          {data.dnc ? <Alert kind="warn">This seller is Do Not Contact. Nothing will be sent or called on any channel.</Alert> : null}
          <TemperatureCard leadId={data.lead_id} temp={data.temperature} reload={load} />
          <ControlCard leadId={data.lead_id} control={data.control} reload={load} />
          <CallCard leadId={data.lead_id} calls={data.calls} reload={load} />
        </>
      ) : <Alert kind="info">No owner is attached to this deal yet — temperature, conversation control and calling appear once one is.</Alert>}
      <CallbacksCard dealId={dealId} callbacks={data.callbacks} reload={load} />
      <NotesCard dealId={dealId} notes={data.notes} reload={load} />
    </div>
  )
}
