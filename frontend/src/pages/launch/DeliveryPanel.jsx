/**
 * DeliveryPanel — the other half of the customer's launch.
 *
 * ===========================================================================
 * THE INTAKE IS NOT THE PROJECT
 * ===========================================================================
 *
 * Until this existed, a customer who had submitted their intake saw a locked
 * form and a progress ring at 100%, and had no way at all to answer "so what
 * is happening now?". Everything after submission — the build, the
 * connections, the testing, the training — was invisible to the only person
 * waiting for it.
 *
 * So this panel answers two questions and nothing else:
 *
 *     WHAT ARE THEY DOING?     one sentence, derived from the phase
 *     WHAT DO YOU NEED FROM ME? a list, each item actionable here
 *
 * ===========================================================================
 * WHY THE ACTIONS ARE FIRST AND THE STATUS SECOND
 * ===========================================================================
 *
 * A status board people cannot act on becomes a status board people stop
 * opening. If the customer owes us something, that is the first thing on the
 * screen; the connections and testing lists sit under it as reassurance, not
 * as the point.
 *
 * NOTHING INTERNAL REACHES HERE. The endpoint behind this builds its response
 * from the customer's entitlements rather than filtering a staff object, so
 * there is no internal note, blocker detail or owner name in the payload for
 * this component to accidentally render.
 */
import { useCallback, useEffect, useState } from 'react'

import { api } from '../../api/client'

const DOT = {
  verified: '#16a34a', not_applicable: '#9ca3af', blocked: '#dc2626',
  testing: '#f59e0b', connected: '#3b82f6', configuring: '#3b82f6',
  credentials_received: '#3b82f6', required: '#cbd5e1',
  pass: '#16a34a', fail: '#dc2626', retest: '#f59e0b', not_tested: '#cbd5e1',
}

function Dot({ status }) {
  return (
    <i style={{
      width: 8, height: 8, borderRadius: 999, flex: '0 0 8px',
      display: 'inline-block', background: DOT[status] || '#cbd5e1',
    }} />
  )
}

function Row({ status, children, right }) {
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'center',
                  padding: '7px 0', borderTop: '1px solid rgba(148,163,184,.22)' }}>
      <Dot status={status} />
      <span style={{ flex: 1, fontSize: 13, minWidth: 0 }}>{children}</span>
      {right}
    </div>
  )
}

/**
 * `data` / `readOnly` exist for the internal preview, and for nothing else.
 *
 * The fetch below is session-scoped: inside a preview it would answer with the
 * OPERATOR's delivery state and paint their integrations into a customer's
 * page. So the preview hands the already-composed customer view down as
 * `data`, this component renders that instead of asking, and `readOnly` takes
 * the buttons away — a preview must not be able to approve anything on a
 * customer's behalf.
 */
