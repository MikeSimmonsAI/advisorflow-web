/* COMPENSATION PROJECTION — read-only forecast, kept apart from earned money.
 *
 * Every figure is rendered from the server's integer-cent `display` strings;
 * nothing is computed or re-rounded here, and there are no payout, approve,
 * bank, tax or message controls. Scope (seller vs management) is decided by
 * the server from the token — this page only passes optional filters.
 */
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import {
  LEGEND, TILE_LABELS, EMPTY_TEXT, usd, errorMessage, emptyState,
  projectionQuery, stageOptions,
} from '../../utils/compensationProjection'

const card = {
  background: 'var(--surface-card)', border: '1px solid var(--border-default)',
  borderRadius: 10, padding: 14, minWidth: 0,
}
const grid = {
  display: 'grid', gap: 12, marginBottom: 16,
  gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))',
}

function Tile({ label, money, sub, dashed }) {
  return (
    <div style={{ ...card, borderStyle: dashed ? 'dashed' : 'solid' }}>
      <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{label}</div>
      <div style={{ color: 'var(--text-strong)', fontSize: 22, fontWeight: 600 }}>
        {usd(money)}
      </div>
      {sub ? <div style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>{sub}</div> : null}
    </div>
  )
}

export default function CompensationProjection() {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [stage, setStage] = useState('')
  // Optional ?brand_sales_org_id= for a user who spans brands. It is only a
  // request: the server still decides whether this caller may see that brand.
  const [params] = useSearchParams()
  const brand = params.get('brand_sales_org_id') || ''

  useEffect(() => {
    setErr('')
    setD(null)
    api.get('/sales/compensation/projection' + projectionQuery(brand, stage))
      .then(setD)
      .catch(e => { setD(null); setErr(errorMessage(e)) })
  }, [stage, brand])

  const f = d?.forecast
  const e = d?.earned
  const stages = d ? stageOptions(d.deals) : []
  const state = emptyState(d)

  return (
    <SalesShell title="Compensation Projection"
                subtitle="Forecast only — not earned, not payable, not owed.">
      {err ? <div className="sw-err" role="alert">{err}</div> : null}
      {!d && !err ? <div className="sw-muted">Loading…</div> : null}
      {d ? (
        <>
          <div style={{ color: 'var(--text-body)', marginBottom: 12 }}>{d.disclaimer}</div>
          <dl className="cp-legend" aria-label="Legend" style={{ ...card, ...grid, marginBottom: 12 }}>
            {LEGEND.map(x => (
              <div key={x.key} data-kind={x.kind}>
                <dt style={{ color: 'var(--text-strong)', fontWeight: 600 }}>{x.label}</dt>
                <dd style={{ color: 'var(--text-muted)', fontSize: 12, margin: 0 }}>{x.text}</dd>
              </div>
            ))}
          </dl>
          {state === 'no_records' || state === 'blocked' ? (
            <div style={card} role="status">{EMPTY_TEXT[state]}</div>
          ) : null}
          <label style={{ color: 'var(--text-muted)', fontSize: 13 }}>
            Stage{' '}
            <select value={stage} onChange={ev => setStage(ev.target.value)}>
              <option value="">All stages</option>
              {stages.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>

          <h3 style={{ color: 'var(--text-strong)' }}>Forecast — not earned ({d.scope})</h3>
          <div style={grid}>
            <Tile dashed label={TILE_LABELS.gross} money={f.gross_sales}
                  sub={f.deal_count + ' open deal(s)'} />
            <Tile dashed label={TILE_LABELS.weightedGross} money={f.weighted_gross_sales}
                  sub={f.weighted_gross_sales.cents == null ? 'No stage probability configured' : null} />
            <Tile dashed label={TILE_LABELS.commission} money={f.commission} />
            <Tile dashed label={TILE_LABELS.weightedCommission} money={f.weighted_commission} />
            <Tile dashed label={TILE_LABELS.pending} money={d.pending.commission}
                  sub={d.pending.deal_count + ' deal(s)'} />
          </div>

          <h3 style={{ color: 'var(--text-strong)' }}>Earned from collected payments (on hold, payable, paid)</h3>
          <div style={grid}>
            <Tile label={TILE_LABELS.onHold} money={e.on_hold} sub={e.on_hold.count + ' entry(ies)'} />
            <Tile label={TILE_LABELS.payable} money={e.payable} sub={e.payable.count + ' entry(ies)'} />
            <Tile label={TILE_LABELS.paid} money={e.paid} sub={e.paid.count + ' entry(ies)'} />
            <Tile label={TILE_LABELS.earned} money={e.total} />
          </div>

          {d.blockers.length ? (
            <div style={card} role="status">
              <strong style={{ color: 'var(--text-strong)' }}>Blockers (configuration — amounts not counted)</strong>
              <ul>{d.blockers.map(b => <li key={b}>{b}</li>)}</ul>
            </div>
          ) : null}

          <h3 style={{ color: 'var(--text-strong)' }}>Deals</h3>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', color: 'var(--text-body)', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ textAlign: 'left' }}>
                  <th>Deal</th><th>Stage</th><th>Probability</th><th>Gross</th>
                  <th>Forecast commission</th><th>Weighted forecast</th><th>Rule / plan</th><th>Status</th>
                </tr>
              </thead>
              <tbody>
                {d.deals.map(x => (
                  <tr key={x.opportunity_id} style={{ borderTop: '1px solid var(--border-default)' }}>
                    <td>{x.company_name}</td>
                    <td>{x.stage}</td>
                    <td>{x.probability_bp == null ? 'not set' : (x.probability_bp / 100) + '%'}</td>
                    <td>{usd(x.gross_sales)}</td>
                    <td>{usd(x.commission)}</td>
                    <td>{x.weighted_commission ? usd(x.weighted_commission) : '—'}</td>
                    <td>{x.plan_name || '—'}{x.capped ? ' (package cap applied)' : ''}</td>
                    <td>{x.label}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {d.excluded.length ? (
            <>
              <h3 style={{ color: 'var(--text-strong)' }}>Excluded — not counted as $0</h3>
              <ul style={{ color: 'var(--text-body)' }}>
                {d.excluded.map(x => (
                  <li key={x.opportunity_id}>{x.company_name || x.opportunity_id}: {x.label}</li>
                ))}
              </ul>
            </>
          ) : null}
        </>
      ) : null}
    </SalesShell>
  )
}
