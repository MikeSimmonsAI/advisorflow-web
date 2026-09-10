/**
 * DealBillingPanel — the money half of a closed deal, on the rep's own screen.
 *
 * A salesperson should not need God Mode to answer "has this customer paid?",
 * and should not have to re-key the terms they already negotiated into a
 * billing screen somebody else owns. This shows exactly what would be charged
 * for THIS deal — the setup fee and the recurring rate as two separate
 * figures, because they are two different commitments — and sends the customer
 * one payment link for both.
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

const BLOCK_STYLE = {
  background: '#fffbeb', border: '1px solid #fcd34d', color: '#92400e',
}

export default function DealBillingPanel({ opp }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [link, setLink] = useState(null)

  const load = useCallback(async () => {
    setError(null)
    try { setData(await api.get('/sales/opportunities/' + opp.id + '/billing')) }
    catch (e) { setError(e?.detail || e.message || 'Could not load billing.') }
  }, [opp.id])

  useEffect(() => { load() }, [load])

  const createLink = async () => {
    setBusy(true); setError(null); setLink(null)
    try {
      const r = await api.post(
        '/sales/opportunities/' + opp.id + '/billing/checkout', {})
      setLink(r.checkout_url)
      load()
    } catch (e) {
      const d = e?.detail
      setError(typeof d === 'string' ? d : (d?.message || 'Could not create the payment link.'))
    } finally { setBusy(false) }
  }

  if (!data) {
    return <Card title="BILLING"><div className="sw-subtle">Loading…</div></Card>
  }

  const blockers = data.blockers || []
  const charges = data.charges || []
  const setup = charges.find(c => c.kind === 'setup_fee')
  const sub = charges.find(c => c.kind === 'subscription')

  return (
    <Card title="BILLING"
          sub="What this deal charges, and whether it can be collected"
          right={data.billable
            ? <Chip tone="green">Ready to bill</Chip>
            : <Chip tone={blockers.length ? 'amber' : 'grey'}>
                {blockers.length ? blockers.length + ' to deal with' : 'Nothing to bill'}
              </Chip>}>
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

      {blockers.map((b, i) => (
        <div key={i} style={{ ...BLOCK_STYLE, borderRadius: 8,
                              padding: '10px 12px', marginBottom: 8 }}>
          <b style={{ fontSize: 11 }}>{b.message}</b>
          {b.code === 'recurring_rate_differs_from_catalogue' && (
            <div style={{ fontSize: 11, marginTop: 3, opacity: 0.85 }}>
              Deal {money(b.deal_cents)}/mo · catalogue {money(b.catalogue_cents)}/mo
            </div>
          )}
        </div>
      ))}

      {data.billable && (
        <div style={{ marginTop: 4 }}>
          <button className="sw-btn primary" onClick={createLink} disabled={busy}>
            {busy ? 'Creating…' : 'Create payment link'}
          </button>
          <span className="sw-subtle" style={{ fontSize: 11, marginLeft: 10 }}>
            {sub && setup ? 'Setup fee is added to the first invoice.'
              : sub ? 'Starts the subscription.'
              : 'One-time charge.'}
          </span>
        </div>
      )}

      {link && (
        <div style={{ marginTop: 10, background: '#eff6ff',
                      border: '1px solid #93c5fd', borderRadius: 8,
                      padding: '10px 12px' }}>
          <b style={{ fontSize: 11, color: '#1d4ed8' }}>Payment link created</b>
          <div style={{ fontSize: 11, marginTop: 4, wordBreak: 'break-all' }}>
            <a href={link} target="_blank" rel="noreferrer">{link}</a>
          </div>
          {/* Said plainly, because a rep who thinks a link means money will
              stop chasing the customer. */}
          <div className="sw-subtle" style={{ fontSize: 10, marginTop: 6 }}>
            Send this to the customer. Nothing is paid until they complete
            checkout — payment shows here once the processor confirms it.
          </div>
        </div>
      )}
    </Card>
  )
}
