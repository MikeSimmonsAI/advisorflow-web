/* The exception queue. "Here are your assigned exceptions" — not "here is the
 * whole CRM". A VA sees only what is assigned to them; an administrator sees
 * the whole queue, assigns it, sweeps for new exceptions and takes escalations. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import '../../styles/shared.css'
import './wholesale.css'
import { Alert, EvoApp, Hero, Panel, Tag } from './ds/ds'
import './ds/evo-pages.css'
import { errText, fmtWhen } from './wsShared'

const OUTCOMES = [
  ['complete', 'Complete'],
  ['unable_to_verify', 'Unable to verify'],
  ['needs_more_info', 'Needs more info'],
  ['escalate', 'Escalate to owner'],
]
const SCOPES = [['mine', 'Assigned to me'], ['unassigned', 'Unassigned'], ['escalated', 'Escalated'], ['all', 'Everything open']]

function Item({ it, manager, people, onDone }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  async function go(path, body) {
    setBusy(true); setErr('')
    try { await api.post(`/wholesale/exceptions/${it.id}/${path}`, body); onDone() }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  return (
    <li className="evo-panel" style={{ padding: 16, listStyle: 'none', marginBottom: 12 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <Tag kind={it.status === 'escalated' ? 'danger' : 'info'}>{it.kind_label}</Tag>
        <strong>{it.title}</strong>
        {it.is_test ? <Tag kind="sandbox">Test</Tag> : null}
      </div>
      {it.subject?.label ? <div className="ws-comp__sub">{it.subject.label}
        {it.subject.phone ? ` · ${it.subject.phone}` : ''}{it.subject.email ? ` · ${it.subject.email}` : ''}</div> : null}
      {it.detail ? <p style={{ margin: '8px 0' }}>{it.detail}</p> : null}
      <div className="ws-comp__sub">
        raised {fmtWhen(it.created_at)} · {it.status.replace(/_/g, ' ')}
        {it.assigned_to_name ? ` · assigned to ${it.assigned_to_name}` : ''}
        {it.outcome_note ? ` · last note: ${it.outcome_note}` : ''}
      </div>
      {manager ? (
        <label className="ws-comp__sub" style={{ display: 'block', marginTop: 8 }}>Assign to{' '}
          <select className="filter-select" value={it.assigned_to_id || ''} disabled={busy}
                  onChange={e => go('assign', { assigned_to_id: e.target.value || null })}>
            <option value="">— unassigned —</option>
            {people.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
      ) : null}
      <textarea className="settings-input" rows={2} style={{ width: '100%', marginTop: 8 }}
                placeholder="What did you find? Required for anything but Complete."
                value={note} onChange={e => setNote(e.target.value)} />
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
        {OUTCOMES.map(([k, label]) => (
          <button key={k} className={`btn ${k === 'complete' ? 'btn--primary' : 'btn--secondary'}`}
                  disabled={busy} onClick={() => go('resolve', { outcome: k, note })}>{label}</button>
        ))}
      </div>
      {err ? <Alert kind="warn">{err}</Alert> : null}
    </li>
  )
}

export default function WholesaleExceptions() {
  const [scope, setScope] = useState('mine')
  const [data, setData] = useState(null)
  const [people, setPeople] = useState([])
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')

  const load = useCallback(async () => {
    setErr('')
    try { setData(await api.get(`/wholesale/exceptions?scope=${scope}`)) }
    catch (e) { setErr(errText(e)) }
  }, [scope])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    if (!data?.manager || people.length) return
    api.get('/admin/users').then(r => {
      const list = Array.isArray(r) ? r : (r.users || r.items || [])
      setPeople(list.map(u => ({ id: u.id, name: u.full_name || u.email })))
    }).catch(() => {})
  }, [data, people.length])

  async function sweep() {
    setErr(''); setNote('')
    try {
      const r = await api.post('/wholesale/exceptions/sweep', {})
      const n = Object.values(r.raised || {}).reduce((a, b) => a + b, 0)
      setNote(n ? `${n} new exception${n === 1 ? '' : 's'} raised.` : 'Nothing new — the queue is up to date.')
      load()
    } catch (e) { setErr(errText(e)) }
  }

  const items = data?.items || []
  return (
    <EvoApp world="operations">
      <Hero scene="capital" eyebrow="Exception Queue" title="Exceptions"
            sub="Automation handles the volume. These are the things it could not settle — each one needs a person, an outcome and a note."
            actions={data?.manager ? <button className="btn btn--primary" onClick={sweep}>Check for new exceptions</button> : null} />
      {err ? <Alert kind="warn">{err}</Alert> : null}
      {note ? <Alert kind="ok">{note}</Alert> : null}
      {data?.manager ? (
        <div className="ws-tabs" style={{ marginBottom: 12 }}>
          {SCOPES.map(([k, label]) => (
            <button key={k} className={`ws-tab ${scope === k ? 'is-active' : ''}`} onClick={() => setScope(k)}>{label}</button>
          ))}
        </div>
      ) : null}
      <Panel title={data?.manager ? SCOPES.find(s => s[0] === scope)?.[1] : 'Your assigned exceptions'} count={items.length}>
        {!data ? null : !items.length ? (
          <p className="ws-muted">{scope === 'mine' ? 'Nothing assigned to you right now.' : 'Nothing here.'}</p>
        ) : (
          <ul style={{ padding: 0, margin: 0 }}>
            {items.map(it => <Item key={it.id} it={it} manager={data.manager} people={people} onDone={load} />)}
          </ul>
        )}
      </Panel>
    </EvoApp>
  )
}
