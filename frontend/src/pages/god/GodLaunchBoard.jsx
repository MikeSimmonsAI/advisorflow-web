/**
 * GodLaunchBoard — dynamic project portfolio for the Control Room.
 *
 * Talks to the god-only /god/launch-board API. Projects are data: add, edit
 * priority, approve, move lanes without a deploy. Nothing here starts a relay
 * run. Failed calls show the error; success is shown only after the server
 * accepted the change AND the board was reloaded. Every write sends the project's
 * expected_version (a change made elsewhere is refused with 409 and the board is
 * reloaded) and an Idempotency-Key (a double click or retry is applied once).
 */
import { useState, useEffect, useCallback } from 'react'
import { api } from '../../api/client'
import {
  LANES, LANE_LABELS, EVIDENCE_KINDS, EVIDENCE_STATES, BOARD_URL, failedBoard, normalizeBoard, errorText,
  laneCounts, launchGates, gatesSummary, statusLines, liveStatusFor, productText, laneActions, buildAction,
  newRequestKey, withExpectedVersion, writeHeaders, isStale,
} from '../../utils/launchBoard'

const card = { background: 'var(--gm-card-bg, var(--gm-pill-blue-bg))', border: '1px solid var(--gm-card-line)',
  borderRadius: 10, padding: 16, marginBottom: 16 }
const label = { color: 'var(--gm-dim)', fontSize: 12 }

function Project({ p, live, busy, onAct }) {
  const [open, setOpen] = useState(false)
  const [prio, setPrio] = useState(String(p.priority))
  const [ev, setEv] = useState({ kind: EVIDENCE_KINDS[0], state: 'verified', ref: '' })
  const [task, setTask] = useState('')
  const st = statusLines({ ...p, working_status: live })
  const gs = gatesSummary(p)
  return (
    <div style={{ padding: '10px 0', borderTop: '1px solid var(--gm-card-line)' }} data-testid={`project-${p.id}`}>
      <div>
        <b>#{p.priority} {p.name}</b>{' '}
        <span style={label}>{p.approved ? `approved by ${p.approved_by}` : 'NOT approved — not queue-eligible'}</span>
      </div>
      {p.summary ? <div style={label}>{p.summary}</div> : null}
      <div style={label}>Live worker: {st.working}</div>
      <div style={label}>Last completed task: {st.lastCompleted}</div>
      <div style={label}>{productText(p)}</div>
      <div style={{ ...label, color: gs.allMet ? 'var(--gm-teal)' : undefined }}>
        Launch gates: {gs.met}/{gs.total} verified{gs.allMet ? '' : ` — missing ${gs.missing.join(', ')}`}
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 6 }}>
        {p.approved
          ? <button type="button" disabled={busy} onClick={() => onAct(p, 'revoke', {})}>Revoke approval</button>
          : <button type="button" disabled={busy} onClick={() => onAct(p, 'approve', {})}>Approve for queue</button>}
        {laneActions(p).map(l => (
          <button type="button" key={l} disabled={busy} onClick={() => onAct(p, 'lane', { lane: l })}>
            {l === 'archived' ? 'Archive' : `Move to ${LANE_LABELS[l]}`}
          </button>
        ))}
        <input aria-label="Priority" size={3} value={prio} onChange={e => setPrio(e.target.value)} />
        <button type="button" disabled={busy} onClick={() => onAct(p, 'priority', { priority: prio })}>Set priority</button>
        <button type="button" onClick={() => setOpen(o => !o)}>{open ? 'Hide details' : 'Details'}</button>
      </div>
      {open ? (
        <div style={{ marginTop: 8 }}>
          {launchGates(p).map(g => (
            <div key={g.kind} style={label}>
              {g.kind}: <b>{g.state}</b>{g.ref ? ` · ref ${g.ref}` : ''}{g.state !== 'none' && !g.ref ? ' · NO REF (not counted)' : ''}
            </div>
          ))}
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
            <select aria-label="Evidence kind" value={ev.kind} onChange={e => setEv({ ...ev, kind: e.target.value })}>
              {EVIDENCE_KINDS.map(k => <option key={k}>{k}</option>)}
            </select>
            <select aria-label="Evidence state" value={ev.state} onChange={e => setEv({ ...ev, state: e.target.value })}>
              {EVIDENCE_STATES.map(k => <option key={k}>{k}</option>)}
            </select>
            <input aria-label="Evidence reference" placeholder="commit / run URL / test id" value={ev.ref}
              onChange={e => setEv({ ...ev, ref: e.target.value })} />
            <button type="button" disabled={busy} onClick={() => onAct(p, 'evidence', ev)}>Record evidence</button>
          </div>
          <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
            <input aria-label="Completed task" placeholder="task completed (does not complete the product)" value={task}
              onChange={e => setTask(e.target.value)} />
            <button type="button" disabled={busy} onClick={() => { onAct(p, 'task', { summary: task }); setTask('') }}>Log task</button>
            <button type="button" disabled={busy}
              onClick={() => onAct(p, 'product', { complete: p.product_state !== 'complete' })}>
              {p.product_state === 'complete' ? 'Unmark product complete' : 'Mark product complete'}
            </button>
          </div>
          <details style={{ marginTop: 6 }}>
            <summary style={label}>History ({(p.history || []).length} events, append-only)</summary>
            {(p.history || []).map((h, i) => <div key={i} style={label}>{h.at} · {h.actor} · {h.action} · {h.detail}</div>)}
          </details>
        </div>
      ) : null}
    </div>
  )
}

