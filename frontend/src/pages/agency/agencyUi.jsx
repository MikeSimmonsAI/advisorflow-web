/**
 * MAX LIFE COMMAND — shared shell pieces: the page frame, the lion mark,
 * load/empty/error states, pills, the DEMO DATA badge and a fetch hook.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import { errText, humanize, toneFor } from './agencyFormat'
import './agency.css'

/** A restrained lion mark: a crowned mane in a single gold stroke. */
export function LionMark({ size = 28 }) {
  return (
    <svg className="ag-lion" width={size} height={size} viewBox="0 0 48 48" aria-hidden="true" focusable="false">
      <path d="M24 5l3.2 4.6 5.3-2 .9 5.6 5.5 1.2-2.2 5.2 4 3.9-4.6 3.2 1.4 5.4-5.6.3-1.3 5.5-5.1-2.5L24 43l-3.5-4.6-5.1 2.5-1.3-5.5-5.6-.3 1.4-5.4L5.3 26.5l4-3.9-2.2-5.2 5.5-1.2.9-5.6 5.3 2z"
        fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M18.5 21.5c1.4-1.2 3.4-1.2 4.2.2M29.5 21.5c-1.4-1.2-3.4-1.2-4.2.2M24 25v4.2M21 31.5c1.8 1.4 4.2 1.4 6 0"
        fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}

export function AgencyPage({ title, eyebrow, actions, children, testid }) {
  return (
    <div className="ag" data-testid={testid}>
      <header className="ag-head">
        <div className="ag-head__id">
          <LionMark />
          <div>
            <div className="ag-eyebrow">{eyebrow || 'Max Life Command'}</div>
            <h1 className="ag-title">{title}</h1>
          </div>
        </div>
        {actions ? <div className="ag-head__actions">{actions}</div> : null}
      </header>
      {children}
      <footer className="ag-foot">MAX LIFE COMMAND · Powered by EvoSysPro</footer>
    </div>
  )
}

export function DemoBadge({ on }) {
  if (!on) return null
  return <span className="ag-demo" title="Sample record created for demonstration — not a real client">DEMO DATA</span>
}

export function Pill({ value, label, tone }) {
  if (value === null || value === undefined || value === '') return null
  return <span className={`ag-pill ag-pill--${tone || toneFor(value)}`}>{label || humanize(value)}</span>
}

export function Loading({ label = 'Loading' }) {
  return <div className="ag-state" role="status"><span className="ag-spin" aria-hidden="true" />{label}…</div>
}

export function Empty({ children }) {
  return <div className="ag-state ag-state--empty">{children}</div>
}

export function ErrorState({ error, onRetry }) {
  if (!error) return null
  return (
    <div className="ag-state ag-state--error" role="alert">
      <span>{errText(error)}</span>
      {onRetry ? <button type="button" className="ag-btn ag-btn--ghost" onClick={onRetry}>Retry</button> : null}
    </div>
  )
}

export function Section({ title, aside, children, className = '' }) {
  return (
    <section className={`ag-card ${className}`}>
      {title ? <div className="ag-card__head"><h2>{title}</h2>{aside}</div> : null}
      {children}
    </section>
  )
}

export function Notice({ children }) {
  return <p className="ag-notice">{children}</p>
}

export function FilterBar({ label, clearTo }) {
  if (!label) return null
  return (
    <div className="ag-filter">
      <span>Filtered: <strong>{label}</strong></span>
      <Link to={clearTo}>Clear</Link>
    </div>
  )
}

/** GET a path; re-runs when the path changes. Returns {data, error, loading, reload}. */
export function useAgency(path) {
  const [state, setState] = useState({ data: null, error: null, loading: !!path })
  const seq = useRef(0)
  const load = useCallback(() => {
    if (!path) { setState({ data: null, error: null, loading: false }); return }
    const n = ++seq.current
    setState(s => ({ ...s, loading: true, error: null }))
    api.get(path)
      .then(d => { if (n === seq.current) setState({ data: d, error: null, loading: false }) })
      .catch(e => { if (n === seq.current) setState({ data: null, error: e || new Error('Request failed'), loading: false }) })
  }, [path])
  useEffect(() => { load() }, [load])
  return { ...state, reload: load }
}

/** Run a write; returns [run, busy, error]. */
export function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const run = useCallback(async (fn) => {
    setBusy(true); setError(null)
    try { return await fn() } catch (e) { setError(e); return undefined } finally { setBusy(false) }
  }, [])
  return [run, busy, error, setError]
}

/** Standard list frame: loading / error / empty / rows. */
export function ListState({ q, empty, children }) {
  if (q.loading && !q.data) return <Loading />
  if (q.error) return <ErrorState error={q.error} onRetry={q.reload} />
  const items = q.data?.items
  if (Array.isArray(items) && items.length === 0) return <Empty>{empty}</Empty>
  return children
}
