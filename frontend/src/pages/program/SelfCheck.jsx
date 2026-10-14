/* System self-check: does the program do what the plan says? Green when
   every check passes; otherwise each problem in plain words with names and a
   "Got it" button. A problem you've acknowledged stays hidden until it
   CHANGES (new people, new detail) - then it shows again.
   GET /program/self-check, refreshed every minute. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import './SelfCheck.css'

export default function SelfCheck({ compact = false }) {
  const [d, setD] = useState(null)
  const [showAck, setShowAck] = useState(false)
  const load = useCallback(() => api.get('/program/self-check').then(setD).catch(() => {}), [])
  useEffect(() => {
    load()
    const t = setInterval(() => { if (document.visibilityState === 'visible') load() }, 60000)
    return () => clearInterval(t)
  }, [load])
  const ack = c => api.post('/program/self-check/ack', { key: c.key, signature: c.signature }).then(setD).catch(() => {})
  if (!d) return null
  const at = new Date(d.checked_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  const open = d.checks.filter(c => !c.ok && !c.acknowledged)
  const acked = d.checks.filter(c => c.acknowledged)
  return (
    <div className={`sc ${d.ok ? 'ok' : 'bad'} ${compact ? 'compact' : ''}`} role="status">
      <div className="sc-head">
        <span className="sc-dot" aria-hidden="true" />
        <b>{d.ok ? 'System check: everything is running as planned' : `System check: ${d.problems} problem${d.problems > 1 ? 's' : ''}`}</b>
        <span className="sc-at">checked {at}</span>
      </div>
      {open.length > 0 && (
        <ul className="sc-list">
          {open.map(c => (
            <li key={c.key}>
              <b>{c.title}:</b> {c.detail}{c.people?.length ? <span className="sc-people"> {c.people.join(', ')}</span> : null}
              <button type="button" className="sc-ack" onClick={() => ack(c)}>Got it</button>
            </li>
          ))}
        </ul>
      )}
      {acked.length > 0 && (
        <p className="sc-acked">
          {acked.length} acknowledged problem{acked.length > 1 ? 's' : ''} hidden until {acked.length > 1 ? 'they change' : 'it changes'}.
          {' '}<button type="button" className="sc-link" onClick={() => setShowAck(v => !v)}>{showAck ? 'Hide' : 'Show'}</button>
        </p>
      )}
      {showAck && acked.length > 0 && (
        <ul className="sc-list ok">{acked.map(c => <li key={c.key}>{c.title}: {c.detail}</li>)}</ul>
      )}
      {!compact && d.ok && acked.length === 0 && (
        <ul className="sc-list ok">{d.checks.map(c => <li key={c.key}>✓ {c.detail}</li>)}</ul>
      )}
    </div>
  )
}
