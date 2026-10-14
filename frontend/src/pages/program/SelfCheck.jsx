/* System self-check: does the program do what the plan says? Green when
   every check passes; otherwise each problem in plain words with names.
   GET /program/self-check, refreshed every minute. */
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import './SelfCheck.css'

export default function SelfCheck({ compact = false }) {
  const [d, setD] = useState(null)
  useEffect(() => {
    let alive = true
    const load = () => api.get('/program/self-check').then(x => { if (alive) setD(x) }).catch(() => {})
    load()
    const t = setInterval(() => { if (document.visibilityState === 'visible') load() }, 60000)
    return () => { alive = false; clearInterval(t) }
  }, [])
  if (!d) return null
  const at = new Date(d.checked_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  const bad = d.checks.filter(c => !c.ok)
  return (
    <div className={`sc ${d.ok ? 'ok' : 'bad'} ${compact ? 'compact' : ''}`} role="status">
      <div className="sc-head">
        <span className="sc-dot" aria-hidden="true" />
        <b>{d.ok ? 'System check: everything is running as planned' : `System check: ${d.problems} problem${d.problems > 1 ? 's' : ''}`}</b>
        <span className="sc-at">checked {at}</span>
      </div>
      {bad.length > 0 && (
        <ul className="sc-list">
          {bad.map(c => (
            <li key={c.key}><b>{c.title}:</b> {c.detail}{c.people?.length ? <span className="sc-people"> {c.people.join(', ')}</span> : null}</li>
          ))}
        </ul>
      )}
      {!compact && d.ok && (
        <ul className="sc-list ok">
          {d.checks.map(c => <li key={c.key}>✓ {c.detail}</li>)}
        </ul>
      )}
    </div>
  )
}
