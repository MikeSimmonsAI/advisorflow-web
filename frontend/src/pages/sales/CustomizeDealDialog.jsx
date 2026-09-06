/* CUSTOMIZE DEAL — negotiated pricing, with the floor visible while you type.
 *
 * THE SUMMARY IS THE POINT. A rep changing a monthly rate sees the term, the
 * recurring contract value and the total move underneath their hands, because
 * the number that matters commercially is the one at the bottom, not the one
 * they are editing.
 *
 * IT SHOWS THE ARITHMETIC, IT DOES NOT OWN IT. Every figure here is the same
 * calculation the server does, and the server recalculates on save regardless
 * of what this sends — the totals are never posted. A browser that could set a
 * total could set a total that disagrees with its own parts.
 *
 * MONTH-TO-MONTH HAS NO TOTAL, AND SAYS SO. There is deliberately no fallback
 * to a twelve- or thirteen-month figure: a contract value invented for a deal
 * with no term is a commitment nobody made.
 *
 * THE FLOOR IS SHOWN BEFORE IT IS HIT. The ceiling this person may discount to
 * is rendered beside the field, so a rep knows they are heading for approval
 * before they submit rather than after. The server decides; this only warns.
 */
import { useEffect, useMemo, useState } from 'react'

function usd(n) {
  if (n == null || n === '') return null
  return '$' + Number(n).toLocaleString(undefined,
    { minimumFractionDigits: 0, maximumFractionDigits: 2 })
}

