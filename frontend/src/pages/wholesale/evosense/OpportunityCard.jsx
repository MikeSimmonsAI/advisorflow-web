/* THE OPPORTUNITY VIEW for one property (server: evosense/opportunity.py).
 *
 * Five things kept apart on purpose - seller opportunity, urgency, economics,
 * evidence confidence, engagement - then the documented priority blend, the
 * one next action and what is still missing. No number here is invented: when
 * the inputs for a spread are not supported, it says so and lists them.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Panel, Tag } from '../ds/ds'

const money = (v) => (v == null ? '—' : '$' + Number(v).toLocaleString())
const DIM = { seller_opportunity: 'Seller opportunity', urgency: 'Urgency', economics: 'Economics', engagement: 'Engagement' }
const CONF_TONE = { verified: 'live', partial: 'estimate', insufficient: 'danger' }
const FLAG = {
  notice_conflict: 'Notice conflict', unconfirmed_notice: 'Unconfirmed notice', possible_postponement: 'Possible postponement',
  sale_date_passed: 'Sale date passed', sale_cancelled_or_resolved: 'Sale cancelled / resolved', missing_ownership: 'Owner not verified',
  owner_conflict: 'Owner conflict', missing_contacts: 'No contact data', missing_valuation: 'No verified ARV',
  unknown_liens: 'Liens unknown', unknown_payoff: 'Payoff unknown',
}

export default function OpportunityCard({ o, q }) {
  const [open, setOpen] = useState(false)
  if (!o) return null
  const e = o.economics || {}
  const pr = o.priority || {}
  return (
    <Panel title="Opportunity" id="opportunity" data-testid="opportunity-card"
           hint="Rules-based - not a probability of contract or closing">
      <div className="evo-chips" style={{ flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
        <span className="evo-tag"><b>Priority {pr.value ?? '—'}</b>{pr.label ? ` · ${pr.label}` : ''}</span>
        <span className="evo-tag">Seller opportunity <b>&nbsp;{o.seller_opportunity?.value ?? '—'}</b></span>
        <span className="evo-tag">Urgency <b>&nbsp;{o.urgency?.label}</b>{o.urgency?.deadline ? ` · sale ${o.urgency.deadline}` : ''}</span>
        <Tag kind={CONF_TONE[o.confidence?.label]}>{o.confidence?.label} evidence</Tag>
        <span className="evo-tag">Engagement: {o.engagement?.label}</span>
        <span className="evo-tag">Stage: {o.workflow_state?.label}</span>
      </div>

      <div style={{ marginTop: 12 }}>
        <p className="evo-small" style={{ margin: 0 }}><b>Money:</b> {e.headline}</p>
        {e.needs?.length ? (
          <p className="evo-small evo-muted" style={{ margin: '4px 0 0' }}>Still needed: {e.needs.join(' · ')}</p>
        ) : null}
        {e.target ? (
          <p className="evo-small" style={{ margin: '4px 0 0' }}>
            Max acquisition {money(e.target.max_acquisition)} · fee target {money(e.target.assignment_fee_target)} ·
            investor price {money(e.target.investor_purchase_price)}
          </p>
        ) : null}
        {Object.keys(e.spreads || {}).length ? (
          <ul className="evo-small" style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            {Object.values(e.spreads).map((s) => (
              <li key={s.kind}>{s.label}: <b>{money(s.value)}</b>{s.low != null ? ` (range ${money(s.low)} to ${money(s.high)})` : ''}</li>
            ))}
          </ul>
        ) : null}
      </div>

      {q ? (
        <div className="evo-small" style={{ marginTop: 12 }}>
          <b>Seller qualification: {q.known} of {q.of} known</b>
          {q.next_question ? <span className="evo-muted"> · next question: “{q.next_question}”</span> : null}
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
            {q.questions.filter((x) => x.answer).map((x) => (
              <li key={x.key}>{x.label}: <b>{x.answer.value}</b> ({x.answer.who}{x.answer.quote ? `: “${x.answer.quote}”` : ''})</li>
            ))}
          </ul>
        </div>
      ) : null}

      {o.next_action ? (
        <p style={{ marginTop: 12 }}>
          <Link className="evo-btn evo-btn--primary evo-btn--sm" to={o.next_action.link || '#actions'}>{o.next_action.label}</Link>
          <span className="evo-small evo-muted" style={{ marginLeft: 8 }}>{o.next_action.why}</span>
        </p>
      ) : null}

      {o.flags?.length ? (
        <div className="evo-chips" style={{ flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
          {o.flags.map((f) => <Tag key={f} kind="estimate">{FLAG[f] || f}</Tag>)}
        </div>
      ) : null}

      <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" style={{ marginTop: 10 }}
              aria-expanded={open} onClick={() => setOpen(!open)}>{open ? 'Hide' : 'Show'} how this was worked out</button>
      {open ? (
        <div className="evo-small" style={{ marginTop: 8 }}>
          <table className="evo-table evo-small">
            <caption className="evo-sr">Priority parts</caption>
            <thead><tr><th scope="col">Part</th><th scope="col" className="is-num">Value</th><th scope="col" className="is-num">Weight</th><th scope="col">Note</th></tr></thead>
            <tbody>
              {(pr.parts || []).map((x) => (
                <tr key={x.dimension}><td>{DIM[x.dimension] || x.dimension}</td><td className="is-num">{x.value ?? 'unknown'}</td>
                  <td className="is-num">{x.effective_weight != null ? `${x.effective_weight}%` : '—'}</td><td>{x.note}</td></tr>
              ))}
            </tbody>
          </table>
          <p style={{ margin: '8px 0 4px' }}><b>Urgency reasons</b></p>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {(o.urgency?.factors || []).length ? o.urgency.factors.map((f, i) => <li key={i}>{f.points} — {f.label}</li>)
              : <li>No deadline or time pressure on file.</li>}
          </ul>
          <p style={{ margin: '8px 0 4px' }}><b>Evidence</b></p>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {(o.confidence?.factors || []).map((f, i) => <li key={i}>{f.label}{f.source ? ` (${f.source})` : ''}</li>)}
          </ul>
          {e.foreclosure ? (
            <>
              <p style={{ margin: '8px 0 4px' }}><b>Foreclosure numbers (each kept separate)</b></p>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {Object.entries(e.foreclosure).map(([k, v]) => (
                  <li key={k}>{k.replace(/_/g, ' ')}: {v == null ? '—' : typeof v === 'string' ? v
                    : v.value != null ? `${money(v.value)} (${v.label})` : v.pct != null ? `${v.pct}% (${v.basis || 'estimated'})` : JSON.stringify(v)}</li>
                ))}
              </ul>
            </>
          ) : null}
          {e.sensitivity ? (
            <>
              <p style={{ margin: '8px 0 4px' }}><b>Max acquisition if ARV or repairs move</b></p>
              <table className="evo-table evo-small">
                <thead><tr><th scope="col">ARV change</th><th scope="col" className="is-num">Repairs +20%</th><th scope="col" className="is-num">As estimated</th><th scope="col" className="is-num">Repairs −20%</th></tr></thead>
                <tbody>
                  {[-10, -5, 0, 5, 10].map((d) => (
                    <tr key={d}><td>{d > 0 ? `+${d}` : d}%</td>
                      {[20, 0, -20].map((r) => <td key={r} className="is-num">{money(e.sensitivity.find((x) => x.arv_change_pct === d && x.repairs_change_pct === r)?.max_acquisition)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
          <p className="evo-muted" style={{ marginTop: 8 }}>
            Assumptions: investor {e.assumptions?.investor_percentage ?? '—'}% of ARV, fee target {money(e.assumptions?.assignment_fee_target)}
            {' '}({e.assumptions?.source}). {o.note}
          </p>
        </div>
      ) : null}
    </Panel>
  )
}
