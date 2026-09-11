/**
 * SELL AN ADD-ON OR A SERVICE to a customer you already sold.
 *
 * The same engine the customer's own Billing page uses — a rep cannot produce
 * an outcome the customer could not have produced themselves. What differs is
 * the gate, and every part of the gate is answered by the server:
 *
 *   WHAT MAY BE SOLD          `offers` is the seller-assisted set, which is a
 *                             different list from the customer's own screen.
 *   WHETHER THIS REP MAY      `authority.may_price` says whether they may put
 *   PRICE QUOTED WORK         a figure on a quoted item. The control is
 *                             disabled when they may not, because being told
 *                             first is not the same experience as filling in a
 *                             number and then being refused.
 *   WHETHER AN ADD-ON CAN     `can_sell_addons` is false with no subscription
 *   ATTACH AT ALL             to attach to, and the reason is shown rather
 *                             than left for the rep to guess.
 *
 * NOTHING HERE PRICES A FIXED ITEM. The amount field appears only for quoted
 * work; the server refuses a figure against a fixed item either way, but a
 * screen that offers the box is a screen that invites the attempt.
 *
 * NO STRIPE IDENTIFIERS. The payment link is shown because it is the thing a
 * customer has to open; nothing else about the processor appears.
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import { Card, Chip, Empty, ErrorBar, money } from './parts'

function errText(e) {
  return (e && (e.detail || e.message)) || 'Something went wrong.'
}

const KIND_LABEL = {
  recurring_addon: 'Recurring add-on',
  one_time: 'One-time',
}

export default function SellCatalogue() {
  const { orgId } = useParams()
  const nav = useNavigate()

  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(null)
  const [notice, setNotice] = useState(null)

  // Per-item draft. Kept keyed by item so switching between two offers does
  // not carry one's quantity or quoted amount onto the other.
  const [draft, setDraft] = useState({})
  const [open, setOpen] = useState(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      setData(await api.get('/sales/catalog/customers/' + orgId))
    } catch (e) {
      setError(errText(e))
      setData(null)
    } finally { setLoading(false) }
  }, [orgId])

  useEffect(() => { load() }, [load])

  const offers = data?.offers || []
  const held = data?.held || []
  const authority = data?.authority || {}
  const canSellAddons = !!data?.can_sell_addons

  const setField = (key, field, value) =>
    setDraft(d => ({ ...d, [key]: { ...(d[key] || {}), [field]: value } }))

  async function sell(item) {
    const d = draft[item.key] || {}
    const body = { item: item.key, quantity: Number(d.quantity || 1) }
    if (item.pricing_mode === 'quoted') {
      const dollars = Number(d.amount)
      if (!dollars || dollars <= 0) {
        setNotice({ tone: 'bad', text: 'Enter the agreed amount for this deal.' })
        return
      }
      body.quoted_amount_cents = Math.round(dollars * 100)
    }
    if (d.note) body.note = d.note

    setBusy(item.key); setNotice(null)
    try {
      const r = await api.post('/sales/catalog/customers/' + orgId + '/sell', body)
      setNotice({
        tone: 'good',
        text: r.status === 'pending'
          ? `${r.item_name} — payment link created. Send it to the customer; nothing is charged until they pay.`
          : `${r.item_name} added to their subscription. It appears on their next invoice.`,
        link: r.checkout_url || null,
      })
      setOpen(null)
      setDraft(d2 => ({ ...d2, [item.key]: {} }))
      await load()
    } catch (e) {
      setNotice({ tone: 'bad', text: errText(e) })
    } finally { setBusy(null) }
  }

  async function resend(purchase) {
    setBusy(purchase.id); setNotice(null)
    try {
      const r = await api.post('/sales/catalog/purchases/' + purchase.id + '/resend')
      setNotice({
        tone: 'good',
        text: `${r.item_name} — this is the same link they were already sent, not a new one.`,
        link: r.checkout_url,
      })
    } catch (e) {
      setNotice({ tone: 'bad', text: errText(e) })
    } finally { setBusy(null) }
  }

  return (
    <SalesShell
      title={data ? ('Sell to ' + data.customer_name) : 'Sell'}
      subtitle="Add-ons and services, sold through the customer's existing billing."
      actions={
        <>
          <button className="sw-btn" onClick={() => nav('/sales/onboarding')}>
            Back
          </button>
          <button className="sw-btn" onClick={load} disabled={loading}>
            Refresh
          </button>
        </>
      }
    >
      <ErrorBar error={error} onRetry={load} />

      {notice ? (
        <div className={notice.tone === 'bad' ? 'sw-err' : 'sw-note'}
             style={{ marginBottom: 14 }}>
          <div>{notice.text}</div>
          {notice.link ? (
            <div style={{ marginTop: 6, wordBreak: 'break-all' }}>
              <a href={notice.link} target="_blank" rel="noreferrer">{notice.link}</a>
            </div>
          ) : null}
        </div>
      ) : null}

      {loading && !data ? <div className="sw-subtle">Loading…</div> : null}

      {data ? (
        <>
          <Card title="What they already have"
                sub="Everything on this customer's account besides their plan."
                bodyless>
            {!held.length ? (
              <div className="sw-card-b">
                <Empty title="Nothing yet">
                  This customer has bought no add-ons or services.
                </Empty>
              </div>
            ) : (
              <div className="sw-tablewrap">
                <table className="sw-table">
                  <thead>
                    <tr>
                      <th>Item</th>
                      <th>Type</th>
                      <th>Amount</th>
                      <th>Status</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {held.map(p => (
                      <tr key={p.id}>
                        <td>
                          <strong>{p.item_name}</strong>
                          {p.note ? <div className="sw-subtle">{p.note}</div> : null}
                        </td>
                        <td>{p.kind_label || KIND_LABEL[p.kind] || p.kind}</td>
                        <td>
                          {money(p.total_cents / 100)}
                          {p.is_recurring ? <span className="sw-subtle">
                            {' /' + (p.billing_interval || 'month')}</span> : null}
                          {p.quantity > 1
                            ? <div className="sw-subtle">{p.quantity} ×
                                {' ' + money(p.amount_cents / 100)}</div>
                            : null}
                        </td>
                        <td>
                          <Chip tone={p.status === 'pending' ? null : 'green'}>
                            {p.status === 'pending' ? 'awaiting payment' : p.status}
                          </Chip>
                        </td>
                        <td>
                          {p.status === 'pending' && p.checkout_url ? (
                            <button className="sw-tiny" disabled={busy === p.id}
                                    onClick={() => resend(p)}>
                              Get payment link
                            </button>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <div style={{ height: 16 }} />

          <Card title="What you can sell them"
                sub={authority.may_price
                  ? 'You can price quoted work on this deal.'
                  : 'Quoted work needs a sales manager to set the amount.'}>
            {!canSellAddons ? (
              <div className="sw-quote" style={{ marginBottom: 12 }}>
                This customer has no active subscription, so a recurring add-on
                has nothing to attach to. One-time services can still be sold.
              </div>
            ) : null}

            {!offers.length ? (
              <Empty title="Nothing is enabled for seller-assisted sale">
                Items are configured per brand in God Mode, and each one has to
                be turned on for sellers deliberately.
              </Empty>
            ) : (
              <div>
                {offers.map(item => {
                  const d = draft[item.key] || {}
                  const quoted = item.pricing_mode === 'quoted'
                  const isAddon = item.kind === 'recurring_addon'
                  const blocked =
                    item.already_held ? 'They already have this.'
                    : (isAddon && !canSellAddons)
                      ? 'No subscription to attach it to.'
                      : (quoted && !authority.may_price)
                        ? 'Needs a sales manager to price.'
                        : null

                  return (
                    <div key={item.key}
                         style={{ borderTop: '1px solid var(--sw-line2)' }}>
                    <div className="sw-attrow" style={{ padding: '12px 0' }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <strong>{item.name}</strong>
                        {' '}
                        <Chip>{item.kind_label || KIND_LABEL[item.kind] || item.kind}</Chip>
                        <div className="sw-subtle">
                          {quoted
                            ? 'Priced on the deal — there is no catalogue price.'
                            : (money(item.amount_cents / 100)
                               + (isAddon ? ' / ' + (item.billing_interval || 'month')
                                          : ' one-time'))}
                        </div>
                        {item.customer_description
                          ? <div className="sw-subtle">{item.customer_description}</div>
                          : null}
                        {blocked ? <div className="sw-subtle">{blocked}</div> : null}
                      </div>

                      <div>
                        {open === item.key ? (
                          <button className="sw-btn sw-ghost"
                                  onClick={() => setOpen(null)}>Cancel</button>
                        ) : (
                          <button className="sw-btn" disabled={!!blocked}
                                  onClick={() => { setOpen(item.key); setNotice(null) }}>
                            Sell
                          </button>
                        )}
                      </div>
                    </div>

                    {open === item.key ? (
                      <div style={{ padding: '0 0 14px' }}>
                        <div className="sw-appr-money">
                          <div className="sw-field">
                            <label>Quantity</label>
                            <input className="sw-input" type="number" min="1"
                                   value={d.quantity || 1}
                                   onChange={e => setField(item.key, 'quantity',
                                                           e.target.value)} />
                          </div>

                          {/* ONLY for quoted work. A box against a fixed item
                              invites an attempt the server refuses. */}
                          {quoted ? (
                            <div className="sw-field">
                              <label>Agreed amount ($)</label>
                              <input className="sw-input" type="number" min="0"
                                     step="0.01" value={d.amount || ''}
                                     placeholder="e.g. 2500"
                                     onChange={e => setField(item.key, 'amount',
                                                             e.target.value)} />
                            </div>
                          ) : (
                            <div className="sw-field">
                              <label>Price</label>
                              <div style={{ paddingTop: 8 }}>
                                {money(item.amount_cents / 100)}
                                <span className="sw-subtle"> — set in the
                                  catalogue and not negotiable here.</span>
                              </div>
                            </div>
                          )}

                          <div className="sw-field">
                            <label>Note (optional)</label>
                            <input className="sw-input" value={d.note || ''}
                                   placeholder="What this covers"
                                   onChange={e => setField(item.key, 'note',
                                                           e.target.value)} />
                          </div>
                        </div>

                        <div className="sw-quote">
                          {isAddon
                            ? 'This joins their existing subscription as a line on their next invoice. Nothing is charged right now.'
                            : 'This creates a payment link. Nothing is charged and nothing is owed until they pay it.'}
                        </div>

                        <div className="sw-appr-act">
                          <button className="sw-btn sw-primary"
                                  disabled={busy === item.key}
                                  onClick={() => sell(item)}>
                            {busy === item.key
                              ? 'Working…'
                              : (isAddon ? 'Add to their subscription'
                                         : 'Create payment link')}
                          </button>
                          <button className="sw-btn sw-ghost"
                                  onClick={() => setOpen(null)}>Cancel</button>
                        </div>
                      </div>
                    ) : null}
                    </div>
                  )
                })}
              </div>
            )}
          </Card>
        </>
      ) : null}
    </SalesShell>
  )
}