function num(v) {
  if (v === '' || v == null) return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

export default function CustomizeDealDialog({ opp, pkg, onCancel, onSave, saving }) {
  const billing = opp.billing || {}
  const auth = opp.pricing_authority || {}

  const [option, setOption] = useState(opp.billing_option || 'month_to_month')
  const [setupFee, setSetupFee] = useState(
    opp.implementation_fee != null ? String(opp.implementation_fee) : '')
  const [unitPrice, setUnitPrice] = useState(
    opp.custom_unit_price != null ? String(opp.custom_unit_price) : '')
  const [unitLabel, setUnitLabel] = useState(opp.custom_unit_label || '')
  const [minUnits, setMinUnits] = useState(
    opp.custom_min_units != null ? String(opp.custom_min_units) : '1')
  const [termMonths, setTermMonths] = useState(
    opp.custom_term_months != null ? String(opp.custom_term_months) : '')
  const [internalNote, setInternalNote] = useState(opp.pricing_notes_internal || '')
  const [customerNote, setCustomerNote] = useState(opp.pricing_description_customer || '')

  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape' && !saving) onCancel() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [saving, onCancel])

  // ── The live commercial picture ────────────────────────────────────────────
  const summary = useMemo(() => {
    const unit = num(unitPrice)
    const units = Math.max(1, num(minUnits) || 1)
    const mrr = unit == null ? null : unit * units
    const term = option === 'term_agreement' ? num(termMonths) : null
    const setup = num(setupFee) != null ? num(setupFee)
      : (billing.implementation_fee != null ? Number(billing.implementation_fee) : null)
    // Only a committed term has a recurring total. Month-to-month gets null,
    // never a guess.
    const rcv = (mrr != null && term) ? mrr * term : null
    const tcv = rcv != null ? rcv + (setup || 0) : null
    return { mrr, term, setup, rcv, tcv }
  }, [unitPrice, minUnits, termMonths, option, setupFee, billing.implementation_fee])

  // ── Will this need a manager? ──────────────────────────────────────────────
  // A warning, not a gate. The server is the authority and refuses regardless
  // of what this renders.
  const floor = useMemo(() => {
    const catalogueMonthly = option === 'term_agreement'
      ? (pkg && pkg.pricing ? pkg.pricing.contract_monthly_price : null)
      : (pkg && pkg.pricing ? pkg.pricing.monthly_price : null)
    const capPct = auth.max_discount_pct_monthly
    if (catalogueMonthly == null || capPct == null || summary.mrr == null) return null
    const floorAmount = Number(catalogueMonthly) * (1 - Number(capPct) / 100)
    return {
      catalogue: Number(catalogueMonthly),
      capPct: Number(capPct),
      floorAmount,
      breached: summary.mrr < floorAmount - 0.005,
    }
  }, [pkg, option, auth.max_discount_pct_monthly, summary.mrr])

  const ready = !saving && (unitPrice === '' || num(unitPrice) != null)

  function submit(e) {
    e.preventDefault()
    if (!ready) return
    // Rates and terms only. No totals: the server recalculates, and sending a
    // total this screen computed would be a second source of truth for money.
    onSave({
      billing_option: option,
      implementation_fee: setupFee === '' ? null : Number(setupFee),
      custom_unit_price: unitPrice === '' ? null : Number(unitPrice),
      custom_unit_label: unitLabel.trim() || null,
      custom_min_units: minUnits === '' ? null : Number(minUnits),
      custom_term_months: (option === 'term_agreement' && termMonths !== '')
        ? Number(termMonths) : null,
      pricing_notes_internal: internalNote.trim() || null,
      pricing_description_customer: customerNote.trim() || null,
    })
  }

  return (
    <div role="dialog" aria-modal="true" aria-label="Customize deal"
         onClick={() => { if (!saving) onCancel() }}
         style={{ position: 'fixed', inset: 0, background: 'rgba(8,14,22,.66)',
                  zIndex: 400, display: 'flex', alignItems: 'flex-start',
                  justifyContent: 'center', padding: '40px 18px', overflowY: 'auto' }}>
      <form onClick={e => e.stopPropagation()} onSubmit={submit}
            className="sw-card"
            style={{ maxWidth: 620, width: '100%', padding: 24 }}>

        <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: '.12em',
                      color: '#7a8ea3', marginBottom: 6 }}>
          CUSTOM DEAL
        </div>
        <h3 style={{ margin: '0 0 4px', fontSize: 17 }}>
          {opp.company_name}
        </h3>
        <p className="sw-subtle" style={{ marginTop: 0, marginBottom: 18 }}>
          Negotiated pricing for this deal only. The catalogue is not changed,
          and no other deal moves.
        </p>

        <div className="sw-field">
          <label>BILLING</label>
          <div className="sw-flex">
            {['month_to_month', 'term_agreement'].map(o => (
              <button key={o} type="button"
                      className={'sw-btn' + (option === o ? ' sw-primary' : '')}
                      onClick={() => setOption(o)} disabled={saving}>
                {o === 'month_to_month' ? 'Month-to-month' : 'Fixed term'}
              </button>
            ))}
          </div>
        </div>

        <div className="sw-grid-even">
          <div className="sw-field">
            <label>IMPLEMENTATION / SETUP <span style={{ fontWeight: 400 }}>(one-time)</span></label>
            <input className="sw-input" type="number" step="0.01" value={setupFee}
                   disabled={saving} onChange={e => setSetupFee(e.target.value)}
                   placeholder={billing.implementation_fee != null
                     ? 'Package default ' + usd(billing.implementation_fee) : 'None'} />
          </div>
          {option === 'term_agreement' && (
            <div className="sw-field">
              <label>TERM <span style={{ fontWeight: 400 }}>(months)</span></label>
              <input className="sw-input" type="number" step="1" min="1" value={termMonths}
                     disabled={saving} onChange={e => setTermMonths(e.target.value)}
                     placeholder="e.g. 13" />
            </div>
          )}
        </div>

        <div className="sw-grid-even">
          <div className="sw-field">
            <label>MONTHLY RATE</label>
            <input className="sw-input" type="number" step="0.01" value={unitPrice}
                   disabled={saving} onChange={e => setUnitPrice(e.target.value)}
                   placeholder="Per unit, or the whole monthly rate" />
          </div>
          <div className="sw-field">
            <label>MINIMUM UNITS</label>
            <input className="sw-input" type="number" step="1" min="1" value={minUnits}
                   disabled={saving} onChange={e => setMinUnits(e.target.value)} />
          </div>
        </div>

        <div className="sw-field">
          <label>UNIT LABEL <span style={{ fontWeight: 400 }}>(optional)</span></label>
          <input className="sw-input" value={unitLabel} disabled={saving}
                 onChange={e => setUnitLabel(e.target.value)}
                 placeholder="e.g. active paying customer — leave blank for a flat rate" />
          <div className="sw-subtle" style={{ marginTop: 5 }}>
            With a label the customer's document shows the arithmetic
            (&ldquo;$250 per active paying customer, 15 minimum&rdquo;) rather than
            just a total.
          </div>
        </div>

        {/* ── What this deal actually is ──────────────────────────────────── */}
        <div className="sw-billing-summary" style={{ marginTop: 6 }}>
          <Row label="Implementation (one-time)" value={usd(summary.setup) || '—'} />
          <Row label="MRR" value={summary.mrr != null ? usd(summary.mrr) + '/mo' : '—'} />
          <Row label="Term" value={summary.term ? summary.term + ' months' : 'Month-to-month'} />
          {summary.rcv != null && (
            <Row label={'Recurring contract value (' + summary.term + ' × '
                        + usd(summary.mrr) + ')'} value={usd(summary.rcv)} />
          )}
          {summary.tcv != null ? (
            <Row label="TOTAL CONTRACT VALUE" value={usd(summary.tcv)} primary />
          ) : (
            <div className="sw-subtle" style={{ marginTop: 8 }}>
              Month-to-month has no fixed term, so there is no contract total to
              quote.
            </div>
          )}
        </div>

        {/* ── The floor, before it is hit ─────────────────────────────────── */}
        {floor && (
          <div className={floor.breached ? 'sw-notbuilt sw-mt' : 'sw-subtle sw-mt'}
               style={floor.breached ? { borderColor: 'rgba(255,170,60,.45)' } : null}>
            {floor.breached ? (
              <>
                <b>NEEDS MANAGER APPROVAL</b>
                <p>
                  Your limit on this package is {floor.capPct}% off
                  {' '}{usd(floor.catalogue)}/mo, so {usd(floor.floorAmount)}/mo is
                  as low as you can go on your own. You can still save this — it
                  goes to your manager as a request, and nothing changes on the
                  deal until they agree.
                </p>
              </>
            ) : (
              <>Within your authority — {floor.capPct}% off {usd(floor.catalogue)}/mo
                 means you can go to {usd(floor.floorAmount)}/mo without approval.</>
            )}
          </div>
        )}

        <div className="sw-field sw-mt">
          <label>WHY — INTERNAL ONLY</label>
          <textarea className="sw-input" rows={2} value={internalNote} disabled={saving}
                    onChange={e => setInternalNote(e.target.value)}
                    placeholder="What was negotiated and why. Your manager sees this." />
          <div className="sw-subtle" style={{ marginTop: 5 }}>
            Never shown to the customer, never sent in an email, never on the
            proposal.
          </div>
        </div>

        <div className="sw-field">
          <label>CUSTOMER-FACING DESCRIPTION <span style={{ fontWeight: 400 }}>(optional)</span></label>
          <textarea className="sw-input" rows={2} value={customerNote} disabled={saving}
                    onChange={e => setCustomerNote(e.target.value)}
                    placeholder="How to describe these terms on their document." />
        </div>

        <div className="sw-flex sw-mt" style={{ justifyContent: 'flex-end' }}>
          <button type="button" className="sw-btn" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button type="submit" className="sw-btn sw-primary" disabled={!ready}>
            {saving ? 'Saving…' : (floor && floor.breached
              ? 'Send for approval' : 'Save deal pricing')}
          </button>
        </div>
      </form>
    </div>
  )
}

function Row({ label, value, primary }) {
  return (
    <div className={'sw-billing-row' + (primary ? ' is-primary' : '')}>
      <span>{label}</span><b>{value}</b>
    </div>
  )
}
