/* The negotiation ledger.
 *
 * "Show: offer amount, date, who made it, counteroffer history, current
 * position." Before Phase 3 a deal carried exactly one number — `proposed_offer`
 * — which was overwritten every time somebody moved. The history of a
 * negotiation was therefore unrecoverable, and "what did we last offer them"
 * could only be answered by asking the person who typed it.
 *
 * THREE THINGS THIS SCREEN WILL NOT DO:
 *
 * 1. It does not send anything. Recording a move is bookkeeping; presenting an
 *    offer to a seller is a person's job, and the approval gate on the
 *    `offer_sent` stage is untouched by anything here.
 * 2. It does not hide a number above the MAO. The maximum allowable offer in
 *    force is shown beside the amount box as it is typed, and the row keeps the
 *    MAO AS IT WAS when the offer was made — so a later change to the repair
 *    estimate cannot retroactively make a bad offer look disciplined.
 * 3. It does not treat a seller's counter as our position. Their number is
 *    theirs; ours is the last thing we said.
 */
import { useMemo, useState } from 'react'
import { api } from '../../api/client'
import { fmtWhen, Note, Why } from './wsShared'
import {
  OFFER_STATUS_LABEL, SELLER, US, amountText, classifyAmount, offerPosition,
  offerStatusText, sortOffers,
} from './wsOfferState'

const ACT_RECORD = 'offer:record'
const actStatus = (id) => `offer:status:${id}`

/* The vocabulary the server accepts, in the order a negotiation runs through
 * it. The words live in wsOfferState so the screen never prints a stored key. */
const STATUSES = Object.entries(OFFER_STATUS_LABEL)

function tone(status) {
  if (status === 'accepted') return 'is-ok'
  if (status === 'rejected' || status === 'withdrawn' || status === 'expired') return 'is-dnc'
  if (status === 'presented' || status === 'countered') return 'is-warn'
  return 'is-muted'
}


