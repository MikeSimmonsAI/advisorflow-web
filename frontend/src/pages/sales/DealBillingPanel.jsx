/**
 * DealBillingPanel — the money half of a closed deal, on the rep's own screen.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * TWO BILLS. TWO BUTTONS. TWO STATUSES. NEVER ONE.
 * ═══════════════════════════════════════════════════════════════════════════
 * The one-time implementation fee and the recurring subscription are separate
 * obligations with separate money, separate lifecycles and separate answers to
 * "has this been collected?". This panel renders them as two independent
 * sections for that reason — collapsing them is what produced a single $3,500
 * Growth charge and a customer being asked to pay a year's setup and their
 * first month as one indivisible amount.
 *
 * Either can be created first. Neither blocks the other. No ordering policy is
 * implied here because none is configured anywhere, and inventing one in the
 * UI would be inventing terms the business never agreed.
 *
 * THE LINK LIVES HERE, NOT IN BROWSER HISTORY. Once a checkout is created the
 * server records it, so this panel can show it again on any device, to any
 * authorised person, for as long as it is outstanding. A seller hunting
 * through Chrome history for a customer's payment page is this panel failing.
 *
 * BLOCKERS ARE NAMED, NOT COUNTED. The server returns what is standing in the
 * way and this renders it verbatim. "Not ready" with no reason is how a rep
 * pings an owner on Slack instead of fixing a mapping themselves.
 *
 * A CHECKOUT LINK IS NOT A PAYMENT. Nothing here claims money arrived; the
 * webhook is the only thing in the system that says that. This panel is
 * careful to say "link created", never "paid".
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { Card, Chip, ErrorBar } from './parts'

function money(cents) {
  if (cents === null || cents === undefined) return '—'
  return '$' + (cents / 100).toLocaleString(undefined,
    { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function when(iso) {
  if (!iso) return null
  const d = new Date(iso)
  if (isNaN(d)) return null
  return d.toLocaleString(undefined,
    { month: 'short', day: 'numeric', year: 'numeric' })
}

const BLOCK_STYLE = {
  background: '#fffbeb', border: '1px solid #fcd34d', color: '#92400e',
}

/* NO RAW BACKEND CODES ON A SELLER'S SCREEN. `checkout_pending` is a database
   value; "Link sent — not paid yet" is what a person needs to read. An unknown
   status is shown as itself rather than hidden, because a state nobody
   labelled is still a state the rep is entitled to see. */
const SETUP_STATUS = {
  not_sent: { label: 'Not sent', tone: 'grey',
              note: 'No payment page has been created for the setup fee.' },
  checkout_pending: { label: 'Link sent — not paid yet', tone: 'amber',
                      note: 'The customer has a payment page. Nothing is '
                            + 'collected until they complete it.' },
  paid: { label: 'Paid', tone: 'green', note: null },
  failed: { label: 'Payment failed', tone: 'red',
            note: 'The processor declined the last attempt. Create a new '
                  + 'payment page to try again.' },
}

const SUB_STATUS = {
  active: { label: 'Active', tone: 'green', note: null },
  trialing: { label: 'In trial', tone: 'green', note: null },
  past_due: { label: 'Past due', tone: 'red',
              note: 'The subscription exists but a payment did not go '
                    + 'through.' },
  canceled: { label: 'Canceled', tone: 'grey', note: null },
  incomplete: { label: 'Not finished', tone: 'amber',
                note: 'Checkout was started but never completed.' },
  unpaid: { label: 'Unpaid', tone: 'red', note: null },
}

function setupPresentation(state) {
  const raw = (state && state.status) || 'not_sent'
  return SETUP_STATUS[raw] || { label: raw, tone: 'grey', note: null }
}

/* The subscription's status comes from Stripe by way of the customer record,
   and NULL there means "no subscription has ever existed" — which is not the
   same as one that stopped. It is only reported as not started; a checkout
   that is open says so separately, because an unfinished checkout and an
   untouched deal look identical otherwise. */
function subPresentation(state) {
  const raw = state && state.status
  if (raw) return SUB_STATUS[raw] || { label: raw, tone: 'grey', note: null }
  if (state && state.checkout_url) {
    return { label: 'Link sent — not started yet', tone: 'amber',
             note: 'The customer has a page to start the subscription. It '
                   + 'begins when they complete it.' }
  }
  return { label: 'Not started', tone: 'grey',
           note: 'No subscription checkout has been created.' }
}

/* COPY MUST NOT LIE. If the clipboard write fails — an insecure origin, a
   locked-down browser — the button says so instead of flashing "Copied" over a
   clipboard that still holds something else. */
