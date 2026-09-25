/* Assignment economics, title, closing — and the two ways a deal ends.
 *
 * THE LEDGER IS THE POINT. A wholesaler's whole business is one subtraction,
 * and before Phase 3 the screen showed the inputs in three separate boxes and
 * left the reader to do it in their head. It is now written out:
 *
 *     what the buyer pays  −  what we pay  −  costs  =  what we expect
 *     what actually landed                           =  what we collected
 *
 * and the difference between the last two lines has its own row, because that
 * difference is the only thing anybody argues about after a closing.
 *
 * WHAT NOTHING HERE COMPUTES. The fee COLLECTED. It is a fact about money that
 * moved, a person types it, and a platform that derived it would be reporting
 * its own arithmetic as revenue.
 *
 * WHAT CLOSING DOES. It locks the economics. That is not obstruction — a fee
 * figure that a stray keystroke can alter is one nobody can stand behind at the
 * end of a quarter. A genuine correction is still possible and is a deliberate
 * act: it needs a sentence saying what is being corrected and why, and it
 * writes its own before-and-after into the audit history.
 *
 * ENDING A DEAL IS DELIBERATE, BOTH WAYS. Closing and losing both confirm, both
 * say what will happen, and losing takes a reason from a fixed list — free text
 * loses the ability to ask "how many did we lose on price", which is the
 * question that changes what somebody does next week.
 */
import { useState } from 'react'
import { api } from '../../api/client'
import { fmtMoney, fmtDate, fmtWhen, fmtLabel, Note, Why } from './wsShared'

const TITLE_STATUSES = [
  ['not_opened', 'Not opened'],
  ['opened', 'Opened'],
  ['title_search', 'Title search under way'],
  ['issue_found', 'Issue found'],
  ['clear_to_close', 'Clear to close'],
  ['closing_scheduled', 'Closing scheduled'],
  ['closed', 'Closed'],
  ['cancelled', 'Cancelled'],
]

const LOST_REASONS = [
  ['seller_changed_mind', 'Seller changed their mind'],
  ['price_too_high', 'Could not agree on price'],
  ['unable_to_contact', 'Could not reach the seller'],
  ['title_issue', 'Title problem'],
  ['property_condition', 'Property condition'],
  ['no_buyer', 'No buyer for it'],
  ['contract_expired', 'Contract expired'],
  ['seller_sold_elsewhere', 'Seller sold it to somebody else'],
  ['duplicate', 'Duplicate record'],
  ['bad_data', 'Bad data'],
  ['other', 'Something else'],
]

const CONTRACT_FIELDS = [
  ['contract_price', 'Contract price', 'money'],
  ['contract_date', 'Contract date', 'date'],
  ['seller_signed_at', 'Seller signed', 'date'],
  ['buyer_signed_at', 'We signed', 'date'],
  ['effective_date', 'Effective date', 'date'],
  ['earnest_money', 'Earnest money', 'money'],
  ['earnest_money_due', 'Earnest money due', 'date'],
  ['earnest_money_received_at', 'Earnest money received', 'date'],
  ['option_fee', 'Option fee', 'money'],
  ['inspection_deadline', 'Inspection deadline', 'date'],
  ['closing_deadline', 'Closing deadline', 'date'],
  ['close_of_escrow_target', 'Target close of escrow', 'date'],
]

const TITLE_FIELDS = [
  ['title_company', 'Title company', 'text'],
  ['title_file_number', 'Escrow / file number', 'text'],
  ['title_escrow_officer', 'Escrow officer', 'text'],
  ['title_contact', 'Other contact', 'text'],
  ['title_phone', 'Phone', 'text'],
  ['title_email', 'Email', 'text'],
  ['title_commitment_received_at', 'Commitment received', 'date'],
  ['closing_date', 'Closing date', 'date'],
  ['closing_time', 'Closing time', 'text'],
  ['closing_location', 'Where it closes', 'text'],
]

const MONEY_KEYS = new Set(['contract_price', 'earnest_money', 'option_fee'])

function draftFrom(deal, fields) {
  return fields.reduce((acc, [key]) => ({ ...acc, [key]: deal[key] ?? '' }), {})
}

/* An emptied box clears the stored value; skipping it would leave the old one
 * in place and the user would believe the edit saved. */
