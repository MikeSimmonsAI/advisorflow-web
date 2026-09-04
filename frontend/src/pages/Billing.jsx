/* CUSTOMER WORKSPACE BILLING (P6)
 *
 * The billing screen for ONE customer organization — the one whose workspace
 * the user is standing in. The cross-organization back office is a separate
 * surface with separate authority and is P7; nothing here is shared with it.
 *
 * TWO RULES THIS FILE FOLLOWS ABSOLUTELY
 *
 *   NO ARITHMETIC ON MONEY. Every amount rendered below is a string the
 *   backend already formatted, or an integer count. There is no division by
 *   100, no currency maths, no proration, no totals summed in the browser.
 *   The backend owns money because it is the side that can be tested against
 *   Stripe, and a number computed twice is a number that eventually disagrees
 *   with itself.
 *
 *   NO PAYMENT METHOD IS NAMED. Not "card", not a list, not a fallback. Which
 *   methods a customer may use is Stripe's answer, per payment flow, and the
 *   UI expresses the PURPOSE — pay the setup invoice, update the autopay
 *   method — then hands off to Stripe's hosted surface. A screen that lists
 *   methods promises something Stripe may refuse.
 *
 * Everything rendered comes from GET /billing/overview and GET /billing/access.
 * The workspace is never named in a request: X-Workspace-Id travels on every
 * call from api/client.js and the server resolves the subject itself.
 */

import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import PageShell from '../components/PageShell'
import './Billing.css'

/* ── vocabulary ─────────────────────────────────────────────────────────
 * The backend's states, said out loud. An unknown key renders from its own
 * name rather than disappearing: new backend states must degrade to readable,
 * never to blank. */
const ACCOUNT_STATES = {
  active: { label: 'Current', tone: 'green' },
  trialing: { label: 'Trial', tone: 'blue' },
  past_due: { label: 'Past Due', tone: 'red' },
  unpaid: { label: 'Unpaid', tone: 'red' },
  canceled: { label: 'Cancelled', tone: 'neutral' },
  cancelled: { label: 'Cancelled', tone: 'neutral' },
  incomplete: { label: 'Incomplete', tone: 'amber' },
}

const INVOICE_STATES = {
  draft: { label: 'Draft', tone: 'neutral' },
  open: { label: 'Open', tone: 'amber' },
  paid: { label: 'Paid', tone: 'green' },
  void: { label: 'Void', tone: 'neutral-dim' },
  uncollectible: { label: 'Uncollectible', tone: 'red' },
}

const PAYMENT_STATES = {
  succeeded: { label: 'Paid', tone: 'green' },
  failed: { label: 'Failed', tone: 'red' },
  pending: { label: 'Pending', tone: 'amber' },
  refunded: { label: 'Refunded', tone: 'neutral' },
  partially_refunded: { label: 'Part. Refunded', tone: 'neutral' },
}

const SETUP_STATES = {
  paid: { label: 'Paid', tone: 'green' },
  unpaid: { label: 'Unpaid', tone: 'amber' },
  not_invoiced: { label: 'Not Yet Invoiced', tone: 'neutral' },
  void: { label: 'Void', tone: 'neutral-dim' },
  open: { label: 'Open', tone: 'amber' },
}

