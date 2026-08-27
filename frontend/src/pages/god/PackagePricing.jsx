/**
 * God Mode — the package price list, editable.
 *
 * Three numbers per package, and they are not interchangeable:
 *
 *   Implementation fee      ONE-TIME. Charged once, identical under either
 *                           billing option, never folded into a monthly figure.
 *   Month-to-month rate     the NORMAL monthly price. No commitment.
 *   Term rate + term        the LOWER monthly price earned by committing. Every
 *                           month of the term is billed; there is no free month
 *                           and no annual prepayment.
 *
 * The server refuses a term rate at or above the month-to-month rate, and a
 * term of zero or less. Its refusals are shown here verbatim rather than
 * paraphrased, because the message names the number that is wrong.
 *
 * The legacy one-time `price` column is not editable from here — deliberately.
 * It is where the original one-time figures live, and a screen called "pricing"
 * is exactly how one of those gets overwritten with a monthly rate by accident.
 * Saving an implementation fee writes `setup_fee`, which takes precedence over
 * it everywhere, so the correct number wins without the old one being lost.
 */
import { useState } from 'react'
import { api } from '../../api/client'
import { Panel, Empty, money, errText } from './GodOpsShared'

function n(v) {
  if (v === '' || v === null || v === undefined) return null
  const x = Number(v)
  return Number.isFinite(x) ? x : null
}

function Row({ pkg, onSaved }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const pr = pkg.pricing || {}
  const cur = pkg.currency || 'USD'

  const [fee, setFee] = useState(pkg.setup_fee ?? '')
  const [m2m, setM2m] = useState(pr.monthly_price ?? '')
  const [term, setTerm] = useState(pr.contract_monthly_price ?? '')
  const [months, setMonths] = useState(pr.contract_term_months ?? '')

  function reset() {
    setFee(pkg.setup_fee ?? '')
    setM2m(pr.monthly_price ?? '')
    setTerm(pr.contract_monthly_price ?? '')
    setMonths(pr.contract_term_months ?? '')
    setErr('')
  }

  async function save() {
    setBusy(true); setErr('')
    try {
      // Only the fields on this form are sent. Anything omitted is left alone
      // by the server rather than being nulled out by an empty control.
      await api.patch('/god/ops/packages/' + pkg.id + '/pricing', {
        setup_fee: n(fee),
        monthly_price: n(m2m),
        contract_monthly_price: n(term),
        contract_term_months: n(months),
      })
      setOpen(false)
      await onSaved()
    } catch (e) {
      setErr(errText(e))
    } finally { setBusy(false) }
  }

  const feeShown = pr.implementation_fee
  const legacyOnly = pkg.setup_fee === null || pkg.setup_fee === undefined

  return (
    <>
      <tr>
        <td data-label="Package">
          {pkg.name}
          {!pkg.is_active && <span className="go-badge" style={{ marginLeft: 8 }}>inactive</span>}
          <div style={{ fontSize: 11, color: 'var(--go-dim)', marginTop: 2 }}>
            <code>{pkg.key}</code>
          </div>
        </td>
        <td data-label="Implementation" className="num">
          {pkg.is_custom ? 'custom'
            : feeShown !== null && feeShown !== undefined ? money(feeShown, cur) : '—'}
          {legacyOnly && feeShown !== null && feeShown !== undefined && (
            <div style={{ fontSize: 10, color: 'var(--go-dim)' }}>from legacy price</div>
          )}
        </td>
        <td data-label="Month-to-month" className="num">
          {pr.monthly_price !== null && pr.monthly_price !== undefined
            ? money(pr.monthly_price, cur) + '/mo' : '—'}
        </td>
        <td data-label="Term rate" className="num">
          {pr.contract_monthly_price !== null && pr.contract_monthly_price !== undefined
            ? money(pr.contract_monthly_price, cur) + '/mo' : '—'}
        </td>
        <td data-label="Term" className="num">
          {pr.has_term_option && pr.contract_term_months
            ? pr.contract_term_months + ' months' : '—'}
        </td>
        <td data-label="Saving" className="num">
          {pr.savings_per_month
            ? <span style={{ color: 'var(--go-green)' }}>{money(pr.savings_per_month, cur)}/mo</span>
            : '—'}
        </td>
        <td data-label="">
          <button className="go-btn sm ghost"
                  onClick={() => { if (open) reset(); setOpen(!open) }}>
            {open ? 'Cancel' : 'Edit pricing'}
          </button>
        </td>
      </tr>

      {open && (
        <tr>
          <td colSpan={7} style={{ background: 'var(--go-panel-2)' }}>
            <div className="go-fields">
              <div className="go-field">
                <label>Implementation fee — one-time</label>
                <input type="number" min="0" step="1" value={fee}
                       onChange={e => setFee(e.target.value)}
                       placeholder={feeShown !== null && feeShown !== undefined ? String(feeShown) : ''} />
              </div>
              <div className="go-field">
                <label>Month-to-month rate — per month</label>
                <input type="number" min="0" step="1" value={m2m}
                       onChange={e => setM2m(e.target.value)} />
              </div>
              <div className="go-field">
                <label>Term rate — per month</label>
                <input type="number" min="0" step="1" value={term}
                       onChange={e => setTerm(e.target.value)} />
              </div>
              <div className="go-field">
                <label>Term length — months</label>
                <input type="number" min="1" step="1" value={months}
                       onChange={e => setMonths(e.target.value)} />
              </div>
            </div>

            <p style={{ margin: '10px 0 0', fontSize: 12, color: 'var(--go-dim)' }}>
              The term rate must be lower than the month-to-month rate, and every
              month of the term is billed monthly — no free month, no annual
              prepayment. Clearing the term rate withdraws the term option and
              leaves month-to-month as the only choice.
            </p>
            <p style={{ margin: '6px 0 0', fontSize: 12, color: 'var(--go-dim)' }}>
              Open deals keep the price they were quoted. New proposals, and any
              proposal republished after this, pick the new numbers up.
            </p>

            {err && <div className="go-note warn" style={{ marginTop: 10 }}>{err}</div>}

            <div className="go-actions" style={{ marginTop: 12 }}>
              <button className="go-btn sm" onClick={save} disabled={busy}>
                {busy ? 'Saving…' : 'Save pricing'}
              </button>
              <button className="go-btn sm ghost" disabled={busy}
                      onClick={() => { reset(); setOpen(false) }}>
                Cancel
              </button>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

export default function PackagePricing({ packages, onSaved }) {
  const list = packages || []
  return (
    <Panel title="Package pricing" count={list.length}>
      {!list.length ? (
        <Empty>This platform has no packages configured.</Empty>
      ) : (
        <table className="go-table">
          <thead>
            <tr>
              <th>Package</th>
              <th className="num">Implementation</th>
              <th className="num">Month-to-month</th>
              <th className="num">Term rate</th>
              <th className="num">Term</th>
              <th className="num">Saving</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {list.map(p => <Row key={p.id} pkg={p} onSaved={onSaved} />)}
          </tbody>
        </table>
      )}
      <div className="go-body" style={{ borderTop: '1px solid var(--go-line)' }}>
        <p style={{ margin: 0, fontSize: 12, color: 'var(--go-dim)' }}>
          The implementation fee is charged once and is the same under either
          billing option. Sales packages are not the legacy Stripe plans, and
          nothing here is wired to charging — provisioning records billing
          <em> intent</em> only.
        </p>
      </div>
    </Panel>
  )
}
