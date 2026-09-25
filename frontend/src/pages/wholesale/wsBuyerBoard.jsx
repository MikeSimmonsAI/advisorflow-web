/* The disposition board — every buyer on this deal, side by side.
 *
 * "The disposition decision is a COMPARISON and a person cannot make it from a
 * list of separate rows they have to hold in their head." So this is one table
 * with the four things the decision actually turns on in adjacent columns:
 *
 *     WHAT THEY OFFERED · WHAT WE MAKE · CAN THEY PAY · HOW FAST THEY CLOSE
 *
 * NOTHING HERE RANKS OR RECOMMENDS. There is no "best buyer" badge, no sort by
 * offer, no highlight on the biggest number. The highest offer from somebody
 * with no proof of funds who has never closed is not the best buyer, and a
 * product that decided otherwise would lose deals on behalf of the person using
 * it. The facts are laid out; a human picks; the pick is stamped with their name.
 *
 * WHAT IT REFUSES TO IMPLY. `verified` proof of funds is a person's judgement —
 * nothing in this module reads a bank letter, and the control says so. A row
 * whose deal sheet never left the building says that plainly rather than sitting
 * quietly on "sent". And a buyer who phoned in an offer after a failed send is
 * a first-class case, not an edge one: the response form works regardless of
 * what the delivery status says.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { fmtMoney, fmtWhen, fmtLabel, errText, Note, Why } from './wsShared'
import { UploadZone, openFile } from './wsFiles'

const RESPONSE_STATUSES = [
  ['not_contacted', 'Not contacted'],
  ['sent', 'Sent'],
  ['delivered', 'Delivered'],
  ['opened', 'Opened'],
  ['replied', 'Replied'],
  ['interested', 'Interested'],
  ['needs_info', 'Needs more information'],
  ['offer_submitted', 'Offer submitted'],
  ['passed', 'Passed'],
  ['rejected', 'Rejected'],
  ['no_response', 'No response'],
]

/* Short, for the <select>, which sizes itself to its longest option and was
 * making proof of funds the widest column on the board. */
const POF_STATUSES = [
  ['not_requested', 'Not requested'],
  ['requested', 'Requested'],
  // THE BUYER SAID SO. Phase 6 added `claimed` to POF_STATUSES on the server —
  // it is what the deal room writes when an investor ticks "already on file
  // with you" — and this list was never updated. A row already sitting on
  // `claimed` therefore rendered a <select> with no matching <option>, which
  // shows blank and makes the operator's next change look like a correction
  // of something they never set. Between `requested` and `received` because
  // that is where it sits in reality: asked for, asserted, not seen.
  ['claimed', 'Claimed by buyer'],
  ['received', 'Received'],
  ['verified', 'Verified'],
  ['expired', 'Expired'],
  ['rejected', 'Rejected'],
]

/* What each one actually means, for the pill and for the selection
 * confirmation — where the difference between "a letter arrived" and "somebody
 * checked it" is the whole point. */
const POF_MEANING = {
  not_requested: 'Not requested',
  requested: 'Requested',
  // Deliberately says who said it. Nobody here has looked at a document.
  claimed: 'Buyer says it is on file — not seen',
  received: 'Received — not checked',
  verified: 'Verified by a person',
  expired: 'Expired',
  rejected: 'Rejected',
}

/* How the buyer says they are funding it, in the words the deal room offered
 * them. An unmapped value falls back to its own name — the vocabulary lives on
 * the server and this map must never silently relabel a value it does not
 * recognise. */
const FINANCING_LABEL = {
  cash: 'Cash',
  hard_money: 'Hard money',
  conventional: 'Conventional',
  other: 'Other funding',
}

const RESPONSE_LABEL = Object.fromEntries(RESPONSE_STATUSES)
const POF_LABEL = POF_MEANING

function statusTone(status) {
  if (['interested', 'offer_submitted', 'replied'].includes(status)) return 'is-ok'
  if (['passed', 'rejected', 'no_response', 'failed', 'blocked'].includes(status)) return 'is-dnc'
  if (['sent', 'delivered', 'opened'].includes(status)) return 'is-warn'
  return 'is-muted'
}

function pofTone(status) {
  if (status === 'verified') return 'is-ok'
  if (status === 'received') return 'is-warn'
  if (['rejected', 'expired'].includes(status)) return 'is-dnc'
  return 'is-muted'
}

/* Sent → delivered → opened → replied, as far as this deal actually got.
 * Written from timestamps that exist, never inferred from the status word. */