function payloadFrom(draft) {
  const out = {}
  Object.entries(draft).forEach(([key, value]) => {
    const text = typeof value === 'string' ? value.trim() : value
    if (text === '' || text === null || text === undefined) { out[key] = null; return }
    out[key] = MONEY_KEYS.has(key) ? Number(text) : text
  })
  return out
}

function Line({ label, value, note, sign, strong, tone, terminal }) {
  return (
    <div className={`ws-ledger__line ${strong ? 'is-strong' : ''} ${terminal ? 'is-terminal' : ''}`}>
      <span className="ws-ledger__sign">{sign || ''}</span>
      <span className="ws-ledger__label">
        {label}
        {note ? <span className="ws-ledger__note">{note}</span> : null}
      </span>
      <span className={`ws-ledger__value ${tone || ''}`}>{value}</span>
    </div>
  )
}


function Economics({ deal, act, busy }) {
  const [correcting, setCorrecting] = useState(false)
  const [correction, setCorrection] = useState({
    reason: '', contract_price: '', buyer_price: '',
    wholesale_fee_collected: '', other_costs: '',
  })

  const buyer = deal.buyer_price
  const contract = deal.contract_price
  const costs = deal.other_costs
  const gross = (buyer !== null && buyer !== undefined
                 && contract !== null && contract !== undefined)
    ? Number(buyer) - Number(contract) : null
  const expected = gross === null ? null : gross - Number(costs || 0)
  const collected = deal.wholesale_fee_collected
  const variance = (expected !== null && collected !== null && collected !== undefined)
    ? Number(collected) - expected : null

  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">
        <span>Assignment economics</span>
        {deal.economics_locked
          ? <span className="ws-pill is-muted">Locked at close</span> : null}
      </div>
      <p className="ws-panel-note">
        One subtraction, written out. The expected fee is arithmetic; the fee
        collected is a fact somebody types after the money moved.
      </p>

      <div className="ws-ledger">
        <Line label="The buyer pays" value={fmtMoney(buyer)}
              note={deal.buyer_selected_at
                ? `buyer chosen ${fmtWhen(deal.buyer_selected_at)}` : 'no buyer chosen yet'} />
        <Line sign="−" label="We pay the seller" value={fmtMoney(contract)}
              note={contract === null || contract === undefined
                ? 'no contract price on file' : 'the contract price'} />
        <Line sign="=" label="Gross assignment fee" value={fmtMoney(gross)} strong />
        <Line sign="−" label="Other costs" value={fmtMoney(costs)}
              note={costs === null || costs === undefined ? 'none recorded' : null} />
        <Line sign="=" label="Expected net fee" value={fmtMoney(expected)} strong
              tone={expected !== null && expected < 0 ? 'is-bad' : ''} />
        <Line label="Fee actually collected" terminal value={fmtMoney(collected)}
              note={collected === null || collected === undefined
                ? 'nothing collected yet — recorded at close' : 'typed by a person'}
              strong tone={collected !== null && collected !== undefined ? 'is-good' : ''} />
        {variance !== null && Math.abs(variance) >= 0.01 ? (
          <Line label="Difference from expected"
                value={`${variance > 0 ? '+' : ''}${fmtMoney(variance)}`}
                note="what landed against what the arithmetic said"
                tone={variance < 0 ? 'is-bad' : 'is-good'} />
        ) : null}
      </div>

      {deal.economics_locked ? (
        <>
          <div className="ws-notice" style={{ marginTop: 14 }}>
            This deal is closed, so its money is locked. A correction is still
            possible — it needs a sentence saying what is being corrected, and it
            is written into the audit history with the before and after.
          </div>
          {correcting ? (
            <div className="ws-respond" style={{ marginTop: 12 }}>
              <div className="ws-respond__head">Correct the economics</div>
              <div className="ws-field">
                <label htmlFor="ws-corr-reason">
                  What is being corrected, and why
                </label>
                <input id="ws-corr-reason" className="ws-input" value={correction.reason}
                       placeholder="e.g. Title deducted a $1,400 lien payoff at closing"
                       onChange={(e) => setCorrection(
                         (c) => ({ ...c, reason: e.target.value }))} />
              </div>
              <div className="ws-grid" style={{ marginTop: 10 }}>
                {[['contract_price', 'Contract price'],
                  ['buyer_price', 'Buyer price'],
                  ['wholesale_fee_collected', 'Fee collected'],
                  ['other_costs', 'Other costs']].map(([key, label]) => (
                  <div className="ws-field" key={key}>
                    <label htmlFor={`ws-corr-${key}`}>{label}</label>
                    <input id={`ws-corr-${key}`} className="ws-input" inputMode="decimal"
                           placeholder={String(deal[key] ?? '—')}
                           value={correction[key]}
                           onChange={(e) => setCorrection(
                             (c) => ({ ...c, [key]: e.target.value }))} />
                  </div>
                ))}
              </div>
              <p className="ws-panel-note">
                Leave a box empty to keep the figure it shows.
              </p>
              <div className="ws-actions" style={{ marginTop: 10 }}>
                <button className="btn btn--primary btn--sm"
                        disabled={busy || correction.reason.trim().length < 8}
                        onClick={async () => {
                          const body = { reason: correction.reason.trim() }
                          ;['contract_price', 'buyer_price',
                            'wholesale_fee_collected', 'other_costs'].forEach((k) => {
                            if (correction[k] !== '') body[k] = Number(correction[k])
                          })
                          const ok = await act(
                            () => api.post(
                              `/wholesale/deals/${deal.id}/economics-correction`, body),
                            'Correction recorded.')
                          if (ok) {
                            setCorrecting(false)
                            setCorrection({ reason: '', contract_price: '',
                                            buyer_price: '',
                                            wholesale_fee_collected: '',
                                            other_costs: '' })
                          }
                        }}>
                  Record the correction
                </button>
                <button className="btn btn--secondary btn--sm" disabled={busy}
                        onClick={() => setCorrecting(false)}>Cancel</button>
              </div>
            </div>
          ) : (
            <div className="ws-actions" style={{ marginTop: 12 }}>
              <button className="btn btn--secondary" disabled={busy}
                      onClick={() => setCorrecting(true)}>
                Correct the economics
              </button>
            </div>
          )}
        </>
      ) : null}
    </div>
  )
}


