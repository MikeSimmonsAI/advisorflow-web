/**
 * CUSTOM DEMO REQUEST — on the deal.
 *
 * Book Demo schedules a walkthrough of a demo that already exists. This is
 * the other thing: the customer needs to see something specific. The
 * salesperson asks (starting from what discovery already captured); a manager
 * who leads that salesperson routes it to an existing demo or approves a
 * custom build with a builder and a target date. Status, the link, every
 * decision and its history stay here on the deal.
 *
 * `can_request` / `can_decide` are the SERVER's answers; every action is
 * re-checked there.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { Card, Chip, Info, dateTime } from './parts'

const TONE = { pending_review: 'amber', requested: 'amber', in_progress: 'amber', ready: 'green', delivered: 'green' }

function errText(e) { return (e && (e.detail || e.message)) || 'Something went wrong.' }

export default function CustomDemoRequest({ oppId, onChanged }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [asking, setAsking] = useState(false)
  const [needs, setNeeds] = useState('')
  const [choice, setChoice] = useState('approve_build')
  const [form, setForm] = useState({ demo_site_id: '', builder_user_id: '', target_date: '', note: '' })

  const load = useCallback(async () => {
    try { setD(await api.get('/sales/opportunities/' + oppId + '/demo-request')); setErr('') }
    catch (e) { setErr(errText(e)) }
  }, [oppId])
  useEffect(() => { load() }, [load])

  if (!d) return err ? <Card title="CUSTOM DEMO REQUEST"><p className="sw-subtle">{err}</p></Card> : null

  const run = async (fn) => {
    setBusy(true); setErr('')
    try { const r = await fn(); if (r) setD(r); await load(); if (onChanged) onChanged() }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const submit = () => run(async () => {
    const r = await api.post('/sales/opportunities/' + oppId + '/demo-request', { needs })
    setAsking(false); return r
  })
  const decide = () => run(() => api.post('/sales/opportunities/' + oppId + '/demo-request/decision', {
    action: choice,
    demo_site_id: choice === 'route_existing' ? form.demo_site_id || null : null,
    builder_user_id: choice === 'approve_build' ? form.builder_user_id || null : null,
    target_date: choice === 'approve_build' ? form.target_date || null : null,
    note: form.note || null,
  }))

  return (
    <Card title="CUSTOM DEMO REQUEST" sub="When the customer needs to see something specific"
          right={<Chip tone={TONE[d.status]}>{d.status_label}</Chip>}>
      {err && <p role="alert" style={{ color: '#a1340c', fontWeight: 600 }}>{err}</p>}

      {d.status && d.status !== 'not_requested' && (
        <div className="sw-infogrid">
          <Info label="STATUS" value={d.status_label} />
          <Info label="REQUESTED" value={d.requested_at ? dateTime(d.requested_at) : '—'} />
          <Info label="BUILDER" value={d.builder || '—'} />
          <Info label="TARGET" value={d.target_at ? dateTime(d.target_at) : '—'} />
        </div>
      )}
      {d.needs && d.status && d.status !== 'not_requested' && (
        <p style={{ whiteSpace: 'pre-wrap', margin: '10px 0' }}><b>Needs to see:</b> {d.needs}</p>
      )}
      {d.demo_url && (
        <a className="sw-btn sw-primary" href={d.demo_url} target="_blank" rel="noopener noreferrer"
           style={{ textDecoration: 'none' }}>Open demo</a>
      )}

      {d.can_request && !asking && (
        <button className="sw-btn sw-primary sw-mt" disabled={busy}
                onClick={() => { setNeeds(d.needs && d.status === 'not_requested' ? d.needs : (d.discovery_needs || '')); setAsking(true) }}>
          Request Custom Demo
        </button>
      )}
      {asking && (
        <div className="sw-field sw-mt">
          <label htmlFor="cdr-needs">WHAT DOES THE CUSTOMER NEED TO SEE?</label>
          <textarea id="cdr-needs" className="sw-textarea" value={needs} onChange={e => setNeeds(e.target.value)}
                    placeholder="Started from discovery. Add anything the builder needs to know." />
          <div className="sw-flex" style={{ justifyContent: 'flex-end', marginTop: 8, gap: 8 }}>
            <button className="sw-btn" onClick={() => setAsking(false)}>Cancel</button>
            <button className="sw-btn sw-primary" disabled={busy || !needs.trim()} onClick={submit}>
              {busy ? 'Sending…' : 'Send request'}
            </button>
          </div>
        </div>
      )}

      {d.can_decide && (
        <div className="sw-mt" style={{ border: '1px solid #e6d3a8', background: '#fffaf0', borderRadius: 10, padding: 12 }}>
          <b>Your decision</b>
          <div className="sw-flex" style={{ gap: 14, flexWrap: 'wrap', margin: '8px 0' }} role="radiogroup" aria-label="Decision">
            {[['approve_build', 'Approve a custom build'], ['route_existing', 'Use an existing demo'], ['send_back', 'Send back']].map(([v, l]) => (
              <label key={v} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <input type="radio" name="cdr-choice" checked={choice === v} onChange={() => setChoice(v)} /> {l}
              </label>
            ))}
          </div>
          {choice === 'approve_build' && (
            <div className="sw-grid-even">
              <div className="sw-field"><label htmlFor="cdr-builder">BUILDER</label>
                <select id="cdr-builder" className="sw-select" value={form.builder_user_id}
                        onChange={e => setForm(f => ({ ...f, builder_user_id: e.target.value }))}>
                  <option value="">Choose…</option>
                  {d.builders.map(b => <option key={b.user_id} value={b.user_id}>{b.name}</option>)}
                </select></div>
              <div className="sw-field"><label htmlFor="cdr-date">TARGET DATE</label>
                <input id="cdr-date" className="sw-input" type="date" value={form.target_date}
                       onChange={e => setForm(f => ({ ...f, target_date: e.target.value }))} /></div>
            </div>
          )}
          {choice === 'route_existing' && (
            <div className="sw-field"><label htmlFor="cdr-existing">EXISTING DEMO</label>
              {d.existing_demos.length ? (
                <select id="cdr-existing" className="sw-select" value={form.demo_site_id}
                        onChange={e => setForm(f => ({ ...f, demo_site_id: e.target.value }))}>
                  <option value="">Choose…</option>
                  {d.existing_demos.map(x => <option key={x.id} value={x.id}>{x.title}{x.for_company ? ' — built for ' + x.for_company : ''}</option>)}
                </select>
              ) : <p className="sw-subtle">No existing demos are published for this brand yet.</p>}
            </div>
          )}
          <div className="sw-field"><label htmlFor="cdr-note">NOTE {choice === 'send_back' ? '(what is missing)' : '(optional)'}</label>
            <textarea id="cdr-note" className="sw-textarea" value={form.note}
                      onChange={e => setForm(f => ({ ...f, note: e.target.value }))} /></div>
          <div className="sw-flex" style={{ justifyContent: 'flex-end' }}>
            <button className="sw-btn sw-primary" disabled={busy} onClick={decide}>{busy ? 'Saving…' : 'Save decision'}</button>
          </div>
        </div>
      )}

      {d.history.length > 0 && (
        <details className="sw-disclose sw-mt">
          <summary>History ({d.history.length})</summary>
          <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
            {d.history.map((h, i) => (
              <li key={i} style={{ marginBottom: 6 }}>
                <b>{h.summary}</b> — {h.at ? dateTime(h.at) : ''}{h.by ? ' · ' + h.by : ''}
                {h.detail && <div className="sw-subtle" style={{ whiteSpace: 'pre-wrap' }}>{h.detail}</div>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  )
}
