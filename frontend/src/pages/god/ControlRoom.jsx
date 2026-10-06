/**
 * Control Room — one screen for the ChatGPT <-> Claude relay.
 *
 * Reads GET /god/relay/state (polled + Refresh now). Give Direction posts
 * Mike's words to /god/relay/direction, which records them for ChatGPT Work to
 * review. It never reaches Claude directly. Light theme; reads --god-* tokens.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import {
  POLL_MS, toneFor, showNeedsMike, KIND_LABEL, timelineNewestFirst,
  makeSubmitGuard, directionDisabledReason, errorMessage,
} from './controlRoomView'

const CSS = `
.cr { font-family: var(--god-font, system-ui, sans-serif); color: var(--god-text, #1f2937); max-width: 1100px; }
.cr * { box-sizing: border-box; }
.cr h1 { font-size: 22px; margin: 0 0 4px; }
.cr .sub { color: var(--god-muted, #6b7280); font-size: 13px; margin-bottom: 14px; }
.cr .bar { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:14px; }
.cr button { font: inherit; cursor: pointer; }
.cr .btn { padding: 8px 14px; border-radius: 8px; border: 1px solid var(--god-border,#d1d5db); background: var(--god-card,#fff); font-size: 13px; font-weight: 600; }
.cr .btn.primary { background: #1d4ed8; color: #fff; border-color: #1d4ed8; }
.cr .btn:disabled { opacity: .5; cursor: not-allowed; }
.cr .card { background: var(--god-card,#fff); border: 1px solid var(--god-border,#e5e7eb); border-radius: 12px; padding: 16px; margin-bottom: 14px; }
.cr .card h2 { font-size: 12px; letter-spacing: .06em; text-transform: uppercase; color: var(--god-muted,#6b7280); margin: 0 0 10px; }
.cr .hero { border-width: 2px; }
.cr .pill { display:inline-block; padding: 4px 12px; border-radius: 999px; font-weight: 700; font-size: 13px; border: 1px solid; }
.cr .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px 18px; margin-top: 12px; }
.cr .lbl { font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: var(--god-muted,#6b7280); }
.cr .val { font-size: 14px; font-weight: 600; word-break: break-word; }
.cr .task { font-size: 18px; font-weight: 700; margin: 10px 0 0; }
.cr .needs { background:#fef2f2; border: 3px solid #dc2626; border-radius: 14px; padding: 20px; margin-bottom: 14px; }
.cr .needs h2 { color:#991b1b; font-size: 18px; margin: 0 0 10px; text-transform: none; letter-spacing: 0; }
.cr .needs dt { font-size: 11px; font-weight: 800; color:#991b1b; text-transform: uppercase; margin-top: 10px; }
.cr .needs dd { margin: 2px 0 0; font-size: 15px; }
.cr .choices { display:flex; gap:10px; margin-top: 16px; flex-wrap: wrap; }
.cr .setup { background:#fffbeb; border:1px solid #fcd34d; color:#92400e; border-radius:8px; padding:10px 12px; font-size:13px; margin-bottom:10px; }
.cr .err { background:#fef2f2; border:1px solid #fca5a5; color:#991b1b; border-radius:8px; padding:8px 12px; font-size:13px; margin-bottom:10px; }
.cr .ok { background:#ecfdf5; border:1px solid #6ee7b7; color:#065f46; border-radius:8px; padding:8px 12px; font-size:13px; margin-bottom:10px; }
.cr textarea { width:100%; min-height: 84px; padding: 10px; border-radius: 8px; border:1px solid var(--god-border,#d1d5db); font: inherit; font-size: 14px; background: var(--god-field,#fff); color: inherit; }
.cr .modes { display:flex; gap:14px; flex-wrap:wrap; margin: 10px 0; font-size: 13px; }
.cr .cols { display:grid; grid-template-columns: 2fr 1fr; gap: 14px; align-items:start; }
@media (max-width: 800px) { .cr .cols { grid-template-columns: 1fr; } .cr .grid { grid-template-columns: 1fr 1fr; } }
.cr ol { list-style:none; margin:0; padding:0; }
.cr li.ev { display:flex; gap:10px; padding: 9px 0; border-bottom: 1px solid var(--god-border,#f3f4f6); }
.cr li.ev:last-child { border-bottom: 0; }
.cr .who { flex: 0 0 74px; font-size: 11px; font-weight: 800; text-transform: uppercase; color: var(--god-muted,#6b7280); padding-top: 2px; }
.cr .who.needs { color:#b91c1c; }
.cr .evt { font-size: 14px; font-weight: 600; }
.cr .evd { font-size: 13px; color: var(--god-muted,#4b5563); }
.cr .evt-time { font-size: 11px; color: var(--god-dim,#9ca3af); }
.cr details { margin-top: 4px; font-size: 11px; }
.cr details pre { white-space: pre-wrap; word-break: break-all; background: var(--god-surface,#f9fafb); padding: 8px; border-radius: 6px; margin: 4px 0 0; }
.cr .note { font-size: 13px; padding: 6px 0; border-bottom: 1px solid var(--god-border,#f3f4f6); }
.cr .q { font-size: 13px; padding: 5px 0; }
.cr .tag { font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 6px; background:#e0e7ff; color:#3730a3; margin-left: 6px; }
`

const fmt = (v) => v || '—'

function Field({ label, value }) {
  return <div><div className="lbl">{label}</div><div className="val">{fmt(value)}</div></div>
}

function StatusHeader({ s }) {
  const t = toneFor(s.status)
  return (
    <div className="card hero" style={{ borderColor: t.bd, background: t.bg }} data-testid="relay-status" data-tone={t.key}>
      <span className="pill" style={{ color: t.fg, borderColor: t.bd, background: '#fff' }}>{t.label}</span>
      <p className="task" style={{ color: t.fg }}>{s.task}</p>
      <div className="grid">
        <Field label="Current actor" value={s.actor} />
        <Field label="Started (CT)" value={s.started_ct} />
        <Field label="Elapsed" value={s.elapsed} />
        <Field label="Last update (CT)" value={s.last_update_ct} />
        <Field label="Project" value={s.project} />
        <Field label="Branch" value={s.branch} />
      </div>
      <div style={{ marginTop: 12 }}><div className="lbl">Next action</div><div className="val">{s.next_action}</div></div>
    </div>
  )
}

function NeedsMike({ gate, onChoose, busy, disabled }) {
  return (
    <section className="needs" role="alert" data-testid="needs-mike">
      <h2>NEEDS MIKE</h2>
      <dl>
        <dt>Decision needed</dt><dd>{gate.decision}</dd>
        <dt>Why</dt><dd>{gate.why}</dd>
        <dt>Risk / impact</dt><dd>{gate.risk}</dd>
      </dl>
      <div className="choices">
        {gate.choices.map((c) => (
          <button key={c.id} className={'btn' + (c.id === 'approve' ? ' primary' : '')}
                  disabled={busy || disabled} onClick={() => onChoose(c)}>{c.label}</button>
        ))}
      </div>
      {disabled && <div className="lbl" style={{ marginTop: 8 }}>Answering from here needs the one-time setup below. You can also reply in GitHub issue #1.</div>}
    </section>
  )
}

function Timeline({ events }) {
  const rows = timelineNewestFirst(events)
  if (!rows.length) return <div className="evd">No relay activity yet.</div>
  return (
    <ol data-testid="timeline">
      {rows.map((e) => (
        <li className="ev" key={e.key}>
          <div className={'who' + (e.kind === 'approval_gate' ? ' needs' : '')}>{KIND_LABEL[e.kind] || 'Relay'}</div>
          <div style={{ flex: 1 }}>
            <div className="evt">{e.title}</div>
            {e.detail && <div className="evd">{e.detail}</div>}
            <div className="evt-time">{e.at_ct}</div>
            <details>
              <summary>Technical details</summary>
              <pre>{JSON.stringify({ run: e.run_id, ...e.technical }, null, 1)}</pre>
            </details>
          </div>
        </li>
      ))}
    </ol>
  )
}

function Composer({ data, onSent }) {
  const [text, setText] = useState('')
  const [mode, setMode] = useState(data.default_mode || 'next_priority')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const guard = useRef(makeSubmitGuard()).current
  const blocked = directionDisabledReason(data)

  const send = async () => {
    if (!guard.begin(text, mode)) return
    setBusy(true); setMsg(null)
    try {
      const r = await api.post('/god/relay/direction', { text, mode })
      guard.end(true)
      setText('')
      setMsg({ ok: true, t: r.chatgpt_signalled
        ? 'Sent. ChatGPT will read it first and decide what Claude does next.'
        : 'Recorded. The ChatGPT wake-up did not go through; it will pick this up on its next check.' })
      onSent()
    } catch (e) {
      guard.end(false)
      setMsg({ ok: false, t: errorMessage(e) })
    } finally { setBusy(false) }
  }

  return (
    <div className="card" data-testid="composer">
      <h2>Give direction</h2>
      {blocked && <div className="setup" data-testid="setup-required">{blocked}</div>}
      <textarea value={text} maxLength={2000} disabled={!!blocked || busy} aria-label="Give direction"
                placeholder="e.g. Finish the campus numbers before voice work."
                onChange={(e) => setText(e.target.value)} />
      <div className="modes" role="radiogroup" aria-label="Direction mode">
        {(data.modes || []).map((m) => (
          <label key={m.id}><input type="radio" name="mode" checked={mode === m.id}
                                   disabled={!!blocked} onChange={() => setMode(m.id)} /> {m.label}</label>
        ))}
      </div>
      {msg && <div className={msg.ok ? 'ok' : 'err'}>{msg.t}</div>}
      <button className="btn primary" disabled={!!blocked || busy || !text.trim()} onClick={send}>
        {busy ? 'Sending…' : 'Send to ChatGPT'}
      </button>
      <div className="evd" style={{ marginTop: 8 }}>ChatGPT reviews your words first. Nothing goes to Claude directly.</div>
    </div>
  )
}

export default function ControlRoom() {
  const [data, setData] = useState(null)
  const [loadErr, setLoadErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [answered, setAnswered] = useState(null)
  const [tick, setTick] = useState(0)

  const load = useCallback(async (refresh = false) => {
    try {
      setData(await api.get('/god/relay/state' + (refresh ? '?refresh=true' : '')))
      setLoadErr(null)
    } catch (e) { setLoadErr(errorMessage(e)) }
  }, [])

  useEffect(() => { load() ; const t = setInterval(() => load(), POLL_MS); return () => clearInterval(t) }, [load])
  useEffect(() => { const t = setInterval(() => setTick((n) => n + 1), 30000); return () => clearInterval(t) }, [])

  const choose = async (c) => {
    setBusy(true)
    try {
      await api.post('/god/relay/direction', { text: c.text, mode: 'next_priority' })
      setAnswered(c.label); load(true)
    } catch (e) { setLoadErr(errorMessage(e)) } finally { setBusy(false) }
  }

  if (!data) return <div className="cr"><style>{CSS}</style><h1>Control Room</h1><div className="sub">{loadErr || 'Loading…'}</div></div>
  const q = data.queue || {}
  return (
    <div className="cr" data-tick={tick}>
      <style>{CSS}</style>
      <h1>Control Room</h1>
      <div className="sub">ChatGPT and Claude, in one place. Staging view; updates itself every {POLL_MS / 1000}s.</div>
      <div className="bar">
        <button className="btn" onClick={() => load(true)}>Refresh now</button>
        {data.notify && <span className="evd">Email alerts: {data.notify.recipients_configured ? `${data.notify.recipients_configured} recipient(s) set, off in staging` : 'no recipients set'}</span>}
      </div>
      {(loadErr || !data.available) && <div className="err">{loadErr || data.error}</div>}
      {answered && <div className="ok">Your answer ({answered}) went to ChatGPT.</div>}

      {showNeedsMike(data) && <NeedsMike gate={data.needs_mike} onChoose={choose} busy={busy}
                                         disabled={!!directionDisabledReason(data)} />}
      <StatusHeader s={data.state} />

      {(data.notifications || []).length > 0 && (
        <div className="card"><h2>What just happened</h2>
          {data.notifications.slice().reverse().map((n, i) => <div className="note" key={i}>{n.text}</div>)}
        </div>)}

      <div className="cols">
        <div>
          <Composer data={data} onSent={() => load(true)} />
          <div className="card"><h2>Timeline</h2><Timeline events={data.events} /></div>
        </div>
        <div className="card" data-testid="queue"><h2>Active queue</h2>
          <div className="lbl">Current objective</div><div className="q">{q.current || 'Nothing running'}</div>
          <div className="lbl">Queued directions</div>
          {(q.queued || []).length ? q.queued.map((d, i) => <div className="q" key={d.input_id || i}>{i + 1}. {d.text}<span className="tag">{d.mode_label}</span></div>) : <div className="q">None</div>}
          <div className="lbl">Completed today</div>
          {(q.completed_today || []).length ? q.completed_today.map((d, i) => <div className="q" key={i}>✓ {d.title}</div>) : <div className="q">None yet</div>}
          <div className="lbl">Blocked / approval</div>
          {(q.attention || []).length ? q.attention.map((d, i) => <div className="q" key={i}>{d.title}</div>) : <div className="q">None</div>}
        </div>
      </div>
    </div>
  )
}