function FieldPanel({ title, note, fields, deal, endpoint, method, extra,
                     act, busy, disabled, disabledNote }) {
  const [draft, setDraft] = useState(() => draftFrom(deal, fields))
  const clean = JSON.stringify(draft) === JSON.stringify(draftFrom(deal, fields))

  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">{title}</div>
      {note ? <p className="ws-panel-note">{note}</p> : null}
      {disabled ? <div className="ws-notice">{disabledNote}</div> : null}
      <div className="ws-grid">
        {fields.map(([key, label, type]) => (
          <div className="ws-field" key={key}>
            <label htmlFor={`fp-${key}`}>{label}</label>
            <input id={`fp-${key}`} className="ws-input"
                   type={type === 'date' ? 'date' : 'text'}
                   inputMode={type === 'money' ? 'decimal' : undefined}
                   disabled={disabled}
                   value={draft[key] ?? ''}
                   onChange={(e) => setDraft(
                     (d) => ({ ...d, [key]: e.target.value }))} />
          </div>
        ))}
      </div>
      {extra ? extra({ draft, setDraft, disabled }) : null}
      <div className="ws-actions" style={{ marginTop: 12 }}>
        <button className="btn btn--primary" disabled={busy || clean || disabled}
                onClick={() => act(
                  () => (method === 'post' ? api.post : api.patch)(
                    endpoint, payloadFrom(draft)),
                  'Saved.')}>
          Save
        </button>
        <button className="btn btn--secondary" disabled={busy || clean}
                onClick={() => setDraft(draftFrom(deal, fields))}>
          Cancel
        </button>
      </div>
    </div>
  )
}