function Trail({ row }) {
  const steps = [
    ['Sent', row.sent_at],
    ['Delivered', row.delivered_at],
    ['Replied', row.replied_at],
  ].filter(([, at]) => at)
  if (!steps.length) {
    return (
      <span className="ws-muted ws-trail__none">
        {row.blocked_reason ? 'never sent' : 'not sent yet'}
      </span>
    )
  }
  return (
    <span className="ws-trail">
      {steps.map(([label, at]) => (
        <span key={label} className="ws-trail__step">
          {label} {fmtWhen(at)}
        </span>
      ))}
    </span>
  )
}


/* ── WHAT THE INVESTOR ACTUALLY SENT ──────────────────────────────────────
 *
 * Phase 6 taught the deal room to collect six things — amount, closing date,
 * financing, a proof-of-funds claim, who is answering and a note — and the
 * board rendered two of them. Three fields were validated, stored, returned
 * by the endpoint and shown on no screen, so an acquisitions assistant could
 * say "we will wire cash, call me on this number" and the operator saw an
 * amount and a date. That is the gap §11 of the 6.1 brief names.
 *
 * It is a SUB-ROW rather than four more columns because the table is already
 * seven wide and because this belongs UNDER the buyer it came from: the whole
 * point is that the person who answered may not be the person in the CRM, and
 * nesting says that without a sentence explaining it.
 *
 * Nothing is invented here. Every line renders only when the server sent a
 * value, and when the investor answered without giving their name the row
 * says the link was used rather than guessing who used it.
 */
function WhatTheySent({ row }) {
  const respondent = row.respondent_name || row.respondent_email
                     || row.respondent_phone
  const financing = row.offer_financing
  // Only when there is something here the row above does NOT already say.
  // The delivery trail already reports "Replied <when>" and the close column
  // already reports the target date, so a sub-row carrying only those would
  // be repetition dressed as detail.
  if (!respondent && !financing) return null

  // Did the person who answered differ from the contact in the buyer list?
  const crmName = (row.contact_name || '').trim().toLowerCase()
  const gaveName = (row.respondent_name || '').trim().toLowerCase()
  const differs = Boolean(gaveName) && Boolean(crmName) && gaveName !== crmName

  return (
    <tr className="ws-dispo__sub">
      <td colSpan={7}>
        <div className="ws-sent">
          <span className="ws-sent__tag">From their deal room link</span>

          {financing ? (
            <span className="ws-sent__item">
              <span className="ws-sent__k">Funding</span>
              <span className="ws-sent__v">
                {FINANCING_LABEL[financing] || fmtLabel(financing)}
              </span>
            </span>
          ) : null}

          {row.target_close_date ? (
            <span className="ws-sent__item">
              <span className="ws-sent__k">Wants to close by</span>
              <span className="ws-sent__v">{row.target_close_date}</span>
            </span>
          ) : null}

          {row.replied_at ? (
            <span className="ws-sent__item">
              <span className="ws-sent__k">Answered</span>
              <span className="ws-sent__v">{fmtWhen(row.replied_at)}</span>
            </span>
          ) : null}

          {respondent ? (
            <span className="ws-sent__item">
              <span className="ws-sent__k">
                {differs ? 'Answered by (not the listed contact)' : 'Answered by'}
              </span>
              <span className="ws-sent__v">
                {row.respondent_name || 'name not given'}
                {row.respondent_email ? (
                  <> · <a href={`mailto:${row.respondent_email}`}>
                    {row.respondent_email}</a></>
                ) : null}
                {row.respondent_phone ? (
                  <> · <a href={`tel:${row.respondent_phone.replace(/[^\d+]/g, '')}`}>
                    {row.respondent_phone}</a></>
                ) : null}
              </span>
            </span>
          ) : null}

          {/* Said once, where it is needed: these details are recorded against
              the RESPONSE and have not touched the buyer's own record. */}
          {differs ? (
            <span className="ws-sent__note">
              Recorded against this response only — {row.buyer_name
                || 'the buyer'}&rsquo;s contact details are unchanged.
            </span>
          ) : null}
        </div>
      </td>
    </tr>
  )
}


