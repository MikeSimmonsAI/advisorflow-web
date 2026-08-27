/* The two ways a customer may buy, and the three numbers that make up the price.
 *
 * Rendered as two cards side by side rather than a dropdown, because the whole
 * point is that a rep and a customer SEE the choice: the regular monthly rate,
 * and the lower rate the 13-month commitment earns. A select box showing
 * "$500/month" tells nobody which of those they are looking at.
 *
 * Month-to-month is always first. It is the package's normal price.
 *
 * THE ONE-TIME IMPLEMENTATION FEE IS SHOWN SEPARATELY AND ALWAYS. It is not a
 * monthly figure, it does not change with the option, and folding it into
 * either card would restate a one-time charge as recurring. */

function usd(n, opts) {
  if (n == null) return null
  return '$' + Number(n).toLocaleString(undefined,
    opts || { minimumFractionDigits: 0, maximumFractionDigits: 2 })
}

export function BillingOptions({ pricing, selected, onChoose, disabled }) {
  if (!pricing) return null
  const opts = pricing.options || []
  const chosen = opts.find(o => o.billing_option === (selected || 'month_to_month'))

  return (
    <div className="sw-field">
      {/* One-time first: it is charged first, and it is the number most often
          forgotten when someone quotes "five hundred a month". */}
      {pricing.implementation_fee != null && (
        <div className="sw-billing-setup">
          <span>Implementation &amp; Setup<em> · one-time</em></span>
          <b>{usd(pricing.implementation_fee)}</b>
        </div>
      )}

      <label style={{ marginTop: 12, display: 'block' }}>MONTHLY PLATFORM</label>

      {!pricing.has_monthly_pricing ? (
        <div className="sw-notbuilt">
          <b>NO RECURRING RATE CONFIGURED</b>
          <p>
            This package has no monthly platform pricing set yet, so no billing
            option can be chosen. The implementation fee above is unaffected.
          </p>
        </div>
      ) : (
        <>
          <div className="sw-billing-options">
            {opts.map((o, i) => {
              const active = (selected || 'month_to_month') === o.billing_option
              const isTerm = o.billing_option === 'term_agreement'
              return (
                <div key={o.billing_option}>
                  {i > 0 && <div className="sw-billing-or">OR</div>}
                  <button type="button" disabled={disabled}
                          className={'sw-billing-card' + (active ? ' is-active' : '')}
                          onClick={() => onChoose(o.billing_option)}>
                    <span className="sw-billing-name">{o.billing_option_label}</span>
                    <span className="sw-billing-rate">
                      {o.monthly_rate != null ? usd(o.monthly_rate) : 'Custom'}
                      {o.monthly_rate != null && <em>/month</em>}
                    </span>
                    {isTerm && o.savings_per_month != null && (
                      <span className="sw-billing-save">
                        SAVE {usd(o.savings_per_month)}/MONTH
                      </span>
                    )}
                    {/* Stated on the card, not buried in terms. Thirteen months
                        means thirteen payments. */}
                    <span className="sw-billing-terms">
                      {isTerm
                        ? `${o.term_months} months · billed monthly · all ${o.payments_required} payments required`
                        : 'No commitment · billed monthly'}
                    </span>
                  </button>
                </div>
              )
            })}
          </div>

          {/* The commercial summary for whatever is selected. Total contract
              value leads for a fixed term because that is the commitment;
              month-to-month has no total, and inventing one would overstate
              the book. */}
          {chosen && (
            <div className="sw-billing-summary">
              <Row label="Implementation fee (one-time)"
                   value={usd(chosen.implementation_fee)} />
              <Row label="Monthly rate / MRR" value={
                chosen.monthly_rate != null ? usd(chosen.monthly_rate) + '/mo' : '—'} />
              <Row label="Term" value={
                chosen.term_months ? chosen.term_months + ' months' : 'Month-to-month'} />
              {chosen.recurring_contract_value != null && (
                <Row label={'Recurring contract value (' + chosen.term_months + ' × '
                            + usd(chosen.monthly_rate) + ')'}
                     value={usd(chosen.recurring_contract_value)} />
              )}
              {chosen.total_contract_value != null && chosen.term_months ? (
                <Row label="TOTAL CONTRACT VALUE"
                     value={usd(chosen.total_contract_value)} primary />
              ) : (
                <div className="sw-subtle" style={{ marginTop: 8 }}>
                  Month-to-month has no fixed term, so there is no contract total
                  to quote.
                </div>
              )}
            </div>
          )}
        </>
      )}

      {pricing.has_monthly_pricing && !pricing.has_term_option && (
        <div className="sw-subtle" style={{ marginTop: 8 }}>
          Month-to-month only — no agreement rate is configured for this package.
        </div>
      )}
    </div>
  )
}

function Row({ label, value, primary }) {
  return (
    <div className={'sw-billing-row' + (primary ? ' is-primary' : '')}>
      <span>{label}</span>
      <b>{value}</b>
    </div>
  )
}