function Ending({ deal, act, busy }) {
  const [mode, setMode] = useState(null)      // 'close' | 'lost'
  const [fee, setFee] = useState('')
  const [closingDate, setClosingDate] = useState(deal.closing_date ?? '')
  const [note, setNote] = useState('')
  const [reason, setReason] = useState('')
  const [detail, setDetail] = useState('')

  const closed = !!deal.closed_at
  const dead = deal.deal_result === 'closed_lost'

  if (closed) {
    return (
      <div className="panel ws-panel">
        <div className="panel-title ws-panel-title">How this deal ended</div>
        <div className="ws-good">
          Closed {fmtWhen(deal.closed_at)} · fee collected{' '}
          <strong>{fmtMoney(deal.wholesale_fee_collected)}</strong>
        </div>
        <p className="ws-panel-note">
          The economics are locked. A correction goes through the panel above.
        </p>
      </div>
    )
  }

  if (dead) {
    return (
      <div className="panel ws-panel">
        <div className="panel-title ws-panel-title">How this deal ended</div>
        <div className="ws-warn">
          Marked lost — <strong>{fmtLabel(deal.lost_reason)}</strong>
          {deal.lost_reason_detail ? `: ${deal.lost_reason_detail}` : ''}
        </div>
      </div>
    )
  }

  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">Ending this deal</div>
      <Note>
        Two ways out, both deliberate. Closing records the money that moved and
        locks it.
      </Note>
      <Why label="Why a lost deal needs a reason from the list">
        <p className="ws-comp__sub">
          Free text loses the ability to ask “how many did we lose on price”,
          which is the question that changes what somebody does next quarter.
        </p>
      </Why>

      {mode === null ? (
        <div className="ws-actions">
          <button className="btn btn--primary" disabled={busy}
                  onClick={() => setMode('close')}>Close this deal</button>
          <button className="btn btn--secondary ws-btn-delete" disabled={busy}
                  onClick={() => setMode('lost')}>Mark this deal lost</button>
        </div>
      ) : null}

      {mode === 'close' ? (
        <div className="ws-respond">
          <div className="ws-respond__head">Close the deal</div>
          <div className="ws-grid">
            <div className="ws-field">
              <label htmlFor="ws-close-fee">
                Fee actually collected (leave blank if not yet paid)
              </label>
              <input id="ws-close-fee" className="ws-input" inputMode="decimal"
                     placeholder="Not collected yet"
                     value={fee} onChange={(e) => setFee(e.target.value)} />
            </div>
            <div className="ws-field">
              <label htmlFor="ws-close-date">Closing date</label>
              <input id="ws-close-date" className="ws-input" type="date"
                     value={closingDate}
                     onChange={(e) => setClosingDate(e.target.value)} />
            </div>
          </div>
          <div className="ws-field" style={{ marginTop: 10 }}>
            <label htmlFor="ws-close-note">Note</label>
            <input id="ws-close-note" className="ws-input" value={note}
                   onChange={(e) => setNote(e.target.value)} />
          </div>
          <div className="ws-confirm ws-confirm--decision" style={{ marginTop: 12 }}>
            <span className="ws-confirm__text">
              {fee === '' ? (
                <>
                  This closes the deal with <strong>no fee recorded</strong>.
                  It will read <strong>Closed — payment pending</strong> until
                  somebody records the money that actually landed, and it will
                  not count as collected revenue anywhere until then.
                </>
              ) : (
                <>
                  This records <strong>{fmtMoney(Number(fee))}</strong> as
                  collected and moves the deal to Closed.
                </>
              )}
              {' '}It <strong>locks the economics</strong>: after this,
              changing the money needs a written correction.
            </span>
            <span className="ws-actions">
              <button className="btn btn--secondary btn--sm" disabled={busy}
                      onClick={() => setMode(null)}>Cancel</button>
              <button className="btn btn--primary btn--sm" disabled={busy}
                      onClick={async () => {
                        const ok = await act(
                          () => api.post(`/wholesale/deals/${deal.id}/close`, {
                            wholesale_fee_collected: fee === '' ? null : Number(fee),
                            closing_date: closingDate || null,
                            deal_result: 'closed_won',
                            note: note || null,
                          }), fee === ''
                            ? 'Closed. Payment is still outstanding.'
                            : 'Deal closed and the fee recorded.')
                        if (ok) setMode(null)
                      }}>
                {fee === '' ? 'Close — payment pending' : 'Close and record the fee'}
              </button>
            </span>
          </div>
        </div>
      ) : null}

      {mode === 'lost' ? (
        <div className="ws-respond">
          <div className="ws-respond__head">Mark this deal lost</div>
          <div className="ws-grid">
            <div className="ws-field">
              <label htmlFor="ws-lost-reason">Why it died</label>
              <select id="ws-lost-reason" className="ws-input" value={reason}
                      onChange={(e) => setReason(e.target.value)}>
                <option value="">Pick a reason…</option>
                {LOST_REASONS.map(([k, label]) => (
                  <option key={k} value={k}>{label}</option>
                ))}
              </select>
            </div>
            <div className="ws-field">
              <label htmlFor="ws-lost-detail">Anything worth remembering</label>
              <input id="ws-lost-detail" className="ws-input" value={detail}
                     onChange={(e) => setDetail(e.target.value)} />
            </div>
          </div>
          <div className="ws-confirm" style={{ marginTop: 12 }}>
            <span className="ws-confirm__text">
              This moves the deal to <strong>Dead / Lost</strong> and stops any
              seller cadence running on it. The record is kept.
            </span>
            <span className="ws-actions">
              <button className="btn btn--secondary btn--sm" disabled={busy}
                      onClick={() => setMode(null)}>Cancel</button>
              <button className="btn btn--danger btn--sm" disabled={busy || !reason}
                      onClick={async () => {
                        const ok = await act(
                          () => api.post(`/wholesale/deals/${deal.id}/lost`,
                                         { reason, detail: detail || null }),
                          'Deal marked lost.')
                        if (ok) setMode(null)
                      }}>
                Mark it lost
              </button>
            </span>
          </div>
        </div>
      ) : null}
    </div>
  )
}


