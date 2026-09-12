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
 * ===========================================================================
 * V2: FOUR GROUPS, AND NOT ONE INVENTED VALUE
 * ===========================================================================
 *
 * The raw list became four cards — CONNECTIONS, TESTING, TRAINING, LAUNCH
 * READINESS — because those are the four questions a customer has about an
 * implementation, and a flat checklist made somebody read all of it to answer
 * any of them.
 *
 * WHAT DID NOT CHANGE IS THE HONESTY, and it is the part to hold on to:
 *
 *   - every count is `done of total` straight from the payload
 *   - a state chip is derived from those counts and nothing else: nothing is
 *     "complete" unless the records say every required row is settled
 *   - an unknown value renders as an em dash, an unscheduled session says
 *     "Not scheduled", and a group with no rows says "Not started"
 *   - no card fills a gap with a plausible-looking value, and no card claims
 *     a stage has begun because the stage before it did
 *
 * THE ACTIONS STAY FIRST. A status board people cannot act on becomes a status
 * board people stop opening. If the customer owes us something, that is above
 * the four cards; the cards are reassurance, not the point.
 *
 * NOTHING INTERNAL REACHES HERE. The endpoint behind this builds its response
 * from the customer's entitlements rather than filtering a staff object, so
 * there is no internal note, blocker detail or owner name in the payload for
 * this component to accidentally render.
 */
import { useCallback, useEffect, useState } from 'react'

import { api } from '../../api/client'
import { Ico } from './LaunchUI'
import { humanise } from './present'

const DASH = '—'

/** done/total → the chip. Derived, never asserted: see the header. */
function groupState(done, total) {
  if (!total) return { label: 'Not started', cls: '' }
  if (done >= total) return { label: 'Complete', cls: 'go' }
  if (done > 0) return { label: 'In progress', cls: 'mid' }
  return { label: 'Not started', cls: '' }
}