export function NegotiationLedger({ deal, offers: rawOffers, analysis, act, busy, isBusy }) {
  const offers = useMemo(() => sortOffers(rawOffers || []), [rawOffers])
  const pending = (key) => (isBusy ? isBusy(key) : !!busy)
  const [direction, setDirection] = useState(US)
  const [amount, setAmount] = useState('')
  const [notes, setNotes] = useState('')
  const [note, setNote] = useState(null)

  const mao = analysis?.max_allowable_offer ?? null
  const typedKind = amount.trim() === '' ? 'missing' : classifyAmount(amount.trim()).kind
  const typed = typedKind === 'ok' || typedKind === 'zero' ? Number(amount) : null
  const overMao = typed !== null && mao !== null && typed > mao && direction === US
  const amountBad = typedKind === 'invalid' || typedKind === 'zero'

  const position = useMemo(() => offerPosition(offers), [offers])

  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">
        <span>The negotiation ({offers.length})</span>
      </div>
      {position.accepted ? (
        <div className="ws-good" role="status">
          Accepted at {amountText(position.accepted.amount)}
          {position.accepted.responded_at ? ` on ${fmtWhen(position.accepted.responded_at)}` : ''}.
          Acceptance is recorded here only; it is not a signed contract.
        </div>
      ) : null}
      <Note>
        Every move, in order, with the maximum allowable offer as it stood at the
        time.
      </Note>
      <Why label="What recording a number here does not do">
        <p className="ws-comp__sub">
          It does not present the number to the seller and it does not approve
          it. Both of those are separate, deliberate acts.
        </p>
      </Why>

      {/* Where the two sides currently stand, which is the question somebody
          picking the phone back up actually has. */}
      <div className="ws-position">
        <div className="ws-position__side">
          <span className="ws-k">Our last offer</span>
          <strong>{position.ours ? amountText(position.ours.amount) : '—'}</strong>
          <span className="ws-position__sub">
            {position.ours
              ? `${offerStatusText(position.ours.status)} · ${fmtWhen(position.ours.created_at) || 'date not recorded'}`
              : 'nothing offered yet'}
          </span>
        </div>
        <div className="ws-position__gap">
          <span className="ws-k">Gap</span>
          <strong className={position.gap !== null && position.gap > 0 ? 'is-apart' : ''}>
            {position.gap === null ? '—' : amountText(Math.abs(position.gap))}
          </strong>
          <span className="ws-position__sub">
            {position.gapNote}
          </span>
        </div>
        <div className="ws-position__side">
          <span className="ws-k">Their last counter</span>
          <strong>{position.theirs ? amountText(position.theirs.amount) : '—'}</strong>
          <span className="ws-position__sub">
            {position.theirs
              ? (fmtWhen(position.theirs.created_at) || 'date not recorded')
              : 'they have not countered'}
          </span>
        </div>
        <div className="ws-position__side">
          <span className="ws-k">Maximum allowable offer</span>
          <strong>{mao === null ? '—' : amountText(mao)}</strong>
          <span className="ws-position__sub">
            {mao === null ? 'not calculable yet — see Analysis'
                          : 'the most we can pay and keep the fee intact'}
          </span>
        </div>
      </div>

      {/* Record a move. */}
      <div className="ws-offer-entry">
        <div className="ws-field">
          <span className="ws-fieldlabel">Who moved</span>
          <div className="ws-toggle">
            <button type="button"
                    className={`ws-toggle__btn ${direction === US ? 'is-on' : ''}`}
                    onClick={() => setDirection(US)}>We offered</button>
            <button type="button"
                    className={`ws-toggle__btn ${direction === SELLER ? 'is-on' : ''}`}
                    onClick={() => setDirection(SELLER)}>Seller countered</button>
          </div>
        </div>
        <div className="ws-field">
          <label htmlFor="ws-offer-amount">Amount</label>
          <input id="ws-offer-amount" className="ws-input" inputMode="decimal"
                 value={amount} onChange={(e) => setAmount(e.target.value)}
                 placeholder={mao !== null && direction === US ? String(mao) : ''} />
        </div>
        <div className="ws-field ws-offer-entry__notes">
          <label htmlFor="ws-offer-notes">Notes</label>
          <input id="ws-offer-notes" className="ws-input" value={notes}
                 onChange={(e) => setNotes(e.target.value)}
                 placeholder="What was said, and by whom" />
        </div>
        <div className="ws-actions">
          <button className="btn btn--primary"
                  disabled={pending(ACT_RECORD) || amount.trim() === '' || amountBad}
                  aria-describedby="ws-offer-amount-hint"
                  onClick={async () => {
                    setNote(null)
                    const done = await act(async () => {
                      const res = await api.post(
                        `/wholesale/deals/${deal.id}/offers`,
                        { amount: Number(amount.trim()), direction,
                          notes: notes || null })
                      // The server says whether this number is inside the MAO.
                      // Kept verbatim rather than re-derived on this screen.
                      setNote(res && res.note ? res.note : null)
                    }, direction === US ? 'Offer recorded.' : 'Counter recorded.',
                    ACT_RECORD)
                    if (done) { setAmount(''); setNotes('') }
                  }}>
            Record {direction === US ? 'our offer' : 'their counter'}
          </button>
        </div>
      </div>

      <p id="ws-offer-amount-hint" className="ws-comp__sub" role="status">
        {amount.trim() === '' ? 'Enter an amount to record a move.'
          : typedKind === 'invalid' ? 'That is not a valid amount — use digits only, not negative.'
          : typedKind === 'zero' ? 'Zero is not recorded as an offer.'
          : pending(ACT_RECORD) ? 'Recording…' : ''}
      </p>

      {/* Before it is recorded, not after. */}
      {overMao ? (
        <div className="ws-warn">
          {amountText(typed)} is above the maximum allowable offer of {amountText(mao)}.
          You can still record it — a wholesaler may knowingly go over — but it
          will need an approval before the deal can move to Offer Sent.
        </div>
      ) : null}
      {note ? <div className="ws-warn">{note}</div> : null}

      {!offers.length ? (
        <p className="ws-panel-note" style={{ marginTop: 14 }}>
          Nothing has been offered or countered yet.
        </p>
      ) : (
        <div className="ws-scroll" style={{ marginTop: 14 }}>
          <table className="ws-table">
            <thead>
              <tr>
                <th>Move</th><th className="ws-num">Amount</th>
                <th className="ws-num">MAO at the time</th>
                <th>Status</th><th>Recorded</th><th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {offers.map((o) => {
                const over = (classifyAmount(o.mao_at_time).kind === 'ok'
                              && classifyAmount(o.amount).kind === 'ok'
                              && o.direction === US
                              && Number(o.amount) > Number(o.mao_at_time))
                return (
                  /* The rail on the left alternates colour with the party, so
                     the shape of the negotiation — who moved, how often, and
                     who moved last — is visible before a number is read. */
                  <tr key={o.id}
                      className={`ws-move-row ${o.direction === US ? 'is-us' : 'is-them'}`}>
                    <td>
                      <span className={`ws-move ${o.direction === US ? 'is-us' : 'is-them'}`}>
                        {o.direction === US ? 'We offered' : 'Seller countered'}
                      </span>
                    </td>
                    <td className="ws-num">
                      {amountText(o.amount)}
                      {over ? <span className="ws-delta is-over">over MAO</span> : null}
                    </td>
                    <td className="ws-num">
                      {classifyAmount(o.mao_at_time).kind === 'missing'
                        ? <span className="ws-muted">not calculable then</span>
                        : amountText(o.mao_at_time)}
                    </td>
                    <td>
                      <label className="ws-vis-hidden"
                             htmlFor={`ws-offer-status-${o.id}`}>
                        Change this offer&apos;s status
                      </label>
                      <select id={`ws-offer-status-${o.id}`}
                              className={`ws-input ws-input--inline ws-statusedit ${tone(o.status)}`}
                              value={o.status || ''} disabled={pending(actStatus(o.id))}
                              onChange={(e) => act(
                                () => api.patch(`/wholesale/offers/${o.id}`,
                                                { status: e.target.value }),
                                'Offer status updated.', actStatus(o.id))}>
                        {!OFFER_STATUS_LABEL[o.status]
                          ? <option value={o.status || ''} disabled>{offerStatusText(o.status)}</option>
                          : null}
                        {STATUSES.map(([key, label]) => (
                          <option key={key} value={key}>{label}</option>
                        ))}
                      </select>
                    </td>
                    <td>
                      {fmtWhen(o.created_at) || 'date not recorded'}
                      <div className="ws-comp__sub">
                        {o.created_by_actor === 'user' ? 'by a person'
                          : (o.created_by_actor || 'recorder not stated')}
                        {o.presented_at ? ` · presented ${fmtWhen(o.presented_at)}` : ''}
                        {o.responded_at ? ` · answered ${fmtWhen(o.responded_at)}` : ''}
                      </div>
                    </td>
                    <td>{o.notes || <span className="ws-muted">—</span>}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
