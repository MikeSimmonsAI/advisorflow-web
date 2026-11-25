// In-app calling from the EvoSys BookIt number (Twilio Voice in the browser).
// Caller ID is set by the server (always the team number); this panel only
// shows what is happening and lets the person mute, hang up and log an outcome.
import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'

const API = '/sales/evosys'
const OUTCOMES = [['talked', 'Talked'], ['voicemail', 'Left voicemail'], ['no_answer', 'No answer'],
                  ['booked', 'Booked a call'], ['not_interested', 'Not interested'], ['wrong_number', 'Wrong number']]
const LABEL = { connecting: 'Connecting…', ringing: 'Ringing…', open: 'Connected', ended: 'Call ended', failed: 'Call failed' }

// Twilio's wording is for developers; say what the person can do instead.
function plain(e) {
  const code = e?.code, name = e?.name || '', msg = e?.message || ''
  if (code === 31401 || code === 31208 || name === 'NotAllowedError' || /permission/i.test(name + msg))
    return 'Your browser blocked the microphone. Allow microphone access for app.evosyspro.live, then press Call again.'
  if (code === 31000 || code === 31005 || /network|connection/i.test(msg)) return 'The connection dropped. Check your internet and try again.'
  if (code === 31486 || /busy/i.test(msg)) return 'Their line is busy.'
  return 'The call could not go through. Try again in a moment.'
}

function fmtNum(n) {
  const d = String(n || '').replace(/\D/g, '').slice(-10)
  return d.length === 10 ? `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}` : n || ''
}

export default function CallPanel({ lead, onClose, onLogged }) {
  const [state, setState] = useState('starting')
  const [from, setFrom] = useState(null)
  const [muted, setMuted] = useState(false)
  const [secs, setSecs] = useState(0)
  const [err, setErr] = useState(null)
  const [callRowId, setCallRowId] = useState(null)
  const [saved, setSaved] = useState(null)
  const dev = useRef(null), call = useRef(null), tick = useRef(null)

  useEffect(() => {
    let dead = false
    ;(async () => {
      try {
        const t = await api.get(`${API}/voice/token`)
        if (!t.ready) { setErr(t.blocker || 'Calling is not set up.'); setState('failed'); return }
        setFrom(t.number)
        const { Device } = await import('@twilio/voice-sdk')
        if (dead) return
        const d = new Device(t.token, { codecPreferences: ['opus', 'pcmu'], closeProtection: true })
        dev.current = d
        setState('connecting')
        const c = await d.connect({ params: { lead_id: lead.id } })
        call.current = c
        c.on('ringing', () => setState('ringing'))
        c.on('accept', () => { setState('open'); tick.current = setInterval(() => setSecs(s => s + 1), 1000) })
        c.on('disconnect', () => { setState(s => (s === 'failed' ? s : 'ended')); clearInterval(tick.current); findRow() })
        c.on('cancel', () => { setState('ended'); clearInterval(tick.current); findRow() })
        c.on('reject', () => { setState('ended'); clearInterval(tick.current); findRow() })
        c.on('error', e => { setErr(plain(e)); setState('failed'); clearInterval(tick.current) })
      } catch (e) {
        setErr(plain(e))
        setState('failed')
      }
    })()
    return () => { dead = true; clearInterval(tick.current); try { call.current?.disconnect(); dev.current?.destroy() } catch { /* already gone */ } }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lead.id])

  async function findRow() {
    try {
      const d = await api.get(`${API}/leads/${lead.id}`)
      const row = [...(d.messages || [])].reverse().find(m => m.channel === 'call' && m.direction === 'out')
      if (row) setCallRowId(row.id)
    } catch { /* outcome just won't be offered */ }
  }

  const hang = () => { try { call.current?.disconnect() } catch { /* noop */ } }
  const mute = () => { const m = !muted; call.current?.mute(m); setMuted(m) }
  const log = async k => {
    try { await api.post(`${API}/leads/${lead.id}/calls/${callRowId}/outcome`, { outcome: k }); setSaved(k); onLogged?.() }
    catch (e) { setErr(e?.detail || 'Could not save the outcome.') }
  }
  const live = state === 'connecting' || state === 'ringing' || state === 'open' || state === 'starting'
  const mm = String(Math.floor(secs / 60)).padStart(1, '0') + ':' + String(secs % 60).padStart(2, '0')

  return (
    <div className="v2-callpanel" role="dialog" aria-label={`Call ${lead.company}`}>
      <div className="v2-callpanel-head">
        <div>
          <div className="v2-eyebrow">Calling from {from ? fmtNum(from) : 'the EvoSys BookIt number'}</div>
          <div className="v2-callname">{lead.company}</div>
          <div className="v2-callnum">{fmtNum(lead.phone)}</div>
        </div>
        <span className={'v2-callstate v2-cs-' + state} aria-live="polite">{state === 'open' ? `Connected · ${mm}` : (LABEL[state] || 'Starting…')}</span>
      </div>
      {err && <p className="v2-err" role="alert">{err}</p>}
      {live ? (
        <div className="v2-row">
          <button className="v2-btn" onClick={mute} disabled={state !== 'open'}>{muted ? 'Unmute' : 'Mute'}</button>
          <button className="v2-btn v2-danger" onClick={hang}>Hang up</button>
        </div>
      ) : (
        <>
          {callRowId && !saved && (
            <div>
              <div className="v2-label">How did it go?</div>
              <div className="v2-chips">{OUTCOMES.map(([k, l]) => <button key={k} className="v2-chip" onClick={() => log(k)}>{l}</button>)}</div>
            </div>
          )}
          {saved && <p className="v2-okmsg">Saved on {lead.company}'s timeline.</p>}
          <div className="v2-row"><button className="v2-btn" onClick={onClose}>Close</button></div>
        </>
      )}
    </div>
  )
}
