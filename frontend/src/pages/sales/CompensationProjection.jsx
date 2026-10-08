/* COMPENSATION PROJECTION — read-only forecast, kept apart from earned money.
 *
 * Every figure is rendered from the server's integer-cent `display` strings;
 * nothing is computed or re-rounded here, and there are no payout, approve,
 * bank, tax or message controls. Scope (seller vs management) is decided by
 * the server from the token — this page only passes optional filters.
 */
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import SalesShell from './SalesShell'

const card = {
  background: 'var(--surface-card)', border: '1px solid var(--border-default)',
  borderRadius: 10, padding: 14, minWidth: 0,
}
const grid = {
  display: 'grid', gap: 12, marginBottom: 16,
  gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))',
}

function usd(m) {
  return m && m.display != null ? '$' + m.display : '—'
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

function errorText(e) {
  const s = e?.status
  if (s === 403) return 'Not authorized to view this compensation scope.'
  if (s === 422) return 'Projection refused — stored data is inconsistent: ' + (e?.message || '')
  if (s === 400) return 'Choose a brand to view.'
  return e?.message || 'Could not load the projection.'
}

export default function CompensationProjection() {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [stage, setStage] = useState('')

  useEffect(() => {
    setErr('')
    api.get('/sales/compensation/projection' + (stage ? '?stage=' + encodeURIComponent(stage) : ''))
      .then(setD)
      .catch(e => { setD(null); setErr(errorText(e)) })
  }, [stage])

  const f = d?.forecast
  const e = d?.earned
  const stages = d ? Array.from(new Set((d.deals || []).map(x => x.stage))) : []

  return (
    <SalesShell title="Compensation Projection"
                subtitle="Forecast only — not earned, not payable, not owed.">
      {err ? <div className="sw-err" role="alert">{err}</div> : null}
      {!d && !err ? <div className="sw-muted">Loading…</div> : null}
      {d ? (
        <>
          <div style={{ color: 'var(--text-body)', marginBottom: 12 }}>{d.disclaimer}</div>
          <label style={{ color: 'var(--text-muted)', fontSize: 13 }}>
            Stage{' '}
            <select value={stage} onChange={ev => setStage(ev.target.value)}>
              <option value="">All stages</option>
              {stages.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>

          <h3 style={{ color: 'var(--text-strong)' }}>Forecast ({d.scope})</h3>
          <div style={grid}>
            <Tile dashed label="Projected gross sales" money={f.gross_sales}
                  sub={f.deal_count + ' open deal(s)'} />
            <Tile dashed label="Expected weighted revenue" money={f.weighted_gross_sales}
                  sub={f.weighted_gross_sales.cents == null ? 'No stage probability configured' : null} />
            <Tile dashed label="Projected commission" money={f.commission} />
            <Tile dashed label="Weighted commission" money={f.weighted_commission} />
            <Tile dashed label="Pending approval (not in forecast)" money={d.pending.commission}
                  sub={d.pending.deal_count + ' deal(s)'} />
          </div>

          <h3 style={{ color: 'var(--text-strong)' }}>Earned from collected payments</h3>
          <div style={grid}>
            <Tile label="On holdback" money={e.on_hold} sub={e.on_hold.count + ' entry(ies)'} />
            <Tile label="Holdback elapsed" money={e.payable} sub={e.payable.count + ' entry(ies)'} />
            <Tile label="Paid" money={e.paid} sub={e.paid.count + ' entry(ies)'} />
            <Tile label="Total earned" money={e.total} />
          </div>

          {d.blockers.length ? (
            <div style={card} role="status">
              <strong style={{ color: 'var(--text-strong)' }}>Blockers</strong>
              <ul>{d.blockers.map(b => <li key={b}>{b}</li>)}</ul>
            </div>
          ) : null}

          <h3 style={{ color: 'var(--text-strong)' }}>Deals</h3>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', color: 'var(--text-body)', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ textAlign: 'left' }}>
                  <th>Deal</th><th>Stage</th><th>Probability</th><th>Gross</th>
                  <th>Commission</th><th>Weighted</th><th>Rule / plan</th><th>Status</th>
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