function ResponseForm({ row, busy, onSave, onCancel }) {
  const [draft, setDraft] = useState({
    status: row.status && RESPONSE_LABEL[row.status] ? row.status : 'replied',
    offer_amount: row.offer_amount ?? '',
    target_close_date: row.target_close_date ?? '',
    response_note: row.response_note ?? '',
  })

  return (
    <div className="ws-respond">
      <div className="ws-respond__head">
        What <strong>{row.buyer_name || 'this buyer'}</strong> said
      </div>
      <div className="ws-grid">
        <div className="ws-field">
          <label htmlFor={`resp-${row.outreach_id}-status`}>Where they stand</label>
          <select id={`resp-${row.outreach_id}-status`} className="ws-input"
                  value={draft.status}
                  onChange={(e) => setDraft((d) => ({ ...d, status: e.target.value }))}>
            {RESPONSE_STATUSES.map(([k, label]) => (
              <option key={k} value={k}>{label}</option>
            ))}
          </select>
        </div>
        <div className="ws-field">
          <label htmlFor={`resp-${row.outreach_id}-amount`}>Their offer</label>
          <input id={`resp-${row.outreach_id}-amount`} className="ws-input"
                 inputMode="decimal" value={draft.offer_amount}
                 onChange={(e) => setDraft(
                   (d) => ({ ...d, offer_amount: e.target.value }))} />
        </div>
        <div className="ws-field">
          <label htmlFor={`resp-${row.outreach_id}-close`}>Close by</label>
          <input id={`resp-${row.outreach_id}-close`} className="ws-input" type="date"
                 value={draft.target_close_date}
                 onChange={(e) => setDraft(
                   (d) => ({ ...d, target_close_date: e.target.value }))} />
        </div>
      </div>
      <div className="ws-field" style={{ marginTop: 10 }}>
        <label htmlFor={`resp-${row.outreach_id}-note`}>Note</label>
        <input id={`resp-${row.outreach_id}-note`} className="ws-input"
               placeholder="Said on the phone, in an email, by text — say which"
               value={draft.response_note}
               onChange={(e) => setDraft(
                 (d) => ({ ...d, response_note: e.target.value }))} />
      </div>
      <p className="ws-panel-note">
        Typed by whoever heard it. Most of a disposition arrives by phone, so
        nothing here waits for an integration to notice a reply.
      </p>
      <div className="ws-actions" style={{ marginTop: 10 }}>
        <button className="btn btn--primary btn--sm" disabled={busy}
                onClick={() => onSave({
                  status: draft.status,
                  offer_amount: draft.offer_amount === ''
                    ? null : Number(draft.offer_amount),
                  target_close_date: draft.target_close_date || null,
                  response_note: draft.response_note || null,
                })}>
          Save what they said
        </button>
        <button className="btn btn--secondary btn--sm" disabled={busy}
                onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}


export function BuyerBoard({ dealId, capability, act, busy }) {
  const [board, setBoard] = useState(null)
  const [error, setError] = useState(null)
  const [responding, setResponding] = useState(null)
  const [confirming, setConfirming] = useState(null)

  const load = useCallback(async () => {
    try {
      setBoard(await api.get(`/wholesale/deals/${dealId}/buyer-board`))
      setError(null)
    } catch (e) {
      setError(errText(e))
    }
  }, [dealId])

  useEffect(() => { load() }, [load])

  // Every action goes through the page's `act` so errors land in one place, and
  // then refreshes this board, which the deal room payload does not carry.
  async function run(fn, message) {
    const done = await act(fn, message)
    await load()
    return done
  }

  if (error) {
    return (
      <div className="panel ws-panel">
        <div className="panel-title ws-panel-title">Buyer comparison</div>
        <div className="ws-warn">{error}</div>
      </div>
    )
  }
  if (!board) {
    return (
      <div className="panel ws-panel">
        <div className="panel-title ws-panel-title">Buyer comparison</div>
        <p className="ws-panel-note">Loading…</p>
      </div>
    )
  }

  const rows = board.buyers || []
  const withOffers = rows.filter((r) => r.offer_amount !== null)

  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">
        <span>Buyer comparison ({rows.length})</span>
      </div>
      <Note>
        What each buyer offered, what we make on it, whether they can pay and how
        fast they close — in one row each.
      </Note>
      <Why label="Why nothing here is marked as the best offer">
        <p className="ws-comp__sub">
          The highest offer from a buyer with no proof of funds is not the same
          thing as the best buyer. This screen does not rank them, because that
          judgement is yours.
        </p>
      </Why>

      <div className="ws-dispo-summary">
        <div>
          <span className="ws-k">We are paying</span>
          <strong>{fmtMoney(board.contract_price)}</strong>
          <span className="ws-position__sub">
            {board.contract_price === null
              ? 'no contract price on file — the spread cannot be computed'
              : 'the contract price this spread is measured against'}
          </span>
        </div>
        <div>
          <span className="ws-k">Offers in</span>
          <strong>{withOffers.length} of {rows.length}</strong>
          <span className="ws-position__sub">
            {withOffers.length
              ? `highest ${fmtMoney(Math.max(...withOffers.map((r) => r.offer_amount)))}`
              : 'nobody has put a number on it yet'}
          </span>
        </div>
        <div>
          <span className="ws-k">Selected</span>
          <strong>
            {(rows.find((r) => r.is_selected) || {}).buyer_name || '—'}
          </strong>
          <span className="ws-position__sub">
            {rows.some((r) => r.is_selected)
              ? 'the assignment still needs its own approval'
              : 'nobody chosen yet'}
          </span>
        </div>
      </div>

      {!rows.length ? (
        <p className="ws-panel-note" style={{ marginTop: 14 }}>
          No buyer has been contacted on this deal yet. Run matching above, tick
          the buyers you want and send the deal sheet.
        </p>
      ) : (
        <div className="ws-scroll" style={{ marginTop: 14 }}>
          <table className="ws-table ws-dispo">
            <thead>
              <tr>
                <th>Buyer</th><th>Where it went</th>
                <th className="ws-num">Their offer</th>
                <th className="ws-num">Our spread</th>
                <th>Proof of funds</th>
                <th className="ws-num">Closes in</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <BoardRow key={r.outreach_id} row={r} busy={busy} run={run}
                          capability={capability}
                          responding={responding === r.outreach_id}
                          onRespond={() => setResponding(r.outreach_id)}
                          onDone={() => setResponding(null)}
                          onSelect={() => setConfirming(r)} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Choosing a buyer sets the money on the deal, so it is confirmed like a
          destructive act even though nothing is destroyed — and the question
          names the fee it implies rather than only the buyer. */}
      {confirming ? (
        <div className="ws-confirm ws-confirm--decision">
          <span className="ws-confirm__text">
            Select <strong>{confirming.buyer_name || 'this buyer'}</strong> at{' '}
            <strong>{fmtMoney(confirming.offer_amount)}</strong>?
            {confirming.spread !== null ? (
              <> That makes the assignment fee <strong>{fmtMoney(confirming.spread)}</strong>.</>
            ) : (
              <> There is no contract price on file, so the fee cannot be worked out yet.</>
            )}
            {confirming.pof_status !== 'verified' ? (
              <> Their proof of funds is <strong>{POF_LABEL[confirming.pof_status]
                 || confirming.pof_status}</strong>.</>
            ) : null}
            {' '}The assignment still needs its own approval.
          </span>
          <span className="ws-actions">
            <button className="btn btn--secondary btn--sm" disabled={busy}
                    onClick={() => setConfirming(null)}>Cancel</button>
            <button className="btn btn--primary btn--sm" disabled={busy}
                    onClick={async () => {
                      const ok = await run(
                        () => api.post(`/wholesale/deals/${dealId}/select-buyer`,
                                       { outreach_id: confirming.outreach_id }),
                        'Buyer selected.')
                      if (ok) setConfirming(null)
                    }}>
              Select this buyer
            </button>
          </span>
        </div>
      ) : null}
    </div>
  )
}


function BoardRow({ row, busy, run, capability, responding, onRespond, onDone,
                   onSelect }) {
  const [pofOpen, setPofOpen] = useState(false)

  return (
    <>
      <tr className={`ws-dispo__row ${row.is_selected ? 'is-selected' : ''}`}>
        <td>
          <div className="ws-comp__addr">
            {row.buyer_name || '(unnamed buyer)'}
            {row.is_selected ? <span className="ws-pill is-ok">Selected</span> : null}
          </div>
          <div className="ws-comp__sub">
            {row.contact_name || '—'}
            {row.match_score !== null && row.match_score !== undefined
              ? ` · match ${row.match_score}` : ''}
            {row.reliability_rating ? ` · rated ${row.reliability_rating}` : ''}
            {row.do_not_contact ? ' · OPTED OUT' : ''}
          </div>
        </td>
        <td>
          <span className={`ws-pill ${statusTone(row.status)}`}>
            {RESPONSE_LABEL[row.status] || fmtLabel(row.status)}
          </span>
          <div className="ws-comp__sub"><Trail row={row} /></div>
          {row.blocked_reason ? (
            <div className="ws-comp__sub ws-blocked">{row.blocked_reason}</div>
          ) : null}
          {row.response_note ? (
            <div className="ws-comp__sub">“{row.response_note}”</div>
          ) : null}
        </td>
        <td className="ws-num">
          {row.offer_amount === null
            ? <span className="ws-muted">no number yet</span>
            : fmtMoney(row.offer_amount)}
        </td>
        <td className="ws-num">
          {row.spread === null
            ? <span className="ws-muted">—</span>
            : (
              <strong className={row.spread > 0 ? 'ws-spread is-up' : 'ws-spread is-down'}>
                {fmtMoney(row.spread)}
              </strong>
            )}
        </td>
        <td>
          <span className={`ws-pill ${pofTone(row.pof_status)}`}>
            {POF_LABEL[row.pof_status] || fmtLabel(row.pof_status)}
          </span>
          <div className="ws-actions ws-actions--wrap" style={{ marginTop: 6 }}>
            <label className="ws-vis-hidden" htmlFor={`pof-${row.outreach_id}`}>
              Proof of funds status
            </label>
            <select id={`pof-${row.outreach_id}`}
                    className="ws-input ws-input--inline" value={row.pof_status}
                    disabled={busy}
                    onChange={(e) => run(
                      () => api.post(
                        `/wholesale/outreach/${row.outreach_id}/pof-status`,
                        { status: e.target.value }),
                      'Proof of funds updated.')}>
              {POF_STATUSES.map(([k, label]) => (
                <option key={k} value={k}>{label}</option>
              ))}
            </select>
            {row.pof_file_id ? (
              <button className="btn btn--secondary btn--sm"
                      onClick={() => openFile(`/wholesale/files/${row.pof_file_id}`)}>
                View letter
              </button>
            ) : null}
            <button className="btn btn--secondary btn--sm"
                    onClick={() => setPofOpen((v) => !v)}>
              {pofOpen ? 'Close' : (row.pof_file_id ? 'Replace' : 'Attach')}
            </button>
          </div>
        </td>
        <td className="ws-num">
          {row.typical_close_days ? `${row.typical_close_days} days` : '—'}
          {row.target_close_date ? (
            <div className="ws-comp__sub">by {row.target_close_date}</div>
          ) : null}
        </td>
        <td>
          <span className="ws-actions ws-actions--wrap">
            <button className="btn btn--secondary btn--sm" disabled={busy}
                    onClick={onRespond}>Record response</button>
            <button className="btn btn--secondary btn--sm" disabled={busy}
                    onClick={() => run(async () => {
                      const r = await api.post(
                        `/wholesale/outreach/${row.outreach_id}/resend`)
                      if (!r.sent) throw new Error(r.reason)
                    }, 'Resent.')}>Resend</button>
            {row.is_selected ? null : (
              <button className="btn btn--primary btn--sm"
                      disabled={busy || row.do_not_contact}
                      title={row.do_not_contact
                        ? 'This buyer has opted out of contact.' : undefined}
                      onClick={onSelect}>Select</button>
            )}
          </span>
        </td>
      </tr>

      {/* Everything the investor sent through their own link, under the buyer
          it belongs to. Renders nothing at all when nobody has answered. */}
      <WhatTheySent row={row} />

      {pofOpen ? (
        <tr><td colSpan={7}>
          <div className="ws-respond">
            <div className="ws-respond__head">
              Proof of funds for {row.buyer_name || 'this buyer'}
            </div>
            <Note tone="warn">
              Attaching a letter records that one arrived. It does not verify it.
            </Note>
            <Why label="What “verified” means here">
              <p className="ws-comp__sub">
                Nothing in this module reads a bank statement. Marking a buyer
                verified is a person saying they checked.
              </p>
            </Why>
            <UploadZone capability={capability} accept=".pdf,image/*"
                        label="Attach proof of funds" busy={busy}
                        onFiles={async (files) => {
                          const fd = new FormData()
                          fd.append('file', files[0])
                          const ok = await run(
                            () => api.upload(
                              `/wholesale/outreach/${row.outreach_id}/proof-of-funds`,
                              fd),
                            'Proof of funds attached.')
                          if (ok) setPofOpen(false)
                        }} />
          </div>
        </td></tr>
      ) : null}

      {responding ? (
        <tr><td colSpan={7}>
          <ResponseForm row={row} busy={busy} onCancel={onDone}
                        onSave={async (payload) => {
                          const ok = await run(
                            () => api.post(
                              `/wholesale/outreach/${row.outreach_id}/response`,
                              payload),
                            'Response recorded.')
                          if (ok) onDone()
                        }} />
        </td></tr>
      ) : null}
    </>
  )
}