function Card({ icon, title, done, total, rows, foot }) {
  const st = groupState(done, total)
  return (
    <article className="lp-icard">
      <div className="lp-ih">
        <span className="lp-ii"><Ico name={icon} size={14} /></span>
        <b>{title}</b>
      </div>
      <span className={'lp-istate ' + st.cls}>{st.label}</span>
      <div className="lp-ilist">
        {rows.length
          ? rows.map(r => (
            <div className="lp-irow" key={r.key}>
              <span className="lp-ilabel">{r.label}</span>
              <span className="lp-ir">{r.value}</span>
            </div>
          ))
          : <div className="lp-irow"><span className="lp-ilabel">
              Nothing recorded yet</span><span className="lp-ir">{DASH}</span>
            </div>}
      </div>
      {foot ? <p className="lp-ifoot">{foot}</p> : null}
    </article>
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
export default function DeliveryPanel({ brand, data = null, readOnly = false,
                                        implementation = null }) {
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
    border: '1px solid var(--lp-cline-strong)', background: '#fff',
    color: 'inherit', cursor: 'pointer', whiteSpace: 'nowrap',
  }

  const impl = implementation || {}
  const outstanding = (d.actions || []).length + (d.blockers || []).length
  const goLive = impl.target_launch_date
    ? new Date(String(impl.target_launch_date).length <= 10
        ? impl.target_launch_date + 'T00:00:00' : impl.target_launch_date)
      .toLocaleDateString()
    : null

  // LAUNCH READINESS IS DERIVED FROM AFFIRMATIVE FACTS ONLY.
  //
  // The first version of this counted "nothing outstanding with you" as one of
  // three settled facts, which made a launch where NOTHING had started read as
  // IN PROGRESS — there was nothing outstanding because there was nothing at
  // all. An absence is not an achievement, and a status card that rounds one
  // up into the other is the invented completion this whole surface exists to
  // avoid.
  //
  // So the count is three things that must each be positively true: a date
  // somebody agreed, a programme whose every group is actually finished, and a
  // live implementation. The rows still SHOW the outstanding count, because it
  // is useful; it just does not earn progress.
  const everyGroupSettled = Boolean(
    c.connections_total && c.connections_done >= c.connections_total
    && c.checks_total && c.checks_done >= c.checks_total
    && c.training_total && c.training_done >= c.training_total)

  const readyRows = [
    { key: 'date', label: 'Target go-live date', value: goLive || 'Not set' },
    { key: 'open', label: 'Outstanding with you',
      value: outstanding ? String(outstanding) : 'None' },
    { key: 'status', label: 'Implementation status',
      value: impl.status ? humanise(impl.status) : DASH },
  ]
  const readyDone = (goLive ? 1 : 0) + (everyGroupSettled ? 1 : 0)
                    + (impl.status === 'live' ? 1 : 0)

  return (
    <section className="lp-band">
      <div className="lp-band-h">
        <h2>Where your launch stands</h2>
        <p>{d.activity}</p>
        <span className="lp-bandnote">
          Updated by {brand.name}, not by this form
        </span>
      </div>

      {error ? (
        <p style={{ fontSize: 12, color: '#b91c1c', margin: '0 0 12px' }}>
          {error}
        </p>
      ) : null}

      {/* What the customer owes us, above everything. */}
      {d.actions.length ? (
        <div className="lp-note warn" style={{ marginBottom: 16 }}>
          <span className="lp-nicon"><Ico name="info" size={16} /></span>
          <div className="lp-nb">
            <b>What {brand.name} needs from you</b>
            {d.actions.map(a => (
              <div key={a.kind + a.id}
                   style={{ display: 'flex', gap: 10, alignItems: 'center',
                            padding: '6px 0', fontSize: 13 }}>
                <span style={{ flex: 1 }}>{a.label}</span>
                {a.kind === 'approve_check' ? (
                  <button style={btn} disabled={readOnly || busy === a.id}
                          title={readOnly ? 'Unavailable in preview' : undefined}
                          onClick={() => act(a.id,
                            '/launch/me/checks/' + a.id + '/approve')}>
                    Yes, this works
                  </button>
                ) : null}
                {a.kind === 'acknowledge_training' ? (
                  <button style={btn} disabled={readOnly || busy === a.id}
                          title={readOnly ? 'Unavailable in preview' : undefined}
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

      <div className="lp-impl">
        <Card icon="plug" title="Connections"
              done={c.connections_done} total={c.connections_total}
              rows={(d.connections || []).map(x => ({
                key: x.label, label: x.label, value: x.status_label || DASH,
              }))}
              foot={c.connections_total
                ? null
                : 'Begins once your onboarding is submitted.'} />

        <Card icon="check" title="Testing"
              done={c.checks_done} total={c.checks_total}
              rows={(d.checks || []).map(x => ({
                key: x.id, label: x.label,
                value: x.approved_at ? 'You approved this'
                  : (x.status_label || DASH),
              }))}
              foot={c.checks_total ? null : 'Scheduled once the build is complete.'} />

        <Card icon="layers" title="Training"
              done={c.training_done} total={c.training_total}
              rows={(d.training || []).map(x => ({
                key: x.id, label: x.title,
                value: x.acknowledged_at ? 'Confirmed'
                  : x.completed_at ? 'Delivered'
                    : x.scheduled_at
                      ? new Date(x.scheduled_at).toLocaleDateString()
                      : 'Not scheduled',
              }))}
              foot={c.training_total ? null : 'Booked with you after testing.'} />

        <Card icon="rocket" title="Launch readiness"
              done={readyDone} total={3} rows={readyRows}
              foot="Confirmed together before go-live." />
      </div>

      {/* Waiting-on, kept as its own line rather than folded into a card: it
          is the one thing here that is neither a count nor a date. */}
      {d.blockers.length ? (
        <div style={{ marginTop: 14 }}>
          <p className="lp-stepno" style={{ margin: '0 0 4px' }}>Waiting on</p>
          {d.blockers.map(b => (
            <div key={b.id} style={{ fontSize: 13, padding: '4px 0' }}>
              <b>{b.title}</b>
              {b.action ? <span> — {b.action}</span> : null}
            </div>
          ))}
        </div>
      ) : null}
    </section>
  )
}
