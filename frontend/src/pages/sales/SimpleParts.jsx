/**
 * Shared pieces for the simplified Sales Workspace screens (Lane F, #281):
 * icons, the Call and Text pop-ups, the "What happened?" outcome form and a
 * small notice. All writes go through EXISTING endpoints:
 *
 *   POST  /sales/opportunities/{id}/notes   — the deal's timeline
 *   PATCH /sales/opportunities/{id}         — next action, due date, stage
 *
 * CALLING AND TEXTING USE THE REP'S OWN PHONE (tel: / sms:), the same way the
 * existing calendar card does. Nothing on these screens calls a messaging
 * provider, so nothing can reach a customer except by the rep's own hand.
 */
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import {
  CALL_OUTCOMES, outcomeByKey, buildOutcomeRequests, telHref, smsHref, ymdPlusDays,
  firstName,
} from './simpleSales'

// ── icons (stroke icons drawn to match the approved prototype) ─────────────

const PATHS = {
  home: 'M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z',
  users: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
  phone: 'M22 16.92v3a2 2 0 0 1-2.18 2 19.8 19.8 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z',
  message: 'M7.9 20A9 9 0 1 0 4 16.1L2 22z',
  calendar: 'M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z',
  play: 'M6 3l14 9-14 9z',
  pipeline: 'M3 6h18M3 12h12M3 18h6M19 15v6M16 18h6',
  chart: 'M3 3v18h18M8 17V9M13 17V5M18 17v-4',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35',
  plus: 'M12 5v14M5 12h14',
  check: 'M20 6 9 17l-5-5',
  chevleft: 'M15 18l-6-6 6-6',
  chevright: 'M9 18l6-6-6-6',
  mail: 'M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM22 6l-10 7L2 6',
  pin: 'M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0zM12 13a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  globe: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM2 12h20M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  team: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1',
  x: 'M18 6 6 18M6 6l12 12',
  arrow: 'M5 12h14M13 6l6 6-6 6',
  folder: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
}

export function Icon({ name, size }) {
  return (
    <svg viewBox="0 0 24 24" width={size || 18} height={size || 18} fill="none"
         stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
         strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d={PATHS[name] || PATHS.more} />
    </svg>
  )
}

export function initialsOf(name) {
  if (!name) return '?'
  return name.trim().split(/\s+/).slice(0, 2).map(p => p[0]).join('').toUpperCase()
}

/** Overdue / Today / Tomorrow / a date — the label rows and the header use. */
export function DueStatus({ bucket, iso }) {
  if (bucket === 'overdue') return <span className="ss-status ss-red">Overdue</span>
  if (bucket === 'today') return <span className="ss-status ss-amber">Today</span>
  if (bucket === 'later' && iso) {
    const d = new Date(String(iso).slice(0, 10) + 'T12:00:00')
    return <span className="ss-status ss-sblue">
      {isNaN(d) ? 'Upcoming' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
    </span>
  }
  return <span className="ss-status">No date</span>
}

export function ErrorLine({ error, onRetry }) {
  if (!error) return null
  return (
    <div className="ss-err" role="alert">
      <span>{String(error)}</span>
      {onRetry && <button type="button" className="ss-btn ss-white ss-xs" onClick={onRetry}>Try again</button>}
    </div>
  )
}

export function Skeleton({ rows = 4 }) {
  return (
    <div aria-busy="true" aria-label="Loading" style={{ padding: '6px 20px' }}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="ss-skel" style={{ width: (90 - i * 12) + '%' }} />
      ))}
    </div>
  )
}

// ── notice ─────────────────────────────────────────────────────────────────

export function useNotice() {
  const [msg, setMsg] = useState(null)
  useEffect(() => {
    if (!msg) return undefined
    const t = setTimeout(() => setMsg(null), 4200)
    return () => clearTimeout(t)
  }, [msg])
  const node = msg ? <div className="ss-toast" role="status" aria-live="polite">{msg}</div> : null
  return [node, setMsg]
}

// ── modal frame ────────────────────────────────────────────────────────────

