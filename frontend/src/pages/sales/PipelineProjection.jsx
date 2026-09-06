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
      const qs = brandSalesOrgId ? '?brand_sales_org_id=' + brandSalesOrgId : ''
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
              <div className="sw-billing-summary">
                <Row label="Pipeline value"
                     value={usd(data.pipeline_value)} primary />
                <Row label="Implementation / setup in pipeline"
                     value={usd(data.pipeline_implementation)} />
                <Row label="Recurring contract value in pipeline"
                     value={usd(data.pipeline_recurring_contract_value)} />
                <Row
                  label="Weighted / expected revenue"
                  value={data.weighted_available
                    ? usd(data.weighted_pipeline_value)
                    : 'Not configured'}
                  muted={!data.weighted_available}
                />
              </div>

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
