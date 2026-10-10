/* The exception queue. "Here are your assigned exceptions" — not "here is the
 * whole CRM". A VA sees only what is assigned to them; an administrator sees
 * the whole queue, assigns it, sweeps for new exceptions and takes escalations.
 *
 * Load states follow wsListState: a failed refresh keeps the last good rows and
 * says they may be out of date; a failed first load says so and takes focus to
 * Try again; an old response never overwrites a newer one. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import '../../styles/shared.css'
import './wholesale.css'
import { Alert, EvoApp, Hero, Panel, Tag } from './ds/ds'
import './ds/evo-pages.css'
import { errText, fmtWhen } from './wsShared'
import {
  exceptionStatusLabel, initialList, listView, loadFailed, loadStarted, loadSucceeded,
  resolveBlockedReason, retryDisabledReason, supportCode, sweepSummary,
} from './wsListState'

const OUTCOMES = [
  ['complete', 'Complete'],
  ['unable_to_verify', 'Unable to verify'],
  ['needs_more_info', 'Needs more info'],
  ['escalate', 'Escalate to owner'],
]
const SCOPES = [['mine', 'Assigned to me'], ['unassigned', 'Unassigned'], ['escalated', 'Escalated'], ['all', 'Everything open']]

const withCode = (e) => errText(e) + (supportCode(e) ? ` (support code ${supportCode(e)})` : '')

function Item({ it, manager, people, peopleReady, onDone }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [submitted, setSubmitted] = useState(false)   // resolved here; the list refresh may not have landed
  const [err, setErr] = useState('')
  const latch = useRef(false)                          // synchronous: two clicks in one tick see it
  async function go(path, body, resolves) {
    if (latch.current) return
    latch.current = true
    setBusy(true); setErr('')
    try { await api.post(`/wholesale/exceptions/${it.id}/${path}`, body); if (resolves) setSubmitted(true); onDone() }
    catch (e) { setErr(withCode(e)) }                  // the note stays in the box
    finally { latch.current = false; setBusy(false) }
  }
  const reasonFor = (k) => (submitted ? 'Already submitted — refresh the list to confirm.'
    : busy ? 'Saving…' : resolveBlockedReason(k, note))
  return (
    <li className="evo-panel ws-exc__item">
      <div className="ws-exc__head">
        <Tag kind={it.status === 'escalated' ? 'danger' : 'info'}>{it.kind_label || 'Exception'}</Tag>
        <strong>{it.title}</strong>
        {it.is_test ? <Tag kind="sandbox">Test</Tag> : null}
      </div>
      {it.subject?.label ? <div className="ws-comp__sub">{it.subject.label}
        {it.subject.phone ? ` · ${it.subject.phone}` : ''}{it.subject.email ? ` · ${it.subject.email}` : ''}</div> : null}
      {it.detail ? <p style={{ margin: '8px 0' }}>{it.detail}</p> : null}
      <div className="ws-comp__sub">
        raised {it.created_at ? fmtWhen(it.created_at) : 'at an unknown time'} · {exceptionStatusLabel(it.status)}
        {it.assigned_to_name ? ` · assigned to ${it.assigned_to_name}` : ''}
        {it.outcome_note ? ` · last note: ${it.outcome_note}` : ''}
      </div>
      {manager ? (
        <label className="ws-comp__sub" style={{ display: 'block', marginTop: 8 }}>Assign to{' '}
          <select className="filter-select" value={it.assigned_to_id || ''} disabled={busy || submitted || !peopleReady}
                  title={!peopleReady ? 'The list of people is not loaded, so assigning is unavailable.' : undefined}
                  onChange={e => go('assign', { assigned_to_id: e.target.value || null }, false)}>
            <option value="">— unassigned —</option>
            {people.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
      ) : null}
      <label style={{ display: 'block', marginTop: 8 }}>
        <span className="evo-sr">Note for this exception</span>
        <textarea className="settings-input" rows={2} style={{ width: '100%' }}
                  placeholder="What did you find? Required for anything but Complete."
                  value={note} onChange={e => setNote(e.target.value)} />
      </label>
      <div className="ws-exc__actions">
        {OUTCOMES.map(([k, label]) => {
          const why = reasonFor(k)
          return (
            <button key={k} type="button" className={`btn ${k === 'complete' ? 'btn--primary' : 'btn--secondary'}`}
                    disabled={!!why} title={why || undefined}
                    onClick={() => go('resolve', { outcome: k, note }, true)}>{label}</button>
          )
        })}
      </div>
      {note.trim() === '' && !submitted ? <div className="ws-comp__sub">Add a note to use Unable to verify, Needs more info or Escalate.</div> : null}
      {submitted ? <p className="ws-comp__sub" role="status">Submitted. Refreshing the list to confirm.</p> : null}
      {err ? <Alert kind="warn">{err}. Your note is kept — try again.</Alert> : null}
    </li>
  )
}

export default function WholesaleExceptions() {
  const [scope, setScope] = useState('mine')
  const [list, setList] = useState(initialList)
  const [manager, setManager] = useState(null)        // null until the server has said
  const [people, setPeople] = useState([])
  const [peopleLoaded, setPeopleLoaded] = useState(false)
  const [peopleErr, setPeopleErr] = useState('')
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')
  const [sweeping, setSweeping] = useState(false)
  const sweepLatch = useRef(false)
  const latest = useRef(0)                            // mirrors list.latest so a response can be checked synchronously

  const load = useCallback(async () => {
    const gen = latest.current + 1
    latest.current = gen
    setList((prev) => loadStarted({ ...prev, latest: gen - 1 }))
    try {
      const data = await api.get(`/wholesale/exceptions?scope=${scope}`)
      if (gen === latest.current) {
        setManager(!!data.manager)
        setList((prev) => loadSucceeded(prev, gen, data.items, Array.isArray(data.items) ? data.items.length : null))
      }
    } catch (e) {
      if (gen === latest.current) setList((prev) => loadFailed(prev, gen, errText(e), supportCode(e)))
    }
  }, [scope])
  useEffect(() => { load() }, [load])

  const loadPeople = useCallback(() => {
    setPeopleErr('')
    api.get('/admin/users').then(r => {
      const rows = Array.isArray(r) ? r : (r.users || r.items || [])
      setPeople(rows.map(u => ({ id: u.id, name: u.full_name || u.email })))
      setPeopleLoaded(true)
    }).catch(() => setPeopleErr('The list of people could not be loaded, so assigning is unavailable.'))
  }, [])
  useEffect(() => { if (manager && !peopleLoaded && !peopleErr) loadPeople() }, [manager, peopleLoaded, peopleErr, loadPeople])

  // A different scope is a different list: never show the old scope's rows under the new label.
  function changeScope(k) {
    if (k === scope) return
    setList((prev) => ({ ...initialList(), latest: prev.latest }))
    setScope(k)
  }

  async function sweep() {
    if (sweepLatch.current) return
    sweepLatch.current = true
    setSweeping(true); setErr(''); setNote('')
    try {
      setNote(sweepSummary(await api.post('/wholesale/exceptions/sweep', {})))
      load()
    } catch (e) { setErr(withCode(e)) }
    finally { sweepLatch.current = false; setSweeping(false) }
  }

  const view = listView(list)
  const items = list.rows || []
  const known = list.rows !== null
  const retry = retryDisabledReason(list)
  return (
    <EvoApp world="operations">
      <Hero scene="capital" eyebrow="Exception Queue" title="Exceptions"
            sub="Automation handles the volume. These are the things it could not settle — each one needs a person, an outcome and a note."
            actions={manager ? <button type="button" className="btn btn--primary" onClick={sweep} disabled={sweeping}
                                       title={sweeping ? 'A check is already running.' : undefined}>Check for new exceptions</button> : null} />
      {err ? <Alert kind="warn">{err}</Alert> : null}
      {note ? <Alert kind="ok">{note}</Alert> : null}
      {peopleErr ? <Alert kind="warn">{peopleErr}{' '}
        <button type="button" className="btn btn--secondary btn--sm" onClick={loadPeople}>Try again</button></Alert> : null}
      {view === 'stale' ? (
        <div className="evo-alert evo-alert--warn" role="alert">
          The exception list could not be refreshed: {list.error}
          {list.supportCode ? <> (support code {list.supportCode})</> : null}.
          {' '}The exceptions below were loaded earlier and may be out of date.
          {' '}<button type="button" className="btn btn--secondary btn--sm" onClick={load}
                      disabled={!!retry} title={retry || undefined}>Try again</button>
        </div>
      ) : null}
      <p className="evo-sr" role="status">{view === 'refreshing' ? 'Refreshing exceptions.' : ''}</p>
      {manager ? (
        <div className="ws-tabs" style={{ marginBottom: 12 }}>
          {SCOPES.map(([k, label]) => (
            <button key={k} type="button" className={`ws-tab ${scope === k ? 'is-active' : ''}`}
                    aria-pressed={scope === k} onClick={() => changeScope(k)}>{label}</button>
          ))}
        </div>
      ) : null}
      <Panel title={manager ? SCOPES.find(s => s[0] === scope)?.[1] : 'Your assigned exceptions'}
             count={known ? items.length : undefined}>
        {view === 'loading' ? <p className="ws-muted" role="status">Loading exceptions…</p> : null}
        {view === 'error' ? (
          <div role="alert">
            <p>The exception list could not be loaded: {list.error}
              {list.supportCode ? <> (support code {list.supportCode})</> : null}</p>
            <p className="ws-muted">Nothing has been changed.</p>
            <button type="button" className="btn btn--secondary" onClick={load}
                    disabled={!!retry} title={retry || undefined} autoFocus>Try again</button>
          </div>
        ) : null}
        {view === 'empty' ? (
          <p className="ws-muted">{scope === 'mine' ? 'Nothing assigned to you right now.' : 'Nothing here.'}</p>
        ) : null}
        {items.length ? (
          <ul style={{ padding: 0, margin: 0 }}>
            {items.map(it => <Item key={it.id} it={it} manager={!!manager} people={people}
                                   peopleReady={peopleLoaded} onDone={load} />)}
          </ul>
        ) : null}
      </Panel>
    </EvoApp>
  )
}