/* ── GETTING PAID ──────────────────────────────────────────────────────────
 *
 * CLOSED AND PAID ARE TWO FACTS, and this panel exists because the module
 * previously had room for only one of them. A deal could not close without a
 * fee typed against it, which forced a guess on the day the wire had not
 * arrived — and a guessed fee is indistinguishable in the database from a real
 * one. Now a deal can close with nothing collected, it says so in those words,
 * and the money is recorded here when it lands.
 *
 * NOTHING ON THIS PANEL IS COMPUTED. The amount is typed by a person, the date
 * is the date they say, and the difference from the expected fee is shown
 * rather than smoothed away, because that difference is the only number
 * anybody argues about after a closing.
 */
function Payment({ deal, act, busy }) {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({
    amount: '', collected_date: '', method: '', reference: '', note: '',
  })
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }))

  const state = deal.payment_state || 'not_closed'
  const checklist = deal.closing_checklist || { missing: [], complete: true }
  const expected = deal.assignment_fee
  const collected = deal.wholesale_fee_collected
  const variance = (collected !== null && collected !== undefined
                    && expected !== null && expected !== undefined)
    ? Number(collected) - Number(expected) : null

  const stateText = {
    not_closed: 'Not closed yet',
    payment_pending: 'Closed — payment pending',
    fee_collected: 'Closed — fee collected',
  }[state]
  const stateTone = { not_closed: 'is-muted', payment_pending: 'is-warn',
                      fee_collected: 'is-ok' }[state]

  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">
        <span>Getting paid</span>
        <span className={`ws-pill ${stateTone}`}>{stateText}</span>
      </div>

      {/* The gap, named. This does not block anything — a legitimate deal
          closes with a piece missing and somebody chasing it — it refuses to
          let the gap be invisible. */}
      {checklist.missing?.length ? (
        <div className="ws-notice">
          Still missing before this closing is complete on paper:{' '}
          <strong>{checklist.missing.join(', ')}</strong>.
        </div>
      ) : null}

      <div className="ws-kv" style={{ marginTop: 12 }}>
        <div>
          <div className="ws-k">Fee expected</div>
          <div className="ws-v">{fmtMoney(expected)}</div>
        </div>
        <div>
          <div className="ws-k">Fee collected</div>
          <div className={`ws-v ${collected != null ? 'ws-collected' : ''}`}>
            {collected != null ? fmtMoney(collected)
              : <span className="ws-muted">nothing yet</span>}
          </div>
        </div>
        {variance !== null && Math.abs(variance) >= 0.01 ? (
          <div>
            <div className="ws-k">Difference</div>
            <div className={`ws-v ${variance < 0 ? 'ws-short' : 'ws-collected'}`}>
              {variance > 0 ? '+' : ''}{fmtMoney(variance)}
            </div>
          </div>
        ) : null}
        <div>
          <div className="ws-k">Collected on</div>
          <div className="ws-v">{deal.fee_collected_at
            ? fmtWhen(deal.fee_collected_at) : <span className="ws-muted">—</span>}</div>
        </div>
        <div>
          <div className="ws-k">How</div>
          <div className="ws-v">{deal.fee_payment_method
            || <span className="ws-muted">—</span>}</div>
        </div>
        <div>
          <div className="ws-k">Reference</div>
          <div className="ws-v">{deal.fee_payment_reference
            || <span className="ws-muted">—</span>}</div>
        </div>
      </div>

      {deal.fee_variance_note ? (
        <Note>{deal.fee_variance_note}</Note>
      ) : null}

      {!open ? (
        <div className="ws-actions" style={{ marginTop: 14 }}>
          <button className="btn btn--primary" disabled={busy}
                  onClick={() => setOpen(true)}>
            {collected != null ? 'Correct the collected fee'
              : 'Record the fee that landed'}
          </button>
        </div>
      ) : (
        <div className="ws-respond" style={{ marginTop: 12 }}>
          <div className="ws-respond__head">Record the money that landed</div>
          <div className="ws-grid">
            <div className="ws-field">
              <label htmlFor="pay-amt">Amount collected</label>
              <input id="pay-amt" className="ws-input" inputMode="decimal"
                     value={form.amount}
                     onChange={(e) => set('amount', e.target.value)} />
            </div>
            <div className="ws-field">
              <label htmlFor="pay-date">Date it landed</label>
              <input id="pay-date" className="ws-input" type="date"
                     value={form.collected_date}
                     onChange={(e) => set('collected_date', e.target.value)} />
            </div>
            <div className="ws-field">
              <label htmlFor="pay-method">How</label>
              <input id="pay-method" className="ws-input" placeholder="Wire, check…"
                     value={form.method}
                     onChange={(e) => set('method', e.target.value)} />
            </div>
            <div className="ws-field">
              <label htmlFor="pay-ref">Reference</label>
              <input id="pay-ref" className="ws-input"
                     placeholder="Wire reference, check number"
                     value={form.reference}
                     onChange={(e) => set('reference', e.target.value)} />
            </div>
          </div>
          <div className="ws-field" style={{ marginTop: 10 }}>
            <label htmlFor="pay-note">
              If it differs from the expected fee, why
            </label>
            <input id="pay-note" className="ws-input" value={form.note}
                   onChange={(e) => set('note', e.target.value)} />
          </div>
          <div className="ws-actions" style={{ marginTop: 12 }}>
            <button className="btn btn--secondary" disabled={busy}
                    onClick={() => setOpen(false)}>Cancel</button>
            <button className="btn btn--primary"
                    disabled={busy || form.amount.trim() === ''}
                    onClick={async () => {
                      const done = await act(
                        () => api.post(`/wholesale/deals/${deal.id}/fee-collected`, {
                          amount: Number(form.amount),
                          collected_date: form.collected_date || null,
                          method: form.method || null,
                          reference: form.reference || null,
                          note: form.note || null,
                        }), 'Collected fee recorded.')
                      if (done) setOpen(false)
                    }}>
              Record it
            </button>
          </div>
          <Why label="What this does and does not do">
            <p className="ws-comp__sub">
              This is the only place collected revenue is set, and it always
              names the person who set it. The Command Center counts this
              figure and nothing else: an expected fee on an open deal is
              pipeline, not income.
            </p>
          </Why>
        </div>
      )}
    </div>
  )
}


