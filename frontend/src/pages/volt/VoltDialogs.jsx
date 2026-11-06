/* VOLT public dialogs (#535): an accessible modal, the Apply Now project picker
 * and Get Future Opportunities.
 *
 * Modal: role="dialog" + aria-modal, labelled by its heading, focus moves into
 * it and is kept there (Tab / Shift+Tab wrap), Escape and the backdrop close it,
 * and focus returns to the control that opened it. The page behind does not
 * scroll while it is open.
 */
import { useEffect, useRef, useState } from 'react'
import { volt } from './voltApi'

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function Modal({ title, onClose, children, labelId = 'v-modal-title', wide = false }) {
  const box = useRef(null)
  const opener = useRef(typeof document !== 'undefined' ? document.activeElement : null)
  useEffect(() => {
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const first = box.current?.querySelector('[data-autofocus]') || box.current?.querySelector(FOCUSABLE)
    first?.focus()
    function key(e) {
      if (e.key === 'Escape') { e.preventDefault(); onClose() }
      if (e.key === 'Tab' && box.current) {
        const els = [...box.current.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null)
        if (!els.length) return
        const a = els[0]; const z = els[els.length - 1]
        if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus() }
        else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus() }
      }
    }
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('keydown', key)
      document.body.style.overflow = prev
      try { opener.current?.focus?.() } catch { /* opener gone */ }
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="v-modal-bg" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className={`v-modal${wide ? ' v-modal--wide' : ''}`} role="dialog" aria-modal="true" aria-labelledby={labelId} ref={box}>
        <div className="v-row" style={{ marginBottom: 10 }}>
          <h2 id={labelId} style={{ fontSize: 24, letterSpacing: '-.03em' }}>{title}</h2>
          <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={onClose} aria-label="Close">Close ✕</button>
        </div>
        {children}
      </div>
    </div>
  )
}

/** Apply Now: pick one of the open projects, then that project's application begins. */
export function ApplyDialog({ projects, onPick, onClose }) {
  return (
    <Modal title="Apply Now" onClose={onClose} labelId="v-apply-title">
      <p className="v-muted" style={{ marginBottom: 12 }}>Choose the project you want to apply for.</p>
      <div className="v-pick" role="list">
        {projects.map((p, i) => (
          <button key={p.key} type="button" role="listitem" className="v-pick-item" data-pick={p.key}
                  data-autofocus={i === 0 ? '' : undefined} onClick={() => onPick(p.key)}>
            <span className="v-icon" aria-hidden="true">{p.icon}</span>
            <span style={{ flex: 1 }}><b>{p.name}</b><br /><span className="v-muted v-small">{p.summary}</span></span>
            <span aria-hidden="true">→</span>
          </button>
        ))}
      </div>
    </Modal>
  )
}

/** Get Future Opportunities: saves interest only. */
export function TalentDialog({ projects, invite, testMode, onClose, onBrowse, ReferralPicker }) {
  const [v, setV] = useState({ first_name: '', last_name: '', email: '' })
  const [interests, setInterests] = useState([])
  const [notify, setNotify] = useState(false)
  const [referrer, setReferrer] = useState(null)
  const [refText, setRefText] = useState('')
  const [errs, setErrs] = useState({})
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(null)
  const toggle = (k) => setInterests((x) => (x.includes(k) ? x.filter((y) => y !== k) : [...x, k]))
  async function submit(e) {
    e.preventDefault()
    setErr(''); setErrs({})
    if (refText.trim() && !referrer) { setErrs({ referrer: 'Select a matching recruiter from the list, or clear the field.' }); return }
    setBusy(true)
    try {
      const r = await volt.talentSignup({ ...v, interests, notify, source: 'portal',
        ...(referrer ? { referrer_rep_id: referrer.rep_id, referral_source: referrer.source } : {}) })
      setDone(r.message)
    } catch (x) { setErrs(x.detail?.fields || {}); setErr(x.message) } finally { setBusy(false) }
  }
  const f = (name, label, props = {}) => (
    <label htmlFor={`v-tp-${name}`}>{label}
      <input id={`v-tp-${name}`} value={v[name]} aria-invalid={errs[name] ? 'true' : undefined}
             onChange={(e) => setV({ ...v, [name]: e.target.value })} {...props} />
      {errs[name] && <span className="v-ferr">{errs[name]}</span>}
    </label>
  )
  return (
    <Modal title="Get Future Opportunities" onClose={onClose} labelId="v-talent-title">
      {done ? (
        <div data-talent-done>
          <div className="v-alert v-alert--ok" role="status">{done}</div>
          <p style={{ margin: '12px 0' }}>When you are ready to apply, choose a current opportunity. Applying creates your account and your full application.</p>
          <div className="v-row" style={{ justifyContent: 'flex-start' }}>
            <button type="button" className="v-btn v-btn--red" data-autofocus onClick={onBrowse}>See current opportunities</button>
            <button type="button" className="v-btn v-btn--line" onClick={onClose}>Close</button>
          </div>
        </div>
      ) : (
        <form className="v-form" onSubmit={submit} noValidate data-form="talent">
          <p className="v-muted v-small">This saves your interest only. You will create your account and complete a full application when you choose to apply.</p>
          <div className="v-split">
            {f('first_name', 'First Name', { autoComplete: 'given-name', maxLength: 70, required: true, 'data-autofocus': '' })}
            {f('last_name', 'Last Name', { autoComplete: 'family-name', maxLength: 70, required: true })}
          </div>
          {f('email', 'Email', { type: 'email', autoComplete: 'email', inputMode: 'email', required: true })}
          <fieldset className="v-fieldset">
            <legend>Interested in <span className="v-muted">(optional)</span></legend>
            {projects.map((p) => (
              <label key={p.key} className="v-check"><input type="checkbox" checked={interests.includes(p.key)} onChange={() => toggle(p.key)} /> {p.name}</label>
            ))}
            <label className="v-check"><input type="checkbox" checked={interests.includes('any')} onChange={() => toggle('any')} /> Any future opportunity</label>
          </fieldset>
          <ReferralPicker value={referrer} onChange={(r) => { setReferrer(r); setErrs((x) => { const n = { ...x }; delete n.referrer; return n }) }}
                          onTextChange={setRefText} signedIn={false} testMode={testMode || /@example\.test$/i.test(v.email)}
                          invite={invite} error={errs.referrer} />
          <label className="v-check v-consent"><input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} data-consent />
            Email me about future VOLT Marketing opportunities.</label>
          {err && <div className="v-alert v-alert--err" role="alert">{Object.keys(errs).length ? 'Please fix the highlighted fields.' : err}</div>}
          <button className="v-btn v-btn--red" type="submit" disabled={busy}>{busy ? 'Saving…' : 'Save my interest'}</button>
        </form>
      )}
    </Modal>
  )
}
