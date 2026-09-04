/**
 * P7 — BACK-OFFICE BILLING COMMAND CENTER.
 *
 * A DIFFERENT SURFACE FROM THE CUSTOMER BILLING PAGE, NOT A WIDER VERSION.
 * /billing answers "what is MY organization billed" for a customer standing in
 * their own workspace. This answers "what is happening to billing across every
 * organization" for EvoSys personnel, and the authority behind the two is
 * different in kind. Nothing here is imported from the customer page and
 * nothing here is reusable as it.
 *
 * THE OPERATOR NEVER SWITCHES THEIR OWN WORKSPACE. Selecting an organization
 * opens its detail under platform authority through /platform/billing/*, which
 * is the whole reason those endpoints exist: administering somebody else's
 * account by becoming them is the wrong mechanism.
 *
 * TWO RULES CARRIED FORWARD FROM P6, FOR THE SAME REASONS.
 *
 *   NO ARITHMETIC ON MONEY. Every figure rendered is a string the backend
 *   already formatted, or an integer count. Totals are summed server-side, per
 *   currency, because a dashboard that adds up money in the browser is a
 *   dashboard that eventually disagrees with the invoices behind it.
 *
 *   NO PAYMENT METHOD IS NAMED. The operator chooses a PURPOSE — a setup
 *   charge or a manual invoice — and Stripe decides which methods are eligible
 *   for the hosted invoice that results. An operator should not have to know
 *   what a payment method type is.
 */

import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import './GodOps.css'
import './BillingCommandCenter.css'

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'needs_attention', label: 'Needs attention' },
  { key: 'past_due', label: 'Past due' },
  { key: 'payment_failed', label: 'Payment failed' },
  { key: 'open_invoices', label: 'Open invoices' },
  { key: 'active_subscriptions', label: 'Active subscriptions' },
  { key: 'active', label: 'Active' },
  { key: 'no_agreement', label: 'No agreement' },
]

/* The backend's attention codes, said out loud. An unknown code renders from
 * its own name rather than vanishing — a new backend signal must degrade to
 * readable, never to invisible. */
const ATTENTION = {
  payment_failed: { label: 'Payment failed', tone: 'blocked' },
  invoice_overdue: { label: 'Invoice overdue', tone: 'blocked' },
  org_past_due: { label: 'Account past due', tone: 'blocked' },
  no_payment_method: { label: 'No payment method', tone: 'warn' },
  agreement_not_executed: { label: 'Agreement not executed', tone: 'warn' },
  subscription_without_agreement: { label: 'No agreement', tone: 'warn' },
  billing_not_configured: { label: 'Billing not configured', tone: 'warn' },
}

const INVOICE_TONE = { paid: 'live', open: 'warn', draft: 'new',
                       void: 'ready', uncollectible: 'blocked' }
const PAYMENT_TONE = { succeeded: 'live', failed: 'blocked', pending: 'warn',
                       refunded: 'ready', partially_refunded: 'ready' }

