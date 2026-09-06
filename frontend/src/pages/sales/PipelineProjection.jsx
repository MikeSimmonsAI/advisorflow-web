/* PIPELINE FINANCIAL PROJECTION — a manager's card.
 *
 * EVERY NUMBER HERE DESCRIBES DEALS THAT HAVE NOT CLOSED. That is stated on the
 * card rather than left to the reader, because the difference between "we
 * expect to owe this" and "we owe this" is the difference between a forecast
 * and a payroll run, and a card that does not say which one it is will
 * eventually be read as the wrong one.
 *
 * NO FORMULA LIVES HERE. Every figure arrives computed from the server, which
 * uses the same compensation engine that pays a real commission. A rate in this
 * file would be a second source of truth for what the company pays people.
 *
 * UNAVAILABLE IS NOT ZERO. Weighted revenue with no configured probabilities
 * renders as "not configured", and an unconfigured compensation plan says so —
 * neither shows $0, which reads as a decision nobody made.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'

function usd(n) {
  if (n == null) return null
  return '$' + Number(n).toLocaleString(undefined,
    { minimumFractionDigits: 0, maximumFractionDigits: 0 })
}

export default function PipelineProjection({ brandSalesOrgId }) {
  const [data, setData] = useState(null)
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      // ALWAYS WITH THE DEAL ROWS. Every total on this card is the sum of a
      // column in that table, and a headline nobody can reconcile to the deals
      // behind it is exactly how $0 of recurring revenue went unquestioned.
      const qs = '?include_deals=true' +
                 (brandSalesOrgId ? '&brand_sales_org_id=' + brandSalesOrgId : '')
      setData(await api.get('/sales/manager/pipeline-projection' + qs))
    } catch (e) {
      setErr(e?.message || 'Could not load the projection.')
      setData(null)
    } finally { setLoading(false) }
  }, [brandSalesOrgId])

  // Loaded when opened, and reloaded on demand. The numbers move whenever a
  // deal, a price or a stage moves, so a stale card is worse than a closed one.
  useEffect(() => { if (open && !data) load() }, [open, data, load])

  return (
    <div className="sw-card sw-mt">
      <div className="sw-flex" style={{ justifyContent: 'space-between',
                                        alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: '.1em',
                        color: '#16324f' }}>
            PIPELINE FINANCIAL PROJECTION
          </div>
          <div className="sw-subtle" style={{ marginTop: 3 }}>
            Open deals on current terms. Not earned, not payable, not owed.
          </div>
        </div>
        <div className="sw-flex">
          {open && (
            <button className="sw-btn" onClick={load} disabled={loading}>
              {loading ? '…' : 'Refresh'}
            </button>
          )}
          <button className="sw-btn" onClick={() => setOpen(o => !o)}>
            {open ? 'Hide' : 'Show'}
          </button>
        </div>
      </div>

      {open && (
        <div style={{ marginTop: 14 }}>
          {err && <div className="sw-notbuilt"><b>COULD NOT LOAD</b><p>{err}</p></div>}
          {loading && !data && <div className="sw-subtle">Loading…</div>}

          {data && (
            <>
              {/* FOUR FIGURES, NOT ONE. A one-time fee, a committed recurring
                  total and an open-ended monthly rate are three different
                  promises, and the single "pipeline value" that used to lead
                  this card was in practice just the sum of the setup fees —
                  which is how it came to read $75,800 of implementation
                  against $0 of recurring. */}
              <div className="sw-billing-summary">
                <Row label="One-time implementation / setup"
                     value={usd(data.pipeline_implementation)} />
                <Row label="Fixed-term recurring contract value"
                     value={usd(data.pipeline_recurring_contract_value)} />
                <Row
                  label="Month-to-month MRR"
                  value={data.pipeline_monthly_recurring
                    ? usd(data.pipeline_monthly_recurring) + '/month'
                    : '—'}
                />
                <Row label="Total fixed contract value"
                     value={usd(data.pipeline_total_fixed_contract_value
                                ?? data.pipeline_value)} primary />
                <Row
                  label="Weighted / expected revenue"
                  value={data.weighted_available
                    ? usd(data.weighted_pipeline_value)
                    : 'Not configured'}
                  muted={!data.weighted_available}
                />
              </div>

              {data.pipeline_monthly_recurring > 0 && (
                <div className="sw-subtle" style={{ marginTop: 6 }}>
                  {data.month_to_month_deal_count}{' '}
                  {data.month_to_month_deal_count === 1 ? 'deal is' : 'deals are'}
                  {' '}month-to-month. Their monthly rate is real revenue but no
                  number of months has been agreed, so it is shown per month and
                  deliberately not multiplied into a contract total.
                </div>
              )}

              {/* NOT COUNTED AS ZERO RECURRING. Saying "$0 recurring" about a
                  deal nobody has priced is a claim; saying the pricing is
                  incomplete is the truth. */}
              {data.pricing_incomplete_count > 0 && (
                <div className="sw-notbuilt sw-mt"
                     style={{ borderColor: 'rgba(255,170,60,.45)' }}>
                  <b>PRICING INCOMPLETE — {data.pricing_incomplete_count}{' '}
                    {data.pricing_incomplete_count === 1 ? 'OPPORTUNITY' : 'OPPORTUNITIES'}</b>
                  <p>
                    Their recurring terms cannot be established from a package, a
                    custom rate or a sent proposal. Any one-time value they carry
                    is counted above; their recurring value is excluded rather
                    than assumed to be nothing, so the recurring figure is a
                    floor. Open the deals below to see which.
                  </p>
                </div>
              )}

              {!data.weighted_available && (
                <div className="sw-subtle" style={{ marginTop: 6 }}>
                  Weighted revenue needs a win probability per stage. None are
                  configured for this brand, so no expected figure is shown
                  rather than one based on assumed odds.
                </div>
              )}

              {data.compensation_plan_configured ? (
                <div className="sw-billing-summary sw-mt">
                  <Row label="Projected direct commissions"
                       value={usd(data.projected_direct_commissions)} />
                  <Row label="Projected manager / upline overrides"
                       value={usd(data.projected_manager_overrides)} />
                  <Row label="Total projected sales compensation"
                       value={usd(data.projected_total_compensation)} />
                  <Row label="Projected revenue after sales comp"
                       value={usd(data.projected_revenue_after_compensation)} primary />
                </div>
              ) : (
                <div className="sw-notbuilt sw-mt">
                  <b>NO COMPENSATION PLAN CONFIGURED</b>
                  <p>
                    No commission can be projected for this sales organization
                    until a plan and its rates exist. Nothing is assumed in the
                    meantime.
                  </p>
                </div>
              )}

              {/* HELD OUT OF THE TOTALS ABOVE, ON PURPOSE. An unapproved deal
                  is not a promise; if it counted, a rep could move the
                  company's projected payroll by typing a number. */}
              {data.pending_approval_deal_count > 0 && (
                <div className="sw-notbuilt sw-mt"
                     style={{ borderColor: 'rgba(255,170,60,.45)' }}>
                  <b>PENDING APPROVAL — NOT INCLUDED ABOVE</b>
                  <p>
                    {data.pending_approval_deal_count}{' '}
                    {data.pending_approval_deal_count === 1 ? 'deal is' : 'deals are'}
                    {' '}priced below the approved floor and waiting on a manager.
                    They would add {usd(data.pending_approval_compensation)} in
                    compensation if approved as asked, and are excluded from the
                    figures above until then.
                  </p>
                </div>
              )}

              {data.deals && data.deals.length > 0 && (
                <details className="sw-mt">
                  <summary style={{ cursor: 'pointer', fontSize: 11,
                                    fontWeight: 800, letterSpacing: '.08em',
                                    color: '#16324f' }}>
                    DEAL-BY-DEAL BREAKDOWN ({data.deals.length})
                  </summary>
                  <div style={{ overflowX: 'auto', marginTop: 10 }}>
                    <table className="sw-table" style={{ fontSize: 12,
                                                         minWidth: 760 }}>
                      <thead>
                        <tr>
                          <th>Deal</th><th>Package</th><th>Stage</th>
                          <th style={{ textAlign: 'right' }}>Setup</th>
                          <th style={{ textAlign: 'right' }}>MRR</th>
                          <th style={{ textAlign: 'right' }}>Term</th>
                          <th style={{ textAlign: 'right' }}>RCV</th>
                          <th style={{ textAlign: 'right' }}>Fixed TCV</th>
                          <th>Priced from</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.deals.map(d => (
                          <tr key={d.opportunity_id}>
                            <td>
                              {d.company_name}
                              {!d.pricing_complete && (
                                <div style={{ fontSize: 10, fontWeight: 700,
                                              color: '#b26a00', marginTop: 2 }}
                                     title={d.incomplete_reason || ''}>
                                  PRICING INCOMPLETE
                                </div>
                              )}
                            </td>
                            <td>{d.package_name || '—'}</td>
                            <td>{(d.stage || '').replace(/_/g, ' ')}</td>
                            <td style={{ textAlign: 'right' }}>{usd(d.one_time_value) ?? '—'}</td>
                            <td style={{ textAlign: 'right' }}>
                              {d.mrr != null ? usd(d.mrr) + '/mo' : '—'}
                            </td>
                            <td style={{ textAlign: 'right' }}>
                              {d.term_months ? d.term_months + ' mo'
                                : d.structure === 'month_to_month' ? 'M2M' : '—'}
                            </td>
                            <td style={{ textAlign: 'right' }}>
                              {d.recurring_contract_value != null
                                ? usd(d.recurring_contract_value) : '—'}
                            </td>
                            <td style={{ textAlign: 'right' }}>
                              {usd(d.fixed_contract_value) ?? '—'}
                            </td>
                            <td style={{ fontSize: 11 }}>
                              {d.pricing_source_label}
                              {d.proposal_number ? ' ' + d.proposal_number : ''}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="sw-subtle" style={{ marginTop: 8 }}>
                    Setup and RCV in this table add up to the totals above. A
                    month-to-month row shows an MRR and no RCV on purpose — its
                    setup fee is committed, its monthly rate has no end date.
                  </div>
                </details>
              )}

              <div className="sw-subtle" style={{ marginTop: 10 }}>
                {data.opportunity_count} open{' '}
                {data.opportunity_count === 1 ? 'deal' : 'deals'}. {data.disclaimer}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

function Row({ label, value, primary, muted }) {
  return (
    <div className={'sw-billing-row' + (primary ? ' is-primary' : '')}>
      <span>{label}</span>
      <b style={muted ? { color: '#8496a4', fontWeight: 600 } : null}>
        {value ?? '—'}
      </b>
    </div>
  )
}