export default function GodLaunchBoard({ relayState, working }) {
  const [board, setBoard] = useState(failedBoard('Loading…'))
  const [lane, setLane] = useState('active')
  const [msg, setMsg] = useState(null)   // { kind: 'ok' | 'error', text }
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ name: '', summary: '', priority: '' })

  const reload = useCallback(async () => {
    try { setBoard(normalizeBoard(await api.get(BOARD_URL))) }
    catch (e) { setBoard(failedBoard(errorText(e))) }   // clears any previously shown board
  }, [])
  useEffect(() => { reload() }, [reload])

  const act = useCallback(async (project, action, input) => {
    const pid = project.id
    const body = withExpectedVersion(buildAction(action, input), project)
    if (!body) { setMsg({ kind: 'error', text: 'Not sent: the input is incomplete (evidence needs a reference).' }); return }
    setBusy(true)
    try {
      await api.post(`${BOARD_URL}/projects/${pid}/${action}`, body, writeHeaders(newRequestKey()))
      await reload()
      setMsg({ kind: 'ok', text: `Saved: ${action}` })
    } catch (e) {
      if (isStale(e)) {
        await reload()
        setMsg({ kind: 'error', text: 'Not saved — this project changed since the board was loaded. The board has been reloaded; check it and try again.' })
      } else {
        setMsg({ kind: 'error', text: `Not saved — ${errorText(e)}` })
      }
    }
    finally { setBusy(false) }
  }, [reload])

  const add = async () => {
    const name = form.name.trim()
    if (!name) { setMsg({ kind: 'error', text: 'Not sent: a name is required.' }); return }
    const body = { name, summary: form.summary }
    if (form.priority.trim()) {
      const n = Number(form.priority)
      if (!Number.isInteger(n) || n < 1) { setMsg({ kind: 'error', text: 'Not sent: priority must be a whole number, 1 or higher.' }); return }
      body.priority = n
    }
    setBusy(true)
    try {
      await api.post(`${BOARD_URL}/projects`, body, writeHeaders(newRequestKey()))
      setForm({ name: '', summary: '', priority: '' })
      await reload()
      setLane('backlog')
      setMsg({ kind: 'ok', text: 'Added to Backlog (unapproved). Nothing was started.' })
    } catch (e) { setMsg({ kind: 'error', text: `Not saved — ${errorText(e)}` }) }
    finally { setBusy(false) }
  }

  const counts = laneCounts(board)
  const rows = board.lanes[lane] || []
  return (
    <div style={card} data-testid="launch-board">
      <h3 style={{ marginTop: 0 }}>Project portfolio</h3>
      {!board.ok ? <div role="alert" style={{ color: 'var(--gm-red)' }}>Launch board unavailable — {board.reason}. No project data is shown.</div> : null}
      {board.storage && board.storage.warning ? (
        <div role="alert" style={{ color: 'var(--gm-red)', marginBottom: 8 }} data-testid="board-storage-warning">{board.storage.warning}</div>
      ) : null}
      {msg ? <div role={msg.kind === 'error' ? 'alert' : 'status'} style={{ color: msg.kind === 'error' ? 'var(--gm-red)' : 'var(--gm-teal)', marginBottom: 8 }}>{msg.text}</div> : null}
      <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
        {LANES.map(l => (
          <button type="button" key={l} aria-pressed={lane === l} onClick={() => setLane(l)}
            style={{ fontWeight: lane === l ? 700 : 400 }}>{LANE_LABELS[l]} ({counts[l]})</button>
        ))}
      </div>
      {board.ok && rows.length === 0 ? <div style={label}>No {LANE_LABELS[lane].toLowerCase()} projects.</div> : null}
      {rows.map(p => <Project key={p.id} p={p} busy={busy} onAct={act} live={liveStatusFor(p, relayState, working)} />)}
      {board.ok && board.queue.length ? (
        <div style={{ ...label, marginTop: 8 }}>Queue-eligible (approved, not archived): {board.queue.map(q => `#${q.priority} ${q.name}`).join(' · ')}</div>
      ) : null}
      <div style={{ marginTop: 12, borderTop: '1px solid var(--gm-card-line)', paddingTop: 8 }}>
        <b>Add project</b> <span style={label}>(lands in Backlog, unapproved; never starts work)</span>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
          <input aria-label="Project name" placeholder="Name" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
          <input aria-label="Project summary" placeholder="Summary" value={form.summary} onChange={e => setForm({ ...form, summary: e.target.value })} />
          <input aria-label="Project priority" placeholder="Priority" size={4} value={form.priority} onChange={e => setForm({ ...form, priority: e.target.value })} />
          <button type="button" disabled={busy} onClick={add}>Add</button>
        </div>
      </div>
    </div>
  )
}