async function copyToClipboard(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch { /* fall through to the legacy path */ }
  try {
    const el = document.createElement('textarea')
    el.value = text
    el.setAttribute('readonly', '')
    el.style.position = 'fixed'
    el.style.opacity = '0'
    document.body.appendChild(el)
    el.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(el)
    return ok
  } catch { return false }
}

function CopyLinkButton({ url }) {
  const [state, setState] = useState('idle')   // idle | copied | failed
  const click = async () => {
    const ok = await copyToClipboard(url)
    setState(ok ? 'copied' : 'failed')
    window.setTimeout(() => setState('idle'), 2500)
  }
  return (
    <button className="sw-btn" onClick={click} style={{ marginLeft: 8 }}>
      {state === 'copied' ? 'Copied' : state === 'failed' ? 'Copy failed' : 'Copy link'}
    </button>
  )
}

const SECTION = {
  border: '1px solid #e5e7eb', borderRadius: 10, padding: '12px 14px',
  marginBottom: 10, background: '#fff',
}

/**
 * One obligation, end to end: what it costs, where it stands, and the single
 * control that moves it forward. Rendered twice — never merged, never with a
 * shared button, because a shared button is a combined charge waiting to
 * happen.
 */
function Obligation({
  label, amount, detail, presentation, checkoutUrl, settled, settledLine,
  canCreate, createLabel, recreateLabel, unavailableReason, onCreate, busy,
}) {
  return (
    <div style={SECTION}>
      <div style={{ display: 'flex', alignItems: 'baseline',
                    justifyContent: 'space-between', gap: 12 }}>
        <div>
          <div className="sw-subtle" style={{ fontSize: 10, letterSpacing: '.04em' }}>
            {label}
          </div>
          <b style={{ fontSize: 18 }}>{amount}</b>
          {detail && (
            <span className="sw-subtle" style={{ fontSize: 11, marginLeft: 8 }}>
              {detail}
            </span>
          )}
        </div>
        <Chip tone={presentation.tone}>{presentation.label}</Chip>
      </div>

      {presentation.note && (
        <div className="sw-subtle" style={{ fontSize: 11, marginTop: 5 }}>
          {presentation.note}
        </div>
      )}

      {/* SETTLED MEANS THE CONTROLS GO AWAY. Leaving a live "send payment
          page" button next to a paid fee is how a customer gets billed twice
          by somebody being helpful. */}
      {settled ? (
        settledLine && (
          <div style={{ fontSize: 11, marginTop: 6, color: '#065f46' }}>
            {settledLine}
          </div>
        )
      ) : (
        <>
          {/* THE EXISTING LINK IS THE FIRST THING OFFERED, not a new one.
              Creating a second page for the same obligation gives the customer
              two ways to pay the same bill. */}
          {checkoutUrl && (
            <div style={{ marginTop: 8, background: '#eff6ff',
                          border: '1px solid #93c5fd', borderRadius: 8,
                          padding: '9px 11px' }}>
              <div style={{ fontSize: 11, wordBreak: 'break-all' }}>
                <a href={checkoutUrl} target="_blank" rel="noreferrer">
                  {checkoutUrl}
                </a>
              </div>
              <div style={{ marginTop: 7 }}>
                <a className="sw-btn" href={checkoutUrl} target="_blank"
                   rel="noreferrer">Open payment page</a>
                <CopyLinkButton url={checkoutUrl} />
              </div>
              <div className="sw-subtle" style={{ fontSize: 10, marginTop: 6 }}>
                Send this to the customer. Nothing is collected until they
                complete it — this shows as paid once the processor confirms it.
              </div>
            </div>
          )}

          {canCreate ? (
            <div style={{ marginTop: 8 }}>
              <button className={'sw-btn' + (checkoutUrl ? '' : ' primary')}
                      onClick={onCreate} disabled={busy}>
                {busy ? 'Creating…'
                  : checkoutUrl ? recreateLabel : createLabel}
              </button>
            </div>
          ) : (
            unavailableReason && (
              <div className="sw-subtle" style={{ fontSize: 11, marginTop: 8 }}>
                {unavailableReason}
              </div>
            )
          )}
        </>
      )}
    </div>
  )
}

