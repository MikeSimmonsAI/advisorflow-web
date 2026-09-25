/* Shared pieces for the five wholesale screens.
 *
 * ONE RULE LIVES HERE AND IS WORTH NAMING: `Money` and `Sourced` never render a
 * figure without its provenance. The backend labels every estimate
 * (estimated / imported / manual / verified) and the point of that labelling is
 * lost the moment a screen prints the number on its own. If you add a place
 * that shows an ARV, a repair estimate or an asking price, use these.
 */
import { Link } from 'react-router-dom'

export function fmtMoney(value, { blank = '—' } = {}) {
  if (value === null || value === undefined || value === '') return blank
  const n = Number(value)
  if (Number.isNaN(n)) return blank
  return '$' + n.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

export function fmtNum(value, blank = '—') {
  if (value === null || value === undefined || value === '') return blank
  const n = Number(value)
  return Number.isNaN(n) ? blank : n.toLocaleString()
}

/* Stored enumerations are snake_case because that is what the API and the CSV
 * importer agree on. Printing them raw — `single_family`, `owner_occupied`,
 * `buy_and_hold` — is the screen showing a person a database value. This turns
 * one into a label WITHOUT changing it: nothing is looked up, mapped or
 * guessed, so a value this code has never seen still reads correctly. */
export function fmtLabel(value, blank = '—') {
  if (value === null || value === undefined || value === '') return blank
  const text = String(value).replace(/[_-]+/g, ' ').trim()
  if (!text) return blank
  return text.charAt(0).toUpperCase() + text.slice(1)
}

export function fmtLabels(values, blank = '—') {
  if (!Array.isArray(values) || !values.length) return blank
  return values.map((v) => fmtLabel(v, '')).filter(Boolean).join(', ') || blank
}

/* `String(true)` on a seller record put the word `true` in front of a person
 * eight times on one screen. A three-state answer stays three-state: null is
 * "not stated", which is NOT the same as "no" and is what every send path
 * treats as an absence of permission. */
export function fmtBool(value, blank = 'not stated') {
  if (value === null || value === undefined || value === '') return blank
  return value ? 'Yes' : 'No'
}

export function fmtDate(value) {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString()
}

export function fmtWhen(value) {
  if (!value) return ''
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString()
}

/** A figure with the label of where it came from. Never one without the other. */
export function Sourced({ value, source, money = true }) {
  const text = money ? fmtMoney(value) : fmtNum(value)
  if (value === null || value === undefined || value === '') {
    return <span className="ws-muted">not set</span>
  }
  return (
    <span>
      {text}
      {source ? <span className={`ws-source src-${source}`}>{source}</span> : null}
    </span>
  )
}

export function Band({ band }) {
  if (!band) return <span className="ws-muted">—</span>
  return <span className={`ws-pill band-${band}`}>{band}</span>
}

export function Score({ value }) {
  const n = Number(value || 0)
  const cls = n >= 75 ? 'is-strong' : n >= 50 ? 'is-mid' : 'is-weak'
  return <span className={`ws-score ${cls}`}>{n}%</span>
}

/** Why a buyer matched, including the parts that did not. */
export function Factors({ factors }) {
  if (!factors || !factors.length) return null
  return (
    <ul className="ws-factors">
      {factors.map((f, i) => (
        <li key={i}>
          <span className={`ws-f-mark ${f.matched === true ? 'ws-f-yes'
            : f.matched === false ? 'ws-f-no' : 'ws-f-na'}`}>
            {f.matched === true ? '✓' : f.matched === false ? '✕' : '–'}
          </span>
          <span>{f.detail}</span>
        </li>
      ))}
    </ul>
  )
}

/** The offer arithmetic, line by line, in the order you would work it on paper. */
export function Steps({ steps }) {
  if (!steps || !steps.length) return null
  return (
    <ol className="ws-steps">
      {steps.map((s, i) => (
        <li key={i}>
          <span className="ws-step-label">
            {s.label}
            {s.note ? <span className="ws-step-note">{s.note}</span> : null}
          </span>
          <span className="ws-step-value">
            {typeof s.value === 'number' && Math.abs(s.value) >= 1000
              ? fmtMoney(s.value) : fmtNum(s.value)}
          </span>
        </li>
      ))}
    </ol>
  )
}

export function Warnings({ items }) {
  if (!items || !items.length) return null
  return (
    <div className="ws-warn">
      {items.filter(Boolean).map((w, i) => <div key={i}>{w}</div>)}
    </div>
  )
}

export function ErrorBox({ error }) {
  if (!error) return null
  return <div className="ws-error">{typeof error === 'string' ? error : error.message}</div>
}

/* `page` for the few empties that ARE the whole screen (a property list with
 * nothing in it). Everywhere else an empty panel is one caption-weight line
 * directly under its title, not a centred void. */
export function Empty({ children, page }) {
  return <div className={`ws-empty ${page ? 'ws-empty--page' : ''}`}>{children}</div>
}

export function DealLink({ id, children }) {
  return <Link to={`/wholesale/deals/${id}`}>{children}</Link>
}

/** Turn any thrown client error into something a person can act on. */
export function errText(e) {
  if (!e) return 'Something went wrong.'
  if (e.status === 402) {
    return (e.message || '') + ' — this organization is not switched on for the '
      + 'wholesale module.'
  }
  if (e.status === 409 && e.message) return e.message
  return e.message || 'Something went wrong.'
}


/* ── Secondary text, Phase 4 ───────────────────────────────────────────────
 *
 * Phase 3 wrote the reasoning for every design decision into the panel itself,
 * in full sentences, above the controls. That is documentation standing where
 * the operator's eye lands first, and by the third panel nobody reads it — so
 * the genuinely load-bearing lines ("nothing here ranks buyers", "the fee
 * collected is typed, not computed") were buried with the rest.
 *
 * Two levels now, and a panel gets at most one of each:
 *
 *   <Note>   one line, at most two, the operator actually needs. Muted.
 *   <Why>    the reasoning, folded away. Open it and it is all still there.
 */

export function Note({ children, tone }) {
  if (!children) return null
  return <p className={`ws-note ${tone ? `is-${tone}` : ''}`}>{children}</p>
}

/* A collapsed explanation. `label` completes "Why ..." so the summary reads as
 * a question somebody might actually have. */
export function Why({ label, children }) {
  if (!children) return null
  return (
    <details className="ws-why">
      <summary>{label || 'Why this works this way'}</summary>
      <div className="ws-why__body">{children}</div>
    </details>
  )
}


/* WHAT THE NUMBER IN THE BOX ACTUALLY SAYS.
 *
 * "302951.72" and "70" are what a field must CONTAIN — a formatted input
 * fights every keystroke and an input that reformats while you type loses
 * precision the moment somebody pastes. So the box keeps the raw value and
 * this line underneath reads it back: $302,951.72, 70%. The figure is never
 * rounded, never re-stored, and never enters a calculation.
 */
export function Reads({ value, as = 'money' }) {
  if (value === null || value === undefined || String(value).trim() === '') {
    return null
  }
  const n = Number(value)
  if (!Number.isFinite(n)) return null
  const text = as === 'percent'
    ? `${n.toLocaleString(undefined, { maximumFractionDigits: 3 })}%`
    : n.toLocaleString(undefined, {
        style: 'currency', currency: 'USD',
        // Cents only when there are cents. "$45,000.00" is noise on a figure
        // somebody typed as 45000.
        minimumFractionDigits: Number.isInteger(n) ? 0 : 2,
        maximumFractionDigits: 2,
      })
  return <span className="ws-reads">{text}</span>
}