export function ClosingWorkspace({ deal, matches, act, busy }) {
  const [assign, setAssign] = useState({
    buyer_id: deal.assigned_buyer_id ?? '',
    buyer_price: deal.buyer_price ?? '',
    assignment_fee: deal.assignment_fee ?? '',
  })

  return (
    <>
      <Economics deal={deal} act={act} busy={busy} />

      <Payment deal={deal} act={act} busy={busy} />

      <div className="panel ws-panel">
        <div className="panel-title ws-panel-title">Assignment</div>
        <Note>Who the deal goes to and at what price. It signs nothing.</Note>
        <Why label="What still has to happen after this">
          <p className="ws-comp__sub">
            The assignment agreement lives in the documents tab and is executed
            by people. Moving to Assignment Pending still needs an approved
            assignment while that gate is on.
          </p>
        </Why>
        {deal.economics_locked ? (
          <div className="ws-notice">
            Closed, so the assignment is fixed. Use the correction above.
          </div>
        ) : null}
        <div className="ws-grid">
          <div className="ws-field">
            <label htmlFor="ws-assign-buyer">Buyer</label>
            <select id="ws-assign-buyer" className="ws-input" value={assign.buyer_id}
                    disabled={deal.economics_locked}
                    onChange={(e) => setAssign(
                      (a) => ({ ...a, buyer_id: e.target.value }))}>
              <option value="">Choose…</option>
              {matches.filter((m) => !m.disqualified).map((m) => (
                <option key={m.buyer_id} value={m.buyer_id}>
                  {m.buyer_name} — {m.score}%
                </option>
              ))}
            </select>
          </div>
          <div className="ws-field">
            <label htmlFor="ws-assign-price">Buyer price</label>
            <input id="ws-assign-price" className="ws-input" inputMode="decimal"
                   disabled={deal.economics_locked} value={assign.buyer_price}
                   onChange={(e) => setAssign(
                     (a) => ({ ...a, buyer_price: e.target.value }))} />
          </div>
          <div className="ws-field">
            <label htmlFor="ws-assign-fee">Assignment fee</label>
            <input id="ws-assign-fee" className="ws-input" inputMode="decimal"
                   disabled={deal.economics_locked} value={assign.assignment_fee}
                   onChange={(e) => setAssign(
                     (a) => ({ ...a, assignment_fee: e.target.value }))} />
            <span className="ws-hint">
              Left blank, it is the spread over the contract price.
            </span>
          </div>
        </div>
        <div className="ws-actions" style={{ marginTop: 12 }}>
          <button className="btn btn--primary"
                  disabled={busy || deal.economics_locked || !assign.buyer_id
                            || assign.buyer_price === ''}
                  onClick={() => act(
                    () => api.post(`/wholesale/deals/${deal.id}/assign`, {
                      buyer_id: assign.buyer_id,
                      buyer_price: Number(assign.buyer_price),
                      assignment_fee: assign.assignment_fee === ''
                        ? null : Number(assign.assignment_fee),
                    }), 'Assignment recorded.')}>
            Save assignment
          </button>
        </div>
      </div>

      <FieldPanel
        title="The contract" fields={CONTRACT_FIELDS} deal={deal}
        endpoint={`/wholesale/deals/${deal.id}/contract`} method="patch"
        act={act} busy={busy}
        disabled={deal.economics_locked}
        disabledNote="Closed, so the contract price is fixed. Use the correction above."
        note={'"Signed" is two events and an option period runs from the later '
              + 'one, so the dates are kept apart rather than collapsed into a '
              + 'single signature date.'} />

      <FieldPanel
        title="Title and closing" fields={TITLE_FIELDS} deal={deal}
        endpoint={`/wholesale/deals/${deal.id}/title`} method="patch"
        act={act} busy={busy}
        note="What the title company asks for, and where the closing happens."
        extra={({ draft, setDraft, disabled }) => (
          <>
            {/* On its own in a grid it stretched the full panel width, which
                made one dropdown look like the most important control here. */}
            <div className="ws-grid" style={{ marginTop: 10 }}>
              <div className="ws-field" style={{ maxWidth: 280 }}>
                <label htmlFor="ws-title-status">Where title stands</label>
                <select id="ws-title-status" className="ws-input" disabled={disabled}
                        value={draft.title_status ?? (deal.title_status || 'not_opened')}
                        onChange={(e) => setDraft(
                          (d) => ({ ...d, title_status: e.target.value }))}>
                  {TITLE_STATUSES.map(([k, label]) => (
                    <option key={k} value={k}>{label}</option>
                  ))}
                  {/* Rows written before Phase 3 use these two spellings; the
                      screen shows what is stored rather than silently
                      re-labelling a value nobody chose. */}
                  {['clear', 'issue'].includes(deal.title_status) ? (
                    <option value={deal.title_status}>
                      {fmtLabel(deal.title_status)} (recorded earlier)
                    </option>
                  ) : null}
                </select>
              </div>
            </div>
            <div className="ws-field" style={{ marginTop: 10 }}>
              <label htmlFor="ws-title-issues">Title issues</label>
              <textarea id="ws-title-issues" className="ws-input" rows={2}
                        disabled={disabled}
                        value={draft.title_issues ?? (deal.title_issues || '')}
                        onChange={(e) => setDraft(
                          (d) => ({ ...d, title_issues: e.target.value }))} />
            </div>
            {deal.title_opened_at ? (
              <p className="ws-panel-note">
                Title opened {fmtDate(deal.title_opened_at)}.
              </p>
            ) : null}
          </>
        )} />

      <Ending deal={deal} act={act} busy={busy} />
    </>
  )
}
