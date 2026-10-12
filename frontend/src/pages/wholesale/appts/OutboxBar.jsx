/* The field app's signal bar (build list #28). Says plainly when there is no
 * signal, how much is saved on the phone waiting to send, and lets the rep
 * send now, try again, or throw away something the server refused. Hidden
 * when there is signal and nothing waiting. */
import { useEffect, useState } from 'react'
import { all, discard, flush, retry, startOutbox, subscribe } from './outbox'

const online = () => (typeof navigator === 'undefined' ? true : navigator.onLine !== false)

export default function OutboxBar() {
  const [items, setItems] = useState([])
  const [on, setOn] = useState(online())
  const [sending, setSending] = useState(false)
  useEffect(() => {
    startOutbox()
    const off = subscribe(setItems)
    all().then(setItems).catch(() => {})
    const up = () => setOn(true)
    const down = () => setOn(false)
    window.addEventListener('online', up)
    window.addEventListener('offline', down)
    return () => { off(); window.removeEventListener('online', up); window.removeEventListener('offline', down) }
  }, [])
  const waiting = items.filter((x) => !x.error)
  const refused = items.filter((x) => x.error)
  if (on && !items.length) return null
  const n = waiting.length
  async function sendNow() { setSending(true); try { await flush() } finally { setSending(false) } }
  return (
    <div className={`ob-bar${on ? '' : ' is-off'}`} role="status" aria-live="polite">
      <div className="ob-row">
        <span className="ob-dot" aria-hidden="true" />
        <span>
          {on ? null : <strong>No signal. </strong>}
          {n ? `${n} thing${n === 1 ? '' : 's'} saved on this phone, waiting to send.`
            : 'Keep working - anything you save stays on this phone and sends when signal comes back.'}
        </span>
        {n && on ? <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={sending} onClick={sendNow}>
          {sending ? 'Sending…' : 'Send now'}</button> : null}
      </div>
      {refused.map((x) => (
        <div key={x.key} className="ob-row ob-row--bad">
          <span>Could not send {x.label || 'a change'}: {x.error}</span>
          <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => retry(x.key)}>Try again</button>
          <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => discard(x.key)}>Throw it away</button>
        </div>
      ))}
    </div>
  )
}
