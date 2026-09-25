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
import { fmtMoney, fmtWhen, Note, Why } from './wsShared'

const US = 'us'
const SELLER = 'seller'

/* The vocabulary the server accepts, in the order a negotiation runs through
 * it. Kept as pairs so the screen never prints a stored key. */
const STATUSES = [
  ['draft', 'Draft — written down, not presented'],
  ['approval_pending', 'Waiting on an approval'],
  ['approved', 'Approved internally'],
  ['presented', 'Presented to the seller'],
  ['countered', 'Countered'],
  ['accepted', 'Accepted'],
  ['rejected', 'Rejected'],
  ['expired', 'Expired'],
  ['withdrawn', 'Withdrawn'],
]

const STATUS_LABEL = Object.fromEntries(STATUSES)

function tone(status) {
  if (status === 'accepted') return 'is-ok'
  if (status === 'rejected' || status === 'withdrawn' || status === 'expired') return 'is-dnc'
  if (status === 'presented' || status === 'countered') return 'is-warn'
  return 'is-muted'
}


export function NegotiationLedger({ deal, offers, analysis, act, busy }) {
  const [direction, setDirection] = useState(US)
  const [amount, setAmount] = useState('')
  const [notes, setNotes] = useState('')
  const [note, setNote] = useState(null)

  const mao = analysis?.max_allowable_offer ?? null
  const typed = amount === '' ? null : Number(amount)
  const overMao = typed !== null && !Number.isNaN(typed)
                  && mao !== null && typed > mao && direction === US

  const position = useMemo(() => {
    const ours = [...offers].reverse().find((o) => o.direction === US)
    const theirs = [...offers].reverse().find((o) => o.direction === SELLER)
    const gap = (ours && theirs && ours.amount !== null && theirs.amount !== null)
      ? Number(theirs.amount) - Number(ours.amount) : null
    return { ours, theirs, gap }
  }, [offers])

  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">
        <span>The negotiation ({offers.length})</span>
      </div>
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
          <strong>{position.ours ? fmtMoney(position.ours.amount) : '—'}</strong>
          <span className="ws-position__sub">
            {position.ours
              ? `${STATUS_LABEL[position.ours.status] || position.ours.status} · ${fmtWhen(position.ours.created_at)}`
              : 'nothing offered yet'}
          </span>
        </div>
        <div className="ws-position__gap">
          <span className="ws-k">Gap</span>
          <strong className={position.gap !== null && position.gap > 0 ? 'is-apart' : ''}>
            {position.gap === null ? '—' : fmtMoney(Math.abs(position.gap))}
          </strong>
          <span className="ws-position__sub">
            {position.gap === null ? 'needs a number from both sides'
              : (position.gap > 0 ? 'they are asking more than we offered'
                                  : 'their number is at or below ours')}
          </span>
        </div>
        <div className="ws-position__side">
          <span className="ws-k">Their last counter</span>
          <strong>{position.theirs ? fmtMoney(position.theirs.amount) : '—'}</strong>
          <span className="ws-position__sub">
            {position.theirs
              ? fmtWhen(position.theirs.created_at)
              : 'they have not countered'}
          </span>
        </div>
        <div className="ws-position__side">
          <span className="ws-k">Maximum allowable offer</span>
          <strong>{fmtMoney(mao)}</strong>
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
          <button className="btn btn--primary" disabled={busy || amount === ''}
                  onClick={async () => {
                    setNote(null)
                    const done = await act(async () => {
                      const res = await api.post(
                        `/wholesale/deals/${deal.id}/offers`,
                        { amount: Number(amount), direction,
                          notes: notes || null })
                      // The server says whether this number is inside the MAO.
                      // Kept verbatim rather than re-derived on this screen.
                      setNote(res && res.note ? res.note : null)
                    }, direction === US ? 'Offer recorded.' : 'Counter recorded.')
                    if (done) { setAmount(''); setNotes('') }
                  }}>
            Record {direction === US ? 'our offer' : 'their counter'}
          </button>
        </div>
      </div>

      {/* Before it is recorded, not after. */}
      {overMao ? (
        <div className="ws-warn">
          {fmtMoney(typed)} is above the maximum allowable offer of {fmtMoney(mao)}.
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
                const over = (o.mao_at_time !== null && o.amount !== null
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
                      {fmtMoney(o.amount)}
                      {over ? <span className="ws-delta is-over">over MAO</span> : null}
                    </td>
                    <td className="ws-num">
                      {o.mao_at_time === null
                        ? <span className="ws-muted">not calculable then</span>
                        : fmtMoney(o.mao_at_time)}
                    </td>
                    <td>
                      <label className="ws-vis-hidden"
                             htmlFor={`ws-offer-status-${o.id}`}>
                        Change this offer&apos;s status
                      </label>
                      <select id={`ws-offer-status-${o.id}`}
                              className={`ws-input ws-input--inline ws-statusedit ${tone(o.status)}`}
                              value={o.status} disabled={busy}
                              onChange={(e) => act(
                                () => api.patch(`/wholesale/offers/${o.id}`,
                                                { status: e.target.value }),
                                'Offer status updated.')}>
                        {STATUSES.map(([key, label]) => (
                          <option key={key} value={key}>{label}</option>
                        ))}
                      </select>
                    </td>
                    <td>
                      {fmtWhen(o.created_at)}
                      <div className="ws-comp__sub">
                        {o.created_by_actor === 'user' ? 'by a person'
                          : (o.created_by_actor || 'unknown')}
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