export default function DealBillingPanel({ opp }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busyPart, setBusyPart] = useState(null)   // 'setup' | 'subscription'

  const load = useCallback(async () => {
    setError(null)
    try { setData(await api.get('/sales/opportunities/' + opp.id + '/billing')) }
    catch (e) { setError(e?.detail || e.message || 'Could not load billing.') }
  }, [opp.id])

  useEffect(() => { load() }, [load])

  /* ONE FUNCTION, ONE EXPLICIT PART. There is no call site that omits it and
     no default — the server refuses a request that does not say which
     obligation it is billing, and this is the client half of that promise. */
  const createCheckout = async (part) => {
    setBusyPart(part); setError(null)
    try {
      await api.post('/sales/opportunities/' + opp.id
        + '/billing/checkout?part=' + part, {})
      /* Re-read rather than trusting the response: the link the panel shows
         must be the one the server recorded, or a second device sees a
         different answer. */
      await load()
    } catch (e) {
      const d = e?.detail
      setError(typeof d === 'string' ? d
        : (d?.message || 'Could not create the payment page.'))
    } finally { setBusyPart(null) }
  }

  if (!data) {
    return <Card title="BILLING"><div className="sw-subtle">Loading…</div></Card>
  }

  const blockers = data.blockers || []
  const charges = data.charges || []
  const setup = charges.find(c => c.kind === 'setup_fee')
  const sub = charges.find(c => c.kind === 'subscription')
  const state = data.state || {}
  const setupState = state.setup || {}
  const subState = state.subscription || {}
  const ready = data.billable_parts || {}

  /* A blocker names the obligation it blocks. "This customer already has a
     subscription" must not appear as the reason an implementation fee cannot
     be collected — that is the combined charge coming back as a refusal. */
  const blocking = (part) => blockers.filter(
    b => !b.applies_to || b.applies_to.includes(part))

  const setupP = setupPresentation(setupState)
  const subP = subPresentation(subState)
  const setupSettled = setupState.status === 'paid'
  const subSettled = ['active', 'trialing'].includes(subState.status)

  const setupPaidOn = when(setupState.paid_at)
  const renewsOn = when(subState.current_period_end)

  /* WHY A BUTTON IS MISSING IS PART OF THE UI. A control that is simply
     absent, on a screen about money, reads as a bug. */
  const whyNot = (part) => {
    const mine = blocking(part)
    return mine.length
      ? (mine.length === 1
        ? mine[0].message
        : 'Deal with what is listed above before billing this.')
      : null
  }

  /* ONE CHIP CANNOT SAY EVERYTHING, so it says the least misleading thing.
     "Partly ready" exists because a deal whose setup fee can be collected
     while its subscription is blocked is neither ready nor stuck, and calling
     it either one sends the rep the wrong way. */
  const allSettled = (!setup || setupSettled) && (!sub || subSettled)
  const head = allSettled && (setup || sub)
    ? { tone: 'green', label: 'Collected' }
    : data.billable
      ? { tone: 'green', label: 'Ready to bill' }
      : (ready.setup || ready.subscription)
        ? { tone: 'amber', label: 'Partly ready' }
        : blockers.length
          ? { tone: 'amber', label: blockers.length + ' to deal with' }
          : { tone: 'grey', label: 'Nothing to bill' }

  return (
    <Card title="BILLING"
          sub="Two separate obligations, tracked and collected separately"
          right={<Chip tone={head.tone}>{head.label}</Chip>}>
      <ErrorBar error={error} onRetry={load} />

      {/* Setup and recurring stay SEPARATE. One blended figure is how a
          customer is told a monthly number that includes a one-off. */}
      <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginBottom: 12 }}>
        <div>
          <div className="sw-subtle" style={{ fontSize: 10 }}>ONE-TIME SETUP</div>
          <b style={{ fontSize: 16 }}>{money(data.setup_cents)}</b>
        </div>
        <div>
          <div className="sw-subtle" style={{ fontSize: 10 }}>RECURRING</div>
          <b style={{ fontSize: 16 }}>
            {data.recurring_cents === null ? '—' : money(data.recurring_cents) + '/mo'}
          </b>
        </div>
        {/* COMMITMENT, ALWAYS. A recurring figure without it is ambiguous
            between two real prices — $500 committed and $597 not — and the rep
            quoting it cannot tell which they are looking at. */}
        <div>
          <div className="sw-subtle" style={{ fontSize: 10 }}>COMMITMENT</div>
          <b style={{ fontSize: 13 }}>
            {data.commitment_label
              || (data.term_months ? data.term_months + ' months' : '—')}
          </b>
        </div>
        <div>
          <div className="sw-subtle" style={{ fontSize: 10 }}>PRICING FROM</div>
          <b style={{ fontSize: 13 }}>{data.pricing_source}</b>
        </div>
      </div>

      {/* A CUSTOM RATE SAYS SO, AND SAYS WHO APPROVED IT. Otherwise "Ready to
          bill" on a bespoke monthly figure is indistinguishable from the same
          words on a catalogue tier, and the one that needs a second pair of
          eyes is the one that looks routine. */}
      {data.custom_pricing && (
        <div style={{ background: '#f5f3ff', border: '1px solid #c4b5fd',
                      borderRadius: 8, padding: '9px 12px', marginBottom: 10 }}>
          <b style={{ fontSize: 11, color: '#5b21b6' }}>
            Custom monthly rate
            {data.custom_pricing.authority === 'manager_approval'
              ? ' · manager approved' : ''}
          </b>
          <div style={{ fontSize: 11, marginTop: 3, opacity: 0.85 }}>
            Charged as a rate set for this deal, not a catalogue plan.
            {data.custom_pricing.authority === 'manager_approval'
              ? ' A manager approved this exact figure.'
              : ' Set within the pricing authority of whoever agreed it.'}
          </div>
          {/* ENTITLEMENTS, TRUTHFULLY. "Not on a tier" is not the same as "no
              limits", and saying so vaguely is how a negotiated customer ends
              up quietly uncapped. */}
          {data.custom_pricing.entitlement && (
            <div className="sw-subtle" style={{ fontSize: 10, marginTop: 6 }}>
              {data.custom_pricing.entitlement.source === 'custom_agreement_snapshot'
                ? <>Entitlements come from this customer&apos;s own agreement.
                    {(data.custom_pricing.entitlement.unset_dimensions || []).length > 0
                      && <> Not yet recorded:{' '}
                           {data.custom_pricing.entitlement.unset_dimensions.join(', ')}.</>}
                  </>
                : <><b>Entitlements need configuring.</b> This customer is on no
                    standard tier and no agreed limits have been recorded, so no
                    ceiling applies to anything yet.</>}
            </div>
          )}
        </div>
      )}

      {/* A BLOCKER SAYS WHICH BILL IT HOLDS UP. Without that, "this customer
          already has a subscription" reads as a reason nothing can be
          collected, and the setup fee that is perfectly billable goes
          unchased. */}
      {blockers.map((b, i) => (
        <div key={i} style={{ ...BLOCK_STYLE, borderRadius: 8,
                              padding: '10px 12px', marginBottom: 8 }}>
          {b.applies_to && b.applies_to.length === 1 && (
            <div style={{ fontSize: 9, letterSpacing: '.05em', opacity: 0.8,
                          marginBottom: 2 }}>
              {b.applies_to[0] === 'setup'
                ? 'AFFECTS THE SETUP FEE ONLY'
                : 'AFFECTS THE SUBSCRIPTION ONLY'}
            </div>
          )}
          <b style={{ fontSize: 11 }}>{b.message}</b>
          {b.code === 'recurring_rate_differs_from_catalogue' && (
            <div style={{ fontSize: 11, marginTop: 3, opacity: 0.85 }}>
              Deal {money(b.deal_cents)}/mo · catalogue {money(b.catalogue_cents)}/mo
            </div>
          )}
        </div>
      ))}

      {/* ─── The two obligations. Independent by design. ─────────────────
          Each one is created, sent, chased and confirmed on its own. Order
          is not enforced because no policy anywhere says setup must clear
          before a subscription may start. */}
      {setup && (
        <Obligation
          label="ONE-TIME SETUP & IMPLEMENTATION"
          amount={money(setup.cents)}
          detail="Charged once"
          presentation={setupP}
          checkoutUrl={setupState.checkout_url}
          settled={setupSettled}
          settledLine={setupSettled
            ? 'Paid ' + money(setupState.paid_cents ?? setup.cents)
              + (setupPaidOn ? ' on ' + setupPaidOn : '') + '.'
            : null}
          canCreate={!!ready.setup}
          createLabel="Create setup payment page"
          recreateLabel="Create a new setup payment page"
          unavailableReason={whyNot('setup')}
          onCreate={() => createCheckout('setup')}
          busy={busyPart === 'setup'} />
      )}

      {sub && (
        <Obligation
          label="MONTHLY SUBSCRIPTION"
          amount={money(sub.cents) + '/mo'}
          detail={data.commitment_label
            || (data.term_months ? data.term_months + '-month agreement' : null)}
          presentation={subP}
          checkoutUrl={subState.checkout_url}
          settled={subSettled}
          settledLine={subSettled
            ? 'Subscription is running'
              + (renewsOn ? ' · next renewal ' + renewsOn : '') + '.'
            : null}
          canCreate={!!ready.subscription}
          createLabel="Create subscription page"
          recreateLabel="Create a new subscription page"
          unavailableReason={whyNot('subscription')}
          onCreate={() => createCheckout('subscription')}
          busy={busyPart === 'subscription'} />
      )}

      {/* Said once, at the bottom, rather than on each section: the same rule
          governs both and repeating it twice makes it read as boilerplate. */}
      {(setup || sub) && (
        <div className="sw-subtle" style={{ fontSize: 10, marginTop: 2 }}>
          These are billed separately and tracked separately. Creating either
          page charges nothing and does not affect the other.
        </div>
      )}
    </Card>
  )
}
