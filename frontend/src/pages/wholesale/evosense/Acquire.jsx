/* ACQUISITION READINESS - read-only views over existing machinery.
 *
 *   ReadyToRun        Command Center: can outreach run on its own, what is in
 *                     the way, and who needs contact data first
 *   FirstContactPlan  Property: which first contact is permitted now, the
 *                     approved wording, and why the other channels are not
 *
 * Nothing here sends, buys or changes anything.
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../../api/client'
import { Panel, Tag } from '../ds/ds'
import { errText } from '../wsShared'

export function ReadyToRun() {
  const [r, setR] = useState(null)
  const [q, setQ] = useState(null)
  const [calls, setCalls] = useState(null)
  useEffect(() => {
    api.get('/wholesale/evosense/automation/readiness').then(setR).catch(() => setR(null))
    api.get('/wholesale/evosense/enrichment/queue?limit=10').then(setQ).catch(() => setQ(null))
    api.get('/wholesale/evosense/call-queue?limit=10').then(setCalls).catch(() => setCalls(null))
  }, [])
  if (!r) return null
  return (
    <Panel title="Ready to run?" action={<Link className="evo-link" to="/wholesale/evosense/controls">Controls</Link>}>
      <p className="evo-small" style={{ marginTop: 0 }}>
        <b>{r.eligible_for_outreach_now}</b> owners can be contacted on a permitted channel now ·{' '}
        <b>{r.consents_confirmed}</b> confirmed text permissions ·{' '}
        contact lookups {r.enrichment_operational ? 'available' : 'not connected'}
      </p>
      {r.blockers?.length ? (
        <ul className="evo-small" style={{ margin: '0 0 8px', paddingLeft: 18 }}>
          {r.blockers.map((b) => <li key={b}>{b}</li>)}
        </ul>
      ) : <p className="evo-small">Nothing is blocking automated outreach.</p>}
      {calls ? (
        <p className="evo-small" style={{ margin: '8px 0 4px' }}>
          <b>Calls to make: {calls.rows.length}</b> · {calls.window.ok ? 'inside calling hours now' : `outside calling hours (opens ${calls.window.opens})`}
          {calls.rows.slice(0, 5).map((c) => (
            <span key={c.property_id} style={{ display: 'block' }}>
              <Link className="evo-feedlink" to={`/wholesale/evosense/property/${c.property_id}#call`}>{c.address}</Link>
              {c.sale_deadline ? ` · sale ${c.sale_deadline}` : ''}
            </span>))}
        </p>
      ) : null}
      {q && q.rows?.length ? (
        <>
          <p className="evo-small" style={{ margin: '8px 0 4px' }}><b>Need contact data first</b> ({q.needing_contact} in all)</p>
          <ol className="evo-small" style={{ margin: 0, paddingLeft: 18 }}>
            {q.rows.map((x) => (
              <li key={x.property_id}>
                <Link className="evo-feedlink" to={`/wholesale/evosense/property/${x.property_id}`}>{x.address}</Link>
                {x.priority != null ? ` · priority ${x.priority}` : ''}{x.sale_deadline ? ` · sale ${x.sale_deadline}` : ''}
              </li>
            ))}
          </ol>
        </>
      ) : null}
      <p className="evo-small evo-muted" style={{ marginTop: 8 }}>{r.notice}</p>
    </Panel>
  )
}

export function FirstContactPlan({ propertyId }) {
  const [open, setOpen] = useState(false)
  const [p, setP] = useState(null)
  useEffect(() => {
    if (open && !p) api.get(`/wholesale/evosense/properties/${propertyId}/first-contact`).then(setP).catch(() => setP(null))
  }, [open, p, propertyId])
  return (
    <Panel title="First contact" id="first-contact" hint="What may be said, on which channel - nothing is sent from here">
      <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? 'Hide' : 'Show'} the first-contact plan</button>
      {open && p ? (
        <div className="evo-small" style={{ marginTop: 10 }}>
          <div className="evo-chips" style={{ flexWrap: 'wrap', gap: 6 }}>
            {p.channels.map((c) => (
              <Tag key={c.channel} kind={c.permitted ? 'live' : 'estimate'} title={(c.why || []).join(' · ')}>
                {c.channel.toUpperCase()}: {c.permitted ? 'permitted' : 'not permitted'}
              </Tag>
            ))}
          </div>
          {p.channels.filter((c) => !c.permitted && c.why?.length).map((c) => (
            <p key={c.channel} style={{ margin: '6px 0 0' }}>{c.channel.toUpperCase()}: {c.why.join(' · ')}</p>
          ))}
          <p style={{ margin: '10px 0 4px' }}><b>Call talk track</b></p>
          <ol style={{ margin: 0, paddingLeft: 18 }}>{p.wording.call_track.map((t) => <li key={t}>{t}</li>)}</ol>
          <p style={{ margin: '10px 0 4px' }}><b>Rules on every call</b></p>
          <ul style={{ margin: 0, paddingLeft: 18 }}>{p.wording.call_rules.map((t) => <li key={t}>{t}</li>)}</ul>
          <p style={{ margin: '10px 0 4px' }}><b>After the call: permission link</b></p>
          <p style={{ margin: 0 }}>{p.wording.permission_link}</p>
          <p className="evo-muted" style={{ marginTop: 8 }}>{p.notice}</p>
        </div>
      ) : null}
    </Panel>
  )
}

const BATCH_STATE_TONE = { found: 'live', no_match: 'estimate', failed: 'danger', interrupted: 'danger', review: 'estimate',
  ready: undefined, skipped: undefined, processing: 'info', cap_reached: 'estimate' }

/* Contact lookups for an existing list: preflight (free) -> prepare -> a typed
 * approval naming the count and the cap -> start / pause / stop. */
