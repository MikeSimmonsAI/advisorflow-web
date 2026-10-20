/**
 * Shared roofing UI: data hook, icons, small primitives, formatters.
 * Everything visible comes from the /roofing-ops API (or the shared
 * /communications and /conversation-intel endpoints). No figure is invented
 * in the browser - an empty answer renders an empty state, not a number.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'

export function useApi(path, deps = []) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(!!path)
  const alive = useRef(true)
  const load = useCallback(async () => {
    if (!path) return
    setLoading(true)
    try {
      const d = await api.get(path)
      if (alive.current) { setData(d); setError(null) }
    } catch (e) {
      if (alive.current) setError(errText(e))
    } finally {
      if (alive.current) setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps])
  useEffect(() => { alive.current = true; load(); return () => { alive.current = false } }, [load])
  return { data, error, loading, reload: load, setData }
}

export function errText(e) {
  if (!e) return 'Something went wrong.'
  const d = e.detail ?? e.data?.detail ?? e.body?.detail
  if (typeof d === 'string') return d
  if (d && typeof d === 'object' && d.message) return d.message
  return e.message || 'Something went wrong.'
}

// ── formatting ─────────────────────────────────────────────────────────────
export const n = (v) => (v === null || v === undefined ? '—' : Number(v).toLocaleString())
export const pct = (v) => (v === null || v === undefined ? '—' : `${Number(v).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`)
export function money(v, short = true) {
  if (v === null || v === undefined) return '—'
  const x = Number(v)
  if (short && x >= 1000000) return `$${(x / 1000000).toFixed(1)}M`
  if (short && x >= 1000) return `$${Math.round(x / 1000)}K`
  return `$${x.toLocaleString()}`
}
export function ago(iso) {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  const s = Math.max(0, (Date.now() - t) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  const d = Math.floor(s / 86400)
  return d === 1 ? 'yesterday' : `${d}d ago`
}
export function until(iso) {
  if (!iso) return ''
  const s = (new Date(iso).getTime() - Date.now()) / 1000
  if (s <= 0) return 'due now'
  if (s < 3600) return `in ${Math.ceil(s / 60)}m`
  if (s < 86400) return `in ${Math.round(s / 3600)}h`
  return `in ${Math.round(s / 86400)}d`
}
/** A booked_time is the salesperson's local wall clock (naive) - show it as written. */
export function localWhen(naive, opts = {}) {
  if (!naive) return ''
  const [d, t] = naive.split('T')
  const [y, m, day] = d.split('-').map(Number)
  const [hh, mm] = (t || '00:00').split(':').map(Number)
  const dt = new Date(y, m - 1, day, hh, mm)
  return dt.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', ...opts })
}
export const initials = (name) => (name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join('')

// ── primitives ─────────────────────────────────────────────────────────────
const TINTS = ['#2F6FED', '#0E9F6E', '#F26A1B', '#7C5CE0', '#D9480F', '#0B7285', '#C2255C']
export function Avatar({ name, size = 36, ai = false }) {
  if (ai) return <span className="rf-avatar rf-avatar--ai" style={{ width: size, height: size }} aria-label="AI">AI</span>
  const s = (name || '').split('').reduce((a, c) => a + c.charCodeAt(0), 0)
  return (
    <span className="rf-avatar" style={{ width: size, height: size, background: TINTS[s % TINTS.length] }} aria-hidden="true">
      {initials(name)}
    </span>
  )
}

const TONES = {
  interested: 'green', ready_to_book: 'orange', needs_human: 'red', question: 'violet', callback: 'violet',
  new_reply: 'blue', booked: 'green', in_progress: 'slate', opted_out: 'slate', dnc: 'slate',
  not_interested: 'slate', neutral: 'slate', wrong_number: 'slate',
}
export function Pill({ tone, children, title }) {
  return <span className={`rf-pill rf-pill--${TONES[tone] || tone || 'slate'}`} title={title}>{children}</span>
}

export function Panel({ title, sub, actions, children, className = '', pad = true }) {
  return (
    <section className={`rf-panel ${className}`}>
      {(title || actions) && (
        <header className="rf-panel__head">
          <div>
            {title && <h2 className="rf-panel__title">{title}</h2>}
            {sub && <p className="rf-panel__sub">{sub}</p>}
          </div>
          {actions && <div className="rf-panel__actions">{actions}</div>}
        </header>
      )}
      <div className={pad ? 'rf-panel__body' : ''}>{children}</div>
    </section>
  )
}

export function Empty({ icon = 'inbox', title, children, action }) {
  return (
    <div className="rf-empty">
      <Icon name={icon} size={28} />
      <p className="rf-empty__title">{title}</p>
      {children && <p className="rf-empty__text">{children}</p>}
      {action}
    </div>
  )
}

export function Loading({ label = 'Loading' }) {
  return <div className="rf-loading" role="status"><span className="rf-spinner" />{label}…</div>
}

export function ErrorNote({ children, onRetry }) {
  return (
    <div className="rf-error" role="alert">
      <Icon name="alert" size={18} />
      <span>{children}</span>
      {onRetry && <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={onRetry}>Try again</button>}
    </div>
  )
}

export function SampleTag() {
  return <span className="rf-sample" title="These figures come from the sample workspace, not a real company">Sample data</span>
}

/** A number with what it is a share of, always printed together. */
export function Ratio({ value, denom, denomLabel, pctValue }) {
  return (
    <span className="rf-ratio">
      <b>{pct(pctValue)}</b>
      <span>{n(value)} of {n(denom)} {denomLabel}</span>
    </span>
  )
}

// ── icons (stroke, 24 grid) ────────────────────────────────────────────────
const P = {
  home: 'M3 11.5 12 4l9 7.5M5.5 9.5V20h13V9.5',
  spark: 'M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18',
  queue: 'M4 6h16M4 12h16M4 18h10',
  chat: 'M4 5h16v11H8l-4 4z',
  calendar: 'M4 6h16v14H4zM4 10h16M8 3v4M16 3v4',
  chart: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  pipeline: 'M3 5h4v14H3zM10 5h4v9h-4zM17 5h4v5h-4z',
  users: 'M16 20v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM22 20v-1a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  send: 'M22 2 11 13M22 2l-7 20-4-9-9-4z',
  upload: 'M12 16V4M7 9l5-5 5 5M4 20h16',
  check: 'M5 12.5 10 17 19 7',
  alert: 'M12 8v5M12 16.5v.5M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z',
  inbox: 'M3 13h5l1.5 3h5L16 13h5M5 5h14l2 8v6H3v-6z',
  phone: 'M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z',
  mail: 'M3 5h18v14H3zM3 6l9 7 9-7',
  pin: 'M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21zM12 12a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z',
  hand: 'M8 13V5.5a1.5 1.5 0 0 1 3 0V12M11 11.5V4a1.5 1.5 0 0 1 3 0v7.5M14 11.5V6a1.5 1.5 0 0 1 3 0v7c0 4.4-2.6 8-7 8-3 0-4.6-1.6-6-4l-2-3.5a1.5 1.5 0 0 1 2.6-1.5L8 15',
  play: 'M7 4v16l13-8z',
  pause: 'M7 4h3v16H7zM14 4h3v16h-3z',
  arrow: 'M5 12h14M13 6l6 6-6 6',
  back: 'M19 12H5M11 6l-6 6 6 6',
  menu: 'M4 6h16M4 12h16M4 18h16',
  close: 'M6 6l12 12M18 6 6 18',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM21 21l-4.3-4.3',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2',
  roof: 'M2 12 12 4l10 8M5 10v10h14V10',
  dollar: 'M12 2v20M17 6H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6',
  file: 'M14 3H6v18h12V7zM14 3v4h4',
  bolt: 'M13 2 4 14h7l-1 8 9-12h-7z',
  filter: 'M3 5h18l-7 8v6l-4 2v-8z',
  refresh: 'M20 11a8 8 0 0 0-14.9-3M4 4v4h4M4 13a8 8 0 0 0 14.9 3M20 20v-4h-4',
  logout: 'M15 4h4v16h-4M10 17l5-5-5-5M15 12H3',
  star: 'M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z',
}
export function Icon({ name, size = 20, className = '', title }) {
  return (
    <svg className={`rf-icon ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden={title ? undefined : 'true'} role={title ? 'img' : undefined}>
      {title && <title>{title}</title>}
      <path d={P[name] || P.spark} />
    </svg>
  )
}

/** Toast that announces what changed after an action. */
export function useToast() {
  const [msg, setMsg] = useState(null)
  useEffect(() => {
    if (!msg) return undefined
    const t = setTimeout(() => setMsg(null), 4200)
    return () => clearTimeout(t)
  }, [msg])
  const node = msg ? <div className={`rf-toast rf-toast--${msg.tone || 'ok'}`} role="status">{msg.text}</div> : null
  return [node, (text, tone) => setMsg({ text, tone })]
}
