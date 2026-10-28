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
import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import { useSalesBrand } from './salesBrand'
import {
  CALL_OUTCOMES, NOT_INTERESTED_CHOICES, outcomeByKey, buildOutcomeRequests, telHref, smsHref,
  ymdPlusDays, firstName, cleanPhone,
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
  grid: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
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
  const box = useRef(null)
  // ON A PHONE the dialog is sized to the VISIBLE viewport, which shrinks when
  // the on-screen keyboard opens — so the header and Save/Cancel stay on
  // screen while only the middle scrolls. (100vh does not shrink for the
  // keyboard; visualViewport does.)
  const [vh, setVh] = useState(null)
  useEffect(() => {
    const vv = window.visualViewport
    if (!vv) return undefined
    const on = () => setVh(Math.round(vv.height))
    on(); vv.addEventListener('resize', on)
    return () => vv.removeEventListener('resize', on)
  }, [])
  // Keyboard users land INSIDE the dialog when it opens, Tab stays inside it,
  // Escape closes it, and focus goes back to whatever opened it.
  useEffect(() => {
    const opener = document.activeElement
    const focusables = () => [...(box.current?.querySelectorAll(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])') || [])]
    const first = box.current?.querySelector('[data-autofocus]') || focusables().find(el => !el.closest('.ss-modalhead'))
    ;(first || box.current)?.focus()
    const onKey = e => {
      if (e.key === 'Escape') { onClose(); return }
      if (e.key !== 'Tab') return
      const f = focusables(); if (!f.length) return
      const a = f[0], z = f[f.length - 1]
      if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus() }
      else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus() }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      if (opener && typeof opener.focus === 'function' && document.contains(opener)) opener.focus()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const id = labelId || 'ss-modal-title'
  return (
    <div className="ss-modalback" style={vh ? { '--ss-vvh': vh + 'px' } : undefined}
         onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <section className="ss-modal" role="dialog" aria-modal="true" aria-labelledby={id} ref={box} tabIndex={-1}>
        <div className="ss-modalhead">
          <div>
            <h2 id={id}>{title}</h2>
            {sub && <p>{sub}</p>}
          </div>
          <button type="button" className="ss-iconbtn" aria-label="Close" onClick={onClose}>
            <Icon name="x" />
          </button>
        </div>
        <div className="ss-modalbody" tabIndex={-1}>{children}</div>
        {footer && <div className="ss-modalfoot">{footer}</div>}
      </section>
    </div>
  )
}

// ── phone number with copy (the desktop fallback) ─────────────────────────

/** The number, a tap-to-dial link, and Copy — for a laptop with no dialer. */
export function PhoneFallback({ phone, verb }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try { await navigator.clipboard.writeText(cleanPhone(phone) || phone); setCopied(true) }
    catch { setCopied(false) }
  }
  return (
    <div className="ss-phoneline">
      <a href={(verb === 'text' ? smsHref : telHref)(phone) || undefined}>{phone}</a>
      <button type="button" className="ss-btn ss-white ss-xs" onClick={copy}>{copied ? 'Copied' : 'Copy number'}</button>
      <small>{verb === 'text' ? 'Messages didn’t open? Text this number from your phone.'
        : 'Nothing dialed? Call this number from your phone.'}</small>
    </div>
  )
}

// ── "What happened?" ───────────────────────────────────────────────────────

/**
 * The single post-call panel, opened after Call hands the number to the
 * device's dialer (or from "Log Call Outcome"). NOTHING is recorded by opening
 * it — opening a dialer proves nothing. One timeline entry and, when the deal
 * changes, one deal update are written only when the rep saves.
 *
 * Not Interested asks one explicit question: Close as Lost (no future
 * follow-up) or Follow up later (rep picks the action and the date).
 *
 * onSaved({ outcome, decision, opp }) — `opp` is the deal as the server returned it.
 * onBook(opp) — after saving Book a Meeting / "Save & book a demo": opens the
 *   real booking form. No booking is assumed; only the scheduler creates one.
 */
export function CallOutcomeModal({ opp, tz, onClose, onSaved, onBook }) {
  const [outcome, setOutcome] = useState('')
  const [decision, setDecision] = useState('')
  const [note, setNote] = useState('')
  const [nextAction, setNextAction] = useState(opp?.next_action || '')
  const [due, setDue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const saving = useRef(false)
  const errBox = useRef(null)
  // The body scrolls on a phone; bring a save error into view so it is never
  // hidden below the fold while the rep looks at the (fixed) Save button.
  useEffect(() => {
    if (error && errBox.current) errBox.current.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [error])

  const o = outcomeByKey(outcome)
  const showNext = o && (!o.decide || decision === 'later')

  function pick(key) {
    setOutcome(key); setDecision(''); setError(null)
    const x = outcomeByKey(key)
    // Suggest the obvious next step; the rep can change or clear it. Not
    // Interested suggests nothing — a later follow-up must be chosen.
    if (x && !x.decide) { setNextAction(x.next || ''); setDue(x.days == null ? '' : ymdPlusDays(x.days, new Date(), tz)) }
    else { setNextAction(''); setDue('') }
  }

  async function save(thenBook) {
    if (saving.current) return            // no double submit
    let req
    try {
      req = buildOutcomeRequests({ stage: opp.stage, outcome, note, nextAction, dueYmd: due, decision })
    } catch (e) { setError(e.message); return }
    saving.current = true; setBusy(true); setError(null)
    try {
      await api.post('/sales/opportunities/' + opp.id + '/notes', req.note)
      let updated = opp
      if (req.patch) updated = await api.patch('/sales/opportunities/' + opp.id, req.patch)
      if (o?.decide && decision === 'dnc') {
        // The real record — the server enforces it from here on.
        await api.post('/sales/opportunities/' + opp.id + '/do-not-contact',
                       { reason: note.trim() || 'Prospect asked not to be contacted (on a call).' })
        updated = { ...(updated || opp), do_not_contact: true }
      }
      onSaved && onSaved({ outcome, decision, opp: updated || opp })
      if ((thenBook || o?.books) && onBook) onBook(opp)
    } catch (e) {
      setError(e.message || 'Could not save the call result.')
    } finally {
      saving.current = false; setBusy(false)
    }
  }

  const primaryLabel = !o ? 'Save Call Result'
    : o.decide && decision === 'lost' ? 'Close as Lost'
    : o.decide && decision === 'later' ? 'Save follow-up'
    : o.decide && decision === 'dnc' ? 'Record Do Not Contact'
    : o.books ? 'Save & open booking'
    : 'Save Call Result'
  const canSave = !!o && (!o.decide || !!decision) && !busy

  return (
    <Modal
      title={'Call ' + (opp?.contact_name || opp?.company_name || '')}
      sub="Your phone’s dialer opens the call. Nothing is recorded until you save what happened."
      onClose={onClose}
      footer={<>
        <button type="button" className="ss-btn ss-white" onClick={onClose}>Cancel</button>
        {o?.key === 'interested' && onBook && (
          <button type="button" className="ss-btn ss-blue" disabled={busy} onClick={() => save(true)}>
            Save &amp; book a demo
          </button>
        )}
        <button type="button" className={'ss-btn ss-primaryact ' + (o?.decide && decision === 'lost' ? 'ss-soft' : o?.decide && decision === 'dnc' ? 'ss-danger' : 'ss-green')}
                disabled={!canSave} onClick={() => save(false)}>
          <Icon name="check" /> {busy ? 'Saving…' : primaryLabel}
        </button>
      </>}
    >
      <div className="ss-callout">
        <strong>{opp?.contact_name || opp?.company_name}</strong>
        {opp?.company_name && opp?.contact_name ? ' · ' + opp.company_name : ''}
        {opp?.phone ? <PhoneFallback phone={opp.phone} verb="call" /> : <div>No phone number on this record.</div>}
      </div>
      <div style={{ height: 16 }} />
      <div ref={errBox}><ErrorLine error={error} /></div>
      <div className="ss-field" role="group" aria-labelledby="ss-what">
        <span className="ss-label" id="ss-what">What happened?</span>
        <div className="ss-outcomes">
          {CALL_OUTCOMES.map((x, i) => (
            <button key={x.key} type="button" className="ss-outcome" data-autofocus={i === 0 ? true : undefined}
                    aria-pressed={outcome === x.key} onClick={() => pick(x.key)}>
              {x.label}
            </button>
          ))}
        </div>
      </div>

      {o?.decide && (
        <div className="ss-field" role="radiogroup" aria-labelledby="ss-ni">
          <span className="ss-label" id="ss-ni">What should happen with this lead?</span>
          <div className="ss-decide">
            {NOT_INTERESTED_CHOICES.map(c => (
              <button key={c.key} type="button" role="radio" aria-checked={decision === c.key}
                      className="ss-decision" onClick={() => { setDecision(c.key); setError(null) }}>
                <strong>{c.label}</strong>
                <small>{c.hint}</small>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="ss-field">
        <label htmlFor="ss-call-note">Quick note (optional)</label>
        <textarea id="ss-call-note" value={note} maxLength={2000}
                  placeholder="Add an important detail…" onChange={e => setNote(e.target.value)} />
      </div>

      {showNext && (
        <div className="ss-twobox">
          <div className="ss-field">
            <label htmlFor="ss-next">Next action{o.decide ? ' *' : ''}</label>
            <input id="ss-next" value={nextAction} maxLength={200} required={!!o.decide}
                   placeholder={o.decide ? 'e.g. Check back after budget season' : ''}
                   onChange={e => setNextAction(e.target.value)} />
          </div>
          <div className="ss-field">
            <label htmlFor="ss-due">When{o.decide ? ' *' : ' (optional)'}</label>
            <input id="ss-due" type="date" value={due} required={!!o.decide}
                   min={ymdPlusDays(0, new Date(), tz)} onChange={e => setDue(e.target.value)} />
          </div>
        </div>
      )}
      {o?.books && (
        <p className="ss-count-note" style={{ margin: 0 }}>
          This records that they agreed to meet. The demo form opens next with this contact picked;
          a meeting exists only once you book it there.
        </p>
      )}
    </Modal>
  )
}

// ── Text ───────────────────────────────────────────────────────────────────

/**
 * Opens the rep's own messaging app with the number and a starting message.
 * Three different things, kept distinct:
 *   · Messages app opened  — shown as a note; nothing is recorded.
 *   · Rep confirms sent    — "I sent it — add to history" writes the timeline.
 *   · Delivered            — not knowable: the rep's own phone sends it, so
 *                            EvoSys never claims delivery.
 */
export function TextModal({ opp, me, onClose, onLogged }) {
  const brand = useSalesBrand()
  const name = firstName(opp?.contact_name)
  const [msg, setMsg] = useState(
    'Hi' + (name ? ' ' + name : '') + ', this is ' + (firstName(me) || 'your sales contact') +
    ' — just checking in. Is now a good time to connect?')
  const [opened, setOpened] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const saving = useRef(false)
  const sms = smsHref(opp?.phone)
  const href = sms ? sms + '?&body=' + encodeURIComponent(msg) : null

  async function logIt() {
    if (saving.current) return
    saving.current = true; setBusy(true); setError(null)
    try {
      const full = 'Text sent (confirmed by rep) — ' + msg
      await api.post('/sales/opportunities/' + opp.id + '/notes', {
        event_type: 'text_logged',
        summary: full.slice(0, 250),
        detail: full.length > 250 ? msg : null,
      })
      onLogged && onLogged()
    } catch (e) {
      setError(e.message || 'Could not record the text.')
    } finally {
      saving.current = false; setBusy(false)
    }
  }

  return (
    <Modal
      title={'Text ' + (opp?.contact_name || opp?.company_name || '')}
      sub={'Opens your phone’s messages app. ' + brand.short + ' doesn’t send it and can’t see delivery.'}
      onClose={onClose}
      footer={<>
        <button type="button" className="ss-btn ss-white" onClick={onClose}>Close</button>
        <button type="button" className="ss-btn ss-soft" disabled={busy} onClick={logIt}>
          <Icon name="check" /> {busy ? 'Saving…' : 'I sent it — add to history'}
        </button>
        {href
          ? <a className="ss-btn ss-blue" href={href} onClick={() => setOpened(true)}><Icon name="message" /> Open Messages</a>
          : <button type="button" className="ss-btn ss-blue" disabled>No phone number</button>}
      </>}
    >
      <ErrorLine error={error} />
      <div className="ss-callout">
        To:{opp?.phone ? <PhoneFallback phone={opp.phone} verb="text" /> : ' no phone number on this record'}
      </div>
      {opened && (
        <p className="ss-count-note" role="status" style={{ margin: '10px 0 0' }}>
          Messages app opened — nothing has been sent or recorded yet. Click “I sent it” once you’ve sent it.
        </p>
      )}
      <div style={{ height: 16 }} />
      <div className="ss-field" style={{ marginBottom: 0 }}>
        <label htmlFor="ss-text-msg">Message</label>
        <textarea id="ss-text-msg" value={msg} maxLength={600} data-autofocus onChange={e => setMsg(e.target.value)} />
      </div>
    </Modal>
  )
}

/**
 * Call and Text for a lead, with both pop-ups. Call first hands the number to
 * the device dialer (tel:), then opens "What happened?" — the click itself
 * records nothing.
 */
export function useContactActions({ me, tz, onChanged, onBook, notify }) {
  const [calling, setCalling] = useState(null)
  const [texting, setTexting] = useState(null)

  function call(opp) {
    const tel = telHref(opp.phone)
    if (!tel || opp.do_not_contact) return
    try { window.location.href = tel } catch { /* no dialer on this device */ }
    setCalling(opp)
  }
  function text(opp) {
    if (smsHref(opp.phone) && !opp.do_not_contact) setTexting(opp)
  }

  const modals = <>
    {calling && (
      <CallOutcomeModal
        opp={calling} tz={tz}
        onClose={() => setCalling(null)}
        onBook={onBook}
        onSaved={({ outcome, decision, opp }) => {
          setCalling(null)
          const label = outcome === 'not_interested'
            ? (decision === 'lost' ? 'closed as Lost' : decision === 'dnc' ? 'Do Not Contact recorded' : 'follow-up scheduled')
            : (outcomeByKey(outcome)?.label || '')
          notify && notify('Saved for ' + (opp.contact_name || opp.company_name) + ' — ' + label + '.')
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
  return { call, text, modals }
}

/** The two square buttons on a list row — only when there is a usable number. */
export function CallTextSquares({ opp, onCall, onText }) {
  const who = opp.contact_name || opp.company_name || 'contact'
  if (opp.do_not_contact) return <DncChip />
  if (!telHref(opp.phone)) return <span className="ss-nophone">No phone</span>
  return <>
    <button type="button" className="ss-square ss-green" aria-label={'Call ' + who}
            title={'Call ' + opp.phone} onClick={() => onCall(opp)}>
      <Icon name="phone" />
    </button>
    <button type="button" className="ss-square ss-blue" aria-label={'Text ' + who}
            title={'Text ' + opp.phone} onClick={() => onText(opp)}>
      <Icon name="message" />
    </button>
  </>
}

/** The one visual for a prospect on Do Not Contact. */
export function DncChip() {
  return <span className="ss-status ss-red ss-dnc" title="This prospect asked not to be contacted">Do not contact</span>
}
