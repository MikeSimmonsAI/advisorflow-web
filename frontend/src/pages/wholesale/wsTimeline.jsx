/* The seller conversation, as one thread.
 *
 * What was here before was two lists stacked on top of each other — every
 * inbound message, then every outbound message — so the one thing a
 * conversation is FOR, the order things were said in, was the one thing the
 * screen did not show. Somebody picking the phone back up could not tell
 * whether the owner's last message had been answered.
 *
 * ONE THREAD, NEWEST FIRST. Merged and sorted on the timestamp each table
 * names its own event after — `sent_at` for ours, `received_at` for theirs.
 * A message with no timestamp sorts to the end rather than to the top, because
 * a missing date is not "just now".
 *
 * WHAT IT WILL NOT PRETEND. There is no inbox here. Nothing in this module
 * receives a text message on its own, so the entry box is labelled as what it
 * is — a person typing in what the owner said — and it says plainly that it
 * sends nothing. A box that looked like a reply field would be a promise the
 * product cannot keep.
 */
import { useMemo } from 'react'
import { fmtWhen, fmtLabel, Note, Why } from './wsShared'

const OUT = 'out'
const IN = 'in'

function stamp(value) {
  if (!value) return null
  const t = new Date(value).getTime()
  return Number.isNaN(t) ? null : t
}

/* The delivery word the platform stored, read as English. `null` is left as
 * "not recorded" — it is not the same as delivered, and a screen that rendered
 * the absence of a status as success would be inventing a delivery. */
function deliveryTone(status) {
  const s = String(status || '').toLowerCase()
  if (['delivered', 'sent', 'ok'].includes(s)) return 'is-ok'
  if (['failed', 'undelivered', 'bounced', 'blocked'].includes(s)) return 'is-dnc'
  if (s) return 'is-warn'
  return 'is-muted'
}


export function SellerTimeline({ communications }) {
  const entries = useMemo(() => {
    const inbound = (communications?.inbound || []).map((m) => ({
      key: `in-${m.id}`, side: IN, body: m.body,
      at: m.received_at, sortable: stamp(m.received_at),
      badge: m.classification ? fmtLabel(m.classification) : null,
      // A message somebody typed in is not a message that arrived. The thread
      // says which, because "the owner texted us" and "a colleague told me the
      // owner said" are different kinds of evidence.
      transcribed: String(m.source || '').toLowerCase() === 'manual',
    }))
    const outbound = (communications?.outbound || []).map((m) => ({
      key: `out-${m.id}`, side: OUT, body: m.body,
      at: m.sent_at, sortable: stamp(m.sent_at),
      status: m.status,
    }))
    return [...inbound, ...outbound].sort((a, b) => {
      // An undated row goes last, not first. Treating a missing timestamp as
      // the epoch or as now both put it somewhere it does not belong.
      if (a.sortable === null && b.sortable === null) return 0
      if (a.sortable === null) return 1
      if (b.sortable === null) return -1
      return b.sortable - a.sortable
    })
  }, [communications])

  if (!entries.length) {
    return (
      <div className="panel ws-panel">
        <div className="panel-title ws-panel-title">The conversation</div>
        <p className="ws-panel-note">
          Nothing has been sent to or heard from this owner yet.
        </p>
      </div>
    )
  }

  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">
        <span>The conversation ({entries.length})</span>
        <span className="ws-muted ws-thread__order">Most recent first</span>
      </div>
      <Note>One thread, in the order things actually happened.</Note>
      <Why label="What the delivery labels mean">
        <p className="ws-comp__sub">
          Outbound messages carry the delivery result the provider gave. Nothing
          is shown as delivered because it was merely sent.
        </p>
      </Why>

      <ol className="ws-thread">
        {entries.map((e) => (
          <li key={e.key} className={`ws-thread__item is-${e.side}`}>
            <div className="ws-thread__bubble">
              <div className="ws-thread__meta">
                <span className="ws-thread__who">
                  {e.side === IN ? 'The owner' : 'Us'}
                </span>
                <span className="ws-thread__at">
                  {e.at ? fmtWhen(e.at) : 'no date recorded'}
                </span>
                {e.side === OUT ? (
                  <span className={`ws-pill ${deliveryTone(e.status)}`}>
                    {e.status ? fmtLabel(e.status) : 'delivery not recorded'}
                  </span>
                ) : null}
                {e.transcribed
                  ? <span className="ws-pill is-muted">recorded by a person</span>
                  : null}
                {e.badge ? <span className="ws-pill is-muted">{e.badge}</span> : null}
              </div>
              <div className="ws-thread__body">
                {e.body || <span className="ws-muted">(no text stored)</span>}
              </div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}