export default function DeliveryPanel({ brand, data = null, readOnly = false }) {
  const [fetched, setFetched] = useState(null)
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const supplied = data !== null
  const d = supplied ? data : fetched

  const load = useCallback(() => {
    if (supplied) return
    api.get('/launch/me/delivery')
      .then(setFetched)
      .catch(() => setFetched(null))
  }, [supplied])

  useEffect(load, [load])

  const act = useCallback(async (id, path) => {
    if (readOnly) return
    setBusy(id)
    setError(null)
    try {
      await api.post(path)
      load()
    } catch (e) {
      setError(e?.detail || 'That did not go through. Please try again.')
    } finally {
      setBusy(null)
    }
  }, [load, readOnly])

  // Nothing to say yet: no programme has been set up for this customer. An
  // empty panel is better than a panel of zeroes implying nothing will happen.
  if (!d) return null
  const c = d.counts
  const nothingYet = !c.connections_total && !c.checks_total && !c.training_total
                     && !d.blockers.length
  if (nothingYet) return null

  const btn = {
    fontSize: 12, fontWeight: 700, padding: '6px 12px', borderRadius: 8,
    border: '1px solid rgba(148,163,184,.5)', background: 'transparent',
    color: 'inherit', cursor: 'pointer', whiteSpace: 'nowrap',
  }

  return (
    <article className="lp-doc" style={{ marginTop: 20 }}>
      <div className="lp-doc-h">
        <p className="lp-stepno">Your implementation</p>
        <h2>Where your launch stands</h2>
        <p>{d.activity}</p>
      </div>

      <div className="lp-doc-b" style={{ paddingBottom: 24 }}>
        {error ? (
          <p style={{ fontSize: 12, color: '#dc2626', marginTop: 0 }}>{error}</p>
        ) : null}

        {d.actions.length ? (
          <div style={{ border: '1px solid rgba(245,158,11,.5)',
                        background: 'rgba(245,158,11,.08)', borderRadius: 10,
                        padding: '12px 14px', marginBottom: 18 }}>
            <b style={{ fontSize: 13 }}>What {brand.name} needs from you</b>
            <div style={{ marginTop: 6 }}>
              {d.actions.map(a => (
                <div key={a.kind + a.id}
                     style={{ display: 'flex', gap: 10, alignItems: 'center',
                              padding: '6px 0', fontSize: 13 }}>
                  <span style={{ flex: 1 }}>{a.label}</span>
                  {a.kind === 'approve_check' ? (
                    <button style={btn} disabled={busy === a.id}
                            onClick={() => act(a.id,
                              '/launch/me/checks/' + a.id + '/approve')}>
                      Yes, this works
                    </button>
                  ) : null}
                  {a.kind === 'acknowledge_training' ? (
                    <button style={btn} disabled={busy === a.id}
                            onClick={() => act(a.id,
                              '/launch/me/training/' + a.id + '/acknowledge')}>
                      Confirm
                    </button>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {d.blockers.length ? (
          <div style={{ marginBottom: 18 }}>
            <p className="lp-stepno" style={{ margin: '0 0 4px' }}>Waiting on</p>
            {d.blockers.map(b => (
              <div key={b.id} style={{ fontSize: 13, padding: '4px 0' }}>
                <b>{b.title}</b>
                {b.action ? <span> — {b.action}</span> : null}
              </div>
            ))}
          </div>
        ) : null}

        {c.connections_total ? (
          <div style={{ marginBottom: 18 }}>
            <p className="lp-stepno" style={{ margin: '0 0 2px' }}>
              Connections · {c.connections_done} of {c.connections_total} verified
            </p>
            {d.connections.map(x => (
              <Row key={x.label} status={x.status}
                   right={<span style={{ fontSize: 12, opacity: .7 }}>
                     {x.status_label}
                   </span>}>{x.label}</Row>
            ))}
          </div>
        ) : null}

        {c.checks_total ? (
          <div style={{ marginBottom: 18 }}>
            <p className="lp-stepno" style={{ margin: '0 0 2px' }}>
              Testing · {c.checks_done} of {c.checks_total} passing
            </p>
            {d.checks.map(x => (
              <Row key={x.id} status={x.status}
                   right={x.approved_at
                     ? <span style={{ fontSize: 12, color: '#16a34a' }}>
                         You approved this
                       </span>
                     : x.can_approve
                       ? <button style={btn} disabled={busy === x.id}
                                 onClick={() => act(x.id,
                                   '/launch/me/checks/' + x.id + '/approve')}>
                           Yes, this works
                         </button>
                       : <span style={{ fontSize: 12, opacity: .7 }}>
                           {x.status_label}
                         </span>}>
                {x.label}
              </Row>
            ))}
          </div>
        ) : null}

        {c.training_total ? (
          <div>
            <p className="lp-stepno" style={{ margin: '0 0 2px' }}>
              Training · {c.training_done} of {c.training_total} delivered
            </p>
            {d.training.map(x => (
              <Row key={x.id} status={x.completed_at ? 'pass' : 'not_tested'}
                   right={x.acknowledged_at
                     ? <span style={{ fontSize: 12, color: '#16a34a' }}>Confirmed</span>
                     : x.can_acknowledge
                       ? <button style={btn} disabled={busy === x.id}
                                 onClick={() => act(x.id,
                                   '/launch/me/training/' + x.id + '/acknowledge')}>
                           Confirm
                         </button>
                       : <span style={{ fontSize: 12, opacity: .7 }}>
                           {x.scheduled_at
                             ? new Date(x.scheduled_at).toLocaleDateString()
                             : 'Not scheduled yet'}
                         </span>}>
                {x.title}
              </Row>
            ))}
          </div>
        ) : null}
      </div>
    </article>
  )
}
