/**
 * Record what happened — the front end of the T9 completion gap.
 *
 * WHY THIS DIALOG IS SHAPED THE WAY IT IS
 * ---------------------------------------
 * T9 Intelligence reported appointment completion as UNKNOWN, and it was
 * right to: nothing recorded whether a meeting took place. The fix is a human
 * telling us, which makes this dialog the entire mechanism — and a dialog
 * people avoid produces exactly the dataset we already had, only with a field
 * in it.
 *
 * So: one click to the common answer, nothing mandatory beyond the verdict
 * itself, and every consequence stated before it happens. Specifically —
 *
 *   · The outcome list comes from the SERVER, per appointment, with the
 *     unavailable options shown greyed WITH THEIR REASON rather than hidden.
 *     Silently omitting "Won" from a meeting with no deal attached leaves the
 *     rep hunting for a button that was never there.
 *   · Moving the deal's stage is an explicit tick, never a side effect, and it
 *     appears only when the server says this caller could actually do it. A
 *     stage that moves because somebody recorded an outcome is how every "Won"
 *     in a pipeline becomes suspect.
 *   · Per-person attendance is optional. Leaving it alone records "unknown",
 *     not "everybody was there" — the one thing we were not told.
 *
 * THE ONE REFUSAL: a future meeting cannot be marked completed. The server
 * enforces it; this dialog explains it up front rather than letting somebody
 * click and be rejected.
 */
import { useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import { Chip, ErrorBar } from './parts'
import { wallTime, dayFromYmd } from './calendarTime'

/** Local YYYY-MM-DD `n` days out, for the follow-up date input. */
function inDays(n) {
  const d = new Date()
  d.setDate(d.getDate() + n)
  const p = x => String(x).padStart(2, '0')
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate())
}

