/**
 * Shared Gold & Jewelry UI: data hook, formatters, small primitives.
 * Everything visible comes from /jewelry-ops. No figure is invented in the
 * browser - an empty answer renders an empty state, not a number.
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
  const d = e.detail ?? e.data?.detail
  if (typeof d === 'string') return d
  if (d && typeof d === 'object' && d.message) return d.message
  return e.message || 'Something went wrong.'
}

export const n = (v) => (v === null || v === undefined ? '—' : Number(v).toLocaleString())
export const money = (v) => (v === null || v === undefined ? '—'
  : '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 }))
export function day(iso) {
  if (!iso) return ''
  const d = new Date(iso.length <= 10 ? iso + 'T00:00:00' : iso)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}
export function when(isoLocal) {
  if (!isoLocal) return ''
  const d = new Date(isoLocal)
  return d.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}
export function time(isoLocal) {
  if (!isoLocal) return ''
  return new Date(isoLocal).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}
export function ago(iso) {
  if (!iso) return ''
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  const d = Math.floor(s / 86400)
  return d === 1 ? 'yesterday' : `${d} days ago`
}
/** "2026-11-06T14:30" in the browser's local wall clock (the store's, in practice). */
export function localInput(d = new Date()) {
  const p = (x) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

export const SEG_CLASS = { gold_seller: 'gold', jewelry_buyer: 'jewelry', coin_collector: 'coins', previous: 'previous',
                           vip: 'vip', inactive: 'inactive', general: 'general' }

export function Loading({ label = 'Loading' }) {
  return <div className="jw-loading" role="status"><span className="jw-spin" />{label}…</div>
}
export function ErrorNote({ children, onRetry }) {
  if (!children) return null
  return (
    <div className="jw-error" role="alert">
      <span>{children}</span>
      {onRetry && <button className="jw-btn jw-btn--sm" onClick={onRetry}>Try again</button>}
    </div>
  )
}
export function Empty({ title, children, action }) {
  return (
    <div className="jw-empty">
      <b>{title}</b>
      {children && <p>{children}</p>}
      {action}
    </div>
  )
}
export function Info({ text }) {
  if (!text) return null
  return <span className="jw-info" title={text} aria-label={text} tabIndex={0}>i</span>
}
export function Kpi({ label, value, foot, def, tone }) {
  return (
    <div className={`jw-kpi ${tone ? 'jw-kpi--' + tone : ''}`}>
      <div className="jw-kpi__label">{label}<Info text={def} /></div>
      <div className="jw-kpi__value">{value}</div>
      {foot && <div className="jw-kpi__foot">{foot}</div>}
    </div>
  )
}
export function Pill({ children, tone = '' }) {
  return <span className={`jw-pill ${tone ? 'jw-pill--' + tone : ''}`}>{children}</span>
}
export function Modal({ title, onClose, children, wide }) {
  useEffect(() => {
    const k = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose])
  return (
    <div className="jw-modal" role="dialog" aria-modal="true" aria-label={title}>
      <div className="jw-modal__scrim" onClick={onClose} />
      <div className={`jw-modal__panel ${wide ? 'wide' : ''}`}>
        <div className="jw-modal__head">
          <h2>{title}</h2>
          <button className="jw-x" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="jw-modal__body">{children}</div>
      </div>
    </div>
  )
}
export function Field({ label, children, hint }) {
  return (
    <label className="jw-field">
      <span className="jw-field__label">{label}</span>
      {children}
      {hint && <span className="jw-field__hint">{hint}</span>}
    </label>
  )
}

/** Search-and-pick a customer from this workspace (the caller's scope). */
export function CustomerPicker({ value, onChange, placeholder = 'Search customers by name or phone…' }) {
  const [q, setQ] = useState('')
  const [list, setList] = useState([])
  const [picked, setPicked] = useState(null)
  useEffect(() => {
    if (!value) { setPicked(null); return }
    if (picked && picked.id === value) return
    api.get('/jewelry-ops/customers/' + value).then(setPicked).catch(() => setPicked(null))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])
  useEffect(() => {
    if (q.trim().length < 2) { setList([]); return }
    const t = setTimeout(() => {
      api.get('/jewelry-ops/customers?limit=8&q=' + encodeURIComponent(q.trim()))
        .then(d => setList(d.customers || [])).catch(() => setList([]))
    }, 220)
    return () => clearTimeout(t)
  }, [q])
  if (picked) {
    return (
      <div className="jw-picked">
        <b>{picked.name}</b><span>{picked.phone || picked.email || ''}</span>
        <button type="button" className="jw-btn jw-btn--sm jw-btn--ghost" onClick={() => { setPicked(null); onChange(null) }}>Change</button>
      </div>
    )
  }
  return (
    <div className="jw-picker">
      <input className="jw-input" value={q} onChange={e => setQ(e.target.value)} placeholder={placeholder} />
      {list.length > 0 && (
        <ul className="jw-picker__list">
          {list.map(c => (
            <li key={c.id}><button type="button" onClick={() => { setPicked(c); onChange(c.id); setQ(''); setList([]) }}>
              <b>{c.name}</b><span>{c.phone || c.email || ''}</span>
            </button></li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function useToast() {
  const [msg, setMsg] = useState(null)
  const t = useRef(null)
  const show = useCallback((text, tone = 'ok') => {
    setMsg({ text, tone })
    clearTimeout(t.current)
    t.current = setTimeout(() => setMsg(null), 4200)
  }, [])
  const node = msg ? <div className={`jw-toast jw-toast--${msg.tone}`} role="status">{msg.text}</div> : null
  return [show, node]
}

export function MountainMark({ size = 40 }) {
  return (
    <svg width={size} height={size * 0.76} viewBox="0 0 90 68" fill="none" aria-hidden="true">
      <path d="M3 52 30 20l14 12 15-24 28 44-20-7-11-14-12 11-12-10-12 20Z" stroke="#dfbf6e" strokeWidth="4.5" strokeLinejoin="round" />
      <path d="M8 60h76" stroke="#fff" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}