export function ContactBatchPanel({ source }) {
  const [pf, setPf] = useState(null)
  const [batch, setBatch] = useState(null)
  const [phrase, setPhrase] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const load = () => {
    api.get(`/wholesale/evosense/contact-batches/preflight?source=${encodeURIComponent(source)}`).then(setPf).catch((e) => setErr(errText(e)))
    api.get('/wholesale/evosense/contact-batches').then((r) => {
      const mine = (r.batches || []).find((b) => b.source === source && !['stopped', 'completed'].includes(b.status))
      if (mine) api.get(`/wholesale/evosense/contact-batches/${mine.id}`).then(setBatch)
      else setBatch(null)
    }).catch(() => {})
  }
  useEffect(load, [source])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (batch?.status !== 'running') return undefined
    const t = setInterval(() => api.get(`/wholesale/evosense/contact-batches/${batch.id}`).then(setBatch).catch(() => {}), 15000)
    return () => clearInterval(t)
  }, [batch?.id, batch?.status])
  async function act(fn) {
    setBusy(true); setErr('')
    try { await fn() } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  if (!pf) return null
  const s = pf.summary
  const pr = s.provider
  return (
    <Panel title="Contact lookups" id="contact-lookups" hint="Nothing is bought until an admin types the approval">
      <div className="evo-chips" style={{ flexWrap: 'wrap', gap: 8 }}>
        <span className="evo-tag"><b>{s.ready}</b>&nbsp;ready for a lookup</span>
        <span className="evo-tag"><b>{s.manual_review}</b>&nbsp;need a person first</span>
        <span className="evo-tag"><b>{s.skipped}</b>&nbsp;skipped</span>
        <span className="evo-tag"><b>{s.unique_owners}</b>&nbsp;owners · {s.duplicate_owners} duplicate</span>
        <span className="evo-tag">Most it could cost: <b>&nbsp;{s.max_spend}</b></span>
      </div>
      <p className="evo-small" style={{ margin: '8px 0 0' }}>
        {pr.label}: {pr.price.per_hit} per find, {pr.price.per_miss} per miss (vendor price read {pr.price.read_on}
        {pr.price.minimum_purchase ? `; ${pr.price.minimum_purchase} minimum credit purchase` : ''}). {s.spend_note}
      </p>
      {pr.blocked_now?.length ? (
        <p className="evo-small evo-muted" style={{ margin: '4px 0 0' }}>Switched off now: {pr.blocked_now.join(' · ')}.
          {' '}Approving a batch turns on only what it needs, up to its cap, and records the change.</p>
      ) : null}
      {Object.keys(s.review_reasons || {}).length ? (
        <details className="evo-small" style={{ marginTop: 6 }}><summary>Why {s.manual_review} need a person first</summary>
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{Object.entries(s.review_reasons).map(([k, v]) => <li key={k}>{v} — {k}</li>)}</ul>
        </details>) : null}
      {!batch ? (
        <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" style={{ marginTop: 10 }} disabled={busy || !s.ready}
                onClick={() => act(async () => setBatch(await api.post('/wholesale/evosense/contact-batches', { source })))}>
          Prepare a batch of {s.ready} (free)</button>
      ) : (
        <div style={{ marginTop: 10 }} className="evo-small">
          <p style={{ margin: 0 }}><b>Batch {batch.status}</b> · {batch.looked_up}/{batch.max_lookups} looked up · {batch.found} found ·
            {' '}{batch.no_match} no verified match · {batch.failed} failed · spent {batch.spent} of {batch.max_spend}
            {batch.stopped_reason ? ` · ${batch.stopped_reason}` : ''}</p>
          {batch.status === 'draft' ? (
            <form style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}
                  onSubmit={(e) => { e.preventDefault(); act(async () => setBatch(await api.post(`/wholesale/evosense/contact-batches/${batch.id}/approve`, { phrase }))) }}>
              <label className="evo-small" style={{ flex: 1, minWidth: 260 }}>Type <b>{batch.approval_phrase}</b> to approve
                <input className="evo-input" value={phrase} onChange={(e) => setPhrase(e.target.value)} aria-label="Approval phrase" /></label>
              <button type="submit" className="evo-btn evo-btn--primary evo-btn--sm" disabled={busy || !phrase}>Approve</button>
              <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy}
                      onClick={() => act(async () => { await api.post(`/wholesale/evosense/contact-batches/${batch.id}/stop`); load() })}>Discard</button>
            </form>
          ) : null}
          <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
            {['approved', 'paused'].includes(batch.status) ? <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" disabled={busy}
              onClick={() => act(async () => { await api.post(`/wholesale/evosense/contact-batches/${batch.id}/${batch.status === 'paused' ? 'resume' : 'start'}`); load() })}>
              {batch.status === 'paused' ? 'Resume' : 'Start lookups'}</button> : null}
            {batch.status === 'running' ? <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy}
              onClick={() => act(async () => { await api.post(`/wholesale/evosense/contact-batches/${batch.id}/pause`); load() })}>Pause</button> : null}
            {['approved', 'running', 'paused'].includes(batch.status) ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy}
              onClick={() => act(async () => { await api.post(`/wholesale/evosense/contact-batches/${batch.id}/stop`); load() })}>Stop</button> : null}
            <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy} title="Stops this batch and pauses every paid lookup in the workspace"
              onClick={() => act(async () => { await api.post(`/wholesale/evosense/contact-batches/${batch.id}/emergency_stop`); load() })}>Emergency stop</button>
          </div>
          {batch.items?.length ? (
            <details style={{ marginTop: 8 }}><summary>Every property in this batch ({batch.items.length})</summary>
              <table className="evo-table evo-small"><tbody>
                {batch.items.map((i) => (
                  <tr key={i.property_id}><td className="is-num">{i.position}</td>
                    <td><Link to={`/wholesale/evosense/property/${i.property_id}`}>{i.address}</Link></td>
                    <td><Tag kind={BATCH_STATE_TONE[i.state]}>{i.state_label}</Tag></td><td>{i.reason}</td></tr>
                ))}
              </tbody></table>
            </details>) : null}
        </div>
      )}
      {err ? <p className="evo-small" style={{ color: '#b91c1c' }}>{err}</p> : null}
    </Panel>
  )
}