export default function OutcomeDialog({ appt, onClose, onRecorded }) {
  const [catalog, setCatalog] = useState(null)
  const [outcome, setOutcome] = useState(appt?.outcome_state?.outcome || '')
  const [notes, setNotes] = useState('')
  const [nextAction, setNextAction] = useState('')
  const [dueAt, setDueAt] = useState('')
  const [advance, setAdvance] = useState(false)
  const [attendance, setAttendance] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!appt?.id) return
    api.get('/sales/appointments/' + appt.id + '/outcome-options')
      .then(setCatalog)
      .catch(e => setError(e.message || 'Could not load the outcome options.'))
  }, [appt?.id])

  const chosen = useMemo(
    () => (catalog?.options || []).find(o => o.value === outcome) || null,
    [catalog, outcome])

  /** Picking an outcome offers its usual next action rather than imposing one. */
  function pick(o) {
    if (!o.available) return
    setOutcome(o.value)
    setError(null)
    if (o.default_next_action && !nextAction) {
      setNextAction(o.default_next_action)
      if (!dueAt) setDueAt(inDays(o.value === 'proposal_needed' ? 2 : 5))
    }
    // The stage tick resets on every change of outcome. Carrying it across
    // would let somebody choose "Won", tick the box, change their mind to
    // "Follow-up required", and still move the deal to Won.
    setAdvance(false)
  }

  async function submit() {
    if (!outcome) return
    setBusy(true); setError(null)
    try {
      await api.post('/sales/appointments/' + appt.id + '/outcome', {
        outcome,
        notes: notes.trim() || undefined,
        attendance: Object.keys(attendance).length ? attendance : undefined,
        next_action: nextAction.trim() || undefined,
        // Sent as a naive local datetime, matching what every other date input
        // in this workspace sends.
        next_action_due_at: dueAt ? dueAt + 'T12:00:00' : undefined,
        advance_stage: advance,
      })
      onRecorded && onRecorded()
    } catch (e) {
      setError(e.message || 'Could not record that outcome.')
    } finally {
      setBusy(false)
    }
  }

  const isFuture = appt?.starts_at && new Date(appt.starts_at + 'Z') > new Date()

  return (
    <div className="sw-modal-back"
         onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="sw-modal" style={{ maxWidth: 600 }}>
        <div className="sw-card-h">
          <div>
            <h3>RECORD WHAT HAPPENED</h3>
            <small>
              {appt?.meeting_type || appt?.title}
              {appt?.starts_at_local ? ' · ' + dayFromYmd(
                String(appt.starts_at_local).slice(0, 10)).toLocaleDateString(
                undefined, { weekday: 'short', month: 'short', day: 'numeric' }) : ''}
              {appt?.starts_at_local ? ' ' + wallTime(appt.starts_at_local) : ''}
            </small>
          </div>
          <div className="sw-spacer" />
          <button className="sw-btn" onClick={onClose}>Close</button>
        </div>

        <div className="sw-card-b">
          <ErrorBar error={error} />

          {/* Said before anybody clicks, rather than after the server refuses.
              A rejection you could have been warned about reads as a bug. */}
          {isFuture && (
            <div className="sw-note">
              This meeting has not started yet, so only a cancellation or a
              reschedule can be recorded. Completion is recorded afterwards, by
              somebody who was in the room — that is what makes the number mean
              anything.
            </div>
          )}

          {!catalog && !error && <div className="sw-subtle">Loading…</div>}

          {catalog && (
            <>
              <div className="sw-field">
                <label>OUTCOME</label>
                <div className="sw-chips">
                  {catalog.options.map(o => (
                    <button key={o.value}
                            type="button"
                            className={'sw-chip'
                              + (outcome === o.value
                                ? (o.occurred ? ' sw-green' : ' sw-amber') : '')}
                            style={{
                              cursor: o.available ? 'pointer' : 'not-allowed',
                              opacity: o.available ? 1 : 0.45,
                            }}
                            disabled={!o.available}
                            title={o.available ? o.hint : o.unavailable_reason}
                            onClick={() => pick(o)}>
                      {o.label}
                    </button>
                  ))}
                </div>
                {chosen && (
                  <div className="sw-subtle" style={{ marginTop: 7 }}>{chosen.hint}</div>
                )}
                {/* The reason an option is unavailable, shown rather than
                    hidden — see the header note. */}
                {catalog.options.some(o => !o.available) && (
                  <div className="sw-subtle" style={{ marginTop: 5 }}>
                    {catalog.options.filter(o => !o.available)[0].unavailable_reason}
                  </div>
                )}
              </div>

              {/* Per-person attendance. Optional, and its absence means
                  "unknown" — never "everyone attended". */}
              {(appt.participants || []).length > 1 && (
                <div className="sw-field">
                  <label>WHO WAS ACTUALLY THERE — optional</label>
                  {(appt.participants || []).map(p => (
                    <div key={p.user_id} className="cal-row">
                      <span className="cal-row-t">{p.full_name}</span>
                      <div className="cal-row-m">
                        <small>{p.role_label || (p.is_required ? 'Required' : 'Optional')}</small>
                      </div>
                      <select className="sw-select" style={{ width: 140 }}
                              value={attendance[p.user_id] || 'unknown'}
                              onChange={e => setAttendance({
                                ...attendance,
                                [p.user_id]: e.target.value,
                              })}>
                        <option value="unknown">Not recorded</option>
                        <option value="attended">Attended</option>
                        <option value="no_show">Did not attend</option>
                        <option value="declined">Declined</option>
                      </select>
                    </div>
                  ))}
                </div>
              )}

              <div className="sw-field">
                <label>NOTES — what came of it</label>
                <textarea className="sw-input" rows={3} value={notes}
                          onChange={e => setNotes(e.target.value)}
                          placeholder="Internal. Never sent to the prospect." />
              </div>

              {appt.opportunity_id && (
                <div className="sw-grid-even">
                  <div className="sw-field">
                    <label>NEXT ACTION ON THE DEAL</label>
                    <input className="sw-input" value={nextAction}
                           onChange={e => setNextAction(e.target.value)}
                           placeholder="Leave blank to change nothing" />
                  </div>
                  <div className="sw-field">
                    <label>DUE</label>
                    <input className="sw-input" type="date" value={dueAt}
                           onChange={e => setDueAt(e.target.value)} />
                  </div>
                </div>
              )}

              {/* THE STAGE MOVE. Explicit, defaulted off, and only offered
                  when the server says this caller could actually perform it. */}
              {chosen?.suggested_stage && catalog.can_advance_stage && (
                <label className="cal-toggle" style={{ marginTop: 4 }}>
                  <input type="checkbox" checked={advance}
                         onChange={e => setAdvance(e.target.checked)} />
                  Also move the deal to <b>&nbsp;{chosen.suggested_stage_label}</b>
                </label>
              )}
              {chosen?.suggested_stage && !catalog.can_advance_stage && (
                <div className="sw-subtle" style={{ marginTop: 6 }}>
                  This outcome usually moves the deal to{' '}
                  {chosen.suggested_stage_label}. You do not have edit access to
                  this deal, so the outcome will be recorded and the stage left
                  alone.
                </div>
              )}

              {/* What is about to happen, in words, before it happens. */}
              {chosen && (
                <div className="sw-note" style={{ marginTop: 12 }}>
                  {chosen.occurred
                    ? 'Recorded as a meeting that TOOK PLACE. It stays on everyone’s calendar as history.'
                    : 'Recorded as a meeting that DID NOT take place. Everyone’s time is released, and the calendar copies are withdrawn.'}
                  {outcome === 'cancelled'
                    ? ' Participants’ calendar events will be cancelled.'
                    : ''}
                </div>
              )}

              <div className="sw-flex sw-mt" style={{ justifyContent: 'flex-end', gap: 8 }}>
                <button className="sw-btn" onClick={onClose}>Cancel</button>
                <button className="sw-btn sw-primary" onClick={submit}
                        disabled={busy || !outcome}>
                  {busy ? 'Recording…' : 'Record outcome'}
                </button>
              </div>

              {appt.outcome_state?.outcome && (
                <div className="sw-subtle" style={{ marginTop: 10 }}>
                  Currently recorded as{' '}
                  <Chip tone="blue">{appt.outcome_state.outcome_label}</Chip>.
                  Recording again replaces it, and the change is written to the
                  deal timeline.
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