function titleize(v) {
  return String(v || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

function Badge({ value, tones = {}, labels = {} }) {
  if (!value) return <span className="go-dimmed">—</span>
  const conf = labels[value] || { label: titleize(value), tone: tones[value] || 'ready' }
  return <span className={`go-badge ${conf.tone || tones[value] || 'ready'}`}>
    {conf.label || titleize(value)}
  </span>
}

function formatDate(value) {
  if (value === null || value === undefined || value === '') return null
  const d = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

/* A per-currency total from the backend. Rendered as separate lines, never
 * added together: 100 USD plus 100 CAD is not a number. */
function Money({ totals, empty = '0.00' }) {
  if (!totals || totals.length === 0) return <>{empty}</>
  return (
    <>
      {totals.map((t, i) => (
        <span key={t.currency} className="bcc-money">
          {i > 0 ? <span className="go-dimmed"> · </span> : null}
          {t.amount} <small>{t.currency}</small>
        </span>
      ))}
    </>
  )
}

function Fact({ k, v, mono = false }) {
  return (
    <div className="go-fact">
      <div className="k">{k}</div>
      <div className={`v ${v === null || v === undefined || v === '' ? 'none' : ''}${mono ? ' bcc-mono' : ''}`}>
        {v === null || v === undefined || v === '' ? 'not set' : v}
      </div>
    </div>
  )
}

/* ═══════════════════════════════════════════════════════════════════════════
 * DASHBOARD
 * ═════════════════════════════════════════════════════════════════════════ */

function Dashboard({ data, onOpen }) {
  const c = data.counts
  const m = data.money
  const attention = data.needs_attention || []

  return (
    <>
      <div className="go-kpis">
        <div className="go-kpi">
          <div className="k">Organizations</div>
          <div className="v">{c.organizations}</div>
          <div className="s">{c.organizations_with_live_agreement} with a live agreement</div>
        </div>
        <div className="go-kpi">
          <div className="k">Active subscriptions</div>
          <div className="v">{c.active_subscriptions}</div>
          <div className="s">agreements executed at Stripe</div>
        </div>
        <div className={`go-kpi ${c.overdue_invoices ? 'alert' : ''}`}>
          <div className="k">Open invoices</div>
          <div className="v">{c.open_invoices}</div>
          <div className="s">{c.overdue_invoices} past their due date</div>
        </div>
        <div className={`go-kpi ${c.failed_payments ? 'alert' : ''}`}>
          <div className="k">Failed payments</div>
          <div className="v">{c.failed_payments}</div>
          <div className="s">{c.organizations_past_due} accounts past due</div>
        </div>
        <div className="go-kpi">
          <div className="k">Open invoice value</div>
          <div className="v bcc-kpi-money"><Money totals={m.open_invoice_total} /></div>
          <div className="s">billed and unpaid</div>
        </div>
        <div className={`go-kpi ${(m.overdue_total || []).length ? 'alert' : ''}`}>
          <div className="k">Overdue value</div>
          <div className="v bcc-kpi-money"><Money totals={m.overdue_total} /></div>
          <div className="s">past due date</div>
        </div>
        <div className="go-kpi good">
          <div className="k">Payments recorded</div>
          <div className="v bcc-kpi-money"><Money totals={m.payments_recorded} /></div>
          <div className="s">succeeded, all time</div>
        </div>
      </div>

      {/* CONTRACTED RECURRING, PER INTERVAL AND PER CURRENCY.
          Deliberately not labelled MRR or ARR: turning a mixed book of monthly
          and annual agreements in more than one currency into a single monthly
          number needs an FX rate and an annualisation rule that nothing here is
          entitled to invent. The narrower question, answered exactly, is worth
          more than the familiar one answered approximately. */}
      <section className="go-panel">
        <h2>Contracted recurring value</h2>
        <div className="go-body">
          {Object.keys(m.contracted_recurring || {}).length === 0 ? (
            <p className="go-dimmed bcc-note">No live agreements carry a recurring amount.</p>
          ) : (
            <div className="go-facts">
              {Object.entries(m.contracted_recurring).map(([interval, totals]) => (
                <Fact key={interval} k={`per ${interval}`}
                      v={<Money totals={totals} empty="—" />} />
              ))}
            </div>
          )}
          <p className="bcc-note go-dimmed">
            Summed from live BillingAgreements, per interval and per currency.
            Not normalised into a single figure — that would need an exchange
            rate and an annualisation rule this system has no authority to set.
          </p>
        </div>
      </section>

      <section className="go-panel">
        <h2>
          Needs attention
          <span className={`count ${attention.length ? 'hot' : ''}`}>{attention.length}</span>
        </h2>
        {attention.length === 0 ? (
          <div className="go-empty">Nothing needs attention. Every account is current.</div>
        ) : (
          <div className="bcc-scroll">
            <table className="go-table">
              <thead>
                <tr>
                  <th>Issue</th><th>Organization</th><th>Brand</th>
                  <th>Billed by</th><th className="num">Amount</th>
                  <th>Since</th><th>Detail</th><th />
                </tr>
              </thead>
              <tbody>
                {attention.map((row, i) => (
                  <tr key={`${row.code}-${row.target_id}-${i}`}>
                    <td><Badge value={row.code} labels={ATTENTION} /></td>
                    <td>{row.organization_name}</td>
                    <td className="go-dimmed">{row.brand_name || '—'}</td>
                    <td className="go-dimmed">{row.merchant_legal_name || '—'}</td>
                    <td className="num">
                      {row.amount ? `${row.amount} ${row.currency}` : '—'}
                    </td>
                    <td className="go-dimmed">{formatDate(row.since) || '—'}</td>
                    <td className="go-dimmed bcc-detail">{row.detail}</td>
                    <td>
                      <button className="go-btn ghost sm"
                              onClick={() => onOpen(row.organization_id)}>Open</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="bcc-two">
        <section className="go-panel">
          <h2>Recent invoices</h2>
          {(data.recent_invoices || []).length === 0 ? (
            <div className="go-empty">No invoices yet.</div>
          ) : (
            <div className="bcc-scroll">
              <table className="go-table">
                <thead><tr><th>Invoice</th><th>Organization</th>
                  <th className="num">Amount</th><th>Status</th></tr></thead>
                <tbody>
                  {data.recent_invoices.map(inv => (
                    <tr key={inv.id} className="clickable"
                        onClick={() => onOpen(inv.organization_id)}>
                      <td className="bcc-mono">{inv.number || '—'}</td>
                      <td>{inv.organization_name || '—'}</td>
                      <td className="num">{inv.total || '—'}</td>
                      <td><Badge value={inv.status} tones={INVOICE_TONE} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="go-panel">
          <h2>Recent payments</h2>
          {(data.recent_payments || []).length === 0 ? (
            <div className="go-empty">No payments recorded yet.</div>
          ) : (
            <div className="bcc-scroll">
              <table className="go-table">
                <thead><tr><th>Organization</th><th className="num">Amount</th>
                  <th>Method</th><th>Status</th></tr></thead>
                <tbody>
                  {data.recent_payments.map(p => (
                    <tr key={p.id} className="clickable"
                        onClick={() => onOpen(p.organization_id)}>
                      <td>{p.organization_name || '—'}</td>
                      <td className="num">{p.amount || '—'}</td>
                      {/* One backend-built string for every method type. This
                          does not know what a card is, which is the point. */}
                      <td className="go-dimmed">{p.payment_method_label || '—'}</td>
                      <td><Badge value={p.status} tones={PAYMENT_TONE} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </>
  )
}

/* ═══════════════════════════════════════════════════════════════════════════
 * ORGANIZATION LIST
 * ═════════════════════════════════════════════════════════════════════════ */

function OrgList({ rows, filter, query, onFilter, onQuery, onOpen, loading }) {
  return (
    <section className="go-panel">
      <h2>Organizations<span className="count">{rows.length}</span></h2>
      <div className="go-body">
        <div className="go-filters">
          <input className="go-input" placeholder="Search name or slug…"
                 value={query} onChange={e => onQuery(e.target.value)} />
          <select value={filter} onChange={e => onFilter(e.target.value)}>
            {FILTERS.map(f => <option key={f.key} value={f.key}>{f.label}</option>)}
          </select>
        </div>
      </div>
      {loading ? (
        <div className="go-empty">Loading organizations…</div>
      ) : rows.length === 0 ? (
        <div className="go-empty">
          No organizations match this filter.
        </div>
      ) : (
        <div className="bcc-scroll">
          <table className="go-table">
            <thead>
              <tr>
                <th>Organization</th><th>Brand</th><th>Billing</th>
                <th>Agreement</th><th className="num">Recurring</th>
                <th>Subscription</th><th className="num">Outstanding</th>
                <th className="num">Issues</th><th />
              </tr>
            </thead>
            <tbody>
              {rows.map(row => {
                const issues = row.failed_payment_count + row.overdue_invoice_count
                return (
                  <tr key={row.organization_id} className="clickable"
                      onClick={() => onOpen(row.organization_id)}>
                    <td>{row.organization_name}</td>
                    <td className="go-dimmed">{row.brand_name || '—'}</td>
                    <td>
                      {row.billing_status
                        ? <Badge value={row.billing_status}
                                 tones={{ active: 'live', past_due: 'blocked',
                                          trialing: 'new', canceled: 'ready' }} />
                        : <span className="go-dimmed">—</span>}
                    </td>
                    <td>
                      {row.has_agreement
                        ? <Badge value={row.agreement_status} tones={{ active: 'live', past_due: 'warn' }} />
                        : <span className="go-dimmed">none</span>}
                    </td>
                    <td className="num">
                      {row.recurring_amount
                        ? `${row.recurring_amount} ${row.currency || ''}/${row.billing_interval || 'mo'}`
                        : '—'}
                    </td>
                    <td>
                      {row.has_subscription
                        ? <span className="go-badge live">Running</span>
                        : <span className="go-badge warn">Not started</span>}
                    </td>
                    <td className="num"><Money totals={row.outstanding} empty="—" /></td>
                    <td className="num">
                      {issues ? <span className="go-badge blocked">{issues}</span>
                              : <span className="go-dimmed">—</span>}
                    </td>
                    <td><button className="go-btn ghost sm">Manage</button></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

/* ═══════════════════════════════════════════════════════════════════════════
 * ORGANIZATION DETAIL
 * ═════════════════════════════════════════════════════════════════════════ */

function NewInvoice({ orgId, onDone, onError }) {
  const [purpose, setPurpose] = useState('manual')
  const [description, setDescription] = useState('')
  const [days, setDays] = useState(30)
  const [lines, setLines] = useState([{ amount: '', description: '' }])
  const [busy, setBusy] = useState(false)

  function setLine(i, patch) {
    setLines(ls => ls.map((l, j) => (i === j ? { ...l, ...patch } : l)))
  }

  /* MINOR UNITS ARE PARSED, NOT CALCULATED. The operator types dollars; this
   * converts once, at the edge, with integer arithmetic on the digit string,
   * and the backend rejects anything that is not an integer anyway. No float
   * ever touches the value. */
  function toCents(text) {
    const t = String(text || '').trim().replace(/[$,\s]/g, '')
    if (!/^\d+(\.\d{1,2})?$/.test(t)) return null
    const [whole, frac = ''] = t.split('.')
    return parseInt(whole, 10) * 100 + parseInt((frac + '00').slice(0, 2), 10)
  }

  async function submit() {
    const items = []
    for (const l of lines) {
      const cents = toCents(l.amount)
      if (cents === null || cents <= 0) {
        onError('Every line needs an amount like 1500 or 1500.00.')
        return
      }
      items.push({ amount_cents: cents, description: l.description || null })
    }
    setBusy(true)
    try {
      const result = await api.post(
        `/platform/billing/organizations/${orgId}/invoices`,
        { line_items: items, description: description || null,
          days_until_due: Number(days) || 30, purpose })
      onDone(result)
      setLines([{ amount: '', description: '' }])
      setDescription('')
    } catch (err) {
      onError(err?.message || 'The invoice could not be created.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="go-body">
      <div className="go-fields">
        <div className="go-field">
          <label>Payment purpose</label>
          {/* THE OPERATOR CHOOSES A PURPOSE, NOT A STRIPE METHOD. Which
              methods are eligible on the resulting hosted invoice comes from
              the Stripe account's own payment method configuration. */}
          <select value={purpose} onChange={e => setPurpose(e.target.value)}>
            <option value="setup">Setup / implementation</option>
            <option value="manual">Manual invoice</option>
          </select>
          <div className="hint">
            Stripe presents whatever payment methods it considers eligible for
            this invoice. Nothing is chosen here.
          </div>
        </div>
        <div className="go-field">
          <label>Due in (days)</label>
          <input className="go-input" type="number" min="1" value={days}
                 onChange={e => setDays(e.target.value)} />
        </div>
        <div className="go-field full">
          <label>Invoice description</label>
          <input className="go-input" value={description}
                 onChange={e => setDescription(e.target.value)}
                 placeholder="What this invoice is for" />
        </div>
      </div>

      <p className="bcc-note go-dimmed">
        These are MANUAL line items. They do not read or change this
        organization's BillingAgreement, which remains the authority for
        recurring billing.
      </p>

      {lines.map((l, i) => (
        <div className="go-fields bcc-line" key={i}>
          <div className="go-field">
            <label>Amount</label>
            <input className="go-input" value={l.amount} placeholder="1500.00"
                   onChange={e => setLine(i, { amount: e.target.value })} />
          </div>
          <div className="go-field">
            <label>Line description</label>
            <input className="go-input" value={l.description}
                   placeholder="Implementation fee"
                   onChange={e => setLine(i, { description: e.target.value })} />
          </div>
        </div>
      ))}

      <div className="go-actions">
        <button className="go-btn ghost sm"
                onClick={() => setLines(ls => [...ls, { amount: '', description: '' }])}>
          Add line
        </button>
        {lines.length > 1 ? (
          <button className="go-btn ghost sm"
                  onClick={() => setLines(ls => ls.slice(0, -1))}>Remove line</button>
        ) : null}
        <button className="go-btn" disabled={busy} onClick={submit}>
          {busy ? 'Creating…' : 'Create draft invoice'}
        </button>
      </div>
    </div>
  )
}

function OrgDetail({ orgId, data, onBack, onReload, onNotice, notice, error }) {
  const [busy, setBusy] = useState(null)
  const [showNew, setShowNew] = useState(false)

  const act = useCallback(async (key, path, body) => {
    setBusy(key)
    onNotice(null)
    try {
      const result = await api.post(path, body)
      onNotice({ ok: true, text: 'Done.', result })
      onReload()
    } catch (err) {
      onNotice({ ok: false, text: err?.message || 'That action did not complete.' })
    } finally {
      setBusy(null)
    }
  }, [onNotice, onReload])

  if (error) {
    return (
      <>
        <button className="go-back" onClick={onBack}>← All organizations</button>
        <div className="go-note err">
          This organization's billing could not be loaded. {error}
        </div>
      </>
    )
  }
  if (!data) {
    return (
      <>
        <button className="go-back" onClick={onBack}>← All organizations</button>
        <div className="go-empty">Loading billing…</div>
      </>
    )
  }

  const id = data.identity || {}
  const agreement = data.agreement
  const sub = data.subscription || {}
  const setup = data.setup || {}
  const pastDue = data.past_due || {}
  const base = `/platform/billing/organizations/${orgId}`
  const hasSubscription = !!sub.stripe_subscription_id

  return (
    <>
      <div className="go-head">
        <div>
          <button className="go-back" onClick={onBack}>← All organizations</button>
          <h1>{id.organization_name}</h1>
          <p>
            {id.brand_name ? `${id.brand_name} · ` : ''}
            billed by {id.merchant_legal_name || 'an unresolved entity'}
          </p>
        </div>
      </div>

      {notice ? (
        <div className={`go-note ${notice.ok ? 'ok' : 'err'}`}>{notice.text}</div>
      ) : null}

      {pastDue.is_past_due ? (
        <div className="go-note err">
          <b>This account is past due.</b>{' '}
          {pastDue.outstanding
            ? `${pastDue.outstanding} outstanding across ${pastDue.outstanding_invoice_count} invoice(s).`
            : ''}{' '}
          {pastDue.failed_payment_count
            ? `${pastDue.failed_payment_count} failed payment attempt(s).`
            : ''}
        </div>
      ) : null}

      <section className="go-panel">
        <h2>Identity</h2>
        <div className="go-body">
          <div className="go-facts">
            <Fact k="Organization" v={id.organization_name} />
            <Fact k="Brand" v={id.brand_name} />
            <Fact k="Legal seller" v={id.merchant_legal_name} />
            <Fact k="Billing status" v={id.organization_id ? (data.organization?.billing_status || 'not set') : null} />
            {/* IDENTIFIERS, NOT CREDENTIALS. Neither authorises anything on its
                own, and no Stripe key is ever sent to a browser. */}
            <Fact k="Stripe customer" v={id.stripe_customer_id} mono />
            <Fact k="Stripe subscription" v={sub.stripe_subscription_id} mono />
          </div>
        </div>
      </section>

      <div className="bcc-two">
        <section className="go-panel">
          <h2>Agreement</h2>
          {agreement ? (
            <div className="go-body">
              <div className="go-facts">
                <Fact k="Package" v={agreement.package_name} />
                <Fact k="Status" v={<Badge value={agreement.status} tones={{ active: 'live', past_due: 'warn' }} />} />
                <Fact k="Recurring"
                      v={agreement.recurring_amount
                        ? `${agreement.recurring_amount} ${agreement.currency} / ${agreement.billing_interval || 'month'}`
                        : null} />
                <Fact k="Setup fee" v={agreement.setup_fee ? `${agreement.setup_fee} ${agreement.currency}` : null} />
                <Fact k="Contract term" v={agreement.contract_term_months ? `${agreement.contract_term_months} months` : null} />
                <Fact k="Billing starts" v={formatDate(agreement.billing_start_date)} />
              </div>
              {(data.agreement_history || []).length > 1 ? (
                <details className="bcc-history">
                  <summary>Agreement history ({data.agreement_history.length})</summary>
                  <div className="bcc-scroll">
                    <table className="go-table">
                      <thead><tr><th>Status</th><th className="num">Recurring</th>
                        <th>Interval</th><th>Package</th></tr></thead>
                      <tbody>
                        {data.agreement_history.map(a => (
                          <tr key={a.id}>
                            <td><Badge value={a.status} tones={{ active: 'live', superseded: 'ready', cancelled: 'blocked' }} /></td>
                            <td className="num">{a.recurring_amount || '—'}</td>
                            <td className="go-dimmed">{a.billing_interval || '—'}</td>
                            <td className="go-dimmed">{a.package_name || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
              ) : null}
            </div>
          ) : (
            <div className="go-empty">No BillingAgreement on this organization.</div>
          )}
        </section>

        <section className="go-panel">
          <h2>Subscription &amp; autopay</h2>
          <div className="go-body">
            <div className="go-facts">
              <Fact k="Subscription"
                    v={hasSubscription
                      ? <span className="go-badge live">Running</span>
                      : <span className="go-badge warn">Not started</span>} />
              <Fact k="Stripe state" v={sub.stripe_state ? titleize(sub.stripe_state) : null} />
              <Fact k="Autopay"
                    v={sub.autopay_active === null || sub.autopay_active === undefined
                      ? <span className="go-badge ready">Unknown</span>
                      : sub.autopay_active
                        ? <span className="go-badge live">Active</span>
                        : <span className="go-badge warn">Inactive</span>} />
              <Fact k="Saved payment method"
                    v={sub.payment_method_on_file === null || sub.payment_method_on_file === undefined
                      ? null
                      : sub.payment_method_on_file ? 'On file' : 'None on file'} />
              <Fact k="Next billing date" v={formatDate(sub.current_period_end)} />
              <Fact k="Cancels at period end" v={sub.cancel_at_period_end ? 'Yes' : null} />
            </div>

            {/* THE DOUBLE-CHARGE GUARD, MADE VISIBLE. The backend refuses a
                second subscription for the same agreement regardless of what
                this renders; disabling the control is so an operator is never
                invited to try. */}
            <div className="go-actions bcc-actions">
              {agreement && !hasSubscription ? (
                <button className="go-btn go" disabled={busy === 'sub'}
                        onClick={() => act('sub', `${base}/agreements/${agreement.id}/subscribe`)}>
                  {busy === 'sub' ? 'Starting…' : 'Start subscription from agreement'}
                </button>
              ) : null}
              {agreement && hasSubscription ? (
                <>
                  <button className="go-btn" disabled title="A subscription already exists for this agreement">
                    Subscription already running
                  </button>
                  <button className="go-btn danger sm" disabled={busy === 'cancel'}
                          onClick={() => act('cancel', `${base}/agreements/${agreement.id}/cancel`,
                                             { at_period_end: true })}>
                    {busy === 'cancel' ? 'Cancelling…' : 'Cancel at period end'}
                  </button>
                </>
              ) : null}
              <button className="go-btn ghost sm" disabled={busy === 'portal'}
                      onClick={() => act('portal', `${base}/portal`)}>
                {busy === 'portal' ? 'Opening…' : 'Billing portal link'}
              </button>
            </div>
            {notice?.ok && notice.result?.portal_url ? (
              <p className="bcc-note">
                Portal link for the customer:{' '}
                <a className="bcc-link" href={notice.result.portal_url}
                   target="_blank" rel="noopener noreferrer">open</a>
              </p>
            ) : null}
          </div>
        </section>
      </div>

      <section className="go-panel">
        <h2>Setup / implementation</h2>
        <div className="go-body">
          {setup.status && setup.status !== 'none' ? (
            <div className="go-facts">
              <Fact k="Amount" v={setup.amount ? `${setup.amount} ${setup.currency}` : null} />
              <Fact k="Status" v={<Badge value={setup.status}
                                         tones={{ paid: 'live', unpaid: 'warn',
                                                  not_invoiced: 'new', void: 'ready' }} />} />
              <Fact k="Invoice" v={setup.invoice_number} mono />
              <Fact k="Hosted invoice"
                    v={setup.hosted_invoice_url
                      ? <a className="bcc-link" href={setup.hosted_invoice_url}
                           target="_blank" rel="noopener noreferrer">open</a>
                      : null} />
            </div>
          ) : (
            <p className="go-dimmed bcc-note">No setup charge on this account.</p>
          )}
        </div>
      </section>

      <section className="go-panel">
        <h2>
          Invoices<span className="count">{(data.invoices || []).length}</span>
          <button className="go-btn ghost sm bcc-head-btn"
                  onClick={() => setShowNew(s => !s)}>
            {showNew ? 'Close' : 'New invoice'}
          </button>
        </h2>
        {showNew ? (
          <NewInvoice orgId={orgId}
                      onDone={r => { onNotice({ ok: true, text: `Draft invoice ${r.stripe_invoice_id} created with ${r.line_item_count} line item(s). Nothing is charged until it is finalized.` }); setShowNew(false); onReload() }}
                      onError={t => onNotice({ ok: false, text: t })} />
        ) : null}
        {(data.invoices || []).length === 0 ? (
          <div className="go-empty">No invoices for this organization.</div>
        ) : (
          <div className="bcc-scroll">
            <table className="go-table">
              <thead>
                <tr><th>Invoice</th><th>Issued</th><th>Due</th>
                  <th className="num">Amount</th><th className="num">Balance</th>
                  <th>Status</th><th>Links</th><th /></tr>
              </thead>
              <tbody>
                {(data.invoices || []).map(inv => (
                  <tr key={inv.id}>
                    <td className="bcc-mono">{inv.number || '—'}</td>
                    <td className="go-dimmed">{formatDate(inv.created_at) || '—'}</td>
                    <td className="go-dimmed">{formatDate(inv.due_date) || '—'}</td>
                    <td className="num">{inv.total || '—'}</td>
                    <td className="num">
                      {inv.amount_due_cents ? inv.amount_due : <span className="go-dimmed">—</span>}
                    </td>
                    <td><Badge value={inv.status} tones={INVOICE_TONE} /></td>
                    <td>
                      {inv.hosted_invoice_url
                        ? <a className="bcc-link" href={inv.hosted_invoice_url}
                             target="_blank" rel="noopener noreferrer">hosted</a>
                        : null}
                      {inv.hosted_invoice_url && inv.invoice_pdf ? ' · ' : ''}
                      {inv.invoice_pdf
                        ? <a className="bcc-link" href={inv.invoice_pdf}
                             target="_blank" rel="noopener noreferrer">pdf</a>
                        : null}
                      {!inv.hosted_invoice_url && !inv.invoice_pdf
                        ? <span className="go-dimmed">—</span> : null}
                    </td>
                    <td>
                      <div className="go-actions">
                        {inv.status === 'draft' ? (
                          <button className="go-btn sm" disabled={busy === `f${inv.id}`}
                                  onClick={() => act(`f${inv.id}`, `${base}/invoices/${inv.id}/finalize`)}>
                            Finalize
                          </button>
                        ) : null}
                        {inv.status === 'open' ? (
                          <button className="go-btn sm" disabled={busy === `s${inv.id}`}
                                  onClick={() => act(`s${inv.id}`, `${base}/invoices/${inv.id}/send`)}>
                            Send
                          </button>
                        ) : null}
                        {inv.status !== 'paid' && inv.status !== 'void' ? (
                          <button className="go-btn danger sm" disabled={busy === `v${inv.id}`}
                                  onClick={() => act(`v${inv.id}`, `${base}/invoices/${inv.id}/void`)}>
                            Void
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="go-panel">
        <h2>Payments<span className="count">{(data.payments || []).length}</span></h2>
        {(data.payments || []).length === 0 ? (
          <div className="go-empty">No payments recorded.</div>
        ) : (
          <div className="bcc-scroll">
            <table className="go-table">
              <thead>
                <tr><th>Date</th><th className="num">Amount</th><th>Method</th>
                  <th>Status</th><th className="num">Refunded</th><th>Detail</th></tr>
              </thead>
              <tbody>
                {(data.payments || []).map(p => (
                  <tr key={p.id}>
                    <td className="go-dimmed">{formatDate(p.created_at) || '—'}</td>
                    <td className="num">{p.amount || '—'}</td>
                    <td className="go-dimmed">{p.payment_method_label || '—'}</td>
                    <td><Badge value={p.status} tones={PAYMENT_TONE} /></td>
                    <td className="num">
                      {p.refunded_cents ? p.refunded : <span className="go-dimmed">—</span>}
                    </td>
                    <td className="go-dimmed bcc-detail">{p.failure_message || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <p className="bcc-basis go-dimmed">{data.basis}</p>
    </>
  )
}

/* ═══════════════════════════════════════════════════════════════════════════
 * PAGE
 * ═════════════════════════════════════════════════════════════════════════ */

export default function BillingCommandCenter() {
  const [tab, setTab] = useState('dashboard')
  const [dashboard, setDashboard] = useState(null)
  const [orgs, setOrgs] = useState([])
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailError, setDetailError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [loading, setLoading] = useState(true)
  const [listLoading, setListLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    api.get('/platform/billing/command-center')
      .then(d => { if (!cancelled) { setDashboard(d); setError(null) } })
      .catch(err => { if (!cancelled) setError(err?.message || 'unavailable') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (tab !== 'organizations') return
    let cancelled = false
    setListLoading(true)
    const params = new URLSearchParams({ status: filter })
    if (query.trim()) params.set('q', query.trim())
    const t = setTimeout(() => {
      api.get(`/platform/billing/organizations?${params.toString()}`)
        .then(d => { if (!cancelled) setOrgs(d?.organizations || []) })
        .catch(() => { if (!cancelled) setOrgs([]) })
        .finally(() => { if (!cancelled) setListLoading(false) })
    }, 250)
    return () => { cancelled = true; clearTimeout(t) }
  }, [tab, filter, query])

  const loadDetail = useCallback((orgId) => {
    setDetail(null)
    setDetailError(null)
    api.get(`/platform/billing/organizations/${orgId}`)
      .then(setDetail)
      .catch(err => setDetailError(err?.message || 'It may have been removed.'))
  }, [])

  function open(orgId) {
    setSelected(orgId)
    setNotice(null)
    setTab('organizations')
    loadDetail(orgId)
  }

  if (loading) {
    return <div className="go-scope"><div className="go-empty">Loading billing…</div></div>
  }

  /* PERMISSION DENIED IS A SENTENCE. This surface is advertised to nobody, so
   * arriving here without authority means a typed URL — and the backend has
   * already refused, which is what matters. */
  if (error) {
    return (
      <div className="go-scope">
        <div className="go-head"><div><h1>Billing Command Center</h1></div></div>
        <div className="go-note err">
          Platform billing is unavailable right now. If this persists, billing
          may not be configured for this environment.
        </div>
      </div>
    )
  }

  return (
    <div className="go-scope">
      <div className="go-head">
        <div>
          <h1>Billing Command Center</h1>
          <p>
            Billing across every customer organization. This is the platform
            surface — a customer's own billing screen is separate and scoped to
            their workspace.
          </p>
        </div>
      </div>

      <div className="go-tabs">
        <button className={`go-tab ${tab === 'dashboard' ? 'on' : ''}`}
                onClick={() => { setTab('dashboard'); setSelected(null) }}>
          Dashboard
        </button>
        <button className={`go-tab ${tab === 'organizations' ? 'on' : ''}`}
                onClick={() => setTab('organizations')}>
          Organizations
        </button>
      </div>

      {tab === 'dashboard' ? (
        <>
          <Dashboard data={dashboard} onOpen={open} />
          <p className="bcc-basis go-dimmed">{dashboard.basis}</p>
        </>
      ) : selected ? (
        <OrgDetail orgId={selected} data={detail} error={detailError}
                   notice={notice} onNotice={setNotice}
                   onReload={() => loadDetail(selected)}
                   onBack={() => { setSelected(null); setDetail(null); setNotice(null) }} />
      ) : (
        <OrgList rows={orgs} filter={filter} query={query} loading={listLoading}
                 onFilter={setFilter} onQuery={setQuery} onOpen={open} />
      )}
    </div>
  )
}