function titleize(value) {
  return String(value).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

function Badge({ value, vocabulary }) {
  if (!value) return <span className="bill-muted">—</span>
  const found = vocabulary[value] || { label: titleize(value), tone: 'neutral' }
  return <span className={`badge badge--${found.tone}`}>{found.label}</span>
}

/* Dates arrive from the backend either as ISO strings (local mirror) or as
 * Unix seconds (Stripe period ends). Formatting is presentation, not money. */
function formatDate(value) {
  if (value === null || value === undefined || value === '') return null
  const date = typeof value === 'number'
    ? new Date(value * 1000)
    : new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return date.toLocaleDateString(undefined,
    { year: 'numeric', month: 'short', day: 'numeric' })
}

function Row({ label, value, mono = false }) {
  if (value === null || value === undefined || value === '') return null
  return (
    <div className="bill-row">
      <span className="bill-row__label">{label}</span>
      <span className={`bill-row__value${mono ? ' bill-row__value--mono' : ''}`}>{value}</span>
    </div>
  )
}

function Section({ title, action, children }) {
  return (
    <section className="panel bill-section">
      <div className="bill-section__head">
        <h2 className="bill-section__title">{title}</h2>
        {action || null}
      </div>
      {children}
    </section>
  )
}

function Empty({ children }) {
  return <p className="empty-state">{children}</p>
}

export default function Billing() {
  const [access, setAccess] = useState(null)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      // Access first: it distinguishes "you may not" from "it broke", which
      // are the same HTTP status from the overview's point of view and very
      // different things to tell somebody.
      const who = await api.get('/billing/access')
      setAccess(who)
      if (!who?.can_view) { setData(null); return }
      setData(await api.get('/billing/overview'))
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  /* Stripe's hosted surfaces are opened, never reimplemented. The portal is
   * where a payment method is updated and where Stripe — not this code —
   * decides which methods are eligible for recurring collection. */
  async function openPortal() {
    setBusy('portal')
    setNotice('')
    try {
      const result = await api.post('/billing/portal')
      if (result?.portal_url || result?.url) {
        window.location.href = result.portal_url || result.url
      } else {
        setNotice('The billing portal could not be opened. Please try again.')
      }
    } catch (err) {
      setNotice(err?.message || 'The billing portal is unavailable right now.')
    } finally {
      setBusy(null)
    }
  }

  if (loading) {
    return (
      <PageShell eyebrow="Account" title="Billing"
                 subtitle="Loading your billing details…">
        <div className="bill-status">
          {[0, 1, 2, 3].map(i => (
            <div key={i} className="bill-skeleton" style={{ height: 96 }} />
          ))}
        </div>
        <div className="bill-skeleton" style={{ height: 220 }} />
      </PageShell>
    )
  }

  /* PERMISSION DENIED IS A SENTENCE, NOT AN EMPTY PAGE. The nav item is
   * hidden for this person, so arriving here means a typed URL or a stale
   * link — and the backend has already refused, which is what actually
   * matters. This only explains it. */
  if (access && !access.can_view) {
    return (
      <PageShell eyebrow="Account" title="Billing">
        <section className="panel">
          <Empty>
            You do not have permission to view billing for this workspace.
            An organization administrator can grant billing access.
          </Empty>
        </section>
      </PageShell>
    )
  }

  if (error || !data) {
    return (
      <PageShell eyebrow="Account" title="Billing">
        <section className="panel">
          <Empty>
            Billing information is temporarily unavailable. This is usually
            brief — please try again in a moment.
          </Empty>
          <div className="bill-actions">
            <button className="btn btn--secondary" onClick={load}>Try again</button>
          </div>
        </section>
      </PageShell>
    )
  }

  const canManage = !!access?.can_manage
  const org = data.organization || {}
  const merchant = data.merchant || {}
  const agreement = data.agreement
  const subscription = data.subscription || {}
  const setup = data.setup || {}
  const pastDue = data.past_due || {}
  const invoices = data.invoices || []
  const payments = data.payments || []

  // The single account state, preferring what Stripe says over the local
  // mirror when both are known — the mirror can lag a webhook by seconds.
  const accountState = subscription.stripe_state || org.billing_status || null
  const nextBilling = formatDate(subscription.current_period_end)
  const isPastDue = !!pastDue.is_past_due
  const needsMethod = subscription.requires_payment_method === true
  const openInvoice = invoices.find(i => i.status === 'open')

  return (
    <PageShell
      eyebrow={merchant.brand_name || 'Account'}
      title="Billing"
      subtitle={org.name ? `Billing for ${org.name}` : undefined}
      action={
        <button className="btn btn--secondary" onClick={openPortal}
                disabled={busy === 'portal'}>
          {busy === 'portal' ? 'Opening…' : 'Billing Portal'}
        </button>
      }
    >
      {notice ? (
        <div className="bill-alert bill-alert--warn">
          <div className="bill-alert__body">
            <p className="bill-alert__text">{notice}</p>
          </div>
        </div>
      ) : null}

      {/* IMPOSSIBLE TO MISS, AND FIRST. A customer who does not notice this
          loses service. Every value comes from backend state — nothing here
          decides on its own that something is wrong. */}
      {isPastDue ? (
        <div className="bill-alert">
          <div className="bill-alert__body">
            <h2 className="bill-alert__title">Payment needs attention</h2>
            <p className="bill-alert__text">
              {pastDue.outstanding
                ? `${pastDue.outstanding} ${agreement?.currency || 'USD'} is outstanding across ${pastDue.outstanding_invoice_count} invoice${pastDue.outstanding_invoice_count === 1 ? '' : 's'}.`
                : 'There is an outstanding balance on this account.'}
              {pastDue.failed_payment_count
                ? ` ${pastDue.failed_payment_count} payment attempt${pastDue.failed_payment_count === 1 ? ' has' : 's have'} failed.`
                : ''}
              {' '}Updating the payment method usually resolves this
              automatically on the next attempt.
            </p>
          </div>
          <div className="bill-alert__actions">
            {openInvoice?.hosted_invoice_url ? (
              <a className="btn btn--primary" href={openInvoice.hosted_invoice_url}
                 target="_blank" rel="noopener noreferrer">View invoice</a>
            ) : null}
            {canManage ? (
              <button className="btn btn--secondary" onClick={openPortal}
                      disabled={busy === 'portal'}>Update payment method</button>
            ) : null}
          </div>
        </div>
      ) : null}

      {!isPastDue && needsMethod ? (
        <div className="bill-alert bill-alert--warn">
          <div className="bill-alert__body">
            <h2 className="bill-alert__title">No payment method on file</h2>
            <p className="bill-alert__text">
              This subscription is set to collect automatically but has no
              saved payment method, so the next invoice will not be paid on its
              own.
            </p>
          </div>
          <div className="bill-alert__actions">
            {canManage ? (
              <button className="btn btn--primary" onClick={openPortal}
                      disabled={busy === 'portal'}>Add payment method</button>
            ) : null}
          </div>
        </div>
      ) : null}

      {/* ── ACCOUNT STATUS ── */}
      <div className="bill-status">
        <div className="bill-status__cell">
          <p className="bill-status__label">Account status</p>
          <p className="bill-status__value bill-status__value--sm">
            <Badge value={accountState} vocabulary={ACCOUNT_STATES} />
          </p>
        </div>
        <div className="bill-status__cell">
          <p className="bill-status__label">Outstanding balance</p>
          <p className="bill-status__value">
            {pastDue.outstanding || '0.00'}
          </p>
          <p className="bill-status__note">
            {pastDue.outstanding_invoice_count
              ? `${pastDue.outstanding_invoice_count} open invoice${pastDue.outstanding_invoice_count === 1 ? '' : 's'}`
              : 'Nothing outstanding'}
          </p>
        </div>
        <div className="bill-status__cell">
          <p className="bill-status__label">Next billing date</p>
          <p className="bill-status__value bill-status__value--sm">
            {nextBilling || <span className="bill-muted">—</span>}
          </p>
          {subscription.cancel_at_period_end
            ? <p className="bill-status__note">Cancels at period end</p> : null}
        </div>
        <div className="bill-status__cell">
          <p className="bill-status__label">Autopay</p>
          <p className="bill-status__value bill-status__value--sm">
            {/* null means NOT ANSWERED — a Stripe outage must not render as
                "autopay is off" on a customer's billing screen. */}
            {subscription.autopay_active === null
              || subscription.autopay_active === undefined
              ? <span className="bill-muted">Unknown</span>
              : subscription.autopay_active
                ? <span className="badge badge--green">Active</span>
                : <span className="badge badge--amber">Inactive</span>}
          </p>
          {subscription.payment_method_on_file === false
            ? <p className="bill-status__note">No saved method</p> : null}
        </div>
      </div>

      <div className="bill-columns">
        {/* ── THE AGREEMENT ── */}
        <Section title="Your agreement">
          {agreement ? (
            <>
              <div className="bill-amount">
                {agreement.recurring_amount || '—'}
                <span className="bill-amount__unit">
                  {agreement.currency} / {agreement.billing_interval || 'month'}
                </span>
              </div>
              <p className="bill-amount__caption">
                {agreement.package_name || 'Subscription'}
              </p>
              <div className="bill-rows" style={{ marginTop: 16 }}>
                <Row label="Status" value={<Badge value={agreement.status} vocabulary={ACCOUNT_STATES} />} />
                <Row label="Billed by" value={agreement.merchant_legal_name} />
                <Row label="Brand" value={agreement.brand_name} />
                <Row label="Contract term"
                     value={agreement.contract_term_months
                       ? `${agreement.contract_term_months} months` : null} />
                <Row label="Billing starts" value={formatDate(agreement.billing_start_date)} />
                <Row label="Trial ends" value={formatDate(agreement.trial_end)} />
                <Row label="Unit" value={agreement.unit_label} />
                <Row label="Minimum units" value={agreement.min_units} />
              </div>
            </>
          ) : (
            <Empty>
              No billing agreement is on file for this workspace yet. Your
              account team sets this up when your implementation is approved.
            </Empty>
          )}
        </Section>

        {/* ── SETUP: ITS OWN SECTION, NOT A LINE IN THE SUBSCRIPTION ──
            A separate payment at a separate time, and under the approved
            payment-flow model it may be paid by methods a recurring
            subscription cannot use. Which ones is Stripe's answer, given on
            its hosted invoice — this screen names none. */}
        <Section title="Setup / implementation">
          {setup.status && setup.status !== 'none' ? (
            <>
              <div className="bill-amount">
                {setup.amount || '—'}
                <span className="bill-amount__unit">{setup.currency} one-time</span>
              </div>
              <p className="bill-amount__caption">
                <Badge value={setup.status} vocabulary={SETUP_STATES} />
              </p>
              <div className="bill-rows" style={{ marginTop: 16 }}>
                <Row label="Invoice" value={setup.invoice_number} mono />
                <Row label="Due" value={formatDate(setup.due_date)} />
              </div>
              {setup.status === 'not_invoiced' ? (
                <p className="bill-amount__caption" style={{ marginTop: 14 }}>
                  This charge has not been invoiced yet. You will receive it
                  from your account team.
                </p>
              ) : null}
              <div className="bill-actions">
                {setup.hosted_invoice_url ? (
                  <a className={`btn ${setup.status === 'unpaid' ? 'btn--primary' : 'btn--secondary'}`}
                     href={setup.hosted_invoice_url} target="_blank" rel="noopener noreferrer">
                    {setup.status === 'unpaid' ? 'Pay setup invoice' : 'View invoice'}
                  </a>
                ) : null}
                {setup.invoice_pdf ? (
                  <a className="btn btn--secondary" href={setup.invoice_pdf}
                     target="_blank" rel="noopener noreferrer">PDF</a>
                ) : null}
              </div>
            </>
          ) : (
            <Empty>No setup or implementation charge on this account.</Empty>
          )}
        </Section>
      </div>

      {/* ── SUBSCRIPTION / AUTOPAY ── */}
      <Section title="Subscription &amp; autopay">
        {subscription.has_subscription ? (
          <>
            <div className="bill-rows">
              <Row label="Amount"
                   value={subscription.recurring_amount_cents !== null
                     && agreement?.recurring_amount
                     ? `${agreement.recurring_amount} ${subscription.currency || ''}` : null} />
              <Row label="Frequency" value={titleize(subscription.billing_interval || 'month')} />
              <Row label="Subscription status"
                   value={<Badge value={subscription.stripe_state} vocabulary={ACCOUNT_STATES} />} />
              <Row label="Autopay"
                   value={subscription.autopay_active === null
                     || subscription.autopay_active === undefined
                     ? 'Unknown'
                     : subscription.autopay_active ? 'Active' : 'Inactive'} />
              <Row label="Saved payment method"
                   value={subscription.payment_method_on_file === null
                     || subscription.payment_method_on_file === undefined
                     ? null
                     : subscription.payment_method_on_file
                       ? 'On file'
                       : 'None on file'} />
              <Row label="Next billing date" value={nextBilling} />
            </div>
            {canManage ? (
              <div className="bill-actions">
                <button className="btn btn--primary" onClick={openPortal}
                        disabled={busy === 'portal'}>
                  {busy === 'portal' ? 'Opening…' : 'Manage subscription'}
                </button>
                <button className="btn btn--secondary" onClick={openPortal}
                        disabled={busy === 'portal'}>Update payment method</button>
              </div>
            ) : null}
          </>
        ) : (
          <Empty>
            {agreement
              ? 'Your agreement is not yet running as a subscription. Your account team activates this.'
              : 'No active subscription on this account.'}
          </Empty>
        )}
      </Section>

      {/* ── INVOICES ── */}
      <Section title="Invoices">
        {invoices.length === 0 ? (
          <Empty>No invoices yet. They will appear here once billing begins.</Empty>
        ) : (
          <div className="bill-table-wrap">
            <table className="bill-table">
              <thead>
                <tr>
                  <th>Invoice</th>
                  <th>Issued</th>
                  <th>Due</th>
                  <th>Amount</th>
                  <th>Balance</th>
                  <th>Status</th>
                  <th>&nbsp;</th>
                </tr>
              </thead>
              <tbody>
                {invoices.map(invoice => (
                  <tr key={invoice.id}>
                    <td className="bill-num">{invoice.number || '—'}</td>
                    <td>{formatDate(invoice.created_at) || <span className="bill-muted">—</span>}</td>
                    <td>{formatDate(invoice.due_date) || <span className="bill-muted">—</span>}</td>
                    {/* Backend-formatted strings. No division, no rounding. */}
                    <td className="bill-num">{invoice.total || '—'}</td>
                    <td className="bill-num">
                      {invoice.amount_due_cents ? invoice.amount_due
                        : <span className="bill-muted">—</span>}
                    </td>
                    <td><Badge value={invoice.status} vocabulary={INVOICE_STATES} /></td>
                    <td>
                      {invoice.hosted_invoice_url ? (
                        <a className="bill-link" href={invoice.hosted_invoice_url}
                           target="_blank" rel="noopener noreferrer">Open</a>
                      ) : null}
                      {invoice.hosted_invoice_url && invoice.invoice_pdf ? ' · ' : ''}
                      {invoice.invoice_pdf ? (
                        <a className="bill-link" href={invoice.invoice_pdf}
                           target="_blank" rel="noopener noreferrer">PDF</a>
                      ) : null}
                      {!invoice.hosted_invoice_url && !invoice.invoice_pdf
                        ? <span className="bill-muted">—</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* ── PAYMENTS ── */}
      <Section title="Payments">
        {payments.length === 0 ? (
          <Empty>No payments recorded yet.</Empty>
        ) : (
          <div className="bill-table-wrap">
            <table className="bill-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Amount</th>
                  <th>Method</th>
                  <th>Status</th>
                  <th>Refunded</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {payments.map(payment => (
                  <tr key={payment.id}>
                    <td>{formatDate(payment.created_at) || <span className="bill-muted">—</span>}</td>
                    <td className="bill-num">{payment.amount || '—'}</td>
                    {/* ONE STRING, BUILT BY THE BACKEND, FOR EVERY METHOD.
                        This deliberately does not know what a card is: a bank
                        debit, Link or a wallet renders here exactly as well,
                        which is the defect P5 recorded and P6 closes. */}
                    <td>
                      {payment.payment_method_label
                        || <span className="bill-muted">—</span>}
                    </td>
                    <td><Badge value={payment.status} vocabulary={PAYMENT_STATES} /></td>
                    <td className="bill-num">
                      {payment.refunded_cents ? payment.refunded
                        : <span className="bill-muted">—</span>}
                    </td>
                    <td className="bill-muted">{payment.failure_message || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </PageShell>
  )
}