/* One click after a call - feeds suppression, wrong party, follow-ups,
 * hand-offs and seller intent. Nothing dials from here. */
export function CallOutcomePanel({ propertyId, onDone }) {
  const [q, setQ] = useState(null)
  const [note, setNote] = useState('')
  const [when, setWhen] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.get('/wholesale/evosense/call-queue?limit=500').then(setQ).catch(() => setQ(null)) }, [propertyId])
  if (!q) return null
  const row = (q.rows || []).find((r) => r.property_id === propertyId)
  if (!row) return null
  async function send(outcome) {
    setBusy(true); setMsg('')
    try {
      const r = await api.post(`/wholesale/evosense/properties/${propertyId}/call-outcome`, { outcome, note: note || null, callback_at: when || null })
      setMsg(`${r.label}. ${(r.actions || []).join(' · ')}`); setNote(''); setWhen('')
      if (onDone) onDone()
    } catch (e) { setMsg(errText(e)) } finally { setBusy(false) }
  }
  return (
    <Panel title="Call the owner" id="call" hint={q.window.ok ? `Inside calling hours · ${q.window.local_time}` : `Outside calling hours (${q.window.local_time}) · opens ${q.window.opens}`}>
      <p className="evo-small" style={{ marginTop: 0 }}>
        {row.numbers.map((n) => (
          <span key={n.contact_point_id} style={{ display: 'block' }}>
            <b>{n.number}</b> {n.line_type ? `(${n.line_type})` : ''} {n.verified ? '· verified' : '· not yet verified'} · from {n.source}
            {n.restrictions.length ? <span style={{ color: '#b91c1c' }}> · do not call: {n.restrictions.join('; ')}</span> : null}
          </span>))}
      </p>
      <p className="evo-small evo-muted" style={{ margin: '0 0 8px' }}>{q.window.rule}</p>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <input className="evo-input" style={{ flex: 1, minWidth: 220 }} placeholder="Note (what was said)" value={note} onChange={(e) => setNote(e.target.value)} />
        <input className="evo-input" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} aria-label="Call back at" />
      </div>
      <div className="evo-chips" style={{ flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
        {Object.entries(q.outcomes).map(([k, label]) => (
          <button key={k} type="button" className="evo-bucketbtn" disabled={busy || (k === 'callback' && !when)}
                  title={k === 'callback' && !when ? 'Pick the call-back time first' : undefined} onClick={() => send(k)}>{label}</button>
        ))}
      </div>
      {msg ? <p className="evo-small" style={{ marginTop: 8 }}>{msg}</p> : null}
    </Panel>
  )
}
