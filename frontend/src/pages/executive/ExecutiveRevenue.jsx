/**
 * ExecutiveRevenue - where the money comes from, and where it does not.
 *
 * ===========================================================================
 * WHY THIS IS A SEPARATE PAGE AND NOT A COLUMN
 * ===========================================================================
 *
 * "Where is revenue coming from" is a different question from "which customers
 * need attention", even though both are answered from the same rows. This page
 * arranges those rows commercially: what each customer is worth per month, who
 * cannot be charged, and which plan limits are being hit.
 *
 * It reads the SAME endpoint Portfolio does. There is no second revenue query
 * anywhere - a revenue figure that disagreed with the finance screen would be
 * worse than no figure at all, and the way that happens is two pieces of code
 * both knowing how to price a plan.
 *
 * ===========================================================================
 * THE TOTAL IS NEVER SHOWN WITHOUT ITS COVERAGE
 * ===========================================================================
 *
 * A customer whose plan does not resolve cannot be priced. Folding them in at
 * zero would report a business earning nothing rather than a figure nobody can
 * produce, so:
 *
 *   - the total always states how many customers it covers
 *   - unpriced customers are LISTED, not averaged away
 *   - "not priced" is words in muted ink, never a dash that reads as nil
 *
 * WHAT IS NOT HERE. Invoices, payments and collections are a finance surface
 * with its own authority. An executive sees what the portfolio is worth per
 * month and which customers cannot be billed; they do not see or move money.
 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { Kpi, Val, money, num, plural } from './ExecUI'

export default function ExecutiveRevenue() {
  const nav = useNavigate()
  const [state, setState] = useState({ loading: true, data: null, error: null })

  useEffect(() => {
    api.get('/executive/portfolio/health?filter=all')
      .then(r => setState({ loading: false, data: r, error: null }))
      .catch(() => setState({
        loading: false, data: null,
        error: 'Could not load revenue. Please try again.' }))
  }, [])

  const { loading, data, error } = state
  if (loading) return <p className="ex-muted">Reading revenue…</p>
  if (error || !data) return <div className="ex-err">{error || 'No data.'}</div>

  const orgs = data.organizations
  const priced = orgs.filter(o => o.mrr !== null && o.mrr !== undefined)
  const unpriced = orgs.filter(o => o.mrr === null || o.mrr === undefined)
  const unbillable = orgs.filter(o => o.has_payment_method === false)
  const held = orgs.filter(o => o.held_leads > 0)
  const mrr = priced.length
    ? priced.reduce((a, o) => a + o.mrr, 0)
    : null

  const byValue = [...priced].sort((a, b) => b.mrr - a.mrr)

  if (!orgs.length) {
    return (
      <>
        <div className="ex-head">
          <div>
            <p className="ex-eyebrow">{data.platform_name}</p>
            <h1 className="ex-h1">Revenue</h1>
          </div>
        </div>
        <div className="ex-card">
          <div className="ex-blank">
            <b>No customers to bill yet.</b>
            <p>Revenue appears here once customers are provisioned onto this brand.</p>
          </div>
        </div>
      </>
    )
  }

  return (
    <>
      <div className="ex-head">
        <div>
          <p className="ex-eyebrow">{data.platform_name}</p>
          <h1 className="ex-h1">Revenue</h1>
          <p className="ex-sub">
            What this portfolio is worth per month, priced from your own
            brand's catalogue. Annual customers count as one month, the same
            way finance counts them.
          </p>
        </div>
      </div>

      <div className="ex-section">
        <div className="ex-kpis">
          <Kpi label="Monthly recurring" value={money(mrr)} lead
               absent="Not priced yet"
               sub={mrr === null
                 ? 'no customer has a plan this brand can price'
                 : unpriced.length
                   ? `across ${num(priced.length)} of ${num(orgs.length)} customers`
                   : `across all ${num(orgs.length)} customers`} />

          <Kpi label="Cannot be priced" value={num(unpriced.length)}
               alarm={unpriced.length > 0}
               sub={unpriced.length
                 ? 'no plan resolves — excluded from the total above, not counted as zero'
                 : 'every customer resolves to a plan'} />

          <Kpi label="Cannot be charged" value={num(unbillable.length)}
               alarm={unbillable.length > 0}
               sub={unbillable.length
                 ? 'no payment method on file'
                 : 'every customer has a payment method'} />

          <Kpi label="Hitting plan limits" value={num(held.length)}
               sub={held.length
                 ? 'inbound prospects are being held — the clearest upgrade case'
                 : 'no customer is at their lead limit'} />
        </div>
      </div>

      <div className="ex-section">
        <h2>By customer <span>· largest first</span></h2>
        <div className="ex-tablewrap">
          <table className="ex-table">
            <thead>
              <tr>
                <th>Organization</th>
                <th>Package</th>
                <th className="ex-num">Monthly</th>
                <th>Payment method</th>
                <th className="ex-num">Held leads</th>
                <th>Sold by</th>
              </tr>
            </thead>
            <tbody>
              {[...byValue, ...unpriced].map(o => (
                <tr key={o.id}>
                  <td>
                    <button className="ex-orgbtn"
                            onClick={() => nav(`/executive/organizations/${o.id}/view`)}>
                      {o.name}
                    </button>
                  </td>
                  <td><Val value={o.package} absent="no package assigned" /></td>
                  <td className="ex-num">
                    <Val value={money(o.mrr)} absent="not priced" />
                  </td>
                  <td>
                    {o.has_payment_method
                      ? <span className="ex-muted">on file</span>
                      : <span className="ex-flag sv-action_required">none on file</span>}
                  </td>
                  <td className="ex-num">
                    {o.held_leads
                      ? <span className="ex-flag">{num(o.held_leads)}</span>
                      : <span className="ex-none-inline">none</span>}
                  </td>
                  <td>
                    <Val value={o.originating_deal?.sold_by}
                         absent="not sold through the pipeline" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {unpriced.length ? (
        <div className="ex-section">
          <h2>Why {plural(unpriced.length, 'one customer is', `${num(unpriced.length)} customers are`)} not priced</h2>
          <div className="ex-card">
            <div className="ex-card-b">
              <p className="ex-sub" style={{ margin: 0 }}>
                {plural(unpriced.length, 'This customer', 'These customers')} could
                not be matched to a plan in this brand's catalogue, so{' '}
                {plural(unpriced.length, 'it is', 'they are')} excluded from the
                total above rather than counted as $0 — counting them as zero
                would report a business earning nothing instead of a figure
                nobody can produce.{' '}
                {unpriced.map(o => o.name).join(', ')}.
              </p>
            </div>
          </div>
        </div>
      ) : null}
    </>
  )
}
