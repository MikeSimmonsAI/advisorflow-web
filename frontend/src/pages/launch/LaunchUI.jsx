/**
 * LaunchUI — the small shared pieces every Launch Pad screen is built from.
 *
 * Eight intake steps that each hand-rolled their own label/input markup would
 * drift apart by the third one. These are the vocabulary: a Field, a group of
 * them, a note, a collapsible area, an upload tile, a brand mark. They carry
 * no state and no data — the step decides what to render, this decides what it
 * looks like.
 *
 * STAGE 1: every control here is a controlled input over local React state.
 * Nothing persists, nothing uploads, nothing is sent anywhere.
 */
import { useRef, useState } from 'react'
import { ICONS } from './launchConfig'

/** One icon, from the single path table. `d` may be a key or a raw path. */
export function Ico({ name, size = 16, stroke = false }) {
  const d = ICONS[name] || name
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true"
      fill={stroke ? 'none' : 'currentColor'}
      stroke={stroke ? 'currentColor' : 'none'}
      strokeWidth={stroke ? 2 : 0} strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  )
}

/**
 * The brand / customer mark.
 *
 * NO GENERATED LOGO. If `logoUrl` is absent this renders initials on a
 * gold-hairline plate — a placeholder that is obviously a placeholder. An
 * invented Atlantis logo baked into the bundle would be mistaken for the real
 * one and then have to be found and removed later. When the real asset
 * arrives, set logoUrl and nothing around this moves.
 */
export function Mark({ src, label, size = 'm' }) {
  const initials = (label || '?')
    .split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase()
  return (
    <div className={'lp-mark ' + size} aria-hidden="true">
      {src ? <img src={src} alt="" /> : initials}
    </div>
  )
}

/** A titled band of fields inside the document. */
export function Group({ title, sub, children }) {
  return (
    <section className="lp-group">
      {title ? <h3>{title}</h3> : null}
      {sub ? <p className="lp-gsub">{sub}</p> : null}
      <div className="lp-fields">{children}</div>
    </section>
  )
}

/** A bare 12-column row, for steps that supply their own headings. */
export function Fields({ children }) {
  return <div className="lp-fields">{children}</div>
}

/**
 * One labelled control.
 *
 * `span` is the 12-column width: 3, 4, 6 (default), 8 or 12. Below 720px every
 * field goes full width — see the media query in LaunchStyles.
 */
export function Field({ label, span, required, optional, hint, children }) {
  const cls = 'lp-f' + (span && span !== 6 ? ' c' + span : '')
  return (
    <div className={cls}>
      <label>
        {label}
        {required ? <span className="lp-req">*</span> : null}
        {optional ? <span className="lp-opt">optional</span> : null}
      </label>
      {children}
      {hint ? <p className="lp-hint">{hint}</p> : null}
    </div>
  )
}

/** Text / email / tel / date / password input bound to the step's state. */
export function Text({ value, onChange, ...rest }) {
  return <input className="lp-in" value={value ?? ''}
    onChange={e => onChange && onChange(e.target.value)} {...rest} />
}

export function Area({ value, onChange, rows, ...rest }) {
  return <textarea className="lp-in" rows={rows} value={value ?? ''}
    onChange={e => onChange && onChange(e.target.value)} {...rest} />
}

export function Select({ value, onChange, options, ...rest }) {
  return (
    <select className="lp-in" value={value ?? ''}
      onChange={e => onChange && onChange(e.target.value)} {...rest}>
      {options.map(o => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  )
}

/** A callout. `tone` is 'info' (default), 'secure' or 'warn'. */
export function Note({ tone = 'info', icon = 'info', title, children }) {
  return (
    <div className={'lp-note ' + tone}>
      <span className="lp-nicon"><Ico name={icon} size={17} /></span>
      <div className="lp-nb">
        {title ? <b>{title}</b> : null}
        <p>{children}</p>
      </div>
    </div>
  )
}

/** A collapsed-by-default area — the overflow the form should not lead with. */
export function Collapse({ title, meta, children, open: initial = false }) {
  const [open, setOpen] = useState(initial)
  return (
    <div className={'lp-collapse' + (open ? ' open' : '')}>
      <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}>
        <span className="lp-caret"><Ico name="caret" size={13} stroke /></span>
        <b>{title}</b>
        {meta ? <span className="lp-cmeta">{meta}</span> : null}
      </button>
      {open ? <div className="lp-collapse-b">{children}</div> : null}
    </div>
  )
}

/**
 * An upload tile — a real one.
 *
 * Clicking opens a file picker and the chosen file is stored against the
 * customer's implementation, org-isolated, on the server. The tile reports
 * what is actually held rather than what was clicked.
 */
export function Upload({ title, note, tag = 'Not provided', icon = 'upload',
                         files, onUpload, onRemove }) {
  // REAL UPLOAD, SAME TILE. The prototype's version was a button that toggled
  // its own boolean — it looked exactly like this and stored nothing. The
  // classes and the markup are unchanged so the approved design is untouched;
  // what changed is that `filled` is now the presence of an actual stored file
  // rather than a local `useState(false)` a click flipped.
  //
  // A tile is matched to its uploads by its own title, sent as the file's
  // label, so the slot a document was dropped into survives a refresh.
  const mine = (files || []).filter(f => f.label === title)
  const filled = mine.length > 0
  const inputRef = useRef(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  const pick = async e => {
    const file = e.target.files && e.target.files[0]
    // Clear immediately so choosing the same file twice still fires onChange.
    e.target.value = ''
    if (!file || !onUpload) return
    setBusy(true); setErr(null)
    try { await onUpload(file, title) }
    catch (ex) { setErr(ex?.detail || 'Upload failed') }
    finally { setBusy(false) }
  }

  return (
    <div className={'lp-upload' + (filled ? ' filled' : '')}
         style={{ cursor: busy ? 'progress' : 'pointer' }}
         onClick={() => { if (!busy && inputRef.current) inputRef.current.click() }}>
      <input ref={inputRef} type="file" onChange={pick}
             style={{ display: 'none' }} aria-label={title} />
      <span className="lp-ui"><Ico name={filled ? 'check' : icon} size={16}
        stroke={filled} /></span>
      <b>{title}</b>
      {err
        ? <span style={{ color: '#b91c1c' }}>{err}</span>
        : mine.length
          ? <span>{mine.map(f => f.filename).join(', ')}</span>
          : note ? <span>{note}</span> : null}
      <span className="lp-utag">
        {busy ? 'Uploading…' : filled ? 'Received' : tag}
      </span>
      {filled && onRemove ? (
        <button type="button" className="lp-ulink"
          onClick={ev => { ev.stopPropagation(); mine.forEach(f => onRemove(f.id)) }}
          style={{ background: 'none', border: 0, padding: 0, cursor: 'pointer',
                   color: '#64748b', textDecoration: 'underline', fontSize: 11 }}>
          Remove
        </button>
      ) : null}
    </div>
  )
}

/** The grid the upload tiles sit in. */
export function Uploads({ children }) {
  return <div className="lp-uploads">{children}</div>
}

export const YES_NO_PENDING = [
  { value: 'not_requested', label: 'Not requested yet' },
  { value: 'requested',     label: 'Requested — waiting' },
  { value: 'granted',       label: 'Granted' },
  { value: 'na',            label: 'Not applicable' },
]
