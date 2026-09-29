// Click-to-call through the organization's Twilio number (stream XC).
//
// This is a BRIDGE, not a browser phone: Twilio rings the user's own saved
// callback phone first, and when they answer it dials the customer, who sees
// the organization's business number. There is no WebRTC calling in this
// stack, and this component never pretends there is.
//
// Every precondition comes from GET /calls/human/readiness/{leadId} - the
// same checks POST /calls/human enforces - so a missing voice number or a
// missing callback phone is shown as "Provider/config required" with what to
// set, instead of a button that fails.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import DispositionModal from './DispositionModal'
import './telephony.css'

function fmt(v) {
  if (!v) return ''
  const d = String(v).replace(/\D/g, '')
  const t = d.length === 11 && d.startsWith('1') ? d.slice(1) : d
  return t.length === 10 ? `(${t.slice(0, 3)}) ${t.slice(3, 6)}-${t.slice(6)}` : v
}

const LIVE = { initiating: 'Starting…', ringing_user: 'Ringing your phone', in_progress: 'Connected to the customer' }
const DONE = new Set(['completed', 'failed'])
const CONFIG_KEYS = new Set(['org_number', 'provider', 'callback_url'])

export default function CallButton({ leadId, onDone, label = 'Call' }) {
  const [ready, setReady] = useState(null)
  const [err, setErr] = useState('')
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [saving, setSaving] = useState(false)
  const [call, setCall] = useState(null)
  const [placing, setPlacing] = useState(false)
  const [dispo, setDispo] = useState(false)
  const [logged, setLogged] = useState(false)
  const timer = useRef(null)

  const load = useCallback(() => {
    if (!leadId) return
    setErr('')
    api.get(`/calls/human/readiness/${leadId}`).then(setReady)
      .catch(e => { setReady(null); setErr(e.message || 'Call setup could not be checked.') })
  }, [leadId])

  useEffect(() => { setCall(null); setLogged(false); load() }, [load])
  useEffect(() => () => clearTimeout(timer.current), [])

  const poll = useCallback((id, n = 0) => {
    clearTimeout(timer.current)
    timer.current = setTimeout(async () => {
      try {
        const c = await api.get(`/calls/${id}`)
        setCall(c)
        if (DONE.has(c.status)) { setDispo(true); onDone && onDone(); return }
      } catch { /* keep polling a little longer */ }
      if (n < 200) poll(id, n + 1)
    }, 3000)
  }, [onDone])

  async function place() {
    setPlacing(true); setErr(''); setLogged(false)
    try {
      const c = await api.post('/calls/human', { lead_id: leadId })
      setCall(c)
      poll(c.id)
    } catch (e) {
      setErr(e.message || 'The call could not be placed.')
      load()
    } finally { setPlacing(false) }
  }

  async function savePhone(e) {
    e.preventDefault()
    setSaving(true); setErr('')
    try { await api.put('/telephony/me/callback-phone', { phone }); setPhone(''); load() }
    catch (e2) { setErr(e2.message || 'That number could not be saved.') }
    finally { setSaving(false) }
  }

  async function verifyCode(e) {
    e.preventDefault()
    setSaving(true); setErr('')
    try { await api.post('/telephony/me/callback-phone/verify', { code }); setCode(''); load() }
    catch (e2) { setErr(e2.message || 'That code could not be verified.') }
    finally { setSaving(false) }
  }

  if (!leadId) return null
  if (!ready && !err) return <span className="tel-note">Checking call setup…</span>

  const failing = (ready?.checks || []).filter(c => !c.ok)
  const configMissing = failing.filter(c => CONFIG_KEYS.has(c.key))
  const needsPhone = failing.some(c => c.key === 'callback_phone')
  const compliance = failing.find(c => c.key === 'compliance')
  const active = call && !DONE.has(call.status)

  return (
    <div className="tel-call">
      <div className="tel-row">
        <button type="button" className="tel-btn tel-btn--primary" disabled={!ready?.ready || placing || active}
                onClick={place} title={ready?.ready ? `Your phone rings first; they see ${fmt(ready.from_number)}` : 'Not available'}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
               aria-hidden="true"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z"/></svg>
          {placing ? 'Calling…' : label}
        </button>
        {call && (
          <span className="tel-status" role="status">
            <span className={`tel-dot ${active ? 'tel-dot--wait' : call.status === 'failed' ? 'tel-dot--bad' : ''}`} />
            {active ? (LIVE[call.status] || call.status)
              : call.status === 'failed' ? `Not connected${call.outcome ? ` (${call.outcome.replace(/_/g, ' ')})` : ''}`
              : `Call ended${call.outcome ? ` · ${call.outcome.replace(/_/g, ' ')}` : ''}`}
          </span>
        )}
        {call && !logged && (
          <button type="button" className="tel-btn" onClick={() => setDispo(true)}>Log outcome</button>
        )}
      </div>

      {ready?.ready && !call && (
        <p className="tel-note">
          Your phone {fmt(ready.callback_phone)} rings first. When you answer, we dial the customer from
          {' '}{fmt(ready.from_number)}{ready.from_level && ready.from_level !== 'organization' ? ` (${ready.from_level} number)` : ''}.
        </p>
      )}
      {call && active && call.status === 'ringing_user' && (
        <p className="tel-note">Answer your phone {fmt(ready?.callback_phone)} to be connected.</p>
      )}

      {compliance && (
        <div className="tel-box tel-box--error"><strong>Calling is blocked</strong>{compliance.detail}</div>
      )}
      {configMissing.length > 0 && (
        <div className="tel-box">
          <strong>Provider/config required</strong>
          <ul>
            {configMissing.map(c => (
              <li key={c.key}>{c.label}: {c.detail}{c.fix && <span className="tel-fix">{c.fix}</span>}</li>
            ))}
          </ul>
        </div>
      )}
      {needsPhone && ready?.callback_pending && (
        <form className="tel-box tel-box--info" onSubmit={verifyCode}>
          <strong>Verify your callback phone</strong>
          <span className="tel-fix">We called {fmt(ready.callback_pending)} with a 6-digit code. Enter it here.
            The number is not used for calls until it is verified.</span>
          <div className="tel-row" style={{ marginTop: 6 }}>
            <input className="tel-input" inputMode="numeric" autoComplete="one-time-code" placeholder="123456"
                   value={code} aria-label="Verification code" maxLength={6}
                   onChange={e => setCode(e.target.value.replace(/\D/g, ''))} />
            <button type="submit" className="tel-btn" disabled={code.length < 6 || saving}>{saving ? 'Checking…' : 'Verify'}</button>
          </div>
        </form>
      )}
      {needsPhone && (
        <form className="tel-box tel-box--info" onSubmit={savePhone}>
          <strong>{ready?.callback_pending ? 'Use a different phone' : 'Verify your callback phone'}</strong>
          <span className="tel-fix">The phone we ring first when you click Call - your own cell or desk line.
            We call it once with a code to prove it is yours.</span>
          <div className="tel-row" style={{ marginTop: 6 }}>
            <input className="tel-input" inputMode="tel" placeholder="(214) 555-0100" value={phone}
                   aria-label="Your callback phone" onChange={e => setPhone(e.target.value)} />
            <button type="submit" className="tel-btn" disabled={!phone.trim() || saving}>{saving ? 'Sending…' : 'Send code'}</button>
          </div>
        </form>
      )}
      {err && <div className="tel-box tel-box--error">{err}</div>}

      {dispo && call && (
        <DispositionModal callId={call.id}
          initialOutcome={call.outcome === 'connected' ? 'connected' : call.outcome === 'no_answer' ? 'no_answer' : undefined}
          onClose={() => setDispo(false)}
          onSaved={() => { setLogged(true); onDone && onDone() }} />
      )}
    </div>
  )
}