export function Modal({ title, sub, onClose, children, footer, labelId }) {
  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const id = labelId || 'ss-modal-title'
  return (
    <div className="ss-modalback" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <section className="ss-modal" role="dialog" aria-modal="true" aria-labelledby={id}>
        <div className="ss-modalhead">
          <div>
            <h2 id={id}>{title}</h2>
            {sub && <p>{sub}</p>}
          </div>
          <button type="button" className="ss-iconbtn" aria-label="Close" onClick={onClose}>
            <Icon name="x" />
          </button>
        </div>
        <div className="ss-modalbody">{children}</div>
        {footer && <div className="ss-modalfoot">{footer}</div>}
      </section>
    </div>
  )
}

// ── "What happened?" ───────────────────────────────────────────────────────

/**
 * The single post-call panel. Opened by the Call button (after the phone is
 * handed the number) and by "Log Call Outcome". Saves one timeline entry and,
 * when anything about the deal changes, one deal update.
 *
 * onSaved({ outcome, opp }) — `opp` is the deal as the server returned it.
 */
export function CallOutcomeModal({ opp, onClose, onSaved, onBook }) {
  const [outcome, setOutcome] = useState('')
  const [note, setNote] = useState('')
  const [nextAction, setNextAction] = useState(opp?.next_action || '')
  const [due, setDue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  function pick(key) {
    setOutcome(key)
    const o = outcomeByKey(key)
    // Suggest the obvious next step; the rep can change it before saving.
    if (o) { setNextAction(o.next); setDue(ymdPlusDays(o.days)) }
  }

  async function save() {
    setBusy(true); setError(null)
    try {
      const req = buildOutcomeRequests({ stage: opp.stage, outcome, note, nextAction, dueYmd: due })
      await api.post('/sales/opportunities/' + opp.id + '/notes', req.note)
      let updated = opp
      if (req.patch) updated = await api.patch('/sales/opportunities/' + opp.id, req.patch)
      onSaved && onSaved({ outcome, opp: updated || opp })
      if (outcome === 'book_meeting' && onBook) onBook(opp)
    } catch (e) {
      setError(e.message || 'Could not save the call result.')
    } finally {
      setBusy(false)
    }
  }

  const tel = telHref(opp?.phone)
  return (
    <Modal
      title={'Call ' + (opp?.contact_name || opp?.company_name || '')}
      sub="Your phone places the call. Then tell us what happened."
      onClose={onClose}
      footer={<>
        <button type="button" className="ss-btn ss-white" onClick={onClose}>Cancel</button>
        <button type="button" className="ss-btn ss-green" disabled={!outcome || busy} onClick={save}>
          <Icon name="check" /> {busy ? 'Saving…' : 'Save Call Result'}
        </button>
      </>}
    >
      <div className="ss-callout">
        <strong>{opp?.contact_name || opp?.company_name}</strong>
        {opp?.company_name && opp?.contact_name ? ' · ' + opp.company_name : ''}<br />
        {tel
          ? <>Phone: <a href={tel}>{opp.phone}</a> — tap to dial again.</>
          : 'No phone number on this record. Add one on Lead Details.'}
      </div>
      <div style={{ height: 16 }} />
      <ErrorLine error={error} />
      <div className="ss-field" role="group" aria-labelledby="ss-what">
        <span className="ss-label" id="ss-what">What happened?</span>
        <div className="ss-outcomes">
          {CALL_OUTCOMES.map(o => (
            <button key={o.key} type="button" className="ss-outcome"
                    aria-pressed={outcome === o.key} onClick={() => pick(o.key)}>
              {o.label}
            </button>
          ))}
        </div>
      </div>
      <div className="ss-field">
        <label htmlFor="ss-call-note">Quick note (optional)</label>
        <textarea id="ss-call-note" value={note} maxLength={2000}
                  placeholder="Add an important detail…" onChange={e => setNote(e.target.value)} />
      </div>
      <div className="ss-twobox">
        <div className="ss-field">
          <label htmlFor="ss-next">Next action</label>
          <input id="ss-next" value={nextAction} maxLength={200}
                 onChange={e => setNextAction(e.target.value)} />
        </div>
        <div className="ss-field">
          <label htmlFor="ss-due">When</label>
          <input id="ss-due" type="date" value={due} onChange={e => setDue(e.target.value)} />
        </div>
      </div>
      {outcome === 'book_meeting' && (
        <p className="ss-count-note" style={{ margin: 0 }}>After saving, the demo form opens with this contact picked.</p>
      )}
    </Modal>
  )
}

// ── Text ───────────────────────────────────────────────────────────────────

/**
 * Opens the rep's own messaging app with the number and a starting message.
 * Logging is a separate, explicit click — opening the app is not proof that a
 * text was sent, so the timeline only records it when the rep says so.
 */
export function TextModal({ opp, me, onClose, onLogged }) {
  const name = firstName(opp?.contact_name)
  const [msg, setMsg] = useState(
    'Hi' + (name ? ' ' + name : '') + ', this is ' + (firstName(me) || 'your sales contact') +
    ' — just checking in. Is now a good time to connect?')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const sms = smsHref(opp?.phone)
  const href = sms ? sms + '?&body=' + encodeURIComponent(msg) : null

  async function logIt() {
    setBusy(true); setError(null)
    try {
      await api.post('/sales/opportunities/' + opp.id + '/notes', {
        event_type: 'text_logged',
        summary: ('Text sent — ' + msg).slice(0, 250),
        detail: msg.length > 238 ? msg : null,
      })
      onLogged && onLogged()
    } catch (e) {
      setError(e.message || 'Could not record the text.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={'Text ' + (opp?.contact_name || opp?.company_name || '')}
      sub="Opens your phone's messages app. Nothing is sent from here."
      onClose={onClose}
      footer={<>
        <button type="button" className="ss-btn ss-white" onClick={onClose}>Close</button>
        <button type="button" className="ss-btn ss-soft" disabled={busy} onClick={logIt}>
          <Icon name="check" /> {busy ? 'Saving…' : 'I sent it — add to history'}
        </button>
        {href
          ? <a className="ss-btn ss-blue" href={href}><Icon name="message" /> Open Messages</a>
          : <button type="button" className="ss-btn ss-blue" disabled>No phone number</button>}
      </>}
    >
      <ErrorLine error={error} />
      <div className="ss-callout">To: {opp?.phone || 'no phone number on this record'}</div>
      <div style={{ height: 16 }} />
      <div className="ss-field" style={{ marginBottom: 0 }}>
        <label htmlFor="ss-text-msg">Message</label>
        <textarea id="ss-text-msg" value={msg} maxLength={600} onChange={e => setMsg(e.target.value)} />
      </div>
    </Modal>
  )
}

/**
 * Call and Text as the two square buttons the list rows use, or as full
 * buttons in a header. Handles both pop-ups itself.
 */
export function useContactActions({ me, onChanged, onBook, notify }) {
  const [calling, setCalling] = useState(null)
  const [texting, setTexting] = useState(null)

  function call(opp) {
    // Hand the number to the phone first, then ask what happened. Calling
    // from a laptop with no phone app simply shows the panel.
    const tel = telHref(opp.phone)
    if (tel) { try { window.location.href = tel } catch { /* no dialer */ } }
    setCalling(opp)
  }

  const modals = <>
    {calling && (
      <CallOutcomeModal
        opp={calling}
        onClose={() => setCalling(null)}
        onBook={onBook}
        onSaved={({ outcome, opp }) => {
          setCalling(null)
          notify && notify('Call result saved to ' + (opp.contact_name || opp.company_name) +
            ' — ' + (outcomeByKey(outcome)?.label || '') + '.')
          onChanged && onChanged(opp)
        }}
      />
    )}
    {texting && (
      <TextModal
        opp={texting} me={me}
        onClose={() => setTexting(null)}
        onLogged={() => {
          const o = texting
          setTexting(null)
          notify && notify('Text added to ' + (o.contact_name || o.company_name) + "'s history.")
          onChanged && onChanged(o)
        }}
      />
    )}
  </>
  return { call, text: setTexting, modals }
}

export function CallTextSquares({ opp, onCall, onText }) {
  const has = !!telHref(opp.phone)
  const who = opp.contact_name || opp.company_name || 'contact'
  return <>
    <button type="button" className="ss-square ss-green" aria-label={'Call ' + who}
            aria-disabled={!has} title={has ? 'Call ' + opp.phone : 'No phone number'}
            onClick={() => has && onCall(opp)}>
      <Icon name="phone" />
    </button>
    <button type="button" className="ss-square ss-blue" aria-label={'Text ' + who}
            aria-disabled={!has} title={has ? 'Text ' + opp.phone : 'No phone number'}
            onClick={() => has && onText(opp)}>
      <Icon name="message" />
    </button>
  </>
}
